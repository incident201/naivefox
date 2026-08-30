/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NeckoTunnel.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <limits>
#include <utility>

#include "AutoFallback.h"
#include "NativeStylePreloadActivation.h"
#include "NativeStylePreloadChannel.h"
#include "ReferrerInfo.h"
#include "RuntimeLogging.h"
#include "StylePreloadKind.h"
#include "base/process_util.h"
#include "mozilla/AppShutdown.h"
#include "mozilla/Base64.h"
#include "mozilla/ClearOnShutdown.h"
#include "mozilla/ErrorNames.h"
#include "mozilla/Mutex.h"
#include "mozilla/OriginAttributes.h"
#include "mozilla/ReentrantMonitor.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "mozilla/StaticPrefs_dom.h"
#include "mozilla/StaticPtr.h"
#include "mozilla/TextUtils.h"
#include "nsCOMPtr.h"
#include "nsError.h"
#include "nsHtml5LeanPreloadDescriptor.h"
#include "nsHtml5SpeculativeScanner.h"
#include "nsHtml5StylePreloadDescriptor.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIChannel.h"
#include "nsIClassOfService.h"
#include "nsIContentPolicy.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsIInputStream.h"
#include "nsIInterfaceRequestor.h"
#include "nsILoadGroup.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
#include "nsIProgressEventSink.h"
#include "nsIProtocolHandler.h"
#include "nsIProtocolProxyService.h"
#include "nsIProxiedChannel.h"
#include "nsIProxiedProtocolHandler.h"
#include "nsIProxyInfo.h"
#include "nsIReferrerInfo.h"
#include "nsIRequest.h"
#include "nsIScriptSecurityManager.h"
#include "nsISocketTransport.h"
#include "nsIStreamListener.h"
#include "nsITLSSocketControl.h"
#include "nsIThread.h"
#include "nsIThreadRetargetableRequest.h"
#include "nsIThreadRetargetableStreamListener.h"
#include "nsITimer.h"
#include "nsITransportSecurityInfo.h"
#include "nsIURI.h"
#include "nsNetCID.h"
#include "nsNetUtil.h"
#include "nsProxyInfo.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

namespace {

constexpr uint32_t kResponseLimit = 64 * 1024;
constexpr auto kExpectedMarker = "naivefox-fixture-small"_ns;
StaticRefPtr<nsIThread> sNativeParserThread;

const char* ProtocolName(ProxyProtocol aProtocol) {
  return aProtocol == ProxyProtocol::H3 ? "h3" : "h2";
}

already_AddRefed<nsISerialEventTarget> NativeParserTarget() {
  MOZ_ASSERT(NS_IsMainThread());
  if (AppShutdown::IsInOrBeyond(ShutdownPhase::AppShutdownNetTeardown)) {
    return nullptr;
  }
  if (!sNativeParserThread) {
    nsCOMPtr<nsIThread> thread;
    if (NS_FAILED(NS_NewNamedThread("HTML5 Parser", getter_AddRefs(thread)))) {
      return nullptr;
    }
    sNativeParserThread = thread.forget();
    RunOnShutdown([] { ShutdownProxyPreambleParserThread(); },
                  ShutdownPhase::AppShutdownNetTeardown);
  }
  nsCOMPtr<nsISerialEventTarget> target = sNativeParserThread.get();
  return target.forget();
}

// Lean analogue of the replacement HttpChannelChild request. The physical
// parent nsHttpChannel remains on the main thread; background-channel DATA is
// delivered according to this logical request's current ODA target.
class NativeRootReplacementRequest final : public nsIThreadRetargetableRequest {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSITHREADRETARGETABLEREQUEST

 private:
  ~NativeRootReplacementRequest() = default;

  nsCOMPtr<nsISerialEventTarget> mDeliveryTarget;
};

NS_IMPL_ISUPPORTS(NativeRootReplacementRequest, nsIThreadRetargetableRequest)

NS_IMETHODIMP NativeRootReplacementRequest::RetargetDeliveryTo(
    nsISerialEventTarget* aNewTarget) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!aNewTarget) {
    return NS_ERROR_INVALID_ARG;
  }
  mDeliveryTarget = aNewTarget;
  return NS_OK;
}

NS_IMETHODIMP NativeRootReplacementRequest::GetDeliveryTarget(
    nsISerialEventTarget** aTarget) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!aTarget) {
    return NS_ERROR_INVALID_ARG;
  }
  nsCOMPtr<nsISerialEventTarget> target = mDeliveryTarget;
  target.forget(aTarget);
  return NS_OK;
}

struct ExplicitProxyRoute {
  nsCOMPtr<nsIURI> mProxyUri;
  nsCOMPtr<nsIProxyInfo> mProxyInfo;
};

class ProxyPreambleListener final : public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  ProxyPreambleListener(uint32_t aMaxBytes, ProxyPreambleCallback&& aCallback)
      : mMaxBytes(aMaxBytes), mCallback(std::move(aCallback)) {}

 private:
  ~ProxyPreambleListener() = default;

  const uint32_t mMaxBytes;
  ProxyPreambleCallback mCallback;
  uint32_t mBodyBytes = 0;
  uint32_t mHttpStatus = 0;
  nsresult mFailure = NS_OK;
};

NS_IMPL_ISUPPORTS(ProxyPreambleListener, nsIStreamListener, nsIRequestObserver)

NS_IMETHODIMP ProxyPreambleListener::OnStartRequest(nsIRequest* aRequest) {
  nsCOMPtr<nsIHttpChannel> channel = do_QueryInterface(aRequest);
  if (!channel) {
    mFailure = NS_ERROR_UNEXPECTED;
    return mFailure;
  }
  nsresult rv = channel->GetResponseStatus(&mHttpStatus);
  if (NS_FAILED(rv)) {
    mFailure = rv;
  }
  return rv;
}

NS_IMETHODIMP ProxyPreambleListener::OnDataAvailable(
    nsIRequest* aRequest, nsIInputStream* aInputStream, uint64_t aOffset,
    uint32_t aCount) {
  if (aCount > mMaxBytes - mBodyBytes) {
    mFailure = NS_ERROR_FILE_TOO_BIG;
    return mFailure;
  }

  char discard[4096];
  uint32_t remaining = aCount;
  while (remaining > 0) {
    uint32_t read = 0;
    nsresult rv = aInputStream->Read(
        discard, std::min<uint32_t>(remaining, sizeof(discard)), &read);
    if (NS_FAILED(rv) || read == 0) {
      mFailure = NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
      return mFailure;
    }
    remaining -= read;
    mBodyBytes += read;
  }
  return NS_OK;
}

NS_IMETHODIMP ProxyPreambleListener::OnStopRequest(nsIRequest* aRequest,
                                                   nsresult aStatus) {
  if (mCallback) {
    auto callback = std::move(mCallback);
    callback(
        {NS_FAILED(mFailure) ? mFailure : aStatus, mHttpStatus, mBodyBytes});
  }
  return NS_OK;
}

class TunnelSmoke final : public nsIHttpUpgradeListener,
                          public nsIStreamListener,
                          public nsIInputStreamCallback,
                          public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIHTTPUPGRADELISTENER
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  TunnelSmoke(const nsACString& aRequest, ProxyProtocol aProtocol)
      : mMutex("NaiveFox::TunnelSmoke"),
        mRequest(aRequest),
        mProtocol(aProtocol) {}

  bool Complete() {
    MutexAutoLock lock(mMutex);
    return mComplete;
  }

  void Snapshot(nsresult& aResult, bool& aConnectCodeKnown,
                int32_t& aConnectCode, bool& aTransportAvailable,
                nsACString& aOuterProtocol) {
    MutexAutoLock lock(mMutex);
    aResult = mResult;
    aConnectCodeKnown = mConnectCodeKnown;
    aConnectCode = mConnectCode;
    aTransportAvailable = mTransportAvailable;
    aOuterProtocol = mOuterProtocol;
  }

 private:
  ~TunnelSmoke() = default;

  void Finish(nsresult aResult) {
    bool notify = false;
    {
      MutexAutoLock lock(mMutex);
      if (!mComplete) {
        mComplete = true;
        mResult = aResult;
        notify = true;
      }
    }
    if (notify) {
      (void)NS_DispatchToMainThread(
          NS_NewRunnableFunction("NaiveFox::TunnelSmokeComplete", []() {}));
    }
  }

  void PrintFailure(const char* aWhere, nsresult aResult) {
    nsAutoCString name;
    GetErrorName(aResult, name);
    std::fprintf(stderr, "%s failed: %s (0x%08x)\n", aWhere, name.get(),
                 static_cast<unsigned>(aResult));
  }

  void FailStreams(nsresult aResult) {
    if (mSocketIn) {
      (void)mSocketIn->CloseWithStatus(aResult);
    }
    if (mSocketOut) {
      (void)mSocketOut->CloseWithStatus(aResult);
    }
    Finish(aResult);
  }

  void FinishResponse() {
    if (mResponse.Find(kExpectedMarker) == kNotFound) {
      FailStreams(NS_ERROR_UNEXPECTED);
      return;
    }
    if (mSocketOut) {
      (void)mSocketOut->CloseWithStatus(NS_OK);
    }
    Finish(NS_OK);
  }

  Mutex mMutex;
  bool mComplete MOZ_GUARDED_BY(mMutex) = false;
  nsresult mResult MOZ_GUARDED_BY(mMutex) = NS_ERROR_NOT_INITIALIZED;
  int32_t mConnectCode MOZ_GUARDED_BY(mMutex) = -1;
  bool mConnectCodeKnown MOZ_GUARDED_BY(mMutex) = false;
  bool mTransportAvailable MOZ_GUARDED_BY(mMutex) = false;
  bool mChannelStopped MOZ_GUARDED_BY(mMutex) = false;
  bool mUpgradeFailed MOZ_GUARDED_BY(mMutex) = false;
  nsresult mUpgradeFailure MOZ_GUARDED_BY(mMutex) = NS_ERROR_NOT_INITIALIZED;
  nsCString mOuterProtocol MOZ_GUARDED_BY(mMutex);

  nsCOMPtr<nsIAsyncInputStream> mSocketIn;
  nsCOMPtr<nsIAsyncOutputStream> mSocketOut;
  nsCString mRequest;
  ProxyProtocol mProtocol;
  uint32_t mWriteOffset = 0;
  nsCString mResponse;
};

NS_IMPL_ISUPPORTS(TunnelSmoke, nsIHttpUpgradeListener, nsIStreamListener,
                  nsIRequestObserver, nsIInputStreamCallback,
                  nsIOutputStreamCallback)

NS_IMETHODIMP TunnelSmoke::OnStartRequest(nsIRequest* aRequest) {
  nsCOMPtr<nsIProxiedChannel> proxied = do_QueryInterface(aRequest);
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(aRequest);
  if (!proxied || !http) {
    Finish(NS_ERROR_UNEXPECTED);
    return NS_ERROR_UNEXPECTED;
  }

  int32_t connectCode = -1;
  nsresult rv = proxied->GetHttpProxyConnectResponseCode(&connectCode);
  if (NS_FAILED(rv)) {
    Finish(rv);
    return rv;
  }
  nsAutoCString outerProtocol;
  rv = http->GetProtocolVersion(outerProtocol);
  if (NS_FAILED(rv)) {
    Finish(rv);
    return rv;
  }

  {
    MutexAutoLock lock(mMutex);
    mConnectCodeKnown = true;
    mConnectCode = connectCode;
    mOuterProtocol = outerProtocol;
  }
  return NS_OK;
}

NS_IMETHODIMP TunnelSmoke::OnDataAvailable(nsIRequest* aRequest,
                                           nsIInputStream* aInputStream,
                                           uint64_t aOffset, uint32_t aCount) {
  char discard[1024];
  while (aCount > 0) {
    uint32_t read = 0;
    MOZ_TRY(aInputStream->Read(
        discard, std::min<uint32_t>(aCount, sizeof(discard)), &read));
    if (read == 0) {
      return NS_ERROR_UNEXPECTED;
    }
    aCount -= read;
  }
  return NS_OK;
}

NS_IMETHODIMP TunnelSmoke::OnStopRequest(nsIRequest* aRequest,
                                         nsresult aStatus) {
  bool upgradeFailed = false;
  nsresult upgradeFailure = NS_ERROR_FAILURE;
  {
    MutexAutoLock lock(mMutex);
    mChannelStopped = true;
    upgradeFailed = mUpgradeFailed;
    upgradeFailure = mUpgradeFailure;
  }
  if (NS_FAILED(aStatus)) {
    PrintFailure("Proxy channel", aStatus);
    Finish(aStatus);
    return NS_OK;
  }

  int32_t connectCode;
  {
    MutexAutoLock lock(mMutex);
    connectCode = mConnectCode;
  }
  if (connectCode == 407) {
    Finish(NS_ERROR_PROXY_AUTHENTICATION_FAILED);
  } else if (connectCode != 200) {
    Finish(NS_ERROR_FAILURE);
  } else if (upgradeFailed) {
    Finish(upgradeFailure);
  }
  return NS_OK;
}

NS_IMETHODIMP TunnelSmoke::OnTransportAvailable(
    nsISocketTransport* aTransport, nsIAsyncInputStream* aSocketIn,
    nsIAsyncOutputStream* aSocketOut) {
  MOZ_TRY(aSocketIn->AsyncWait(nullptr, 0, 0, nullptr));
  MOZ_TRY(aSocketOut->AsyncWait(nullptr, 0, 0, nullptr));

  if (mProtocol == ProxyProtocol::H2) {
    nsAutoCString alpn;
    nsCOMPtr<nsITLSSocketControl> tlsSocketControl;
    nsresult alpnRv =
        aTransport->GetTlsSocketControl(getter_AddRefs(tlsSocketControl));
    if (NS_SUCCEEDED(alpnRv) && tlsSocketControl) {
      nsCOMPtr<nsITransportSecurityInfo> securityInfo;
      alpnRv = tlsSocketControl->GetSecurityInfo(getter_AddRefs(securityInfo));
      if (NS_SUCCEEDED(alpnRv) && securityInfo) {
        alpnRv = securityInfo->GetNegotiatedNPN(alpn);
      }
    }
    if (NS_FAILED(alpnRv) || !alpn.EqualsLiteral("h2")) {
      (void)aSocketIn->CloseWithStatus(NS_ERROR_FAILURE);
      (void)aSocketOut->CloseWithStatus(NS_ERROR_FAILURE);
      Finish(NS_ERROR_FAILURE);
      return NS_ERROR_FAILURE;
    }
  }

  mSocketIn = aSocketIn;
  mSocketOut = aSocketOut;
  {
    MutexAutoLock lock(mMutex);
    mTransportAvailable = true;
  }

  nsresult rv = mSocketOut->AsyncWait(this, 0, 0, nullptr);
  if (NS_FAILED(rv)) {
    FailStreams(rv);
    return rv;
  }
  rv = mSocketIn->AsyncWait(this, 0, 0, nullptr);
  if (NS_FAILED(rv)) {
    FailStreams(rv);
  }
  return rv;
}

NS_IMETHODIMP TunnelSmoke::OnUpgradeFailed(nsresult aErrorCode) {
  PrintFailure("Raw CONNECT upgrade", aErrorCode);
  bool channelStopped = false;
  {
    MutexAutoLock lock(mMutex);
    mUpgradeFailed = true;
    mUpgradeFailure = aErrorCode;
    channelStopped = mChannelStopped;
  }
  if (channelStopped) {
    Finish(aErrorCode);
  }
  return NS_OK;
}

NS_IMETHODIMP TunnelSmoke::OnWebSocketConnectionAvailable(
    mozilla::net::WebSocketConnectionBase* aConnection) {
  return NS_ERROR_NOT_IMPLEMENTED;
}

NS_IMETHODIMP TunnelSmoke::OnOutputStreamReady(nsIAsyncOutputStream* aStream) {
  while (mWriteOffset < mRequest.Length()) {
    uint32_t written = 0;
    nsresult rv = aStream->Write(mRequest.get() + mWriteOffset,
                                 mRequest.Length() - mWriteOffset, &written);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      rv = aStream->AsyncWait(this, 0, 0, nullptr);
      if (NS_FAILED(rv)) {
        FailStreams(rv);
      }
      return rv;
    }
    if (NS_FAILED(rv) || written == 0) {
      rv = NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
      FailStreams(rv);
      return rv;
    }
    mWriteOffset += written;
  }
  nsresult rv = aStream->CloseWithStatus(NS_OK);
  if (NS_FAILED(rv)) {
    FailStreams(rv);
  }
  return rv;
}

NS_IMETHODIMP TunnelSmoke::OnInputStreamReady(nsIAsyncInputStream* aStream) {
  char buffer[4096];
  while (true) {
    uint32_t read = 0;
    nsresult rv = aStream->Read(buffer, sizeof(buffer), &read);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      rv = aStream->AsyncWait(this, 0, 0, nullptr);
      if (NS_FAILED(rv)) {
        FailStreams(rv);
      }
      return rv;
    }
    if (rv == NS_BASE_STREAM_CLOSED || (NS_SUCCEEDED(rv) && read == 0)) {
      FinishResponse();
      return NS_OK;
    }
    if (NS_FAILED(rv)) {
      FailStreams(rv);
      return rv;
    }
    if (read > kResponseLimit - mResponse.Length()) {
      FailStreams(NS_ERROR_FILE_TOO_BIG);
      return NS_ERROR_FILE_TOO_BIG;
    }
    mResponse.Append(buffer, read);
  }
}

nsresult GetSystemPrincipal(nsIPrincipal** aPrincipal) {
  nsCOMPtr<nsIScriptSecurityManager> securityManager =
      do_GetService(NS_SCRIPTSECURITYMANAGER_CONTRACTID);
  if (!securityManager) {
    return NS_ERROR_FAILURE;
  }
  return securityManager->GetSystemPrincipal(aPrincipal);
}

nsresult MakeBasicAuthorization(const nsACString& aUser,
                                const nsACString& aPassword,
                                nsACString& aAuthorization) {
  if (aUser.IsEmpty() && aPassword.IsEmpty()) {
    aAuthorization.Truncate();
    return NS_OK;
  }

  nsAutoCString userPass(aUser);
  userPass.Append(':');
  userPass.Append(aPassword);
  aAuthorization.AssignLiteral("Basic ");
  return Base64EncodeAppend(userPass, aAuthorization);
}

bool IsValidPreamblePath(const nsACString& aPath) {
  if (aPath.IsEmpty() || aPath.Length() > 2048 || aPath.First() != '/' ||
      (aPath.Length() >= 2 && aPath.CharAt(1) == '/')) {
    return false;
  }
  for (size_t index = 0; index < aPath.Length(); ++index) {
    const unsigned char value = aPath.CharAt(index);
    if (value <= 0x20 || value >= 0x7f || value == '#' || value == '\\') {
      return false;
    }
    if (value == '%') {
      if (aPath.Length() - index < 3 ||
          !IsAsciiHexDigit(aPath.CharAt(index + 1)) ||
          !IsAsciiHexDigit(aPath.CharAt(index + 2))) {
        return false;
      }
      index += 2;
    }
  }
  return true;
}

nsresult BuildExplicitProxyRoute(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, ProxyProtocol aProtocol,
    const Maybe<HostResolverRule>& aHostResolverRule,
    bool aIncludeAuthorization, ExplicitProxyRoute& aRoute) {
  if (aProtocol == ProxyProtocol::Auto) {
    return NS_ERROR_NOT_IMPLEMENTED;
  }

  nsCOMPtr<nsIURI> proxyUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(proxyUri), aProxyUrl));

  nsAutoCString proxyScheme;
  nsAutoCString proxyHost;
  nsAutoCString proxyUserPass;
  int32_t proxyPort = -1;
  MOZ_TRY(proxyUri->GetScheme(proxyScheme));
  MOZ_TRY(proxyUri->GetAsciiHost(proxyHost));
  MOZ_TRY(proxyUri->GetUserPass(proxyUserPass));
  MOZ_TRY(proxyUri->GetPort(&proxyPort));
  if (!proxyScheme.EqualsLiteral("https") || proxyHost.IsEmpty() ||
      !proxyUserPass.IsEmpty()) {
    return NS_ERROR_INVALID_ARG;
  }
  if (proxyPort == -1) {
    proxyPort = 443;
  }
  if (proxyPort <= 0 || proxyPort > std::numeric_limits<uint16_t>::max()) {
    return NS_ERROR_INVALID_ARG;
  }

  nsAutoCString authorization;
  if (aIncludeAuthorization) {
    MOZ_TRY(MakeBasicAuthorization(aProxyUser, aProxyPassword, authorization));
  }

  nsCOMPtr<nsIProtocolProxyService> proxyService =
      do_GetService(NS_PROTOCOLPROXYSERVICE_CONTRACTID);
  if (!proxyService) {
    return NS_ERROR_FAILURE;
  }
  uint32_t proxyFlags = nsIProxyInfo::TRANSPARENT_PROXY_RESOLVES_HOST |
                        nsIProxyInfo::ALWAYS_TUNNEL_VIA_PROXY;
  nsCOMPtr<nsIProxyInfo> proxyInfo;
  if (aProtocol == ProxyProtocol::H3) {
    proxyFlags |= nsIProxyInfo::DISABLE_HTTP3_PROXY_FALLBACK |
                  nsIProxyInfo::DO_NOT_FORCE_HTTP3_PROXY_PMTUD;
    MOZ_TRY(proxyService->NewMASQUEProxyInfo(
        proxyHost, proxyPort,
        "/.well-known/masque/udp/{target_host}/{target_port}/"_ns,
        authorization, "naivefox-raw-tunnel"_ns, proxyFlags, UINT32_MAX,
        nullptr, getter_AddRefs(proxyInfo)));
  } else {
    MOZ_TRY(proxyService->NewProxyInfo("https"_ns, proxyHost, proxyPort,
                                       authorization, "naivefox-raw-tunnel"_ns,
                                       proxyFlags, UINT32_MAX, nullptr,
                                       getter_AddRefs(proxyInfo)));
  }
  nsCOMPtr<net::nsProxyInfo> concreteProxy = do_QueryInterface(proxyInfo);
  if (!concreteProxy) {
    return NS_ERROR_FAILURE;
  }
  proxyInfo = concreteProxy->CloneProxyInfoWithNewResolveFlags(
      nsIProtocolProxyService::RESOLVE_PREFER_HTTPS_PROXY |
      nsIProtocolProxyService::RESOLVE_ALWAYS_TUNNEL);
  concreteProxy = do_QueryInterface(proxyInfo);
  if (!concreteProxy) {
    return NS_ERROR_FAILURE;
  }
  if (aHostResolverRule &&
      aHostResolverRule->mLogicalHost.Equals(
          proxyHost, nsCaseInsensitiveCStringComparator) &&
      !aHostResolverRule->mPhysicalHost.IsEmpty()) {
    concreteProxy->SetNaiveFoxPhysicalHost(aHostResolverRule->mPhysicalHost);
  }

  aRoute.mProxyUri = proxyUri;
  aRoute.mProxyInfo = proxyInfo;
  return NS_OK;
}

}  // namespace

void ShutdownProxyPreambleParserThread() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sNativeParserThread) {
    return;
  }
  (void)sNativeParserThread->Shutdown();
  sNativeParserThread = nullptr;
}

class ProxyPreambleOperation::Impl final {
 public:
  struct Stream final {
    nsCOMPtr<nsIURI> mUri;
    nsCOMPtr<nsIRequest> mRequest;
    nsCOMPtr<nsIStreamListener> mPendingOpenListener;
    uint32_t mHttpStatus = 0;
    bool mHeadersReceived = false;
    bool mResponseHeadersReceived = false;
    bool mRequestCommitted = false;
    bool mNativeCacheNewEntry = false;
    uint64_t mNativeProcessStyleRequestId = 0;
    bool mDone = false;
  };

