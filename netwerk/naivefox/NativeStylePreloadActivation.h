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

struct NativeRootReplacementActivationDescriptor final {
  uint64_t mChannelId = 0;
  nsCString mResourceSpec;
  nsCString mOriginalSpec;
  nsCString mOriginAttributesSuffix;
  nsCString mReferrerSpec;
  uint8_t mReferrerPolicy = 0;
  bool mSendReferrer = true;
  uint32_t mLoadFlags = 0;
  uint32_t mContentPolicyType = 0;
  uint32_t mHttpStatus = 0;
  nsCString mContentType;
  nsCString mCharset;
  uint64_t mGeneration = 0;
};

using NativeStylePreloadPrimaryCallback =
    std::function<nsresult(const NativeStylePreloadActivationDescriptor&)>;
using NativeStylePreloadFinalCallback = std::function<nsresult(nsresult)>;
using NativeRootReplacementPrimaryCallback =
    std::function<nsresult(const NativeRootReplacementActivationDescriptor&)>;
using NativeRootReplacementSetupCallback = std::function<nsresult(nsresult)>;
using NativeRootReplacementForwardedStartCallback = std::function<nsresult()>;
using NativeRootReplacementDataCallback = std::function<nsresult(nsCString&&)>;
using NativeRootReplacementStopCallback = std::function<nsresult(nsresult)>;

class NativeStylePreloadActivation final {
 public:
  static nsresult Initialize();
  static bool IsReady();
  static void Shutdown();

  static nsresult RegisterAndDispatch(
      NativeStylePreloadActivationDescriptor&& aDescriptor,
      NativeStylePreloadPrimaryCallback&& aPrimaryCallback,
      NativeStylePreloadFinalCallback&& aFinalCallback, uint64_t& aRequestId);
  static void CompleteStyle(uint64_t aRequestId, nsresult aStatus);
  static void Cancel(uint64_t aRequestId);

  static nsresult RegisterRootReplacement(
      NativeRootReplacementActivationDescriptor&& aDescriptor,
      NativeRootReplacementPrimaryCallback&& aPrimaryCallback,
      NativeRootReplacementSetupCallback&& aSetupCallback,
      NativeRootReplacementForwardedStartCallback&& aForwardedStartCallback,
      NativeRootReplacementDataCallback&& aDataCallback,
      NativeRootReplacementStopCallback&& aStopCallback, uint64_t& aRequestId);
  static nsresult ForwardRootReplacementData(uint64_t aRequestId,
                                             nsCString&& aData);
  static nsresult ForwardRootReplacementStop(uint64_t aRequestId,
                                             nsresult aStatus);
  static void NotifyRootReplacementRedirectVerificationRun(uint64_t aRequestId);
  static void ResolveRootReplacementRedirectVerification(uint64_t aRequestId,
                                                         nsresult aStatus);
  static void CompleteRootReplacement(uint64_t aRequestId, nsresult aStatus);
  static void CancelRootReplacement(uint64_t aRequestId);

  static nsresult RunProcessBootstrapAdmission();
};

int RunNativeStylePreloadActivationChild(int aArgc, char* aArgv[]);

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_NativeStylePreloadActivation_h
