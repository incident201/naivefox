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
      "       %s --profile PATH --raw-tunnel-smoke PROXY_URL TARGET\n",
      aProgram, aProgram, aProgram, aProgram);
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
    } else if (std::strcmp(aArgv[i], "--help") == 0) {
      PrintUsage(aArgv[0]);
      return 0;
    } else {
      PrintUsage(aArgv[0]);
      return 2;
    }
  }

  int modes = static_cast<int>(runtimeSmoke) + !fetchUrl.IsEmpty() +
              !rawProxyUrl.IsEmpty();
  if (profile.IsEmpty() || modes != 1 ||
      (rawProxyUrl.IsEmpty() != rawTarget.IsEmpty())) {
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
    } else {
      const char* proxyUser = std::getenv("NAIVEFOX_PROXY_USER");
      const char* proxyPassword = std::getenv("NAIVEFOX_PROXY_PASS");
      nsAutoCString user(proxyUser ? proxyUser : "");
      nsAutoCString password(proxyPassword ? proxyPassword : "");
      rv = mozilla::naivefox::RunRawTunnelSmoke(rawProxyUrl, rawTarget, user,
                                                password);
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