  ExplicitProxyRoute mRoute;
  nsCOMPtr<nsIPrincipal> mPrincipal;
  nsCOMPtr<nsILoadGroup> mLoadGroup;
  PreambleConfig mConfig;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
  ProxyPreambleCallback mBarrierCallback;
  ProxyPreambleFinishedCallback mFinishedCallback;
  nsTArray<Stream> mStreams;
  nsTArray<nsCString> mDiscoveredSpecs;
  nsCOMPtr<nsIReferrerInfo> mRootReferrerInfo;
  OriginAttributes mRootOriginAttributes;
  nsCOMPtr<nsISerialEventTarget> mNativeParserTarget;
  // The document-handoff arm constructs the lean consumer on the main thread,
  // matching nsHtml5StreamParser construction. After publication, Feed(),
  // Finish(), and destruction occur only on the serial parser target. The
  // original native-parser control still constructs lazily on that target.
  UniquePtr<nsHtml5SpeculativeScanner> mNativeParserScanner;
  std::atomic<uint64_t> mNativeParserGeneration{1};
  uint64_t mNativeStyleActivationRequestId = 0;
  uint64_t mNativeRootReplacementRequestId = 0;
  uint64_t mNativeProcessRootRequestId = 0;
  uint64_t mNativeRootReplacementChannelId = 0;
  nsCOMPtr<nsIThreadRetargetableRequest> mNativeRootLogicalRequest;
  uint64_t mConnectionId = 0;
  std::atomic<uint64_t> mNativeParserConsumerGeneration{0};
  nsCString mRootPrePath;
  nsCString mHtml;
  uint32_t mParseOffset = 0;
  uint32_t mBodyBytes = 0;
  std::atomic<uint32_t> mNativeParserNextSequence{0};
  uint32_t mNativeProcessNextSequence = 1;
  uint32_t mNativeProcessDiscoverySequence = 0;
  std::atomic<uint32_t> mNativeParserPendingMainCallbacks{0};
  std::atomic<uint32_t> mNativeParserRetargetBodyBytes{0};
  std::atomic<nsresult> mNativeParserRetargetFailure{NS_OK};
  nsresult mFirstFailure = NS_OK;
  bool mParserInHead = true;
  bool mRootDone = false;
  bool mRootCompletedSuccessfully = false;
  bool mBarrierFired = false;
  bool mDocumentBarrierTaskDispatched = false;
  bool mFirstResourceBodyBufferConsumed = false;
  bool mDocumentRootStopDeferred = false;
  nsresult mDocumentRootStopStatus = NS_OK;
  bool mDeferredImageOpenDispatched = false;
  bool mFinishedFired = false;
  bool mConnectHandoffAdmitted = false;
  bool mTunnelApplicationActive = false;
  bool mTunnelServerApplicationActive = false;
  bool mNavigationStopStyleResponseStarted = false;
  bool mNavigationStopIssued = false;
  bool mNavigationStopStyleAborted = false;
  bool mCancelled = false;
  bool mAllStreamsCompletedNormally = true;
  bool mHaveRootOriginAttributes = false;
  std::atomic<bool> mNativeParserFinishQueued{false};
  bool mNativeParserFinished = false;
  bool mNativeParserDescriptorAccepted = false;
  uint32_t mNativeParserResourceDescriptorsAccepted = 0;
  nsCOMPtr<nsIURI> mNativeParserBaseURI;
  bool mNativeParserBaseSeen = false;
  bool mNativeParserContractFailed = false;
  bool mNativeParserReplacementInstallQueued = false;
  bool mNativeRootReplacementSetupReady = false;
  bool mNativeRootForwardedStartReceived = false;
  bool mNativeProcessRootReady = false;
  std::atomic<bool> mNativeParserConsumerReady{false};
  std::atomic<bool> mNativeParserRetargetArmed{false};
  std::atomic<bool> mNativeParserRetargetListenerChainChecked{false};
  std::atomic<bool> mNativeParserRetargetInstalled{false};
  std::atomic<bool> mNativeParserRetargetDataFinished{false};
  std::atomic<bool> mNativeParserFirstRetargetFeedLogged{false};
  bool mNativeParserRootSuspended = false;
  bool mNativeParserFirstFeedLogged = false;
  uint32_t mCompletedSuccessfulResources = 0;
};

class ProxyPreambleOperation::StreamListener final
    : public nsIThreadRetargetableStreamListener,
      public nsIInterfaceRequestor,
      public nsIProgressEventSink {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER
  NS_DECL_NSITHREADRETARGETABLESTREAMLISTENER
  NS_DECL_NSIINTERFACEREQUESTOR
  NS_DECL_NSIPROGRESSEVENTSINK

  StreamListener(ProxyPreambleOperation* aOwner, uint32_t aStreamId)
      : mOwnerMonitor("ProxyPreambleOperation::StreamListener::mOwnerMonitor"),
        mOwner(aOwner),
        mStreamId(aStreamId) {}

 private:
  ~StreamListener() = default;

  // RetargetDeliveryTo synchronously re-enters CheckListenerChain from inside
  // OnStartRequest. A reentrant monitor protects the cross-thread owner while
  // permitting that source-faithful call stack.
  ReentrantMonitor mOwnerMonitor MOZ_UNANNOTATED;
  RefPtr<ProxyPreambleOperation> mOwner MOZ_GUARDED_BY(mOwnerMonitor);
  const uint32_t mStreamId;
};

NS_IMPL_ISUPPORTS(ProxyPreambleOperation::StreamListener, nsIStreamListener,
                  nsIRequestObserver, nsIThreadRetargetableStreamListener,
                  nsIInterfaceRequestor, nsIProgressEventSink)

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::GetInterface(
    const nsIID& aIID, void** aResult) {
  if (aIID.Equals(NS_GET_IID(nsIProgressEventSink))) {
    return QueryInterface(aIID, aResult);
  }
  return NS_ERROR_NO_INTERFACE;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnStatus(
    nsIRequest* aRequest, nsresult aStatus, const char16_t* aStatusArg) {
  ReentrantMonitorAutoEnter ownerLock(mOwnerMonitor);
  if (mOwner && aStatus == NS_NET_STATUS_WAITING_FOR) {
    mOwner->OnRequestCommitted(mStreamId, aRequest);
  }
  return NS_OK;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnProgress(
    nsIRequest* aRequest, int64_t aProgress, int64_t aProgressMax) {
  return NS_OK;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnStartRequest(
    nsIRequest* aRequest) {
  ReentrantMonitorAutoEnter ownerLock(mOwnerMonitor);
  return mOwner ? mOwner->OnStartRequest(mStreamId, aRequest)
                : NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnDataAvailable(
    nsIRequest* aRequest, nsIInputStream* aInputStream, uint64_t aOffset,
    uint32_t aCount) {
  ReentrantMonitorAutoEnter ownerLock(mOwnerMonitor);
  return mOwner ? mOwner->OnDataAvailable(mStreamId, aInputStream, aCount)
                : NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnStopRequest(
    nsIRequest* aRequest, nsresult aStatus) {
  ReentrantMonitorAutoEnter ownerLock(mOwnerMonitor);
  if (!mOwner) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  mOwner->OnStopRequest(mStreamId, aStatus);
  mOwner = nullptr;
  return NS_OK;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::CheckListenerChain() {
  ReentrantMonitorAutoEnter ownerLock(mOwnerMonitor);
  return mOwner ? mOwner->CheckNativeParserRetargetListener(mStreamId)
                : NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnDataFinished(
    nsresult aStatus) {
  ReentrantMonitorAutoEnter ownerLock(mOwnerMonitor);
  if (!mOwner) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  // Only the root request is retargeted. Resource pumps retain their ordinary
  // main-thread listener lifecycle and must not enter the root parser-finish
  // contract if a platform configuration happens to surface OnDataFinished.
  if (!detail::PreambleUsesRetargetedRootDelivery(mOwner->mImpl->mConfig.mMode,
                                                  mStreamId)) {
    return NS_OK;
  }
  if (mOwner->mImpl->mConfig.mMode ==
      PreambleMode::TreeNativeParserRootRendezvousOverlap) {
    // The physical parent pump stays on main. Its terminal event is forwarded
    // through the logical replacement background actor from OnStopRequest.
    return NS_OK;
  }
  return mOwner->OnRetargetedDataFinished(mStreamId, aStatus);
}

namespace {

void LowercaseAscii(nsACString& aValue) {
  char* values = aValue.BeginWriting();
  for (uint32_t index = 0; index < aValue.Length(); ++index) {
    char& value = values[index];
    if (value >= 'A' && value <= 'Z') {
      value += 'a' - 'A';
    }
  }
}

bool IsHtmlSpace(char aValue) {
  return aValue == ' ' || aValue == '\t' || aValue == '\r' || aValue == '\n' ||
         aValue == '\f';
}

bool StartsWithTagName(const nsACString& aLowerTag, const nsACString& aName) {
  if (aLowerTag.Length() < aName.Length() + 2 || aLowerTag.CharAt(0) != '<' ||
      !Substring(aLowerTag, 1, aName.Length()).Equals(aName)) {
    return false;
  }
  const char boundary = aLowerTag.CharAt(aName.Length() + 1);
  return IsHtmlSpace(boundary) || boundary == '>' || boundary == '/';
}

bool ExtractQuotedAttribute(const nsACString& aTag, const nsACString& aLowerTag,
                            const nsACString& aName, nsACString& aValue) {
  uint32_t searchFrom = 0;
  while (searchFrom < aLowerTag.Length()) {
    int32_t found = aLowerTag.Find(aName, searchFrom);
    if (found < 0) {
      return false;
    }
    const uint32_t nameStart = static_cast<uint32_t>(found);
    const uint32_t nameEnd = nameStart + aName.Length();
    if ((nameStart == 0 || IsHtmlSpace(aLowerTag.CharAt(nameStart - 1))) &&
        (nameEnd == aLowerTag.Length() ||
         IsHtmlSpace(aLowerTag.CharAt(nameEnd)) ||
         aLowerTag.CharAt(nameEnd) == '=')) {
      uint32_t cursor = nameEnd;
      while (cursor < aLowerTag.Length() &&
             IsHtmlSpace(aLowerTag.CharAt(cursor))) {
        ++cursor;
      }
      if (cursor < aLowerTag.Length() && aLowerTag.CharAt(cursor) == '=') {
        ++cursor;
        while (cursor < aLowerTag.Length() &&
               IsHtmlSpace(aLowerTag.CharAt(cursor))) {
          ++cursor;
        }
        if (cursor < aLowerTag.Length() && (aLowerTag.CharAt(cursor) == '\'' ||
                                            aLowerTag.CharAt(cursor) == '"')) {
          const char quote = aLowerTag.CharAt(cursor++);
          int32_t end = aLowerTag.FindChar(quote, cursor);
          if (end > static_cast<int32_t>(cursor)) {
            aValue.Assign(
                Substring(aTag, cursor, static_cast<uint32_t>(end) - cursor));
            return true;
          }
        }
      }
    }
    searchFrom = nameEnd;
  }
  return false;
}

bool GetHtmlAttributeValue(const nsACString& aLowerTag, const nsACString& aName,
                           nsACString& aValue) {
  aValue.Truncate();
  uint32_t cursor = 1;
  if (cursor < aLowerTag.Length() && aLowerTag.CharAt(cursor) == '/') {
    ++cursor;
  }
  while (cursor < aLowerTag.Length() &&
         !IsHtmlSpace(aLowerTag.CharAt(cursor)) &&
         aLowerTag.CharAt(cursor) != '>' && aLowerTag.CharAt(cursor) != '/') {
    ++cursor;
  }

  while (cursor < aLowerTag.Length()) {
    while (cursor < aLowerTag.Length() &&
           IsHtmlSpace(aLowerTag.CharAt(cursor))) {
      ++cursor;
    }
    if (cursor >= aLowerTag.Length() || aLowerTag.CharAt(cursor) == '>' ||
        aLowerTag.CharAt(cursor) == '/') {
      return false;
    }

    const uint32_t nameStart = cursor;
    while (cursor < aLowerTag.Length() &&
           !IsHtmlSpace(aLowerTag.CharAt(cursor)) &&
           aLowerTag.CharAt(cursor) != '=' && aLowerTag.CharAt(cursor) != '>' &&
           aLowerTag.CharAt(cursor) != '/') {
      ++cursor;
    }
    const uint32_t nameEnd = cursor;
    while (cursor < aLowerTag.Length() &&
           IsHtmlSpace(aLowerTag.CharAt(cursor))) {
      ++cursor;
    }

    uint32_t valueStart = cursor;
    uint32_t valueEnd = cursor;
    if (cursor < aLowerTag.Length() && aLowerTag.CharAt(cursor) == '=') {
      ++cursor;
      while (cursor < aLowerTag.Length() &&
             IsHtmlSpace(aLowerTag.CharAt(cursor))) {
        ++cursor;
      }
      if (cursor < aLowerTag.Length() && (aLowerTag.CharAt(cursor) == '\'' ||
                                          aLowerTag.CharAt(cursor) == '"')) {
        const char quote = aLowerTag.CharAt(cursor++);
        valueStart = cursor;
        while (cursor < aLowerTag.Length() &&
               aLowerTag.CharAt(cursor) != quote) {
          ++cursor;
        }
        valueEnd = cursor;
        if (cursor < aLowerTag.Length()) {
          ++cursor;
        }
      } else {
        valueStart = cursor;
        while (cursor < aLowerTag.Length() &&
               !IsHtmlSpace(aLowerTag.CharAt(cursor)) &&
               aLowerTag.CharAt(cursor) != '>') {
          ++cursor;
        }
        valueEnd = cursor;
      }
    }

    if (Substring(aLowerTag, nameStart, nameEnd - nameStart).Equals(aName)) {
      aValue.Assign(Substring(aLowerTag, valueStart, valueEnd - valueStart));
      aValue.Trim(" \t\r\n\f");
      return true;
    }
  }
  return false;
}

bool StartsWithClosingTagName(const nsACString& aLowerTag,
                              const nsACString& aName) {
  if (aLowerTag.Length() < aName.Length() + 3 || aLowerTag.CharAt(0) != '<' ||
      aLowerTag.CharAt(1) != '/' ||
      !Substring(aLowerTag, 2, aName.Length()).Equals(aName)) {
    return false;
  }
  const char boundary = aLowerTag.CharAt(aName.Length() + 2);
  return IsHtmlSpace(boundary) || boundary == '>';
}

bool KeepsParserInHead(const nsACString& aLowerTag) {
  if (StringBeginsWith(aLowerTag, "<!"_ns) ||
      StringBeginsWith(aLowerTag, "</"_ns)) {
    return true;
  }
  return StartsWithTagName(aLowerTag, "html"_ns) ||
         StartsWithTagName(aLowerTag, "head"_ns) ||
         StartsWithTagName(aLowerTag, "base"_ns) ||
         StartsWithTagName(aLowerTag, "link"_ns) ||
         StartsWithTagName(aLowerTag, "meta"_ns) ||
         StartsWithTagName(aLowerTag, "noscript"_ns) ||
         StartsWithTagName(aLowerTag, "script"_ns) ||
         StartsWithTagName(aLowerTag, "style"_ns) ||
         StartsWithTagName(aLowerTag, "template"_ns) ||
         StartsWithTagName(aLowerTag, "title"_ns);
}

}  // namespace

namespace detail {

bool PreambleStylesheetIsNonDeferred(const nsACString& aLowerTag,
                                     bool aAlternate) {
  if (aAlternate) {
    return false;
  }
  nsAutoCString ignored;
  if (GetHtmlAttributeValue(aLowerTag, "disabled"_ns, ignored)) {
    return false;
  }
  nsAutoCString media;
  return !GetHtmlAttributeValue(aLowerTag, "media"_ns, media) ||
         media.IsEmpty() || media.EqualsLiteral("all");
}

bool PreambleScriptIsParserBlockingClassic(const nsACString& aLowerTag) {
  nsAutoCString ignored;
  if (GetHtmlAttributeValue(aLowerTag, "async"_ns, ignored) ||
      GetHtmlAttributeValue(aLowerTag, "defer"_ns, ignored)) {
    return false;
  }

  nsAutoCString type;
  if (!GetHtmlAttributeValue(aLowerTag, "type"_ns, type) || type.IsEmpty()) {
    return true;
  }
  return type.EqualsLiteral("application/ecmascript") ||
         type.EqualsLiteral("application/javascript") ||
         type.EqualsLiteral("application/x-ecmascript") ||
         type.EqualsLiteral("application/x-javascript") ||
         type.EqualsLiteral("text/ecmascript") ||
         type.EqualsLiteral("text/javascript") ||
         type.EqualsLiteral("text/javascript1.0") ||
         type.EqualsLiteral("text/javascript1.1") ||
         type.EqualsLiteral("text/javascript1.2") ||
         type.EqualsLiteral("text/javascript1.3") ||
         type.EqualsLiteral("text/javascript1.4") ||
         type.EqualsLiteral("text/javascript1.5") ||
         type.EqualsLiteral("text/jscript") ||
         type.EqualsLiteral("text/livescript") ||
         type.EqualsLiteral("text/x-ecmascript") ||
         type.EqualsLiteral("text/x-javascript");
}

}  // namespace detail

ProxyPreambleOperation::ProxyPreambleOperation() : mImpl(MakeUnique<Impl>()) {}

ProxyPreambleOperation::~ProxyPreambleOperation() {
  MOZ_ASSERT(!mImpl || mImpl->mCancelled || mImpl->mFinishedFired ||
             mImpl->mStreams.IsEmpty());
}

nsresult ProxyPreambleOperation::NotifyConnectHandoffAdmitted() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!PreambleModeUsesScopedNavigationStop(mImpl->mConfig.mMode)) {
    return NS_OK;
  }
  if (mImpl->mCancelled || mImpl->mFinishedFired ||
      mImpl->mConnectHandoffAdmitted) {
    return NS_ERROR_UNEXPECTED;
  }
  mImpl->mConnectHandoffAdmitted = true;
  return MaybeIssueNativeParserNavigationStop();
}

nsresult ProxyPreambleOperation::NotifyTunnelApplicationActive() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mConfig.mMode !=
      PreambleMode::TreeNativeParserDocumentStartNavigationStop) {
    return NS_OK;
  }
  if (mImpl->mCancelled || mImpl->mFinishedFired) {
    return NS_ERROR_UNEXPECTED;
  }
  if (mImpl->mTunnelApplicationActive) {
    return NS_OK;
  }
  mImpl->mTunnelApplicationActive = true;
  RuntimeLogEvent(
      "Connection %llu preamble "
      "native-parser-document-start-navigation-stop "
      "phase=tunnel-application-active direction=client-to-target "
      "bytes_positive=1 protocol=%s\n",
      static_cast<unsigned long long>(mImpl->mConnectionId),
      ProtocolName(mImpl->mProtocol));
  return MaybeIssueNativeParserNavigationStop();
}

nsresult ProxyPreambleOperation::NotifyTunnelServerApplicationActive() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mConfig.mMode !=
          PreambleMode::TreeNativeParserDocumentStartResponseStop) {
    return NS_OK;
  }
  if (mImpl->mCancelled || mImpl->mFinishedFired) {
    return NS_ERROR_UNEXPECTED;
  }
  if (mImpl->mTunnelServerApplicationActive) {
    return NS_OK;
  }
  if (mImpl->mStreams.Length() == 2 &&
      (mImpl->mStreams[1].mDone || !mImpl->mStreams[1].mRequest)) {
    // Target activity that arrives after the synthetic stylesheet has already
    // completed cannot cause a response-scoped stop. Keep that outcome a
    // clean natural-completion branch instead of logging mixed causal
    // evidence that the runtime did not act on.
    return NS_OK;
  }
  mImpl->mTunnelServerApplicationActive = true;
  RuntimeLogEvent(
      "Connection %llu preamble "
      "native-parser-document-start-response-stop "
      "phase=tunnel-application-active direction=target-to-client "
      "bytes_positive=1 payload=decoded protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId));
  return MaybeIssueNativeParserNavigationStop();
}

nsresult ProxyPreambleOperation::MaybeIssueNativeParserNavigationStop() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!PreambleModeUsesScopedNavigationStop(mImpl->mConfig.mMode)) {
    return NS_OK;
  }
  if (mImpl->mNavigationStopIssued) {
    return NS_OK;
  }
  if (mImpl->mStreams.Length() != 2 ||
      !detail::PreambleNavigationStopMayIssue(
          mImpl->mConfig.mMode, mImpl->mConnectHandoffAdmitted,
          mImpl->mNavigationStopStyleResponseStarted,
          mImpl->mTunnelApplicationActive,
          mImpl->mTunnelServerApplicationActive) ||
      !mImpl->mRootDone || !mImpl->mRootCompletedSuccessfully ||
      !mImpl->mNativeParserFinished) {
    return NS_OK;
  }
  if (mImpl->mConfig.mMode ==
          PreambleMode::TreeNativeParserDocumentStartResponseStop &&
      (mImpl->mStreams[1].mDone || !mImpl->mStreams[1].mRequest)) {
    // A slow or silent target may not respond until the synthetic stylesheet
    // has naturally completed. The response-scoped stop is opportunistic and
    // must never turn that healthy tunnel into a product failure.
    return NS_OK;
  }
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      !mImpl->mBarrierFired || !mImpl->mLoadGroup ||
      !mImpl->mStreams[1].mRequest || mImpl->mStreams[1].mDone) {
    return NS_ERROR_UNEXPECTED;
  }
  mImpl->mNavigationStopIssued = true;
  RuntimeLogEvent(
      "Connection %llu preamble "
      "%s "
      "phase=navigation-stop-issued reason=NS_BINDING_ABORTED "
      "load_group=scoped protocol=%s\n",
      static_cast<unsigned long long>(mImpl->mConnectionId),
      mImpl->mConfig.mMode ==
              PreambleMode::TreeNativeParserDocumentStartResponseStop
          ? "native-parser-document-start-response-stop"
          : "native-parser-document-start-navigation-stop",
      ProtocolName(mImpl->mProtocol));
  return mImpl->mLoadGroup->CancelWithReason(
      NS_BINDING_ABORTED, "NaiveFox::ProxyPreambleNavigationStop"_ns);
}

void ProxyPreambleOperation::Cancel(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mFinishedFired) {
    return;
  }
  mImpl->mCancelled = true;
  if (mImpl->mNativeStyleActivationRequestId) {
    NativeStylePreloadActivation::Cancel(
        mImpl->mNativeStyleActivationRequestId);
    mImpl->mNativeStyleActivationRequestId = 0;
  }
  if (mImpl->mNativeRootReplacementRequestId) {
    NativeStylePreloadActivation::CancelRootReplacement(
        mImpl->mNativeRootReplacementRequestId);
    mImpl->mNativeRootReplacementRequestId = 0;
  }
  if (mImpl->mNativeProcessRootRequestId) {
    NativeStylePreloadActivation::CancelProcessRoot(
        mImpl->mNativeProcessRootRequestId,
        mImpl->mNativeParserGeneration.load(std::memory_order_acquire),
        NS_FAILED(aStatus) ? aStatus : NS_ERROR_ABORT);
    mImpl->mNativeProcessRootRequestId = 0;
  }
  mImpl->mNativeRootLogicalRequest = nullptr;
  mImpl->mNativeParserRetargetArmed.store(false, std::memory_order_release);
  mImpl->mNativeParserRetargetInstalled.store(false, std::memory_order_release);
  mImpl->mNativeParserGeneration.fetch_add(1, std::memory_order_acq_rel);
  if (mImpl->mNativeParserRootSuspended) {
    // This operation owns exactly one Suspend(). Balance it before cancelling
    // so a cancelled root cannot remain retained by the channel indefinitely.
    nsCOMPtr<nsIRequest> root =
        mImpl->mStreams.IsEmpty() ? nullptr : mImpl->mStreams[0].mRequest;
    mImpl->mNativeParserRootSuspended = false;
    if (root) {
      if (PreambleModeUsesNativeRootReplacement(mImpl->mConfig.mMode)) {
        (void)root->Cancel(aStatus);
      }
      (void)root->Resume();
    }
  }
  if (mImpl->mNativeParserTarget) {
    RefPtr self = this;
    // This cleanup is ordered after every already-queued scanner operation.
    // Holding the operation here prevents main-thread destruction from racing
    // a scanner method on the parser target.
    (void)mImpl->mNativeParserTarget->Dispatch(
        NS_NewRunnableFunction(
            "NaiveFox::NativeParserCancelCleanup",
            [self] { self->mImpl->mNativeParserScanner = nullptr; }),
        NS_DISPATCH_NORMAL);
  }
  // Clear externally owned continuations before Cancel(), which can dispatch
  // OnStopRequest reentrantly on some channel implementations.
  mImpl->mBarrierCallback = nullptr;
  mImpl->mFinishedCallback = nullptr;
  nsTArray<nsCOMPtr<nsIRequest>> requests;
  for (auto& stream : mImpl->mStreams) {
    if (stream.mRequest) {
      requests.AppendElement(stream.mRequest.forget());
    }
    stream.mDone = true;
  }
  for (const auto& request : requests) {
    (void)request->Cancel(aStatus);
  }
}

