/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdlib>
#include <filesystem>
#include <string>
#include <tuple>
#include <utility>

#include "Config.h"
#include "NeckoTunnel.h"
#include "TunnelSession.h"
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
  EXPECT_EQ(config.mPreamble.mMode, PreambleMode::Off);
  EXPECT_TRUE(config.mPreamble.mPath.EqualsLiteral("/"));
  EXPECT_EQ(config.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(config.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentFirstBufferTaskOverlap);
  EXPECT_EQ(config.mPreamble.mMaxBytes,
            PreambleConfig::kDefaultDocumentMaxBytes);
  EXPECT_EQ(config.mMaxConnections, 0U);
  EXPECT_FALSE(config.mOuterSessionGate);
  EXPECT_TRUE(config.mImplicitPreambleGate);
  EXPECT_FALSE(config.mDiagnosticFirstSocksTunnelUrgentStart);
  EXPECT_FALSE(config.mDiagnosticOptimisticLocalReply);
}

TEST(NaiveFoxConfig, OmittedPreamblePromotesExplicitProtocols)
{
  nsAutoCString error;
  Config implicitHttpConnect;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"http://127.0.0.1:1080","proxy":"https://proxy.example"})"_ns,
          implicitHttpConnect, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(implicitHttpConnect.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentFirstBufferOverlap);
  EXPECT_TRUE(implicitHttpConnect.mImplicitPreambleGate);

  Config implicitHttpConnectH3;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"http://127.0.0.1:1080","proxy":"quic://proxy.example"})"_ns,
          implicitHttpConnectH3, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(implicitHttpConnectH3.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserResourceCommittedOverlap);
  EXPECT_TRUE(implicitHttpConnectH3.mPreamble.mPath.EqualsLiteral("/"));
  EXPECT_EQ(implicitHttpConnectH3.mPreamble.mMaxAssets, 6U);
  EXPECT_EQ(implicitHttpConnectH3.mPreamble.mMaxBytes,
            PreambleConfig::kMaximumBytes);
  EXPECT_TRUE(implicitHttpConnectH3.mPreamble.mCacheResources);

  Config implicitMixedH2;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://127.0.0.1:1080","http://127.0.0.1:8080"],"proxy":"https://proxy.example"})"_ns,
          implicitMixedH2, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(implicitMixedH2.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentFirstBufferOverlap);

  Config implicitMixedH3;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://127.0.0.1:1080","http://127.0.0.1:8080"],"proxy":"quic://proxy.example"})"_ns,
          implicitMixedH3, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(implicitMixedH3.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserResourceCommittedOverlap);
  EXPECT_EQ(implicitMixedH3.mPreamble.mMaxAssets, 6U);
  EXPECT_EQ(implicitMixedH3.mPreamble.mMaxBytes, PreambleConfig::kMaximumBytes);
  EXPECT_TRUE(implicitMixedH3.mPreamble.mCacheResources);

  Config implicitQuic;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"quic://proxy.example"})"_ns,
          implicitQuic, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(implicitQuic.mPreamble.mMode, PreambleMode::Off);
  EXPECT_EQ(implicitQuic.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::Off);
  EXPECT_EQ(implicitQuic.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserResourceCommittedOverlap);
  EXPECT_TRUE(implicitQuic.mPreamble.mPath.EqualsLiteral("/"));
  EXPECT_EQ(implicitQuic.mPreamble.mMaxAssets, 6U);
  EXPECT_EQ(implicitQuic.mPreamble.mMaxBytes, PreambleConfig::kMaximumBytes);
  EXPECT_TRUE(implicitQuic.mPreamble.mCacheResources);
  EXPECT_FALSE(implicitQuic.mOuterSessionGate);
  EXPECT_TRUE(implicitQuic.mImplicitPreambleGate);

  Config explicitOff;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"quic://proxy.example","preamble":{"mode":"off"}})"_ns,
          explicitOff, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(explicitOff.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::Off);
  EXPECT_EQ(explicitOff.mPreamble.mMaxBytes, 0U);
  EXPECT_FALSE(explicitOff.mOuterSessionGate);
  EXPECT_FALSE(explicitOff.mImplicitPreambleGate);

  Config explicitGateOnly;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"quic://proxy.example","preamble":{"mode":"off"},"outer-session-gate":true})"_ns,
          explicitGateOnly, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(explicitGateOnly.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::Off);
  EXPECT_TRUE(explicitGateOnly.mOuterSessionGate);
  EXPECT_FALSE(explicitGateOnly.mImplicitPreambleGate);

  Config mixedProtocols;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":["socks://127.0.0.1:1080","http://127.0.0.1:8080"],"proxy":["https://h2.example","quic://h3.example"]})"_ns,
          mixedProtocols, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(mixedProtocols.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentFirstBufferOverlap);
  EXPECT_EQ(mixedProtocols.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserResourceCommittedOverlap);
  EXPECT_EQ(mixedProtocols.mPreamble.mMaxAssets, 6U);
  EXPECT_EQ(mixedProtocols.mPreamble.mMaxBytes, PreambleConfig::kMaximumBytes);
  EXPECT_TRUE(mixedProtocols.mPreamble.mCacheResources);
  EXPECT_TRUE(mixedProtocols.mImplicitPreambleGate);

  for (const auto& [value, expected] :
       {std::pair{"false", false}, std::pair{"true", true}}) {
    for (const auto& [proxy, protocol] :
         {std::pair{"https://proxy.example", ProxyProtocol::H2},
          std::pair{"quic://proxy.example", ProxyProtocol::H3}}) {
      Config explicitGate;
      error.Truncate();
      nsAutoCString json(R"({"listen":"socks://127.0.0.1:1080","proxy":")"_ns);
      json.Append(proxy);
      json.Append(R"(","outer-session-gate":)"_ns);
      json.Append(value);
      json.Append('}');
      ASSERT_EQ(ParseConfig(json, explicitGate, error), NS_OK) << error.get();
      EXPECT_EQ(explicitGate.mPreamble.ModeForProtocol(protocol),
                protocol == ProxyProtocol::H2
                    ? PreambleMode::DocumentFirstBufferTaskOverlap
                    : PreambleMode::TreeNativeParserResourceCommittedOverlap);
      EXPECT_EQ(explicitGate.mOuterSessionGate, expected);
      EXPECT_FALSE(explicitGate.mImplicitPreambleGate);
    }
  }
}

