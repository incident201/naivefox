/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <algorithm>
#include <cstdint>

#include "Socks5Parser.h"
#include "gtest/gtest.h"
#include "mozilla/Span.h"
#include "nsTArray.h"

namespace mozilla::naivefox {

namespace {

using Event = Socks5Parser::Event;

Event FeedWithMaximumFragment(Socks5Parser& aParser,
                              const nsTArray<uint8_t>& aBytes,
                              size_t aMaximumFragment,
                              bool& aSawMethodSelection) {
  size_t offset = 0;
  while (offset < aBytes.Length()) {
    size_t available = std::min(aMaximumFragment, aBytes.Length() - offset);
    size_t consumed = 0;
    Event event =
        aParser.Consume(Span(aBytes.Elements() + offset, available), consumed);
    EXPECT_GT(consumed, 0U);
    EXPECT_LE(consumed, available);
    offset += consumed;

    if (event == Event::SendNoAuthenticationSelection) {
      EXPECT_FALSE(aSawMethodSelection);
      aSawMethodSelection = true;
      continue;
    }
    if (event != Event::NeedMore) {
      return event;
    }
  }
  return Event::NeedMore;
}

struct AuthenticationEvents final {
  bool mSawUsernamePasswordSelection = false;
  bool mSawAuthenticationSuccess = false;
};

Event FeedAuthenticatedWithMaximumFragment(
    Socks5Parser& aParser, const nsTArray<uint8_t>& aBytes,
    size_t aMaximumFragment, AuthenticationEvents& aEvents) {
  size_t offset = 0;
  while (offset < aBytes.Length()) {
    const size_t available =
        std::min(aMaximumFragment, aBytes.Length() - offset);
    size_t consumed = 0;
    const Event event =
        aParser.Consume(Span(aBytes.Elements() + offset, available), consumed);
    EXPECT_GT(consumed, 0U);
    EXPECT_LE(consumed, available);
    offset += consumed;

    if (event == Event::SendUsernamePasswordSelection) {
      EXPECT_FALSE(aEvents.mSawUsernamePasswordSelection);
      aEvents.mSawUsernamePasswordSelection = true;
      continue;
    }
    if (event == Event::SendAuthenticationSuccess) {
      EXPECT_TRUE(aEvents.mSawUsernamePasswordSelection);
      EXPECT_FALSE(aEvents.mSawAuthenticationSuccess);
      aEvents.mSawAuthenticationSuccess = true;
      continue;
    }
    if (event != Event::NeedMore) {
      return event;
    }
  }
  return Event::NeedMore;
}

nsTArray<uint8_t> DomainRequest(const nsACString& aHost, uint16_t aPort) {
  nsTArray<uint8_t> bytes{0x05, 0x02, 0x02, 0x00, 0x05, 0x01, 0x00, 0x03};
  bytes.AppendElement(static_cast<uint8_t>(aHost.Length()));
  bytes.AppendElements(reinterpret_cast<const uint8_t*>(aHost.BeginReading()),
                       aHost.Length());
  bytes.AppendElement(static_cast<uint8_t>(aPort >> 8));
  bytes.AppendElement(static_cast<uint8_t>(aPort));
  return bytes;
}

nsTArray<uint8_t> AuthenticatedDomainRequest(const nsACString& aUser,
                                             const nsACString& aPassword,
                                             const nsACString& aHost,
                                             uint16_t aPort) {
  EXPECT_LE(aUser.Length(), 255U);
  EXPECT_LE(aPassword.Length(), 255U);
  EXPECT_LE(aHost.Length(), 255U);

  nsTArray<uint8_t> bytes{0x05, 0x02, 0x00, 0x02, 0x01,
                          static_cast<uint8_t>(aUser.Length())};
  bytes.AppendElements(reinterpret_cast<const uint8_t*>(aUser.BeginReading()),
                       aUser.Length());
  bytes.AppendElement(static_cast<uint8_t>(aPassword.Length()));
  bytes.AppendElements(
      reinterpret_cast<const uint8_t*>(aPassword.BeginReading()),
      aPassword.Length());
  bytes.AppendElements(nsTArray<uint8_t>{0x05, 0x01, 0x00, 0x03});
  bytes.AppendElement(static_cast<uint8_t>(aHost.Length()));
  bytes.AppendElements(reinterpret_cast<const uint8_t*>(aHost.BeginReading()),
                       aHost.Length());
  bytes.AppendElement(static_cast<uint8_t>(aPort >> 8));
  bytes.AppendElement(static_cast<uint8_t>(aPort));
  return bytes;
}

TEST(NaiveFoxSocks5Parser, DomainSurvivesEveryFragmentSize)
{
  const nsCString host("never-resolve-this.invalid");
  nsTArray<uint8_t> bytes = DomainRequest(host, 443);

  for (size_t fragment = 1; fragment <= bytes.Length(); ++fragment) {
    Socks5Parser parser;
    bool sawMethodSelection = false;
    EXPECT_EQ(
        FeedWithMaximumFragment(parser, bytes, fragment, sawMethodSelection),
        Event::RequestReady)
        << "fragment=" << fragment;
    EXPECT_TRUE(sawMethodSelection);
    ASSERT_TRUE(parser.IsComplete());
    EXPECT_EQ(parser.Target().mType, Socks5Target::Type::Domain);
    EXPECT_EQ(parser.Target().mHost, host);
    EXPECT_EQ(parser.Target().mPort, 443U);
    EXPECT_EQ(parser.Target().Authority(), "never-resolve-this.invalid:443");
  }
}

TEST(NaiveFoxSocks5Parser, IPv4)
{
  nsTArray<uint8_t> bytes{0x05, 0x01, 0x00, 0x05, 0x01, 0x00, 0x01,
                          127,  0,    0,    1,    0x1f, 0x90};
  Socks5Parser parser;
  bool sawMethodSelection = false;
  EXPECT_EQ(FeedWithMaximumFragment(parser, bytes, 1, sawMethodSelection),
            Event::RequestReady);
  EXPECT_TRUE(sawMethodSelection);
  EXPECT_EQ(parser.Target().mType, Socks5Target::Type::IPv4);
  EXPECT_EQ(parser.Target().mHost, "127.0.0.1");
  EXPECT_EQ(parser.Target().mPort, 8080U);
  EXPECT_EQ(parser.Target().Authority(), "127.0.0.1:8080");
}

TEST(NaiveFoxSocks5Parser, IPv6)
{
  nsTArray<uint8_t> bytes{0x05, 0x01, 0x00, 0x05, 0x01, 0x00, 0x04};
  const uint8_t address[] = {0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0,
                             0,    0,    0,    0,    0, 0, 0, 1};
  bytes.AppendElements(address, sizeof(address));
  bytes.AppendElements(nsTArray<uint8_t>{0x01, 0xbb});

  Socks5Parser parser;
  bool sawMethodSelection = false;
  EXPECT_EQ(FeedWithMaximumFragment(parser, bytes, 3, sawMethodSelection),
            Event::RequestReady);
  EXPECT_EQ(parser.Target().mType, Socks5Target::Type::IPv6);
  EXPECT_EQ(parser.Target().mHost, "2001:db8::1");
  EXPECT_EQ(parser.Target().mPort, 443U);
  EXPECT_EQ(parser.Target().Authority(), "[2001:db8::1]:443");
}

TEST(NaiveFoxSocks5Parser, RejectsUnsupportedAuthentication)
{
  Socks5Parser parser;
  const nsTArray<uint8_t> bytes{0x05, 0x02, 0x01, 0x02};
  bool sawMethodSelection = false;
  EXPECT_EQ(FeedWithMaximumFragment(parser, bytes, bytes.Length(),
                                    sawMethodSelection),
            Event::RejectMethods);
  EXPECT_FALSE(sawMethodSelection);
}

TEST(NaiveFoxSocks5Parser,
     UsernamePasswordAuthenticationSurvivesEveryFragmentSize)
{
  const nsCString user("user:name");
  const nsCString password("p@ss word");
  const nsCString host("authenticated.invalid");
  const nsTArray<uint8_t> bytes =
      AuthenticatedDomainRequest(user, password, host, 8443);

  for (size_t fragment = 1; fragment <= bytes.Length(); ++fragment) {
    Socks5Parser parser(user, password);
    AuthenticationEvents events;
    EXPECT_EQ(FeedAuthenticatedWithMaximumFragment(parser, bytes, fragment,
                                                   events),
              Event::RequestReady)
        << "fragment=" << fragment;
    EXPECT_TRUE(events.mSawUsernamePasswordSelection);
    EXPECT_TRUE(events.mSawAuthenticationSuccess);
    ASSERT_TRUE(parser.IsComplete());
    EXPECT_EQ(parser.Target().mHost, host);
    EXPECT_EQ(parser.Target().mPort, 8443U);
  }
}

TEST(NaiveFoxSocks5Parser, AuthenticationRequiredRejectsNoAuthenticationOnly)
{
  Socks5Parser parser("user"_ns, "password"_ns);
  const nsTArray<uint8_t> bytes{0x05, 0x01, 0x00};
  AuthenticationEvents events;
  EXPECT_EQ(FeedAuthenticatedWithMaximumFragment(parser, bytes, bytes.Length(),
                                                 events),
            Event::RejectMethods);
  EXPECT_FALSE(events.mSawUsernamePasswordSelection);
  EXPECT_FALSE(events.mSawAuthenticationSuccess);
}

TEST(NaiveFoxSocks5Parser, RejectsWrongUsernameOrPassword)
{
  for (bool wrongUser : {false, true}) {
    const nsCString user(wrongUser ? "wrong"_ns : "user"_ns);
    const nsCString password(wrongUser ? "password"_ns : "wrong"_ns);
    Socks5Parser parser("user"_ns, "password"_ns);
    const nsTArray<uint8_t> bytes = AuthenticatedDomainRequest(
        user, password, "unused.invalid"_ns, 443);
    AuthenticationEvents events;
    EXPECT_EQ(FeedAuthenticatedWithMaximumFragment(parser, bytes, 1, events),
              Event::RejectAuthentication);
    EXPECT_TRUE(events.mSawUsernamePasswordSelection);
    EXPECT_FALSE(events.mSawAuthenticationSuccess);
  }
}

TEST(NaiveFoxSocks5Parser, SupportsOneSidedConfiguredCredentials)
{
  for (bool emptyUser : {false, true}) {
    const nsCString user(emptyUser ? ""_ns : "user"_ns);
    const nsCString password(emptyUser ? "password"_ns : ""_ns);
    Socks5Parser parser(user, password);
    const nsTArray<uint8_t> bytes = AuthenticatedDomainRequest(
        user, password, "one-sided.invalid"_ns, 443);
    AuthenticationEvents events;
    EXPECT_EQ(FeedAuthenticatedWithMaximumFragment(parser, bytes, 1, events),
              Event::RequestReady);
    EXPECT_TRUE(events.mSawUsernamePasswordSelection);
    EXPECT_TRUE(events.mSawAuthenticationSuccess);
  }
}

TEST(NaiveFoxSocks5Parser, RejectsBadAuthenticationVersion)
{
  Socks5Parser parser("user"_ns, "password"_ns);
  const nsTArray<uint8_t> bytes{0x05, 0x01, 0x02, 0x02};
  AuthenticationEvents events;
  EXPECT_EQ(FeedAuthenticatedWithMaximumFragment(parser, bytes, bytes.Length(),
                                                 events),
            Event::AuthenticationProtocolError);
  EXPECT_TRUE(events.mSawUsernamePasswordSelection);
  EXPECT_FALSE(events.mSawAuthenticationSuccess);
}

TEST(NaiveFoxSocks5Parser, RejectsBindAndUdpAssociate)
{
  for (uint8_t command : {uint8_t(0x02), uint8_t(0x03)}) {
    Socks5Parser parser;
    const nsTArray<uint8_t> bytes{0x05, 0x01, 0x00, 0x05, command};
    bool sawMethodSelection = false;
    EXPECT_EQ(FeedWithMaximumFragment(parser, bytes, bytes.Length(),
                                      sawMethodSelection),
              Event::RejectCommand);
    EXPECT_TRUE(sawMethodSelection);
  }
}

TEST(NaiveFoxSocks5Parser, FailedStateIsTerminalAndConsumesNothing)
{
  Socks5Parser parser;
  const nsTArray<uint8_t> request{0x05, 0x01, 0x00, 0x05, 0x03, 0x00,
                                  0x01, 0x00, 0x00, 0x00, 0x00};
  size_t consumed = 0;
  EXPECT_EQ(parser.Consume(Span(request.Elements(), 3), consumed),
            Event::SendNoAuthenticationSelection);
  EXPECT_EQ(consumed, 3U);
  EXPECT_EQ(parser.Consume(Span(request.Elements() + 3, request.Length() - 3),
                           consumed),
            Event::RejectCommand);
  EXPECT_EQ(consumed, 2U);

  nsTArray<uint8_t> tail;
  tail.SetLength(1024);
  std::fill(tail.Elements(), tail.Elements() + tail.Length(), 0xaa);
  consumed = 123;
  EXPECT_EQ(parser.Consume(Span(tail), consumed), Event::ProtocolError);
  EXPECT_EQ(consumed, 0U);
}

TEST(NaiveFoxSocks5Parser, RejectsBadVersionReservedAndAddressType)
{
  {
    Socks5Parser parser;
    const nsTArray<uint8_t> bytes{0x04};
    size_t consumed = 0;
    EXPECT_EQ(parser.Consume(Span(bytes), consumed), Event::RejectMethods);
  }
  for (const nsTArray<uint8_t>& bytes : {
           nsTArray<uint8_t>{0x05, 0x01, 0x00, 0x04},
           nsTArray<uint8_t>{0x05, 0x01, 0x00, 0x05, 0x01, 0x01},
       }) {
    Socks5Parser parser;
    bool sawMethodSelection = false;
    EXPECT_EQ(FeedWithMaximumFragment(parser, bytes, bytes.Length(),
                                      sawMethodSelection),
              Event::ProtocolError);
  }
  {
    Socks5Parser parser;
    const nsTArray<uint8_t> bytes{0x05, 0x01, 0x00, 0x05, 0x01, 0x00, 0x09};
    bool sawMethodSelection = false;
    EXPECT_EQ(FeedWithMaximumFragment(parser, bytes, bytes.Length(),
                                      sawMethodSelection),
              Event::RejectAddressType);
  }
}

TEST(NaiveFoxSocks5Parser, BuildsProtocolReplies)
{
  nsTArray<uint8_t> reply;
  Socks5Parser::MakeMethodSelection(true, reply);
  EXPECT_EQ(reply, (nsTArray<uint8_t>{0x05, 0x00}));
  Socks5Parser::MakeMethodSelection(false, reply);
  EXPECT_EQ(reply, (nsTArray<uint8_t>{0x05, 0xff}));
  Socks5Parser::MakeUsernamePasswordMethodSelection(reply);
  EXPECT_EQ(reply, (nsTArray<uint8_t>{0x05, 0x02}));
  Socks5Parser::MakeAuthenticationReply(true, reply);
  EXPECT_EQ(reply, (nsTArray<uint8_t>{0x01, 0x00}));
  Socks5Parser::MakeAuthenticationReply(false, reply);
  EXPECT_EQ(reply, (nsTArray<uint8_t>{0x01, 0xff}));

  Socks5Parser::MakeReply(0x07, reply);
  EXPECT_EQ(reply, (nsTArray<uint8_t>{0x05, 0x07, 0x00, 0x01, 0x00, 0x00, 0x00,
                                      0x00, 0x00, 0x00}));
}

}  // namespace

}  // namespace mozilla::naivefox