nsresult ProxyPreambleOperation::Start(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const PreambleConfig& aConfig,
    ProxyProtocol aProtocol, ProxyPreambleCallback&& aBarrierCallback,
    ProxyPreambleFinishedCallback&& aFinishedCallback,
    const Maybe<HostResolverRule>& aHostResolverRule, uint64_t aConnectionId) {
  MOZ_ASSERT(NS_IsMainThread());
  const uint32_t expectedH2NativeParserResourceCount =
      aConfig.mMode == PreambleMode::TreeNativeParserResourceCommittedOverlap
          ? 6U
          : 3U;
  if (!aBarrierCallback || aConfig.mMode == PreambleMode::Off ||
      !IsValidPreamblePath(aConfig.mPath) || aConfig.mMaxBytes == 0 ||
      aConfig.mMaxBytes > PreambleConfig::kMaximumBytes ||
      aConfig.mMaxAssets > PreambleConfig::kMaximumAssets ||
      (aConfig.mCacheResources && !PreambleModeUsesResources(aConfig.mMode)) ||
      (aConfig.mMode == PreambleMode::TreeResourceNativeCacheCommittedOverlap &&
       (aProtocol != ProxyProtocol::H3 || aConfig.mMaxAssets != 1 ||
        !aConfig.mCacheResources)) ||
      (PreambleModeUsesNativeParser(aConfig.mMode) &&
       ((aProtocol != ProxyProtocol::H3 &&
         !(aProtocol == ProxyProtocol::H2 &&
           (aConfig.mMode ==
                PreambleMode::TreeNativeParserDocumentStartOverlap ||
            aConfig.mMode ==
                PreambleMode::TreeNativeParserDocumentStartResourceTree ||
            aConfig.mMode ==
                PreambleMode::TreeNativeParserResourceCommittedOverlap ||
            aConfig.mMode ==
                PreambleMode::TreeNativeParserDocumentStartNavigationStop))) ||
        (PreambleModeUsesNativeParserResourceTree(aConfig.mMode)
             ? (aProtocol == ProxyProtocol::H2
                    ? aConfig.mMaxAssets !=
                          expectedH2NativeParserResourceCount
                    : (aConfig.mMaxAssets != 3U && aConfig.mMaxAssets != 6U))
             : aConfig.mMaxAssets != 1U) ||
        !aConfig.mCacheResources))) {
    return NS_ERROR_INVALID_ARG;
  }
  mImpl->mConfig = aConfig;
  mImpl->mProtocol = aProtocol;
  mImpl->mBarrierCallback = std::move(aBarrierCallback);
  mImpl->mFinishedCallback = std::move(aFinishedCallback);
  mImpl->mConnectionId = aConnectionId;
  MOZ_TRY(BuildExplicitProxyRoute(aProxyUrl, aProxyUser, aProxyPassword,
                                  aProtocol, aHostResolverRule, false,
                                  mImpl->mRoute));
  MOZ_TRY(GetSystemPrincipal(getter_AddRefs(mImpl->mPrincipal)));
  MOZ_TRY(
      NS_NewLoadGroup(getter_AddRefs(mImpl->mLoadGroup), mImpl->mPrincipal));
  if (PreambleModeUsesNativeParser(aConfig.mMode) &&
      !PreambleModeUsesNativeParserProcess(aConfig.mMode)) {
    mImpl->mNativeParserTarget = NativeParserTarget();
    if (!mImpl->mNativeParserTarget) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    RuntimeLogEvent(
        "Preamble native-parser-preload lifecycle=target-ready generation=1 "
        "protocol=%s\n",
        ProtocolName(aProtocol));
  }

  nsAutoCString rootSpec;
  MOZ_TRY(mImpl->mRoute.mProxyUri->GetPrePath(rootSpec));
  mImpl->mRootPrePath = rootSpec;
  rootSpec.Append(aConfig.mPath);
  nsCOMPtr<nsIURI> rootUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(rootUri), rootSpec));

  auto openStream = [this](nsIURI* aUri, nsContentPolicyType aContentPolicyType,
                           uint32_t& aStreamId) -> nsresult {
    nsCOMPtr<nsIChannel> templateChannel;
    MOZ_TRY(
        NS_NewChannel(getter_AddRefs(templateChannel), aUri, mImpl->mPrincipal,
                      nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
                          nsILoadInfo::SEC_DONT_FOLLOW_REDIRECTS |
                          nsILoadInfo::SEC_COOKIES_OMIT,
                      aContentPolicyType, nullptr, nullptr, mImpl->mLoadGroup));
    nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();
    nsCOMPtr<nsIProxiedProtocolHandler> protocolHandler =
        do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "https");
    if (!protocolHandler) {
      return NS_ERROR_FAILURE;
    }
    nsCOMPtr<nsIChannel> channel;
    MOZ_TRY(protocolHandler->NewProxiedChannel(aUri, mImpl->mRoute.mProxyInfo,
                                               0, nullptr, loadInfo,
                                               getter_AddRefs(channel)));
    nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
    nsCOMPtr<nsIHttpChannel> httpChannel = do_QueryInterface(channel);
    if (!internal || !httpChannel) {
      return NS_ERROR_FAILURE;
    }
    MOZ_TRY(httpChannel->SetRequestMethod("GET"_ns));
    MOZ_TRY(internal->SetAllowSpdy(true));
    MOZ_TRY(internal->SetAllowHttp3(mImpl->mProtocol == ProxyProtocol::H3));
    MOZ_TRY(internal->SetBlockAuthPrompt(true));
    MOZ_TRY(internal->SetProxyPreamble());
    if (mImpl->mConfig.mMode == PreambleMode::DocumentCarrierDispatch) {
      if (mImpl->mProtocol != ProxyProtocol::H3 ||
          aContentPolicyType != nsIContentPolicy::TYPE_DOCUMENT) {
        return NS_ERROR_INVALID_ARG;
      }
      MOZ_TRY(internal->SetProxyPreambleUseCarrierDispatch());
    }
    if (mImpl->mConfig.mMode == PreambleMode::DocumentColdWinnerHandoff) {
      if (mImpl->mProtocol != ProxyProtocol::H3 ||
          aContentPolicyType != nsIContentPolicy::TYPE_DOCUMENT) {
        return NS_ERROR_INVALID_ARG;
      }
      MOZ_TRY(internal->SetProxyPreambleUseColdWinnerHandoff());
    }
    if (mImpl->mConfig.mMode == PreambleMode::DocumentNativeCacheOpen) {
      if (mImpl->mProtocol != ProxyProtocol::H3 ||
          aContentPolicyType != nsIContentPolicy::TYPE_DOCUMENT) {
        return NS_ERROR_INVALID_ARG;
      }
      MOZ_TRY(internal->SetProxyPreambleUseNativeCacheOpen());
    }
    if (mImpl->mConfig.mMode == PreambleMode::DocumentHandshakeConfirmed) {
      if (mImpl->mProtocol != ProxyProtocol::H3 ||
          aContentPolicyType != nsIContentPolicy::TYPE_DOCUMENT) {
        return NS_ERROR_INVALID_ARG;
      }
      MOZ_TRY(internal->SetProxyPreambleHandshakeDwell(16));
    }
    MOZ_TRY(internal->SetDocumentURI(aUri));
    MOZ_TRY(channel->SetLoadGroup(mImpl->mLoadGroup));
    // DocumentNativeCacheOpen deliberately keeps INHIBIT_CACHING and performs
    // an OPEN_READONLY miss.
    // The native parser arm models ordinary document/resource traffic. Keep
    // its proxy-pool identity non-anonymous so the root, parser stylesheet,
    // and following CONNECT can use one outer browser-style session. Other
    // diagnostic arms retain their historical service-owned isolation.
    uint32_t loadFlags = PreambleModeUsesNativeParser(mImpl->mConfig.mMode)
                             ? nsIRequest::LOAD_NORMAL
                             : nsIRequest::LOAD_ANONYMOUS |
                                   nsIChannel::LOAD_BYPASS_SERVICE_WORKER;
    if (!detail::PreambleChannelUsesCache(mImpl->mConfig, mImpl->mProtocol,
                                          false)) {
      loadFlags |= nsIRequest::INHIBIT_CACHING;
    }
    MOZ_TRY(channel->SetLoadFlags(loadFlags));
    // Mirror the native top-level document scheduling cause from nsDocShell.
    // Necko derives transport priority from class-of-service state; do not
    // synthesize an HTTP Priority header or tune the resulting frame size.
    if (aContentPolicyType == nsIContentPolicy::TYPE_DOCUMENT) {
      nsCOMPtr<nsIClassOfService> cos = do_QueryInterface(channel);
      if (cos) {
        cos->AddClassFlags(nsIClassOfService::UrgentStart);
        if (StaticPrefs::dom_document_priority_incremental()) {
          cos->SetIncremental(true);
        }
      }
    }

    aStreamId = mImpl->mStreams.Length();
    auto& stream = *mImpl->mStreams.AppendElement();
    stream.mUri = aUri;
    stream.mRequest = channel;
    RefPtr<StreamListener> listener = new StreamListener(this, aStreamId);
    if ((mImpl->mConfig.mMode == PreambleMode::DocumentStartOverlap ||
         mImpl->mConfig.mMode == PreambleMode::DocumentStartTaskOverlap ||
         PreambleModeUsesNativeParserDocumentStart(mImpl->mConfig.mMode)) &&
        aContentPolicyType == nsIContentPolicy::TYPE_DOCUMENT) {
      MOZ_TRY(channel->SetNotificationCallbacks(listener));
    }
    nsresult rv = channel->AsyncOpen(listener);
    if (NS_FAILED(rv)) {
      mImpl->mStreams.RemoveLastElement();
      return rv;
    }
    return NS_OK;
  };

  uint32_t rootStreamId = 0;
  return openStream(rootUri, nsIContentPolicy::TYPE_DOCUMENT, rootStreamId);
}

void ProxyPreambleOperation::OnRequestCommitted(uint32_t aStreamId,
                                                nsIRequest* aRequest) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || aStreamId >= mImpl->mStreams.Length() ||
      !SameCOMIdentity(mImpl->mStreams[aStreamId].mRequest, aRequest)) {
    return;
  }
  if (mImpl->mConfig.mMode == PreambleMode::DocumentStartTaskOverlap &&
      aStreamId == 0) {
    nsresult dispatchRv = DispatchDocumentBarrierTask();
    if (NS_FAILED(dispatchRv) && NS_SUCCEEDED(mImpl->mFirstFailure)) {
      mImpl->mFirstFailure = dispatchRv;
    }
    return;
  }
  if ((mImpl->mConfig.mMode == PreambleMode::DocumentStartOverlap ||
       PreambleModeUsesNativeParserDocumentStart(mImpl->mConfig.mMode)) &&
      aStreamId == 0) {
    FireBarrierCallback();
    return;
  }
  if (mImpl->mConfig.mMode == PreambleMode::TreeResourceCommittedOverlap &&
      aStreamId > 0) {
    mImpl->mStreams[aStreamId].mRequestCommitted = true;
    MaybeFireBarrier();
  }
  if (mImpl->mConfig.mMode ==
          PreambleMode::TreeResourceNativeCacheCommittedOverlap &&
      aStreamId > 0) {
    nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(aRequest);
    bool cacheOpenSucceeded = false;
    if (!internal ||
        NS_FAILED(internal->GetProxyPreambleNativeResourceCacheOpenSucceeded(
            &cacheOpenSucceeded)) ||
        !cacheOpenSucceeded) {
      if (NS_SUCCEEDED(mImpl->mFirstFailure)) {
        mImpl->mFirstFailure = NS_ERROR_UNEXPECTED;
      }
      mImpl->mAllStreamsCompletedNormally = false;
      return;
    }
    auto& stream = mImpl->mStreams[aStreamId];
    stream.mNativeCacheNewEntry = true;
    stream.mRequestCommitted = true;
    MaybeFireBarrier();
  }
  if (PreambleModeUsesNativeParser(mImpl->mConfig.mMode)) {
    if (PreambleModeUsesNativeParserResourceTree(mImpl->mConfig.mMode)) {
      if (aStreamId == 0 || aStreamId > mImpl->mConfig.mMaxAssets ||
          mImpl->mStreams.Length() != mImpl->mConfig.mMaxAssets + 1 ||
          mImpl->mStreams[aStreamId].mDone) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "invalid-resource-tree-commit");
        return;
      }
      mImpl->mStreams[aStreamId].mRequestCommitted = true;
      RuntimeLogEvent(
          "Preamble native-parser-resource-tree lifecycle=resource-committed "
          "stream=%u status=waiting-for protocol=%s\n",
          aStreamId, ProtocolName(mImpl->mProtocol));
      if (mImpl->mConfig.mMode ==
              PreambleMode::TreeNativeParserResourceCommittedOverlap &&
          mImpl->mNativeParserResourceDescriptorsAccepted ==
              mImpl->mConfig.mMaxAssets) {
        MaybeFireBarrier();
      }
      return;
    }
    if (aStreamId != 1 || mImpl->mStreams.Length() != 2 ||
        mImpl->mStreams[aStreamId].mDone) {
      FailNativeParserContract(NS_ERROR_UNEXPECTED, "invalid-resource-commit");
      return;
    }
    auto& stream = mImpl->mStreams[aStreamId];
    if (!stream.mRequestCommitted) {
      stream.mRequestCommitted = true;
      if (PreambleModeUsesScopedNavigationStop(mImpl->mConfig.mMode)) {
        RuntimeLogEvent(
            "Connection %llu preamble "
            "%s "
            "phase=stylesheet-committed stream=1 status=waiting-for "
            "protocol=%s\n",
            static_cast<unsigned long long>(mImpl->mConnectionId),
            mImpl->mConfig.mMode ==
                    PreambleMode::TreeNativeParserDocumentStartResponseStop
                ? "native-parser-document-start-response-stop"
                : "native-parser-document-start-navigation-stop",
            ProtocolName(mImpl->mProtocol));
      } else {
        RuntimeLogEvent(
            "Preamble native-parser-preload lifecycle=resource-committed "
            "stream=1 status=waiting-for protocol=%s\n",
            ProtocolName(mImpl->mProtocol));
      }
    }
    if (PreambleModeUsesScopedNavigationStop(mImpl->mConfig.mMode)) {
      nsresult cancelRv = MaybeIssueNativeParserNavigationStop();
      if (NS_FAILED(cancelRv)) {
        FailNativeParserContract(cancelRv, "navigation-stop-cancel-failed");
      }
      return;
    }
    MaybeFireBarrier();
  }
}

nsresult ProxyPreambleOperation::DispatchDocumentBarrierTask() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mDocumentBarrierTaskDispatched || mImpl->mBarrierFired) {
    return NS_OK;
  }
  mImpl->mDocumentBarrierTaskDispatched = true;
  if (mImpl->mConfig.mMaxAssets == 6) {
    if (mImpl->mConfig.mMode ==
        PreambleMode::TreeResourceCommittedOverlap) {
      RuntimeLogEvent(
          "Preamble resource-committed-overlap barrier=task-dispatched "
          "assets=6 protocol=%s\n",
          ProtocolName(mImpl->mProtocol));
    }
  }
  RefPtr self = this;
  nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::DocumentBarrierTask", [self]() {
        if (!self->mImpl->mCancelled) {
          self->FireBarrierCallback();
        }
        if (self->mImpl->mDocumentRootStopDeferred) {
          const nsresult stopStatus = self->mImpl->mDocumentRootStopStatus;
          self->mImpl->mDocumentRootStopDeferred = false;
          self->OnStopRequest(0, stopStatus);
        }
      }));
  if (NS_FAILED(rv)) {
    mImpl->mDocumentBarrierTaskDispatched = false;
  }
  return rv;
}

nsresult ProxyPreambleOperation::OnStartRequest(uint32_t aStreamId,
                                                nsIRequest* aRequest) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || aStreamId >= mImpl->mStreams.Length()) {
    return NS_ERROR_ABORT;
  }
  auto& stream = mImpl->mStreams[aStreamId];
  stream.mHeadersReceived = true;
  nsCOMPtr<nsIHttpChannel> channel = do_QueryInterface(aRequest);
  if (!channel) {
    if (aStreamId == 0) {
      mImpl->mFirstFailure = NS_ERROR_UNEXPECTED;
    }
    return NS_ERROR_UNEXPECTED;
  }
  if (aStreamId == 0 &&
      mImpl->mConfig.mMode == PreambleMode::DocumentNativeCacheOpen) {
    nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(aRequest);
    bool readOnlyMiss = false;
    if (!internal ||
        NS_FAILED(
            internal->GetProxyPreambleNativeCacheReadOnlyMiss(&readOnlyMiss)) ||
        !readOnlyMiss) {
      mImpl->mFirstFailure = NS_ERROR_UNEXPECTED;
      return NS_ERROR_UNEXPECTED;
    }
  }
  if (aStreamId == 0 &&
      mImpl->mConfig.mMode == PreambleMode::DocumentColdWinnerHandoff) {
    nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(aRequest);
    bool handoffSucceeded = false;
    if (!internal ||
        NS_FAILED(internal->GetProxyPreambleColdWinnerHandoffSucceeded(
            &handoffSucceeded)) ||
        !handoffSucceeded) {
      mImpl->mFirstFailure = NS_ERROR_UNEXPECTED;
      return NS_ERROR_UNEXPECTED;
    }
  }
  nsresult rv = channel->GetResponseStatus(&stream.mHttpStatus);
  stream.mResponseHeadersReceived = NS_SUCCEEDED(rv);
  if (aStreamId == 1 &&
      PreambleModeUsesScopedNavigationStop(mImpl->mConfig.mMode)) {
    if (!stream.mResponseHeadersReceived || stream.mHttpStatus < 200 ||
        stream.mHttpStatus >= 300) {
      nsresult failure = NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
      FailNativeParserContract(failure,
                               "navigation-stop-stylesheet-response-rejected");
      return failure;
    }
    if (mImpl->mNavigationStopStyleResponseStarted) {
      FailNativeParserContract(
          NS_ERROR_UNEXPECTED,
          "navigation-stop-stylesheet-response-started-twice");
      return NS_ERROR_UNEXPECTED;
    }
    mImpl->mNavigationStopStyleResponseStarted = true;
    RuntimeLogEvent(
        "Connection %llu preamble "
        "%s "
        "phase=stylesheet-response-started http=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        mImpl->mConfig.mMode ==
                PreambleMode::TreeNativeParserDocumentStartResponseStop
            ? "native-parser-document-start-response-stop"
            : "native-parser-document-start-navigation-stop",
        stream.mHttpStatus, ProtocolName(mImpl->mProtocol));
    nsresult stopRv = MaybeIssueNativeParserNavigationStop();
    if (NS_FAILED(stopRv)) {
      FailNativeParserContract(stopRv, "navigation-stop-cancel-failed");
      return stopRv;
    }
  }
  if (aStreamId == 0 && NS_FAILED(rv) && NS_SUCCEEDED(mImpl->mFirstFailure)) {
    mImpl->mFirstFailure = rv;
  }
  if (aStreamId == 0) {
    if (PreambleModeUsesNativeParser(mImpl->mConfig.mMode)) {
      nsCOMPtr<nsIChannel> rootChannel = do_QueryInterface(aRequest);
      nsCOMPtr<nsILoadInfo> loadInfo =
          rootChannel ? rootChannel->LoadInfo() : nullptr;
      nsAutoCString contentType;
      nsAutoCString contentCharset;
      if (!loadInfo || !stream.mResponseHeadersReceived ||
          stream.mHttpStatus < 200 || stream.mHttpStatus >= 300 ||
          NS_FAILED(rootChannel->GetContentType(contentType)) ||
          !contentType.Equals("text/html"_ns,
                              nsCaseInsensitiveCStringComparator)) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "unsupported-root-response");
        return NS_ERROR_UNEXPECTED;
      }
      (void)rootChannel->GetContentCharset(contentCharset);
      contentCharset.Trim(" \t\r\n");
      if (!contentCharset.IsEmpty() &&
          !contentCharset.Equals("utf-8"_ns,
                                 nsCaseInsensitiveCStringComparator)) {
        FailNativeParserContract(NS_ERROR_ILLEGAL_INPUT,
                                 "unsupported-root-charset");
        return NS_ERROR_ILLEGAL_INPUT;
      }
      mImpl->mRootOriginAttributes = loadInfo->GetOriginAttributes();
      mImpl->mHaveRootOriginAttributes = true;
      RuntimeLogEvent(
          "Preamble native-parser-preload lifecycle=root-admitted "
          "media=text/html charset=%s protocol=%s\n",
          contentCharset.IsEmpty() ? "absent" : "utf-8",
          ProtocolName(mImpl->mProtocol));
    }
    nsAutoCString policyHeader;
    mozilla::dom::ReferrerPolicy policy = mozilla::dom::ReferrerPolicy::_empty;
    if (NS_SUCCEEDED(
            channel->GetResponseHeader("referrer-policy"_ns, policyHeader))) {
      policy = mozilla::dom::ReferrerInfo::ReferrerPolicyFromHeaderString(
          NS_ConvertUTF8toUTF16(policyHeader));
    }
    mImpl->mRootReferrerInfo =
        new mozilla::dom::ReferrerInfo(stream.mUri, policy, true);
    if (PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode)) {
      nsresult processRv = StartNativeParserProcessRoot(aRequest, channel);
      if (NS_FAILED(processRv)) {
        FailNativeParserContract(
            processRv, mImpl->mConfig.mMode ==
                               PreambleMode::TreeNativeParserFullProcessOverlap
                           ? "full-process-root-start-failed"
                           : "process-root-start-failed");
      }
      return processRv;
    }
    if (mImpl->mConfig.mMode ==
        PreambleMode::TreeNativeParserRootRendezvousOverlap) {
      nsresult replacementRv =
          StartNativeParserRootReplacement(aRequest, channel);
      if (NS_FAILED(replacementRv)) {
        FailNativeParserContract(replacementRv,
                                 "root-replacement-start-failed");
      }
      return replacementRv;
    }
    if (mImpl->mConfig.mMode ==
        PreambleMode::TreeNativeParserDocumentHandoffOverlap) {
      nsAutoCString rootPrePath;
      if (!mImpl->mRootReferrerInfo || !mImpl->mHaveRootOriginAttributes ||
          !stream.mUri || NS_FAILED(stream.mUri->GetPrePath(rootPrePath)) ||
          !rootPrePath.Equals(mImpl->mRootPrePath)) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "invalid-root-origin-referrer");
        return NS_ERROR_UNEXPECTED;
      }
      LogNativeParserDocumentHandoffPhase("root-response-validated");
      nsresult suspendRv = aRequest->Suspend();
      if (NS_FAILED(suspendRv)) {
        FailNativeParserContract(suspendRv, "root-suspend-failed");
        return suspendRv;
      }
      mImpl->mNativeParserRootSuspended = true;
      LogNativeParserDocumentHandoffPhase("handoff-suspend");
      if (mImpl->mNativeParserScanner) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "consumer-already-constructed");
        return NS_ERROR_UNEXPECTED;
      }
      // nsHtml5StreamParser and its tokenizer/tree-builder are constructed on
      // the content main thread. Keep that upstream ownership boundary here;
      // this control arm deliberately does not retarget request delivery.
      mImpl->mNativeParserScanner = MakeUnique<nsHtml5SpeculativeScanner>(
          mImpl->mNativeParserTarget.get());
      LogNativeParserDocumentHandoffPhase("consumer-constructed-main");
      nsresult installRv = DispatchNativeParserReplacementListenerInstall();
      if (NS_FAILED(installRv)) {
        FailNativeParserContract(installRv,
                                 "replacement-install-dispatch-failed");
        return installRv;
      }
      // The suspended request resumes only from the ordinary next-main-turn
      // replacement-listener installation. Render-blocking runnables remain
      // confined to subsequent parser -> main speculative-load flushes.
      return NS_OK;
    }
    if (PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode)) {
      nsAutoCString rootPrePath;
      if (!mImpl->mRootReferrerInfo || !mImpl->mHaveRootOriginAttributes ||
          !stream.mUri || NS_FAILED(stream.mUri->GetPrePath(rootPrePath)) ||
          !rootPrePath.Equals(mImpl->mRootPrePath)) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "invalid-root-origin-referrer");
        return NS_ERROR_UNEXPECTED;
      }
      LogNativeParserRetargetPhase("root-response-validated");
      nsresult suspendRv = aRequest->Suspend();
      if (NS_FAILED(suspendRv)) {
        FailNativeParserContract(suspendRv, "root-suspend-failed");
        return suspendRv;
      }
      mImpl->mNativeParserRootSuspended = true;
      LogNativeParserRetargetPhase("handoff-suspend");
      if (mImpl->mNativeParserScanner) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "consumer-already-constructed");
        return NS_ERROR_UNEXPECTED;
      }
      mImpl->mNativeParserScanner = MakeUnique<nsHtml5SpeculativeScanner>(
          mImpl->mNativeParserTarget.get());
      LogNativeParserRetargetPhase("consumer-constructed-main");

      nsresult retargetRv = InstallNativeParserRetargetDelivery(aRequest);
      if (NS_FAILED(retargetRv)) {
        FailNativeParserContract(retargetRv, "root-retarget-failed");
        return retargetRv;
      }
      nsresult installRv = DispatchNativeParserReplacementListenerInstall();
      if (NS_FAILED(installRv)) {
        FailNativeParserContract(installRv,
                                 "replacement-install-dispatch-failed");
        return installRv;
      }
      return NS_OK;
    }
  }
  if (aStreamId == 0 &&
      mImpl->mConfig.mMode == PreambleMode::DocumentHeadersTaskOverlap &&
      stream.mResponseHeadersReceived && stream.mHttpStatus >= 200 &&
      stream.mHttpStatus < 300) {
    nsresult dispatchRv = DispatchDocumentBarrierTask();
    if (NS_FAILED(dispatchRv) && NS_SUCCEEDED(mImpl->mFirstFailure)) {
      mImpl->mFirstFailure = dispatchRv;
    }
    return dispatchRv;
  }
  MaybeFireBarrier();
  return rv;
}

