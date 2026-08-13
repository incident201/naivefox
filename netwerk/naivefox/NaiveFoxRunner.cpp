/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "Config.h"
#include "GeckoRuntime.h"
#include "HttpClient.h"
#include "NaiveFoxAPI.h"
#include "NeckoTunnel.h"
#include "ProfilerControl.h"
#include "ProxyProtocol.h"
#include "RuntimeLogging.h"
#include "SocksServer.h"
#include "TunnelSession.h"
#include "mozilla/Logging.h"
#include "nsError.h"
#include "nsString.h"
#include "nsXPCOM.h"

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
      "       %s --profile PATH --fetch URL\n"
      "       %s --profile PATH --raw-tunnel-smoke PROXY_URL TARGET "
      "[--protocol h2|h3|auto]\n"
      "       %s --profile PATH --socks-listen 127.0.0.1:PORT "
      "--proxy PROXY_URL [--protocol h2|h3|auto] [--max-connections N]\n",
      aProgram, aProgram, aProgram, aProgram, aProgram, aProgram);
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

}  // namespace

extern "C" MOZ_EXPORT int NaiveFoxMain(int aArgc, char* aArgv[]) {
  AutoLogging logging;
  mozilla::LogModule::Init(aArgc, aArgv);
  AUTO_PROFILER_INIT;

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
    nsAutoCString profile;
    if (NS_SUCCEEDED(rv)) {
      rv = mozilla::naivefox::ResolveAndCreateProfile(profile, error);
    }
    if (NS_FAILED(rv)) {
      std::fprintf(stderr, "NaiveFox config error: %s\n", error.get());
      return 2;
    }

    mozilla::naivefox::GeckoRuntime runtime;
    rv = runtime.Initialize(aArgc, aArgv, profile, config.mProtocol);
    if (NS_SUCCEEDED(rv)) {
      mozilla::naivefox::TunnelConfig tunnelConfig;
      tunnelConfig.mProxyUrl = config.mProxyUrl;
      tunnelConfig.mProxyUser = config.mProxyUser;
      tunnelConfig.mProxyPassword = config.mProxyPassword;
      tunnelConfig.mProtocol = config.mProtocol;
      rv = mozilla::naivefox::RunLocalProxyServer(config.mListeners,
                                                  tunnelConfig);
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
  bool protocolSpecified = false;

  for (int i = 1; i < aArgc; ++i) {
    if (std::strcmp(aArgv[i], "--profile") == 0 && i + 1 < aArgc) {
      profile.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--fetch") == 0 && i + 1 < aArgc) {
      fetchUrl.Assign(aArgv[++i]);
    } else if (std::strcmp(aArgv[i], "--runtime-smoke") == 0) {
      runtimeSmoke = true;
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

  int modes = static_cast<int>(runtimeSmoke) + !fetchUrl.IsEmpty() +
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
