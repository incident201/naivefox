/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NeckoTunnel_h
#define netwerk_naivefox_NeckoTunnel_h

#include <functional>

#include "Config.h"
#include "ProxyProtocol.h"
#include "mozilla/Maybe.h"
#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "nsError.h"
#include "nsISupportsImpl.h"
#include "nsStringFwd.h"
#include "nsTArray.h"
#include "nscore.h"

class nsIHttpUpgradeListener;
class nsIHttpChannel;
class nsIInputStream;
class nsIRequest;
class nsIStreamListener;
class nsHtml5LeanPreloadDescriptor;
class nsHtml5StylePreloadDescriptor;

namespace mozilla::naivefox {

struct NativeStylePreloadActivationDescriptor;
struct NativeRootReplacementActivationDescriptor;
struct NativeStylePreloadProcessDescriptor;

// Internal Naive proxy authentication helper. This is not part of the
// embedded C ABI.
nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization);

struct ProxyPreambleResult final {
  nsresult mStatus = NS_ERROR_NOT_INITIALIZED;
  uint32_t mHttpStatus = 0;
  uint32_t mBodyBytes = 0;
  uint32_t mStartedResources = 0;
  uint32_t mCommittedResources = 0;
  uint32_t mNativeCacheNewResources = 0;
  bool mRootDone = false;
  bool mTerminalFallback = false;

  bool Succeeded() const {
    return NS_SUCCEEDED(mStatus) && mHttpStatus >= 200 && mHttpStatus < 300;
  }
};

using ProxyPreambleCallback = std::function<void(ProxyPreambleResult)>;

struct ProxyPreambleFinalResult final {
  nsresult mStatus = NS_ERROR_NOT_INITIALIZED;
  uint32_t mHttpStatus = 0;
  uint32_t mBodyBytes = 0;
  uint32_t mCompletedSuccessfulResources = 0;
  uint32_t mNativeCacheNewResources = 0;
  bool mRootDone = false;
  bool mCompletedNormally = false;
  bool mNavigationStopStyleCommitted = false;
  bool mNavigationStopStyleResponseStarted = false;
  bool mNavigationStopStyleAborted = false;
};

using ProxyPreambleFinishedCallback =
    std::function<void(ProxyPreambleFinalResult)>;