nsresult ProxyPreambleOperation::StartNativeParserRootReplacement(
    nsIRequest* aRequest, nsIHttpChannel* aChannel) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(mImpl->mConfig.mMode ==
             PreambleMode::TreeNativeParserRootRendezvousOverlap);
  if (!aRequest || !aChannel || mImpl->mStreams.IsEmpty() ||
      mImpl->mNativeRootReplacementRequestId ||
      mImpl->mNativeParserRootSuspended || mImpl->mNativeParserScanner ||
      !mImpl->mRootReferrerInfo || !mImpl->mHaveRootOriginAttributes) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsIChannel> rootChannel = do_QueryInterface(aRequest);
  nsCOMPtr<nsILoadInfo> loadInfo =
      rootChannel ? rootChannel->LoadInfo() : nullptr;
  if (!rootChannel || !loadInfo ||
      loadInfo->GetExternalContentPolicyType() !=
          ExtContentPolicy::TYPE_DOCUMENT) {
    return NS_ERROR_UNEXPECTED;
  }

  NativeRootReplacementActivationDescriptor descriptor;
  MOZ_TRY(aChannel->GetChannelId(&descriptor.mChannelId));
  if (!descriptor.mChannelId) {
    return NS_ERROR_UNEXPECTED;
  }
  MOZ_TRY(mImpl->mStreams[0].mUri->GetSpec(descriptor.mResourceSpec));
  nsCOMPtr<nsIURI> originalUri;
  MOZ_TRY(rootChannel->GetOriginalURI(getter_AddRefs(originalUri)));
  if (!originalUri) {
    return NS_ERROR_UNEXPECTED;
  }
  MOZ_TRY(originalUri->GetSpec(descriptor.mOriginalSpec));
  mImpl->mRootOriginAttributes.CreateSuffix(descriptor.mOriginAttributesSuffix);
  nsCOMPtr<nsIURI> originalReferrer;
  MOZ_TRY(mImpl->mRootReferrerInfo->GetOriginalReferrer(
      getter_AddRefs(originalReferrer)));
  if (originalReferrer) {
    MOZ_TRY(originalReferrer->GetSpec(descriptor.mReferrerSpec));
  }
  descriptor.mReferrerPolicy =
      static_cast<uint8_t>(mImpl->mRootReferrerInfo->ReferrerPolicy());
  MOZ_TRY(mImpl->mRootReferrerInfo->GetSendReferrer(&descriptor.mSendReferrer));
  MOZ_TRY(rootChannel->GetLoadFlags(&descriptor.mLoadFlags));
  descriptor.mContentPolicyType =
      static_cast<uint32_t>(loadInfo->GetExternalContentPolicyType());
  descriptor.mHttpStatus = mImpl->mStreams[0].mHttpStatus;
  MOZ_TRY(rootChannel->GetContentType(descriptor.mContentType));
  (void)rootChannel->GetContentCharset(descriptor.mCharset);
  descriptor.mCharset.Trim(" \t\r\n");
  descriptor.mGeneration =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);

  nsAutoCString rootPrePath;
  MOZ_TRY(mImpl->mStreams[0].mUri->GetPrePath(rootPrePath));
  if (!rootPrePath.Equals(mImpl->mRootPrePath) ||
      !descriptor.mContentType.Equals("text/html"_ns,
                                      nsCaseInsensitiveCStringComparator) ||
      (!descriptor.mCharset.IsEmpty() &&
       !descriptor.mCharset.Equals("utf-8"_ns,
                                   nsCaseInsensitiveCStringComparator))) {
    return NS_ERROR_UNEXPECTED;
  }

  mImpl->mNativeRootReplacementChannelId = descriptor.mChannelId;
  LogNativeParserRootReplacementPhase("root-response-validated");
  MOZ_TRY(aRequest->Suspend());
  mImpl->mNativeParserRootSuspended = true;
  LogNativeParserRootReplacementPhase("physical-root-suspended");

  RefPtr self = this;
  const uint64_t generation = descriptor.mGeneration;
  nsresult rv = NativeStylePreloadActivation::RegisterRootReplacement(
      std::move(descriptor),
      [self, generation](
          const NativeRootReplacementActivationDescriptor& aDescriptor) {
        return self->LinkNativeParserRootReplacement(generation, aDescriptor);
      },
      [self, generation](nsresult aStatus) {
        return self->OnNativeParserRootReplacementReady(generation, aStatus);
      },
      [self, generation]() {
        return self->OnNativeParserRootForwardedStart(generation);
      },
      [self, generation](nsCString&& aData) {
        return self->OnNativeParserRootData(generation, std::move(aData));
      },
      [self, generation](nsresult aStatus) {
        return self->OnNativeParserRootStop(generation, aStatus);
      },
      mImpl->mNativeRootReplacementRequestId);
  if (NS_SUCCEEDED(rv)) {
    LogNativeParserRootReplacementPhase("replacement-registered");
  }
  return rv;
}

nsresult ProxyPreambleOperation::StartNativeParserProcessRoot(
    nsIRequest* aRequest, nsIHttpChannel* aChannel) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode));
  if (!aRequest || !aChannel || mImpl->mStreams.IsEmpty() ||
      mImpl->mNativeProcessRootRequestId || mImpl->mNativeParserRootSuspended ||
      mImpl->mNativeParserScanner || mImpl->mNativeParserTarget ||
      !mImpl->mRootReferrerInfo || !mImpl->mHaveRootOriginAttributes ||
      !NativeStylePreloadActivation::IsProcessReady()) {
    return NS_ERROR_NOT_AVAILABLE;
  }

  nsCOMPtr<nsIChannel> rootChannel = do_QueryInterface(aRequest);
  nsCOMPtr<nsILoadInfo> loadInfo =
      rootChannel ? rootChannel->LoadInfo() : nullptr;
  if (!rootChannel || !loadInfo ||
      loadInfo->GetExternalContentPolicyType() !=
          ExtContentPolicy::TYPE_DOCUMENT) {
    return NS_ERROR_UNEXPECTED;
  }

  NativeRootReplacementActivationDescriptor descriptor;
  MOZ_TRY(aChannel->GetChannelId(&descriptor.mChannelId));
  if (!descriptor.mChannelId) {
    return NS_ERROR_UNEXPECTED;
  }
  MOZ_TRY(mImpl->mStreams[0].mUri->GetSpec(descriptor.mResourceSpec));
  nsCOMPtr<nsIURI> originalUri;
  MOZ_TRY(rootChannel->GetOriginalURI(getter_AddRefs(originalUri)));
  if (!originalUri) {
    return NS_ERROR_UNEXPECTED;
  }
  MOZ_TRY(originalUri->GetSpec(descriptor.mOriginalSpec));
  mImpl->mRootOriginAttributes.CreateSuffix(descriptor.mOriginAttributesSuffix);
  nsCOMPtr<nsIURI> originalReferrer;
  MOZ_TRY(mImpl->mRootReferrerInfo->GetOriginalReferrer(
      getter_AddRefs(originalReferrer)));
  if (originalReferrer) {
    MOZ_TRY(originalReferrer->GetSpec(descriptor.mReferrerSpec));
  }
  descriptor.mReferrerPolicy =
      static_cast<uint8_t>(mImpl->mRootReferrerInfo->ReferrerPolicy());
  MOZ_TRY(mImpl->mRootReferrerInfo->GetSendReferrer(&descriptor.mSendReferrer));
  MOZ_TRY(rootChannel->GetLoadFlags(&descriptor.mLoadFlags));
  descriptor.mContentPolicyType =
      static_cast<uint32_t>(loadInfo->GetExternalContentPolicyType());
  descriptor.mHttpStatus = mImpl->mStreams[0].mHttpStatus;
  MOZ_TRY(rootChannel->GetContentType(descriptor.mContentType));
  (void)rootChannel->GetContentCharset(descriptor.mCharset);
  descriptor.mCharset.Trim(" \t\r\n");
  descriptor.mGeneration =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);

  nsAutoCString rootPrePath;
  MOZ_TRY(mImpl->mStreams[0].mUri->GetPrePath(rootPrePath));
  if (!rootPrePath.Equals(mImpl->mRootPrePath) ||
      !descriptor.mContentType.Equals("text/html"_ns,
                                      nsCaseInsensitiveCStringComparator) ||
      (!descriptor.mCharset.IsEmpty() &&
       !descriptor.mCharset.Equals("utf-8"_ns,
                                   nsCaseInsensitiveCStringComparator))) {
    return NS_ERROR_UNEXPECTED;
  }

  mImpl->mNativeRootReplacementChannelId = descriptor.mChannelId;
  MOZ_TRY(aRequest->Suspend());
  mImpl->mNativeParserRootSuspended = true;
  mImpl->mNativeProcessNextSequence = 1;
  mImpl->mNativeProcessDiscoverySequence = 0;
  const bool fullProcess =
      mImpl->mConfig.mMode == PreambleMode::TreeNativeParserFullProcessOverlap;
  const char* processMode =
      fullProcess ? "native-parser-full-process" : "native-parser-process";
  RuntimeLogEvent(
      "Connection %llu preamble %s phase=physical-root-"
      "suspended channel=%llu generation=%llu parent_pid=%llu protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
      static_cast<unsigned long long>(mImpl->mNativeRootReplacementChannelId),
      static_cast<unsigned long long>(descriptor.mGeneration),
      static_cast<unsigned long long>(base::GetCurrentProcId()));

  RefPtr self = this;
  const uint64_t generation = descriptor.mGeneration;
  NativeStylePreloadProcessRootCallbacks callbacks;
  callbacks.mReady = [self, generation]() {
    return self->OnNativeParserProcessRootReady(generation);
  };
  callbacks.mStyleDiscovered =
      [self, generation](const NativeStylePreloadProcessDescriptor& aStyle) {
        return self->OnNativeParserProcessStyleDiscovered(generation, aStyle);
      };
  callbacks.mFinished = [self, generation](
                            uint32_t aLastSequence, uint32_t aBodyBytes,
                            uint32_t aStyleCount, nsresult aStatus) {
    self->OnNativeParserProcessRootFinished(generation, aLastSequence,
                                            aBodyBytes, aStyleCount, aStatus);
  };
  callbacks.mFailed = [self, generation](nsresult aStatus) {
    self->OnNativeParserProcessFailed(generation, aStatus);
  };
  nsresult rv = NativeStylePreloadActivation::StartProcessRoot(
      std::move(descriptor), mImpl->mConfig.mMaxBytes, fullProcess,
      std::move(callbacks), mImpl->mNativeProcessRootRequestId);
  if (NS_SUCCEEDED(rv)) {
    RuntimeLogEvent(
        "Connection %llu preamble %s phase=root-registered "
        "request=%llu generation=%llu parent_pid=%llu protocol=h3\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
        static_cast<unsigned long long>(mImpl->mNativeProcessRootRequestId),
        static_cast<unsigned long long>(generation),
        static_cast<unsigned long long>(base::GetCurrentProcId()));
  }
  return rv;
}

nsresult ProxyPreambleOperation::OnNativeParserProcessRootReady(
    uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      !PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode) ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      !mImpl->mNativeProcessRootRequestId ||
      !mImpl->mNativeParserRootSuspended || mImpl->mNativeProcessRootReady) {
    return NS_ERROR_UNEXPECTED;
  }
  mImpl->mNativeProcessRootReady = true;
  nsCOMPtr<nsIRequest> root = mImpl->mStreams[0].mRequest;
  if (!root) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  mImpl->mNativeParserRootSuspended = false;
  nsresult rv = root->Resume();
  if (NS_SUCCEEDED(rv)) {
    const char* processMode =
        mImpl->mConfig.mMode == PreambleMode::TreeNativeParserFullProcessOverlap
            ? "native-parser-full-process"
            : "native-parser-process";
    RuntimeLogEvent(
        "Connection %llu preamble %s phase=root-ready-"
        "resume request=%llu generation=%llu parent_pid=%llu protocol=h3\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
        static_cast<unsigned long long>(mImpl->mNativeProcessRootRequestId),
        static_cast<unsigned long long>(aGeneration),
        static_cast<unsigned long long>(base::GetCurrentProcId()));
  }
  return rv;
}

nsresult ProxyPreambleOperation::OnNativeParserProcessStyleDiscovered(
    uint64_t aGeneration,
    const NativeStylePreloadProcessDescriptor& aDescriptor) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      !PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode) ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      aDescriptor.mRootRequestId != mImpl->mNativeProcessRootRequestId ||
      aDescriptor.mRootGeneration != aGeneration ||
      !aDescriptor.mStyleRequestId ||
      aDescriptor.mDiscoverySequence !=
          mImpl->mNativeProcessDiscoverySequence + 1 ||
      mImpl->mNativeParserDescriptorAccepted) {
    return NS_ERROR_UNEXPECTED;
  }

  nsHtml5StylePreloadDescriptor descriptor(
      nsString(aDescriptor.mUrl), nsString(aDescriptor.mCharset),
      nsString(aDescriptor.mCrossOrigin), nsString(aDescriptor.mMedia),
      nsString(aDescriptor.mReferrerPolicy), nsString(aDescriptor.mNonce),
      nsString(aDescriptor.mIntegrity), aDescriptor.mLinkPreload,
      nsString(aDescriptor.mFetchPriority));
  MOZ_TRY(OpenNativeParserStylesheet(std::move(descriptor),
                                     aDescriptor.mStyleRequestId));
  mImpl->mNativeProcessDiscoverySequence = aDescriptor.mDiscoverySequence;
  mImpl->mNativeParserDescriptorAccepted = true;
  const char* processMode =
      mImpl->mConfig.mMode == PreambleMode::TreeNativeParserFullProcessOverlap
          ? "native-parser-full-process"
          : "native-parser-process";
  RuntimeLogEvent(
      "Connection %llu preamble %s phase=style-opened "
      "root=%llu style=%llu sequence=%u parent_pid=%llu protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
      static_cast<unsigned long long>(aDescriptor.mRootRequestId),
      static_cast<unsigned long long>(aDescriptor.mStyleRequestId),
      aDescriptor.mDiscoverySequence,
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  return NS_OK;
}

void ProxyPreambleOperation::OnNativeParserProcessRootFinished(
    uint64_t aGeneration, uint32_t aLastSequence, uint32_t aBodyBytes,
    uint32_t aStyleCount, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  const bool valid =
      !mImpl->mCancelled && !mImpl->mNativeParserContractFailed &&
      PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode) &&
      aGeneration ==
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) &&
      mImpl->mNativeProcessRootRequestId && mImpl->mNativeProcessRootReady &&
      mImpl->mRootDone &&
      aLastSequence + 1 == mImpl->mNativeProcessNextSequence &&
      aBodyBytes == mImpl->mNativeParserRetargetBodyBytes.load(
                        std::memory_order_acquire) &&
      aStyleCount == 1 && mImpl->mNativeProcessDiscoverySequence == 1 &&
      mImpl->mNativeParserDescriptorAccepted && mImpl->mStreams.Length() == 2;
  if (!valid || NS_FAILED(aStatus)) {
    FailNativeParserContract(NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED,
                             "process-root-finish-invalid");
    return;
  }
  const uint64_t requestId = mImpl->mNativeProcessRootRequestId;
  mImpl->mNativeProcessRootRequestId = 0;
  mImpl->mBodyBytes = aBodyBytes;
  mImpl->mNativeParserFinished = true;
  const char* processMode =
      mImpl->mConfig.mMode == PreambleMode::TreeNativeParserFullProcessOverlap
          ? "native-parser-full-process"
          : "native-parser-process";
  RuntimeLogEvent(
      "Connection %llu preamble %s phase=parser-finished "
      "request=%llu generation=%llu sequence=%u bytes=%u styles=%u "
      "parent_pid=%llu protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
      static_cast<unsigned long long>(requestId),
      static_cast<unsigned long long>(aGeneration), aLastSequence, aBodyBytes,
      aStyleCount, static_cast<unsigned long long>(base::GetCurrentProcId()));
  MaybeFireBarrier();
  MaybeFinish();
}

void ProxyPreambleOperation::OnNativeParserProcessFailed(uint64_t aGeneration,
                                                         nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire)) {
    return;
  }
  FailNativeParserContract(NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE,
                           "process-transport-failed");
}

nsresult ProxyPreambleOperation::LinkNativeParserRootReplacement(
    uint64_t aGeneration,
    const NativeRootReplacementActivationDescriptor& aDescriptor) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mConfig.mMode !=
          PreambleMode::TreeNativeParserRootRendezvousOverlap ||
      !mImpl->mNativeParserRootSuspended ||
      !mImpl->mNativeRootReplacementRequestId ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      aDescriptor.mGeneration != aGeneration ||
      aDescriptor.mChannelId != mImpl->mNativeRootReplacementChannelId ||
      aDescriptor.mContentPolicyType !=
          static_cast<uint32_t>(ExtContentPolicy::TYPE_DOCUMENT) ||
      aDescriptor.mHttpStatus != mImpl->mStreams[0].mHttpStatus ||
      !aDescriptor.mContentType.Equals("text/html"_ns,
                                       nsCaseInsensitiveCStringComparator)) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsIHttpChannel> root =
      do_QueryInterface(mImpl->mStreams[0].mRequest);
  nsCOMPtr<nsIChannel> rootChannel =
      do_QueryInterface(mImpl->mStreams[0].mRequest);
  uint64_t actualChannelId = 0;
  nsAutoCString actualSpec;
  nsAutoCString actualOriginalSpec;
  nsAutoCString actualOriginAttributesSuffix;
  nsAutoCString actualReferrerSpec;
  nsAutoCString actualCharset;
  uint32_t actualLoadFlags = 0;
  nsCOMPtr<nsIURI> actualOriginalUri;
  nsCOMPtr<nsIURI> actualReferrerUri;
  mImpl->mRootOriginAttributes.CreateSuffix(actualOriginAttributesSuffix);
  if (!root || !rootChannel ||
      NS_FAILED(root->GetChannelId(&actualChannelId)) ||
      !mImpl->mStreams[0].mUri ||
      NS_FAILED(mImpl->mStreams[0].mUri->GetSpec(actualSpec)) ||
      NS_FAILED(
          rootChannel->GetOriginalURI(getter_AddRefs(actualOriginalUri))) ||
      !actualOriginalUri ||
      NS_FAILED(actualOriginalUri->GetSpec(actualOriginalSpec)) ||
      NS_FAILED(rootChannel->GetLoadFlags(&actualLoadFlags)) ||
      NS_FAILED(mImpl->mRootReferrerInfo->GetOriginalReferrer(
          getter_AddRefs(actualReferrerUri))) ||
      actualChannelId != aDescriptor.mChannelId ||
      !actualSpec.Equals(aDescriptor.mResourceSpec) ||
      !actualOriginalSpec.Equals(aDescriptor.mOriginalSpec) ||
      !actualOriginAttributesSuffix.Equals(
          aDescriptor.mOriginAttributesSuffix) ||
      actualLoadFlags != aDescriptor.mLoadFlags ||
      static_cast<uint8_t>(mImpl->mRootReferrerInfo->ReferrerPolicy()) !=
          aDescriptor.mReferrerPolicy) {
    return NS_ERROR_UNEXPECTED;
  }
  (void)rootChannel->GetContentCharset(actualCharset);
  if (actualReferrerUri) {
    MOZ_TRY(actualReferrerUri->GetSpec(actualReferrerSpec));
  }
  bool actualSendReferrer = false;
  MOZ_TRY(mImpl->mRootReferrerInfo->GetSendReferrer(&actualSendReferrer));
  actualCharset.Trim(" \t\r\n");
  if (!actualReferrerSpec.Equals(aDescriptor.mReferrerSpec) ||
      actualSendReferrer != aDescriptor.mSendReferrer ||
      !actualCharset.Equals(aDescriptor.mCharset,
                            nsCaseInsensitiveCStringComparator)) {
    return NS_ERROR_UNEXPECTED;
  }

  LogNativeParserRootReplacementPhase("connect-parent-same-root-linked");
  const uint64_t requestId = mImpl->mNativeRootReplacementRequestId;
  RefPtr self = this;
  nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NativeRootRedirectVerifierRun",
      [self, aGeneration, requestId] {
        self->RunNativeParserRootRedirectVerification(aGeneration, requestId);
      }));
  if (NS_SUCCEEDED(rv)) {
    LogNativeParserRootReplacementPhase("redirect-verifier-run-queued");
  }
  return rv;
}

void ProxyPreambleOperation::RunNativeParserRootRedirectVerification(
    uint64_t aGeneration, uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      aRequestId != mImpl->mNativeRootReplacementRequestId) {
    NativeStylePreloadActivation::ResolveRootReplacementRedirectVerification(
        aRequestId, NS_BINDING_ABORTED);
    return;
  }
  NativeStylePreloadActivation::NotifyRootReplacementRedirectVerificationRun(
      aRequestId);
  LogNativeParserRootReplacementPhase("redirect-verifier-run");
  RefPtr self = this;
  nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NativeRootRedirectVerifierCallback",
      [self, aGeneration, aRequestId] {
        self->ResolveNativeParserRootRedirectVerification(aGeneration,
                                                          aRequestId);
      }));
  if (NS_FAILED(rv)) {
    NativeStylePreloadActivation::ResolveRootReplacementRedirectVerification(
        aRequestId, rv);
    FailNativeParserContract(rv,
                             "root-redirect-verifier-callback-dispatch-failed");
    return;
  }
  LogNativeParserRootReplacementPhase("redirect-verifier-callback-queued");
}

void ProxyPreambleOperation::ResolveNativeParserRootRedirectVerification(
    uint64_t aGeneration, uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  const bool valid =
      !mImpl->mCancelled && !mImpl->mNativeParserContractFailed &&
      aGeneration ==
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) &&
      aRequestId == mImpl->mNativeRootReplacementRequestId;
  NativeStylePreloadActivation::ResolveRootReplacementRedirectVerification(
      aRequestId, valid ? NS_OK : NS_BINDING_ABORTED);
  if (valid) {
    LogNativeParserRootReplacementPhase("redirect-verifier-callback-resolved");
  }
}

nsresult ProxyPreambleOperation::OnNativeParserRootReplacementReady(
    uint64_t aGeneration, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (NS_FAILED(aStatus)) {
    FailNativeParserContract(aStatus, "root-replacement-setup-failed");
    return aStatus;
  }
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      !mImpl->mNativeRootReplacementRequestId ||
      !mImpl->mNativeParserRootSuspended ||
      mImpl->mNativeRootReplacementSetupReady) {
    return NS_ERROR_UNEXPECTED;
  }

  mImpl->mNativeRootReplacementSetupReady = true;
  LogNativeParserRootReplacementPhase("replacement-listener-published");
  LogNativeParserRootReplacementPhase("forward-on-start-sent");
  nsresult rv = ResumeNativeParserDocumentHandoffRoot();
  if (NS_FAILED(rv)) {
    FailNativeParserContract(rv, "root-resume-failed");
    return rv;
  }
  return NS_OK;
}

nsresult ProxyPreambleOperation::OnNativeParserRootForwardedStart(
    uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      !mImpl->mNativeRootReplacementRequestId ||
      !mImpl->mNativeRootReplacementSetupReady ||
      mImpl->mNativeParserRootSuspended ||
      mImpl->mNativeRootForwardedStartReceived || mImpl->mNativeParserScanner ||
      mImpl->mNativeParserConsumerReady) {
    return NS_ERROR_UNEXPECTED;
  }

  mImpl->mNativeRootForwardedStartReceived = true;
  LogNativeParserRootReplacementPhase("forward-on-start-received");
  mImpl->mNativeParserScanner =
      MakeUnique<nsHtml5SpeculativeScanner>(mImpl->mNativeParserTarget.get());
  LogNativeParserRootReplacementPhase("consumer-constructed-main");
  mImpl->mNativeParserConsumerGeneration = aGeneration;
  mImpl->mNativeParserConsumerReady = true;

  mImpl->mNativeRootLogicalRequest = new NativeRootReplacementRequest();
  nsresult rv = InstallNativeParserLogicalRetargetDelivery();
  if (NS_FAILED(rv)) {
    FailNativeParserContract(rv, "root-retarget-failed");
    return rv;
  }
  return NS_OK;
}

nsresult ProxyPreambleOperation::OnNativeParserRootData(uint64_t aGeneration,
                                                        nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      !mImpl->mNativeRootReplacementRequestId ||
      !mImpl->mNativeRootForwardedStartReceived ||
      !mImpl->mNativeRootLogicalRequest ||
      !mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire)) {
    return NS_ERROR_UNEXPECTED;
  }
  return DispatchNativeParserChunk(std::move(aData));
}

nsresult ProxyPreambleOperation::OnNativeParserRootStop(uint64_t aGeneration,
                                                        nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      aGeneration !=
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire) ||
      !mImpl->mNativeRootReplacementRequestId ||
      !mImpl->mNativeRootForwardedStartReceived ||
      !mImpl->mNativeRootLogicalRequest || NS_FAILED(aStatus)) {
    return NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED;
  }
  return DispatchNativeParserFinish();
}

