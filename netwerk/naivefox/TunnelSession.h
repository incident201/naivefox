/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_TunnelSession_h
#define netwerk_naivefox_TunnelSession_h

#include <functional>

#include "Config.h"
#include "ProxyProtocol.h"
#include "mozilla/Maybe.h"
#include "mozilla/RefPtr.h"
#include "mozilla/Span.h"
#include "mozilla/UniquePtr.h"
#include "nsISupportsImpl.h"
#include "nsString.h"
#include "nscore.h"

class nsIAsyncInputStream;
class nsIAsyncOutputStream;
class nsIEventTarget;
class nsIRequest;
class nsISocketTransport;

namespace mozilla::naivefox {

class TunnelAttempt;

namespace detail {

constexpr bool ShouldRunPreamble(PreambleMode aMode, bool aColdLeader) {
  return aMode != PreambleMode::Off && aColdLeader;
}

// Main-thread-only sequence guard. Keeping the generation transition in this
// small value type makes cancellation and late callback behavior testable
// without constructing Necko channels.
class PreambleSequenceState final {
 public:
  bool Begin(uint64_t aGeneration, ProxyProtocol aProtocol) {
    if (mConnectGeneration == aGeneration ||
        (mGeneration == aGeneration && mPhase != Phase::Idle)) {
      return false;
    }
    mGeneration = aGeneration;
    mProtocol = aProtocol;
    mPhase = Phase::InFlight;
    return true;
  }

  bool IsInFlight(uint64_t aGeneration, ProxyProtocol aProtocol) const {
    return mGeneration == aGeneration && mProtocol == aProtocol &&
           mPhase == Phase::InFlight;
  }

  bool Complete(uint64_t aGeneration, ProxyProtocol aProtocol) {
    if (!IsInFlight(aGeneration, aProtocol)) {
      return false;
    }
    mPhase = Phase::Complete;
    return true;
  }

  void Cancel() {
    if (mPhase == Phase::InFlight) {
      mPhase = Phase::Complete;
    }
  }

  bool TryStartConnect(uint64_t aGeneration) {
    if (mConnectGeneration == aGeneration) {
      return false;
    }
    mConnectGeneration = aGeneration;
    return true;
  }

 private:
  enum class Phase : uint8_t { Idle, InFlight, Complete };

  uint64_t mGeneration = 0;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
  Phase mPhase = Phase::Idle;
  uint64_t mConnectGeneration = 0;
};

}  // namespace detail

struct TunnelConfig final {
  TunnelConfig() = default;
  TunnelConfig(const TunnelConfig& aOther)
      : mProxyUrl(aOther.mProxyUrl),
        mProxyUser(aOther.mProxyUser),
        mProxyPassword(aOther.mProxyPassword),
        mProtocol(aOther.mProtocol),
        mHostResolverRule(aOther.mHostResolverRule),
        mPreamble(aOther.mPreamble),
        mOuterSessionGate(aOther.mOuterSessionGate),
        mDiagnosticFirstSocksTunnelUrgentStart(
            aOther.mDiagnosticFirstSocksTunnelUrgentStart) {
    mExtraHeaders.AppendElements(aOther.mExtraHeaders);
  }
  TunnelConfig& operator=(const TunnelConfig& aOther) {
    if (this != &aOther) {
      mProxyUrl = aOther.mProxyUrl;
      mProxyUser = aOther.mProxyUser;
      mProxyPassword = aOther.mProxyPassword;
      mProtocol = aOther.mProtocol;
      mHostResolverRule = aOther.mHostResolverRule;
      mPreamble = aOther.mPreamble;
      mOuterSessionGate = aOther.mOuterSessionGate;
      mDiagnosticFirstSocksTunnelUrgentStart =
          aOther.mDiagnosticFirstSocksTunnelUrgentStart;
      mExtraHeaders.Clear();
      mExtraHeaders.AppendElements(aOther.mExtraHeaders);
    }
    return *this;
  }

  nsCString mProxyUrl;
  nsCString mProxyUser;
  nsCString mProxyPassword;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
  Maybe<HostResolverRule> mHostResolverRule;
  nsTArray<ExtraHeader> mExtraHeaders;
  PreambleConfig mPreamble;
  bool mOuterSessionGate = false;
  bool mDiagnosticFirstSocksTunnelUrgentStart = false;
};

class TunnelSession final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(TunnelSession)

