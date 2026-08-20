/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_TunnelSession_h
#define netwerk_naivefox_TunnelSession_h

#include <functional>

#include "ProxyProtocol.h"
#include "mozilla/Maybe.h"
#include "mozilla/RefPtr.h"
#include "mozilla/Span.h"
#include "mozilla/UniquePtr.h"
#include "nsString.h"
#include "nscore.h"

class nsIAsyncInputStream;
class nsIAsyncOutputStream;
class nsIEventTarget;
class nsISocketTransport;

namespace mozilla::naivefox {

class TunnelAttempt;

struct TunnelConfig final {
  nsCString mProxyUrl;
  nsCString mProxyUser;
  nsCString mProxyPassword;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
};

class TunnelSession final : public RefCounted<TunnelSession> {
 public:
  MOZ_DECLARE_REFCOUNTED_TYPENAME(TunnelSession)

  using EstablishedCallback = std::function<void(const nsACString&, bool)>;
  using FailureCallback = std::function<void(nsresult)>;
  using ClosedCallback = std::function<void(nsresult)>;

  TunnelSession(nsIAsyncInputStream* aLocalIn, nsIAsyncOutputStream* aLocalOut,
                const TunnelConfig& aConfig, nsIEventTarget* aSocketTarget,
                EstablishedCallback&& aOnEstablished,
                FailureCallback&& aOnFailure, ClosedCallback&& aOnClosed);
  ~TunnelSession();

  nsresult Start(const nsACString& aTargetAuthority,
                 Span<const uint8_t> aInitialPayload = {});
  nsresult StartPump();
  void Cancel(nsresult aStatus);

 private:
  friend class TunnelAttempt;
  class Impl;

  nsresult StartAttempt(ProxyProtocol aProtocol);
  void OpenAttemptOnMain(uint64_t aGeneration, ProxyProtocol aProtocol,
                         const nsACString& aTargetAuthority);
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
  void ResetAttemptState();
  void MaybeFinishAttempt();
  void TunnelReady();
  void Fail(nsresult aStatus);

  UniquePtr<Impl> mImpl;
};

}  // namespace mozilla::naivefox

#endif
