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
  ASSERT_EQ(config.mProxies.Length(), 1U);
  EXPECT_EQ(config.mProxies[0].mProtocol, ProxyProtocol::H2);
  EXPECT_TRUE(config.mProxies[0].mUrl.EqualsLiteral("https://example.com:443"));
  EXPECT_TRUE(config.mProxies[0].mUser.EqualsLiteral("user"));
  EXPECT_TRUE(config.mProxies[0].mPassword.EqualsLiteral("pass"));
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
  ASSERT_EQ(config.mProxies.Length(), 1U);
  EXPECT_EQ(config.mProxies[0].mProtocol, ProxyProtocol::H3);
  EXPECT_TRUE(config.mProxies[0].mUrl.EqualsLiteral("https://192.0.2.1:8443"));
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
  ASSERT_EQ(config.mProxies.Length(), 1U);
  EXPECT_TRUE(
      config.mProxies[0].mUrl.EqualsLiteral("https://[2001:db8::1]:9443"));
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
  ASSERT_EQ(config.mProxies.Length(), 1U);
  EXPECT_TRUE(config.mProxies[0].mUser.EqualsLiteral("user@name"));
  EXPECT_TRUE(config.mProxies[0].mPassword.EqualsLiteral("p:/ss"));
}

TEST(NaiveFoxConfig, NonLoopbackAndWildcardListeners)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://0.0.0.0:1080","http://192.168.1.1:8080","http://[::]:8081"],"proxy":"https://user:pass@example.com"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mListeners.Length(), 3U);
  EXPECT_TRUE(config.mListeners[0].mHost.EqualsLiteral("0.0.0.0"));
  EXPECT_TRUE(config.mListeners[1].mHost.EqualsLiteral("192.168.1.1"));
  EXPECT_TRUE(config.mListeners[2].mHost.EqualsLiteral("::"));
  EXPECT_TRUE(config.mListeners[2].mIPv6);
}

TEST(NaiveFoxConfig, ProxyArrayMapsOneToOneToListeners)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://0.0.0.0:1080","http://0.0.0.0:8080"],"proxy":["https://first:secret@one.example","quic://second:secret@two.example:8443"]})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mProxies.Length(), 2U);
  EXPECT_TRUE(config.mProxies[0].mUrl.EqualsLiteral("https://one.example:443"));
  EXPECT_EQ(config.mProxies[0].mProtocol, ProxyProtocol::H2);
  EXPECT_TRUE(
      config.mProxies[1].mUrl.EqualsLiteral("https://two.example:8443"));
  EXPECT_EQ(config.mProxies[1].mProtocol, ProxyProtocol::H3);
}

TEST(NaiveFoxConfig, SingleProxyArrayIsShared)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://127.0.0.1:1080","http://127.0.0.1:8080"],"proxy":["https://user:pass@example.com"]})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mProxies.Length(), 1U);
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
      R"({"listen":"socks://127.0.0.1:1080","proxy":[]})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":[42]})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":["https://u:p@one.example","https://u:p@two.example"]})",
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
      R"({"listen":"socks://proxy.example:1080","proxy":"https://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1","proxy":"https://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"http://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@example.com/path"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:%zz@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@bad_host"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@-bad.example"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://u:p@one.example,https://u:p@two.example"})",
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