TEST(NaiveFoxConfig, MaxConnectionsIsBoundedAndExplicit)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","max-connections":1})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(config.mMaxConnections, 1U);

  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","max-connections":-1})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","max-connections":"1"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","max-connections":4294967296})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","max-connections":1,"max-connections":2})",
  };
  for (const char* json : kInvalid) {
    Config invalid;
    error.Truncate();
    EXPECT_TRUE(
        NS_FAILED(ParseConfig(nsDependentCString(json), invalid, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, OuterSessionGateBoolean)
{
  for (const auto& [value, expected] :
       {std::pair{"true", true}, std::pair{"false", false}}) {
    Config config;
    nsAutoCString error;
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","outer-session-gate":)"_ns);
    json.Append(value);
    json.Append('}');
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK) << error.get();
    EXPECT_EQ(config.mOuterSessionGate, expected);
  }
}

TEST(NaiveFoxConfig, RejectsInvalidOuterSessionGate)
{
  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","outer-session-gate":null})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","outer-session-gate":1})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","outer-session-gate":"true"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","outer-session-gate":true,"outer-session-gate":false})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, DiagnosticFirstSocksTunnelUrgentStartBoolean)
{
  for (const auto& [value, expected] :
       {std::pair{"true", true}, std::pair{"false", false}}) {
    Config config;
    nsAutoCString error;
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-first-socks-tunnel-urgent-start":)"_ns);
    json.Append(value);
    json.Append('}');
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK) << error.get();
    EXPECT_EQ(config.mDiagnosticFirstSocksTunnelUrgentStart, expected);
  }
}

TEST(NaiveFoxConfig, RejectsInvalidDiagnosticFirstSocksTunnelUrgentStart)
{
  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-first-socks-tunnel-urgent-start":null})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-first-socks-tunnel-urgent-start":1})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-first-socks-tunnel-urgent-start":"true"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-first-socks-tunnel-urgent-start":true,"diagnostic-first-socks-tunnel-urgent-start":false})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, DiagnosticOptimisticLocalReplyBoolean)
{
  for (const auto& [value, expected] :
       {std::pair{"true", true}, std::pair{"false", false}}) {
    Config config;
    nsAutoCString error;
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-optimistic-local-reply":)"_ns);
    json.Append(value);
    json.Append('}');
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK) << error.get();
    EXPECT_EQ(config.mDiagnosticOptimisticLocalReply, expected);
  }
}

TEST(NaiveFoxConfig, RejectsInvalidDiagnosticOptimisticLocalReply)
{
  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-optimistic-local-reply":null})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-optimistic-local-reply":1})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-optimistic-local-reply":"true"})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-optimistic-local-reply":true,"diagnostic-optimistic-local-reply":false})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, FiniteExchangeDiagnosticIsExplicitAndH2Only)
{
  Config config;
  nsAutoCString error;
  EXPECT_FALSE(config.mDiagnosticH2FiniteExchanges);
  EXPECT_FALSE(config.mDiagnosticH2FiniteReadThrough);
  EXPECT_FALSE(config.mDiagnosticH2FiniteStreamUploads);
  EXPECT_FALSE(config.mDiagnosticH2FiniteBudgetedDownloads);
  for (const char* listener : {"socks", "http"}) {
    nsAutoCString json("{\"listen\":\""_ns);
    json.Append(listener);
    json.AppendLiteral(
        "://127.0.0.1:1080\",\"proxy\":\"https://proxy.example\","
        "\"diagnostic-h2-finite-exchanges\":true}");
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK) << error.get();
    EXPECT_TRUE(config.mDiagnosticH2FiniteExchanges);
    EXPECT_FALSE(config.mDiagnosticH2FiniteReadThrough);
    EXPECT_EQ(config.mPreamble.ModeForProtocol(ProxyProtocol::H2),
              nsDependentCString(listener).EqualsLiteral("socks")
                  ? PreambleMode::DocumentFirstBufferTaskOverlap
                  : PreambleMode::DocumentFirstBufferOverlap);
  }
  for (
      const char* tail :
      {R"("diagnostic-h2-finite-exchanges":null)",
       R"("diagnostic-h2-finite-exchanges":1)",
       R"("diagnostic-h2-finite-exchanges":"true")",
       R"("diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-exchanges":false)",
       R"("diagnostic-h2-finite-exchanges":true,"diagnostic-optimistic-local-reply":true)"}) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example",)"_ns);
    json.Append(tail);
    json.Append('}');
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error)));
  }
  EXPECT_TRUE(NS_FAILED(ParseConfig(
      R"({"listen":"socks://127.0.0.1:1080","proxy":"quic://proxy.example","diagnostic-h2-finite-exchanges":true})"_ns,
      config, error)));
}

