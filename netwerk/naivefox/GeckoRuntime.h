/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_GeckoRuntime_h
#define netwerk_naivefox_GeckoRuntime_h

#include "ProxyProtocol.h"
#include "nsCOMPtr.h"
#include "nsIDirectoryService.h"
#include "nsIFile.h"
#include "nsIIOService.h"
#include "nsStringFwd.h"
#include "nscore.h"

namespace mozilla::naivefox {

class GeckoRuntime final {
 public:
  GeckoRuntime() = default;
  ~GeckoRuntime();

  GeckoRuntime(const GeckoRuntime&) = delete;
  GeckoRuntime& operator=(const GeckoRuntime&) = delete;

  nsresult Initialize(int aArgc, char* aArgv[], const nsACString& aProfilePath,
                      ProxyProtocol aProtocol);
  nsresult RunEventLoopSmoke();

 private:
  void Shutdown();

  nsCOMPtr<nsIFile> mExecutable;
  nsCOMPtr<nsIFile> mBinDirectory;
  nsCOMPtr<nsIDirectoryServiceProvider> mDirectoryProvider;
  nsCOMPtr<nsIIOService> mIOService;
  bool mCommandLineInitialized = false;
  bool mXPCOMInitialized = false;
};

}  // namespace mozilla::naivefox

#endif
