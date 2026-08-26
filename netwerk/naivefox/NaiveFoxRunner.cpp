/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

#include "Config.h"
#include "GeckoRuntime.h"
#include "HttpClient.h"
#include "NaiveFoxAPI.h"
#include "NeckoTunnel.h"
#include "NativeStylePreloadActivation.h"
#include "ProfilerControl.h"
#include "ProxyProtocol.h"
#include "RuntimeLogging.h"
#include "SocksServer.h"
#include "TunnelSession.h"
#include "mozilla/Logging.h"
#include "nsError.h"
#include "nsString.h"
#include "nsXPCOM.h"

#ifdef ENABLE_TESTS
#  include "GTestRunner.h"
#endif

#ifdef ENABLE_TESTS
namespace mozilla {
int (*RunGTest)(int*, char**) = nullptr;
}  // namespace mozilla
#endif

namespace {

class AutoLogging final {
 public:
  AutoLogging() { NS_LogInit(); }
  ~AutoLogging() {
    mozilla::naivefox::ShutdownRuntimeLogging();
    NS_LogTerm();
  }
};

void PrintUsage(const char* aProgram) {
  std::printf(
      "Usage: %s [CONFIG_PATH]\n"
      "       %s --version\n"
      "       %s --profile PATH --runtime-smoke\n"
      "       %s --profile PATH --activation-process-smoke\n"
      "       %s --profile PATH --fetch URL\n"
      "       %s --profile PATH --raw-tunnel-smoke PROXY_URL TARGET "
      "[--protocol h2|h3|auto]\n"
      "       %s --profile PATH --socks-listen 127.0.0.1:PORT "
      "--proxy PROXY_URL [--protocol h2|h3|auto] [--max-connections N]\n",
      aProgram, aProgram, aProgram, aProgram, aProgram, aProgram, aProgram);
}

bool ParseProxyProtocol(const char* aValue,
                        mozilla::naivefox::ProxyProtocol& aProtocol) {
  if (std::strcmp(aValue, "h2") == 0) {
    aProtocol = mozilla::naivefox::ProxyProtocol::H2;
    return true;
  }
  if (std::strcmp(aValue, "h3") == 0) {
    aProtocol = mozilla::naivefox::ProxyProtocol::H3;
    return true;
  }
  if (std::strcmp(aValue, "auto") == 0) {
    aProtocol = mozilla::naivefox::ProxyProtocol::Auto;
    return true;
  }
  return false;
}

const char* ProxyProtocolName(mozilla::naivefox::ProxyProtocol aProtocol) {
  switch (aProtocol) {
    case mozilla::naivefox::ProxyProtocol::H2:
      return "HTTPS (h2)";
    case mozilla::naivefox::ProxyProtocol::H3:
      return "QUIC (h3)";
    case mozilla::naivefox::ProxyProtocol::Auto:
      return "auto";
  }
  return "unknown";
}

mozilla::naivefox::ProxyProtocol RuntimeProtocol(
    const mozilla::naivefox::Config& aConfig) {
  for (const auto& proxy : aConfig.mProxies) {
    if (proxy.mProtocol == mozilla::naivefox::ProxyProtocol::H3) {
      return mozilla::naivefox::ProxyProtocol::H3;
    }
  }
  return mozilla::naivefox::ProxyProtocol::H2;
}

bool PreambleNeedsCacheRuntime(const mozilla::naivefox::Config& aConfig) {
  const auto protocol = RuntimeProtocol(aConfig);
  return aConfig.mPreamble.mCacheResources ||
         mozilla::naivefox::PreambleModeUsesNativeCacheOpen(
             aConfig.mPreamble.ModeForProtocol(protocol));
}

bool PreambleNeedsNativeStyleActivationRuntime(
    const mozilla::naivefox::Config& aConfig) {
  const auto protocol = RuntimeProtocol(aConfig);
  return mozilla::naivefox::PreambleModeNeedsNativeStyleActivationRuntime(
      aConfig.mPreamble.ModeForProtocol(protocol));
}

nsTArray<mozilla::naivefox::TunnelConfig> MakeTunnelConfigs(
    const mozilla::naivefox::Config& aConfig) {
  nsTArray<mozilla::naivefox::TunnelConfig> tunnelConfigs;
  for (const auto& proxy : aConfig.mProxies) {
    auto& tunnelConfig = *tunnelConfigs.AppendElement();
    tunnelConfig.mProxyUrl = proxy.mUrl;
    tunnelConfig.mProxyUser = proxy.mUser;
    tunnelConfig.mProxyPassword = proxy.mPassword;
    tunnelConfig.mProtocol = proxy.mProtocol;
    tunnelConfig.mHostResolverRule = aConfig.mHostResolverRule;
    tunnelConfig.mExtraHeaders.AppendElements(aConfig.mExtraHeaders);
    tunnelConfig.mPreamble = aConfig.mPreamble;
    tunnelConfig.mOuterSessionGate = aConfig.mOuterSessionGate;
    tunnelConfig.mDiagnosticFirstSocksTunnelUrgentStart =
        aConfig.mDiagnosticFirstSocksTunnelUrgentStart;
  }
  return tunnelConfigs;
}

enum class EmbeddedRunState { Idle, Starting, Running, Stopping, Finished };

std::mutex sEmbeddedMutex;
EmbeddedRunState sEmbeddedState = EmbeddedRunState::Idle;
RefPtr<mozilla::naivefox::LocalProxyServerControl> sEmbeddedControl;

bool BeginEmbeddedRun(
    RefPtr<mozilla::naivefox::LocalProxyServerControl>& aControl) {
  std::lock_guard lock(sEmbeddedMutex);
  if (sEmbeddedState != EmbeddedRunState::Idle) {
    return false;
  }
  sEmbeddedState = EmbeddedRunState::Starting;
  sEmbeddedControl = new mozilla::naivefox::LocalProxyServerControl();
  aControl = sEmbeddedControl;
  return true;
}

void MarkEmbeddedRunning() {
  std::lock_guard lock(sEmbeddedMutex);
  if (sEmbeddedState == EmbeddedRunState::Starting) {
    sEmbeddedState = sEmbeddedControl && sEmbeddedControl->StopRequested()
                         ? EmbeddedRunState::Stopping
                         : EmbeddedRunState::Running;
  }
}

void FinishEmbeddedRun(bool aXPCOMAttempted) {
  std::lock_guard lock(sEmbeddedMutex);
  sEmbeddedControl = nullptr;
  sEmbeddedState =
      aXPCOMAttempted ? EmbeddedRunState::Finished : EmbeddedRunState::Idle;
}

}  // namespace

