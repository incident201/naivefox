/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsScriptSecurityManager.h"

#include "SystemPrincipal.h"
#include "mozilla/BasePrincipal.h"
#include "mozilla/ClearOnShutdown.h"
#include "mozilla/OriginAttributes.h"
#include "mozilla/StaticPtr.h"
#include "nsIChannel.h"
#include "nsIDomainPolicy.h"
#include "nsILoadInfo.h"
#include "nsIProtocolHandler.h"
#include "nsIURI.h"
#include "nsJSPrincipals.h"
#include "nsNetUtil.h"
#include "nsString.h"

using namespace mozilla;

StaticRefPtr<nsIIOService> nsScriptSecurityManager::sIOService;
std::atomic<bool> nsScriptSecurityManager::sStrictFileOriginPolicy = true;
nsIStringBundle* nsScriptSecurityManager::sStrBundle = nullptr;

static StaticRefPtr<nsScriptSecurityManager> gScriptSecMan;

namespace {

// The lean runtime intentionally keeps result and triggering principals
// system-owned. Safe Browsing is the narrow exception which needs the
// codebase principal of the final channel URI, matching the upstream
// GetChannelURIPrincipal contract without restoring the browser principal
// graph.
class NaiveFoxChannelURIPrincipal final : public BasePrincipal {
 public:
  NaiveFoxChannelURIPrincipal(nsIURI* aURI, const nsACString& aOrigin,
                              const OriginAttributes& aAttrs)
      : BasePrincipal(eContentPrincipal, aOrigin, aAttrs), mURI(aURI) {}

  NS_IMETHOD_(MozExternalRefCountType) AddRef() override {
    return nsJSPrincipals::AddRef();
  }
  NS_IMETHOD_(MozExternalRefCountType) Release() override {
    return nsJSPrincipals::Release();
  }
  NS_IMETHOD QueryInterface(REFNSIID aIID, void** aInstancePtr) override;
  NS_IMETHOD GetURI(nsIURI** aURI) override {
    NS_IF_ADDREF(*aURI = mURI);
    return NS_OK;
  }
  NS_IMETHOD GetDomain(nsIURI** aDomain) override {
    *aDomain = nullptr;
    return NS_OK;
  }
  NS_IMETHOD SetDomain(nsIURI*) override { return NS_ERROR_NOT_AVAILABLE; }
  NS_IMETHOD GetBaseDomain(nsACString& aBaseDomain) override {
    return mURI->GetHost(aBaseDomain);
  }
  NS_IMETHOD GetAddonId(nsAString& aAddonId) override {
    aAddonId.Truncate();
    return NS_OK;
  }
  NS_IMETHOD GetIsOriginPotentiallyTrustworthy(bool* aResult) override {
    *aResult = mURI->SchemeIs("https") || mURI->SchemeIs("wss");
    return NS_OK;
  }
  nsresult GetScriptLocation(nsACString& aStr) override {
    return mURI->GetSpec(aStr);
  }
  nsresult GetSiteIdentifier(SiteIdentifier& aSite) override {
    aSite.Init(this);
    return NS_OK;
  }
  bool IsContentPrincipal() const override { return true; }

 private:
  ~NaiveFoxChannelURIPrincipal() override = default;
  bool SubsumesInternal(nsIPrincipal* aOther,
                        DocumentDomainConsideration) override {
    nsCOMPtr<nsIURI> other;
    return aOther && NS_SUCCEEDED(aOther->GetURI(getter_AddRefs(other))) &&
           other && MayLoadInternal(other);
  }
  bool MayLoadInternal(nsIURI* aURI) override {
    bool equal = false;
    return aURI && NS_SUCCEEDED(mURI->EqualsExceptRef(aURI, &equal)) && equal;
  }

  nsCOMPtr<nsIURI> mURI;
};

NS_IMPL_QUERY_INTERFACE(NaiveFoxChannelURIPrincipal, nsIPrincipal)

}  // namespace

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
    nsIChannel* aChannel, nsIPrincipal** aResult) {
  NS_ENSURE_ARG_POINTER(aChannel);
  NS_ENSURE_ARG_POINTER(aResult);
  *aResult = nullptr;

  nsCOMPtr<nsIURI> uri;
  nsresult rv = NS_GetFinalChannelURI(aChannel, getter_AddRefs(uri));
  NS_ENSURE_SUCCESS(rv, rv);
  NS_ENSURE_TRUE(uri, NS_ERROR_UNEXPECTED);
  NS_ENSURE_TRUE(uri->SchemeIs("http") || uri->SchemeIs("https"),
                 NS_ERROR_NOT_AVAILABLE);

  nsCOMPtr<nsILoadInfo> loadInfo = aChannel->LoadInfo();
  NS_ENSURE_TRUE(loadInfo, NS_ERROR_UNEXPECTED);

  bool inheritsPrincipal = false;
  rv = NS_URIChainHasFlags(uri,
                           nsIProtocolHandler::URI_INHERITS_SECURITY_CONTEXT,
                           &inheritsPrincipal);
  NS_ENSURE_SUCCESS(rv, rv);
  NS_ENSURE_FALSE(inheritsPrincipal, NS_ERROR_NOT_AVAILABLE);

  nsAutoCString origin;
  nsAutoCString hostPort;
  rv = uri->GetAsciiHostPort(hostPort);
  NS_ENSURE_SUCCESS(rv, rv);
  NS_ENSURE_FALSE(hostPort.IsEmpty(), NS_ERROR_UNEXPECTED);
  rv = uri->GetScheme(origin);
  NS_ENSURE_SUCCESS(rv, rv);
  origin.AppendLiteral("://");
  origin.Append(hostPort);

  RefPtr<NaiveFoxChannelURIPrincipal> principal =
      new NaiveFoxChannelURIPrincipal(uri, origin,
                                      loadInfo->GetOriginAttributes());
  principal.forget(aResult);
  return NS_OK;
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
