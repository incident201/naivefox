/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NeckoTunnel.h"

#include <algorithm>
#include <cstdio>
#include <limits>

#include "mozilla/Base64.h"
#include "mozilla/ErrorNames.h"
#include "mozilla/Mutex.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "nsCOMPtr.h"
#include "nsError.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIChannel.h"
#include "nsIContentPolicy.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsIInputStream.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
#include "nsIProtocolHandler.h"
#include "nsIProtocolProxyService.h"
#include "nsIProxiedChannel.h"
#include "nsIProxiedProtocolHandler.h"
#include "nsIProxyInfo.h"
#include "nsIRequest.h"
#include "nsIScriptSecurityManager.h"
#include "nsISocketTransport.h"
#include "nsIStreamListener.h"
#include "nsITLSSocketControl.h"
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

  explicit TunnelSmoke(const nsACString& aRequest)
      : mMutex("NaiveFox::TunnelSmoke"), mRequest(aRequest) {}

  bool Complete() {
    MutexAutoLock lock(mMutex);
    return mComplete;
  }

  void Snapshot(nsresult& aResult, int32_t& aConnectCode, nsACString& aAlpn) {
    MutexAutoLock lock(mMutex);
    aResult = mResult;
    aConnectCode = mConnectCode;
    aAlpn = mAlpn;
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
  nsCString mAlpn MOZ_GUARDED_BY(mMutex);

  nsCOMPtr<nsIAsyncInputStream> mSocketIn;
  nsCOMPtr<nsIAsyncOutputStream> mSocketOut;
  nsCString mRequest;
  uint32_t mWriteOffset = 0;
  nsCString mResponse;
};

NS_IMPL_ISUPPORTS(TunnelSmoke, nsIHttpUpgradeListener, nsIStreamListener,
                  nsIRequestObserver, nsIInputStreamCallback,
                  nsIOutputStreamCallback)

NS_IMETHODIMP TunnelSmoke::OnStartRequest(nsIRequest* aRequest) {
  nsCOMPtr<nsIProxiedChannel> proxied = do_QueryInterface(aRequest);
  if (!proxied) {
    Finish(NS_ERROR_UNEXPECTED);
    return NS_ERROR_UNEXPECTED;
  }

  int32_t connectCode = -1;
  nsresult rv = proxied->GetHttpProxyConnectResponseCode(&connectCode);
  if (NS_FAILED(rv)) {
    Finish(rv);
    return rv;
  }

  {
    MutexAutoLock lock(mMutex);
    mConnectCode = connectCode;
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
  }
  return NS_OK;
}

NS_IMETHODIMP TunnelSmoke::OnTransportAvailable(
    nsISocketTransport* aTransport, nsIAsyncInputStream* aSocketIn,
    nsIAsyncOutputStream* aSocketOut) {
  MOZ_TRY(aSocketIn->AsyncWait(nullptr, 0, 0, nullptr));
  MOZ_TRY(aSocketOut->AsyncWait(nullptr, 0, 0, nullptr));

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
  {
    MutexAutoLock lock(mMutex);
    mAlpn = alpn;
  }

  mSocketIn = aSocketIn;
  mSocketOut = aSocketOut;

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
  Finish(aErrorCode);
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
  return NS_OK;
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
  if (aUser.IsEmpty() || aPassword.IsEmpty()) {
    return NS_ERROR_INVALID_ARG;
  }

  nsAutoCString userPass(aUser);
  userPass.Append(':');
  userPass.Append(aPassword);
  aAuthorization.AssignLiteral("Basic ");
  return Base64EncodeAppend(userPass, aAuthorization);
}

}  // namespace

nsresult OpenNeckoTunnel(const nsACString& aProxyUrl,
                         const nsACString& aTargetAuthority,
                         const nsACString& aProxyUser,
                         const nsACString& aProxyPassword,
                         nsIHttpUpgradeListener* aUpgradeListener,
                         nsIStreamListener* aChannelListener,
                         const nsACString& aConnectPadding,
                         ProxyProtocol aProtocol) {
  if (!aUpgradeListener || !aChannelListener) {
    return NS_ERROR_INVALID_ARG;
  }
  if (aProtocol != ProxyProtocol::H2) {
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
  if (!targetScheme.EqualsLiteral("http") || targetHost.IsEmpty() ||
      !targetUserPass.IsEmpty() || targetPort <= 0 ||
      targetPort > std::numeric_limits<uint16_t>::max()) {
    return NS_ERROR_INVALID_ARG;
  }

  nsAutoCString authorization;
  MOZ_TRY(MakeBasicAuthorization(aProxyUser, aProxyPassword, authorization));

  nsCOMPtr<nsIProtocolProxyService> proxyService =
      do_GetService(NS_PROTOCOLPROXYSERVICE_CONTRACTID);
  if (!proxyService) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIProxyInfo> proxyInfo;
  MOZ_TRY(proxyService->NewProxyInfo(
      "https"_ns, proxyHost, proxyPort, authorization, "naivefox-raw-tunnel"_ns,
      nsIProxyInfo::TRANSPARENT_PROXY_RESOLVES_HOST |
          nsIProxyInfo::ALWAYS_TUNNEL_VIA_PROXY,
      UINT32_MAX, nullptr, getter_AddRefs(proxyInfo)));
  nsCOMPtr<net::nsProxyInfo> concreteProxy = do_QueryInterface(proxyInfo);
  if (!concreteProxy) {
    return NS_ERROR_FAILURE;
  }
  proxyInfo = concreteProxy->CloneProxyInfoWithNewResolveFlags(
      nsIProtocolProxyService::RESOLVE_PREFER_HTTPS_PROXY |
      nsIProtocolProxyService::RESOLVE_ALWAYS_TUNNEL);

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
  MOZ_TRY(protocolHandler->NewProxiedChannel(
      targetUri, proxyInfo, 0, nullptr, loadInfo, getter_AddRefs(channel)));

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
  MOZ_TRY(internal->SetAllowHttp3(false));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  MOZ_TRY(internal->SetConnectOnly(false));
  if (!aConnectPadding.IsEmpty()) {
    MOZ_TRY(internal->SetProxyConnectHeader("padding"_ns, aConnectPadding));
  }

  MOZ_TRY(internal->HTTPUpgrade(EmptyCString(), aUpgradeListener));
  return channel->AsyncOpen(aChannelListener);
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
  nsAutoCString targetHostPort;
  MOZ_TRY(targetUri->GetHostPort(targetHostPort));

  nsAutoCString request("GET /small HTTP/1.1\r\nHost: "_ns);
  request.Append(targetHostPort);
  request.AppendLiteral("\r\nConnection: close\r\n\r\n");
  RefPtr<TunnelSmoke> listener = new TunnelSmoke(request);
  MOZ_TRY(OpenNeckoTunnel(aProxyUrl, aTargetAuthority, aProxyUser,
                          aProxyPassword, listener, listener, EmptyCString(),
                          aProtocol));

  if (!SpinEventLoopUntil("NaiveFox::RunRawTunnelSmoke"_ns,
                          [&listener]() { return listener->Complete(); })) {
    return NS_ERROR_FAILURE;
  }

  nsresult result;
  int32_t connectCode;
  nsAutoCString alpn;
  listener->Snapshot(result, connectCode, alpn);
  std::printf("Proxy CONNECT status: %d\n", connectCode);
  std::printf("Outer ALPN: %s\n", alpn.get());
  if (NS_SUCCEEDED(result)) {
    std::printf("Raw tunnel response marker verified\n");
  }
  return result;
}

}  // namespace mozilla::naivefox