TEST(NaiveFoxConfig, FiniteReadThroughRequiresExplicitFiniteTransport)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":true})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_TRUE(config.mDiagnosticH2FiniteExchanges);
  EXPECT_TRUE(config.mDiagnosticH2FiniteReadThrough);
  for (
      const char* tail :
      {R"("diagnostic-h2-finite-read-through":true)",
       R"("diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-exchanges":false)",
       R"("diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":1)",
       R"("diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":"true")",
       R"("diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":null)",
       R"("diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-read-through":false)"}) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example",)"_ns);
    json.Append(tail);
    json.Append('}');
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error)));
  }
  EXPECT_TRUE(NS_FAILED(ParseConfig(
      R"({"listen":"socks://127.0.0.1:1080","proxy":"quic://proxy.example","diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":true})"_ns,
      config, error)));
}

TEST(NaiveFoxConfig, FiniteUploadStreamingRequiresBothReadThroughDirections)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"http://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-stream-uploads":true})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_TRUE(config.mDiagnosticH2FiniteStreamUploads);
  for (
      const char* tail :
      {R"("diagnostic-h2-finite-stream-uploads":true)",
       R"("diagnostic-h2-finite-read-through":false,"diagnostic-h2-finite-stream-uploads":true)",
       R"("diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-stream-uploads":null)",
       R"("diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-stream-uploads":1)",
       R"("diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-stream-uploads":true,"diagnostic-h2-finite-stream-uploads":false)"}) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-h2-finite-exchanges":true,)"_ns);
    json.Append(tail);
    json.Append('}');
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error)));
  }
}

TEST(NaiveFoxConfig, BudgetedFiniteDownloadsRequireBidirectionalReadThrough)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":true,"diagnostic-h2-finite-stream-uploads":true,"diagnostic-h2-finite-budgeted-downloads":true})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_TRUE(config.mDiagnosticH2FiniteBudgetedDownloads);
  for (
      const char* tail :
      {R"("diagnostic-h2-finite-budgeted-downloads":true)",
       R"("diagnostic-h2-finite-stream-uploads":false,"diagnostic-h2-finite-budgeted-downloads":true)",
       R"("diagnostic-h2-finite-stream-uploads":true,"diagnostic-h2-finite-budgeted-downloads":null)",
       R"("diagnostic-h2-finite-stream-uploads":true,"diagnostic-h2-finite-budgeted-downloads":1)",
       R"("diagnostic-h2-finite-stream-uploads":true,"diagnostic-h2-finite-budgeted-downloads":true,"diagnostic-h2-finite-budgeted-downloads":false)"}) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","diagnostic-h2-finite-exchanges":true,"diagnostic-h2-finite-read-through":true,)"_ns);
    json.Append(tail);
    json.Append('}');
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error)));
  }
}

TEST(NaiveFoxConfig, PreambleModesAndBudgets)
{
  struct Expected {
    const char* mJson;
    PreambleMode mMode;
    const char* mPath;
    uint32_t mMaxAssets;
    uint32_t mMaxBytes;
  };
  static constexpr Expected kExpected[] = {
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off"}})",
       PreambleMode::Off, "/", 0, 0},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/"}})",
       PreambleMode::Root, "/", 0, 64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/camouflage/index.html"}})",
       PreambleMode::Tree, "/camouflage/index.html", 2, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","path":"/camouflage/"}})",
       PreambleMode::DocumentComplete, "/camouflage/", 0, 64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-overlap","path":"/camouflage/"}})",
       PreambleMode::DocumentOverlap, "/camouflage/", 0, 64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-first-buffer-overlap","path":"/camouflage/"}})",
       PreambleMode::DocumentFirstBufferOverlap, "/camouflage/", 0, 64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-first-buffer-task-overlap","path":"/camouflage/"}})",
       PreambleMode::DocumentFirstBufferTaskOverlap, "/camouflage/", 0,
       64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-start-overlap","path":"/camouflage/"}})",
       PreambleMode::DocumentStartOverlap, "/camouflage/", 0, 64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-complete","path":"/camouflage/"}})",
       PreambleMode::TreeComplete, "/camouflage/", 2, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-overlap","path":"/camouflage/"}})",
       PreambleMode::TreeOverlap, "/camouflage/", 2, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-early-overlap","path":"/camouflage/"}})",
       PreambleMode::TreeEarlyOverlap, "/camouflage/", 2, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-early-overlap","path":"/camouflage/","max-assets":0}})",
       PreambleMode::TreeEarlyOverlap, "/camouflage/", 0, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-root-overlap","path":"/camouflage/"}})",
       PreambleMode::TreeRootOverlap, "/camouflage/", 2, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-root-overlap","path":"/camouflage/","max-assets":0}})",
       PreambleMode::TreeRootOverlap, "/camouflage/", 0, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-committed-overlap","path":"/camouflage/","max-assets":1}})",
       PreambleMode::Off, "/camouflage/", 1, 256 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-committed-overlap","path":"/camouflage/","max-assets":3,"max-bytes":131072}})",
       PreambleMode::Off, "/camouflage/", 3, 128 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-committed-overlap","path":"/camouflage/","max-assets":6,"max-bytes":393216}})",
       PreambleMode::Off, "/camouflage/", 6, 384 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"path":"/a%20b","max-bytes":393216,"max-assets":6,"mode":"tree"}})",
       PreambleMode::Tree, "/a%20b", 6, 384 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/page?scenario=browser_page&completion=0123456789abcdef"}})",
       PreambleMode::Root,
       "/page?scenario=browser_page&completion=0123456789abcdef", 0, 64 * 1024},
      {R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/health","max-assets":0,"max-bytes":1}})",
       PreambleMode::Root, "/health", 0, 1},
  };

  for (const auto& expected : kExpected) {
    Config config;
    nsAutoCString error;
    ASSERT_EQ(ParseConfig(nsDependentCString(expected.mJson), config, error),
              NS_OK)
        << expected.mJson << ": " << error.get();
    EXPECT_EQ(config.mPreamble.mMode, expected.mMode);
    EXPECT_TRUE(config.mPreamble.mPath.Equals(expected.mPath));
    EXPECT_EQ(config.mPreamble.mMaxAssets, expected.mMaxAssets);
    EXPECT_EQ(config.mPreamble.mMaxBytes, expected.mMaxBytes);
    EXPECT_FALSE(config.mPreamble.mCacheResources);
  }
}

