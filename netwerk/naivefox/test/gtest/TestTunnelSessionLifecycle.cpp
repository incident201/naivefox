/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "SocksServer.h"
#include "TunnelSession.h"
#include "gtest/gtest.h"
#include "mozilla/Atomics.h"
#include "mozilla/Monitor.h"
#include "mozilla/ScopeExit.h"
#include "mozilla/gtest/MozAssertions.h"
#include "nsCOMPtr.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIPipe.h"
#include "nsIThread.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

class TunnelSessionTestPeer final {
 public:
  static void ApplyChannelStop(TunnelSession* aSession) {
    aSession->ApplyChannelStop(0, ProxyProtocol::H2, NS_OK);
  }

  static bool ShouldGateOuterSession(const TunnelConfig& aConfig) {
    return TunnelSession::ShouldGateOuterSession(aConfig);
  }
};

namespace {

static_assert(TunnelSession::HasThreadSafeRefCnt::value);
static_assert(LocalProxyServerControl::HasThreadSafeRefCnt::value);

TEST(NaiveFoxEmbeddedLifecycle, StopRequestIsLatchedAndIdempotent)
{
  RefPtr control = new LocalProxyServerControl();
  EXPECT_FALSE(control->StopRequested());
  control->RequestStop();
  EXPECT_TRUE(control->StopRequested());
  control->RequestStop();
  EXPECT_TRUE(control->StopRequested());
}

TEST(NaiveFoxTunnelSessionLifecycle, OuterGatePreservesAutoFallbackSemantics)
{
  TunnelConfig config;
  EXPECT_FALSE(TunnelSessionTestPeer::ShouldGateOuterSession(config));

  config.mOuterSessionGate = true;
  config.mProtocol = ProxyProtocol::H2;
  EXPECT_TRUE(TunnelSessionTestPeer::ShouldGateOuterSession(config));
  config.mProtocol = ProxyProtocol::H3;
  EXPECT_TRUE(TunnelSessionTestPeer::ShouldGateOuterSession(config));
  config.mProtocol = ProxyProtocol::Auto;
  EXPECT_FALSE(TunnelSessionTestPeer::ShouldGateOuterSession(config));
}

TEST(NaiveFoxTunnelSessionLifecycle, OnlyColdLeaderRunsConfiguredPreamble)
{
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::Off, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::Off, false));

  // A leader (or every ungated tunnel) runs the single root request. Queued
  // and warm gate participants both reach this decision as non-leaders.
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::Root, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::Root, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::Tree, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::Tree, false));
}

TEST(NaiveFoxTunnelSessionLifecycle, LatePreambleCallbackCannotDoubleOpen)
{
  detail::PreambleSequenceState state;
  constexpr uint64_t generation = 7;

  EXPECT_TRUE(state.Begin(generation, ProxyProtocol::H3));
  EXPECT_FALSE(state.Begin(generation, ProxyProtocol::H3));
  EXPECT_TRUE(state.Complete(generation, ProxyProtocol::H3));
  EXPECT_FALSE(state.Complete(generation, ProxyProtocol::H3));
  EXPECT_TRUE(state.TryStartConnect(generation));
  EXPECT_FALSE(state.TryStartConnect(generation));
  EXPECT_FALSE(state.Begin(generation, ProxyProtocol::H3));

  // Canceling the request disarms a later OnStopRequest callback as well.
  EXPECT_TRUE(state.Begin(generation + 1, ProxyProtocol::H2));
  state.Cancel();
  EXPECT_FALSE(state.Complete(generation + 1, ProxyProtocol::H2));
}

RefPtr<TunnelSession> NewSession(nsIEventTarget* aSocketTarget,
                                 Atomic<uint32_t, Relaxed>& aClosedCount) {
  nsCOMPtr<nsIAsyncInputStream> localIn;
  nsCOMPtr<nsIAsyncOutputStream> localOut;
  NS_NewPipe2(getter_AddRefs(localIn), getter_AddRefs(localOut), true, true);
  TunnelConfig config;
  return new TunnelSession(
      localIn, localOut, config, aSocketTarget, [](const nsACString&, bool) {},
      [](nsresult) {}, [&aClosedCount](nsresult) { ++aClosedCount; });
}

