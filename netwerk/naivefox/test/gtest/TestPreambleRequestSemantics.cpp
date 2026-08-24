/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "SecFetch.h"

#include "gtest/gtest.h"
#include "nsIContentPolicy.h"
#include "nsIURI.h"
#include "nsNetUtil.h"

namespace mozilla::dom {

TEST(NaiveFoxPreambleSemantics, SystemOwnedResourceUsesDocumentOrigin) {
  nsCOMPtr<nsIURI> documentURI;
  nsCOMPtr<nsIURI> resourceURI;
  ASSERT_EQ(NS_NewURI(getter_AddRefs(documentURI),
                      "https://proxy.example/camouflage/"_ns),
            NS_OK);
  ASSERT_EQ(NS_NewURI(getter_AddRefs(resourceURI),
                      "https://proxy.example/camouflage/app.js"_ns),
            NS_OK);

  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(
                  nsIContentPolicy::TYPE_SCRIPT, documentURI, resourceURI)
                  .EqualsLiteral("same-origin"));
  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(
                  nsIContentPolicy::TYPE_STYLESHEET, documentURI, resourceURI)
                  .EqualsLiteral("same-origin"));
  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(
                  nsIContentPolicy::TYPE_IMAGE, documentURI, resourceURI)
                  .EqualsLiteral("same-origin"));
  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(
                  nsIContentPolicy::TYPE_DOCUMENT, documentURI, documentURI)
                  .EqualsLiteral("none"));
}

TEST(NaiveFoxPreambleSemantics, ResourceOriginIncludesPort) {
  nsCOMPtr<nsIURI> documentURI;
  nsCOMPtr<nsIURI> resourceURI;
  ASSERT_EQ(NS_NewURI(getter_AddRefs(documentURI),
                      "https://proxy.example:443/camouflage/"_ns),
            NS_OK);
  ASSERT_EQ(NS_NewURI(getter_AddRefs(resourceURI),
                      "https://proxy.example:8443/app.js"_ns),
            NS_OK);

  EXPECT_TRUE(ComputeNaiveFoxSecFetchSite(
                  nsIContentPolicy::TYPE_SCRIPT, documentURI, resourceURI)
                  .EqualsLiteral("cross-site"));
}

}  // namespace mozilla::dom
