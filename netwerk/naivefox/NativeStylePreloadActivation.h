/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NativeStylePreloadActivation_h
#define netwerk_naivefox_NativeStylePreloadActivation_h

#include <cstdint>
#include <functional>

#include "nsError.h"
#include "nsString.h"

namespace mozilla::naivefox {

struct NativeStylePreloadActivationDescriptor final {
  nsCString mResourceSpec;
  nsCString mDocumentSpec;
  nsCString mReferrerSpec;
  nsCString mOriginAttributesSuffix;
  uint8_t mReferrerPolicy = 0;
  bool mSendReferrer = true;
  uint8_t mPreloadKind = 0;
};

using NativeStylePreloadPrimaryCallback =
    std::function<nsresult(const NativeStylePreloadActivationDescriptor&)>;
using NativeStylePreloadFinalCallback = std::function<nsresult(nsresult)>;

class NativeStylePreloadActivation final {
 public:
  static nsresult Initialize();
  static bool IsReady();
  static void Shutdown();

  static nsresult RegisterAndDispatch(
      NativeStylePreloadActivationDescriptor&& aDescriptor,
      NativeStylePreloadPrimaryCallback&& aPrimaryCallback,
      NativeStylePreloadFinalCallback&& aFinalCallback, uint64_t& aRequestId);
  static void Cancel(uint64_t aRequestId);
};

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_NativeStylePreloadActivation_h