TEST(NaiveFoxConfig, PreambleResourceCacheIsExplicitAndTreeOnly)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","h3-mode":"tree-root-overlap","path":"/camouflage/","cache-resources":true}})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_TRUE(config.mPreamble.mCacheResources);
  EXPECT_FALSE(config.mPreamble.CacheResourcesForProtocol(ProxyProtocol::H2));
  EXPECT_TRUE(config.mPreamble.CacheResourcesForProtocol(ProxyProtocol::H3));

  Config nativeCacheCommitted;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-native-cache-committed-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeCacheCommitted, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeCacheCommitted.mPreamble.mMode, PreambleMode::Off);
  EXPECT_EQ(nativeCacheCommitted.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeResourceNativeCacheCommittedOverlap);
  EXPECT_EQ(nativeCacheCommitted.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeCacheCommitted.mPreamble.mCacheResources);

  Config nativeParserPreload;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-preload-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserPreload, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserPreload.mPreamble.mMode, PreambleMode::Off);
  EXPECT_EQ(nativeParserPreload.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserPreloadOverlap);
  EXPECT_EQ(nativeParserPreload.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserPreload.mPreamble.mCacheResources);

  Config nativeParserDocumentStart;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserDocumentStart, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserDocumentStart.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::TreeNativeParserDocumentStartOverlap);
  EXPECT_EQ(nativeParserDocumentStart.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserDocumentStart.mPreamble.mCacheResources);

  Config nativeParserDocumentStartH2;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-document-start-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserDocumentStartH2, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserDocumentStartH2.mPreamble.ModeForProtocol(ProxyProtocol::H2),
      PreambleMode::TreeNativeParserDocumentStartOverlap);
  EXPECT_EQ(
      nativeParserDocumentStartH2.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::Off);
  EXPECT_EQ(nativeParserDocumentStartH2.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserDocumentStartH2.mPreamble.mCacheResources);

  Config nativeParserResourceTreeH2;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-document-start-resource-tree","path":"/camouflage/","max-assets":3,"max-bytes":131072,"cache-resources":true}})"_ns,
          nativeParserResourceTreeH2, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserResourceTreeH2.mPreamble.ModeForProtocol(ProxyProtocol::H2),
      PreambleMode::TreeNativeParserDocumentStartResourceTree);
  EXPECT_EQ(
      nativeParserResourceTreeH2.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::Off);
  EXPECT_EQ(nativeParserResourceTreeH2.mPreamble.mMaxAssets, 3U);
  EXPECT_EQ(nativeParserResourceTreeH2.mPreamble.mMaxBytes, 131072U);
  EXPECT_TRUE(nativeParserResourceTreeH2.mPreamble.mCacheResources);

  Config nativeParserResourceCommittedTreeH3;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-resource-committed-overlap","path":"/camouflage/","max-assets":3,"max-bytes":131072,"cache-resources":true}})"_ns,
          nativeParserResourceCommittedTreeH3, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserResourceCommittedTreeH3.mPreamble.ModeForProtocol(
                ProxyProtocol::H3),
            PreambleMode::TreeNativeParserResourceCommittedOverlap);
  EXPECT_EQ(nativeParserResourceCommittedTreeH3.mPreamble.mMaxAssets, 3U);
  EXPECT_EQ(nativeParserResourceCommittedTreeH3.mPreamble.mMaxBytes, 131072U);
  EXPECT_TRUE(nativeParserResourceCommittedTreeH3.mPreamble.mCacheResources);

  Config nativeParserResourceCommittedPageH2;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-resource-committed-overlap","path":"/camouflage/","max-assets":6,"max-bytes":393216,"cache-resources":true}})"_ns,
          nativeParserResourceCommittedPageH2, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserResourceCommittedPageH2.mPreamble.ModeForProtocol(
                ProxyProtocol::H2),
            PreambleMode::TreeNativeParserResourceCommittedOverlap);
  EXPECT_EQ(nativeParserResourceCommittedPageH2.mPreamble.ModeForProtocol(
                ProxyProtocol::H3),
            PreambleMode::Off);
  EXPECT_EQ(nativeParserResourceCommittedPageH2.mPreamble.mMaxAssets, 6U);
  EXPECT_EQ(nativeParserResourceCommittedPageH2.mPreamble.mMaxBytes, 393216U);
  EXPECT_TRUE(nativeParserResourceCommittedPageH2.mPreamble.mCacheResources);

  Config nativeParserDocumentStartNavigationStop;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-navigation-stop","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserDocumentStartNavigationStop, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserDocumentStartNavigationStop.mPreamble.ModeForProtocol(
                ProxyProtocol::H3),
            PreambleMode::TreeNativeParserDocumentStartNavigationStop);
  EXPECT_EQ(nativeParserDocumentStartNavigationStop.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(
      nativeParserDocumentStartNavigationStop.mPreamble.mCacheResources);

  Config nativeParserDocumentStartNavigationStopH2;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-document-start-navigation-stop","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserDocumentStartNavigationStopH2, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserDocumentStartNavigationStopH2.mPreamble.ModeForProtocol(
                ProxyProtocol::H2),
            PreambleMode::TreeNativeParserDocumentStartNavigationStop);
  EXPECT_EQ(nativeParserDocumentStartNavigationStopH2.mPreamble.ModeForProtocol(
                ProxyProtocol::H3),
            PreambleMode::Off);
  EXPECT_EQ(nativeParserDocumentStartNavigationStopH2.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(
      nativeParserDocumentStartNavigationStopH2.mPreamble.mCacheResources);

  Config nativeParserDocumentStartResponseStop;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-response-stop","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserDocumentStartResponseStop, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserDocumentStartResponseStop.mPreamble.ModeForProtocol(
                ProxyProtocol::H3),
            PreambleMode::TreeNativeParserDocumentStartResponseStop);
  EXPECT_EQ(nativeParserDocumentStartResponseStop.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserDocumentStartResponseStop.mPreamble.mCacheResources);

  Config nativeParserDocumentHandoff;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-handoff-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserDocumentHandoff, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserDocumentHandoff.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::TreeNativeParserDocumentHandoffOverlap);
  EXPECT_EQ(nativeParserDocumentHandoff.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserDocumentHandoff.mPreamble.mCacheResources);

  Config nativeParserRetarget;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-retarget-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserRetarget, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserRetarget.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserRetargetOverlap);
  EXPECT_EQ(nativeParserRetarget.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserRetarget.mPreamble.mCacheResources);

  Config nativeParserIpcRendezvous;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-ipc-rendezvous-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserIpcRendezvous, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserIpcRendezvous.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::TreeNativeParserIpcRendezvousOverlap);
  EXPECT_EQ(nativeParserIpcRendezvous.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserIpcRendezvous.mPreamble.mCacheResources);

  Config nativeParserRootRendezvous;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-root-rendezvous-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserRootRendezvous, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserRootRendezvous.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::TreeNativeParserRootRendezvousOverlap);
  EXPECT_EQ(nativeParserRootRendezvous.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserRootRendezvous.mPreamble.mCacheResources);

  Config nativeParserProcess;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-process-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserProcess, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(nativeParserProcess.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeNativeParserProcessOverlap);
  EXPECT_EQ(nativeParserProcess.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserProcess.mPreamble.mCacheResources);

  Config nativeParserFullProcess;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-full-process-overlap","path":"/camouflage/","max-assets":1,"cache-resources":true}})"_ns,
          nativeParserFullProcess, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      nativeParserFullProcess.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::TreeNativeParserFullProcessOverlap);
  EXPECT_EQ(nativeParserFullProcess.mPreamble.mMaxAssets, 1U);
  EXPECT_TRUE(nativeParserFullProcess.mPreamble.mCacheResources);

  static constexpr const char* kInvalid[] =
      {
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","cache-resources":false}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","path":"/","cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","path":"/","cache-resources":false}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","cache-resources":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","cache-resources":true,"cache-resources":false}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-resource-native-cache-committed-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-resource-native-cache-committed-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-native-cache-committed-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-native-cache-committed-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-preload-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-preload-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-preload-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-preload-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-document-start-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-document-start-navigation-stop","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-navigation-stop","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-navigation-stop","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-document-start-response-stop","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-document-start-response-stop","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-response-stop","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-start-response-stop","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-document-handoff-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-document-handoff-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-handoff-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-document-handoff-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-retarget-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-retarget-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-retarget-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-retarget-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-ipc-rendezvous-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-ipc-rendezvous-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-ipc-rendezvous-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-ipc-rendezvous-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-root-rendezvous-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-root-rendezvous-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-root-rendezvous-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-root-rendezvous-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-process-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-process-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-process-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-process-overlap","path":"/","max-assets":2,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-native-parser-full-process-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-full-process-overlap","path":"/","max-assets":1,"cache-resources":true}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-full-process-overlap","path":"/","max-assets":1}})",
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-native-parser-full-process-overlap","path":"/","max-assets":2,"cache-resources":true}})",
      };
  for (const char* json : kInvalid) {
    Config invalid;
    error.Truncate();
    EXPECT_TRUE(
        NS_FAILED(ParseConfig(nsDependentCString(json), invalid, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, RejectsInvalidPreamble)
{
  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":null})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"invalid"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree_early_overlap","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":""}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"https://example.com/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"//example.com/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/page#fragment"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/bad\\path"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/bad%2"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/space here"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/caf\u00e9"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","max-bytes":0}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/","max-bytes":0}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","max-assets":7}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","max-bytes":393217}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","max-assets":-1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","max-assets":1.0}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree","path":"/","max-bytes":"1"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","mode":"tree","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","path":"/","extra":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off"},"preamble":{"mode":"off"}})",
  };
  for (const char* json : kInvalid) {
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(nsDependentCString(json), config, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, ProtocolSpecificPreambleModes)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","h2-mode":"document-complete","h3-mode":"tree-root-overlap","path":"/camouflage/","max-assets":2}})"_ns,
          config, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(config.mPreamble.mMode, PreambleMode::DocumentComplete);
  EXPECT_EQ(config.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentComplete);
  EXPECT_EQ(config.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeRootOverlap);
  EXPECT_EQ(config.mPreamble.mMaxAssets, 2U);
  EXPECT_EQ(config.mPreamble.mMaxBytes, 256U * 1024U);

  Config h3Only;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-root-overlap","path":"/camouflage/"}})"_ns,
          h3Only, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(h3Only.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::Off);
  EXPECT_EQ(h3Only.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::TreeRootOverlap);
  EXPECT_EQ(h3Only.mPreamble.mMaxAssets, 2U);
  EXPECT_EQ(h3Only.mPreamble.mMaxBytes, 256U * 1024U);

  Config legacy;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","path":"/camouflage/"}})"_ns,
          legacy, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(legacy.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentComplete);
  EXPECT_EQ(legacy.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::DocumentComplete);
  EXPECT_EQ(legacy.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(legacy.mPreamble.mMaxBytes, 64U * 1024U);

  Config documentOverlap;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","h3-mode":"document-overlap","path":"/camouflage/"}})"_ns,
          documentOverlap, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(documentOverlap.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentComplete);
  EXPECT_EQ(documentOverlap.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::DocumentOverlap);
  EXPECT_EQ(documentOverlap.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(documentOverlap.mPreamble.mMaxBytes, 64U * 1024U);

  Config documentStartOverlap;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","h3-mode":"document-start-overlap","path":"/camouflage/"}})"_ns,
          documentStartOverlap, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(documentStartOverlap.mPreamble.ModeForProtocol(ProxyProtocol::H2),
            PreambleMode::DocumentComplete);
  EXPECT_EQ(documentStartOverlap.mPreamble.ModeForProtocol(ProxyProtocol::H3),
            PreambleMode::DocumentStartOverlap);
  EXPECT_EQ(documentStartOverlap.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(documentStartOverlap.mPreamble.mMaxBytes, 64U * 1024U);

  Config documentHandshakeConfirmed;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"document-handshake-confirmed","path":"/camouflage/"}})"_ns,
          documentHandshakeConfirmed, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      documentHandshakeConfirmed.mPreamble.ModeForProtocol(ProxyProtocol::H2),
      PreambleMode::Off);
  EXPECT_EQ(
      documentHandshakeConfirmed.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::DocumentHandshakeConfirmed);
  EXPECT_EQ(documentHandshakeConfirmed.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(documentHandshakeConfirmed.mPreamble.mMaxBytes, 64U * 1024U);

  Config documentCarrierDispatch;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"document-carrier-dispatch","path":"/camouflage/"}})"_ns,
          documentCarrierDispatch, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      documentCarrierDispatch.mPreamble.ModeForProtocol(ProxyProtocol::H2),
      PreambleMode::Off);
  EXPECT_EQ(
      documentCarrierDispatch.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::DocumentCarrierDispatch);
  EXPECT_EQ(documentCarrierDispatch.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(documentCarrierDispatch.mPreamble.mMaxBytes, 64U * 1024U);

  Config documentColdWinnerHandoff;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"document-cold-winner-handoff","path":"/camouflage/"}})"_ns,
          documentColdWinnerHandoff, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      documentColdWinnerHandoff.mPreamble.ModeForProtocol(ProxyProtocol::H2),
      PreambleMode::Off);
  EXPECT_EQ(
      documentColdWinnerHandoff.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::DocumentColdWinnerHandoff);
  EXPECT_EQ(documentColdWinnerHandoff.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(documentColdWinnerHandoff.mPreamble.mMaxBytes, 64U * 1024U);

  Config documentNativeCacheOpen;
  error.Truncate();
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"document-native-cache-open","path":"/camouflage/"}})"_ns,
          documentNativeCacheOpen, error),
      NS_OK)
      << error.get();
  EXPECT_EQ(
      documentNativeCacheOpen.mPreamble.ModeForProtocol(ProxyProtocol::H2),
      PreambleMode::Off);
  EXPECT_EQ(
      documentNativeCacheOpen.mPreamble.ModeForProtocol(ProxyProtocol::H3),
      PreambleMode::DocumentNativeCacheOpen);
  EXPECT_EQ(documentNativeCacheOpen.mPreamble.mMaxAssets, 0U);
  EXPECT_EQ(documentNativeCacheOpen.mPreamble.mMaxBytes, 64U * 1024U);

  static constexpr const char* kInvalid[] = {
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","h2-mode":"root","h2-mode":"off","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"root","h3-mode":"invalid","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"h3-mode":"tree-root-overlap","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"off","h3-mode":"off","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-complete","h2-mode":"document-complete","h3-mode":"off","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-overlap","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-start-overlap","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-handshake-confirmed","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"document-handshake-confirmed","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-carrier-dispatch","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"document-carrier-dispatch","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-cold-winner-handoff","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"document-cold-winner-handoff","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-native-cache-open","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"document-native-cache-open","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"document-native-channel-open","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"document-native-channel-open","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"document-native-channel-open","path":"/"}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"tree-resource-committed-overlap","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-resource-committed-overlap","path":"/","max-assets":1}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h3-mode":"tree-resource-committed-overlap","path":"/","max-assets":7}})",
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","preamble":{"mode":"off","h2-mode":"tree-native-parser-resource-committed-overlap","path":"/","max-assets":3,"cache-resources":true}})",
  };
  for (const char* json : kInvalid) {
    Config invalid;
    error.Truncate();
    EXPECT_TRUE(
        NS_FAILED(ParseConfig(nsDependentCString(json), invalid, error)))
        << json;
    EXPECT_FALSE(error.IsEmpty()) << json;
  }
}