nsresult ProxyPreambleOperation::QueueNativeParserRootBody(
    nsIInputStream* aInputStream, uint32_t aCount) {
  MOZ_ASSERT(NS_IsMainThread());
  const bool processMode =
      PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode);
  const bool rootRendezvous =
      mImpl->mConfig.mMode ==
      PreambleMode::TreeNativeParserRootRendezvousOverlap;
  if (!aInputStream || (!processMode && !rootRendezvous) ||
      (processMode ? !mImpl->mNativeProcessRootRequestId
                   : !mImpl->mNativeRootReplacementRequestId) ||
      (processMode && !mImpl->mNativeProcessRootReady) ||
      aCount >
          mImpl->mConfig.mMaxBytes - mImpl->mNativeParserRetargetBodyBytes.load(
                                         std::memory_order_acquire)) {
    return NS_ERROR_UNEXPECTED;
  }
  nsCString chunk;
  if (!chunk.SetLength(aCount, fallible)) {
    return NS_ERROR_OUT_OF_MEMORY;
  }
  uint32_t offset = 0;
  while (offset < aCount) {
    uint32_t read = 0;
    nsresult rv = aInputStream->Read(chunk.BeginWriting() + offset,
                                     aCount - offset, &read);
    if (NS_FAILED(rv) || !read) {
      return NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
    }
    offset += read;
  }
  mImpl->mNativeParserRetargetBodyBytes.fetch_add(aCount,
                                                  std::memory_order_acq_rel);
  if (processMode) {
    const uint32_t sequence = mImpl->mNativeProcessNextSequence;
    nsresult rv = NativeStylePreloadActivation::ForwardProcessRootData(
        mImpl->mNativeProcessRootRequestId,
        mImpl->mNativeParserGeneration.load(std::memory_order_acquire),
        sequence, std::move(chunk));
    if (NS_SUCCEEDED(rv)) {
      ++mImpl->mNativeProcessNextSequence;
      const char* processMode =
          mImpl->mConfig.mMode ==
                  PreambleMode::TreeNativeParserFullProcessOverlap
              ? "native-parser-full-process"
              : "native-parser-process";
      RuntimeLogEvent(
          "Connection %llu preamble %s phase=root-data "
          "request=%llu generation=%llu sequence=%u bytes=%u parent_pid=%llu "
          "protocol=h3\n",
          static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
          static_cast<unsigned long long>(mImpl->mNativeProcessRootRequestId),
          static_cast<unsigned long long>(
              mImpl->mNativeParserGeneration.load(std::memory_order_acquire)),
          sequence, aCount,
          static_cast<unsigned long long>(base::GetCurrentProcId()));
    }
    return rv;
  }
  return NativeStylePreloadActivation::ForwardRootReplacementData(
      mImpl->mNativeRootReplacementRequestId, std::move(chunk));
}

void ProxyPreambleOperation::LogNativeParserDocumentHandoffPhase(
    const char* aPhase) const {
  MOZ_ASSERT(mImpl->mConfig.mMode ==
             PreambleMode::TreeNativeParserDocumentHandoffOverlap);
  RuntimeLogEvent(
      "Connection %llu preamble native-parser-document-handoff phase=%s "
      "protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId), aPhase);
}

void ProxyPreambleOperation::LogNativeParserRetargetPhase(
    const char* aPhase) const {
  MOZ_ASSERT(PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode));
  RuntimeLogEvent(
      "Connection %llu preamble native-parser-retarget phase=%s protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId), aPhase);
}

void ProxyPreambleOperation::LogNativeParserRootReplacementPhase(
    const char* aPhase) const {
  MOZ_ASSERT(mImpl->mConfig.mMode ==
             PreambleMode::TreeNativeParserRootRendezvousOverlap);
  RuntimeLogEvent(
      "Connection %llu preamble native-parser-root-replacement phase=%s "
      "channel=%llu request=%llu generation=%llu protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId), aPhase,
      static_cast<unsigned long long>(mImpl->mNativeRootReplacementChannelId),
      static_cast<unsigned long long>(mImpl->mNativeRootReplacementRequestId),
      static_cast<unsigned long long>(
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire)));
}

nsresult ProxyPreambleOperation::InstallNativeParserRetargetDelivery(
    nsIRequest* aRequest) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode));
  const bool rootReplacementForwarded =
      mImpl->mConfig.mMode ==
          PreambleMode::TreeNativeParserRootRendezvousOverlap &&
      mImpl->mNativeRootReplacementSetupReady &&
      mImpl->mNativeRootForwardedStartReceived &&
      !mImpl->mNativeParserRootSuspended;
  if (!aRequest || !mImpl->mNativeParserTarget ||
      (!mImpl->mNativeParserRootSuspended && !rootReplacementForwarded) ||
      mImpl->mNativeParserRetargetArmed.load(std::memory_order_acquire) ||
      mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire)) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsIThreadRetargetableRequest> retargetable =
      do_QueryInterface(aRequest);
  if (!retargetable) {
    return NS_ERROR_NO_INTERFACE;
  }
  mImpl->mNativeParserRetargetListenerChainChecked.store(
      false, std::memory_order_release);
  mImpl->mNativeParserRetargetArmed.store(true, std::memory_order_release);
  nsresult retargetRv =
      retargetable->RetargetDeliveryTo(mImpl->mNativeParserTarget);
  nsCOMPtr<nsISerialEventTarget> deliveryTarget;
  nsresult targetRv =
      retargetable->GetDeliveryTarget(getter_AddRefs(deliveryTarget));
  const bool listenerChecked =
      mImpl->mNativeParserRetargetListenerChainChecked.load(
          std::memory_order_acquire);
  const bool targetMatches =
      NS_SUCCEEDED(targetRv) && deliveryTarget &&
      SameCOMIdentity(deliveryTarget, mImpl->mNativeParserTarget);
  if (!detail::PreambleRetargetDeliveryVerified(
          listenerChecked, NS_SUCCEEDED(retargetRv), targetMatches)) {
    mImpl->mNativeParserRetargetArmed.store(false, std::memory_order_release);
    return NS_FAILED(retargetRv) ? retargetRv : NS_ERROR_UNEXPECTED;
  }
  mImpl->mNativeParserRetargetInstalled.store(true, std::memory_order_release);
  RuntimeLogEvent(
      "Connection %llu preamble native-parser-retarget "
      "phase=delivery-retargeted target=html5-parser verified=1 protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId));
  return NS_OK;
}

nsresult ProxyPreambleOperation::InstallNativeParserLogicalRetargetDelivery() {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(mImpl->mConfig.mMode ==
             PreambleMode::TreeNativeParserRootRendezvousOverlap);
  if (!mImpl->mNativeRootLogicalRequest || !mImpl->mNativeParserTarget ||
      !mImpl->mNativeRootReplacementSetupReady ||
      !mImpl->mNativeRootForwardedStartReceived ||
      mImpl->mNativeParserRootSuspended ||
      mImpl->mNativeParserRetargetArmed.load(std::memory_order_acquire) ||
      mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire)) {
    return NS_ERROR_UNEXPECTED;
  }

  mImpl->mNativeParserRetargetArmed.store(true, std::memory_order_release);
  MOZ_TRY(mImpl->mNativeRootLogicalRequest->RetargetDeliveryTo(
      mImpl->mNativeParserTarget));
  nsCOMPtr<nsISerialEventTarget> deliveryTarget;
  MOZ_TRY(mImpl->mNativeRootLogicalRequest->GetDeliveryTarget(
      getter_AddRefs(deliveryTarget)));
  if (!deliveryTarget ||
      !SameCOMIdentity(deliveryTarget, mImpl->mNativeParserTarget)) {
    mImpl->mNativeParserRetargetArmed.store(false, std::memory_order_release);
    return NS_ERROR_UNEXPECTED;
  }
  mImpl->mNativeParserRetargetInstalled.store(true, std::memory_order_release);
  RuntimeLogEvent(
      "Connection %llu preamble native-parser-retarget "
      "phase=delivery-retargeted target=html5-parser verified=1 protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId));
  LogNativeParserRootReplacementPhase("logical-request-retargeted");
  return NS_OK;
}

nsresult ProxyPreambleOperation::CheckNativeParserRetargetListener(
    uint32_t aStreamId) {
  MOZ_ASSERT(NS_IsMainThread());
  const bool rootReplacementForwarded =
      mImpl->mConfig.mMode ==
          PreambleMode::TreeNativeParserRootRendezvousOverlap &&
      mImpl->mNativeRootReplacementSetupReady &&
      mImpl->mNativeRootForwardedStartReceived &&
      !mImpl->mNativeParserRootSuspended;
  if (!PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode) ||
      aStreamId != 0 ||
      !mImpl->mNativeParserRetargetArmed.load(std::memory_order_acquire) ||
      mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire) ||
      (!mImpl->mNativeParserRootSuspended && !rootReplacementForwarded) ||
      !mImpl->mNativeParserScanner) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  mImpl->mNativeParserRetargetListenerChainChecked.store(
      true, std::memory_order_release);
  return NS_OK;
}

nsresult
ProxyPreambleOperation::DispatchNativeParserReplacementListenerInstall() {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(PreambleModeUsesNativeParserHandoff(mImpl->mConfig.mMode));
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      !mImpl->mNativeParserTarget || !mImpl->mNativeParserRootSuspended ||
      !mImpl->mNativeParserScanner ||
      mImpl->mNativeParserReplacementInstallQueued ||
      mImpl->mNativeParserConsumerReady.load(std::memory_order_acquire) ||
      (PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode) &&
       !mImpl->mNativeParserRetargetInstalled.load(
           std::memory_order_acquire))) {
    return NS_ERROR_UNEXPECTED;
  }

  mImpl->mNativeParserReplacementInstallQueued = true;
  const uint64_t generation =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
  RefPtr self = this;
  nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NativeParserReplacementListenerInstall", [self, generation] {
        self->OnNativeParserReplacementListenerInstalled(generation);
      }));
  if (NS_FAILED(rv)) {
    mImpl->mNativeParserReplacementInstallQueued = false;
  }
  return rv;
}

void ProxyPreambleOperation::OnNativeParserReplacementListenerInstalled(
    uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserGeneration.load(
                               std::memory_order_acquire) != aGeneration) {
    return;
  }
  if (!mImpl->mNativeParserReplacementInstallQueued ||
      !mImpl->mNativeParserRootSuspended || !mImpl->mNativeParserScanner ||
      mImpl->mNativeParserConsumerReady) {
    FailNativeParserContract(NS_ERROR_UNEXPECTED,
                             "replacement-listener-install-failed");
    return;
  }

  // This ordinary main-thread event models the replacement-listener handoff.
  // It is the atomic publication point: body delivery can observe neither a
  // ready consumer with the wrong generation nor a resumed root without one.
  mImpl->mNativeParserReplacementInstallQueued = false;
  mImpl->mNativeParserConsumerGeneration = aGeneration;
  mImpl->mNativeParserConsumerReady = true;
  if (mImpl->mConfig.mMode ==
      PreambleMode::TreeNativeParserDocumentHandoffOverlap) {
    LogNativeParserDocumentHandoffPhase("replacement-listener-installed");
  } else {
    LogNativeParserRetargetPhase("replacement-listener-installed");
  }
  nsresult rv = ResumeNativeParserDocumentHandoffRoot();
  if (NS_FAILED(rv)) {
    FailNativeParserContract(rv, "root-resume-failed");
    if (!mImpl->mStreams.IsEmpty() && mImpl->mStreams[0].mRequest) {
      (void)mImpl->mStreams[0].mRequest->Cancel(rv);
    }
  }
}

nsresult ProxyPreambleOperation::ResumeNativeParserDocumentHandoffRoot() {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(PreambleModeUsesNativeParserHandoff(mImpl->mConfig.mMode));
  if (!mImpl->mNativeParserRootSuspended || mImpl->mStreams.IsEmpty() ||
      !mImpl->mStreams[0].mRequest) {
    return NS_ERROR_UNEXPECTED;
  }
  nsCOMPtr<nsIRequest> root = mImpl->mStreams[0].mRequest;
  // Clear the owned-suspend bit before Resume(), so even a reentrant failure
  // path cannot attempt to balance the same Suspend() twice.
  mImpl->mNativeParserRootSuspended = false;
  // Mark the semantic Resume call before invoking it. A channel may schedule
  // body delivery from Resume(); this ordering keeps first-parser-feed
  // deterministically after the handoff boundary. A failed Resume invalidates
  // the operation through the caller's fail-closed path.
  if (mImpl->mConfig.mMode ==
      PreambleMode::TreeNativeParserDocumentHandoffOverlap) {
    LogNativeParserDocumentHandoffPhase("handoff-resume");
  } else if (mImpl->mConfig.mMode ==
             PreambleMode::TreeNativeParserRootRendezvousOverlap) {
    LogNativeParserRootReplacementPhase("physical-root-resume");
  } else {
    LogNativeParserRetargetPhase("handoff-resume");
  }
  nsresult rv = root->Resume();
  return rv;
}

nsresult ProxyPreambleOperation::DispatchNativeParserChunk(nsCString&& aChunk) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(PreambleModeUsesNativeParser(mImpl->mConfig.mMode));
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mNativeParserFinishQueued || !mImpl->mNativeParserTarget) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsISerialEventTarget> deliveryTarget = mImpl->mNativeParserTarget;
  if (mImpl->mConfig.mMode ==
      PreambleMode::TreeNativeParserRootRendezvousOverlap) {
    if (!mImpl->mNativeRootLogicalRequest) {
      return NS_ERROR_UNEXPECTED;
    }
    MOZ_TRY(mImpl->mNativeRootLogicalRequest->GetDeliveryTarget(
        getter_AddRefs(deliveryTarget)));
    if (!deliveryTarget) {
      return NS_ERROR_UNEXPECTED;
    }
  }

  const uint64_t generation =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
  const uint32_t sequence = ++mImpl->mNativeParserNextSequence;
  const uint32_t bytes = aChunk.Length();
  ++mImpl->mNativeParserPendingMainCallbacks;
  RefPtr self = this;
  nsresult rv = deliveryTarget->Dispatch(
      NS_NewRunnableFunction(
          "NaiveFox::NativeParserFeed",
          [self, generation, sequence, chunk = std::move(aChunk)]() mutable {
            if (self->mImpl->mNativeParserGeneration.load(
                    std::memory_order_acquire) != generation) {
              return;
            }

            if (self->mImpl->mConfig.mMode ==
                    PreambleMode::TreeNativeParserRootRendezvousOverlap &&
                !self->mImpl->mNativeParserFirstRetargetFeedLogged.exchange(
                    true, std::memory_order_acq_rel)) {
              RuntimeLogEvent(
                  "Connection %llu preamble native-parser-retarget "
                  "phase=first-parser-feed delivery=logical-background "
                  "protocol=h3\n",
                  static_cast<unsigned long long>(self->mImpl->mConnectionId));
            }

            // The controlled document is UTF-8 and its markup is ASCII. Avoid
            // inventing an incremental decoder in this DOM-free arm: a byte
            // outside ASCII is unsupported and invalidates the admission.
            const char* bytes = chunk.BeginReading();
            const bool ascii =
                std::all_of(bytes, bytes + chunk.Length(), [](char value) {
                  return static_cast<unsigned char>(value) < 0x80;
                });
            nsresult parserStatus = ascii ? NS_OK : NS_ERROR_ILLEGAL_INPUT;
            nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
            nsTArray<nsHtml5LeanPreloadDescriptor> leanDescriptors;
            if (NS_SUCCEEDED(parserStatus)) {
              if (!self->mImpl->mNativeParserScanner &&
                  PreambleModeUsesLightweightNativeParser(
                      self->mImpl->mConfig.mMode)) {
                self->mImpl->mNativeParserScanner =
                    MakeUnique<nsHtml5SpeculativeScanner>();
              }
              if (!self->mImpl->mNativeParserScanner) {
                parserStatus = NS_ERROR_UNEXPECTED;
              } else {
                parserStatus = self->mImpl->mNativeParserScanner->Feed(
                    NS_ConvertASCIItoUTF16(chunk));
              }
              if (NS_SUCCEEDED(parserStatus)) {
                if (PreambleModeUsesNativeParserResourceTree(
                        self->mImpl->mConfig.mMode)) {
                  self->mImpl->mNativeParserScanner->TakeLeanDescriptors(
                      leanDescriptors);
                } else {
                  self->mImpl->mNativeParserScanner->TakeStyleDescriptors(
                      descriptors);
                }
              }
            }
            if (self->mImpl->mNativeParserGeneration.load(
                    std::memory_order_acquire) != generation) {
              return;
            }

            nsCOMPtr<nsIRunnable> completion = NS_NewRunnableFunction(
                "NaiveFox::NativeParserFeedComplete",
                [self, generation, sequence, parserStatus,
                 descriptors = std::move(descriptors),
                 leanDescriptors = std::move(leanDescriptors)]() mutable {
                  self->OnNativeParserOutput(
                      generation, sequence, false, parserStatus,
                      std::move(descriptors), std::move(leanDescriptors));
                });
            (void)NS_DispatchToMainThread(
                CreateRenderBlockingRunnable(completion.forget()));
          }),
      NS_DISPATCH_NORMAL);
  if (NS_FAILED(rv)) {
    --mImpl->mNativeParserPendingMainCallbacks;
    return rv;
  }
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=chunk-queued sequence=%u "
      "bytes=%u generation=%llu protocol=%s\n",
      sequence, bytes, static_cast<unsigned long long>(generation),
      ProtocolName(mImpl->mProtocol));
  return NS_OK;
}

nsresult ProxyPreambleOperation::DispatchNativeParserFinish() {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(PreambleModeUsesNativeParser(mImpl->mConfig.mMode));
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mNativeParserFinishQueued || !mImpl->mNativeParserTarget) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsISerialEventTarget> deliveryTarget = mImpl->mNativeParserTarget;
  if (mImpl->mConfig.mMode ==
      PreambleMode::TreeNativeParserRootRendezvousOverlap) {
    if (!mImpl->mNativeRootLogicalRequest) {
      return NS_ERROR_UNEXPECTED;
    }
    MOZ_TRY(mImpl->mNativeRootLogicalRequest->GetDeliveryTarget(
        getter_AddRefs(deliveryTarget)));
    if (!deliveryTarget) {
      return NS_ERROR_UNEXPECTED;
    }
  }

  mImpl->mNativeParserFinishQueued = true;
  const uint64_t generation =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
  const uint32_t sequence = ++mImpl->mNativeParserNextSequence;
  ++mImpl->mNativeParserPendingMainCallbacks;
  RefPtr self = this;
  nsresult rv = deliveryTarget->Dispatch(
      NS_NewRunnableFunction(
          "NaiveFox::NativeParserFinish",
          [self, generation, sequence] {
            if (self->mImpl->mNativeParserGeneration.load(
                    std::memory_order_acquire) != generation) {
              return;
            }
            nsresult parserStatus = NS_OK;
            nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
            nsTArray<nsHtml5LeanPreloadDescriptor> leanDescriptors;
            if (!self->mImpl->mNativeParserScanner &&
                PreambleModeUsesLightweightNativeParser(
                    self->mImpl->mConfig.mMode)) {
              self->mImpl->mNativeParserScanner =
                  MakeUnique<nsHtml5SpeculativeScanner>();
            }
            if (!self->mImpl->mNativeParserScanner) {
              parserStatus = NS_ERROR_UNEXPECTED;
            } else {
              if (PreambleModeUsesRetargetedNativeParser(
                      self->mImpl->mConfig.mMode)) {
                self->LogNativeParserRetargetPhase("parser-data-finished");
              }
              parserStatus = self->mImpl->mNativeParserScanner->Finish();
            }
            if (NS_SUCCEEDED(parserStatus)) {
              if (PreambleModeUsesNativeParserResourceTree(
                      self->mImpl->mConfig.mMode)) {
                self->mImpl->mNativeParserScanner->TakeLeanDescriptors(
                    leanDescriptors);
              } else {
                self->mImpl->mNativeParserScanner->TakeStyleDescriptors(
                    descriptors);
              }
            }
            // Destruction must stay on the parser target too.
            self->mImpl->mNativeParserScanner = nullptr;
            if (self->mImpl->mNativeParserGeneration.load(
                    std::memory_order_acquire) != generation) {
              return;
            }
            nsCOMPtr<nsIRunnable> completion = NS_NewRunnableFunction(
                "NaiveFox::NativeParserFinishComplete",
                [self, generation, sequence, parserStatus,
                 descriptors = std::move(descriptors),
                 leanDescriptors = std::move(leanDescriptors)]() mutable {
                  self->OnNativeParserOutput(
                      generation, sequence, true, parserStatus,
                      std::move(descriptors), std::move(leanDescriptors));
                });
            (void)NS_DispatchToMainThread(
                CreateRenderBlockingRunnable(completion.forget()));
          }),
      NS_DISPATCH_NORMAL);
  if (NS_FAILED(rv)) {
    --mImpl->mNativeParserPendingMainCallbacks;
    mImpl->mNativeParserFinishQueued = false;
    return rv;
  }
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=finish-queued sequence=%u "
      "generation=%llu protocol=%s\n",
      sequence, static_cast<unsigned long long>(generation),
      ProtocolName(mImpl->mProtocol));
  return NS_OK;
}

void ProxyPreambleOperation::OnNativeParserOutput(
    uint64_t aGeneration, uint32_t aSequence, bool aFinished, nsresult aStatus,
    nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors,
    nsTArray<nsHtml5LeanPreloadDescriptor>&& aLeanDescriptors) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserGeneration.load(
                               std::memory_order_acquire) != aGeneration) {
    return;
  }
  MOZ_ASSERT(mImpl->mNativeParserPendingMainCallbacks > 0);
  --mImpl->mNativeParserPendingMainCallbacks;
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=%s sequence=%u "
      "descriptors=%u status=0x%08x generation=%llu protocol=%s\n",
      aFinished ? "parser-finished" : "chunk-flushed", aSequence,
      static_cast<unsigned>(aDescriptors.Length() + aLeanDescriptors.Length()),
      static_cast<unsigned>(aStatus),
      static_cast<unsigned long long>(aGeneration),
      ProtocolName(mImpl->mProtocol));

  if (NS_FAILED(aStatus)) {
    FailNativeParserContract(aStatus,
                             aFinished ? "finish-failed" : "feed-failed");
    return;
  }
  const bool resourceTree =
      PreambleModeUsesNativeParserResourceTree(mImpl->mConfig.mMode);
  if (resourceTree && !aDescriptors.IsEmpty()) {
    FailNativeParserContract(NS_ERROR_UNEXPECTED,
                             "resource-tree-style-output-not-empty");
    return;
  }
  if (!resourceTree && !aLeanDescriptors.IsEmpty()) {
    FailNativeParserContract(NS_ERROR_UNEXPECTED,
                             "stylesheet-lean-output-not-empty");
    return;
  }
  if (resourceTree) {
    for (auto& descriptor : aLeanDescriptors) {
      nsresult rv = OpenNativeParserResource(std::move(descriptor));
      if (NS_FAILED(rv)) {
        FailNativeParserContract(rv, "resource-admission-failed");
        return;
      }
    }
  } else if (!aDescriptors.IsEmpty()) {
    if (aDescriptors.Length() != 1 || mImpl->mNativeParserDescriptorAccepted) {
      FailNativeParserContract(NS_ERROR_UNEXPECTED, "stylesheet-count-not-one");
      return;
    }
    nsresult rv = OpenNativeParserStylesheet(std::move(aDescriptors[0]));
    if (NS_FAILED(rv)) {
      FailNativeParserContract(rv, "stylesheet-admission-failed");
      return;
    }
    mImpl->mNativeParserDescriptorAccepted = true;
  }
  if (aFinished) {
    if (resourceTree) {
      if (mImpl->mNativeParserResourceDescriptorsAccepted !=
              mImpl->mConfig.mMaxAssets ||
          mImpl->mStreams.Length() != mImpl->mConfig.mMaxAssets + 1) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "resource-count-mismatch");
        return;
      }
    } else {
      if (!mImpl->mNativeParserDescriptorAccepted ||
          mImpl->mStreams.Length() != 2) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "stylesheet-count-not-one");
        return;
      }
    }
    mImpl->mNativeParserFinished = true;
    if (mImpl->mConfig.mMode ==
            PreambleMode::TreeNativeParserRootRendezvousOverlap &&
        mImpl->mNativeRootReplacementRequestId) {
      NativeStylePreloadActivation::CompleteRootReplacement(
          mImpl->mNativeRootReplacementRequestId, NS_OK);
      mImpl->mNativeRootReplacementRequestId = 0;
      mImpl->mNativeRootLogicalRequest = nullptr;
    }
    nsresult stopRv = MaybeIssueNativeParserNavigationStop();
    if (NS_FAILED(stopRv)) {
      FailNativeParserContract(stopRv, "navigation-stop-cancel-failed");
      return;
    }
  }
  MaybeFireBarrier();
  MaybeFinish();
}

