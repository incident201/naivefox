/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NeckoTunnel.h"

#include <algorithm>
#include <cstdio>
#include <limits>
#include <utility>

#include "AutoFallback.h"
#include "ReferrerInfo.h"
#include "mozilla/Base64.h"
#include "mozilla/ErrorNames.h"
#include "mozilla/Mutex.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "mozilla/StaticPrefs_dom.h"
#include "mozilla/TextUtils.h"
#include "nsCOMPtr.h"
#include "nsError.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIChannel.h"
#include "nsIClassOfService.h"
#include "nsIContentPolicy.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsIInputStream.h"
#include "nsILoadGroup.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
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
    if (value <= 0x20 || value >= 0x7f || value == '?' || value == '#' ||
        value == '\\') {
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

class ProxyPreambleOperation::Impl final {
 public:
  struct Stream final {
    nsCOMPtr<nsIURI> mUri;
    nsCOMPtr<nsIRequest> mRequest;
    uint32_t mHttpStatus = 0;
    bool mHeadersReceived = false;
    bool mResponseHeadersReceived = false;
    bool mDone = false;
  };

  ExplicitProxyRoute mRoute;
  nsCOMPtr<nsIPrincipal> mPrincipal;
  nsCOMPtr<nsILoadGroup> mLoadGroup;
  PreambleConfig mConfig;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
  ProxyPreambleCallback mBarrierCallback;
  std::function<void(bool, uint32_t)> mFinishedCallback;
  nsTArray<Stream> mStreams;
  nsTArray<nsCString> mDiscoveredSpecs;
  nsCOMPtr<nsIReferrerInfo> mRootReferrerInfo;
  nsCString mRootPrePath;
  nsCString mHtml;
  uint32_t mParseOffset = 0;
  uint32_t mBodyBytes = 0;
  nsresult mFirstFailure = NS_OK;
  bool mParserInHead = true;
  bool mRootDone = false;
  bool mBarrierFired = false;
  bool mFinishedFired = false;
  bool mCancelled = false;
  bool mAllStreamsCompletedNormally = true;
  uint32_t mCompletedSuccessfulResources = 0;
};

class ProxyPreambleOperation::StreamListener final : public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  StreamListener(ProxyPreambleOperation* aOwner, uint32_t aStreamId)
      : mOwner(aOwner), mStreamId(aStreamId) {}

 private:
  ~StreamListener() = default;

  RefPtr<ProxyPreambleOperation> mOwner;
  const uint32_t mStreamId;
};

NS_IMPL_ISUPPORTS(ProxyPreambleOperation::StreamListener, nsIStreamListener,
                  nsIRequestObserver)

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnStartRequest(
    nsIRequest* aRequest) {
  return mOwner->OnStartRequest(mStreamId, aRequest);
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnDataAvailable(
    nsIRequest* aRequest, nsIInputStream* aInputStream, uint64_t aOffset,
    uint32_t aCount) {
  return mOwner->OnDataAvailable(mStreamId, aInputStream, aCount);
}

NS_IMETHODIMP ProxyPreambleOperation::StreamListener::OnStopRequest(
    nsIRequest* aRequest, nsresult aStatus) {
  mOwner->OnStopRequest(mStreamId, aStatus);
  mOwner = nullptr;
  return NS_OK;
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

void ProxyPreambleOperation::Cancel(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelled || mImpl->mFinishedFired) {
    return;
  }
  mImpl->mCancelled = true;
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
    std::function<void(bool, uint32_t)>&& aFinishedCallback,
    const Maybe<HostResolverRule>& aHostResolverRule) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!aBarrierCallback || aConfig.mMode == PreambleMode::Off ||
      !IsValidPreamblePath(aConfig.mPath) || aConfig.mMaxBytes == 0 ||
      aConfig.mMaxBytes > PreambleConfig::kMaximumBytes ||
      aConfig.mMaxAssets > PreambleConfig::kMaximumAssets) {
    return NS_ERROR_INVALID_ARG;
  }
  mImpl->mConfig = aConfig;
  mImpl->mProtocol = aProtocol;
  mImpl->mBarrierCallback = std::move(aBarrierCallback);
  mImpl->mFinishedCallback = std::move(aFinishedCallback);
  MOZ_TRY(BuildExplicitProxyRoute(aProxyUrl, aProxyUser, aProxyPassword,
                                  aProtocol, aHostResolverRule, false,
                                  mImpl->mRoute));
  MOZ_TRY(GetSystemPrincipal(getter_AddRefs(mImpl->mPrincipal)));
  MOZ_TRY(
      NS_NewLoadGroup(getter_AddRefs(mImpl->mLoadGroup), mImpl->mPrincipal));

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
    MOZ_TRY(internal->SetDocumentURI(aUri));
    MOZ_TRY(channel->SetLoadGroup(mImpl->mLoadGroup));
    MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                  nsIRequest::LOAD_ANONYMOUS |
                                  nsIChannel::LOAD_BYPASS_SERVICE_WORKER));
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
  nsresult rv = channel->GetResponseStatus(&stream.mHttpStatus);
  stream.mResponseHeadersReceived = NS_SUCCEEDED(rv);
  if (aStreamId == 0 && NS_FAILED(rv) && NS_SUCCEEDED(mImpl->mFirstFailure)) {
    mImpl->mFirstFailure = rv;
  }
  if (aStreamId == 0) {
    nsAutoCString policyHeader;
    mozilla::dom::ReferrerPolicy policy = mozilla::dom::ReferrerPolicy::_empty;
    if (NS_SUCCEEDED(
            channel->GetResponseHeader("referrer-policy"_ns, policyHeader))) {
      policy = mozilla::dom::ReferrerInfo::ReferrerPolicyFromHeaderString(
          NS_ConvertUTF8toUTF16(policyHeader));
    }
    mImpl->mRootReferrerInfo =
        new mozilla::dom::ReferrerInfo(stream.mUri, policy, true);
  }
  MaybeFireBarrier();
  return rv;
}