extern "C" NAIVEFOX_EXPORT void NaiveFoxRequestStop(void) {
  RefPtr<mozilla::naivefox::LocalProxyServerControl> control;
  {
    std::lock_guard lock(sEmbeddedMutex);
    if (sEmbeddedState != EmbeddedRunState::Starting &&
        sEmbeddedState != EmbeddedRunState::Running &&
        sEmbeddedState != EmbeddedRunState::Stopping) {
      return;
    }
    sEmbeddedState = EmbeddedRunState::Stopping;
    control = sEmbeddedControl;
  }
  if (control) {
    control->RequestStop();
  }
}

extern "C" NAIVEFOX_EXPORT int NaiveFoxRunEmbedded(const char* aConfigJson,
                                                   const char* aProfilePath,
                                                   const char* aRuntimePath) {
  if (!aConfigJson || !*aConfigJson || !aProfilePath || !*aProfilePath ||
      !aRuntimePath || !*aRuntimePath) {
    return NAIVEFOX_STATUS_INVALID_ARGUMENT;
  }

  RefPtr<mozilla::naivefox::LocalProxyServerControl> control;
  if (!BeginEmbeddedRun(control)) {
    return NAIVEFOX_STATUS_ALREADY_USED;
  }

  mozilla::naivefox::Config config;
  nsAutoCString error;
  nsresult rv = mozilla::naivefox::ParseConfig(nsDependentCString(aConfigJson),
                                               config, error);
  if (NS_SUCCEEDED(rv)) {
    rv = mozilla::naivefox::GeckoRuntime::ValidateEmbeddedLocations(
        nsDependentCString(aProfilePath), nsDependentCString(aRuntimePath));
  }
  if (NS_FAILED(rv)) {
    FinishEmbeddedRun(false);
    return NAIVEFOX_STATUS_INVALID_ARGUMENT;
  }

  bool xpcomAttempted = false;
  int status = NAIVEFOX_STATUS_RUNTIME_ERROR;
  {
    AutoLogging logging;
    mozilla::LogModule::Init(0, nullptr);
    rv = mozilla::naivefox::ConfigureRuntimeLogging(config.mLogMode,
                                                    config.mLogPath, error);
    if (NS_FAILED(rv)) {
      status = NAIVEFOX_STATUS_INVALID_ARGUMENT;
    } else {
      mozilla::naivefox::GeckoRuntime runtime;
      xpcomAttempted = true;
      rv = runtime.InitializeEmbedded(
          nsDependentCString(aProfilePath), nsDependentCString(aRuntimePath),
          RuntimeProtocol(config), config.mNoPostQuantum,
          PreambleNeedsCacheRuntime(config),
          PreambleNeedsNativeStyleActivationRuntime(config));
      if (NS_SUCCEEDED(rv)) {
        MarkEmbeddedRunning();
        mozilla::naivefox::RuntimeLogEvent(
            "NaiveFox embedded runtime started listeners=%u upstreams=%u\n",
            static_cast<unsigned>(config.mListeners.Length()),
            static_cast<unsigned>(config.mProxies.Length()));
        auto tunnelConfigs = MakeTunnelConfigs(config);
        rv = mozilla::naivefox::RunLocalProxyServer(
            config.mListeners, tunnelConfigs, config.mMaxConnections, control);
      }
      status =
          NS_SUCCEEDED(rv) ? NAIVEFOX_STATUS_OK : NAIVEFOX_STATUS_RUNTIME_ERROR;
    }
  }
  FinishEmbeddedRun(xpcomAttempted);
  return status;
}

