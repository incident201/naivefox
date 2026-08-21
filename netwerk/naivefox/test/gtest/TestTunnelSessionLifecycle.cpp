/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

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
};

namespace {

static_assert(TunnelSession::HasThreadSafeRefCnt::value);

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
