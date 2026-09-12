/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

#include "CliSignalStop.h"
#include "Config.h"
#include "GeckoRuntime.h"
#include "NaiveFoxAPI.h"
#include "ProxyProtocol.h"
#include "RuntimeLogging.h"
#include "SocksServer.h"
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
      "Usage: %s [CONFIG_PATH]\n       %s --version\n       %s --help\n",
      aProgram, aProgram, aProgram);
}

const char* ProxyProtocolName(mozilla::naivefox::ProxyProtocol aProtocol) {
  switch (aProtocol) {
    case mozilla::naivefox::ProxyProtocol::H2:
      return "HTTPS (h2)";
    case mozilla::naivefox::ProxyProtocol::H3:
      return "QUIC (h3)";
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

nsTArray<mozilla::naivefox::TransportConfig> MakeTransportConfigs(
    const mozilla::naivefox::Config& aConfig) {
  nsTArray<mozilla::naivefox::TransportConfig> tunnelConfigs;
  for (const auto& proxy : aConfig.mProxies) {
    auto& tunnelConfig = *tunnelConfigs.AppendElement();
    tunnelConfig.mProxyUrl = proxy.mUrl;
    tunnelConfig.mProxyUser = proxy.mUser;
    tunnelConfig.mProxyPassword = proxy.mPassword;
    tunnelConfig.mProtocol = proxy.mProtocol;
    tunnelConfig.mHostResolverRule = aConfig.mHostResolverRule;
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
          RuntimeProtocol(config), config.mNoPostQuantum);
      if (NS_SUCCEEDED(rv)) {
        MarkEmbeddedRunning();
        mozilla::naivefox::RuntimeLogEvent(
            "NaiveFox embedded runtime started listeners=%u upstreams=%u\n",
            static_cast<unsigned>(config.mListeners.Length()),
            static_cast<unsigned>(config.mProxies.Length()));
        auto tunnelConfigs = MakeTransportConfigs(config);
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

  if (aArgc == 2 && std::strcmp(aArgv[1], "--version") == 0) {
    std::printf("%s\n", NaiveFoxVersion());
    return 0;
  }
  if (aArgc == 2 && std::strcmp(aArgv[1], "--help") == 0) {
    PrintUsage(aArgv[0]);
    return 0;
  }
  if (aArgc > 2 || (aArgc == 2 && (!*aArgv[1] || aArgv[1][0] == '-'))) {
    PrintUsage(aArgv[0]);
    return 2;
  }
  {
    nsAutoCString configPath(aArgc == 2 ? aArgv[1] : "config.json");
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
    RefPtr<mozilla::naivefox::LocalProxyServerControl> control;
#if defined(XP_LINUX) && !defined(ANDROID)
    control = new mozilla::naivefox::LocalProxyServerControl();
    mozilla::naivefox::CliSignalStop signalStop;
    rv = signalStop.Start(control);
    if (NS_FAILED(rv)) {
      std::fprintf(stderr, "NaiveFox signal monitor failed: 0x%08x\n",
                   static_cast<unsigned>(rv));
      return 1;
    }
#endif

    mozilla::naivefox::GeckoRuntime runtime;
    rv = runtime.Initialize(aArgc, aArgv, profile.Path(), runtimeProtocol,
                            config.mNoPostQuantum);
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
      auto tunnelConfigs = MakeTransportConfigs(config);
      rv = mozilla::naivefox::RunLocalProxyServer(
          config.mListeners, tunnelConfigs, config.mMaxConnections, control);
    }
#if defined(XP_LINUX) && !defined(ANDROID)
    if (signalStop.Failed()) {
      rv = NS_ERROR_FAILURE;
    }
#endif
    if (NS_FAILED(rv)) {
      std::fprintf(stderr, "NaiveFox failed: 0x%08x\n",
                   static_cast<unsigned>(rv));
      return 1;
    }
    mozilla::naivefox::RuntimeLog("NaiveFox completed successfully\n");
    return 0;
  }
}
