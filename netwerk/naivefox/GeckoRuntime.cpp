/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "GeckoRuntime.h"

#ifdef XP_WIN
#  include <windows.h>
#else
#  include <limits.h>
#  include <unistd.h>
#endif

#include <cstdlib>
#include <cstring>

#include "mozIStorageService.h"
#include "mozilla/AppShutdown.h"
#include "mozilla/Preferences.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "nsAppDirectoryServiceDefs.h"
#include "nsDirectoryServiceDefs.h"
#include "nsDirectoryServiceUtils.h"
#include "nsIFile.h"
#include "nsIIOService.h"
#include "nsIObserverService.h"
#include "nsISimpleEnumerator.h"
#include "nsLocalFile.h"
#include "nsNetUtil.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"
#include "nsThreadUtils.h"
#include "nsXULAppAPI.h"
#ifdef MOZ_NAIVEFOX
#  include "xpcpublic.h"
#endif

namespace mozilla::naivefox {

namespace {

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
                                  ProxyProtocol aProtocol) {
  if (mXPCOMInitialized || aProfilePath.IsEmpty()) {
    return NS_ERROR_INVALID_ARG;
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

  bool isDirectory = false;
  MOZ_TRY(profile->IsDirectory(&isDirectory));
  if (!isDirectory) {
    return NS_ERROR_FILE_NOT_DIRECTORY;
  }

  RefPtr<DirectoryProvider> provider =
      new DirectoryProvider(profile, mBinDirectory, mExecutable);
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

  nsCOMPtr<nsIObserverService> observers =
      do_GetService(NS_OBSERVERSERVICE_CONTRACTID);
  if (!observers) {
    return NS_ERROR_FAILURE;
  }
  nsresult storageRv = NS_OK;
  nsCOMPtr<mozIStorageService> storage =
      do_GetService("@mozilla.org/storage/service;1", &storageRv);
  MOZ_TRY(storageRv);
  MOZ_TRY(observers->NotifyObservers(nullptr, "profile-do-change", u"startup"));
  net_EnsurePSMInit();

  mIOService = do_GetIOService();
  return mIOService ? NS_OK : NS_ERROR_FAILURE;
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

  if (mXPCOMInitialized) {
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownNetTeardown);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownTeardown);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdown);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownQM);
    AppShutdown::AdvanceShutdownPhase(ShutdownPhase::AppShutdownTelemetry);
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

GeckoProcessType XRE_GetProcessType() { return GeckoProcessType_Default; }

const char* XRE_GetProcessTypeString() { return "default"; }

GeckoChildID XRE_GetChildID() { return 0; }

bool XRE_IsE10sParentProcess() { return false; }

#  define GECKO_PROCESS_TYPE(enum_value, enum_name, string_name,               \
                             proc_typename, process_bin_type,                  \
                             procinfo_typename, webidl_typename, allcaps_name) \
    bool XRE_Is##proc_typename##Process() { return enum_value == 0; }
#  include "mozilla/GeckoProcessTypes.h"
#  undef GECKO_PROCESS_TYPE

bool XRE_UseNativeEventProcessing() { return false; }

nsISerialEventTarget* XRE_GetAsyncIOEventTarget() {
  static nsCOMPtr<nsISerialEventTarget> sTarget =
      mozilla::GetMainThreadSerialEventTarget();
  return sTarget;
}

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
