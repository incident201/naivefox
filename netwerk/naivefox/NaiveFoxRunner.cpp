/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "GeckoRuntime.h"
#include "HttpClient.h"
#include "NaiveFoxAPI.h"
#include "NeckoTunnel.h"
#include "ProfilerControl.h"
#include "SocksServer.h"
#include "mozilla/Logging.h"
#include "nsError.h"
#include "nsString.h"
#include "nsXPCOM.h"

namespace {

class AutoLogging final {
 public:
  AutoLogging() { NS_LogInit(); }
  ~AutoLogging() { NS_LogTerm(); }
};

void PrintUsage(const char* aProgram) {
  std::printf(
      "Usage: %s --version\n"
      "       %s --profile PATH --runtime-smoke\n"
      "       %s --profile PATH --fetch URL\n"
      "       %s --profile PATH --raw-tunnel-smoke PROXY_URL TARGET\n"
      "       %s --profile PATH --socks-listen 127.0.0.1:PORT "
      "--proxy PROXY_URL [--max-connections N]\n",
      aProgram, aProgram, aProgram, aProgram, aProgram);
}

}  // namespace

extern "C" MOZ_EXPORT int NaiveFoxMain(int aArgc, char* aArgv[]) {
  AutoLogging logging;
  mozilla::LogModule::Init(aArgc, aArgv);
  AUTO_PROFILER_INIT;

  nsCString profile;
  nsCString fetchUrl;
  nsCString rawProxyUrl;
  nsCString rawTarget;
  nsCString socksListen;
  nsCString proxyUrl;
  uint32_t maxConnections = 0;
  bool runtimeSmoke = false;

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
      (maxConnections && socksListen.IsEmpty())) {
    PrintUsage(aArgv[0]);
    return 2;
  }

  mozilla::naivefox::GeckoRuntime runtime;
  nsresult rv = runtime.Initialize(aArgc, aArgv, profile);
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
                                                password);
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
                                                 maxConnections);
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
