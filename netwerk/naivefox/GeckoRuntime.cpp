/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "GeckoRuntime.h"

#include "NaiveFoxAPI.h"
#include "NativeStylePreloadActivation.h"

#ifdef XP_WIN
#  include <windows.h>
#else
#  include <limits.h>
#  include <unistd.h>
#endif

#include <atomic>
#include <cstdlib>
#include <cstring>
#include <filesystem>

#include "CacheObserver.h"
#include "mozIStorageService.h"
#include "mozilla/AppShutdown.h"
#include "mozilla/Preferences.h"
#include "mozilla/RefPtr.h"
#include "mozilla/Span.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "mozilla/Utf8.h"
#include "mozilla/ipc/IOThread.h"
#include "mozilla/net/UrlClassifierFeatureFactory.h"
#include "nsAppDirectoryServiceDefs.h"
#include "nsDirectoryServiceDefs.h"
#include "nsDirectoryServiceUtils.h"
#include "nsIFile.h"
#include "nsIIOService.h"
#include "nsINetworkLinkService.h"
#include "nsIObserverService.h"
#include "nsISimpleEnumerator.h"
#include "nsISocketTransportService.h"
#include "nsITimer.h"
#include "nsLocalFile.h"
#include "nsNetCID.h"
#include "nsNetUtil.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"
#include "nsThreadUtils.h"
#include "nsXULAppAPI.h"
#if defined(XP_LINUX) || defined(ANDROID)
#  include "NetlinkService.h"
#endif
#ifdef MOZ_NAIVEFOX
#  include "xpcpublic.h"
#endif

namespace mozilla::naivefox {

#if !defined(ANDROID) && !defined(__ANDROID__)
// Implemented by the lean activation transport. Keep the exported C entry
// point below independent from the C++ actor implementation.
int RunNativeStylePreloadActivationChild(int aArgc, char* aArgv[]);
#endif

namespace {

constexpr uint32_t kNetworkStartupBarrierTimeoutMs = 5000;

class StartupBarrierState final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(StartupBarrierState)

  void Complete() {
    MOZ_ASSERT(NS_IsMainThread());
    mComplete = true;
  }

  void Timeout() {
    MOZ_ASSERT(NS_IsMainThread());
    mTimedOut = true;
  }

  bool IsComplete() const {
    MOZ_ASSERT(NS_IsMainThread());
    return mComplete;
  }

  bool IsTimedOut() const {
    MOZ_ASSERT(NS_IsMainThread());
    return mTimedOut;
  }

 private:
  ~StartupBarrierState() = default;

  bool mComplete = false;
  bool mTimedOut = false;
};

template <typename Predicate>
nsresult WaitForStartupCondition(const nsACString& aName,
                                 Predicate&& aPredicate) {
  MOZ_ASSERT(NS_IsMainThread());

  RefPtr<StartupBarrierState> deadline = new StartupBarrierState();
  nsCOMPtr<nsITimer> timer;
  MOZ_TRY(NS_NewTimerWithCallback(
      getter_AddRefs(timer), [deadline](nsITimer*) { deadline->Timeout(); },
      kNetworkStartupBarrierTimeoutMs, nsITimer::TYPE_ONE_SHOT, aName));

  const bool processed = SpinEventLoopUntil(
      aName, [&]() { return aPredicate() || deadline->IsTimedOut(); });
  (void)timer->Cancel();

  if (!processed) {
    return NS_ERROR_FAILURE;
  }
  // Prefer a condition that became true on the same event-loop turn as the
  // deadline.  Otherwise a real timer event makes the wait fail closed.
  return aPredicate() ? NS_OK : NS_ERROR_NET_TIMEOUT;
}

class DirectoryProvider final : public nsIDirectoryServiceProvider {
 public:
  NS_DECL_ISUPPORTS
  NS_DECL_NSIDIRECTORYSERVICEPROVIDER

  DirectoryProvider(nsIFile* aProfile, nsIFile* aBinDirectory,
                    nsIFile* aExecutable)
      : mProfile(aProfile),
        mBinDirectory(aBinDirectory),
        mExecutable(aExecutable) {}

 private:
  ~DirectoryProvider() = default;

  nsresult Clone(nsIFile* aFile, bool* aPersistent, nsIFile** aResult) {
    *aPersistent = true;
    return aFile->Clone(aResult);
  }

