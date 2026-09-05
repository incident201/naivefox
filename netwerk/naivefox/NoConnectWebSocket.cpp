/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NoConnectWebSocket.h"

#include "NeckoTunnel.h"
#include "TunnelSession.h"
#include "mozilla/OriginAttributes.h"
#include "mozilla/Try.h"
#include "mozilla/net/WebSocketChannel.h"
#include "nsContentUtils.h"
#include "nsHttpConnectionInfo.h"
#include "nsIChannel.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsILoadInfo.h"
#include "nsIURI.h"
#include "nsIURIMutator.h"
#include "nsNetUtil.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

NS_IMPL_ISUPPORTS(NoConnectWebSocket, nsIWebSocketListener)

NoConnectWebSocket::NoConnectWebSocket(
    std::function<void()> aStarted,
    std::function<void(const nsACString&)> aMessage,
    std::function<void(uint32_t)> aAcknowledged,
    std::function<void(nsresult)> aStopped)
    : mStarted(std::move(aStarted)),
      mMessage(std::move(aMessage)),
      mAcknowledged(std::move(aAcknowledged)),
      mStopped(std::move(aStopped)) {}

NoConnectWebSocket::~NoConnectWebSocket() = default;

nsresult NoConnectWebSocket::Start(const TunnelConfig& aConfig,
                                   const nsACString& aCookie,
                                   const nsACString& aPath,
                                   const nsACString& aProtocol) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mChannel || mClosing || aCookie.IsEmpty() ||
      !(aProtocol.EqualsLiteral("nfc1.hybrid.v1") ||
        aProtocol.EqualsLiteral("nfc1.hybrid.a1"))) {
    return NS_ERROR_INVALID_ARG;
  }
  nsCOMPtr<nsIChannel> templateChannel;
  MOZ_TRY(CreateNoConnectChannel(aConfig.mProxyUrl, aPath, ProxyProtocol::H2,
                                 aConfig.mHostResolverRule,
                                 getter_AddRefs(templateChannel)));
  nsCOMPtr<nsIURI> uri;
  MOZ_TRY(templateChannel->GetURI(getter_AddRefs(uri)));
  nsCOMPtr<nsIChannel> channel;
  MOZ_TRY(NS_NewChannel(
      getter_AddRefs(channel), uri, nsContentUtils::GetSystemPrincipal(),
      nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
          nsILoadInfo::SEC_DONT_FOLLOW_REDIRECTS |
          nsILoadInfo::SEC_COOKIES_OMIT,
      nsIContentPolicy::TYPE_WEBSOCKET));
  MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                nsIRequest::LOAD_ANONYMOUS |
                                nsIChannel::LOAD_BYPASS_SERVICE_WORKER));
  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(channel);
  if (!internal || !http) {
    return NS_ERROR_NO_INTERFACE;
  }
  MOZ_TRY(internal->SetAllowSpdy(false));
  MOZ_TRY(internal->SetAllowHttp3(false));
  MOZ_TRY(internal->SetAllowAltSvc(false));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  MOZ_TRY(internal->SetBypassProxy(true));
  nsAutoCString host;
  MOZ_TRY(uri->GetAsciiHost(host));
  int32_t port = -1;
  MOZ_TRY(uri->GetPort(&port));
  if (port == -1) {
    port = 443;
  }
  nsAutoCString routedHost(host);
  if (aConfig.mHostResolverRule &&
      aConfig.mHostResolverRule->mLogicalHost.Equals(
          host, nsCaseInsensitiveCStringComparator)) {
    routedHost = aConfig.mHostResolverRule->mPhysicalHost;
  }
  RefPtr<net::nsHttpConnectionInfo> connection = new net::nsHttpConnectionInfo(
      host, port, "http/1.1"_ns, EmptyCString(), nullptr, OriginAttributes(),
      routedHost, port, false);
  connection->SetNoSpdy(true);
  connection->SetHttp3Disabled(true);
  connection->SetAnonymous(true);
  internal->SetConnectionInfo(connection);
  MOZ_TRY(http->SetRequestHeader("Cookie"_ns, aCookie, false));
  nsAutoCString origin;
  MOZ_TRY(uri->GetPrePath(origin));
  nsCOMPtr<nsIURI> websocketUri;
  MOZ_TRY(NS_MutateURI(uri).SetScheme("wss"_ns).Finalize(websocketUri));
  RefPtr<net::WebSocketChannel> websocket = new net::WebSocketSSLChannel();
  MOZ_TRY(websocket->InitNativeChannel(channel));
  MOZ_TRY(websocket->SetProtocol(aProtocol));
  MOZ_TRY(websocket->SetPingInterval(0));
  mProtocol = aProtocol;
  MOZ_TRY(websocket->AsyncOpenNative(websocketUri, origin, OriginAttributes(),
                                     0, this, nullptr));
  mChannel = std::move(websocket);
  return NS_OK;
}

