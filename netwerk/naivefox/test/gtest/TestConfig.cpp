/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdlib>
#include <filesystem>
#include <string>
#include <tuple>
#include <utility>

#include "Config.h"
#include "gtest/gtest.h"

namespace mozilla::naivefox {

namespace {

class ScopedEnvironment final {
 public:
  ScopedEnvironment(const char* aName, const char* aValue) : mName(aName) {
    const char* current = std::getenv(aName);
    if (current) {
      mHadValue = true;
      mValue = current;
    }
    if (aValue) {
      ::setenv(aName, aValue, 1);
    } else {
      ::unsetenv(aName);
    }
  }

  ~ScopedEnvironment() {
    if (mHadValue) {
      ::setenv(mName.c_str(), mValue.c_str(), 1);
    } else {
      ::unsetenv(mName.c_str());
    }
  }

 private:
  std::string mName;
  std::string mValue;
  bool mHadValue = false;
};

class ScopedTestDirectory final {
 public:
  ScopedTestDirectory() {
    std::error_code error;
    std::string name = (std::filesystem::temp_directory_path(error) /
                        "naivefox-config-test-XXXXXX")
                           .string();
    if (error) {
      return;
    }
    name.push_back('\0');
    if (char* created = ::mkdtemp(name.data())) {
      mPath = created;
    }
  }

  ~ScopedTestDirectory() {
    std::error_code error;
    std::filesystem::remove_all(mPath, error);
  }

  const std::filesystem::path& Path() const { return mPath; }

 private:
  std::filesystem::path mPath;
};

}  // namespace

TEST(NaiveFoxConfig, RejectsOversizedStringInput)
{
  nsCString json;
  ASSERT_TRUE(json.SetLength(1024 * 1024 + 1, fallible));
  Config config;
  nsAutoCString error;
  EXPECT_EQ(ParseConfig(json, config, error), NS_ERROR_FILE_TOO_BIG);
  EXPECT_STREQ(error.get(), "config is too large");
}

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
  EXPECT_TRUE(config.mListeners[0].mUser.IsEmpty());
  EXPECT_TRUE(config.mListeners[0].mPassword.IsEmpty());
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

TEST(NaiveFoxConfig, OptionalUpstreamCredentials)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://127.0.0.1:1080","http://127.0.0.1:8080"],"proxy":["https://proxy.example","quic://[2001:db8::1]:8443"]})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mProxies.Length(), 2U);
  EXPECT_TRUE(config.mProxies[0].mUrl.EqualsLiteral("https://proxy.example:443"));
  EXPECT_TRUE(config.mProxies[0].mUser.IsEmpty());
  EXPECT_TRUE(config.mProxies[0].mPassword.IsEmpty());
  EXPECT_EQ(config.mProxies[0].mProtocol, ProxyProtocol::H2);
  EXPECT_TRUE(
      config.mProxies[1].mUrl.EqualsLiteral("https://[2001:db8::1]:8443"));
  EXPECT_TRUE(config.mProxies[1].mUser.IsEmpty());
  EXPECT_TRUE(config.mProxies[1].mPassword.IsEmpty());
  EXPECT_EQ(config.mProxies[1].mProtocol, ProxyProtocol::H3);
}

TEST(NaiveFoxConfig, SocksListenerCredentials)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://user%40name:p%3A%2Fss@127.0.0.1:1080","proxy":"https://proxy.example"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mListeners.Length(), 1U);
  EXPECT_TRUE(config.mListeners[0].mUser.EqualsLiteral("user@name"));
  EXPECT_TRUE(config.mListeners[0].mPassword.EqualsLiteral("p:/ss"));
  EXPECT_TRUE(config.mListeners[0].mHost.EqualsLiteral("127.0.0.1"));
  EXPECT_EQ(config.mListeners[0].mPort, 1080);
}

TEST(NaiveFoxConfig, HostResolverRule)
{
  for (const auto& [rule, logical, physical] : {
           std::tuple{"MAP proxy.example.com 127.0.0.1", "proxy.example.com",
                      "127.0.0.1"},
           std::tuple{"  MAP\\tproxy.example.com\\t::1  ", "proxy.example.com",
                      "::1"},
           std::tuple{"MAP proxy.example.com localhost", "proxy.example.com",
                      "localhost"},
           std::tuple{"MAP proxy.example.com backend.example.com",
                      "proxy.example.com", "backend.example.com"},
       }) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","host-resolver-rules":")"_ns);
    json.Append(rule);
    json.AppendLiteral("\"}");
    Config config;
    nsAutoCString error;
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK)
        << rule << ": " << error.get();
    ASSERT_TRUE(config.mHostResolverRule.isSome());
    EXPECT_TRUE(config.mHostResolverRule->mLogicalHost.Equals(logical));
    EXPECT_TRUE(config.mHostResolverRule->mPhysicalHost.Equals(physical));
  }
}