  nsCOMPtr<nsIFile> mProfile;
  nsCOMPtr<nsIFile> mBinDirectory;
  nsCOMPtr<nsIFile> mExecutable;
};

NS_IMPL_ISUPPORTS(DirectoryProvider, nsIDirectoryServiceProvider)

NS_IMETHODIMP DirectoryProvider::GetFile(const char* aProperty,
                                         bool* aPersistent, nsIFile** aResult) {
  NS_ENSURE_ARG_POINTER(aPersistent);
  NS_ENSURE_ARG_POINTER(aResult);
  *aResult = nullptr;

  if (std::strcmp(aProperty, NS_APP_USER_PROFILE_50_DIR) == 0 ||
      std::strcmp(aProperty, NS_APP_USER_PROFILE_LOCAL_50_DIR) == 0 ||
      std::strcmp(aProperty, NS_APP_PROFILE_DIR_STARTUP) == 0 ||
      std::strcmp(aProperty, NS_APP_PROFILE_LOCAL_DIR_STARTUP) == 0 ||
      std::strcmp(aProperty, NS_APP_PREFS_50_DIR) == 0) {
    return Clone(mProfile, aPersistent, aResult);
  }

  if (std::strcmp(aProperty, NS_APP_PREFS_50_FILE) == 0) {
    nsCOMPtr<nsIFile> prefs;
    MOZ_TRY(mProfile->Clone(getter_AddRefs(prefs)));
    MOZ_TRY(prefs->AppendNative("prefs.js"_ns));
    return Clone(prefs, aPersistent, aResult);
  }

  if (std::strcmp(aProperty, NS_GRE_DIR) == 0 ||
      std::strcmp(aProperty, NS_GRE_BIN_DIR) == 0) {
    return Clone(mBinDirectory, aPersistent, aResult);
  }

  if (std::strcmp(aProperty, XRE_EXECUTABLE_FILE) == 0) {
    return Clone(mExecutable, aPersistent, aResult);
  }

  if (std::strcmp(aProperty, NS_APP_PREF_DEFAULTS_50_DIR) == 0) {
    nsCOMPtr<nsIFile> defaults;
    MOZ_TRY(mBinDirectory->Clone(getter_AddRefs(defaults)));
    MOZ_TRY(defaults->AppendNative("defaults"_ns));
    MOZ_TRY(defaults->AppendNative("pref"_ns));
    return Clone(defaults, aPersistent, aResult);
  }

  return NS_ERROR_FAILURE;
}

}  // namespace

GeckoRuntime::~GeckoRuntime() { Shutdown(); }