namespace detail {

enum class PreambleResourceKind : uint8_t {
  Other,
  Stylesheet,
  Script,
};

inline bool PreambleChannelUsesCache(const PreambleConfig& aConfig,
                                     ProxyProtocol aProtocol,
                                     bool aIsResource) {
  return aIsResource && aConfig.CacheResourcesForProtocol(aProtocol);
}

// Preserve the native scheduling cause used by Gecko's stylesheet and script
// loaders. The caller classifies the parsed resource; the channel layer then
// derives HTTP Priority and transport priority state from class-of-service.
constexpr bool PreambleResourceNeedsLeader(PreambleResourceKind aKind,
                                           bool aDeferred, bool aParserBlocking,
                                           bool aDiscoveredInHead) {
  if (aKind == PreambleResourceKind::Stylesheet) {
    return !aDeferred;
  }
  return aKind == PreambleResourceKind::Script && aParserBlocking &&
         aDiscoveredInHead;
}

bool PreambleStylesheetIsNonDeferred(const nsACString& aLowerTag,
                                     bool aAlternate);
bool PreambleScriptIsParserBlockingClassic(const nsACString& aLowerTag);

constexpr bool PreambleBarrierReached(
    PreambleMode aMode, bool aRootResponseAccepted, bool aRootDone,
    uint32_t aAssetCount, uint32_t aAssetsWithHeadersNotDone,
    uint32_t aAssetsWithHeadersOrDone, uint32_t aAssetsDone,
    uint32_t aAssetsCommitted = 0, bool aRootCompletedSuccessfully = true,
    uint32_t aNativeCacheNewResources = 0, bool aNativeParserFinished = false) {
  if (aMode == PreambleMode::Off) {
    return false;
  }
  if (aMode == PreambleMode::DocumentOverlap) {
    return aRootResponseAccepted && !aRootDone;
  }
  if (aMode == PreambleMode::DocumentHeadersTaskOverlap ||
      aMode == PreambleMode::DocumentFirstBufferOverlap ||
      aMode == PreambleMode::DocumentFirstBufferTaskOverlap ||
      aMode == PreambleMode::DocumentStartOverlap ||
      aMode == PreambleMode::DocumentStartTaskOverlap ||
      PreambleModeUsesNativeParserDocumentStart(aMode)) {
    return false;
  }
  if (aMode == PreambleMode::TreeNativeParserResourceCommittedOverlap) {
    return (aRootResponseAccepted || aRootDone) && aAssetCount > 0 &&
           aAssetsCommitted == aAssetCount;
  }
  if (!aRootDone) {
    return false;
  }
  if (aMode == PreambleMode::DocumentComplete ||
      aMode == PreambleMode::DocumentCarrierDispatch ||
      aMode == PreambleMode::DocumentColdWinnerHandoff ||
      aMode == PreambleMode::DocumentNativeCacheOpen ||
      aMode == PreambleMode::DocumentHandshakeConfirmed) {
    return true;
  }
  if (aMode == PreambleMode::TreeComplete) {
    return aAssetsDone == aAssetCount;
  }
  if (aMode == PreambleMode::TreeEarlyOverlap) {
    return aAssetCount > 0 && aAssetsWithHeadersNotDone > 0;
  }
  if (aMode == PreambleMode::TreeRootOverlap) {
    return aAssetCount > 0;
  }
  if (aMode == PreambleMode::TreeResourceCommittedOverlap) {
    return aRootCompletedSuccessfully && aAssetCount > 0 &&
           aAssetsCommitted == aAssetCount;
  }
  if (aMode == PreambleMode::TreeResourceNativeCacheCommittedOverlap) {
    return aRootCompletedSuccessfully && aAssetCount == 1 &&
           aAssetsCommitted == 1 && aNativeCacheNewResources == 1;
  }
  if (aMode == PreambleMode::TreeNativeParserPreloadOverlap) {
    return aRootCompletedSuccessfully && aNativeParserFinished &&
           aAssetCount == 1 && aAssetsCommitted == 1 && aAssetsDone == 0;
  }
  if (aMode == PreambleMode::TreeNativeParserDocumentHandoffOverlap) {
    return aRootCompletedSuccessfully && aNativeParserFinished &&
           aAssetCount == 1 && aAssetsCommitted == 1 && aAssetsDone == 0;
  }
  if (aMode == PreambleMode::TreeNativeParserRetargetOverlap) {
    return aRootCompletedSuccessfully && aNativeParserFinished &&
           aAssetCount == 1 && aAssetsCommitted == 1 && aAssetsDone == 0;
  }
  if (aMode == PreambleMode::TreeNativeParserIpcRendezvousOverlap) {
    return aRootCompletedSuccessfully && aNativeParserFinished &&
           aAssetCount == 1 && aAssetsCommitted == 1 && aAssetsDone == 0;
  }
  if (aMode == PreambleMode::TreeNativeParserRootRendezvousOverlap) {
    return aRootCompletedSuccessfully && aNativeParserFinished &&
           aAssetCount == 1 && aAssetsCommitted == 1 && aAssetsDone == 0;
  }
  if (PreambleModeUsesNativeParserProcess(aMode)) {
    return aRootCompletedSuccessfully && aNativeParserFinished &&
           aAssetCount == 1 && aAssetsCommitted == 1 && aAssetsDone == 0;
  }
  return aMode == PreambleMode::TreeOverlap &&
         aAssetsWithHeadersOrDone == aAssetCount;
}

constexpr bool PreambleOverlapsConnect(PreambleMode aMode) {
  return aMode == PreambleMode::DocumentOverlap ||
         aMode == PreambleMode::DocumentHeadersTaskOverlap ||
         aMode == PreambleMode::DocumentFirstBufferOverlap ||
         aMode == PreambleMode::DocumentFirstBufferTaskOverlap ||
         aMode == PreambleMode::DocumentStartOverlap ||
         aMode == PreambleMode::DocumentStartTaskOverlap ||
         aMode == PreambleMode::TreeOverlap ||
         aMode == PreambleMode::TreeEarlyOverlap ||
         aMode == PreambleMode::TreeRootOverlap ||
         aMode == PreambleMode::TreeResourceCommittedOverlap ||
         aMode == PreambleMode::TreeResourceNativeCacheCommittedOverlap ||
         aMode == PreambleMode::TreeNativeParserPreloadOverlap ||
         PreambleModeUsesNativeParserDocumentStart(aMode) ||
         aMode == PreambleMode::TreeNativeParserDocumentHandoffOverlap ||
         aMode == PreambleMode::TreeNativeParserRetargetOverlap ||
         aMode == PreambleMode::TreeNativeParserIpcRendezvousOverlap ||
         aMode == PreambleMode::TreeNativeParserRootRendezvousOverlap ||
         PreambleModeUsesNativeParserProcess(aMode);
}

constexpr bool PreambleRetargetDeliveryVerified(bool aListenerChainAccepted,
                                                bool aRetargetSucceeded,
                                                bool aTargetIdentityMatches) {
  return aListenerChainAccepted && aRetargetSucceeded && aTargetIdentityMatches;
}

constexpr bool PreambleUsesRetargetedRootDelivery(PreambleMode aMode,
                                                  uint32_t aStreamId) {
  return PreambleModeUsesRetargetedNativeParser(aMode) && aStreamId == 0;
}

constexpr bool PreambleNeedsCompletionFallback(PreambleMode aMode,
                                               bool aBarrierFired) {
  return (aMode == PreambleMode::DocumentOverlap ||
          aMode == PreambleMode::DocumentHeadersTaskOverlap ||
          aMode == PreambleMode::DocumentFirstBufferOverlap ||
          aMode == PreambleMode::DocumentFirstBufferTaskOverlap ||
          aMode == PreambleMode::DocumentStartOverlap ||
          aMode == PreambleMode::DocumentStartTaskOverlap ||
          aMode == PreambleMode::TreeEarlyOverlap ||
          aMode == PreambleMode::TreeRootOverlap ||
          aMode == PreambleMode::TreeResourceCommittedOverlap) &&
         !aBarrierFired;
}

constexpr bool PreambleResourceCompletedSuccessfully(
    bool aResponseHeadersReceived, uint32_t aHttpStatus, nsresult aStopStatus) {
  return aResponseHeadersReceived && aHttpStatus >= 200 && aHttpStatus < 300 &&
         NS_SUCCEEDED(aStopStatus);
}

constexpr bool PreambleNavigationStopExpectedStyleAbort(
    PreambleMode aMode, bool aBarrierFired, bool aStyleResponseStarted,
    bool aConnectHandoffAdmitted, bool aTunnelApplicationActive,
    bool aTunnelServerApplicationActive, bool aNavigationStopIssued,
    nsresult aStopStatus) {
  const bool requiredActivity =
      aMode == PreambleMode::TreeNativeParserDocumentStartNavigationStop
          ? aTunnelApplicationActive
          : aMode == PreambleMode::TreeNativeParserDocumentStartResponseStop &&
                aTunnelServerApplicationActive;
  return PreambleModeUsesScopedNavigationStop(aMode) && aBarrierFired &&
         aStyleResponseStarted && aConnectHandoffAdmitted && requiredActivity &&
         aNavigationStopIssued && aStopStatus == NS_BINDING_ABORTED;
}

constexpr bool PreambleNavigationStopMayIssue(
    PreambleMode aMode, bool aConnectHandoffAdmitted,
    bool aStyleResponseStarted, bool aTunnelApplicationActive,
    bool aTunnelServerApplicationActive) {
  const bool requiredActivity =
      aMode == PreambleMode::TreeNativeParserDocumentStartNavigationStop
          ? aTunnelApplicationActive
          : aMode == PreambleMode::TreeNativeParserDocumentStartResponseStop &&
                aTunnelServerApplicationActive;
  return PreambleModeUsesScopedNavigationStop(aMode) &&
         aConnectHandoffAdmitted && aStyleResponseStarted && requiredActivity;
}

constexpr bool PreambleNavigationStopCompletedSuccessfully(
    PreambleMode aMode, bool aRootDone, bool aCompletedNormally,
    nsresult aStatus, uint32_t aHttpStatus,
    uint32_t aCompletedSuccessfulResources, bool aStyleCommitted,
    bool aStyleResponseStarted, bool aStyleAborted) {
  if (!PreambleModeUsesScopedNavigationStop(aMode) || !aRootDone ||
      !aCompletedNormally || NS_FAILED(aStatus) || aHttpStatus < 200 ||
      aHttpStatus >= 300 || !aStyleCommitted || !aStyleResponseStarted) {
    return false;
  }
  if (aStyleAborted) {
    return aCompletedSuccessfulResources == 0;
  }
  return aMode == PreambleMode::TreeNativeParserDocumentStartResponseStop &&
         aCompletedSuccessfulResources == 1;
}

}  // namespace detail