TEST(NaiveFoxConfig, ExtraHeaders)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"X-Test: value\r\nX-Another:\tvalue2  \r\n"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mExtraHeaders.Length(), 2U);
  EXPECT_TRUE(config.mExtraHeaders[0].mName.EqualsLiteral("X-Test"));
  EXPECT_TRUE(config.mExtraHeaders[0].mValue.EqualsLiteral("value"));
  EXPECT_TRUE(config.mExtraHeaders[1].mName.EqualsLiteral("X-Another"));
  EXPECT_TRUE(config.mExtraHeaders[1].mValue.EqualsLiteral("value2"));

  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":""})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_TRUE(config.mExtraHeaders.IsEmpty());
}

TEST(NaiveFoxConfig, NoPostQuantumBoolean)
{
  for (const auto& [value, expected] : {
           std::pair{"true", true},
           std::pair{"false", false},
       }) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","no-post-quantum":)"_ns);
    json.Append(value);
    json.Append('}');
    Config config;
    nsAutoCString error;
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK) << error.get();
    EXPECT_EQ(config.mNoPostQuantum, expected);
  }

  Config config;
  nsAutoCString error;
  ASSERT_EQ(ParseConfig(
                R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example"})"_ns,
                config, error),
            NS_OK)
      << error.get();
  EXPECT_FALSE(config.mNoPostQuantum);
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
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com","host-resolver-rules":true})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com","extra-headers":[]})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com","no-post-quantum":"true"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com","host-resolver-rules":"MAP example.com 127.0.0.1","host-resolver-rules":"MAP example.com ::1"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com","extra-headers":"X-One: 1","extra-headers":"X-Two: 2"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://example.com","no-post-quantum":true,"no-post-quantum":false})",
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
      R"({"listen":"http://user:pass@127.0.0.1:8080","proxy":"https://example.com"})",
      R"({"listen":"socks://user@127.0.0.1:1080","proxy":"https://example.com"})",
      R"({"listen":"socks://:pass@127.0.0.1:1080","proxy":"https://example.com"})",
      R"({"listen":"socks://user:@127.0.0.1:1080","proxy":"https://example.com"})",
      R"({"listen":"socks://user:%zz@127.0.0.1:1080","proxy":"https://example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"http://u:p@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://user@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://:pass@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://user:@example.com"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://@example.com"})",
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

TEST(NaiveFoxConfig, RejectsOversizedListenerCredentials)
{
  nsAutoCString json(
      R"({"listen":"socks://user:)"_ns);
  for (size_t index = 0; index < 256; ++index) {
    json.Append('p');
  }
  json.AppendLiteral(
      R"(@127.0.0.1:1080","proxy":"https://example.com"})");
  Config config;
  nsAutoCString error;
  EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error)));
  EXPECT_FALSE(error.IsEmpty());
}

TEST(NaiveFoxConfig, RejectsUnsupportedHostResolverRules)
{
  static constexpr const char* kInvalidRules[] = {
      "",
      "MAP",
      "MAP proxy.example.com",
      "MAP proxy.example.com 127.0.0.1 extra",
      "map proxy.example.com 127.0.0.1",
      "EXCLUDE proxy.example.com",
      "MAP *.example.com 127.0.0.1",
      "MAP proxy.example.com bad_host",
      "MAP proxy.example.com [::1]",
      "MAP proxy.example.com 127.0.0.1,MAP other.example.com 127.0.0.2",
      "MAP proxy.example.com\n127.0.0.1",
  };
  for (const char* rule : kInvalidRules) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","host-resolver-rules":")"_ns);
    const nsDependentCString ruleText(rule);
    for (size_t index = 0; index < ruleText.Length(); ++index) {
      const char value = ruleText.CharAt(index);
      if (value == '\n') {
        json.AppendLiteral("\\n");
      } else {
        json.Append(value);
      }
    }
    json.AppendLiteral("\"}");
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error))) << rule;
    EXPECT_FALSE(error.IsEmpty()) << rule;
  }
}

TEST(NaiveFoxConfig, RejectsInvalidExtraHeaders)
{
  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"Missing-Colon"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"Bad Name: value"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"X-One: 1\nX-Two: 2"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"X-One: 1\rX-Two: 2"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"X-One: 1\r\n\r\nX-Two: 2"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"X-One: 1\r\nx-one: 2"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":"X-Test: value\u0001"})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }

  static constexpr const char* kProtected[] = {
      "padding",           "Host",               "Connection",
      "Proxy-Connection", "Keep-Alive",         "Transfer-Encoding",
      "TE",                "Trailer",            "Upgrade",
      "Content-Length",    "Proxy-Authorization", "Proxy-Authenticate",
      "ALPN",
  };
  for (const char* name : kProtected) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","extra-headers":")"_ns);
    json.Append(name);
    json.AppendLiteral(": value\"}");
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error))) << name;
    EXPECT_TRUE(error.EqualsLiteral(
        "extra-headers contains a protected header name"))
        << name << ": " << error.get();
  }
}