nsresult GeckoRuntime::Initialize(int aArgc, char* aArgv[],
                                  const nsACString& aProfilePath,
                                  ProxyProtocol aProtocol, bool aNoPostQuantum,
                                  bool aEnablePreambleCache2,
                                  bool aEnableNativeStyleActivation,
                                  bool aEnableNativeActivationProcess) {
  if (mXPCOMInitialized || aProfilePath.IsEmpty()) {
    return NS_ERROR_INVALID_ARG;
  }

  (void)aArgc;
  (void)aArgv;

#ifdef XP_WIN
  char executablePath[MAX_PATH + 1];
  const DWORD executableLength =
      GetModuleFileNameA(nullptr, executablePath, MAX_PATH);
  if (executableLength == 0 || executableLength >= MAX_PATH) {
#else
  char executablePath[PATH_MAX + 1];
  const ssize_t executableLength =
      readlink("/proc/self/exe", executablePath, PATH_MAX);
  if (executableLength <= 0 || executableLength > PATH_MAX) {
#endif
    return NS_ERROR_FAILURE;
  }
  executablePath[executableLength] = '\0';
  MOZ_TRY(NS_NewNativeLocalFile(
      nsDependentCSubstring(executablePath, executableLength),
      getter_AddRefs(mExecutable)));
  MOZ_TRY(mExecutable->GetParent(getter_AddRefs(mBinDirectory)));

  nsCOMPtr<nsIFile> profile;
  MOZ_TRY(NS_NewNativeLocalFile(aProfilePath, getter_AddRefs(profile)));

  return InitializeWithLocations(profile, mBinDirectory, mExecutable, aProtocol,
                                 nullptr, aNoPostQuantum, aEnablePreambleCache2,
                                 aEnableNativeStyleActivation,
                                 aEnableNativeActivationProcess);
}

nsresult GeckoRuntime::InitializeEmbedded(
    const nsACString& aProfilePath, const nsACString& aRuntimePath,
    ProxyProtocol aProtocol, bool aNoPostQuantum, bool aEnablePreambleCache2,
    bool aEnableNativeStyleActivation, bool aEnableNativeActivationProcess) {
  if (mXPCOMInitialized) {
    return NS_ERROR_INVALID_ARG;
  }
  if (aEnableNativeActivationProcess) {
    return NS_ERROR_NOT_IMPLEMENTED;
  }
  MOZ_TRY(ValidateEmbeddedLocations(aProfilePath, aRuntimePath));

  nsCOMPtr<nsIFile> profile;
  nsCOMPtr<nsIFile> binDirectory;
  nsCOMPtr<nsIFile> executable;
  MOZ_TRY(NS_NewNativeLocalFile(aProfilePath, getter_AddRefs(profile)));
  MOZ_TRY(NS_NewNativeLocalFile(aRuntimePath, getter_AddRefs(binDirectory)));
  MOZ_TRY(profile->Normalize());
  MOZ_TRY(binDirectory->Normalize());
  MOZ_TRY(binDirectory->Clone(getter_AddRefs(executable)));
#ifdef XP_WIN
  MOZ_TRY(executable->AppendNative("xul.dll"_ns));
#else
  MOZ_TRY(executable->AppendNative("libxul.so"_ns));
#endif

#ifdef ANDROID
  nsAutoCString normalizedRuntimePath;
  MOZ_TRY(binDirectory->GetNativePath(normalizedRuntimePath));
  return InitializeWithLocations(
      profile, binDirectory, executable, aProtocol, &normalizedRuntimePath,
      aNoPostQuantum, aEnablePreambleCache2, aEnableNativeStyleActivation,
      aEnableNativeActivationProcess);
#else
  return InitializeWithLocations(profile, binDirectory, executable, aProtocol,
                                 nullptr, aNoPostQuantum, aEnablePreambleCache2,
                                 aEnableNativeStyleActivation,
                                 aEnableNativeActivationProcess);
#endif
}

nsresult GeckoRuntime::ValidateEmbeddedLocations(
    const nsACString& aProfilePath, const nsACString& aRuntimePath) {
  if (aProfilePath.IsEmpty() || aRuntimePath.IsEmpty() ||
      !IsUtf8(Span(aProfilePath.BeginReading(), aProfilePath.Length())) ||
      !IsUtf8(Span(aRuntimePath.BeginReading(), aRuntimePath.Length())) ||
      !std::filesystem::path(PromiseFlatCString(aProfilePath).get())
           .is_absolute() ||
      !std::filesystem::path(PromiseFlatCString(aRuntimePath).get())
           .is_absolute()) {
    return NS_ERROR_INVALID_ARG;
  }

  nsCOMPtr<nsIFile> profile;
  nsCOMPtr<nsIFile> binDirectory;
  nsCOMPtr<nsIFile> executable;
  MOZ_TRY(NS_NewNativeLocalFile(aProfilePath, getter_AddRefs(profile)));
  MOZ_TRY(NS_NewNativeLocalFile(aRuntimePath, getter_AddRefs(binDirectory)));
  MOZ_TRY(profile->Normalize());
  MOZ_TRY(binDirectory->Normalize());
  MOZ_TRY(binDirectory->Clone(getter_AddRefs(executable)));
#ifdef XP_WIN
  MOZ_TRY(executable->AppendNative("xul.dll"_ns));
#else
  MOZ_TRY(executable->AppendNative("libxul.so"_ns));
#endif

  bool isDirectory = false;
  MOZ_TRY(profile->IsDirectory(&isDirectory));
  if (!isDirectory) {
    return NS_ERROR_FILE_NOT_DIRECTORY;
  }
  bool isWritable = false;
  MOZ_TRY(profile->IsWritable(&isWritable));
  if (!isWritable) {
    return NS_ERROR_FILE_ACCESS_DENIED;
  }
  MOZ_TRY(binDirectory->IsDirectory(&isDirectory));
  if (!isDirectory) {
    return NS_ERROR_FILE_NOT_DIRECTORY;
  }
  bool isFile = false;
  MOZ_TRY(executable->IsFile(&isFile));
  return isFile ? NS_OK : NS_ERROR_FILE_NOT_FOUND;
}

nsresult GeckoRuntime::InitializeWithLocations(
    nsIFile* aProfile, nsIFile* aBinDirectory, nsIFile* aExecutable,
    ProxyProtocol aProtocol, const nsACString* aAndroidRuntimePath,
    bool aNoPostQuantum, bool aEnablePreambleCache2,
    bool aEnableNativeStyleActivation, bool aEnableNativeActivationProcess) {
  if (mXPCOMInitialized || !aProfile || !aBinDirectory || !aExecutable) {
    return NS_ERROR_INVALID_ARG;
  }

  bool isDirectory = false;
  MOZ_TRY(aProfile->IsDirectory(&isDirectory));
  if (!isDirectory) {
    return NS_ERROR_FILE_NOT_DIRECTORY;
  }
  bool isWritable = false;
  MOZ_TRY(aProfile->IsWritable(&isWritable));
  if (!isWritable) {
    return NS_ERROR_FILE_ACCESS_DENIED;
  }
  MOZ_TRY(aBinDirectory->IsDirectory(&isDirectory));
  if (!isDirectory) {
    return NS_ERROR_FILE_NOT_DIRECTORY;
  }
  bool isFile = false;
  MOZ_TRY(aExecutable->IsFile(&isFile));
  if (!isFile) {
    return NS_ERROR_FILE_NOT_FOUND;
  }

#ifdef XP_WIN
  if (_putenv_s("MOZ_HEADLESS", "1") != 0 ||
      _putenv_s("MOZ_DISABLE_SOCKET_PROCESS", "1") != 0) {
#else
  if (setenv("MOZ_HEADLESS", "1", 1) != 0 ||
      setenv("MOZ_DISABLE_SOCKET_PROCESS", "1", 1) != 0) {
#endif
    return NS_ERROR_FAILURE;
  }
#ifdef ANDROID
  if (!aAndroidRuntimePath ||
      setenv("MOZ_ANDROID_LIBDIR",
             PromiseFlatCString(*aAndroidRuntimePath).get(), 1) != 0) {
    return NS_ERROR_FAILURE;
  }
#else
  (void)aAndroidRuntimePath;
#endif

  mExecutable = aExecutable;
  mBinDirectory = aBinDirectory;

  RefPtr<DirectoryProvider> provider =
      new DirectoryProvider(aProfile, mBinDirectory, mExecutable);
  mDirectoryProvider = provider;

  mSQLiteLifetime = MakeUnique<AutoSQLiteLifetime>();
  MOZ_TRY(NS_InitXPCOM(nullptr, mBinDirectory, mDirectoryProvider));
  mXPCOMInitialized = true;

  Preferences::InitializeUserPrefs();
  Preferences::SetBool("network.process.enabled", false);
  Preferences::SetBool("network.http.network_access_on_socket_process.enabled",
                       false);
  Preferences::SetBool("network.http.http3.enable",
                       aProtocol != ProxyProtocol::H2);
  Preferences::SetBool("security.nocertdb", false);

  if (aNoPostQuantum) {
    mHadKyberPref = Preferences::HasUserValue("security.tls.enable_kyber");
    mHadMlkemPref = Preferences::HasUserValue("security.tls.enable_mlkem1024");
    mHadHttp3KyberPref =
        Preferences::HasUserValue("network.http.http3.enable_kyber");
    (void)Preferences::GetBool("security.tls.enable_kyber", &mOldKyberPref);
    (void)Preferences::GetBool("security.tls.enable_mlkem1024", &mOldMlkemPref);
    (void)Preferences::GetBool("network.http.http3.enable_kyber",
                               &mOldHttp3KyberPref);
    Preferences::SetBool("security.tls.enable_kyber", false);
    Preferences::SetBool("security.tls.enable_mlkem1024", false);
    Preferences::SetBool("network.http.http3.enable_kyber", false);
    mNoPostQuantumApplied = true;
  }

  nsCOMPtr<nsIObserverService> observers =
      do_GetService(NS_OBSERVERSERVICE_CONTRACTID);
  if (!observers) {
    return NS_ERROR_FAILURE;
  }
  nsresult storageRv = NS_OK;
  nsCOMPtr<mozIStorageService> storage =
      do_GetService("@mozilla.org/storage/service;1", &storageRv);
  MOZ_TRY(storageRv);
  // The minimized runtime does not start Firefox's browser cache graph. Opt in
  // only for an explicit preamble cache2 experiment, before profile-do-change
  // so CacheObserver sees the normal profile event.
  if (aEnablePreambleCache2) {
    MOZ_TRY(net::CacheObserver::Init());
    mPreambleCache2Initialized = true;
  }
  MOZ_TRY(observers->NotifyObservers(nullptr, "profile-do-change", u"startup"));
  net_EnsurePSMInit();

  mTemporaryTrustStore = MakeUnique<TemporaryTrustStore>();
  nsAutoCString trustError;
  nsresult trustRv = mTemporaryTrustStore->LoadFromEnvironment(trustError);
  if (NS_FAILED(trustRv)) {
    mTemporaryTrustStore = nullptr;
    return trustRv;
  }
  if (mTemporaryTrustStore->IsConfigured()) {
    constexpr auto kHttp3ThirdPartyRootsPref =
        "network.http.http3.disable_when_third_party_roots_found";
    MOZ_TRY(Preferences::GetBool(kHttp3ThirdPartyRootsPref,
                                 &mOldHttp3ThirdPartyRootsDefault,
                                 PrefValueKind::Default));
    mHttp3ThirdPartyRootsPrefWasLocked =
        Preferences::IsLocked(kHttp3ThirdPartyRootsPref);
    if (mHttp3ThirdPartyRootsPrefWasLocked) {
      MOZ_TRY(Preferences::Unlock(kHttp3ThirdPartyRootsPref));
    }
    MOZ_TRY(Preferences::SetBool(kHttp3ThirdPartyRootsPref, false,
                                 PrefValueKind::Default));
    MOZ_TRY(Preferences::Lock(kHttp3ThirdPartyRootsPref));
    mSslCertFileApplied = true;
  }

  mIOService = do_GetIOService();
  if (!mIOService) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(WaitForNetworkStartup());
  if (aEnableNativeActivationProcess) {
    MOZ_TRY(NativeStylePreloadActivation::InitializeProcess());
    if (!NativeStylePreloadActivation::IsProcessReady()) {
      return NS_ERROR_FAILURE;
    }
    mNativeActivationProcessInitialized = true;
  }
  if (aEnableNativeStyleActivation) {
    MOZ_TRY(NativeStylePreloadActivation::Initialize());
    mNativeStyleActivationInitialized = true;
    MOZ_TRY(WaitForStartupCondition(
        "NaiveFox::NativeStyleActivationWarmup"_ns,
        []() { return NativeStylePreloadActivation::IsReady(); }));
  }
  return NS_OK;
}

nsresult GeckoRuntime::WaitForNetworkStartup() {
  MOZ_ASSERT(NS_IsMainThread());

#if defined(XP_LINUX) || defined(ANDROID)
  nsCOMPtr<nsINetworkLinkService> linkService =
      do_GetService(NS_NETWORK_LINK_SERVICE_CONTRACTID);
  const auto initialState = net::NetlinkService::GetInitialNetworkState();
  NAIVEFOX_NETWORK_STARTUP_LOG(("barrier.wait link_service=%d initial_state=%u",
                                !!linkService,
                                static_cast<unsigned>(initialState)));
  if (!linkService) {
    return NS_ERROR_FAILURE;
  }

  MOZ_TRY(WaitForStartupCondition("NaiveFox::InitialNetworkState"_ns, []() {
    return net::InitialNetworkStateIsTerminal(
        net::NetlinkService::GetInitialNetworkState());
  }));
  if (!net::InitialNetworkStateAllowsStartup(
          net::NetlinkService::GetInitialNetworkState())) {
    NAIVEFOX_NETWORK_STARTUP_LOG(("barrier.initial-failed"));
    return NS_ERROR_FAILURE;
  }
  NAIVEFOX_NETWORK_STARTUP_LOG(("barrier.initial-ready"));
#endif

  // The readiness latch is set on the netlink thread after it queued any
  // initial up/down/changed notifications.  Drain the main-thread queue first
  // so every observer has posted its connection-manager work.
  RefPtr<StartupBarrierState> mainThreadBarrier = new StartupBarrierState();
  MOZ_TRY(NS_DispatchToCurrentThread(NS_NewRunnableFunction(
      "NaiveFox::NetworkMainThreadBarrier",
      [mainThreadBarrier]() { mainThreadBarrier->Complete(); })));
  MOZ_TRY(WaitForStartupCondition(
      "NaiveFox::NetworkMainThreadBarrier"_ns,
      [mainThreadBarrier]() { return mainThreadBarrier->IsComplete(); }));
#if defined(XP_LINUX) || defined(ANDROID)
  NAIVEFOX_NETWORK_STARTUP_LOG(("barrier.main-drained"));
#endif

  // nsHttpConnectionMgr posts VerifyTraffic to the socket thread.  This FIFO
  // barrier reports back to main only after all work caused by the initial
  // network-state convergence has finished.
  nsCOMPtr<nsIEventTarget> socketTarget =
      do_GetService(NS_SOCKETTRANSPORTSERVICE_CONTRACTID);
  if (!socketTarget) {
    return NS_ERROR_FAILURE;
  }
  RefPtr<StartupBarrierState> socketThreadBarrier = new StartupBarrierState();
  MOZ_TRY(socketTarget->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::NetworkSocketThreadBarrier", [socketThreadBarrier]() {
        // Return to the main thread only after all earlier socket-thread work
        // has run.  The refcounted state remains safe if startup times out.
        (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::NetworkSocketThreadBarrierComplete",
            [socketThreadBarrier]() { socketThreadBarrier->Complete(); }));
      })));
  nsresult rv = WaitForStartupCondition(
      "NaiveFox::NetworkSocketThreadBarrier"_ns,
      [socketThreadBarrier]() { return socketThreadBarrier->IsComplete(); });
