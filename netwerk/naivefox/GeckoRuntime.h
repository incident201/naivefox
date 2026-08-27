/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_GeckoRuntime_h
#define netwerk_naivefox_GeckoRuntime_h

#include "ProxyProtocol.h"
#include "TemporaryTrustStore.h"
#include "mozilla/AutoSQLiteLifetime.h"
#include "mozilla/UniquePtr.h"
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
                      ProxyProtocol aProtocol, bool aNoPostQuantum = false,
                      bool aEnablePreambleCache2 = false,
                      bool aEnableNativeStyleActivation = false,
                      bool aEnableNativeActivationProcess = false);
  static nsresult ValidateEmbeddedLocations(const nsACString& aProfilePath,
                                            const nsACString& aRuntimePath);
  nsresult InitializeEmbedded(const nsACString& aProfilePath,
                              const nsACString& aRuntimePath,
                              ProxyProtocol aProtocol,
                              bool aNoPostQuantum = false,
                              bool aEnablePreambleCache2 = false,
                              bool aEnableNativeStyleActivation = false,
                              bool aEnableNativeActivationProcess = false);
  nsresult RunEventLoopSmoke();

 private:
  nsresult InitializeWithLocations(
      nsIFile* aProfile, nsIFile* aBinDirectory, nsIFile* aExecutable,
      ProxyProtocol aProtocol, const nsACString* aAndroidRuntimePath,
      bool aNoPostQuantum, bool aEnablePreambleCache2,
      bool aEnableNativeStyleActivation, bool aEnableNativeActivationProcess);
  nsresult WaitForNetworkStartup();
  void Shutdown();

  nsCOMPtr<nsIFile> mExecutable;
  nsCOMPtr<nsIFile> mBinDirectory;
  nsCOMPtr<nsIDirectoryServiceProvider> mDirectoryProvider;
  nsCOMPtr<nsIIOService> mIOService;
  UniquePtr<AutoSQLiteLifetime> mSQLiteLifetime;
  UniquePtr<TemporaryTrustStore> mTemporaryTrustStore;
  bool mXPCOMInitialized = false;
  bool mPreambleCache2Initialized = false;
  bool mNativeStyleActivationInitialized = false;
  bool mNativeActivationProcessInitialized = false;
  bool mNoPostQuantumApplied = false;
  bool mSslCertFileApplied = false;
  bool mHadKyberPref = false;
  bool mHadMlkemPref = false;
  bool mHadHttp3KyberPref = false;
  bool mHttp3ThirdPartyRootsPrefWasLocked = false;
  bool mOldKyberPref = false;
  bool mOldMlkemPref = false;
  bool mOldHttp3KyberPref = false;
  bool mOldHttp3ThirdPartyRootsDefault = false;
};

}  // namespace mozilla::naivefox

#endif
