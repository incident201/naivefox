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

struct NativeStylePreloadProcessDescriptor final {
  uint64_t mRootRequestId = 0;
  uint64_t mRootGeneration = 0;
  uint64_t mStyleRequestId = 0;
  uint32_t mDiscoverySequence = 0;
  nsString mUrl;
  nsString mCharset;
  nsString mCrossOrigin;
  nsString mMedia;
  nsString mReferrerPolicy;
  nsString mNonce;
  nsString mIntegrity;
  nsString mFetchPriority;
  bool mLinkPreload = false;
};

struct NativeStylePreloadProcessRootCallbacks final {
  std::function<nsresult()> mReady;
  std::function<nsresult(const NativeStylePreloadProcessDescriptor&)>
      mStyleDiscovered;
  std::function<void(uint32_t, uint32_t, uint32_t, nsresult)> mFinished;
  std::function<void(nsresult)> mFailed;
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

  static nsresult InitializeProcess();
  static bool IsProcessReady();
  static void ShutdownProcess();
  static nsresult StartProcessRoot(
      NativeRootReplacementActivationDescriptor&& aDescriptor,
      uint32_t aMaximumBodyBytes, bool aFullProcess,
      NativeStylePreloadProcessRootCallbacks&& aCallbacks,
      uint64_t& aRequestId);
  static nsresult ForwardProcessRootData(uint64_t aRequestId,
                                         uint64_t aGeneration,
                                         uint32_t aSequence, nsCString&& aData);
  static nsresult ForwardProcessRootStop(uint64_t aRequestId,
                                         uint64_t aGeneration,
                                         uint32_t aSequence, nsresult aStatus);
  static void CancelProcessRoot(uint64_t aRequestId, uint64_t aGeneration,
                                nsresult aStatus);
  static nsresult CompleteProcessStyle(uint64_t aStyleRequestId,
                                       nsresult aStatus);

  static nsresult RunProcessBootstrapAdmission();
};

int RunNativeStylePreloadActivationChild(int aArgc, char* aArgv[]);

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_NativeStylePreloadActivation_h
