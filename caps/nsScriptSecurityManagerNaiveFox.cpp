/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsScriptSecurityManager.h"

#include "SystemPrincipal.h"
#include "mozilla/BasePrincipal.h"
#include "mozilla/ClearOnShutdown.h"
#include "mozilla/StaticPtr.h"
#include "nsIChannel.h"
#include "nsIDomainPolicy.h"
#include "nsIURI.h"
#include "nsJSPrincipals.h"
#include "nsString.h"

using namespace mozilla;

StaticRefPtr<nsIIOService> nsScriptSecurityManager::sIOService;
std::atomic<bool> nsScriptSecurityManager::sStrictFileOriginPolicy = true;
nsIStringBundle* nsScriptSecurityManager::sStrBundle = nullptr;

static StaticRefPtr<nsScriptSecurityManager> gScriptSecMan;

NS_IMPL_ISUPPORTS(nsScriptSecurityManager, nsIScriptSecurityManager)

nsScriptSecurityManager::nsScriptSecurityManager()
    : mPrefInitialized(false), mIsJavaScriptEnabled(false) {}

nsScriptSecurityManager::~nsScriptSecurityManager() = default;

nsresult nsScriptSecurityManager::Init() {
  mSystemPrincipal = SystemPrincipal::Init();
  return mSystemPrincipal ? NS_OK : NS_ERROR_FAILURE;
}

void nsScriptSecurityManager::InitJSCallbacks(JSContext*) {}

void nsScriptSecurityManager::ClearJSCallbacks(JSContext*) {}

void nsScriptSecurityManager::Shutdown() {
  sIOService = nullptr;
  if (gScriptSecMan) {
    SystemPrincipal::Shutdown();
  }
}

nsScriptSecurityManager* nsScriptSecurityManager::GetScriptSecurityManager() {
  return gScriptSecMan;
}

void nsScriptSecurityManager::InitStatics() {
  RefPtr manager = new nsScriptSecurityManager();
  MOZ_RELEASE_ASSERT(NS_SUCCEEDED(manager->Init()));
  ClearOnShutdown(&gScriptSecMan);
  gScriptSecMan = std::move(manager);
}

already_AddRefed<SystemPrincipal>
nsScriptSecurityManager::SystemPrincipalSingletonConstructor() {
  if (!gScriptSecMan) {
    return nullptr;
  }
  return do_AddRef(gScriptSecMan->mSystemPrincipal).downcast<SystemPrincipal>();
}

bool nsScriptSecurityManager::SecurityCompareURIs(nsIURI* aSourceURI,
                                                   nsIURI* aTargetURI) {
  if (!aSourceURI || !aTargetURI) {
    return false;
  }
  bool equal = false;
  return NS_SUCCEEDED(aSourceURI->EqualsExceptRef(aTargetURI, &equal)) && equal;
}

bool nsScriptSecurityManager::IsHttpOrHttpsAndCrossOrigin(nsIURI* aUriA,
                                                           nsIURI* aUriB) {
  return aUriA && aUriB &&
         (aUriA->SchemeIs("http") || aUriA->SchemeIs("https")) &&
         !SecurityCompareURIs(aUriA, aUriB);
}

nsresult nsScriptSecurityManager::ReportError(const char*, nsIURI*, nsIURI*,
                                               bool, uint64_t) {
  return NS_OK;
}

nsresult nsScriptSecurityManager::ReportError(const char*, const nsACString&,
                                               const nsACString&, bool,
                                               uint64_t) {
  return NS_OK;
}

void nsScriptSecurityManager::DeactivateDomainPolicy() {
  mDomainPolicy = nullptr;
}

NS_IMETHODIMP nsScriptSecurityManager::CanCreateWrapper(
    JSContext*, const nsIID&, nsISupports*, nsIClassInfo*) {
  return NS_OK;
}

NS_IMETHODIMP nsScriptSecurityManager::CanCreateInstance(JSContext*,
                                                          const nsCID&) {
  return NS_OK;
}

NS_IMETHODIMP nsScriptSecurityManager::CanGetService(JSContext*,
                                                      const nsCID&) {
  return NS_OK;
}

NS_IMETHODIMP nsScriptSecurityManager::CheckLoadURIFromScript(JSContext*,
                                                               nsIURI*) {
  return NS_OK;
}

NS_IMETHODIMP nsScriptSecurityManager::CheckLoadURIWithPrincipal(
    nsIPrincipal* aPrincipal, nsIURI*, uint32_t, uint64_t) {
  return aPrincipal && BasePrincipal::Cast(aPrincipal)->IsSystemPrincipal()
             ? NS_OK
             : NS_ERROR_DOM_BAD_URI;
}

NS_IMETHODIMP nsScriptSecurityManager::CheckLoadURIWithPrincipalFromJS(
    nsIPrincipal* aPrincipal, nsIURI* aURI, uint32_t aFlags,
    uint64_t aInnerWindowID, JSContext*) {
  return CheckLoadURIWithPrincipal(aPrincipal, aURI, aFlags, aInnerWindowID);
}

