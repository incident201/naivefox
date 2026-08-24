/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NeckoTunnel.h"
#include "SecFetch.h"
#include "gtest/gtest.h"
#include "nsIContentPolicy.h"
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
