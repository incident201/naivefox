/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NativeStylePreloadChannel.h"

#include "ProxyProtocol.h"
#include "RuntimeLogging.h"
#include "StylePreloadKind.h"
#include "mozilla/NaiveFoxURIPrincipal.h"
#include "mozilla/dom/RequestBinding.h"
#include "mozilla/net/CookieJarSettings.h"
#include "nsCOMPtr.h"
#include "nsIChannel.h"
#include "nsIClassOfService.h"
#include "nsIContentPolicy.h"
#include "nsICookieJarSettings.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
#include "nsIProxiedProtocolHandler.h"
#include "nsIProxyInfo.h"
#include "nsIReferrerInfo.h"
#include "nsIRequest.h"
#include "nsITimedChannel.h"
#include "nsIURI.h"
#include "nsNetUtil.h"
#include "nsServiceManagerUtils.h"

namespace mozilla::naivefox {

nsContentPolicyType StyleContentPolicyType(
    css::StylePreloadKind aPreloadKind) {
  return aPreloadKind == css::StylePreloadKind::None
             ? nsIContentPolicy::TYPE_INTERNAL_STYLESHEET
             : nsIContentPolicy::TYPE_INTERNAL_STYLESHEET_PRELOAD;
}

nsresult NewNativeStylePreloadChannel(
    nsIURI* aResourceURI, nsIURI* aDocumentURI,
    css::StylePreloadKind aPreloadKind,
    const OriginAttributes& aOriginAttributes,
    nsIReferrerInfo* aResolvedReferrerInfo, nsILoadGroup* aLoadGroup,
    nsIProxyInfo* aProxyInfo, ProxyProtocol aProtocol,
    nsIChannel** aResult) {
  NS_ENSURE_ARG_POINTER(aResourceURI);
  NS_ENSURE_ARG_POINTER(aDocumentURI);
  NS_ENSURE_ARG_POINTER(aResolvedReferrerInfo);
  NS_ENSURE_ARG_POINTER(aResult);
  *aResult = nullptr;

  nsCOMPtr<nsIPrincipal> documentPrincipal =
      NaiveFoxURIPrincipal::Create(aDocumentURI, aOriginAttributes);
  if (!documentPrincipal || documentPrincipal->IsSystemPrincipal()) {
    RuntimeLogEvent(
        "Native stylesheet channel contract-failed reason=document-principal\n");
    return NS_ERROR_UNEXPECTED;
  }

  // This is the channel contract used by css::Loader for CORS_NONE.  Do not
  // add SEC_COOKIES_OMIT or SEC_DONT_FOLLOW_REDIRECTS: an ordinary parser
  // stylesheet inherits its document security context and follows the native
  // cookie/redirect path.
  constexpr nsSecurityFlags securityFlags =
      nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_INHERITS_SEC_CONTEXT |
      nsILoadInfo::SEC_ALLOW_CHROME;

  nsCOMPtr<nsICookieJarSettings> cookieJarSettings =
      net::CookieJarSettings::Create(documentPrincipal);
  if (!cookieJarSettings) {
    return NS_ERROR_FAILURE;
  }

  nsCOMPtr<nsIChannel> templateChannel;
  const nsContentPolicyType contentPolicyType =
      StyleContentPolicyType(aPreloadKind);
  MOZ_TRY(NS_NewChannel(
      getter_AddRefs(templateChannel), aResourceURI, documentPrincipal,
      securityFlags, contentPolicyType,
      cookieJarSettings,
      /* aPerformanceStorage = */ nullptr, aLoadGroup));

  nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();
  if (!loadInfo ||
      loadInfo->InternalContentPolicyType() != contentPolicyType ||
      loadInfo->GetExternalContentPolicyType() !=
          ExtContentPolicy::TYPE_STYLESHEET) {
    RuntimeLogEvent(
        "Native stylesheet channel contract-failed reason=content-policy\n");
    return NS_ERROR_UNEXPECTED;
  }
  MOZ_TRY(loadInfo->SetCookieJarSettings(cookieJarSettings));

  nsCOMPtr<nsIChannel> channel;
  if (aProxyInfo) {
    nsCOMPtr<nsIProxiedProtocolHandler> protocolHandler =
        do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "https");
    if (!protocolHandler) {
      return NS_ERROR_FAILURE;
    }
    MOZ_TRY(protocolHandler->NewProxiedChannel(
        aResourceURI, aProxyInfo, 0, nullptr, loadInfo,
        getter_AddRefs(channel)));
  } else {
    channel = templateChannel;
  }

  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  nsCOMPtr<nsIHttpChannel> httpChannel = do_QueryInterface(channel);
  if (!internal || !httpChannel) {
    return NS_ERROR_FAILURE;
  }

  MOZ_TRY(httpChannel->SetRequestMethod("GET"_ns));
  MOZ_TRY(internal->SetAllowSpdy(true));
  MOZ_TRY(internal->SetAllowHttp3(aProtocol == ProxyProtocol::H3));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  if (aProxyInfo) {
    MOZ_TRY(internal->SetProxyPreamble());
  }
  MOZ_TRY(internal->SetDocumentURI(aDocumentURI));
  MOZ_TRY(httpChannel->SetReferrerInfo(aResolvedReferrerInfo));
  MOZ_TRY(channel->SetLoadGroup(aLoadGroup));

  // Leave LOAD_ANONYMOUS, LOAD_BYPASS_SERVICE_WORKER, and INHIBIT_CACHING
  // unset.  With NS_HTTP_PROXY_PREAMBLE this naturally selects the ordinary
  // writable Cache2 lifecycle in nsHttpChannel::ConnectOnTailUnblock.
  nsLoadFlags loadFlags = 0;
  MOZ_TRY(channel->GetLoadFlags(&loadFlags));
  constexpr nsLoadFlags forbiddenLoadFlags =
      nsIRequest::LOAD_ANONYMOUS | nsIChannel::LOAD_BYPASS_SERVICE_WORKER |
      nsIRequest::INHIBIT_CACHING;
  if (loadFlags & forbiddenLoadFlags) {
    RuntimeLogEvent(
        "Native stylesheet channel contract-failed reason=load-flags flags=%u\n",
        static_cast<unsigned>(loadFlags));
    return NS_ERROR_UNEXPECTED;
  }

  nsCOMPtr<nsIClassOfService> cos = do_QueryInterface(channel);
  if (!cos) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(cos->AddClassFlags(nsIClassOfService::Leader));
  cos->SetFetchPriorityDOM(dom::FetchPriority::Auto);

  nsCOMPtr<nsITimedChannel> timedChannel = do_QueryInterface(channel);
  if (!timedChannel) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(timedChannel->SetInitiatorType(u"link"_ns));

  channel.forget(aResult);
  return NS_OK;
}

}  // namespace mozilla::naivefox