nsresult ProxyPreambleOperation::OpenNativeParserResource(
    nsHtml5LeanPreloadDescriptor&& aDescriptor) {
  MOZ_ASSERT(NS_IsMainThread());
  using DescriptorKind = nsHtml5LeanPreloadDescriptor::Kind;
  if (!PreambleModeUsesNativeParserResourceTree(mImpl->mConfig.mMode) ||
      mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      !mImpl->mRootReferrerInfo || !mImpl->mHaveRootOriginAttributes) {
    return NS_ERROR_UNEXPECTED;
  }

  const DescriptorKind descriptorKind = aDescriptor.GetKind();
  if (descriptorKind == DescriptorKind::DocumentCharset ||
      descriptorKind == DescriptorKind::DocumentMode ||
      descriptorKind == DescriptorKind::CharsetComplaint) {
    return NS_OK;
  }
  if (descriptorKind == DescriptorKind::Base) {
    if (mImpl->mNativeParserBaseSeen) {
      return NS_OK;
    }
    nsCOMPtr<nsIURI> baseUri;
    MOZ_TRY(NS_NewURI(getter_AddRefs(baseUri),
                      NS_ConvertUTF16toUTF8(aDescriptor.UrlOrSizes()), nullptr,
                      mImpl->mStreams[0].mUri));
    nsAutoCString prePath;
    MOZ_TRY(baseUri->GetPrePath(prePath));
    if (!prePath.Equals(mImpl->mRootPrePath)) {
      return NS_ERROR_DOM_BAD_URI;
    }
    mImpl->mNativeParserBaseURI = baseUri;
    mImpl->mNativeParserBaseSeen = true;
    return NS_OK;
  }
  // A bounded first product experiment does not guess CSP/referrer or picture
  // semantics. Their ordered presence is observable and invalidates the arm.
  if (descriptorKind == DescriptorKind::CSP ||
      descriptorKind == DescriptorKind::MetaReferrer ||
      descriptorKind == DescriptorKind::Unsupported) {
    return NS_ERROR_NOT_IMPLEMENTED;
  }
  if (mImpl->mNativeParserResourceDescriptorsAccepted >=
      mImpl->mConfig.mMaxAssets) {
    return NS_ERROR_FILE_TOO_BIG;
  }

  NativeParserResourceKind resourceKind;
  switch (descriptorKind) {
    case DescriptorKind::Style:
      if (aDescriptor.UrlOrSizes().IsEmpty() ||
          !aDescriptor.CharsetOrSrcset().IsEmpty() ||
          !aDescriptor.CrossOrigin().IsEmpty() ||
          !aDescriptor.Media().IsEmpty() ||
          !aDescriptor.ReferrerPolicyOrIntegrity().IsEmpty() ||
          !aDescriptor.NonceOrType().IsEmpty() ||
          !aDescriptor.TypeOrSizesOrIntegrity().IsEmpty() ||
          !aDescriptor.FetchPriority().IsEmpty() ||
          aDescriptor.IsLinkPreload()) {
        return NS_ERROR_NOT_IMPLEMENTED;
      }
      resourceKind = NativeParserResourceKind::Style;
      break;
    case DescriptorKind::ScriptFromHead:
      if (aDescriptor.UrlOrSizes().IsEmpty() ||
          !aDescriptor.CharsetOrSrcset().IsEmpty() ||
          !aDescriptor.TypeOrSizesOrIntegrity().IsEmpty() ||
          !aDescriptor.CrossOrigin().IsEmpty() ||
          !aDescriptor.Media().IsEmpty() ||
          !aDescriptor.NonceOrType().IsEmpty() ||
          !aDescriptor.ReferrerPolicyOrIntegrity().IsEmpty() ||
          !aDescriptor.ScriptReferrerPolicy().IsEmpty() ||
          !aDescriptor.FetchPriority().IsEmpty() || aDescriptor.IsAsync() ||
          !aDescriptor.IsDefer() || aDescriptor.IsLinkPreload()) {
        return NS_ERROR_NOT_IMPLEMENTED;
      }
      resourceKind = NativeParserResourceKind::Script;
      break;
    case DescriptorKind::Image:
      if (aDescriptor.UrlOrSizes().IsEmpty() ||
          !aDescriptor.CharsetOrSrcset().IsEmpty() ||
          !aDescriptor.TypeOrSizesOrIntegrity().IsEmpty() ||
          !aDescriptor.CrossOrigin().IsEmpty() ||
          !aDescriptor.Media().IsEmpty() ||
          !aDescriptor.NonceOrType().IsEmpty() ||
          !aDescriptor.ReferrerPolicyOrIntegrity().IsEmpty() ||
          !aDescriptor.FetchPriority().IsEmpty() ||
          aDescriptor.IsLinkPreload()) {
        return NS_ERROR_NOT_IMPLEMENTED;
      }
      resourceKind = NativeParserResourceKind::Image;
      break;
    default:
      return NS_ERROR_NOT_IMPLEMENTED;
  }

  nsCOMPtr<nsIURI> resourceUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(resourceUri),
                    NS_ConvertUTF16toUTF8(aDescriptor.UrlOrSizes()), nullptr,
                    mImpl->mNativeParserBaseURI
                        ? mImpl->mNativeParserBaseURI.get()
                        : mImpl->mStreams[0].mUri.get()));
  nsAutoCString prePath;
  nsAutoCString spec;
  MOZ_TRY(resourceUri->GetPrePath(prePath));
  MOZ_TRY(resourceUri->GetSpec(spec));
  if (!prePath.Equals(mImpl->mRootPrePath) ||
      mImpl->mDiscoveredSpecs.Contains(spec)) {
    return NS_ERROR_DOM_BAD_URI;
  }

  nsCOMPtr<nsIChannel> channel;
  MOZ_TRY(NewNativeParserResourcePreloadChannel(
      resourceUri, mImpl->mStreams[0].mUri, resourceKind,
      mImpl->mRootOriginAttributes, mImpl->mRootReferrerInfo, mImpl->mLoadGroup,
      mImpl->mRoute.mProxyInfo, mImpl->mProtocol, getter_AddRefs(channel)));
  const uint32_t streamId = mImpl->mStreams.Length();
  auto& stream = *mImpl->mStreams.AppendElement();
  stream.mUri = resourceUri;
  stream.mRequest = channel;
  mImpl->mDiscoveredSpecs.AppendElement(spec);
  RefPtr<StreamListener> listener = new StreamListener(this, streamId);
  MOZ_TRY(channel->SetNotificationCallbacks(listener));
  const bool deferImageToNextMainTurn =
      resourceKind == NativeParserResourceKind::Image &&
      mImpl->mConfig.mMode ==
          PreambleMode::TreeNativeParserResourceCommittedOverlap &&
      mImpl->mConfig.mMaxAssets == 6;
  if (deferImageToNextMainTurn) {
    stream.mPendingOpenListener = listener;
    if (!mImpl->mDeferredImageOpenDispatched) {
      mImpl->mDeferredImageOpenDispatched = true;
      RefPtr self = this;
      nsresult dispatchRv = NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ReleaseDeferredNativeParserImages",
          [self] { self->ReleaseDeferredNativeParserImages(); }));
      if (NS_FAILED(dispatchRv)) {
        mImpl->mDeferredImageOpenDispatched = false;
        mImpl->mDiscoveredSpecs.RemoveLastElement();
        mImpl->mStreams.RemoveLastElement();
        return dispatchRv;
      }
    }
  } else {
    nsresult rv = channel->AsyncOpen(listener);
    if (NS_FAILED(rv)) {
      mImpl->mDiscoveredSpecs.RemoveLastElement();
      mImpl->mStreams.RemoveLastElement();
      return rv;
    }
  }
  ++mImpl->mNativeParserResourceDescriptorsAccepted;
  RuntimeLogEvent(
      "Preamble native-parser-resource-tree lifecycle=resource-%s "
      "stream=%u kind=%s referrer=inherited protocol=%s\n",
      deferImageToNextMainTurn ? "prepared" : "opened", streamId,
      resourceKind == NativeParserResourceKind::Style    ? "style"
      : resourceKind == NativeParserResourceKind::Script ? "script"
                                                         : "image",
      ProtocolName(mImpl->mProtocol));
  return NS_OK;
}

void ProxyPreambleOperation::ReleaseDeferredNativeParserImages() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mConfig.mMode !=
          PreambleMode::TreeNativeParserResourceCommittedOverlap ||
      mImpl->mConfig.mMaxAssets != 6) {
    return;
  }
  for (uint32_t streamId = 1; streamId < mImpl->mStreams.Length();
       ++streamId) {
    auto& stream = mImpl->mStreams[streamId];
    if (!stream.mPendingOpenListener) {
      continue;
    }
    nsCOMPtr<nsIChannel> channel = do_QueryInterface(stream.mRequest);
    nsCOMPtr<nsIStreamListener> listener =
        stream.mPendingOpenListener.forget();
    if (!channel || !listener) {
      FailNativeParserContract(NS_ERROR_UNEXPECTED,
                               "deferred-image-open-invalid");
      return;
    }
    nsresult rv = channel->AsyncOpen(listener);
    if (NS_FAILED(rv)) {
      FailNativeParserContract(rv, "deferred-image-open-failed");
      return;
    }
    RuntimeLogEvent(
        "Preamble native-parser-resource-tree "
        "lifecycle=deferred-resource-opened stream=%u kind=image "
        "cause=next-main-turn protocol=%s\n",
        streamId, ProtocolName(mImpl->mProtocol));
  }
}

nsresult ProxyPreambleOperation::OpenNativeParserStylesheet(
    nsHtml5StylePreloadDescriptor&& aDescriptor,
    uint64_t aProcessStyleRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  const bool processMode =
      PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode);
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mNativeParserDescriptorAccepted || mImpl->mStreams.Length() != 1 ||
      !mImpl->mRootReferrerInfo || !mImpl->mHaveRootOriginAttributes ||
      processMode != bool(aProcessStyleRequestId) ||
      aDescriptor.Url().IsEmpty() || aDescriptor.IsLinkPreload() ||
      !aDescriptor.Charset().IsEmpty() ||
      !aDescriptor.CrossOrigin().IsEmpty() || !aDescriptor.Media().IsEmpty() ||
      !aDescriptor.ReferrerPolicy().IsEmpty() ||
      !aDescriptor.Nonce().IsEmpty() || !aDescriptor.Integrity().IsEmpty() ||
      !aDescriptor.FetchPriority().IsEmpty()) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsIURI> resourceUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(resourceUri),
                    NS_ConvertUTF16toUTF8(aDescriptor.Url()), nullptr,
                    mImpl->mStreams[0].mUri));
  nsAutoCString prePath;
  nsAutoCString spec;
  MOZ_TRY(resourceUri->GetPrePath(prePath));
  MOZ_TRY(resourceUri->GetSpec(spec));
  if (!prePath.Equals(mImpl->mRootPrePath) ||
      mImpl->mDiscoveredSpecs.Contains(spec)) {
    return NS_ERROR_DOM_BAD_URI;
  }

  const uint32_t streamId = mImpl->mStreams.Length();
  MOZ_ASSERT(streamId == 1);
  auto& stream = *mImpl->mStreams.AppendElement();
  stream.mUri = resourceUri;
  stream.mNativeProcessStyleRequestId = aProcessStyleRequestId;
  mImpl->mDiscoveredSpecs.AppendElement(spec);

  if (PreambleModeUsesNativeStyleActivation(mImpl->mConfig.mMode)) {
    NativeStylePreloadActivationDescriptor activation;
    activation.mResourceSpec = spec;
    MOZ_TRY(mImpl->mStreams[0].mUri->GetSpec(activation.mDocumentSpec));
    mImpl->mRootOriginAttributes.CreateSuffix(
        activation.mOriginAttributesSuffix);
    nsCOMPtr<nsIURI> originalReferrer;
    MOZ_TRY(mImpl->mRootReferrerInfo->GetOriginalReferrer(
        getter_AddRefs(originalReferrer)));
    if (originalReferrer) {
      MOZ_TRY(originalReferrer->GetSpec(activation.mReferrerSpec));
    }
    activation.mReferrerPolicy =
        static_cast<uint8_t>(mImpl->mRootReferrerInfo->ReferrerPolicy());
    MOZ_TRY(
        mImpl->mRootReferrerInfo->GetSendReferrer(&activation.mSendReferrer));
    activation.mPreloadKind =
        static_cast<uint8_t>(css::StylePreloadKind::FromParser);

    RefPtr self = this;
    const uint64_t generation =
        mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
    nsresult rv = NativeStylePreloadActivation::RegisterAndDispatch(
        std::move(activation),
        [self, generation](
            const NativeStylePreloadActivationDescriptor& aActivation) {
          return self->CreateNativeParserStylesheetChannel(generation,
                                                           aActivation);
        },
        [self, generation](nsresult aStatus) {
          return self->ReleaseNativeParserStylesheetChannel(generation,
                                                            aStatus);
        },
        mImpl->mNativeStyleActivationRequestId);
    if (NS_FAILED(rv)) {
      mImpl->mDiscoveredSpecs.RemoveLastElement();
      mImpl->mStreams.RemoveLastElement();
      return rv;
    }
    return NS_OK;
  }

  nsCOMPtr<nsIChannel> channel;
  nsresult rv = NewNativeStylePreloadChannel(
      resourceUri, mImpl->mStreams[0].mUri, css::StylePreloadKind::FromParser,
      mImpl->mRootOriginAttributes, mImpl->mRootReferrerInfo, mImpl->mLoadGroup,
      mImpl->mRoute.mProxyInfo, mImpl->mProtocol, getter_AddRefs(channel));
  if (NS_FAILED(rv)) {
    mImpl->mDiscoveredSpecs.RemoveLastElement();
    mImpl->mStreams.RemoveLastElement();
    return rv;
  }
  stream.mRequest = channel;
  RefPtr<StreamListener> listener = new StreamListener(this, streamId);
  MOZ_TRY(channel->SetNotificationCallbacks(listener));
  rv = channel->AsyncOpen(listener);
  if (NS_FAILED(rv)) {
    mImpl->mDiscoveredSpecs.RemoveLastElement();
    mImpl->mStreams.RemoveLastElement();
    return rv;
  }
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=stylesheet-opened stream=1 "
      "kind=from-parser referrer=inherited protocol=%s\n",
      ProtocolName(mImpl->mProtocol));
  return NS_OK;
}

nsresult ProxyPreambleOperation::CreateNativeParserStylesheetChannel(
    uint64_t aGeneration,
    const NativeStylePreloadActivationDescriptor& aActivation) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire) !=
          aGeneration ||
      !PreambleModeUsesNativeStyleActivation(mImpl->mConfig.mMode) ||
      !mImpl->mNativeStyleActivationRequestId ||
      mImpl->mStreams.Length() != 2 || mImpl->mStreams[1].mRequest ||
      mImpl->mStreams[1].mPendingOpenListener) {
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsIURI> resourceUri;
  nsCOMPtr<nsIURI> documentUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(resourceUri), aActivation.mResourceSpec));
  MOZ_TRY(NS_NewURI(getter_AddRefs(documentUri), aActivation.mDocumentSpec));
  nsAutoCString admittedResourceSpec;
  nsAutoCString admittedDocumentSpec;
  MOZ_TRY(mImpl->mStreams[1].mUri->GetSpec(admittedResourceSpec));
  MOZ_TRY(mImpl->mStreams[0].mUri->GetSpec(admittedDocumentSpec));
  if (!aActivation.mResourceSpec.Equals(admittedResourceSpec) ||
      !aActivation.mDocumentSpec.Equals(admittedDocumentSpec) ||
      aActivation.mPreloadKind !=
          static_cast<uint8_t>(css::StylePreloadKind::FromParser)) {
    return NS_ERROR_UNEXPECTED;
  }

  OriginAttributes originAttributes;
  if (!originAttributes.PopulateFromSuffix(
          aActivation.mOriginAttributesSuffix)) {
    return NS_ERROR_UNEXPECTED;
  }
  nsAutoCString reconstructedSuffix;
  originAttributes.CreateSuffix(reconstructedSuffix);
  if (!reconstructedSuffix.Equals(aActivation.mOriginAttributesSuffix)) {
    return NS_ERROR_UNEXPECTED;
  }
  nsCOMPtr<nsIURI> referrerUri;
  if (!aActivation.mReferrerSpec.IsEmpty()) {
    MOZ_TRY(NS_NewURI(getter_AddRefs(referrerUri), aActivation.mReferrerSpec));
  }
  RefPtr<dom::ReferrerInfo> referrerInfo = new dom::ReferrerInfo(
      referrerUri,
      static_cast<dom::ReferrerPolicy>(aActivation.mReferrerPolicy),
      aActivation.mSendReferrer);

  nsCOMPtr<nsIChannel> channel;
  MOZ_TRY(NewNativeStylePreloadChannel(
      resourceUri, documentUri, css::StylePreloadKind::FromParser,
      originAttributes, referrerInfo, mImpl->mLoadGroup,
      mImpl->mRoute.mProxyInfo, mImpl->mProtocol, getter_AddRefs(channel)));
  RefPtr<StreamListener> listener = new StreamListener(this, 1);
  MOZ_TRY(channel->SetNotificationCallbacks(listener));
  mImpl->mStreams[1].mRequest = channel;
  mImpl->mStreams[1].mPendingOpenListener = listener;
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=stylesheet-channel-created "
      "stream=1 activation=ipc-rendezvous protocol=h3\n");
  return NS_OK;
}

nsresult ProxyPreambleOperation::ReleaseNativeParserStylesheetChannel(
    uint64_t aGeneration, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (NS_FAILED(aStatus)) {
    FailNativeParserContract(aStatus, "stylesheet-activation-failed");
    return aStatus;
  }
  if (mImpl->mCancelled || mImpl->mNativeParserContractFailed ||
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire) !=
          aGeneration ||
      mImpl->mStreams.Length() != 2 || !mImpl->mStreams[1].mRequest ||
      !mImpl->mStreams[1].mPendingOpenListener) {
    FailNativeParserContract(NS_ERROR_UNEXPECTED,
                             "stylesheet-activation-state-invalid");
    return NS_ERROR_UNEXPECTED;
  }
  nsCOMPtr<nsIChannel> channel = do_QueryInterface(mImpl->mStreams[1].mRequest);
  nsCOMPtr<nsIStreamListener> listener =
      mImpl->mStreams[1].mPendingOpenListener.forget();
  if (!channel || !listener) {
    FailNativeParserContract(NS_ERROR_UNEXPECTED,
                             "stylesheet-activation-channel-missing");
    return NS_ERROR_UNEXPECTED;
  }
  nsresult rv = channel->AsyncOpen(listener);
  if (NS_FAILED(rv)) {
    FailNativeParserContract(rv, "stylesheet-async-open-failed");
    return rv;
  }
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=stylesheet-opened stream=1 "
      "kind=from-parser referrer=inherited activation=ipc-rendezvous "
      "protocol=h3\n");
  return NS_OK;
}

void ProxyPreambleOperation::FailNativeParserContract(nsresult aStatus,
                                                      const char* aReason) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mNativeParserContractFailed) {
    return;
  }
  mImpl->mNativeParserContractFailed = true;
  if (mImpl->mNativeStyleActivationRequestId) {
    NativeStylePreloadActivation::Cancel(
        mImpl->mNativeStyleActivationRequestId);
    mImpl->mNativeStyleActivationRequestId = 0;
  }
  if (mImpl->mNativeRootReplacementRequestId) {
    NativeStylePreloadActivation::CancelRootReplacement(
        mImpl->mNativeRootReplacementRequestId);
    mImpl->mNativeRootReplacementRequestId = 0;
  }
  if (mImpl->mNativeProcessRootRequestId) {
    NativeStylePreloadActivation::CancelProcessRoot(
        mImpl->mNativeProcessRootRequestId,
        mImpl->mNativeParserGeneration.load(std::memory_order_acquire),
        NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED);
    mImpl->mNativeProcessRootRequestId = 0;
  }
  mImpl->mNativeRootLogicalRequest = nullptr;
  mImpl->mNativeParserRetargetArmed.store(false, std::memory_order_release);
  mImpl->mNativeParserRetargetInstalled.store(false, std::memory_order_release);
  mImpl->mAllStreamsCompletedNormally = false;
  if (NS_SUCCEEDED(mImpl->mFirstFailure)) {
    mImpl->mFirstFailure = NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED;
  }
  const uint64_t generation =
      mImpl->mNativeParserGeneration.fetch_add(1, std::memory_order_acq_rel) +
      1;
  RuntimeLogEvent(
      "Preamble native-parser-preload lifecycle=contract-failed reason=%s "
      "status=0x%08x generation=%llu protocol=%s\n",
      aReason, static_cast<unsigned>(mImpl->mFirstFailure),
      static_cast<unsigned long long>(generation),
      ProtocolName(mImpl->mProtocol));
  if (mImpl->mNativeParserTarget) {
    RefPtr self = this;
    (void)mImpl->mNativeParserTarget->Dispatch(
        NS_NewRunnableFunction(
            "NaiveFox::NativeParserFailureCleanup",
            [self] { self->mImpl->mNativeParserScanner = nullptr; }),
        NS_DISPATCH_NORMAL);
  }
  if (mImpl->mNativeParserRootSuspended) {
    nsCOMPtr<nsIRequest> root =
        mImpl->mStreams.IsEmpty() ? nullptr : mImpl->mStreams[0].mRequest;
    mImpl->mNativeParserRootSuspended = false;
    if (root) {
      // A contract failure must not leak this arm's owned suspend count. The
      // failed OnStartRequest/Cancel path remains responsible for termination.
      if (PreambleModeUsesNativeRootReplacement(mImpl->mConfig.mMode)) {
        (void)root->Cancel(mImpl->mFirstFailure);
        (void)root->Resume();
      } else {
        (void)root->Resume();
        (void)root->Cancel(mImpl->mFirstFailure);
      }
    }
  }
}

nsresult ProxyPreambleOperation::DispatchNativeParserOutputToMain(
    uint64_t aGeneration, uint32_t aSequence, bool aFinished, nsresult aStatus,
    nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors,
    nsTArray<nsHtml5LeanPreloadDescriptor>&& aLeanDescriptors) {
  RefPtr self = this;
  nsCOMPtr<nsIRunnable> completion = NS_NewRunnableFunction(
      aFinished ? "NaiveFox::NativeParserRetargetFinishComplete"
                : "NaiveFox::NativeParserRetargetFeedComplete",
      [self, aGeneration, aSequence, aFinished, aStatus,
       descriptors = std::move(aDescriptors),
       leanDescriptors = std::move(aLeanDescriptors)]() mutable {
        self->OnNativeParserOutput(aGeneration, aSequence, aFinished, aStatus,
                                   std::move(descriptors),
                                   std::move(leanDescriptors));
      });
  return NS_DispatchToMainThread(
      CreateRenderBlockingRunnable(completion.forget()));
}

nsresult ProxyPreambleOperation::OnRetargetedDataAvailable(
    uint32_t aStreamId, nsIInputStream* aInputStream, uint32_t aCount) {
  MOZ_ASSERT(PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode));
  auto recordFailure = [this](nsresult aStatus) {
    nsresult expected = NS_OK;
    (void)mImpl->mNativeParserRetargetFailure.compare_exchange_strong(
        expected, NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED,
        std::memory_order_acq_rel);
  };
  const uint64_t generation =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
  if (aStreamId != 0 || !aInputStream || !mImpl->mNativeParserTarget ||
      !mImpl->mNativeParserTarget->IsOnCurrentThread() ||
      !mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire) ||
      !mImpl->mNativeParserConsumerReady.load(std::memory_order_acquire) ||
      mImpl->mNativeParserConsumerGeneration.load(std::memory_order_acquire) !=
          generation ||
      mImpl->mNativeParserFinishQueued.load(std::memory_order_acquire) ||
      mImpl->mNativeParserRetargetDataFinished.load(
          std::memory_order_acquire) ||
      !mImpl->mNativeParserScanner) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-retarget "
        "phase=data-precondition-failed stream=%u input=%d target=%d "
        "on_target=%d installed=%d consumer=%d generation_match=%d "
        "finish_queued=%d data_finished=%d scanner=%d protocol=h3\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), aStreamId,
        aInputStream != nullptr, mImpl->mNativeParserTarget != nullptr,
        mImpl->mNativeParserTarget &&
            mImpl->mNativeParserTarget->IsOnCurrentThread(),
        mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire),
        mImpl->mNativeParserConsumerReady.load(std::memory_order_acquire),
        mImpl->mNativeParserConsumerGeneration.load(
            std::memory_order_acquire) == generation,
        mImpl->mNativeParserFinishQueued.load(std::memory_order_acquire),
        mImpl->mNativeParserRetargetDataFinished.load(
            std::memory_order_acquire),
        mImpl->mNativeParserScanner != nullptr);
    recordFailure(NS_ERROR_UNEXPECTED);
    return NS_ERROR_UNEXPECTED;
  }

  const uint32_t bodyBytes =
      mImpl->mNativeParserRetargetBodyBytes.load(std::memory_order_acquire);
  if (bodyBytes > mImpl->mConfig.mMaxBytes ||
      aCount > mImpl->mConfig.mMaxBytes - bodyBytes) {
    recordFailure(NS_ERROR_FILE_TOO_BIG);
    return NS_ERROR_FILE_TOO_BIG;
  }
  nsAutoCString body;
  if (!body.SetLength(aCount, fallible)) {
    recordFailure(NS_ERROR_OUT_OF_MEMORY);
    return NS_ERROR_OUT_OF_MEMORY;
  }
  uint32_t offset = 0;
  while (offset < aCount) {
    uint32_t read = 0;
    nsresult rv = aInputStream->Read(body.BeginWriting() + offset,
                                     aCount - offset, &read);
    if (NS_FAILED(rv) || read == 0) {
      rv = NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
      recordFailure(rv);
      return rv;
    }
    offset += read;
  }
  mImpl->mNativeParserRetargetBodyBytes.store(bodyBytes + aCount,
                                              std::memory_order_release);

  if (!mImpl->mNativeParserFirstRetargetFeedLogged.exchange(
          true, std::memory_order_acq_rel)) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-retarget "
        "phase=first-parser-feed delivery=retargeted-direct protocol=h3\n",
        static_cast<unsigned long long>(mImpl->mConnectionId));
  }

  const char* bytes = body.BeginReading();
  const bool ascii = std::all_of(bytes, bytes + body.Length(), [](char value) {
    return static_cast<unsigned char>(value) < 0x80;
  });
  nsresult parserStatus = ascii ? NS_OK : NS_ERROR_ILLEGAL_INPUT;
  nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
  if (NS_SUCCEEDED(parserStatus)) {
    parserStatus =
        mImpl->mNativeParserScanner->Feed(NS_ConvertASCIItoUTF16(body));
    if (NS_SUCCEEDED(parserStatus)) {
      mImpl->mNativeParserScanner->TakeStyleDescriptors(descriptors);
    }
  }
  const uint32_t sequence = ++mImpl->mNativeParserNextSequence;
  ++mImpl->mNativeParserPendingMainCallbacks;
  nsresult dispatchRv = DispatchNativeParserOutputToMain(
      generation, sequence, false, parserStatus, std::move(descriptors),
      nsTArray<nsHtml5LeanPreloadDescriptor>());
  if (NS_FAILED(dispatchRv)) {
    --mImpl->mNativeParserPendingMainCallbacks;
    recordFailure(dispatchRv);
    return dispatchRv;
  }
  if (NS_FAILED(parserStatus)) {
    recordFailure(parserStatus);
  }
  return parserStatus;
}

