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
class nsIInputStream;
class nsIRequest;
class nsIStreamListener;

namespace mozilla::naivefox {

// Internal Naive proxy authentication helper. This is not part of the
// embedded C ABI.
nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization);

struct ProxyPreambleResult final {
  nsresult mStatus = NS_ERROR_NOT_INITIALIZED;
  uint32_t mHttpStatus = 0;
  uint32_t mBodyBytes = 0;

  bool Succeeded() const {
    return NS_SUCCEEDED(mStatus) && mHttpStatus >= 200 && mHttpStatus < 300;
  }
};

using ProxyPreambleCallback = std::function<void(ProxyPreambleResult)>;

namespace detail {

constexpr bool PreambleBarrierReached(PreambleMode aMode, bool aRootDone,
                                      uint32_t aAssetCount,
                                      uint32_t aAssetsWithHeadersNotDone,
                                      uint32_t aAssetsWithHeadersOrDone,
                                      uint32_t aAssetsDone) {
  if (!aRootDone || aMode == PreambleMode::Off) {
    return false;
  }
  if (aMode == PreambleMode::DocumentComplete) {
    return true;
  }
  if (aMode == PreambleMode::TreeComplete) {
    return aAssetsDone == aAssetCount;
  }
  if (aMode == PreambleMode::TreeEarlyOverlap) {
    return aAssetCount > 0 && aAssetsWithHeadersNotDone > 0;
  }
  return aMode == PreambleMode::TreeOverlap &&
         aAssetsWithHeadersOrDone == aAssetCount;
}

constexpr bool PreambleOverlapsConnect(PreambleMode aMode) {
  return aMode == PreambleMode::TreeOverlap ||
         aMode == PreambleMode::TreeEarlyOverlap;
}

constexpr bool PreambleNeedsCompletionFallback(PreambleMode aMode,
                                               bool aBarrierFired) {
  return aMode == PreambleMode::TreeEarlyOverlap && !aBarrierFired;
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

 private:
  friend nsresult OpenProxyPreambleOperation(
      const nsACString&, const nsACString&, const nsACString&,
      const PreambleConfig&, ProxyProtocol, ProxyPreambleCallback&&,
      std::function<void()>&&, const Maybe<HostResolverRule>&,
      RefPtr<ProxyPreambleOperation>&);
  class Impl;

  ProxyPreambleOperation();
  ~ProxyPreambleOperation();

  nsresult Start(const nsACString& aProxyUrl, const nsACString& aProxyUser,
                 const nsACString& aProxyPassword,
                 const PreambleConfig& aConfig, ProxyProtocol aProtocol,
                 ProxyPreambleCallback&& aBarrierCallback,
                 std::function<void()>&& aFinishedCallback,
                 const Maybe<HostResolverRule>& aHostResolverRule);
  nsresult OnStartRequest(uint32_t aStreamId, nsIRequest* aRequest);
  nsresult OnDataAvailable(uint32_t aStreamId, nsIInputStream* aInputStream,
                           uint32_t aCount);
  void OnStopRequest(uint32_t aStreamId, nsresult aStatus);
  void FireBarrierCallback();
  void MaybeFireBarrier();
  void MaybeFinish();

  UniquePtr<Impl> mImpl;
};

nsresult OpenProxyPreambleOperation(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const PreambleConfig& aConfig,
    ProxyProtocol aProtocol, ProxyPreambleCallback&& aBarrierCallback,
    std::function<void()>&& aFinishedCallback,
    const Maybe<HostResolverRule>& aHostResolverRule,
    RefPtr<ProxyPreambleOperation>& aOperation);

nsresult OpenProxyPreamble(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const nsACString& aPath,
    uint32_t aMaxBytes, ProxyProtocol aProtocol,
    ProxyPreambleCallback&& aCallback,
    const Maybe<HostResolverRule>& aHostResolverRule = {},
    nsIRequest** aOpenedRequest = nullptr);

nsresult OpenNeckoTunnel(const nsACString& aProxyUrl,
                         const nsACString& aTargetAuthority,
                         const nsACString& aProxyUser,
                         const nsACString& aProxyPassword,
                         nsIHttpUpgradeListener* aUpgradeListener,
                         nsIStreamListener* aChannelListener,
                         const nsACString& aConnectPadding,
                         ProxyProtocol aProtocol,
                         const Maybe<HostResolverRule>& aHostResolverRule = {},
                         const nsTArray<ExtraHeader>& aExtraHeaders = {},
                         nsIRequest** aOpenedRequest = nullptr);

nsresult RunRawTunnelSmoke(const nsACString& aProxyUrl,
                           const nsACString& aTargetAuthority,
                           const nsACString& aProxyUser,
                           const nsACString& aProxyPassword,
                           ProxyProtocol aProtocol);

}  // namespace mozilla::naivefox

#endif