  using EstablishedCallback = std::function<void(const nsACString&, bool)>;
  using FailureCallback = std::function<void(nsresult)>;
  using ClosedCallback = std::function<void(nsresult)>;

  TunnelSession(nsIAsyncInputStream* aLocalIn, nsIAsyncOutputStream* aLocalOut,
                const TunnelConfig& aConfig, bool aConnectUrgentStart,
                nsIEventTarget* aSocketTarget,
                EstablishedCallback&& aOnEstablished,
                FailureCallback&& aOnFailure, ClosedCallback&& aOnClosed);

  nsresult Start(const nsACString& aTargetAuthority,
                 Span<const uint8_t> aInitialPayload = {});
  nsresult StartPump();
  void Cancel(nsresult aStatus);

 private:
  friend class TunnelAttempt;
  friend class TunnelSessionTestPeer;
  class Impl;

  ~TunnelSession();

  nsresult StartAttempt(ProxyProtocol aProtocol);
  void OpenAttemptOnMain(uint64_t aGeneration, ProxyProtocol aProtocol,
                         const nsACString& aTargetAuthority);
  void BeginPreambleOnMain(uint64_t aGeneration, ProxyProtocol aProtocol,
                           const nsACString& aTargetAuthority);
  void FinishPreambleOnMain(uint64_t aGeneration, ProxyProtocol aProtocol,
                            const nsACString& aTargetAuthority,
                            nsresult aStatus, uint32_t aHttpStatus,
                            uint32_t aBodyBytes, uint32_t aStartedResources,
                            uint32_t aCommittedResources,
                            uint32_t aNativeCacheNewResources, bool aRootDone,
                            bool aTerminalFallback);
  void FinishPreambleOperationOnMain(uint64_t aGeneration,
                                     ProxyProtocol aProtocol, nsresult aStatus,
                                     uint32_t aHttpStatus, uint32_t aBodyBytes,
                                     bool aRootDone, bool aCompletedNormally,
                                     uint32_t aCompletedSuccessfulResources,
                                     uint32_t aNativeCacheNewResources);
  void PreambleTimeoutOnMain(uint64_t aGeneration, ProxyProtocol aProtocol);
  void PreambleDrainTimeoutOnMain(uint64_t aGeneration);
  void OpenConnectOnMain(uint64_t aGeneration, ProxyProtocol aProtocol,
                         const nsACString& aTargetAuthority);
  void NotifyOuterGateReady();
  void ReleaseOuterGate();
  void FailPreambleOnMain(nsresult aStatus);
  void CancelRequestOnMain(nsresult aStatus);
  void ClearRequestOnMain(uint64_t aGeneration, nsIRequest* aRequest);
  void ApplyConnectMetadata(uint64_t aGeneration, ProxyProtocol aProtocol,
                            nsresult aStatus, bool aConnectCodeKnown,
                            int32_t aConnectCode,
                            const Maybe<bool>& aPaddingHeaderPresent,
                            const nsACString& aOuterProtocol);
  void ApplyChannelStop(uint64_t aGeneration, ProxyProtocol aProtocol,
                        nsresult aStatus);
  void ApplyTransport(uint64_t aGeneration, ProxyProtocol aProtocol,
                      nsISocketTransport* aTransport,
                      nsIAsyncInputStream* aSocketIn,
                      nsIAsyncOutputStream* aSocketOut);
  void ApplyUpgradeFailure(uint64_t aGeneration, ProxyProtocol aProtocol,
                           nsresult aStatus);
  void ApplyEstablishmentTimeout(uint64_t aGeneration, ProxyProtocol aProtocol);
  void ApplyOpenFailure(uint64_t aGeneration, ProxyProtocol aProtocol,
                        nsresult aStatus);
  bool IsCurrentAttempt(uint64_t aGeneration, ProxyProtocol aProtocol) const;
  static bool ShouldGateOuterSession(const TunnelConfig& aConfig);
  void ResetAttemptState();
  void MaybeFinishAttempt();
  void TunnelReady();
  void Fail(nsresult aStatus);
  void CancelInternal(nsresult aStatus, bool aCancelRequest);

  UniquePtr<Impl> mImpl;
};

}  // namespace mozilla::naivefox

#endif
