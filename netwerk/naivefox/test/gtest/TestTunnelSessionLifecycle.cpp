/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <array>
#include <thread>

#include "NeckoTunnel.h"
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

  static void FailPreambleOnMain(TunnelSession* aSession, nsresult aStatus) {
    aSession->FailPreambleOnMain(aStatus);
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

TEST(NaiveFoxSocksServer, DisabledUrgentStartClaimDoesNotConsumeSelector)
{
  detail::FirstSocksTunnelUrgentStartSelector selector;
  EXPECT_FALSE(selector.Claim(false));
  EXPECT_TRUE(selector.Claim(true));
  EXPECT_FALSE(selector.Claim(true));
}

TEST(NaiveFoxSocksServer, ConcurrentUrgentStartClaimSelectsExactlyOneTunnel)
{
  detail::FirstSocksTunnelUrgentStartSelector selector;
  Atomic<uint32_t, Relaxed> claims{0};
  std::array<std::thread, 16> workers;
  for (auto& worker : workers) {
    worker = std::thread([&]() {
      if (selector.Claim(true)) {
        ++claims;
      }
    });
  }
  for (auto& worker : workers) {
    worker.join();
  }
  EXPECT_EQ(claims, 1U);
  EXPECT_FALSE(selector.Claim(true));
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

  // A leader (or every ungated tunnel) runs the configured operation. Queued
  // and warm gate participants both reach this decision as non-leaders.
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::Root, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::Root, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::Tree, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::Tree, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::DocumentComplete, true));
  EXPECT_TRUE(
      detail::ShouldRunPreamble(PreambleMode::DocumentCarrierDispatch, true));
  EXPECT_FALSE(
      detail::ShouldRunPreamble(PreambleMode::DocumentCarrierDispatch, false));
  EXPECT_TRUE(
      detail::ShouldRunPreamble(PreambleMode::DocumentColdWinnerHandoff, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(
      PreambleMode::DocumentColdWinnerHandoff, false));
  EXPECT_TRUE(
      detail::ShouldRunPreamble(PreambleMode::DocumentNativeCacheOpen, true));
  EXPECT_FALSE(
      detail::ShouldRunPreamble(PreambleMode::DocumentNativeCacheOpen, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(
      PreambleMode::DocumentHandshakeConfirmed, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(
      PreambleMode::DocumentHandshakeConfirmed, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::DocumentOverlap, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::DocumentOverlap, false));
  EXPECT_TRUE(
      detail::ShouldRunPreamble(PreambleMode::DocumentStartOverlap, true));
  EXPECT_FALSE(
      detail::ShouldRunPreamble(PreambleMode::DocumentStartOverlap, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::TreeComplete, true));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::TreeOverlap, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::TreeOverlap, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::TreeEarlyOverlap, true));
  EXPECT_FALSE(
      detail::ShouldRunPreamble(PreambleMode::TreeEarlyOverlap, false));
  EXPECT_TRUE(detail::ShouldRunPreamble(PreambleMode::TreeRootOverlap, true));
  EXPECT_FALSE(detail::ShouldRunPreamble(PreambleMode::TreeRootOverlap, false));
}

TEST(NaiveFoxTunnelSessionLifecycle, ProtocolSpecificPreambleModeSelection)
{
  PreambleConfig config;
  config.mMode = PreambleMode::DocumentComplete;
  config.mH3Mode = Some(PreambleMode::TreeRootOverlap);

  const PreambleMode h3FirstAttempt = config.ModeForProtocol(ProxyProtocol::H3);
  EXPECT_EQ(h3FirstAttempt, PreambleMode::TreeRootOverlap);
  EXPECT_TRUE(detail::ShouldRunPreamble(h3FirstAttempt, true));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(h3FirstAttempt));

  // Auto fallback must resolve the mode again for the new H2 attempt instead
  // of retaining the failed H3 attempt's effective mode.
  const PreambleMode h2Fallback = config.ModeForProtocol(ProxyProtocol::H2);
  EXPECT_EQ(h2Fallback, PreambleMode::DocumentComplete);
  EXPECT_TRUE(detail::ShouldRunPreamble(h2Fallback, true));
  EXPECT_FALSE(detail::PreambleOverlapsConnect(h2Fallback));

  PreambleConfig legacy;
  legacy.mMode = PreambleMode::TreeComplete;
  EXPECT_EQ(legacy.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::TreeComplete);
  EXPECT_EQ(legacy.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeComplete);
}