// Owns the complete browser-like preamble load: the document channel and any
// same-origin resource channels discovered while its response is streaming.
// It intentionally has a lifetime independent from the CONNECT request so an
// overlapping tree operation can continue draining resource bodies after the
// barrier callback allows CONNECT to start.
class ProxyPreambleOperation final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ProxyPreambleOperation)

  // Public only so the XPCOM interface macros can name the implementation
  // type. It remains an operation-owned internal listener.
  class StreamListener;

  void Cancel(nsresult aStatus);
  nsresult NotifyConnectHandoffAdmitted();
  nsresult NotifyTunnelApplicationActive();
  nsresult NotifyTunnelServerApplicationActive();

 private:
  friend nsresult OpenProxyPreambleOperation(
      const nsACString&, const nsACString&, const nsACString&,
      const PreambleConfig&, ProxyProtocol, ProxyPreambleCallback&&,
      ProxyPreambleFinishedCallback&&, const Maybe<HostResolverRule>&, uint64_t,
      RefPtr<ProxyPreambleOperation>&);
  class Impl;

  ProxyPreambleOperation();
  ~ProxyPreambleOperation();

  nsresult Start(const nsACString& aProxyUrl, const nsACString& aProxyUser,
                 const nsACString& aProxyPassword,
                 const PreambleConfig& aConfig, ProxyProtocol aProtocol,
                 ProxyPreambleCallback&& aBarrierCallback,
                 ProxyPreambleFinishedCallback&& aFinishedCallback,
                 const Maybe<HostResolverRule>& aHostResolverRule,
                 uint64_t aConnectionId);
  nsresult OnStartRequest(uint32_t aStreamId, nsIRequest* aRequest);
  nsresult DispatchDocumentBarrierTask();
  nsresult OnDataAvailable(uint32_t aStreamId, nsIInputStream* aInputStream,
                           uint32_t aCount);
  nsresult OnRetargetedDataAvailable(uint32_t aStreamId,
                                     nsIInputStream* aInputStream,
                                     uint32_t aCount);
  nsresult OnRetargetedDataFinished(uint32_t aStreamId, nsresult aStatus);
  nsresult CheckNativeParserRetargetListener(uint32_t aStreamId);
  void OnRequestCommitted(uint32_t aStreamId, nsIRequest* aRequest);
  void OnStopRequest(uint32_t aStreamId, nsresult aStatus);
  nsresult MaybeIssueNativeParserNavigationStop();
  nsresult DispatchNativeParserChunk(nsCString&& aChunk);
  nsresult DispatchNativeParserFinish();
  void ReleaseDeferredNativeParserImages();
  nsresult DispatchNativeParserReplacementListenerInstall();
  void OnNativeParserReplacementListenerInstalled(uint64_t aGeneration);
  nsresult ResumeNativeParserDocumentHandoffRoot();
  void LogNativeParserDocumentHandoffPhase(const char* aPhase) const;
  void LogNativeParserRetargetPhase(const char* aPhase) const;
  void LogNativeParserRootReplacementPhase(const char* aPhase) const;
  nsresult InstallNativeParserRetargetDelivery(nsIRequest* aRequest);
  nsresult StartNativeParserRootReplacement(nsIRequest* aRequest,
                                            nsIHttpChannel* aChannel);
  nsresult StartNativeParserProcessRoot(nsIRequest* aRequest,
                                        nsIHttpChannel* aChannel);
  nsresult OnNativeParserProcessRootReady(uint64_t aGeneration);
  nsresult OnNativeParserProcessStyleDiscovered(
      uint64_t aGeneration,
      const NativeStylePreloadProcessDescriptor& aDescriptor);
  void OnNativeParserProcessRootFinished(uint64_t aGeneration,
                                         uint32_t aLastSequence,
                                         uint32_t aBodyBytes,
                                         uint32_t aStyleCount,
                                         nsresult aStatus);
  void OnNativeParserProcessFailed(uint64_t aGeneration, nsresult aStatus);
  nsresult LinkNativeParserRootReplacement(
      uint64_t aGeneration,
      const NativeRootReplacementActivationDescriptor& aDescriptor);
  void RunNativeParserRootRedirectVerification(uint64_t aGeneration,
                                               uint64_t aRequestId);
  void ResolveNativeParserRootRedirectVerification(uint64_t aGeneration,
                                                   uint64_t aRequestId);
  nsresult OnNativeParserRootReplacementReady(uint64_t aGeneration,
                                              nsresult aStatus);
  nsresult OnNativeParserRootForwardedStart(uint64_t aGeneration);
  nsresult OnNativeParserRootData(uint64_t aGeneration, nsCString&& aData);
  nsresult OnNativeParserRootStop(uint64_t aGeneration, nsresult aStatus);
  nsresult QueueNativeParserRootBody(nsIInputStream* aInputStream,
                                     uint32_t aCount);
  nsresult InstallNativeParserLogicalRetargetDelivery();
  nsresult DispatchNativeParserOutputToMain(
      uint64_t aGeneration, uint32_t aSequence, bool aFinished,
      nsresult aStatus, nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors,
      nsTArray<nsHtml5LeanPreloadDescriptor>&& aLeanDescriptors);
  void OnNativeParserOutput(
      uint64_t aGeneration, uint32_t aSequence, bool aFinished,
      nsresult aStatus, nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors,
      nsTArray<nsHtml5LeanPreloadDescriptor>&& aLeanDescriptors);
  nsresult OpenNativeParserStylesheet(
      nsHtml5StylePreloadDescriptor&& aDescriptor,
      uint64_t aProcessStyleRequestId = 0);
  nsresult OpenNativeParserResource(nsHtml5LeanPreloadDescriptor&& aDescriptor);
  nsresult CreateNativeParserStylesheetChannel(
      uint64_t aGeneration,
      const NativeStylePreloadActivationDescriptor& aActivation);
  nsresult ReleaseNativeParserStylesheetChannel(uint64_t aGeneration,
                                                nsresult aStatus);
  void FailNativeParserContract(nsresult aStatus, const char* aReason);
  void FireBarrierCallback(bool aTerminalFallback = false);
  void MaybeFireBarrier();
  void MaybeFinish();

  UniquePtr<Impl> mImpl;
};