TEST(NaiveFoxConfig, TunnelConfigPreambleCopySemantics)
{
  TunnelConfig source;
  source.mPreamble.mMode = PreambleMode::Tree;
  source.mPreamble.mH2Mode = Some(PreambleMode::DocumentComplete);
  source.mPreamble.mH3Mode = Some(PreambleMode::TreeRootOverlap);
  source.mPreamble.mPath.AssignLiteral("/camouflage/");
  source.mPreamble.mMaxAssets = 6;
  source.mPreamble.mMaxBytes = PreambleConfig::kMaximumBytes;
  source.mPreamble.mCacheResources = true;
  source.mOuterSessionGate = true;
  source.mImplicitPreambleGate = true;
  source.mDiagnosticFirstSocksTunnelUrgentStart = true;
  source.mDiagnosticOptimisticLocalReply = true;
  source.mDiagnosticH2FiniteExchanges = true;
  source.mDiagnosticH2FiniteReadThrough = true;
  source.mDiagnosticH2FiniteStreamUploads = true;
  source.mDiagnosticH2FiniteBudgetedDownloads = true;

  TunnelConfig constructed(source);
  TunnelConfig assigned;
  assigned = source;
  source.mPreamble.mMode = PreambleMode::Off;
  source.mPreamble.mPath.AssignLiteral("/");
  source.mPreamble.mMaxAssets = 0;
  source.mPreamble.mMaxBytes = 0;

  for (const TunnelConfig* copy : {&constructed, &assigned}) {
    EXPECT_EQ(copy->mPreamble.mMode, PreambleMode::Tree);
    EXPECT_EQ(copy->mPreamble.ModeForProtocol(ProxyProtocol::H2),
              PreambleMode::DocumentComplete);
    EXPECT_EQ(copy->mPreamble.ModeForProtocol(ProxyProtocol::H3),
              PreambleMode::TreeRootOverlap);
    EXPECT_TRUE(copy->mPreamble.mPath.EqualsLiteral("/camouflage/"));
    EXPECT_EQ(copy->mPreamble.mMaxAssets, 6U);
    EXPECT_EQ(copy->mPreamble.mMaxBytes, 384U * 1024U);
    EXPECT_TRUE(copy->mPreamble.mCacheResources);
    EXPECT_TRUE(copy->mOuterSessionGate);
    EXPECT_TRUE(copy->mImplicitPreambleGate);
    EXPECT_TRUE(copy->mDiagnosticFirstSocksTunnelUrgentStart);
    EXPECT_TRUE(copy->mDiagnosticOptimisticLocalReply);
    EXPECT_TRUE(copy->mDiagnosticH2FiniteExchanges);
    EXPECT_TRUE(copy->mDiagnosticH2FiniteReadThrough);
    EXPECT_TRUE(copy->mDiagnosticH2FiniteStreamUploads);
    EXPECT_TRUE(copy->mDiagnosticH2FiniteBudgetedDownloads);
  }
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
  EXPECT_TRUE(
      config.mProxies[0].mUrl.EqualsLiteral("https://proxy.example:443"));
  EXPECT_TRUE(config.mProxies[0].mUser.IsEmpty());
  EXPECT_TRUE(config.mProxies[0].mPassword.IsEmpty());
  EXPECT_EQ(config.mProxies[0].mProtocol, ProxyProtocol::H2);
  EXPECT_TRUE(
      config.mProxies[1].mUrl.EqualsLiteral("https://[2001:db8::1]:8443"));
  EXPECT_TRUE(config.mProxies[1].mUser.IsEmpty());
  EXPECT_TRUE(config.mProxies[1].mPassword.IsEmpty());
  EXPECT_EQ(config.mProxies[1].mProtocol, ProxyProtocol::H3);
}

