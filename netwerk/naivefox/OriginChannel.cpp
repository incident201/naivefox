/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "OriginChannel.h"

#include <limits>

#include "mozilla/Base64.h"
#include "mozilla/ResultExtensions.h"
#include "mozilla/TextUtils.h"
#include "nsIChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
#include "nsIProtocolProxyService.h"
#include "nsIProxiedProtocolHandler.h"
#include "nsIScriptSecurityManager.h"
#include "nsIURI.h"
#include "nsNetCID.h"
#include "nsNetUtil.h"
#include "nsProxyInfo.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {
namespace {
struct ExplicitProxyRoute {
  nsCOMPtr<nsIURI> mProxyUri;
  nsCOMPtr<nsIProxyInfo> mProxyInfo;
};
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

bool IsValidOriginPath(const nsACString& aPath) {
  if (aPath.IsEmpty() || aPath.First() != '/' ||
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
        authorization, "naivefox"_ns, proxyFlags, UINT32_MAX, nullptr,
        getter_AddRefs(proxyInfo)));
  } else {
    MOZ_TRY(proxyService->NewProxyInfo(
        "https"_ns, proxyHost, proxyPort, authorization, "naivefox"_ns,
        proxyFlags, UINT32_MAX, nullptr, getter_AddRefs(proxyInfo)));
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
nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization) {
  return MakeBasicAuthorization(aUser, aPassword, aAuthorization);
}

nsresult CreateOriginChannel(const nsACString& aProxyUrl,
                             const nsACString& aPath, ProxyProtocol aProtocol,
                             const Maybe<HostResolverRule>& aHostResolverRule,
                             nsIChannel** aChannel,
                             nsContentPolicyType aContentPolicyType) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsValidOriginPath(aPath)) {
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
  MOZ_TRY(
      NS_NewChannel(getter_AddRefs(templateChannel), uri, principal,
                    nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
                        nsILoadInfo::SEC_DONT_FOLLOW_REDIRECTS |
                        nsILoadInfo::SEC_COOKIES_OMIT,
                    aContentPolicyType));
  nsCOMPtr<nsIProxiedProtocolHandler> handler =
      do_GetService(NS_NETWORK_PROTOCOL_CONTRACTID_PREFIX "https");
  if (!handler) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIChannel> channel;
  nsCOMPtr<nsILoadInfo> loadInfo = templateChannel->LoadInfo();
  MOZ_TRY(handler->NewProxiedChannel(uri, route.mProxyInfo, 0, nullptr,
                                     loadInfo, getter_AddRefs(channel)));
  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  if (!internal) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(internal->SetAllowSpdy(true));
  MOZ_TRY(internal->SetAllowHttp3(aProtocol == ProxyProtocol::H3));
  MOZ_TRY(internal->SetBlockAuthPrompt(true));
  // The existing route-only hook sends ordinary origin requests over the
  // explicit strict H2/H3 route, without a proxy CONNECT or proxy credentials.
  MOZ_TRY(internal->SetNaiveFoxOriginRoute());
  MOZ_TRY(channel->SetLoadFlags(nsIRequest::INHIBIT_CACHING |
                                nsIRequest::LOAD_ANONYMOUS |
                                nsIChannel::LOAD_BYPASS_SERVICE_WORKER));
  channel.forget(aChannel);
  return NS_OK;
}
}  // namespace mozilla::naivefox