extern "C" NAIVEFOX_EXPORT int NaiveFoxMain(int aArgc, char* aArgv[]) {
#ifdef ENABLE_TESTS
  if (std::getenv("MOZ_RUN_GTEST")) {
    mozilla::EnsureGTestRunnerLinked();
    if (!mozilla::RunGTest) {
      std::fprintf(stderr,
                   "TEST-UNEXPECTED-FAIL | gtest | runner is not linked\n");
      return 1;
    }
    return mozilla::RunGTest(&aArgc, aArgv);
  }
#endif

  AutoLogging logging;
  mozilla::LogModule::Init(aArgc, aArgv);

  const bool configMode = aArgc == 1 || (aArgc == 2 && aArgv[1][0] != '-' &&
                                         std::strlen(aArgv[1]) != 0);
  if (configMode) {
    nsAutoCString configPath(aArgc == 1 ? "config.json" : aArgv[1]);
    mozilla::naivefox::Config config;
    nsAutoCString error;
    nsresult rv = mozilla::naivefox::LoadConfigFile(configPath, config, error);
    if (NS_SUCCEEDED(rv)) {
      rv = mozilla::naivefox::ConfigureRuntimeLogging(config.mLogMode,
                                                      config.mLogPath, error);
    }
    mozilla::naivefox::ProfileDirectory profile;
    if (NS_SUCCEEDED(rv)) {
      rv = mozilla::naivefox::ResolveAndCreateProfile(profile, error);
    }
    if (NS_FAILED(rv)) {
      std::fprintf(stderr, "NaiveFox config error: %s\n", error.get());
      return 2;
    }

    const auto runtimeProtocol = RuntimeProtocol(config);

    mozilla::naivefox::GeckoRuntime runtime;
    rv = runtime.Initialize(aArgc, aArgv, profile.Path(), runtimeProtocol,
                            config.mNoPostQuantum,
                            PreambleNeedsCacheRuntime(config),
                            PreambleNeedsNativeStyleActivationRuntime(config));
    if (NS_SUCCEEDED(rv)) {
      mozilla::naivefox::RuntimeLogEvent(
          "NaiveFox started listeners=%u upstreams=%u\n",
          static_cast<unsigned>(config.mListeners.Length()),
          static_cast<unsigned>(config.mProxies.Length()));
      for (size_t index = 0; index < config.mProxies.Length(); ++index) {
        mozilla::naivefox::RuntimeLogEvent(
            "Proxying via %s endpoint=%s upstream=%u\n",
            ProxyProtocolName(config.mProxies[index].mProtocol),
            config.mProxies[index].mUrl.get(),
            static_cast<unsigned>(index + 1));
      }
      auto tunnelConfigs = MakeTunnelConfigs(config);
      rv = mozilla::naivefox::RunLocalProxyServer(
          config.mListeners, tunnelConfigs, config.mMaxConnections);
    }
    if (NS_FAILED(rv)) {
      std::fprintf(stderr, "NaiveFox failed: 0x%08x\n",
                   static_cast<unsigned>(rv));
      return 1;
    }
    mozilla::naivefox::RuntimeLog("NaiveFox completed successfully\n");
    return 0;
  }

  nsCString profile;
  nsCString fetchUrl;
  nsCString rawProxyUrl;
  nsCString rawTarget;
  nsCString socksListen;
  nsCString proxyUrl;
  mozilla::naivefox::ProxyProtocol protocol =
      mozilla::naivefox::ProxyProtocol::H2;
  uint32_t maxConnections = 0;
  bool runtimeSmoke = false;
  bool activationProcessSmoke = false;
  bool protocolSpecified = false;

  for (int i = 1; i < aArgc; ++i) {
    if (std::strcmp(aArgv[i], "--profile") == 0 && i + 1 < aArgc) {
      profile.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--fetch") == 0 && i + 1 < aArgc) {
      fetchUrl.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--runtime-smoke") == 0) {
      runtimeSmoke = true;
    } else if (std::strcmp(aArgv[i], "--activation-process-smoke") == 0) {
      activationProcessSmoke = true;
    } else if (std::strcmp(aArgv[i], "--raw-tunnel-smoke") == 0 &&
               i + 2 < aArgc) {
      rawProxyUrl.Assign(aArgv[++i]);
      rawTarget.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--socks-listen") == 0 && i + 1 < aArgc) {
      socksListen.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--proxy") == 0 && i + 1 < aArgc) {
      proxyUrl.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--protocol") == 0 && i + 1 < aArgc &&
               !protocolSpecified) {
      protocolSpecified = true;
      if (!ParseProxyProtocol(aArgv[++i], protocol)) {
        PrintUsage(aArgv[0]);
        return 2;
      }
    } else if (std::strcmp(aArgv[i], "--max-connections") == 0 &&
               i + 1 < aArgc) {
      char* end = nullptr;
      unsigned long value = std::strtoul(aArgv[++i], &end, 10);
      if (!end || *end || value == 0 || value > UINT32_MAX) {
        PrintUsage(aArgv[0]);
        return 2;
      }
      maxConnections = static_cast<uint32_t>(value);
    } else if (std::strcmp(aArgv[i], "--help") == 0) {
      PrintUsage(aArgv[0]);
      return 0;
    } else {
      PrintUsage(aArgv[0]);
      return 2;
    }
  }

  int modes = static_cast<int>(runtimeSmoke) +
              static_cast<int>(activationProcessSmoke) + !fetchUrl.IsEmpty() +
              !rawProxyUrl.IsEmpty() + !socksListen.IsEmpty();
  if (profile.IsEmpty() || modes != 1 ||
      (rawProxyUrl.IsEmpty() != rawTarget.IsEmpty()) ||
      (socksListen.IsEmpty() != proxyUrl.IsEmpty()) ||
      (maxConnections && socksListen.IsEmpty()) ||
      (protocolSpecified && rawProxyUrl.IsEmpty() && socksListen.IsEmpty())) {
    PrintUsage(aArgv[0]);
    return 2;
  }

  nsAutoCString loggingError;
  nsresult rv = mozilla::naivefox::ConfigureRuntimeLogging(
      mozilla::naivefox::RuntimeLogMode::Console, EmptyCString(), loggingError);
  if (NS_FAILED(rv)) {
    std::fprintf(stderr, "NaiveFox logging error: %s\n", loggingError.get());
    return 1;
  }

  mozilla::naivefox::GeckoRuntime runtime;
  rv = runtime.Initialize(aArgc, aArgv, profile, protocol);
  if (NS_SUCCEEDED(rv)) {
    if (runtimeSmoke) {
      rv = runtime.RunEventLoopSmoke();
    } else if (activationProcessSmoke) {
      rv = mozilla::naivefox::NativeStylePreloadActivation::
          RunProcessBootstrapAdmission();
    } else if (!fetchUrl.IsEmpty()) {
      rv = mozilla::naivefox::FetchWithNecko(fetchUrl);
    } else if (!rawProxyUrl.IsEmpty()) {
      const char* proxyUser = std::getenv("NAIVEFOX_PROXY_USER");
      const char* proxyPassword = std::getenv("NAIVEFOX_PROXY_PASS");
      nsAutoCString user(proxyUser ? proxyUser : "");
      nsAutoCString password(proxyPassword ? proxyPassword : "");
      rv = mozilla::naivefox::RunRawTunnelSmoke(rawProxyUrl, rawTarget, user,
                                                password, protocol);
    } else {
      constexpr auto kListenPrefix = "127.0.0.1:"_ns;
      if (!StringBeginsWith(socksListen, kListenPrefix)) {
        rv = NS_ERROR_INVALID_ARG;
      } else {
        nsAutoCString portText(Substring(socksListen, kListenPrefix.Length()));
        char* end = nullptr;
        unsigned long port = std::strtoul(portText.get(), &end, 10);
        if (!end || *end || port == 0 || port > UINT16_MAX) {
          rv = NS_ERROR_INVALID_ARG;
        } else {
          const char* proxyUser = std::getenv("NAIVEFOX_PROXY_USER");
          const char* proxyPassword = std::getenv("NAIVEFOX_PROXY_PASS");
          nsAutoCString user(proxyUser ? proxyUser : "");
          nsAutoCString password(proxyPassword ? proxyPassword : "");
          rv = mozilla::naivefox::RunSocksServer(static_cast<uint16_t>(port),
                                                 proxyUrl, user, password,
                                                 maxConnections, protocol);
        }
      }
    }
  }

  if (NS_FAILED(rv)) {
    std::fprintf(stderr, "NaiveFox failed: 0x%08x\n",
                 static_cast<unsigned>(rv));
    return 1;
  }

  std::printf("NaiveFox completed successfully\n");
  return 0;
}