TEST(NaiveFoxConfig, OneSidedUpstreamCredentials)
{
  struct Credentials {
    const char* mScheme;
    const char* mUserInfo;
    const char* mUser;
    const char* mPassword;
    ProxyProtocol mProtocol;
  };
  for (const auto& credentials : {
           Credentials{"https", "user:password@", "user", "password",
                       ProxyProtocol::H2},
           Credentials{"https", "user:@", "user", "", ProxyProtocol::H2},
           Credentials{"https", ":password@", "", "password",
                       ProxyProtocol::H2},
           Credentials{"https", ":@", "", "", ProxyProtocol::H2},
           Credentials{"quic", "user:password@", "user", "password",
                       ProxyProtocol::H3},
           Credentials{"quic", "user:@", "user", "", ProxyProtocol::H3},
           Credentials{"quic", ":password@", "", "password", ProxyProtocol::H3},
           Credentials{"quic", ":@", "", "", ProxyProtocol::H3},
       }) {
    nsAutoCString json(R"({"listen":"socks://127.0.0.1:1080","proxy":")"_ns);
    json.Append(credentials.mScheme);
    json.AppendLiteral("://");
    json.Append(credentials.mUserInfo);
    json.AppendLiteral("proxy.example:443");
    json.AppendLiteral(R"("})");

    Config config;
    nsAutoCString error;
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK)
        << json.get() << ": " << error.get();
    ASSERT_EQ(config.mProxies.Length(), 1U);
    EXPECT_EQ(config.mProxies[0].mProtocol, credentials.mProtocol);
    EXPECT_STREQ(config.mProxies[0].mUser.get(), credentials.mUser);
    EXPECT_STREQ(config.mProxies[0].mPassword.get(), credentials.mPassword);
  }
}