nsresult NoConnectWebSocket::Send(const nsACString& aMessage) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mOpen || mClosing || !mChannel) {
    return NS_ERROR_NOT_CONNECTED;
  }
  if (aMessage.Length() < 512 || aMessage.Length() > 256 * 1024) {
    return NS_ERROR_ILLEGAL_VALUE;
  }
  return mChannel->SendBinaryMsg(aMessage);
}

void NoConnectWebSocket::Close(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mClosing) {
    return;
  }
  mClosing = true;
  mOpen = false;
  mCloseStatus = aStatus;
  RefPtr<net::WebSocketChannel> channel = mChannel;
  if (!channel) {
    return;
  }
  if (NS_FAILED(aStatus)) {
    channel->CancelNative(aStatus);
  } else {
    (void)channel->Close(nsIWebSocketChannel::CLOSE_NORMAL, EmptyCString());
  }
}

NS_IMETHODIMP NoConnectWebSocket::OnStart(nsISupports*) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mChannel || mClosing) {
    return NS_OK;
  }
  nsAutoCString protocol;
  nsAutoCString extensions;
  nsresult rv = mChannel->GetProtocol(protocol);
  if (NS_SUCCEEDED(rv)) {
    rv = mChannel->GetExtensions(extensions);
  }
  if (NS_FAILED(rv) || !protocol.Equals(mProtocol) || !extensions.IsEmpty()) {
    Close(NS_ERROR_ILLEGAL_VALUE);
    return NS_OK;
  }
  mOpen = true;
  RefPtr<NoConnectWebSocket> self = this;
  auto callback = mStarted;
  if (callback) {
    callback();
  }
  return NS_OK;
}

NS_IMETHODIMP NoConnectWebSocket::OnStop(nsISupports*, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  RefPtr<NoConnectWebSocket> self = this;
  mOpen = false;
  mClosing = true;
  mChannel = nullptr;
  auto callback = std::move(mStopped);
  mStarted = nullptr;
  mMessage = nullptr;
  mAcknowledged = nullptr;
  if (callback) {
    callback(NS_FAILED(mCloseStatus) ? mCloseStatus : aStatus);
  }
  return NS_OK;
}

NS_IMETHODIMP NoConnectWebSocket::OnMessageAvailable(nsISupports*,
                                                     const nsACString&) {
  Close(NS_ERROR_ILLEGAL_VALUE);
  return NS_OK;
}

NS_IMETHODIMP NoConnectWebSocket::OnBinaryMessageAvailable(
    nsISupports*, const nsACString& aMessage) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mOpen || mClosing) {
    return NS_OK;
  }
  if (aMessage.IsEmpty() || aMessage.Length() > 256 * 1024) {
    Close(NS_ERROR_ILLEGAL_VALUE);
    return NS_OK;
  }
  RefPtr<NoConnectWebSocket> self = this;
  auto callback = mMessage;
  if (callback) {
    callback(aMessage);
  }
  return NS_OK;
}

NS_IMETHODIMP NoConnectWebSocket::OnAcknowledge(nsISupports*, uint32_t aSize) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mOpen || mClosing) {
    return NS_OK;
  }
  // Necko also acknowledges whole PING/PONG payloads (at most 125 bytes).
  // NFC1 writes are at least 512 bytes and have a separate queue budget.
  if (aSize <= 125) {
    return NS_OK;
  }
  RefPtr<NoConnectWebSocket> self = this;
  auto callback = mAcknowledged;
  if (callback) {
    callback(aSize);
  }
  return NS_OK;
}

NS_IMETHODIMP NoConnectWebSocket::OnServerClose(nsISupports*, uint16_t aCode,
                                                const nsACString&) {
  Close(aCode == nsIWebSocketChannel::CLOSE_NORMAL ? NS_OK : NS_ERROR_ABORT);
  return NS_OK;
}

NS_IMETHODIMP NoConnectWebSocket::OnError() { return NS_OK; }

void ShutdownNoConnectWebSockets() {
  MOZ_ASSERT(NS_IsMainThread());
  net::WebSocketChannel::Shutdown();
}

}  // namespace mozilla::naivefox