TEST(NaiveFoxTunnelSessionLifecycle, ParserRetargetIsRootDeliveryOnly)
{
  EXPECT_TRUE(PreambleModeRequiresFailClosed(
      PreambleMode::TreeNativeParserProcessOverlap));
  EXPECT_FALSE(PreambleModeRequiresFailClosed(
      PreambleMode::TreeNativeParserRootRendezvousOverlap));
  EXPECT_TRUE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserRetargetOverlap, 0));
  EXPECT_FALSE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserRetargetOverlap, 1));
  EXPECT_TRUE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap, 0));
  EXPECT_FALSE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap, 1));
  EXPECT_TRUE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserRootRendezvousOverlap, 0));
  EXPECT_FALSE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserRootRendezvousOverlap, 1));
  EXPECT_FALSE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserProcessOverlap, 0));
  EXPECT_FALSE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserDocumentHandoffOverlap, 0));
  EXPECT_FALSE(detail::PreambleUsesRetargetedRootDelivery(
      PreambleMode::TreeNativeParserPreloadOverlap, 0));
}

TEST(NaiveFoxTunnelSessionLifecycle, ResourceCacheFollowsEffectiveTreeMode)
{
  PreambleConfig config;
  config.mMode = PreambleMode::DocumentComplete;
  config.mH3Mode = Some(PreambleMode::TreeRootOverlap);

  EXPECT_FALSE(config.CacheResourcesForProtocol(ProxyProtocol::H2));
  EXPECT_FALSE(config.CacheResourcesForProtocol(ProxyProtocol::H3));

  config.mCacheResources = true;
  EXPECT_FALSE(config.CacheResourcesForProtocol(ProxyProtocol::H2));
  EXPECT_TRUE(config.CacheResourcesForProtocol(ProxyProtocol::H3));
  EXPECT_FALSE(
      detail::PreambleChannelUsesCache(config, ProxyProtocol::H3, false));
  EXPECT_TRUE(
      detail::PreambleChannelUsesCache(config, ProxyProtocol::H3, true));

  config.mH3Mode = Some(PreambleMode::TreeResourceNativeCacheCommittedOverlap);
  // The product arm restores the native cache lifecycle only for the
  // discovered resource; the synthetic root remains cache-inhibited.
  EXPECT_FALSE(
      detail::PreambleChannelUsesCache(config, ProxyProtocol::H3, false));
  EXPECT_TRUE(
      detail::PreambleChannelUsesCache(config, ProxyProtocol::H3, true));

  config.mH3Mode = Some(PreambleMode::Off);
  EXPECT_FALSE(config.CacheResourcesForProtocol(ProxyProtocol::H3));
}