TEST(NaiveFoxConfig, PercentEncodedOneSidedUpstreamCredentials)
{
  Config config;
  nsAutoCString error;
  ASSERT_EQ(
      ParseConfig(
          R"({"listen":"socks://127.0.0.1:1080","proxy":"https://user%40name:@proxy.example"})"_ns,
          config, error),
      NS_OK)
      << error.get();
  ASSERT_EQ(config.mProxies.Length(), 1U);
  EXPECT_TRUE(config.mProxies[0].mUser.EqualsLiteral("user@name"));
  EXPECT_TRUE(config.mProxies[0].mPassword.IsEmpty());
}

TEST(NaiveFoxConfig, InsecureConcurrencyIsCompatibilityOnly)
{
  for (const char* value :
       {"1", "2", "2147483647", R"("1")", R"("+2")", R"("0002")"}) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","insecure-concurrency":)"_ns);
    json.Append(value);
    json.Append('}');
    Config config;
    nsAutoCString error;
    ASSERT_EQ(ParseConfig(json, config, error), NS_OK)
        << value << ": " << error.get();
    ASSERT_EQ(config.mProxies.Length(), 1U);
  }

  for (const char* value :
       {"0", "-1", "2147483648", "1.0", "1e0", R"("")", R"(" 2")", R"("2 ")",
        R"("2x")", R"("-1")", "true", "null", "[]", "{}"}) {
    nsAutoCString json(
        R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","insecure-concurrency":)"_ns);
    json.Append(value);
    json.Append('}');
    Config config;
    nsAutoCString error;
    EXPECT_TRUE(NS_FAILED(ParseConfig(json, config, error))) << value;
    EXPECT_FALSE(error.IsEmpty()) << value;
  }

  Config config;
  nsAutoCString error;
  nsAutoCString huge(
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","insecure-concurrency":")"_ns);
  for (size_t index = 0; index < 1024; ++index) {
    huge.Append('9');
  }
  huge.AppendLiteral(R"("})");
  EXPECT_TRUE(NS_FAILED(ParseConfig(huge, config, error)));
  EXPECT_FALSE(error.IsEmpty());

  EXPECT_TRUE(NS_FAILED(ParseConfig(
      R"({"listen":"socks://127.0.0.1:1080","proxy":"https://proxy.example","insecure-concurrency":1,"insecure-concurrency":2})"_ns,
      config, error)));
  EXPECT_FALSE(error.IsEmpty());
}