nsresult OpenProxyPreambleOperation(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const PreambleConfig& aConfig,
    ProxyProtocol aProtocol, ProxyPreambleCallback&& aBarrierCallback,
    ProxyPreambleFinishedCallback&& aFinishedCallback,
    const Maybe<HostResolverRule>& aHostResolverRule, uint64_t aConnectionId,
    RefPtr<ProxyPreambleOperation>& aOperation);

nsresult OpenProxyPreamble(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const nsACString& aPath,
    uint32_t aMaxBytes, ProxyProtocol aProtocol,
    ProxyPreambleCallback&& aCallback,
    const Maybe<HostResolverRule>& aHostResolverRule = {},
    nsIRequest** aOpenedRequest = nullptr);

nsresult OpenNeckoTunnel(
    const nsACString& aProxyUrl, const nsACString& aTargetAuthority,
    const nsACString& aProxyUser, const nsACString& aProxyPassword,
    nsIHttpUpgradeListener* aUpgradeListener,
    nsIStreamListener* aChannelListener, const nsACString& aConnectPadding,
    ProxyProtocol aProtocol,
    const Maybe<HostResolverRule>& aHostResolverRule = {},
    const nsTArray<ExtraHeader>& aExtraHeaders = {},
    bool aConnectUrgentStart = false, bool aUseAnonymousConnection = true,
    nsIRequest** aOpenedRequest = nullptr);

nsresult RunRawTunnelSmoke(const nsACString& aProxyUrl,
                           const nsACString& aTargetAuthority,
                           const nsACString& aProxyUser,
                           const nsACString& aProxyPassword,
                           ProxyProtocol aProtocol);

// Diagnostic finite HTTP transactions on the same explicit H2 proxy route as
// the document preamble. Normal channel bodies, not upgraded tunnel streams.
nsresult OpenFiniteHttpExchange(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword,
    const Maybe<HostResolverRule>& aHostResolverRule,
    const nsACString& aMethod, const nsTArray<ExtraHeader>& aHeaders,
    const nsACString& aBody, nsIStreamListener* aListener,
    nsIRequest** aOpenedRequest);

// Paired with the process-wide lazy "HTML5 Parser" thread used by the
// DOM-free native speculative-preload arm. Must run on main before XPCOM
// thread shutdown.
void ShutdownProxyPreambleParserThread();

}  // namespace mozilla::naivefox

#endif