TEST(NaiveFoxTunnelSessionLifecycle, PreambleModesUseDistinctBarriers)
{
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::DocumentComplete,
                                              false, false, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(PreambleMode::DocumentComplete,
                                             true, true, 0, 0, 0, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentCarrierDispatch, false, false, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::DocumentCarrierDispatch, true, true, 0, 0, 0, 0));
  EXPECT_FALSE(
      detail::PreambleOverlapsConnect(PreambleMode::DocumentCarrierDispatch));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentColdWinnerHandoff, false, false, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::DocumentColdWinnerHandoff, true, true, 0, 0, 0, 0));
  EXPECT_FALSE(
      detail::PreambleOverlapsConnect(PreambleMode::DocumentColdWinnerHandoff));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentNativeCacheOpen, false, false, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::DocumentNativeCacheOpen, true, true, 0, 0, 0, 0));
  EXPECT_FALSE(
      detail::PreambleOverlapsConnect(PreambleMode::DocumentNativeCacheOpen));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentHandshakeConfirmed, false, false, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::DocumentHandshakeConfirmed, true, true, 0, 0, 0, 0));
  EXPECT_FALSE(detail::PreambleOverlapsConnect(
      PreambleMode::DocumentHandshakeConfirmed));

  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::DocumentOverlap,
                                              false, false, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(PreambleMode::DocumentOverlap,
                                             true, false, 0, 0, 0, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::DocumentOverlap,
                                              true, true, 0, 0, 0, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentStartOverlap, false, false, 0, 0, 0, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentStartOverlap, true, false, 0, 0, 0, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::DocumentStartOverlap, true, true, 0, 0, 0, 0));

  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeComplete, true,
                                              true, 2, 0, 2, 1));
  EXPECT_TRUE(detail::PreambleBarrierReached(PreambleMode::TreeComplete, true,
                                             true, 2, 0, 2, 2));

  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeOverlap, true,
                                              true, 2, 1, 1, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(PreambleMode::TreeOverlap, true,
                                             true, 2, 0, 2, 2));

  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeEarlyOverlap,
                                              false, false, 2, 1, 1, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeEarlyOverlap,
                                              true, true, 0, 0, 0, 0));
  // A failed or completed asset without response headers is not admission.
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeEarlyOverlap,
                                              true, true, 2, 0, 2, 2));
  // Response headers are insufficient once that same asset has completed.
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeEarlyOverlap,
                                              true, true, 2, 1, 2, 2));
  EXPECT_TRUE(detail::PreambleBarrierReached(PreambleMode::TreeEarlyOverlap,
                                             true, true, 2, 1, 1, 0));

  // Root-overlap is defined by client-side scheduling state, not by whether
  // response HEADERS or FIN happened to win a transport race.
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeRootOverlap,
                                              false, false, 1, 0, 0, 1));
  EXPECT_FALSE(detail::PreambleBarrierReached(PreambleMode::TreeRootOverlap,
                                              true, true, 0, 0, 0, 0));
  EXPECT_TRUE(detail::PreambleBarrierReached(PreambleMode::TreeRootOverlap,
                                             true, true, 1, 0, 0, 1));

  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeResourceCommittedOverlap, true, true, 1, 0, 0, 0, 0));
  EXPECT_FALSE(
      detail::PreambleBarrierReached(PreambleMode::TreeResourceCommittedOverlap,
                                     true, true, 1, 0, 0, 0, 1, false));
  EXPECT_TRUE(
      detail::PreambleBarrierReached(PreambleMode::TreeResourceCommittedOverlap,
                                     true, true, 1, 0, 0, 0, 1, true));

  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap, true, true, 1, 0,
      0, 0, 1, true, 0));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap, true, true, 1, 0,
      0, 0, 0, true, 1));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap, true, true, 1, 0,
      0, 0, 1, false, 1));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap, true, true, 1, 0,
      0, 0, 1, true, 1));

  // Parser preload admission is intentionally stronger than a channel open:
  // EOF has validated that there was exactly one supported descriptor, the
  // real stylesheet request reached WAITING_FOR, and its response is still
  // unfinished when CONNECT is released.
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserPreloadOverlap, true, true, 1, 0, 0, 0, 1,
      true, 0, false));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserPreloadOverlap, true, true, 1, 0, 0, 1, 1,
      true, 0, true));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserPreloadOverlap, true, true, 1, 0, 0, 0, 0,
      true, 0, true));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserPreloadOverlap, true, true, 1, 0, 0, 0, 1,
      true, 0, true));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserDocumentHandoffOverlap, true, true, 1, 0, 0,
      0, 1, true, 0, false));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserDocumentHandoffOverlap, true, true, 1, 0, 0,
      0, 1, true, 0, true));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserRetargetOverlap, true, true, 1, 0, 0, 0, 1,
      true, 0, false));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserRetargetOverlap, true, true, 1, 0, 0, 0, 1,
      true, 0, true));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap, true, true, 1, 0, 0,
      0, 1, true, 0, false));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap, true, true, 1, 0, 0,
      0, 1, true, 0, true));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserRootRendezvousOverlap, true, true, 1, 0, 0,
      0, 1, true, 0, false));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserRootRendezvousOverlap, true, true, 1, 0, 0,
      0, 1, true, 0, true));
  EXPECT_FALSE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserProcessOverlap, true, true, 1, 0, 0, 0, 1,
      true, 0, false));
  EXPECT_TRUE(detail::PreambleBarrierReached(
      PreambleMode::TreeNativeParserProcessOverlap, true, true, 1, 0, 0, 0, 1,
      true, 0, true));

  EXPECT_TRUE(detail::PreambleOverlapsConnect(PreambleMode::DocumentOverlap));
  EXPECT_TRUE(
      detail::PreambleOverlapsConnect(PreambleMode::DocumentStartOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(PreambleMode::TreeOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(PreambleMode::TreeEarlyOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(PreambleMode::TreeRootOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeResourceCommittedOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeNativeParserPreloadOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeNativeParserDocumentHandoffOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeNativeParserRetargetOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeNativeParserRootRendezvousOverlap));
  EXPECT_TRUE(detail::PreambleOverlapsConnect(
      PreambleMode::TreeNativeParserProcessOverlap));
  EXPECT_FALSE(detail::PreambleOverlapsConnect(PreambleMode::TreeComplete));
}

TEST(NaiveFoxTunnelSessionLifecycle, EarlyOverlapTerminalNonAdmissionFallsBack)
{
  struct TerminalState {
    const char* mDescription;
    bool mRootDone;
    uint32_t mAssetCount;
    uint32_t mAssetsWithHeadersNotDone;
    uint32_t mAssetsWithHeadersOrDone;
    uint32_t mAssetsDone;
  };
  static constexpr TerminalState kTerminalStates[] = {
      {"zero assets", true, 0, 0, 0, 0},
      {"assets finished before root", true, 2, 0, 2, 2},
      {"root failure without assets", true, 0, 0, 0, 0},
  };

  for (const auto& state : kTerminalStates) {
    const bool barrierFired = detail::PreambleBarrierReached(
        PreambleMode::TreeEarlyOverlap, state.mRootDone, state.mRootDone,
        state.mAssetCount, state.mAssetsWithHeadersNotDone,
        state.mAssetsWithHeadersOrDone, state.mAssetsDone);
    EXPECT_FALSE(barrierFired) << state.mDescription;
    EXPECT_TRUE(detail::PreambleNeedsCompletionFallback(
        PreambleMode::TreeEarlyOverlap, barrierFired))
        << state.mDescription;
  }

  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeEarlyOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeOverlap, false));

  EXPECT_TRUE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::DocumentOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::DocumentOverlap, true));

  EXPECT_TRUE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::DocumentStartOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::DocumentStartOverlap, true));

  EXPECT_TRUE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeRootOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeRootOverlap, true));

  EXPECT_TRUE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeResourceCommittedOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeResourceCommittedOverlap, true));

  // This mode is a fail-closed product contract: completing the resource
  // without its async new-cache-entry admission must not release CONNECT.
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeResourceNativeCacheCommittedOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserPreloadOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserPreloadOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserDocumentHandoffOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserDocumentHandoffOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserRetargetOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserRetargetOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserIpcRendezvousOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserRootRendezvousOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserRootRendezvousOverlap, true));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserProcessOverlap, false));
  EXPECT_FALSE(detail::PreambleNeedsCompletionFallback(
      PreambleMode::TreeNativeParserProcessOverlap, true));

  EXPECT_TRUE(detail::PreambleRetargetDeliveryVerified(true, true, true));
  EXPECT_FALSE(detail::PreambleRetargetDeliveryVerified(false, true, true));
  EXPECT_FALSE(detail::PreambleRetargetDeliveryVerified(true, false, true));
  EXPECT_FALSE(detail::PreambleRetargetDeliveryVerified(true, true, false));

  detail::PreambleSequenceState sequence;
  constexpr uint64_t generation = 17;
  EXPECT_TRUE(sequence.Begin(generation, ProxyProtocol::H2));
  EXPECT_TRUE(sequence.Complete(generation, ProxyProtocol::H2));
  EXPECT_FALSE(sequence.Complete(generation, ProxyProtocol::H2));
  EXPECT_TRUE(sequence.TryStartConnect(generation));
  EXPECT_FALSE(sequence.TryStartConnect(generation));
}

