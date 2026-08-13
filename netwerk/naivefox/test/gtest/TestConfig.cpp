/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "Config.h"
#include "gtest/gtest.h"

namespace mozilla::naivefox {

TEST(NaiveFoxConfig, StringListenerAndHttpsDefaults)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://user:pass@example.com"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mListeners.Length(), 1U);
  EXPECT_EQ(config.mListeners[0].mType, ListenerType::Socks5);
  EXPECT_TRUE(config.mListeners[0].mHost.EqualsLiteral("127.0.0.1"));
  EXPECT_EQ(config.mListeners[0].mPort, 1080);
  EXPECT_EQ(config.mProtocol, ProxyProtocol::H2);
  EXPECT_TRUE(config.mProxyUrl.EqualsLiteral("https://example.com:443"));
  EXPECT_TRUE(config.mProxyUser.EqualsLiteral("user"));
  EXPECT_TRUE(config.mProxyPassword.EqualsLiteral("pass"));
  EXPECT_EQ(config.mLogMode, RuntimeLogMode::Disabled);
}

TEST(NaiveFoxConfig, MixedListenersQuicAndConsoleLog)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://127.0.0.1:1080","http://127.0.0.1:8080"],"proxy":"quic://usr:pwd@192.0.2.1:8443","log":""})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mListeners.Length(), 2U);
  EXPECT_EQ(config.mListeners[0].mType, ListenerType::Socks5);
  EXPECT_EQ(config.mListeners[1].mType, ListenerType::HttpConnect);
  EXPECT_EQ(config.mProtocol, ProxyProtocol::H3);
  EXPECT_TRUE(config.mProxyUrl.EqualsLiteral("https://192.0.2.1:8443"));
  EXPECT_EQ(config.mLogMode, RuntimeLogMode::Console);
}

TEST(NaiveFoxConfig, IPv6AndFileLog)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"http://[::1]:8080","proxy":"https://user:pass@[2001:db8::1]:9443","log":"/tmp/naivefox.log"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mListeners.Length(), 1U);
  EXPECT_TRUE(config.mListeners[0].mIPv6);
  EXPECT_TRUE(config.mListeners[0].mHost.EqualsLiteral("::1"));
  EXPECT_TRUE(config.mProxyUrl.EqualsLiteral("https://[2001:db8::1]:9443"));
  EXPECT_EQ(config.mLogMode, RuntimeLogMode::File);
  EXPECT_TRUE(config.mLogPath.EqualsLiteral("/tmp/naivefox.log"));
}

TEST(NaiveFoxConfig, PercentEncodedCredentials)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://localhost:1080","proxy":"quic://user%40name:p%3A%2Fss@proxy.example"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_TRUE(config.mProxyUser.EqualsLiteral("user@name"));
  EXPECT_TRUE(config.mProxyPassword.EqualsLiteral("p:/ss"));
}

TEST(NaiveFoxConfig, RejectsMalformedAndWrongTypes)
{
  static constexpr const char* kInvalid[] = {
      R"({)",
      R"({"listen":"socks://127.0.0.1:1080"})",
      R"({"proxy":"https://u:p@example.com"})",
      R"({"listen":42,"proxy":"https://u:p@example.com"})",
      R"({"listen":["socks://127.0.0.1:1080",42],"proxy":"https://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":false})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@example.com","log":true})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@example.com","extra":"x"})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty());
  }
}

TEST(NaiveFoxConfig, RejectsUnsupportedAndUnsafeUris)
{
  static constexpr const char* kInvalid[] = {
      R"({"listen":"ftp://127.0.0.1:1080","proxy":"https://u:p@example.com"})",
      R"({"listen":"socks://0.0.0.0:1080","proxy":"https://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1","proxy":"https://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"http://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@example.com/path"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:%zz@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@bad_host"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@-bad.example"})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty());
  }
}

}  // namespace mozilla::naivefox
