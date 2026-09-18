/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "TransportCookies.h"
#include "gtest/gtest.h"
#include "nsIURI.h"
#include "nsNetUtil.h"
#include "prtime.h"

using namespace mozilla::naivefox;

namespace {
nsCOMPtr<nsIURI> CookieURI(const char* aSpec) {
  nsCOMPtr<nsIURI> uri;
  EXPECT_EQ(NS_OK, NS_NewURI(getter_AddRefs(uri), aSpec));
  return uri;
}
nsCString SessionCookie() {
  nsCString result("session=");
  for (int i = 0; i < 64; ++i) {
    result.Append('a');
  }
  result.AppendLiteral("; Secure; HttpOnly; Path=/");
  return result;
}
}  // namespace

TEST(NaiveFoxTransportCookies, OriginPathAndCarrierIsolation)
{
  TransportCookies first, second;
  auto root = CookieURI("https://edge.example.com/");
  auto api = CookieURI("https://edge.example.com/api/sync");
  auto other = CookieURI("https://other.example.com/api/sync");
  const auto now = PR_Now();
  ASSERT_EQ(NS_OK,
            first.Store(root, "edge=one; Secure; Path=/"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK, first.Store(root, SessionCookie(), ""_ns, now));
  ASSERT_TRUE(first.HasSession());
  ASSERT_EQ(NS_OK,
            first.Store(api, "scoped=yes; Secure; Path=/api"_ns, ""_ns, now));
  nsAutoCString header;
  ASSERT_EQ(NS_OK, first.RequestHeader(root, header));
  EXPECT_EQ(kNotFound, header.Find("scoped="));
  ASSERT_EQ(NS_OK, first.RequestHeader(api, header));
  EXPECT_TRUE(StringBeginsWith(header, "scoped=yes; "_ns));
  ASSERT_EQ(NS_OK, first.RequestHeader(
                       CookieURI("https://edge.example.com/apix").get(), header));
  EXPECT_EQ(kNotFound, header.Find("scoped="));
  EXPECT_TRUE(NS_FAILED(first.RequestHeader(other, header)));
  EXPECT_TRUE(NS_FAILED(
      first.RequestHeader(CookieURI("http://edge.example.com/").get(), header)));
  EXPECT_TRUE(NS_FAILED(
      first.RequestHeader(CookieURI("https://edge.example.com:444/").get(), header)));
  ASSERT_EQ(NS_OK, second.RequestHeader(root, header));
  EXPECT_TRUE(header.IsEmpty());
  EXPECT_FALSE(second.HasSession());
  first.Clear();
  EXPECT_FALSE(first.HasSession());
  EXPECT_TRUE(NS_FAILED(first.Store(root, SessionCookie(), ""_ns, now)));
}

TEST(NaiveFoxTransportCookies, DomainPrefixAndHeaderValidation)
{
  TransportCookies jar;
  auto uri = CookieURI("https://edge.example.com/api/sync");
  const auto now = PR_Now();
  for (const auto& cookie :
       {"wrong=a; Domain=other.example.com; Secure"_ns,
        "suffix=a; Domain=com; Secure"_ns, "suffix2=a; Domain=co.uk; Secure"_ns,
        "injected=a\r\nbad=b"_ns, "__Secure-no=a"_ns,
        "__Host-no=a; Secure; Domain=example.com; Path=/"_ns,
        "__Host-path=a; Secure; Path=/api"_ns, "bad name=value"_ns,
        "bad=one,two"_ns}) {
    ASSERT_EQ(NS_OK, jar.Store(uri, cookie, ""_ns, now));
  }
  nsAutoCString header;
  ASSERT_EQ(NS_OK, jar.RequestHeader(uri, header));
  EXPECT_TRUE(header.IsEmpty());
  ASSERT_EQ(NS_OK,
            jar.Store(uri, "domain=yes; Domain=.EXAMPLE.COM; Secure; Path=/"_ns,
                      ""_ns, now));
  ASSERT_EQ(NS_OK,
            jar.Store(uri, "__Host-good=yes; Secure; Path=/"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK, jar.Store(uri, "quoted=\"value\"; Secure"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK, jar.RequestHeader(uri, header));
  EXPECT_NE(kNotFound, header.Find("domain=yes"));
  EXPECT_NE(kNotFound, header.Find("__Host-good=yes"));
  EXPECT_NE(kNotFound, header.Find("quoted=\"value\""));
  EXPECT_TRUE(NS_FAILED(
      jar.Store(uri, "session=invalid; Secure; Path=/"_ns, ""_ns, now)));
}

TEST(NaiveFoxTransportCookies, UpdateDeleteAndExpiry)
{
  TransportCookies jar;
  auto uri = CookieURI("https://edge.example.com/");
  const auto now = PR_Now();
  ASSERT_EQ(NS_OK, jar.Store(uri, "edge=old; Secure; Path=/"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK, jar.Store(uri, "edge=new; Secure; Path=/"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK,
            jar.Store(uri, "past=no; Expires=Wed, 09 Jun 2021 10:18:14 GMT"_ns,
                      ""_ns, now));
  ASSERT_EQ(NS_OK, jar.Store(uri, "age=no; Max-Age=1"_ns, ""_ns,
                             now - 2 * PR_USEC_PER_SEC));
  ASSERT_EQ(
      NS_OK,
      jar.Store(
          uri,
          "precedence=yes; Max-Age=60; Expires=Wed, 09 Jun 2021 10:18:14 GMT"_ns,
          ""_ns, now));
  ASSERT_EQ(
      NS_OK,
      jar.Store(uri, "clock=yes; Expires=Wed, 09 Jun 2021 10:19:14 GMT"_ns,
                "Wed, 09 Jun 2021 10:18:14 GMT"_ns, now));
  nsAutoCString header;
  ASSERT_EQ(NS_OK, jar.RequestHeader(uri, header));
  EXPECT_EQ(kNotFound, header.Find("edge=old"));
  EXPECT_NE(kNotFound, header.Find("edge=new"));
  EXPECT_EQ(kNotFound, header.Find("past="));
  EXPECT_EQ(kNotFound, header.Find("age="));
  EXPECT_NE(kNotFound, header.Find("precedence=yes"));
  EXPECT_NE(kNotFound, header.Find("clock=yes"));
  ASSERT_EQ(NS_OK, jar.Store(uri, "edge=; Max-Age=0; Path=/"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK, jar.RequestHeader(uri, header));
  EXPECT_EQ(kNotFound, header.Find("edge="));
}

TEST(NaiveFoxTransportCookies, ExplicitMemoryBounds)
{
  TransportCookies jar;
  auto uri = CookieURI("https://edge.example.com/");
  const auto now = PR_Now();
  for (int i = 0; i < 32; ++i) {
    nsAutoCString cookie("c");
    cookie.AppendInt(i);
    cookie.AppendLiteral("=v");
    ASSERT_EQ(NS_OK, jar.Store(uri, cookie, ""_ns, now));
  }
  EXPECT_EQ(NS_ERROR_FILE_TOO_BIG, jar.Store(uri, "extra=v"_ns, ""_ns, now));
  ASSERT_EQ(NS_OK, jar.Store(uri, "c0=updated"_ns, ""_ns, now));
  TransportCookies bytes;
  nsCString value;
  for (int i = 0; i < 3800; ++i) {
    value.Append('a');
  }
  for (int i = 0; i < 5; ++i) {
    nsAutoCString cookie("large");
    cookie.AppendInt(i);
    cookie.Append('=');
    cookie.Append(value);
    EXPECT_EQ(i < 4 ? NS_OK : NS_ERROR_FILE_TOO_BIG,
              bytes.Store(uri, cookie, ""_ns, now));
  }
}
