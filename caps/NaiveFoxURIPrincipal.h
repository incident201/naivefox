/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_NaiveFoxURIPrincipal_h
#define mozilla_NaiveFoxURIPrincipal_h

#include "mozilla/BasePrincipal.h"
#include "mozilla/OriginAttributes.h"
#include "nsCOMPtr.h"
#include "nsJSPrincipals.h"

class nsIURI;

namespace mozilla {

// A deliberately narrow codebase principal for lean network channels.  It
// preserves the URI/origin contract used by Necko and Safe Browsing without
// linking ContentPrincipal's DOM, extension, wrapper, or serialization graph.
class NaiveFoxURIPrincipal final : public BasePrincipal {
 public:
  static already_AddRefed<NaiveFoxURIPrincipal> Create(
      nsIURI* aURI, const OriginAttributes& aAttrs);

  NS_IMETHOD_(MozExternalRefCountType) AddRef() override {
    return nsJSPrincipals::AddRef();
  }
  NS_IMETHOD_(MozExternalRefCountType) Release() override {
    return nsJSPrincipals::Release();
  }
  NS_IMETHOD QueryInterface(REFNSIID aIID, void** aInstancePtr) override;
  NS_IMETHOD GetURI(nsIURI** aURI) override;
  NS_IMETHOD GetDomain(nsIURI** aDomain) override;
  NS_IMETHOD SetDomain(nsIURI*) override;
  NS_IMETHOD GetBaseDomain(nsACString& aBaseDomain) override;
  NS_IMETHOD GetAddonId(nsAString& aAddonId) override;
  NS_IMETHOD GetIsOriginPotentiallyTrustworthy(bool* aResult) override;

  nsresult GetScriptLocation(nsACString& aStr) override;
  nsresult GetSiteIdentifier(SiteIdentifier& aSite) override;
  bool IsContentPrincipal() const override { return true; }

 private:
  NaiveFoxURIPrincipal(nsIURI* aURI, const nsACString& aOrigin,
                       const OriginAttributes& aAttrs);
  ~NaiveFoxURIPrincipal() override = default;

  bool SubsumesInternal(nsIPrincipal* aOther,
                        DocumentDomainConsideration) override;
  bool MayLoadInternal(nsIURI* aURI) override;

  nsCOMPtr<nsIURI> mURI;
};

}  // namespace mozilla

#endif  // mozilla_NaiveFoxURIPrincipal_h