#if defined(XP_LINUX) || defined(ANDROID)
  NAIVEFOX_NETWORK_STARTUP_LOG(
      ("barrier.socket-drained rv=%08x", static_cast<uint32_t>(rv)));
#endif
  return rv;
}

nsresult GeckoRuntime::RunEventLoopSmoke() {
  if (!mXPCOMInitialized) {
    return NS_ERROR_NOT_INITIALIZED;
  }

  bool handled = false;
  MOZ_TRY(NS_DispatchToCurrentThread(NS_NewRunnableFunction(
      "NaiveFox::RuntimeSmoke", [&handled]() { handled = true; })));

  return SpinEventLoopUntil("NaiveFox::RuntimeSmoke"_ns,
                            [&handled]() { return handled; })
             ? NS_OK
             : NS_ERROR_FAILURE;
}

void GeckoRuntime::Shutdown() {
  mIOService = nullptr;
  mTemporaryTrustStore = nullptr;

  if (mXPCOMInitialized) {
    if (mNativeActivationProcessInitialized) {
      NativeStylePreloadActivation::ShutdownProcess();
      mNativeActivationProcessInitialized = false;
    }
    if (mNativeStyleActivationInitialized) {
      NativeStylePreloadActivation::Shutdown();
      mNativeStyleActivationInitialized = false;
    }
    if (mNoPostQuantumApplied) {
      if (mHadKyberPref) {
        Preferences::SetBool("security.tls.enable_kyber", mOldKyberPref);
      } else {
        Preferences::ClearUser("security.tls.enable_kyber");
      }
      if (mHadMlkemPref) {
        Preferences::SetBool("security.tls.enable_mlkem1024", mOldMlkemPref);
      } else {
        Preferences::ClearUser("security.tls.enable_mlkem1024");
      }
      if (mHadHttp3KyberPref) {
        Preferences::SetBool("network.http.http3.enable_kyber",
                             mOldHttp3KyberPref);
      } else {
        Preferences::ClearUser("network.http.http3.enable_kyber");
      }
      mNoPostQuantumApplied = false;
    }
    if (mSslCertFileApplied) {
      constexpr auto kHttp3ThirdPartyRootsPref =
          "network.http.http3.disable_when_third_party_roots_found";
      (void)Preferences::Unlock(kHttp3ThirdPartyRootsPref);
      (void)Preferences::SetBool(kHttp3ThirdPartyRootsPref,
                                 mOldHttp3ThirdPartyRootsDefault,
                                 PrefValueKind::Default);
      if (mHttp3ThirdPartyRootsPrefWasLocked) {
        (void)Preferences::Lock(kHttp3ThirdPartyRootsPref);
      }
      mSslCertFileApplied = false;
    }
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownNetTeardown);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownTeardown);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdown);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownQM);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownTelemetry);
    if (mPreambleCache2Initialized) {
      // AppShutdown has already delivered profile-before-change, which drains
      // CacheStorageService and CacheFileIOManager.  Pair our explicit Init
      // before XPCOM tears down the observer service.
      (void)net::CacheObserver::Shutdown();
      mPreambleCache2Initialized = false;
    }
    // The full browser invokes this through nsLayoutStatics. The lean runtime
    // has no layout shutdown path, so explicitly unregister feature preference
    // callbacks while XPCOM and Preferences are still alive.
    net::UrlClassifierFeatureFactory::Shutdown();
    (void)NS_ShutdownXPCOM(nullptr);
    mXPCOMInitialized = false;
  }

  mSQLiteLifetime = nullptr;

  mDirectoryProvider = nullptr;
  mBinDirectory = nullptr;
  mExecutable = nullptr;
}

}  // namespace mozilla::naivefox

