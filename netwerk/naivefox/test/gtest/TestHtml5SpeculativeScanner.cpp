/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "gtest/gtest.h"
#include "nsHtml5SpeculativeScanner.h"

TEST(Html5SpeculativeScanner, DiscoversStyleWithParserSemantics)
{
  nsHtml5SpeculativeScanner scanner;

  ASSERT_EQ(NS_OK,
            scanner.Feed(u"<!doctype html><html><head><template><link "
                         u"rel=stylesheet href=ignored.css></template><li"_ns));
  ASSERT_EQ(NS_OK,
            scanner.Feed(u"nk rel=stylesheet href=app.css charset=utf-8 "
                         u"crossorigin=anonymous media=screen "
                         u"referrerpolicy=no-referrer nonce=n integrity=i "
                         u"fetchpriority=high>"_ns));
  ASSERT_EQ(NS_OK, scanner.Finish());

  nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
  scanner.TakeStyleDescriptors(descriptors);
  ASSERT_EQ(1U, descriptors.Length());
  const auto& style = descriptors[0];
  EXPECT_EQ(u"app.css"_ns, style.Url());
  EXPECT_EQ(u"utf-8"_ns, style.Charset());
  EXPECT_EQ(u"anonymous"_ns, style.CrossOrigin());
  EXPECT_EQ(u"screen"_ns, style.Media());
  EXPECT_EQ(u"no-referrer"_ns, style.ReferrerPolicy());
  EXPECT_EQ(u"n"_ns, style.Nonce());
  EXPECT_EQ(u"i"_ns, style.Integrity());
  EXPECT_EQ(u"high"_ns, style.FetchPriority());
  EXPECT_FALSE(style.IsLinkPreload());
}

TEST(Html5SpeculativeScanner, PreservesLeanResourceDiscoveryOrder)
{
  nsHtml5SpeculativeScanner scanner;

  ASSERT_EQ(
      NS_OK,
      scanner.Feed(
          u"<!doctype html><html><head><link rel=stylesheet href=front.css>"
          u"<script defer src=front.js></script></head><body>"
          u"<img src=front.svg></body></html>"_ns));
  ASSERT_EQ(NS_OK, scanner.Finish());

  nsTArray<nsHtml5LeanPreloadDescriptor> descriptors;
  scanner.TakeLeanDescriptors(descriptors);
  ASSERT_EQ(4U, descriptors.Length());
  EXPECT_EQ(nsHtml5LeanPreloadDescriptor::Kind::DocumentMode,
            descriptors[0].GetKind());
  EXPECT_EQ(nsHtml5LeanPreloadDescriptor::Kind::Style,
            descriptors[1].GetKind());
  EXPECT_EQ(u"front.css"_ns, descriptors[1].UrlOrSizes());
  EXPECT_EQ(nsHtml5LeanPreloadDescriptor::Kind::ScriptFromHead,
            descriptors[2].GetKind());
  EXPECT_EQ(u"front.js"_ns, descriptors[2].UrlOrSizes());
  EXPECT_TRUE(descriptors[2].IsDefer());
  EXPECT_EQ(nsHtml5LeanPreloadDescriptor::Kind::Image,
            descriptors[3].GetKind());
  EXPECT_EQ(u"front.svg"_ns, descriptors[3].UrlOrSizes());
}