TEST(NaiveFoxTunnelSessionLifecycle, QueuedChannelStopKeepsSessionAlive)
{
  nsCOMPtr<nsIThread> socketThread;
  ASSERT_NS_SUCCEEDED(
      NS_NewNamedThread("NFSessionLife", getter_AddRefs(socketThread)));

  Monitor gate("NaiveFoxTunnelSessionLifecycle::gate");
  bool blockerStarted = false;
  bool unblock = false;
  auto shutdownThread = MakeScopeExit([&]() {
    {
      MonitorAutoLock lock(gate);
      unblock = true;
      lock.NotifyAll();
    }
    if (socketThread) {
      (void)socketThread->Shutdown();
    }
  });
  ASSERT_NS_SUCCEEDED(socketThread->Dispatch(
      NS_NewRunnableFunction("NaiveFox::TestBlockSocketThread",
                             [&]() {
                               MonitorAutoLock lock(gate);
                               blockerStarted = true;
                               lock.NotifyAll();
                               while (!unblock) {
                                 lock.Wait();
                               }
                             }),
      NS_DISPATCH_NORMAL));

  {
    MonitorAutoLock lock(gate);
    while (!blockerStarted) {
      lock.Wait();
    }
  }

  Atomic<uint32_t, Relaxed> closedCount{0};
  Atomic<bool, Relaxed> channelStopRan{false};
  RefPtr<TunnelSession> session = NewSession(socketThread, closedCount);
  ASSERT_TRUE(session);
  ASSERT_NS_SUCCEEDED(socketThread->Dispatch(
      NS_NewRunnableFunction("NaiveFox::TunnelChannelStopTest",
                             [queued = RefPtr{session}, &channelStopRan]() {
                               TunnelSessionTestPeer::ApplyChannelStop(queued);
                               channelStopRan = true;
                             }),
      NS_DISPATCH_NORMAL));

  session = nullptr;
  EXPECT_EQ(closedCount, 0u);

  {
    MonitorAutoLock lock(gate);
    unblock = true;
    lock.NotifyAll();
  }
  ASSERT_NS_SUCCEEDED(socketThread->Shutdown());
  shutdownThread.release();
  EXPECT_TRUE(channelStopRan);
  EXPECT_EQ(closedCount, 1u);
}

TEST(NaiveFoxTunnelSessionLifecycle, ConcurrentRefPtrHandoff)
{
  nsCOMPtr<nsIThread> socketThread;
  ASSERT_NS_SUCCEEDED(
      NS_NewNamedThread("NFSessionRefs", getter_AddRefs(socketThread)));
  auto shutdownThread = MakeScopeExit([&]() {
    if (socketThread) {
      (void)socketThread->Shutdown();
    }
  });

  Atomic<uint32_t, Relaxed> closedCount{0};
  RefPtr<TunnelSession> session = NewSession(socketThread, closedCount);
  ASSERT_TRUE(session);

  Monitor gate("NaiveFoxTunnelSessionLifecycle::refGate");
  bool start = false;
  bool workerDone = false;
  constexpr size_t kIterations = 50000;
  ASSERT_NS_SUCCEEDED(socketThread->Dispatch(
      NS_NewRunnableFunction(
          "NaiveFox::TestConcurrentSessionRefs",
          [workerSession = RefPtr{session}, &gate, &start, &workerDone]() {
            {
              MonitorAutoLock lock(gate);
              while (!start) {
                lock.Wait();
              }
            }
            for (size_t i = 0; i < kIterations; ++i) {
              RefPtr<TunnelSession> copy = workerSession;
              MOZ_RELEASE_ASSERT(copy);
            }
            MonitorAutoLock lock(gate);
            workerDone = true;
            lock.NotifyAll();
          }),
      NS_DISPATCH_NORMAL));

  {
    MonitorAutoLock lock(gate);
    start = true;
    lock.NotifyAll();
  }
  for (size_t i = 0; i < kIterations; ++i) {
    RefPtr<TunnelSession> copy = session;
    ASSERT_TRUE(copy);
  }
  {
    MonitorAutoLock lock(gate);
    while (!workerDone) {
      lock.Wait();
    }
  }

  ASSERT_NS_SUCCEEDED(socketThread->Shutdown());
  shutdownThread.release();
  EXPECT_EQ(closedCount, 0u);
  session = nullptr;
  EXPECT_EQ(closedCount, 1u);
}

}  // namespace
}  // namespace mozilla::naivefox