nsresult ProxyPreambleOperation::OnRetargetedDataFinished(uint32_t aStreamId,
                                                          nsresult aStatus) {
  MOZ_ASSERT(PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode));
  auto recordFailure = [this](nsresult aFailure) {
    nsresult expected = NS_OK;
    (void)mImpl->mNativeParserRetargetFailure.compare_exchange_strong(
        expected, NS_FAILED(aFailure) ? aFailure : NS_ERROR_UNEXPECTED,
        std::memory_order_acq_rel);
  };
  const uint64_t generation =
      mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
  bool expectedDataFinished = false;
  bool expectedFinishQueued = false;
  if (aStreamId != 0 || NS_FAILED(aStatus) || !mImpl->mNativeParserTarget ||
      !mImpl->mNativeParserTarget->IsOnCurrentThread() ||
      !mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire) ||
      !mImpl->mNativeParserConsumerReady.load(std::memory_order_acquire) ||
      mImpl->mNativeParserConsumerGeneration.load(std::memory_order_acquire) !=
          generation ||
      !mImpl->mNativeParserFirstRetargetFeedLogged.load(
          std::memory_order_acquire) ||
      !mImpl->mNativeParserScanner ||
      !mImpl->mNativeParserRetargetDataFinished.compare_exchange_strong(
          expectedDataFinished, true, std::memory_order_acq_rel) ||
      !mImpl->mNativeParserFinishQueued.compare_exchange_strong(
          expectedFinishQueued, true, std::memory_order_acq_rel)) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-retarget "
        "phase=finish-precondition-failed stream=%u status=0x%08x "
        "target=%d on_target=%d installed=%d consumer=%d "
        "generation_match=%d first_feed=%d scanner=%d "
        "data_finished_was=%d finish_queued_was=%d protocol=h3\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), aStreamId,
        static_cast<unsigned>(aStatus), mImpl->mNativeParserTarget != nullptr,
        mImpl->mNativeParserTarget &&
            mImpl->mNativeParserTarget->IsOnCurrentThread(),
        mImpl->mNativeParserRetargetInstalled.load(std::memory_order_acquire),
        mImpl->mNativeParserConsumerReady.load(std::memory_order_acquire),
        mImpl->mNativeParserConsumerGeneration.load(
            std::memory_order_acquire) == generation,
        mImpl->mNativeParserFirstRetargetFeedLogged.load(
            std::memory_order_acquire),
        mImpl->mNativeParserScanner != nullptr, expectedDataFinished,
        expectedFinishQueued);
    nsresult failure = NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED;
    recordFailure(failure);
    return failure;
  }

  nsresult parserStatus = mImpl->mNativeParserScanner->Finish();
  nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
  if (NS_SUCCEEDED(parserStatus)) {
    mImpl->mNativeParserScanner->TakeStyleDescriptors(descriptors);
  }
  mImpl->mNativeParserScanner = nullptr;
  RuntimeLogEvent(
      "Connection %llu preamble native-parser-retarget "
      "phase=parser-data-finished protocol=h3\n",
      static_cast<unsigned long long>(mImpl->mConnectionId));
  const uint32_t sequence = ++mImpl->mNativeParserNextSequence;
  ++mImpl->mNativeParserPendingMainCallbacks;
  nsresult dispatchRv = DispatchNativeParserOutputToMain(
      generation, sequence, true, parserStatus, std::move(descriptors),
      nsTArray<nsHtml5LeanPreloadDescriptor>());
  if (NS_FAILED(dispatchRv)) {
    --mImpl->mNativeParserPendingMainCallbacks;
    recordFailure(dispatchRv);
    return dispatchRv;
  }
  if (NS_FAILED(parserStatus)) {
    recordFailure(parserStatus);
  }
  return parserStatus;
}

nsresult ProxyPreambleOperation::OnDataAvailable(uint32_t aStreamId,
                                                 nsIInputStream* aInputStream,
                                                 uint32_t aCount) {
  if ((mImpl->mConfig.mMode ==
           PreambleMode::TreeNativeParserRootRendezvousOverlap ||
       PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode)) &&
      aStreamId == 0) {
    return QueueNativeParserRootBody(aInputStream, aCount);
  }
  if (detail::PreambleUsesRetargetedRootDelivery(mImpl->mConfig.mMode,
                                                 aStreamId)) {
    return OnRetargetedDataAvailable(aStreamId, aInputStream, aCount);
  }
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || aStreamId >= mImpl->mStreams.Length()) {
    return NS_ERROR_ABORT;
  }
  if (aCount > mImpl->mConfig.mMaxBytes - mImpl->mBodyBytes) {
    if (NS_SUCCEEDED(mImpl->mFirstFailure)) {
      mImpl->mFirstFailure = NS_ERROR_FILE_TOO_BIG;
    }
    return NS_ERROR_FILE_TOO_BIG;
  }

  nsAutoCString body;
  if (aStreamId == 0) {
    if (!body.SetLength(aCount, fallible)) {
      return NS_ERROR_OUT_OF_MEMORY;
    }
  }
  char discard[4096];
  uint32_t offset = 0;
  while (offset < aCount) {
    uint32_t read = 0;
    char* destination = aStreamId == 0 ? body.BeginWriting() + offset : discard;
    const uint32_t capacity =
        aStreamId == 0 ? aCount - offset
                       : std::min<uint32_t>(aCount - offset, sizeof(discard));
    nsresult rv = aInputStream->Read(destination, capacity, &read);
    if (NS_FAILED(rv) || read == 0) {
      return NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
    }
    offset += read;
    mImpl->mBodyBytes += read;
  }

  if (aStreamId > 0 &&
      mImpl->mConfig.mMode ==
          PreambleMode::TreeNativeParserResourceCommittedOverlap &&
      mImpl->mConfig.mMaxAssets == 6 &&
      !mImpl->mFirstResourceBodyBufferConsumed &&
      mImpl->mStreams[aStreamId].mResponseHeadersReceived &&
      mImpl->mStreams[aStreamId].mHttpStatus >= 200 &&
      mImpl->mStreams[aStreamId].mHttpStatus < 300 &&
      !mImpl->mStreams[aStreamId].mDone) {
    // This is actual response progress, not a byte threshold: the complete
    // first Necko-delivered resource buffer must be consumed successfully.
    mImpl->mFirstResourceBodyBufferConsumed = true;
    RuntimeLogEvent(
        "Preamble native-parser-resource-tree "
        "lifecycle=first-resource-body-buffer-consumed stream=%u "
        "protocol=%s\n",
        aStreamId, ProtocolName(mImpl->mProtocol));
    MaybeFireBarrier();
  }

  if (aStreamId == 0 &&
      (mImpl->mConfig.mMode == PreambleMode::DocumentFirstBufferOverlap ||
       mImpl->mConfig.mMode ==
           PreambleMode::DocumentFirstBufferTaskOverlap) &&
      !mImpl->mBarrierFired &&
      mImpl->mStreams[0].mResponseHeadersReceived &&
      mImpl->mStreams[0].mHttpStatus >= 200 &&
      mImpl->mStreams[0].mHttpStatus < 300 && !mImpl->mStreams[0].mDone) {
    // Admit only after the complete first Necko-delivered body buffer was
    // consumed successfully.  This is a channel event, not a byte threshold:
    // a short read or failed buffer never releases CONNECT.
    if (mImpl->mConfig.mMode ==
        PreambleMode::DocumentFirstBufferTaskOverlap) {
      if (!mImpl->mDocumentBarrierTaskDispatched) {
        nsresult dispatchRv = DispatchDocumentBarrierTask();
        if (NS_FAILED(dispatchRv)) {
          if (NS_SUCCEEDED(mImpl->mFirstFailure)) {
            mImpl->mFirstFailure = dispatchRv;
          }
          return dispatchRv;
        }
      }
    } else {
      FireBarrierCallback();
    }
  }

  if (aStreamId == 0 && PreambleModeUsesNativeParser(mImpl->mConfig.mMode)) {
    if (mImpl->mNativeParserContractFailed) {
      return NS_ERROR_UNEXPECTED;
    }
    if (mImpl->mConfig.mMode ==
        PreambleMode::TreeNativeParserDocumentHandoffOverlap) {
      const uint64_t generation =
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire);
      if (mImpl->mNativeParserRootSuspended ||
          !mImpl->mNativeParserConsumerReady ||
          mImpl->mNativeParserConsumerGeneration != generation) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED,
                                 "body-before-consumer-install");
        return NS_ERROR_UNEXPECTED;
      }
      if (!mImpl->mNativeParserFirstFeedLogged) {
        mImpl->mNativeParserFirstFeedLogged = true;
        // This first control arm intentionally retains the pre-existing main
        // listener copy-and-dispatch delivery. It measures only the native
        // Suspend -> replacement-installed -> Resume handoff; a later arm can
        // test nsIThreadRetargetableRequest without conflating the mechanisms.
        RuntimeLogEvent(
            "Connection %llu preamble native-parser-document-handoff "
            "phase=first-parser-feed delivery=main-copy-dispatch "
            "protocol=h3\n",
            static_cast<unsigned long long>(mImpl->mConnectionId));
      }
    }
    nsresult rv = DispatchNativeParserChunk(std::move(body));
    if (NS_FAILED(rv)) {
      FailNativeParserContract(rv, "chunk-dispatch-failed");
    }
    return rv;
  }

  if (aStreamId != 0 ||
      mImpl->mConfig.mMode == PreambleMode::DocumentComplete ||
      mImpl->mConfig.mMode == PreambleMode::DocumentCarrierDispatch ||
      mImpl->mConfig.mMode == PreambleMode::DocumentColdWinnerHandoff ||
      mImpl->mConfig.mMode == PreambleMode::DocumentNativeCacheOpen ||
      mImpl->mConfig.mMode == PreambleMode::DocumentHandshakeConfirmed ||
      mImpl->mConfig.mMode == PreambleMode::DocumentOverlap ||
      mImpl->mConfig.mMode == PreambleMode::DocumentHeadersTaskOverlap ||
      mImpl->mConfig.mMode == PreambleMode::DocumentFirstBufferOverlap ||
      mImpl->mConfig.mMode == PreambleMode::DocumentFirstBufferTaskOverlap ||
      mImpl->mConfig.mMode == PreambleMode::DocumentStartOverlap ||
      mImpl->mConfig.mMode == PreambleMode::DocumentStartTaskOverlap ||
      mImpl->mStreams.Length() - 1 >= mImpl->mConfig.mMaxAssets) {
    return NS_OK;
  }
  mImpl->mHtml.Append(body);

  while (mImpl->mStreams.Length() - 1 < mImpl->mConfig.mMaxAssets) {
    int32_t tagStart = mImpl->mHtml.FindChar('<', mImpl->mParseOffset);
    if (tagStart < 0) {
      mImpl->mParseOffset = mImpl->mHtml.Length();
      break;
    }
    int32_t tagEnd = mImpl->mHtml.FindChar('>', tagStart + 1);
    if (tagEnd < 0) {
      mImpl->mParseOffset = static_cast<uint32_t>(tagStart);
      break;
    }
    mImpl->mParseOffset = static_cast<uint32_t>(tagEnd) + 1;
    nsAutoCString tag(Substring(mImpl->mHtml, tagStart,
                                static_cast<uint32_t>(tagEnd - tagStart + 1)));
    nsAutoCString lowerTag(tag);
    LowercaseAscii(lowerTag);

    if (StartsWithClosingTagName(lowerTag, "head"_ns) ||
        StartsWithTagName(lowerTag, "body"_ns) ||
        (mImpl->mParserInHead && !KeepsParserInHead(lowerTag))) {
      mImpl->mParserInHead = false;
    }
    const bool discoveredInHead = mImpl->mParserInHead;

    nsContentPolicyType contentPolicyType = nsIContentPolicy::TYPE_OTHER;
    detail::PreambleResourceKind resourceKind =
        detail::PreambleResourceKind::Other;
    bool deferredResource = false;
    bool parserBlockingScript = false;
    nsAutoCString attributeName;
    if (StartsWithTagName(lowerTag, "link"_ns)) {
      nsAutoCString rel;
      if (!ExtractQuotedAttribute(tag, lowerTag, "rel"_ns, rel)) {
        continue;
      }
      LowercaseAscii(rel);
      bool stylesheet = false;
      bool alternate = false;
      uint32_t tokenStart = 0;
      while (tokenStart < rel.Length()) {
        while (tokenStart < rel.Length() && IsHtmlSpace(rel[tokenStart])) {
          ++tokenStart;
        }
        uint32_t tokenEnd = tokenStart;
        while (tokenEnd < rel.Length() && !IsHtmlSpace(rel[tokenEnd])) {
          ++tokenEnd;
        }
        const auto token = Substring(rel, tokenStart, tokenEnd - tokenStart);
        stylesheet |= token.EqualsLiteral("stylesheet");
        alternate |= token.EqualsLiteral("alternate");
        tokenStart = tokenEnd;
      }
      if (!stylesheet) {
        continue;
      }
      contentPolicyType = nsIContentPolicy::TYPE_STYLESHEET;
      resourceKind = detail::PreambleResourceKind::Stylesheet;
      // Without a DOM/media environment, only classify stylesheets which are
      // unambiguously non-deferred. This covers an ordinary render-blocking
      // <link rel=stylesheet> without guessing media-query state.
      deferredResource =
          !detail::PreambleStylesheetIsNonDeferred(lowerTag, alternate);
      attributeName.AssignLiteral("href");
    } else if (StartsWithTagName(lowerTag, "script"_ns)) {
      contentPolicyType = nsIContentPolicy::TYPE_SCRIPT;
      resourceKind = detail::PreambleResourceKind::Script;
      // A classic head script without async/defer follows Gecko's
      // parser-blocking ScriptLoader path. The type classifier accepts the
      // standard JavaScript MIME values but excludes modules and data blocks.
      parserBlockingScript =
          detail::PreambleScriptIsParserBlockingClassic(lowerTag);
      attributeName.AssignLiteral("src");
    } else if (StartsWithTagName(lowerTag, "img"_ns)) {
      contentPolicyType = nsIContentPolicy::TYPE_IMAGE;
      attributeName.AssignLiteral("src");
    } else {
      continue;
    }
    nsAutoCString reference;
    if (!ExtractQuotedAttribute(tag, lowerTag, attributeName, reference) ||
        reference.IsEmpty()) {
      continue;
    }
    nsCOMPtr<nsIURI> resourceUri;
    if (NS_FAILED(NS_NewURI(getter_AddRefs(resourceUri), reference, nullptr,
                            mImpl->mStreams[0].mUri))) {
      continue;
    }
    nsAutoCString prePath;
    nsAutoCString spec;
    if (NS_FAILED(resourceUri->GetPrePath(prePath)) ||
        !prePath.Equals(mImpl->mRootPrePath) ||
        NS_FAILED(resourceUri->GetSpec(spec)) ||
        mImpl->mDiscoveredSpecs.Contains(spec)) {
      continue;
    }
    mImpl->mDiscoveredSpecs.AppendElement(spec);

    uint32_t streamId = 0;
    // Resource discovery is intentionally incremental: AsyncOpen happens as
    // soon as a complete tag arrives, while the root response is still being
    // consumed, allowing Necko/Neqo to multiplex the native request streams.
    nsresult rv = [&]() -> nsresult {
      nsCOMPtr<nsIChannel> templateChannel;
      MOZ_TRY(NS_NewChannel(
          getter_AddRefs(templateChannel), resourceUri, mImpl->mPrincipal,
          nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
              nsILoadInfo::SEC_DONT_FOLLOW_REDIRECTS |
              nsILoadInfo::SEC_COOKIES_OMIT,
          contentPolicyType, nullptr, nullptr, mImpl->mLoadGroup));
      nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();
      nsCOMPtr<nsIProxiedProtocolHandler> protocolHandler =
          do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "https");
      if (!protocolHandler) {
        return NS_ERROR_FAILURE;
      }
      nsCOMPtr<nsIChannel> channel;
      MOZ_TRY(protocolHandler->NewProxiedChannel(
          resourceUri, mImpl->mRoute.mProxyInfo, 0, nullptr, loadInfo,
          getter_AddRefs(channel)));
      nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
      nsCOMPtr<nsIHttpChannel> httpChannel = do_QueryInterface(channel);
      if (!internal || !httpChannel) {
        return NS_ERROR_FAILURE;
      }
      MOZ_TRY(httpChannel->SetRequestMethod("GET"_ns));
      MOZ_TRY(internal->SetAllowSpdy(true));
      MOZ_TRY(internal->SetAllowHttp3(mImpl->mProtocol == ProxyProtocol::H3));
      MOZ_TRY(internal->SetBlockAuthPrompt(true));
      MOZ_TRY(internal->SetProxyPreamble());
      MOZ_TRY(internal->SetDocumentURI(mImpl->mStreams[0].mUri));
      if (!mImpl->mRootReferrerInfo) {
        return NS_ERROR_NOT_INITIALIZED;
      }
      MOZ_TRY(httpChannel->SetReferrerInfo(mImpl->mRootReferrerInfo));
      MOZ_TRY(channel->SetLoadGroup(mImpl->mLoadGroup));
      uint32_t loadFlags =
          nsIRequest::LOAD_ANONYMOUS | nsIChannel::LOAD_BYPASS_SERVICE_WORKER;
      if (!detail::PreambleChannelUsesCache(mImpl->mConfig, mImpl->mProtocol,
                                            true)) {
        loadFlags |= nsIRequest::INHIBIT_CACHING;
      }
      MOZ_TRY(channel->SetLoadFlags(loadFlags));
      if (mImpl->mConfig.mMode ==
          PreambleMode::TreeResourceNativeCacheCommittedOverlap) {
        MOZ_TRY(internal->SetProxyPreambleUseNativeResourceCacheOpen());
      }
      if (detail::PreambleResourceNeedsLeader(resourceKind, deferredResource,
                                              parserBlockingScript,
                                              discoveredInHead)) {
        nsCOMPtr<nsIClassOfService> cos = do_QueryInterface(channel);
        if (cos) {
          // Match the native CSS/ScriptLoader cause. nsHttpChannel derives the
          // Priority header; do not synthesize that wire output here.
          cos->AddClassFlags(nsIClassOfService::Leader);
        }
      }
      streamId = mImpl->mStreams.Length();
      auto& stream = *mImpl->mStreams.AppendElement();
      stream.mUri = resourceUri;
      stream.mRequest = channel;
      RefPtr<StreamListener> listener = new StreamListener(this, streamId);
      if (mImpl->mConfig.mMode == PreambleMode::TreeResourceCommittedOverlap ||
          mImpl->mConfig.mMode ==
              PreambleMode::TreeResourceNativeCacheCommittedOverlap) {
        MOZ_TRY(channel->SetNotificationCallbacks(listener));
      }
      nsresult openRv = channel->AsyncOpen(listener);
      if (NS_FAILED(openRv)) {
        mImpl->mStreams.RemoveLastElement();
      }
      return openRv;
    }();
    // A browser navigation survives a failed subresource. The attempted
    // stream still participates in the complete/overlap barrier when opened,
    // but failure to open an asset must not turn a successful document into a
    // failed preamble.
    (void)rv;
  }
  return NS_OK;
}

void ProxyPreambleOperation::OnStopRequest(uint32_t aStreamId,
                                           nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || aStreamId >= mImpl->mStreams.Length()) {
    return;
  }
  if (aStreamId == 0 &&
      (mImpl->mConfig.mMode == PreambleMode::DocumentHeadersTaskOverlap ||
       mImpl->mConfig.mMode ==
           PreambleMode::DocumentFirstBufferTaskOverlap ||
       mImpl->mConfig.mMode == PreambleMode::DocumentStartTaskOverlap) &&
      mImpl->mDocumentBarrierTaskDispatched && !mImpl->mBarrierFired &&
      !mImpl->mDocumentRootStopDeferred) {
    // Some channel implementations deliver the terminal callback
    // synchronously after the successful response event that queued the
    // barrier. Keep the channel itself running, but defer terminal bookkeeping
    // until that task. This preserves the boundary without imposing
    // Suspend/Resume backpressure on the root response.
    mImpl->mDocumentRootStopDeferred = true;
    mImpl->mDocumentRootStopStatus = aStatus;
    return;
  }
  if (aStreamId == 1 && mImpl->mNativeStyleActivationRequestId) {
    NativeStylePreloadActivation::CompleteStyle(
        mImpl->mNativeStyleActivationRequestId, aStatus);
    mImpl->mNativeStyleActivationRequestId = 0;
  }
  if (aStreamId == 1 &&
      mImpl->mStreams[aStreamId].mNativeProcessStyleRequestId) {
    const uint64_t styleRequestId =
        mImpl->mStreams[aStreamId].mNativeProcessStyleRequestId;
    mImpl->mStreams[aStreamId].mNativeProcessStyleRequestId = 0;
    nsresult completeRv = NativeStylePreloadActivation::CompleteProcessStyle(
        styleRequestId, aStatus);
    if (NS_FAILED(completeRv)) {
      FailNativeParserContract(completeRv, "process-style-complete-failed");
    } else {
      const char* processMode =
          mImpl->mConfig.mMode ==
                  PreambleMode::TreeNativeParserFullProcessOverlap
              ? "native-parser-full-process"
              : "native-parser-process";
      RuntimeLogEvent(
          "Connection %llu preamble %s "
          "phase=style-onstop-complete style=%llu status=0x%08x "
          "parent_pid=%llu protocol=h3\n",
          static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
          static_cast<unsigned long long>(styleRequestId),
          static_cast<unsigned>(aStatus),
          static_cast<unsigned long long>(base::GetCurrentProcId()));
    }
  }
  if (aStreamId == 0 && mImpl->mNativeProcessRootRequestId) {
    if (NS_FAILED(aStatus)) {
      FailNativeParserContract(aStatus, "process-root-stop-failed");
    } else {
      const uint32_t sequence = mImpl->mNativeProcessNextSequence;
      nsresult forwardRv = NativeStylePreloadActivation::ForwardProcessRootStop(
          mImpl->mNativeProcessRootRequestId,
          mImpl->mNativeParserGeneration.load(std::memory_order_acquire),
          sequence, aStatus);
      if (NS_FAILED(forwardRv)) {
        FailNativeParserContract(forwardRv, "process-root-stop-forward-failed");
      } else {
        ++mImpl->mNativeProcessNextSequence;
        const char* processMode =
            mImpl->mConfig.mMode ==
                    PreambleMode::TreeNativeParserFullProcessOverlap
                ? "native-parser-full-process"
                : "native-parser-process";
        RuntimeLogEvent(
            "Connection %llu preamble %s phase=root-stop "
            "request=%llu generation=%llu sequence=%u status=0x%08x "
            "parent_pid=%llu protocol=h3\n",
            static_cast<unsigned long long>(mImpl->mConnectionId), processMode,
            static_cast<unsigned long long>(mImpl->mNativeProcessRootRequestId),
            static_cast<unsigned long long>(
                mImpl->mNativeParserGeneration.load(std::memory_order_acquire)),
            sequence, static_cast<unsigned>(aStatus),
            static_cast<unsigned long long>(base::GetCurrentProcId()));
      }
    }
  }
  if (aStreamId == 0 && mImpl->mNativeRootReplacementRequestId) {
    if (mImpl->mConfig.mMode ==
        PreambleMode::TreeNativeParserRootRendezvousOverlap) {
      nsresult forwardRv =
          NativeStylePreloadActivation::ForwardRootReplacementStop(
              mImpl->mNativeRootReplacementRequestId, aStatus);
      if (NS_FAILED(forwardRv)) {
        FailNativeParserContract(forwardRv, "root-stop-forward-failed");
      }
    } else {
      NativeStylePreloadActivation::CompleteRootReplacement(
          mImpl->mNativeRootReplacementRequestId, aStatus);
      mImpl->mNativeRootReplacementRequestId = 0;
    }
  }
  auto& stream = mImpl->mStreams[aStreamId];
  stream.mRequest = nullptr;
  stream.mDone = true;
  const bool expectedNavigationStopStyleAbort =
      aStreamId == 1 &&
      detail::PreambleNavigationStopExpectedStyleAbort(
          mImpl->mConfig.mMode, mImpl->mBarrierFired,
          mImpl->mNavigationStopStyleResponseStarted,
          mImpl->mConnectHandoffAdmitted, mImpl->mTunnelApplicationActive,
          mImpl->mTunnelServerApplicationActive, mImpl->mNavigationStopIssued,
          aStatus);
  if (expectedNavigationStopStyleAbort) {
    mImpl->mNavigationStopStyleAborted = true;
    RuntimeLogEvent(
        "Connection %llu preamble "
        "%s "
        "phase=stylesheet-onstop status=NS_BINDING_ABORTED expected=1 "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        mImpl->mConfig.mMode ==
                PreambleMode::TreeNativeParserDocumentStartResponseStop
            ? "native-parser-document-start-response-stop"
            : "native-parser-document-start-navigation-stop",
        ProtocolName(mImpl->mProtocol));
  } else if (NS_FAILED(aStatus)) {
    mImpl->mAllStreamsCompletedNormally = false;
  }
  if (aStreamId > 0) {
    if (!expectedNavigationStopStyleAbort) {
      const bool resourceSucceeded =
          detail::PreambleResourceCompletedSuccessfully(
              stream.mResponseHeadersReceived, stream.mHttpStatus, aStatus);
      if (resourceSucceeded) {
        ++mImpl->mCompletedSuccessfulResources;
      } else {
        mImpl->mAllStreamsCompletedNormally = false;
      }
    }
  }
  if (aStreamId == 0 && NS_FAILED(aStatus) &&
      NS_SUCCEEDED(mImpl->mFirstFailure)) {
    mImpl->mFirstFailure = aStatus;
  }
  if (aStreamId == 0) {
    if (PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode) &&
        mImpl->mConfig.mMode !=
            PreambleMode::TreeNativeParserRootRendezvousOverlap) {
      mImpl->mBodyBytes =
          mImpl->mNativeParserRetargetBodyBytes.load(std::memory_order_acquire);
      const nsresult retargetFailure =
          mImpl->mNativeParserRetargetFailure.load(std::memory_order_acquire);
      if (NS_FAILED(retargetFailure)) {
        FailNativeParserContract(retargetFailure, "retarget-parser-failed");
      } else if (!mImpl->mNativeParserRetargetDataFinished.load(
                     std::memory_order_acquire)) {
        nsresult parserRv = DispatchNativeParserFinish();
        if (NS_FAILED(parserRv)) {
          FailNativeParserContract(parserRv, "finish-dispatch-failed");
        }
      } else if (!mImpl->mNativeParserFinishQueued.load(
                     std::memory_order_acquire)) {
        FailNativeParserContract(NS_ERROR_UNEXPECTED, "missing-parser-finish");
      }
    }
    mImpl->mRootDone = true;
    mImpl->mRootCompletedSuccessfully =
        detail::PreambleResourceCompletedSuccessfully(
            stream.mResponseHeadersReceived, stream.mHttpStatus, aStatus);
    if (!mImpl->mRootCompletedSuccessfully) {
      mImpl->mAllStreamsCompletedNormally = false;
    }
    if (PreambleModeUsesNativeParser(mImpl->mConfig.mMode) &&
        !PreambleModeUsesNativeParserProcess(mImpl->mConfig.mMode) &&
        !PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode) &&
        !mImpl->mNativeParserContractFailed) {
      nsresult parserRv = DispatchNativeParserFinish();
      if (NS_FAILED(parserRv)) {
        FailNativeParserContract(parserRv, "finish-dispatch-failed");
      }
    }
    nsresult stopRv = MaybeIssueNativeParserNavigationStop();
    if (NS_FAILED(stopRv)) {
      FailNativeParserContract(stopRv, "navigation-stop-cancel-failed");
    }
  }

  MaybeFireBarrier();
  MaybeFinish();
}

