/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstring>

#include "HttpConnectParser.h"
#include "gtest/gtest.h"

namespace mozilla::naivefox {

namespace {

HttpConnectParser::Event Consume(HttpConnectParser& aParser,
                                 const nsACString& aInput, size_t& aConsumed) {
  return aParser.Consume(
      Span(reinterpret_cast<const uint8_t*>(aInput.BeginReading()),
           aInput.Length()),
      aConsumed);
}

}  // namespace

TEST(NaiveFoxHttpConnectParser, CompleteDomainRequest)
{
  HttpConnectParser parser;
  size_t consumed = 0;
  EXPECT_EQ(Consume(parser,
                    "CONNECT example.com:443 HTTP/1.1\r\nHost: "
                    "example.com:443\r\n\r\n"_ns,
                    consumed),
            HttpConnectParser::Event::RequestReady);
  EXPECT_EQ(consumed, 59U);
  EXPECT_TRUE(parser.Target().mHost.EqualsLiteral("example.com"));
  EXPECT_EQ(parser.Target().mPort, 443);
  EXPECT_TRUE(parser.Target().Authority().EqualsLiteral("example.com:443"));
}

TEST(NaiveFoxHttpConnectParser, ArbitraryFragmentation)
{
  const nsAutoCString request(
      "CONNECT 127.0.0.1:8080 HTTP/1.1\r\nHost: 127.0.0.1:8080\r\n\r\n"_ns);
  for (size_t split = 0; split <= request.Length(); ++split) {
    HttpConnectParser parser;
    size_t consumed = 0;
    auto event = Consume(parser, Substring(request, 0, split), consumed);
    EXPECT_EQ(consumed, split);
    if (split < request.Length()) {
      EXPECT_EQ(event, HttpConnectParser::Event::NeedMore);
      event = Consume(parser, Substring(request, split), consumed);
      EXPECT_EQ(consumed, request.Length() - split);
    }
    EXPECT_EQ(event, HttpConnectParser::Event::RequestReady) << split;
    EXPECT_TRUE(parser.Target().mHost.EqualsLiteral("127.0.0.1"));
    EXPECT_EQ(parser.Target().mPort, 8080);
  }
}

TEST(NaiveFoxHttpConnectParser, ByteByByteAndSplitCrlf)
{
  const nsAutoCString request(
      "CONNECT [::1]:443 HTTP/1.1\r\nHost: [::1]:443\r\n\r\n"_ns);
  HttpConnectParser parser;
  HttpConnectParser::Event event = HttpConnectParser::Event::NeedMore;
  for (size_t i = 0; i < request.Length(); ++i) {
    const char value = request.CharAt(i);
    size_t consumed = 0;
    nsAutoCString byte;
    byte.Append(value);
    event = Consume(parser, byte, consumed);
    EXPECT_EQ(consumed, 1U);
  }
  EXPECT_EQ(event, HttpConnectParser::Event::RequestReady);
  EXPECT_TRUE(parser.Target().mIPv6);
  EXPECT_TRUE(parser.Target().Authority().EqualsLiteral("[::1]:443"));
}

TEST(NaiveFoxHttpConnectParser, PreservesEarlyPayload)
{
  const nsAutoCString input(
      "CONNECT example.com:443 HTTP/1.1\r\n\r\n\x16\x03\x01payload"_ns);
  HttpConnectParser parser;
  size_t consumed = 0;
  EXPECT_EQ(Consume(parser, input, consumed),
            HttpConnectParser::Event::RequestReady);
  EXPECT_EQ(Substring(input, consumed), "\x16\x03\x01payload"_ns);
}

TEST(NaiveFoxHttpConnectParser, RejectsInvalidRequests)
{
  struct Case final {
    const char* mRequest;
    HttpConnectParser::Event mEvent;
  };
  static constexpr Case kCases[] = {
      {"GET http://example.com/ HTTP/1.1\r\n\r\n",
       HttpConnectParser::Event::UnsupportedMethod},
      {"CONNECT example.com HTTP/1.1\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
      {"CONNECT :443 HTTP/1.1\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
      {"CONNECT example.com:0 HTTP/1.1\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
      {"CONNECT ::1:443 HTTP/1.1\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
      {"CONNECT [not-ipv6]:443 HTTP/1.1\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
      {"CONNECT example.com:443 HTTP/2\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
      {"CONNECT example.com:443 HTTP/1.1\r\n folded\r\n\r\n",
       HttpConnectParser::Event::ProtocolError},
  };
  for (const auto& test : kCases) {
    HttpConnectParser parser;
    size_t consumed = 0;
    EXPECT_EQ(Consume(parser, nsDependentCString(test.mRequest), consumed),
              test.mEvent)
        << test.mRequest;
  }
}

TEST(NaiveFoxHttpConnectParser, RejectsOversizedHeaders)
{
  nsAutoCString request("CONNECT example.com:443 HTTP/1.1\r\nX: "_ns);
  for (size_t i = 0; i < HttpConnectParser::kMaximumHeaderBytes; ++i) {
    request.Append('a');
  }
  HttpConnectParser parser;
  size_t consumed = 0;
  EXPECT_EQ(Consume(parser, request, consumed),
            HttpConnectParser::Event::HeaderTooLarge);
  EXPECT_EQ(consumed, HttpConnectParser::kMaximumHeaderBytes);
}

}  // namespace mozilla::naivefox