#ifdef MOZ_NAIVEFOX
constexpr const volatile xpc::ReadOnlyPage xpc::ReadOnlyPage::sInstance;

void xpc::ReadOnlyPage::Init() {}

namespace {

std::atomic<GeckoProcessType> sNaiveFoxProcessType{GeckoProcessType_Default};
std::atomic<bool> sNaiveFoxActivationChildEntered{false};

}  // namespace

namespace mozilla::naivefox {

bool EnterNativeStylePreloadActivationChildRole() {
  bool expected = false;
  if (!sNaiveFoxActivationChildEntered.compare_exchange_strong(
          expected, true, std::memory_order_acq_rel,
          std::memory_order_acquire)) {
    return false;
  }
  sNaiveFoxProcessType.store(GeckoProcessType_Utility,
                             std::memory_order_release);
  return true;
}

}  // namespace mozilla::naivefox

GeckoProcessType XRE_GetProcessType() {
  return sNaiveFoxProcessType.load(std::memory_order_acquire);
}

const char* XRE_GetProcessTypeString() {
  return XRE_GetProcessType() == GeckoProcessType_Utility ? "utility"
                                                          : "default";
}

GeckoChildID XRE_GetChildID() {
  return XRE_GetProcessType() == GeckoProcessType_Default ? 0 : 1;
}

