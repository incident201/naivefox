/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NeckoTunnel.h"
#include "NativeStylePreloadChannel.h"
#include "ProxyProtocol.h"
#include "ReferrerInfo.h"
#include "SecFetch.h"
#include "StylePreloadKind.h"
#include "gtest/gtest.h"
#include "mozilla/BasePrincipal.h"
#include "mozilla/OriginAttributes.h"
#include "nsContentUtils.h"
#include "nsIChannel.h"
#include "nsIClassOfService.h"
#include "nsIContentPolicy.h"
#include "nsICookieJarSettings.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
#include "nsIReferrerInfo.h"
#include "nsIRequest.h"
#include "nsITimedChannel.h"
#include "nsIURI.h"
#include "nsNetUtil.h"

namespace mozilla::dom {

TEST(NaiveFoxPreambleSemantics, SystemOwnedResourceUsesDocumentOrigin)
{
  nsCOMPtr<nsIURI> documentURI;
  nsCOMPtr<nsIURI> resourceURI;
  ASSERT_EQ(NS_NewURI(getter_AddRefs(documentURI),
                      "https://proxy.example/camouflage/"_ns),
            NS_OK);
  ASSERT_EQ(NS_NewURI(getter_AddRefs(resourceURI),
                      "https://proxy.example/camouflage/app.js"_ns),
            NS_OK);

  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(nsIContentPolicy::TYPE_SCRIPT,
                                          documentURI, resourceURI)
                  .EqualsLiteral("same-origin"));
  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(nsIContentPolicy::TYPE_STYLESHEET,
                                          documentURI, resourceURI)
                  .EqualsLiteral("same-origin"));
  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(nsIContentPolicy::TYPE_IMAGE,
                                          documentURI, resourceURI)
                  .EqualsLiteral("same-origin"));
  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(nsIContentPolicy::TYPE_DOCUMENT,
                                          documentURI, documentURI)
                  .EqualsLiteral("none"));
}

TEST(NaiveFoxPreambleSemantics, ResourceOriginIncludesPort)
{
  nsCOMPtr<nsIURI> documentURI;
  nsCOMPtr<nsIURI> resourceURI;
  ASSERT_EQ(NS_NewURI(getter_AddRefs(documentURI),
                      "https://proxy.example:443/camouflage/"_ns),
            NS_OK);
  ASSERT_EQ(NS_NewURI(getter_AddRefs(resourceURI),
                      "https://proxy.example:8443/app.js"_ns),
            NS_OK);

  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(nsIContentPolicy::TYPE_SCRIPT,
                                          documentURI, resourceURI)
                  .EqualsLiteral("cross-site"));
}

}  // namespace mozilla::dom