TEST(NaiveFoxConfig, TemporaryProfileWithoutHome)
{
  ScopedTestDirectory root;
  ASSERT_FALSE(root.Path().empty());
  const std::filesystem::path temporaryRoot = root.Path() / "runtime";
  ASSERT_TRUE(std::filesystem::create_directory(temporaryRoot));

  ScopedEnvironment profileOverride("NAIVEFOX_PROFILE", nullptr);
  ScopedEnvironment stateHome("XDG_STATE_HOME", nullptr);
  ScopedEnvironment home("HOME", nullptr);
  ScopedEnvironment runtimeHome("XDG_RUNTIME_DIR", nullptr);
  const std::string nativeTemporaryRoot = temporaryRoot.string();
  ScopedEnvironment temporaryHome("TMPDIR", nativeTemporaryRoot.c_str());

  std::filesystem::path profilePath;
  {
    ProfileDirectory profile;
    nsAutoCString error;
    ASSERT_EQ(ResolveAndCreateProfile(profile, error), NS_OK) << error.get();
    EXPECT_TRUE(profile.IsTemporary());
    profilePath = PromiseFlatCString(profile.Path()).get();
    EXPECT_EQ(profilePath.parent_path(), temporaryRoot);
    EXPECT_TRUE(std::filesystem::is_directory(profilePath));
    const auto permissions = std::filesystem::status(profilePath).permissions();
    EXPECT_EQ(permissions & std::filesystem::perms::all,
              std::filesystem::perms::owner_all);
  }
  EXPECT_FALSE(std::filesystem::exists(profilePath));
}

TEST(NaiveFoxConfig, TemporaryProfileIsTheDefault)
{
  ScopedTestDirectory root;
  ASSERT_FALSE(root.Path().empty());
  const std::filesystem::path temporaryRoot = root.Path() / "runtime";
  const std::filesystem::path stateRoot = root.Path() / "state";
  const std::filesystem::path homeRoot = root.Path() / "home";
  ASSERT_TRUE(std::filesystem::create_directory(temporaryRoot));
  ASSERT_TRUE(std::filesystem::create_directory(stateRoot));
  ASSERT_TRUE(std::filesystem::create_directory(homeRoot));

  ScopedEnvironment profileOverride("NAIVEFOX_PROFILE", nullptr);
  const std::string state = stateRoot.string();
  const std::string home = homeRoot.string();
  const std::string temporary = temporaryRoot.string();
  ScopedEnvironment stateHome("XDG_STATE_HOME", state.c_str());
  ScopedEnvironment homeEnvironment("HOME", home.c_str());
  ScopedEnvironment runtimeHome("XDG_RUNTIME_DIR", nullptr);
  ScopedEnvironment temporaryHome("TMPDIR", temporary.c_str());

  std::filesystem::path profilePath;
  {
    ProfileDirectory profile;
    nsAutoCString error;
    ASSERT_EQ(ResolveAndCreateProfile(profile, error), NS_OK) << error.get();
    EXPECT_TRUE(profile.IsTemporary());
    profilePath = PromiseFlatCString(profile.Path()).get();
    EXPECT_EQ(profilePath.parent_path(), temporaryRoot);
    EXPECT_TRUE(std::filesystem::is_directory(profilePath));
  }
  EXPECT_FALSE(std::filesystem::exists(profilePath));
  EXPECT_FALSE(std::filesystem::exists(stateRoot / "naivefox" / "profile"));
  EXPECT_FALSE(std::filesystem::exists(homeRoot / ".local" / "state" /
                                       "naivefox" / "profile"));
}

TEST(NaiveFoxConfig, ExplicitProfileRemainsPersistent)
{
  ScopedTestDirectory root;
  ASSERT_FALSE(root.Path().empty());
  const std::filesystem::path persistent = root.Path() / "profile";
  const std::string persistentString = persistent.string();
  ScopedEnvironment profileOverride("NAIVEFOX_PROFILE",
                                    persistentString.c_str());

  ProfileDirectory profile;
  nsAutoCString error;
  ASSERT_EQ(ResolveAndCreateProfile(profile, error), NS_OK) << error.get();
  EXPECT_FALSE(profile.IsTemporary());
  EXPECT_EQ(PromiseFlatCString(profile.Path()).get(), persistentString);
  EXPECT_TRUE(std::filesystem::is_directory(persistent));
}

}  // namespace mozilla::naivefox
