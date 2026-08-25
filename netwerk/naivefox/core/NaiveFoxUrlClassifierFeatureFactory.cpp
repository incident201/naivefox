/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "mozilla/net/UrlClassifierFeatureFactory.h"

#include "UrlClassifierFeaturePhishingProtection.h"

namespace mozilla::net {

void UrlClassifierFeatureFactory::Shutdown() {
  UrlClassifierFeaturePhishingProtection::MaybeShutdown();
}

void UrlClassifierFeatureFactory::GetFeaturesFromChannel(
    nsIChannel*, nsTArray<nsCOMPtr<nsIUrlClassifierFeature>>&) {}

void UrlClassifierFeatureFactory::GetCancelingFeaturesFromChannel(
    nsIChannel*, nsTArray<nsCOMPtr<nsIUrlClassifierFeature>>&) {}

void UrlClassifierFeatureFactory::GetNonCancelingFeaturesFromChannel(
    nsIChannel*, nsTArray<nsCOMPtr<nsIUrlClassifierFeature>>&) {}

void UrlClassifierFeatureFactory::GetPhishingProtectionFeatures(
    nsTArray<RefPtr<nsIUrlClassifierFeature>>& aFeatures) {
  UrlClassifierFeaturePhishingProtection::MaybeCreate(aFeatures);
}

void UrlClassifierFeatureFactory::GetRealTimeProtectionFeatures(
    nsTArray<RefPtr<nsIUrlClassifierFeature>>&) {}

already_AddRefed<nsIUrlClassifierFeature>
UrlClassifierFeatureFactory::GetFeatureByName(const nsACString& aName) {
  return UrlClassifierFeaturePhishingProtection::GetIfNameMatches(aName);
}

void UrlClassifierFeatureFactory::GetFeatureNames(
    nsTArray<nsCString>& aArray) {
  UrlClassifierFeaturePhishingProtection::GetFeatureNames(aArray);
}

already_AddRefed<nsIUrlClassifierFeature>
UrlClassifierFeatureFactory::CreateFeatureWithTables(
    const nsACString&, const nsTArray<nsCString>&,
    const nsTArray<nsCString>&) {
  return nullptr;
}

}  // namespace mozilla::net