TEST(NaiveFoxConfig, BasicAuthorizationAllowsEmptyCredentialSide)
{
  nsAutoCString authorization;
  EXPECT_EQ(BuildProxyAuthorization("user"_ns, "password"_ns, authorization),
            NS_OK);
  EXPECT_TRUE(authorization.EqualsLiteral("Basic dXNlcjpwYXNzd29yZA=="));

  EXPECT_EQ(BuildProxyAuthorization("user"_ns, ""_ns, authorization), NS_OK);
  EXPECT_TRUE(authorization.EqualsLiteral("Basic dXNlcjo="));

  EXPECT_EQ(BuildProxyAuthorization(""_ns, "password"_ns, authorization),
            NS_OK);
  EXPECT_TRUE(authorization.EqualsLiteral("Basic OnBhc3N3b3Jk"));

  EXPECT_EQ(BuildProxyAuthorization(""_ns, ""_ns, authorization), NS_OK);
  EXPECT_TRUE(authorization.IsEmpty());
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
  ASSERT_EQ(
      ParseConfig(
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
  nsAutoCString json(R"({"listen":"socks://user:)"_ns);
  for (size_t index = 0; index < 256; ++index) {
    json.Append('p');
  }
  json.AppendLiteral(R"(@127.0.0.1:1080","proxy":"https://example.com"})");
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
      "padding",
      "Host",
      "Connection",
      "Proxy-Connection",
      "Keep-Alive",
      "Transfer-Encoding",
      "TE",
      "Trailer",
      "Upgrade",
      "Content-Length",
      "Proxy-Authorization",
      "Proxy-Authenticate",
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
    EXPECT_TRUE(
        error.EqualsLiteral("extra-headers contains a protected header name"))
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
