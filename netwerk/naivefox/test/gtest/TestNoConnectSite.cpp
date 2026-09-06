/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include <cstring>
#include <string>

#include "NoConnectSite.h"
#include "gtest/gtest.h"

using namespace mozilla;
using namespace mozilla::naivefox;

TEST(NaiveFoxNoConnectSite, DiscoversAllDirectKindsAndDeduplicates)
{
  const char* html =
      "<!doctype html><link rel=stylesheet href=css/main.css>"
      "<script defer src=/js/main.js></script><link rel=preload as=image "
      "href=/hero.webp>"
      "<img src=/hero.webp><link rel=icon href=/favicon.ico>";
  NoConnectSite site;
  ASSERT_EQ(NS_OK, site.Feed(Span(reinterpret_cast<const uint8_t*>(html),
                                  strlen(html)),
                             true));
  auto resources = site.TakeResources();
  ASSERT_EQ(4U, resources.Length());
  EXPECT_EQ("/css/main.css"_ns, resources[0].mPath);
  EXPECT_EQ(SiteResourceKind::Style, resources[0].mKind);
  EXPECT_EQ("/js/main.js"_ns, resources[1].mPath);
  EXPECT_EQ(SiteResourceKind::Script, resources[1].mKind);
  EXPECT_EQ("/hero.webp"_ns, resources[2].mPath);
  EXPECT_EQ("/favicon.ico"_ns, resources[3].mPath);
}

TEST(NaiveFoxNoConnectSite, IgnoresInertAndSecondaryContent)
{
  const char* html =
      "<!doctype html><template><div><img src=/ignored.png></div></template>"
      "<noscript><img src=/also-ignored.png></noscript>"
      "<script>const x='<img src=/fake.png>';</script><a href=/next>next</a>"
      "<!-- <img src=/comment.png> --><img src=/real.png>";
  NoConnectSite site;
  ASSERT_EQ(NS_OK, site.Feed(Span(reinterpret_cast<const uint8_t*>(html),
                                  strlen(html)),
                             true));
  auto resources = site.TakeResources();
  ASSERT_EQ(1U, resources.Length());
  EXPECT_EQ("/real.png"_ns, resources[0].mPath);
}

TEST(NaiveFoxNoConnectSite, StreamingUTF8AndEntities)
{
  const char* html =
      "<!doctype html><p>\xD0\x9F\xD1\x80\xD0\xB8\xD0\xB2\xD0\xB5\xD1\x82</p>"
      "<img src='pics/caf\xC3\xA9.png?a=1&amp;b=2#part'>";
  for (size_t chunk : {1U, 2U, 7U, 64U}) {
    NoConnectSite site;
    const size_t length = strlen(html);
    for (size_t offset = 0; offset < length;) {
      const size_t count = std::min(chunk, length - offset);
      ASSERT_EQ(NS_OK,
                site.Feed(Span(reinterpret_cast<const uint8_t*>(html + offset),
                               count)));
      offset += count;
    }
    ASSERT_EQ(NS_OK, site.Feed(Span<const uint8_t>(), true));
    auto resources = site.TakeResources();
    ASSERT_EQ(1U, resources.Length());
    EXPECT_EQ("/pics/caf%C3%A9.png?a=1&b=2"_ns, resources[0].mPath);
  }
}

TEST(NaiveFoxNoConnectSite, NoFixedDocumentOrResourceCountBudget)
{
  std::string html = "<!doctype html>";
  html.append(80 * 1024, ' ');
  for (size_t i = 0; i < 40; ++i) {
    html += "<script defer src=/code-" + std::to_string(i) + ".js></script>";
  }
  NoConnectSite site;
  ASSERT_EQ(NS_OK, site.Feed(Span(reinterpret_cast<const uint8_t*>(html.data()),
                                  html.size()),
                             true));
  EXPECT_EQ(40U, site.TakeResources().Length());
}

TEST(NaiveFoxNoConnectSite, RootOnlyAndDataIcon)
{
  const char* html = "<!doctype html><link rel=icon href='data:,'><p>hello</p>";
  NoConnectSite site;
  ASSERT_EQ(NS_OK, site.Feed(Span(reinterpret_cast<const uint8_t*>(html),
                                  strlen(html)),
                             true));
  EXPECT_TRUE(site.TakeResources().IsEmpty());
}

TEST(NaiveFoxNoConnectSite, RejectsUnsupportedAndUnsafeGraphs)
{
  for (const char* html :
       {"<img src='/assets/%2e%2e/private.png'>",
        "<img src=https://other.example/image.png>",
        "<img src=//other.example/image.png>",
        "<script defer src=/api/sync></script>",
        "<base href=/other/><img src=x.png>", "<img loading=lazy src=a.png>",
        "<img src=a.png srcset='b.png 2x'>",
        "<script type=module src=a.js></script>",
        "<script async src=a.js></script>", "<iframe src=child.html></iframe>",
        "<link rel=stylesheet href=same><script defer src=same></script>",
        "<meta http-equiv=refresh content='0;url=next'>"}) {
    NoConnectSite site;
    EXPECT_TRUE(NS_FAILED(site.Feed(
        Span(reinterpret_cast<const uint8_t*>(html), strlen(html)), true)))
        << html;
  }
}

TEST(NaiveFoxNoConnectSite, RejectsBrokenUTF8AtEveryBoundary)
{
  for (const std::string bytes :
       {std::string("\xFF"), std::string("\xC3"), std::string("a\0b", 3)}) {
    NoConnectSite site;
    EXPECT_TRUE(NS_FAILED(site.Feed(
        Span(reinterpret_cast<const uint8_t*>(bytes.data()), bytes.size()),
        true)));
  }
}