NS_IMETHODIMP nsScriptSecurityManager::CheckLoadURIStrWithPrincipal(
    nsIPrincipal* aPrincipal, const nsACString&, uint32_t) {
  return aPrincipal && BasePrincipal::Cast(aPrincipal)->IsSystemPrincipal()
             ? NS_OK
             : NS_ERROR_DOM_BAD_URI;
}

NS_IMETHODIMP nsScriptSecurityManager::CheckLoadURIStrWithPrincipalFromJS(
    nsIPrincipal* aPrincipal, const nsACString& aURI, uint32_t aFlags,
    JSContext*) {
  return CheckLoadURIStrWithPrincipal(aPrincipal, aURI, aFlags);
}

NS_IMETHODIMP nsScriptSecurityManager::InFileURIAllowlist(nsIURI*,
                                                           bool* aResult) {
  *aResult = false;
  return NS_OK;
}

NS_IMETHODIMP nsScriptSecurityManager::GetSystemPrincipal(
    nsIPrincipal** aResult) {
  NS_IF_ADDREF(*aResult = mSystemPrincipal);
  return NS_OK;
}

#define NAIVEFOX_UNAVAILABLE_PRINCIPAL(method, args) \
  NS_IMETHODIMP nsScriptSecurityManager::method args { \
    *_retval = nullptr;                              \
    return NS_ERROR_NOT_AVAILABLE;                   \
  }

NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    GetLoadContextContentPrincipal,
    (nsIURI*, nsILoadContext*, nsIPrincipal** _retval))
NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    GetDocShellContentPrincipal,
    (nsIURI*, nsIDocShell*, nsIPrincipal** _retval))
NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    PrincipalWithOA,
    (nsIPrincipal*, JS::Handle<JS::Value>, JSContext*, nsIPrincipal** _retval))
NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    CreateContentPrincipal,
    (nsIURI*, JS::Handle<JS::Value>, JSContext*, nsIPrincipal** _retval))
NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    CreateContentPrincipalFromOrigin,
    (const nsACString&, nsIPrincipal** _retval))
NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    JSONToPrincipal, (const nsACString&, nsIPrincipal** _retval))
NAIVEFOX_UNAVAILABLE_PRINCIPAL(
    CreateNullPrincipal,
    (JS::Handle<JS::Value>, JSContext*, nsIPrincipal** _retval))

#undef NAIVEFOX_UNAVAILABLE_PRINCIPAL

NS_IMETHODIMP nsScriptSecurityManager::PrincipalToJSON(nsIPrincipal*,
                                                        nsACString& aResult) {
  aResult.Truncate();
  return NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP nsScriptSecurityManager::CheckSameOriginURI(nsIURI* aSource,
                                                           nsIURI* aTarget,
                                                           bool, bool) {
  return SecurityCompareURIs(aSource, aTarget) ? NS_OK
                                               : NS_ERROR_DOM_BAD_URI;
}

NS_IMETHODIMP nsScriptSecurityManager::GetChannelResultPrincipal(
    nsIChannel*, nsIPrincipal** aResult) {
  return GetSystemPrincipal(aResult);
}

NS_IMETHODIMP nsScriptSecurityManager::GetChannelResultStoragePrincipal(
    nsIChannel*, nsIPrincipal** aResult) {
  return GetSystemPrincipal(aResult);
}

NS_IMETHODIMP nsScriptSecurityManager::GetChannelResultPrincipals(
    nsIChannel*, nsIPrincipal** aPrincipal,
    nsIPrincipal** aPartitionedPrincipal) {
  nsresult rv = GetSystemPrincipal(aPrincipal);
  NS_ENSURE_SUCCESS(rv, rv);
  return GetSystemPrincipal(aPartitionedPrincipal);
}

nsresult nsScriptSecurityManager::GetChannelResultPrincipalIfNotSandboxed(
    nsIChannel*, nsIPrincipal** aResult) {
  return GetSystemPrincipal(aResult);
}

NS_IMETHODIMP nsScriptSecurityManager::GetChannelURIPrincipal(
    nsIChannel*, nsIPrincipal** aResult) {
  return GetSystemPrincipal(aResult);
}

NS_IMETHODIMP nsScriptSecurityManager::ActivateDomainPolicy(
    nsIDomainPolicy** aResult) {
  *aResult = nullptr;
  return NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP nsScriptSecurityManager::GetDomainPolicyActive(bool* aResult) {
  *aResult = false;
  return NS_OK;
}

NS_IMETHODIMP nsScriptSecurityManager::ActivateDomainPolicyInternal(
    nsIDomainPolicy** aResult) {
  *aResult = nullptr;
  return NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP_(void) nsScriptSecurityManager::CloneDomainPolicy(
    mozilla::dom::DomainPolicyClone*) {}

NS_IMETHODIMP nsScriptSecurityManager::PolicyAllowsScript(nsIURI*,
                                                           bool* aResult) {
  *aResult = false;
  return NS_OK;
}