TEST(NaiveFoxTunnelSessionLifecycle, ResourceDrainRequiresSuccessfulHttp)
{
  EXPECT_TRUE(detail::PreambleResourceCompletedSuccessfully(true, 200, NS_OK));
  EXPECT_TRUE(detail::PreambleResourceCompletedSuccessfully(true, 299, NS_OK));
  EXPECT_FALSE(
      detail::PreambleResourceCompletedSuccessfully(false, 200, NS_OK));
  EXPECT_FALSE(detail::PreambleResourceCompletedSuccessfully(true, 199, NS_OK));
  EXPECT_FALSE(detail::PreambleResourceCompletedSuccessfully(true, 300, NS_OK));
  EXPECT_FALSE(detail::PreambleResourceCompletedSuccessfully(
      true, 200, NS_ERROR_NET_RESET));
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

RefPtr<TunnelSession> NewSession(
    nsIEventTarget* aSocketTarget, Atomic<uint32_t, Relaxed>& aClosedCount,
    Atomic<uint32_t, Relaxed>* aFailureCount = nullptr) {
  nsCOMPtr<nsIAsyncInputStream> localIn;
  nsCOMPtr<nsIAsyncOutputStream> localOut;
  NS_NewPipe2(getter_AddRefs(localIn), getter_AddRefs(localOut), true, true);
  TunnelConfig config;
  return new TunnelSession(
      localIn, localOut, config, false, aSocketTarget,
      [](const nsACString&, bool) {},
      [aFailureCount](nsresult) {
        if (aFailureCount) {
          ++*aFailureCount;
        }
      },
      [&aClosedCount](nsresult) { ++aClosedCount; });
}

TEST(NaiveFoxTunnelSessionLifecycle,
     FailClosedPreambleDispatchesOneTerminalFailure)
{
  nsCOMPtr<nsIThread> socketThread;
  ASSERT_NS_SUCCEEDED(
      NS_NewNamedThread("NFPreambleFail", getter_AddRefs(socketThread)));
  auto shutdownThread = MakeScopeExit([&]() {
    if (socketThread) {
      (void)socketThread->Shutdown();
    }
  });

  Atomic<uint32_t, Relaxed> closedCount{0};
  Atomic<uint32_t, Relaxed> failureCount{0};
  RefPtr<TunnelSession> session =
      NewSession(socketThread, closedCount, &failureCount);
  ASSERT_TRUE(session);

  TunnelSessionTestPeer::FailPreambleOnMain(session, NS_ERROR_NET_TIMEOUT);
  TunnelSessionTestPeer::FailPreambleOnMain(session, NS_ERROR_FAILURE);

  ASSERT_NS_SUCCEEDED(socketThread->Shutdown());
  shutdownThread.release();
  EXPECT_EQ(failureCount, 1u);
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
