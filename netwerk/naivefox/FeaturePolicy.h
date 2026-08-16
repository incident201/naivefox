/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_FeaturePolicy_h
#define mozilla_dom_FeaturePolicy_h

#include "mozilla/Maybe.h"
#include "nsCOMPtr.h"
#include "nsIPrincipal.h"
#include "nsString.h"
#include "nsTArray.h"

namespace mozilla::dom {

#ifdef MOZ_NAIVEFOX
enum class NoCorsMediaRequestState : uint8_t {
  NotAvailable,
  Initial,
  Subsequent,
};
#endif

// LoadInfo retains this value as passive request metadata.  NaiveFox never
// creates documents or iframe feature policies, so the DOM object itself is
// deliberately outside the standalone networking build.
struct FeaturePolicyInfo final {
  CopyableTArray<nsString> mInheritedDeniedFeatureNames;
  CopyableTArray<nsString> mAttributeEnabledFeatureNames;
  nsString mDeclaredString;
  nsCOMPtr<nsIPrincipal> mDefaultOrigin;
  nsCOMPtr<nsIPrincipal> mSelfOrigin;
  nsCOMPtr<nsIPrincipal> mSrcOrigin;
};

using MaybeFeaturePolicyInfo = Maybe<FeaturePolicyInfo>;

}  // namespace mozilla::dom

#endif
