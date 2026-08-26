/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "mozilla/NaiveFoxURIPrincipal.h"

#include "nsIProtocolHandler.h"
#include "nsIURI.h"
#include "nsNetUtil.h"
#include "nsString.h"

namespace mozilla {

namespace {

nsresult GetHttpOrigin(nsIURI* aURI, nsACString& aOrigin) {
  NS_ENSURE_ARG_POINTER(aURI);
  if (!aURI->SchemeIs("http") && !aURI->SchemeIs("https")) {
    return NS_ERROR_NOT_AVAILABLE;
  }

  bool inheritsPrincipal = false;
  MOZ_TRY(NS_URIChainHasFlags(
      aURI, nsIProtocolHandler::URI_INHERITS_SECURITY_CONTEXT,
      &inheritsPrincipal));
  if (inheritsPrincipal) {
    return NS_ERROR_NOT_AVAILABLE;
  }

  nsAutoCString hostPort;
  MOZ_TRY(aURI->GetAsciiHostPort(hostPort));
  if (hostPort.IsEmpty()) {
    return NS_ERROR_MALFORMED_URI;
  }

  MOZ_TRY(aURI->GetScheme(aOrigin));
  aOrigin.AppendLiteral("://");
  aOrigin.Append(hostPort);
  return NS_OK;
}

}  // namespace

NaiveFoxURIPrincipal::NaiveFoxURIPrincipal(
    nsIURI* aURI, const nsACString& aOrigin,
    const OriginAttributes& aAttrs)
    : BasePrincipal(eContentPrincipal, aOrigin, aAttrs), mURI(aURI) {}

already_AddRefed<NaiveFoxURIPrincipal> NaiveFoxURIPrincipal::Create(
    nsIURI* aURI, const OriginAttributes& aAttrs) {
  nsAutoCString origin;
  if (NS_FAILED(GetHttpOrigin(aURI, origin))) {
    return nullptr;
  }
  RefPtr principal = new NaiveFoxURIPrincipal(aURI, origin, aAttrs);
  return principal.forget();
}

NS_IMPL_QUERY_INTERFACE(NaiveFoxURIPrincipal, nsIPrincipal)

NS_IMETHODIMP NaiveFoxURIPrincipal::GetURI(nsIURI** aURI) {
  NS_IF_ADDREF(*aURI = mURI);
  return NS_OK;
}

NS_IMETHODIMP NaiveFoxURIPrincipal::GetDomain(nsIURI** aDomain) {
  *aDomain = nullptr;
  return NS_OK;
}

NS_IMETHODIMP NaiveFoxURIPrincipal::SetDomain(nsIURI*) {
  return NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP NaiveFoxURIPrincipal::GetBaseDomain(nsACString& aBaseDomain) {
  return mURI->GetHost(aBaseDomain);
}

NS_IMETHODIMP NaiveFoxURIPrincipal::GetAddonId(nsAString& aAddonId) {
  aAddonId.Truncate();
  return NS_OK;
}

NS_IMETHODIMP NaiveFoxURIPrincipal::GetIsOriginPotentiallyTrustworthy(
    bool* aResult) {
  *aResult = mURI->SchemeIs("https") || mURI->SchemeIs("wss");
  return NS_OK;
}

nsresult NaiveFoxURIPrincipal::GetScriptLocation(nsACString& aStr) {
  return mURI->GetSpec(aStr);
}

nsresult NaiveFoxURIPrincipal::GetSiteIdentifier(SiteIdentifier& aSite) {
  aSite.Init(this);
  return NS_OK;
}

bool NaiveFoxURIPrincipal::SubsumesInternal(
    nsIPrincipal* aOther, DocumentDomainConsideration) {
  return aOther && FastEquals(aOther);
}

bool NaiveFoxURIPrincipal::MayLoadInternal(nsIURI* aURI) {
  nsAutoCString candidateOrigin;
  nsAutoCString principalOrigin;
  return NS_SUCCEEDED(GetHttpOrigin(aURI, candidateOrigin)) &&
         NS_SUCCEEDED(GetHttpOrigin(mURI, principalOrigin)) &&
         candidateOrigin == principalOrigin;
}

}  // namespace mozilla