namespace mozilla::naivefox {

TEST(NaiveFoxPreambleSemantics, InternalStylePreloadKeepsStylesheetIdentity)
{
  EXPECT_EQ(StyleContentPolicyType(css::StylePreloadKind::None),
            nsIContentPolicy::TYPE_INTERNAL_STYLESHEET);
  EXPECT_EQ(StyleContentPolicyType(css::StylePreloadKind::FromParser),
            nsIContentPolicy::TYPE_INTERNAL_STYLESHEET_PRELOAD);
  EXPECT_EQ(
      StyleContentPolicyType(css::StylePreloadKind::FromLinkRelPreloadElement),
      nsIContentPolicy::TYPE_INTERNAL_STYLESHEET_PRELOAD);
  EXPECT_EQ(nsContentUtils::InternalContentPolicyTypeToExternal(
                nsIContentPolicy::TYPE_INTERNAL_STYLESHEET_PRELOAD),
            ExtContentPolicy::TYPE_STYLESHEET);
  EXPECT_EQ(nsContentUtils::InternalContentPolicyTypeToExternal(
                nsIContentPolicy::TYPE_INTERNAL_STYLESHEET),
            ExtContentPolicy::TYPE_STYLESHEET);
}

TEST(NaiveFoxPreambleSemantics, NativeStylePreloadChannelContract)
{
  nsCOMPtr<nsIURI> documentURI;
  nsCOMPtr<nsIURI> resourceURI;
  ASSERT_EQ(NS_NewURI(getter_AddRefs(documentURI),
                      "https://proxy.example/camouflage/"_ns),
            NS_OK);
  ASSERT_EQ(NS_NewURI(getter_AddRefs(resourceURI),
                      "https://proxy.example/camouflage/style.css"_ns),
            NS_OK);

  OriginAttributes attrs;
  RefPtr<dom::ReferrerInfo> referrerInfo = new dom::ReferrerInfo(
      documentURI, dom::ReferrerPolicy::Strict_origin_when_cross_origin, true);
  nsCOMPtr<nsIChannel> channel;
  ASSERT_EQ(NewNativeStylePreloadChannel(
                resourceURI, documentURI, css::StylePreloadKind::FromParser,
                attrs, referrerInfo,
                /* aLoadGroup = */ nullptr,
                /* aProxyInfo = */ nullptr, ProxyProtocol::H3,
                getter_AddRefs(channel)),
            NS_OK);
  ASSERT_TRUE(channel);

  nsCOMPtr<nsILoadInfo> loadInfo = channel->LoadInfo();
  ASSERT_TRUE(loadInfo);
  EXPECT_EQ(loadInfo->InternalContentPolicyType(),
            nsIContentPolicy::TYPE_INTERNAL_STYLESHEET_PRELOAD);
  EXPECT_EQ(loadInfo->GetExternalContentPolicyType(),
            ExtContentPolicy::TYPE_STYLESHEET);
  constexpr nsSecurityFlags expectedSecurityFlags =
      nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_INHERITS_SEC_CONTEXT |
      nsILoadInfo::SEC_ALLOW_CHROME;
  EXPECT_EQ(loadInfo->GetSecurityFlags(), expectedSecurityFlags);
  ASSERT_TRUE(loadInfo->GetLoadingPrincipal());
  ASSERT_TRUE(loadInfo->TriggeringPrincipal());
  EXPECT_FALSE(loadInfo->GetLoadingPrincipal()->IsSystemPrincipal());
  EXPECT_FALSE(loadInfo->TriggeringPrincipal()->IsSystemPrincipal());
  EXPECT_TRUE(loadInfo->GetLoadingPrincipal()->IsSameOrigin(documentURI));
  EXPECT_TRUE(loadInfo->TriggeringPrincipal()->IsSameOrigin(documentURI));
  EXPECT_EQ(loadInfo->GetOriginAttributes(), attrs);
  nsCOMPtr<nsICookieJarSettings> cookieJarSettings;
  ASSERT_EQ(loadInfo->GetCookieJarSettings(
                getter_AddRefs(cookieJarSettings)),
            NS_OK);
  ASSERT_TRUE(cookieJarSettings);

  nsLoadFlags loadFlags = 0;
  ASSERT_EQ(channel->GetLoadFlags(&loadFlags), NS_OK);
  EXPECT_EQ(loadFlags & (nsIRequest::LOAD_ANONYMOUS |
                         nsIChannel::LOAD_BYPASS_SERVICE_WORKER |
                         nsIRequest::INHIBIT_CACHING),
            0u);

  nsCOMPtr<nsIHttpChannel> httpChannel = do_QueryInterface(channel);
  nsCOMPtr<nsIHttpChannelInternal> internal = do_QueryInterface(channel);
  ASSERT_TRUE(httpChannel);
  ASSERT_TRUE(internal);
  nsAutoCString method;
  ASSERT_EQ(httpChannel->GetRequestMethod(method), NS_OK);
  EXPECT_TRUE(method.EqualsLiteral("GET"));
  bool allowed = false;
  ASSERT_EQ(internal->GetAllowSpdy(&allowed), NS_OK);
  EXPECT_TRUE(allowed);
  ASSERT_EQ(internal->GetAllowHttp3(&allowed), NS_OK);
  EXPECT_TRUE(allowed);
  nsCOMPtr<nsIURI> actualDocumentURI;
  ASSERT_EQ(internal->GetDocumentURI(getter_AddRefs(actualDocumentURI)), NS_OK);
  bool equal = false;
  ASSERT_EQ(actualDocumentURI->Equals(documentURI, &equal), NS_OK);
  EXPECT_TRUE(equal);

  nsCOMPtr<nsIReferrerInfo> actualReferrerInfo;
  ASSERT_EQ(httpChannel->GetReferrerInfo(getter_AddRefs(actualReferrerInfo)),
            NS_OK);
  ASSERT_TRUE(actualReferrerInfo);
  nsCOMPtr<nsIURI> actualReferrer = actualReferrerInfo->GetOriginalReferrer();
  ASSERT_TRUE(actualReferrer);
  ASSERT_EQ(actualReferrer->Equals(documentURI, &equal), NS_OK);
  EXPECT_TRUE(equal);
  EXPECT_EQ(actualReferrerInfo->ReferrerPolicy(),
            dom::ReferrerPolicy::Strict_origin_when_cross_origin);

  nsCOMPtr<nsIClassOfService> cos = do_QueryInterface(channel);
  ASSERT_TRUE(cos);
  uint32_t classFlags = 0;
  ASSERT_EQ(cos->GetClassFlags(&classFlags), NS_OK);
  EXPECT_TRUE(classFlags & nsIClassOfService::Leader);
  EXPECT_FALSE(classFlags & nsIClassOfService::UrgentStart);
  nsIClassOfService::FetchPriority fetchPriority;
  ASSERT_EQ(cos->GetFetchPriority(&fetchPriority), NS_OK);
  EXPECT_EQ(fetchPriority, nsIClassOfService::FETCHPRIORITY_AUTO);

  nsCOMPtr<nsITimedChannel> timedChannel = do_QueryInterface(channel);
  ASSERT_TRUE(timedChannel);
  nsAutoString initiatorType;
  ASSERT_EQ(timedChannel->GetInitiatorType(initiatorType), NS_OK);
  EXPECT_TRUE(initiatorType.EqualsLiteral("link"));
}

TEST(NaiveFoxPreambleSemantics, NativeBlockingResourcesAreLeaders)
{
  using detail::PreambleResourceKind;
  using detail::PreambleResourceNeedsLeader;

  EXPECT_TRUE(PreambleResourceNeedsLeader(PreambleResourceKind::Stylesheet,
                                          false, false, false));
  EXPECT_FALSE(PreambleResourceNeedsLeader(PreambleResourceKind::Stylesheet,
                                           true, false, true));
  EXPECT_TRUE(PreambleResourceNeedsLeader(PreambleResourceKind::Script, false,
                                          true, true));
  EXPECT_FALSE(PreambleResourceNeedsLeader(PreambleResourceKind::Script, false,
                                           true, false));
  EXPECT_FALSE(PreambleResourceNeedsLeader(PreambleResourceKind::Script, false,
                                           false, true));
  EXPECT_FALSE(PreambleResourceNeedsLeader(PreambleResourceKind::Other, false,
                                           true, true));
}

TEST(NaiveFoxPreambleSemantics, ParsesBlockingResourceAttributes)
{
  EXPECT_TRUE(detail::PreambleStylesheetIsNonDeferred(
      "<link rel=\"stylesheet\">"_ns, false));
  EXPECT_TRUE(detail::PreambleStylesheetIsNonDeferred(
      "<link rel=\"stylesheet\" media=\" all \">"_ns, false));
  EXPECT_TRUE(detail::PreambleStylesheetIsNonDeferred(
      "<link rel=\"stylesheet\" media=\"\">"_ns, false));
  EXPECT_TRUE(detail::PreambleStylesheetIsNonDeferred(
      "<link media=all rel=\"stylesheet\">"_ns, false));
  EXPECT_FALSE(detail::PreambleStylesheetIsNonDeferred(
      "<link rel=\"stylesheet\" media=\"print\">"_ns, false));
  EXPECT_FALSE(detail::PreambleStylesheetIsNonDeferred(
      "<link rel=\"stylesheet\" disabled>"_ns, false));
  EXPECT_FALSE(detail::PreambleStylesheetIsNonDeferred(
      "<link rel=\"alternate stylesheet\">"_ns, true));

  EXPECT_TRUE(detail::PreambleScriptIsParserBlockingClassic(
      "<script src=\"app.js\">"_ns));
  EXPECT_TRUE(detail::PreambleScriptIsParserBlockingClassic(
      "<script type=\"\" src=\"app.js\">"_ns));
  EXPECT_TRUE(detail::PreambleScriptIsParserBlockingClassic(
      "<script type=\"text/javascript\" src=\"app.js\">"_ns));
  EXPECT_TRUE(detail::PreambleScriptIsParserBlockingClassic(
      "<script type=application/javascript src=\"app.js\">"_ns));
  EXPECT_FALSE(detail::PreambleScriptIsParserBlockingClassic(
      "<script async type=\"text/javascript\" src=\"app.js\">"_ns));
  EXPECT_FALSE(detail::PreambleScriptIsParserBlockingClassic(
      "<script defer src=\"app.js\">"_ns));
  EXPECT_FALSE(detail::PreambleScriptIsParserBlockingClassic(
      "<script type=\"module\" src=\"app.js\">"_ns));
  EXPECT_FALSE(detail::PreambleScriptIsParserBlockingClassic(
      "<script type=\"application/json\" src=\"data.js\">"_ns));
}

}  // namespace mozilla::naivefox