void ProxyPreambleOperation::FireBarrierCallback(bool aTerminalFallback) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mBarrierFired || !mImpl->mBarrierCallback) {
    return;
  }
  mImpl->mBarrierFired = true;
  auto callback = std::move(mImpl->mBarrierCallback);
  const uint32_t rootStatus =
      mImpl->mStreams.IsEmpty() ? 0 : mImpl->mStreams[0].mHttpStatus;
  const uint32_t startedResources =
      mImpl->mStreams.IsEmpty() ? 0 : mImpl->mStreams.Length() - 1;
  uint32_t committedResources = 0;
  uint32_t nativeCacheNewResources = 0;
  for (uint32_t index = 1; index < mImpl->mStreams.Length(); ++index) {
    committedResources += mImpl->mStreams[index].mRequestCommitted;
    nativeCacheNewResources += mImpl->mStreams[index].mNativeCacheNewEntry;
  }
  callback({mImpl->mFirstFailure, rootStatus, mImpl->mBodyBytes,
            startedResources, committedResources, nativeCacheNewResources,
            mImpl->mRootDone, aTerminalFallback});
}

void ProxyPreambleOperation::MaybeFireBarrier() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mBarrierFired) {
    return;
  }
  const uint32_t assetCount =
      mImpl->mStreams.IsEmpty() ? 0 : mImpl->mStreams.Length() - 1;
  const bool rootResponseAccepted =
      !mImpl->mStreams.IsEmpty() &&
      mImpl->mStreams[0].mResponseHeadersReceived &&
      mImpl->mStreams[0].mHttpStatus >= 200 &&
      mImpl->mStreams[0].mHttpStatus < 300 && !mImpl->mStreams[0].mDone;
  uint32_t assetsWithHeadersNotDone = 0;
  uint32_t assetsWithHeadersOrDone = 0;
  uint32_t assetsDone = 0;
  uint32_t assetsCommitted = 0;
  uint32_t nativeCacheNewResources = 0;
  for (uint32_t index = 1; index < mImpl->mStreams.Length(); ++index) {
    const auto& candidate = mImpl->mStreams[index];
    assetsWithHeadersNotDone +=
        candidate.mResponseHeadersReceived && !candidate.mDone;
    assetsWithHeadersOrDone += candidate.mHeadersReceived || candidate.mDone;
    assetsDone += candidate.mDone;
    assetsCommitted += candidate.mRequestCommitted;
    nativeCacheNewResources += candidate.mNativeCacheNewEntry;
  }
  const bool barrierReached = detail::PreambleBarrierReached(
      mImpl->mConfig.mMode, rootResponseAccepted, mImpl->mRootDone, assetCount,
      assetsWithHeadersNotDone, assetsWithHeadersOrDone, assetsDone,
      assetsCommitted, mImpl->mRootCompletedSuccessfully,
      nativeCacheNewResources, mImpl->mNativeParserFinished);
  if (barrierReached) {
    if (mImpl->mConfig.mMode ==
            PreambleMode::TreeNativeParserResourceCommittedOverlap &&
        mImpl->mConfig.mMaxAssets == 6) {
      if (!mImpl->mFirstResourceBodyBufferConsumed) {
        return;
      }
      RuntimeLogEvent(
          "Preamble native-parser-resource-tree "
          "barrier=first-resource-body-buffer assets=6 committed=6 "
          "protocol=%s\n",
          ProtocolName(mImpl->mProtocol));
      FireBarrierCallback();
      return;
    }
    if (mImpl->mConfig.mMode ==
            PreambleMode::TreeResourceCommittedOverlap &&
        mImpl->mConfig.mMaxAssets == 6) {
      if (!mImpl->mDocumentBarrierTaskDispatched) {
        nsresult dispatchRv = DispatchDocumentBarrierTask();
        if (NS_FAILED(dispatchRv) && NS_SUCCEEDED(mImpl->mFirstFailure)) {
          mImpl->mFirstFailure = dispatchRv;
        }
      }
      return;
    }
    FireBarrierCallback();
  }
}

void ProxyPreambleOperation::MaybeFinish() {
  MOZ_ASSERT(NS_IsMainThread());
  bool allDone = !mImpl->mStreams.IsEmpty();
  for (const auto& candidate : mImpl->mStreams) {
    allDone &= candidate.mDone;
  }
  if (PreambleModeUsesNativeParser(mImpl->mConfig.mMode) &&
      (!mImpl->mNativeParserFinished ||
       mImpl->mNativeParserPendingMainCallbacks != 0)) {
    return;
  }
  if (allDone && !mImpl->mBarrierFired &&
      (mImpl->mConfig.mMode ==
           PreambleMode::TreeResourceNativeCacheCommittedOverlap ||
       PreambleModeUsesLightweightNativeParser(mImpl->mConfig.mMode) ||
       mImpl->mConfig.mMode ==
           PreambleMode::TreeNativeParserDocumentHandoffOverlap ||
       PreambleModeUsesRetargetedNativeParser(mImpl->mConfig.mMode))) {
    // This mode has a strict causal admission contract. Keep the operation
    // alive for the ordinary outer preamble timeout rather than converting a
    // completed-but-invalid resource into a CONNECT barrier or dropping the
    // operation while the sequence is still in flight.
    return;
  }
  if (allDone && !mImpl->mFinishedFired) {
    mImpl->mFinishedFired = true;
    // No future stream transition can satisfy an overlap admission fallback
    // once every stream is done. Continue CONNECT immediately instead of
    // waiting for the outer preamble timeout.
    if (detail::PreambleNeedsCompletionFallback(mImpl->mConfig.mMode,
                                                mImpl->mBarrierFired)) {
      FireBarrierCallback(true);
    }
    auto callback = std::move(mImpl->mFinishedCallback);
    if (callback) {
      const uint32_t rootStatus =
          mImpl->mStreams.IsEmpty() ? 0 : mImpl->mStreams[0].mHttpStatus;
      callback({mImpl->mFirstFailure, rootStatus, mImpl->mBodyBytes,
                mImpl->mCompletedSuccessfulResources,
                [&]() {
                  uint32_t count = 0;
                  for (uint32_t index = 1; index < mImpl->mStreams.Length();
                       ++index) {
                    count += mImpl->mStreams[index].mNativeCacheNewEntry;
                  }
                  return count;
                }(),
                mImpl->mRootDone, mImpl->mAllStreamsCompletedNormally,
                mImpl->mStreams.Length() == 2 &&
                    mImpl->mStreams[1].mRequestCommitted,
                mImpl->mNavigationStopStyleResponseStarted,
                mImpl->mNavigationStopStyleAborted});
    }
  }
}

nsresult OpenProxyPreambleOperation(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const PreambleConfig& aConfig,
    ProxyProtocol aProtocol, ProxyPreambleCallback&& aBarrierCallback,
    ProxyPreambleFinishedCallback&& aFinishedCallback,
    const Maybe<HostResolverRule>& aHostResolverRule, uint64_t aConnectionId,
    RefPtr<ProxyPreambleOperation>& aOperation) {
  RefPtr operation = new ProxyPreambleOperation();
  MOZ_TRY(operation->Start(aProxyUrl, aProxyUser, aProxyPassword, aConfig,
                           aProtocol, std::move(aBarrierCallback),
                           std::move(aFinishedCallback), aHostResolverRule,
                           aConnectionId));
  aOperation = std::move(operation);
  return NS_OK;
}

nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization) {
  return MakeBasicAuthorization(aUser, aPassword, aAuthorization);
}

nsresult CreateNoConnectChannel(
    const nsACString& aProxyUrl, const nsACString& aPath,
    ProxyProtocol aProtocol, const Maybe<HostResolverRule>& aHostResolverRule,
    nsIChannel** aChannel) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsValidPreamblePath(aPath)) {
    return NS_ERROR_INVALID_ARG;
  }
  ExplicitProxyRoute route;
  MOZ_TRY(BuildExplicitProxyRoute(aProxyUrl, EmptyCString(), EmptyCString(),
                                  aProtocol, aHostResolverRule, false, route));
  nsAutoCString spec;
  MOZ_TRY(route.mProxyUri->GetPrePath(spec));
  spec.Append(aPath);
  nsCOMPtr<nsIURI> uri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(uri), spec));
  nsCOMPtr<nsIPrincipal> principal;
  MOZ_TRY(GetSystemPrincipal(getter_AddRefs(principal)));
  nsCOMPtr<nsIChannel> templateChannel;
  MOZ_TRY(NS_NewChannel(
      getter_AddRefs(templateChannel), uri, principal,
      nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
          nsILoadInfo::SEC_DONT_FOLLOW_REDIRECTS |
          nsILoadInfo::SEC_COOKIES_OMIT,
      nsIContentPolicy::TYPE_OTHER));
  nsCOMPtr<nsIProxiedProtocolHandler> handler =
      do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "https");
  if (!handler) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIChannel> channel;
  nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();
  MOZ_TRY(handler->NewProxiedChannel(uri, route.mProxyInfo, 0, nullptr, loadInfo,
                                    getter_AddRefs(channel)));
  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  if (!internal) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(internal->SetAllowSpdy(true));
  MOZ_TRY(internal->SetAllowHttp3(aProtocol == ProxyProtocol::H3));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  // The existing route-only hook sends ordinary origin requests over the
  // explicit strict H2/H3 route, without a proxy CONNECT or proxy credentials.
  MOZ_TRY(internal->SetProxyPreamble());
  MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                nsIRequest::LOAD_ANONYMOUS |
                                nsIChannel::LOAD_BYPASS_SERVICE_WORKER));
  channel.forget(aChannel);
  return NS_OK;
}

nsresult OpenProxyPreamble(const nsACString& aProxyUrl,
                           const nsACString& aProxyUser,
                           const nsACString& aProxyPassword,
                           const nsACString& aPath, uint32_t aMaxBytes,
                           ProxyProtocol aProtocol,
                           ProxyPreambleCallback&& aCallback,
                           const Maybe<HostResolverRule>& aHostResolverRule,
                           nsIRequest** aOpenedRequest) {
  if (!aCallback || !IsValidPreamblePath(aPath) || aMaxBytes == 0 ||
      aMaxBytes > PreambleConfig::kMaximumBytes) {
    return NS_ERROR_INVALID_ARG;
  }

  ExplicitProxyRoute route;
  MOZ_TRY(BuildExplicitProxyRoute(aProxyUrl, aProxyUser, aProxyPassword,
                                  aProtocol, aHostResolverRule, false, route));

  nsAutoCString preambleUrl;
  MOZ_TRY(route.mProxyUri->GetPrePath(preambleUrl));
  preambleUrl.Append(aPath);
  nsCOMPtr<nsIURI> preambleUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(preambleUri), preambleUrl));

  nsCOMPtr<nsIPrincipal> principal;
  MOZ_TRY(GetSystemPrincipal(getter_AddRefs(principal)));
  nsCOMPtr<nsIChannel> templateChannel;
  MOZ_TRY(
      NS_NewChannel(getter_AddRefs(templateChannel), preambleUri, principal,
                    nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
                        nsILoadInfo::SEC_DONT_FOLLOW_REDIRECTS |
                        nsILoadInfo::SEC_COOKIES_OMIT,
                    nsIContentPolicy::TYPE_OTHER));
  nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();

  nsCOMPtr<nsIProxiedProtocolHandler> protocolHandler =
      do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "https");
  if (!protocolHandler) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIChannel> channel;
  MOZ_TRY(protocolHandler->NewProxiedChannel(preambleUri, route.mProxyInfo, 0,
                                             nullptr, loadInfo,
                                             getter_AddRefs(channel)));

  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  nsCOMPtr<nsIHttpChannel> httpChannel = do_QueryInterface(channel);
  if (!internal || !httpChannel) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(httpChannel->SetRequestMethod("GET"_ns));
  MOZ_TRY(internal->SetAllowSpdy(true));
  MOZ_TRY(internal->SetAllowHttp3(aProtocol == ProxyProtocol::H3));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  MOZ_TRY(internal->SetProxyPreamble());
  // Raw CONNECT upgrades are anonymous at the connection-info layer. Match
  // that flag so the preamble's wildcard H2/H3 pool key is identical; proxy
  // credentials remain absent and nsHttpChannel suppresses auth-cache reuse
  // explicitly for NS_HTTP_PROXY_PREAMBLE.
  MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                nsIRequest::LOAD_ANONYMOUS |
                                nsIChannel::LOAD_BYPASS_SERVICE_WORKER));

  RefPtr<ProxyPreambleListener> listener =
      new ProxyPreambleListener(aMaxBytes, std::move(aCallback));
  MOZ_TRY(channel->AsyncOpen(listener));
  if (aOpenedRequest) {
    nsCOMPtr<nsIRequest> request = channel;
    request.forget(aOpenedRequest);
  }
  return NS_OK;
}

nsresult OpenNeckoTunnel(
    const nsACString& aProxyUrl, const nsACString& aTargetAuthority,
    const nsACString& aProxyUser, const nsACString& aProxyPassword,
    nsIHttpUpgradeListener* aUpgradeListener,
    nsIStreamListener* aChannelListener, const nsACString& aConnectPadding,
    ProxyProtocol aProtocol, const Maybe<HostResolverRule>& aHostResolverRule,
    const nsTArray<ExtraHeader>& aExtraHeaders, bool aConnectUrgentStart,
    bool aUseAnonymousConnection, nsIRequest** aOpenedRequest) {
  if (!aUpgradeListener || !aChannelListener) {
    return NS_ERROR_INVALID_ARG;
  }
  ExplicitProxyRoute route;
  MOZ_TRY(BuildExplicitProxyRoute(aProxyUrl, aProxyUser, aProxyPassword,
                                  aProtocol, aHostResolverRule, true, route));

  nsAutoCString targetUrl("http://"_ns);
  targetUrl.Append(aTargetAuthority);
  targetUrl.Append('/');
  nsCOMPtr<nsIURI> targetUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(targetUri), targetUrl));

  nsAutoCString targetScheme;
  nsAutoCString targetHost;
  nsAutoCString targetUserPass;
  int32_t targetPort = -1;
  MOZ_TRY(targetUri->GetScheme(targetScheme));
  MOZ_TRY(targetUri->GetAsciiHost(targetHost));
  MOZ_TRY(targetUri->GetUserPass(targetUserPass));
  MOZ_TRY(targetUri->GetPort(&targetPort));
  if (targetPort == -1) {
    // The synthetic carrier is HTTP, so Gecko canonicalizes an explicit
    // :80 to the default-port sentinel. CONNECT still targets TCP port 80.
    targetPort = 80;
  }
  if (!targetScheme.EqualsLiteral("http") || targetHost.IsEmpty() ||
      !targetUserPass.IsEmpty() || targetPort <= 0 ||
      targetPort > std::numeric_limits<uint16_t>::max()) {
    return NS_ERROR_INVALID_ARG;
  }

  nsCOMPtr<nsIPrincipal> principal;
  MOZ_TRY(GetSystemPrincipal(getter_AddRefs(principal)));
  nsCOMPtr<nsIChannel> templateChannel;
  MOZ_TRY(NS_NewChannel(getter_AddRefs(templateChannel), targetUri, principal,
                        nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL,
                        nsIContentPolicy::TYPE_OTHER));
  nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();

  nsCOMPtr<nsIProxiedProtocolHandler> protocolHandler =
      do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "http");
  if (!protocolHandler) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIChannel> channel;
  MOZ_TRY(protocolHandler->NewProxiedChannel(targetUri, route.mProxyInfo, 0,
                                             nullptr, loadInfo,
                                             getter_AddRefs(channel)));

  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  if (!internal) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIHttpChannel> httpChannel = do_QueryInterface(channel);
  if (!httpChannel) {
    return NS_ERROR_FAILURE;
  }
  // The HTTP URI is only a carrier for the CONNECT authority. Applying HSTS
  // to it replaces the explicitly proxied raw channel with an origin HTTPS
  // channel for preloaded hosts such as github.com.
  MOZ_TRY(httpChannel->SetAllowSTS(false));
  MOZ_TRY(internal->SetAllowSpdy(true));
  MOZ_TRY(internal->SetAllowHttp3(aProtocol == ProxyProtocol::H3));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  MOZ_TRY(internal->SetConnectOnly(false));
  if (!aUseAnonymousConnection) {
    // SetConnectOnly supplies the upstream CONNECT caps and proxy resolve
    // policy, but its generic helper also forces an anonymous pool identity.
    // A browser-origin root/resource lifecycle is non-anonymous. Preserve the
    // CONNECT mechanics while placing this tunnel in that same ordinary pool.
    MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                  nsIRequest::LOAD_BYPASS_CACHE |
                                  nsIChannel::LOAD_BYPASS_SERVICE_WORKER));
  }
  for (const auto& header : aExtraHeaders) {
    MOZ_TRY(internal->SetProxyConnectHeader(header.mName, header.mValue));
  }
  if (!aConnectPadding.IsEmpty()) {
    MOZ_TRY(internal->SetProxyConnectHeader("padding"_ns, aConnectPadding));
  }

  if (aConnectUrgentStart) {
    nsCOMPtr<nsIClassOfService> cos = do_QueryInterface(channel);
    if (!cos) {
      return NS_ERROR_NO_INTERFACE;
    }
    MOZ_TRY(cos->AddClassFlags(nsIClassOfService::UrgentStart));
    if (StaticPrefs::dom_document_priority_incremental()) {
      MOZ_TRY(cos->SetIncremental(true));
    }
  }

  MOZ_TRY(internal->HTTPUpgrade(EmptyCString(), aUpgradeListener));
  MOZ_TRY(channel->AsyncOpen(aChannelListener));
  if (aOpenedRequest) {
    nsCOMPtr<nsIRequest> request = channel;
    request.forget(aOpenedRequest);
  }
  return NS_OK;
}

nsresult RunRawTunnelSmoke(const nsACString& aProxyUrl,
                           const nsACString& aTargetAuthority,
                           const nsACString& aProxyUser,
                           const nsACString& aProxyPassword,
                           ProxyProtocol aProtocol) {
  nsAutoCString targetUrl("http://"_ns);
  targetUrl.Append(aTargetAuthority);
  targetUrl.Append('/');
  nsCOMPtr<nsIURI> targetUri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(targetUri), targetUrl));

  nsAutoCString request("GET /delay?ms=250 HTTP/1.1\r\nHost: "_ns);
  // Preserve an explicitly requested default port. nsIURI::GetHostPort()
  // omits :80 for this synthetic HTTP carrier, while proxy CONNECT still
  // requires the caller's exact host:port authority.
  request.Append(aTargetAuthority);
  request.AppendLiteral("\r\nConnection: close\r\n\r\n");
  ProxyProtocol actualProtocol =
      aProtocol == ProxyProtocol::Auto ? ProxyProtocol::H3 : aProtocol;
  bool fallbackUsed = false;

  while (true) {
    RefPtr<TunnelSmoke> listener = new TunnelSmoke(request, actualProtocol);
    nsCOMPtr<nsIRequest> openedRequest;
    MOZ_TRY(OpenNeckoTunnel(aProxyUrl, aTargetAuthority, aProxyUser,
                            aProxyPassword, listener, listener, EmptyCString(),
                            actualProtocol, {}, {}, false, true,
                            getter_AddRefs(openedRequest)));

    nsCOMPtr<nsITimer> establishmentTimer;
    bool establishmentTimedOut = false;
    if (aProtocol == ProxyProtocol::Auto &&
        actualProtocol == ProxyProtocol::H3) {
      auto timer = NS_NewTimerWithCallback(
          [request = openedRequest, &establishmentTimedOut](nsITimer*) {
            establishmentTimedOut = true;
            (void)request->Cancel(NS_ERROR_NET_TIMEOUT);
          },
          5000, nsITimer::TYPE_ONE_SHOT,
          "NaiveFox::RawAutoH3EstablishmentTimeout"_ns);
      if (timer.isErr()) {
        return timer.unwrapErr();
      }
      establishmentTimer = timer.unwrap();
    }

    if (!SpinEventLoopUntil("NaiveFox::RunRawTunnelSmoke"_ns,
                            [&listener]() { return listener->Complete(); })) {
      return NS_ERROR_FAILURE;
    }
    if (establishmentTimer) {
      (void)establishmentTimer->Cancel();
    }

    nsresult result;
    bool connectCodeKnown = false;
    int32_t connectCode = -1;
    bool transportAvailable = false;
    nsAutoCString outerProtocol;
    listener->Snapshot(result, connectCodeKnown, connectCode,
                       transportAvailable, outerProtocol);

    const AutoFallbackState fallbackState{
        aProtocol,
        actualProtocol,
        fallbackUsed,
        false,
        true,
        NS_FAILED(result),
        establishmentTimedOut,
        connectCodeKnown,
        connectCode,
        transportAvailable,
    };
    if (ShouldRetryH2FromH3(fallbackState)) {
      fallbackUsed = true;
      actualProtocol = ProxyProtocol::H2;
      continue;
    }

    std::printf("Proxy CONNECT status: %d\n", connectCode);
    std::printf("Outer protocol: %s\n", outerProtocol.get());
    const auto expectedProtocol =
        actualProtocol == ProxyProtocol::H3 ? "h3"_ns : "h2"_ns;
    if (!outerProtocol.Equals(expectedProtocol)) {
      return NS_ERROR_FAILURE;
    }
    if (NS_SUCCEEDED(result)) {
      std::printf("Raw tunnel response marker verified\n");
    }
    return result;
  }
}

}  // namespace mozilla::naivefox