bool XRE_IsE10sParentProcess() { return false; }

#  define GECKO_PROCESS_TYPE(enum_value, enum_name, string_name,               \
                             proc_typename, process_bin_type,                  \
                             procinfo_typename, webidl_typename, allcaps_name) \
    bool XRE_Is##proc_typename##Process() {                                    \
      return XRE_GetProcessType() == GeckoProcessType_##enum_name;             \
    }
#  include "mozilla/GeckoProcessTypes.h"
#  undef GECKO_PROCESS_TYPE

bool XRE_UseNativeEventProcessing() { return false; }

nsISerialEventTarget* XRE_GetAsyncIOEventTarget() {
  if (mozilla::ipc::IOThread* ioThread = mozilla::ipc::IOThread::Get()) {
    return ioThread->GetEventTarget();
  }
  return mozilla::GetMainThreadSerialEventTarget();
}

#  if !defined(ANDROID) && !defined(__ANDROID__)
extern "C" NAIVEFOX_EXPORT int NaiveFoxActivationChildMain(int aArgc,
                                                           char* aArgv[]) {
  return mozilla::naivefox::RunNativeStylePreloadActivationChild(aArgc, aArgv);
}
#  endif

nsresult XRE_GetFileFromPath(const char* aPath, nsIFile** aResult) {
#  ifdef XP_WIN
  char fullPath[MAX_PATH + 1];
  const DWORD length = GetFullPathNameA(aPath, MAX_PATH, fullPath, nullptr);
  if (length == 0 || length >= MAX_PATH) {
    return NS_ERROR_FAILURE;
  }
#  else
  char fullPath[PATH_MAX + 1];
  if (!realpath(aPath, fullPath)) {
    return NS_ERROR_FAILURE;
  }
#  endif
  return NS_NewNativeLocalFile(nsDependentCString(fullPath), aResult);
}
#endif