nsresult ProxyPreambleOperation::OnDataAvailable(uint32_t aStreamId,
                                                 nsIInputStream* aInputStream,
                                                 uint32_t aCount) {
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

  if (aStreamId != 0 ||
      mImpl->mConfig.mMode == PreambleMode::DocumentComplete ||
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
      MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                    nsIRequest::LOAD_ANONYMOUS |
                                    nsIChannel::LOAD_BYPASS_SERVICE_WORKER));
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
  auto& stream = mImpl->mStreams[aStreamId];
  stream.mRequest = nullptr;
  stream.mDone = true;
  if (NS_FAILED(aStatus)) {
    mImpl->mAllStreamsCompletedNormally = false;
  }
  if (aStreamId > 0) {
    const bool resourceSucceeded =
        detail::PreambleResourceCompletedSuccessfully(
            stream.mResponseHeadersReceived, stream.mHttpStatus, aStatus);
    if (resourceSucceeded) {
      ++mImpl->mCompletedSuccessfulResources;
    } else {
      mImpl->mAllStreamsCompletedNormally = false;
    }
  }
  if (aStreamId == 0 && NS_FAILED(aStatus) &&
      NS_SUCCEEDED(mImpl->mFirstFailure)) {
    mImpl->mFirstFailure = aStatus;
  }
  if (aStreamId == 0) {
    mImpl->mRootDone = true;
  }

  MaybeFireBarrier();
  MaybeFinish();
}

void ProxyPreambleOperation::FireBarrierCallback() {
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
  callback({mImpl->mFirstFailure, rootStatus, mImpl->mBodyBytes,
            startedResources, mImpl->mRootDone});
}

void ProxyPreambleOperation::MaybeFireBarrier() {
  MOZ_ASSERT(NS_IsMainThread());
  const uint32_t assetCount =
      mImpl->mStreams.IsEmpty() ? 0 : mImpl->mStreams.Length() - 1;
  uint32_t assetsWithHeadersNotDone = 0;
  uint32_t assetsWithHeadersOrDone = 0;
  uint32_t assetsDone = 0;
  for (uint32_t index = 1; index < mImpl->mStreams.Length(); ++index) {
    const auto& candidate = mImpl->mStreams[index];
    assetsWithHeadersNotDone +=
        candidate.mResponseHeadersReceived && !candidate.mDone;
    assetsWithHeadersOrDone += candidate.mHeadersReceived || candidate.mDone;
    assetsDone += candidate.mDone;
  }
  const bool barrierReached = detail::PreambleBarrierReached(
      mImpl->mConfig.mMode, mImpl->mRootDone, assetCount,
      assetsWithHeadersNotDone, assetsWithHeadersOrDone, assetsDone);
  if (barrierReached) {
    FireBarrierCallback();
  }
}

void ProxyPreambleOperation::MaybeFinish() {
  MOZ_ASSERT(NS_IsMainThread());
  bool allDone = !mImpl->mStreams.IsEmpty();
  for (const auto& candidate : mImpl->mStreams) {
    allDone &= candidate.mDone;
  }
  if (allDone && !mImpl->mFinishedFired) {
    mImpl->mFinishedFired = true;
    // No future stream transition can satisfy an overlap admission fallback
    // once every stream is done. Continue CONNECT immediately instead of
    // waiting for the outer preamble timeout.
    if (detail::PreambleNeedsCompletionFallback(mImpl->mConfig.mMode,
                                                mImpl->mBarrierFired)) {
      FireBarrierCallback();
    }
    auto callback = std::move(mImpl->mFinishedCallback);
    if (callback) {
      callback(mImpl->mAllStreamsCompletedNormally,
               mImpl->mCompletedSuccessfulResources);
    }
  }
}

nsresult OpenProxyPreambleOperation(
    const nsACString& aProxyUrl, const nsACString& aProxyUser,
    const nsACString& aProxyPassword, const PreambleConfig& aConfig,
    ProxyProtocol aProtocol, ProxyPreambleCallback&& aBarrierCallback,
    std::function<void(bool, uint32_t)>&& aFinishedCallback,
    const Maybe<HostResolverRule>& aHostResolverRule,
    RefPtr<ProxyPreambleOperation>& aOperation) {
  RefPtr operation = new ProxyPreambleOperation();
  MOZ_TRY(operation->Start(aProxyUrl, aProxyUser, aProxyPassword, aConfig,
                           aProtocol, std::move(aBarrierCallback),
                           std::move(aFinishedCallback), aHostResolverRule));
  aOperation = std::move(operation);
  return NS_OK;
}

nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization) {
  return MakeBasicAuthorization(aUser, aPassword, aAuthorization);
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
    nsIRequest** aOpenedRequest) {
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
                            actualProtocol, {}, {}, false,
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
