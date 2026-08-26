/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NativeStylePreloadActivation.h"

#include <atomic>
#include <utility>

#include "RuntimeLogging.h"
#include "mozilla/Assertions.h"
#include "mozilla/RefPtr.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "mozilla/StaticPtr.h"
#include "mozilla/UniquePtr.h"
#include "mozilla/ipc/Endpoint.h"
#include "mozilla/ipc/IOThread.h"
#include "mozilla/ipc/ProtocolUtils.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationChild.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationParent.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationRequestChild.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationRequestParent.h"
#include "nsCOMPtr.h"
#include "nsHashKeys.h"
#include "nsISerialEventTarget.h"
#include "nsISocketTransportService.h"
#include "nsIThread.h"
#include "nsNetCID.h"
#include "nsServiceManagerUtils.h"
#include "nsTHashMap.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

namespace {

using ipc::Endpoint;
using ipc::IPCResult;

enum class ActivationLeg : uint8_t { Primary = 1, Background = 2 };
enum class ActivationKind : uint8_t { Style = 1, RootReplacement = 2 };

constexpr uint8_t kPrimaryChildActor = 1 << 0;
constexpr uint8_t kPrimaryParentActor = 1 << 1;
constexpr uint8_t kBackgroundChildActor = 1 << 2;
constexpr uint8_t kBackgroundParentActor = 1 << 3;
constexpr uint8_t kPrimaryActorMask = kPrimaryChildActor | kPrimaryParentActor;
constexpr uint8_t kBackgroundActorMask =
    kBackgroundChildActor | kBackgroundParentActor;

class ActivationState;
class ActivationChild;
class ActivationParent;
class ActivationRequestChild;
class ActivationRequestParent;

StaticRefPtr<ActivationState> sActivationState;

bool IsKnownKind(uint8_t aKind) {
  return aKind == static_cast<uint8_t>(ActivationKind::Style) ||
         aKind == static_cast<uint8_t>(ActivationKind::RootReplacement);
}

bool IsKnownLeg(uint8_t aLeg) {
  return aLeg == static_cast<uint8_t>(ActivationLeg::Primary) ||
         aLeg == static_cast<uint8_t>(ActivationLeg::Background);
}

const char* KindLogPrefix(ActivationKind aKind) {
  return aKind == ActivationKind::Style ? "Native style activation"
                                        : "Native root replacement activation";
}

const char* LegLogName(ActivationLeg aLeg) {
  return aLeg == ActivationLeg::Primary ? "primary" : "background";
}

NativeStylePreloadActivationArgs SerializeStyleDescriptor(
    uint64_t aRequestId,
    const NativeStylePreloadActivationDescriptor& aDescriptor) {
  NativeStylePreloadActivationArgs args;
  args.requestId() = aRequestId;
  args.resourceSpec() = aDescriptor.mResourceSpec;
  args.documentSpec() = aDescriptor.mDocumentSpec;
  args.referrerSpec() = aDescriptor.mReferrerSpec;
  args.originAttributesSuffix() = aDescriptor.mOriginAttributesSuffix;
  args.referrerPolicy() = aDescriptor.mReferrerPolicy;
  args.sendReferrer() = aDescriptor.mSendReferrer;
  args.preloadKind() = aDescriptor.mPreloadKind;
  return args;
}

bool StyleDescriptorMatches(
    const NativeStylePreloadActivationArgs& aArgs,
    const NativeStylePreloadActivationDescriptor& aOther) {
  return aArgs.resourceSpec().Equals(aOther.mResourceSpec) &&
         aArgs.documentSpec().Equals(aOther.mDocumentSpec) &&
         aArgs.referrerSpec().Equals(aOther.mReferrerSpec) &&
         aArgs.originAttributesSuffix().Equals(
             aOther.mOriginAttributesSuffix) &&
         aArgs.referrerPolicy() == aOther.mReferrerPolicy &&
         aArgs.sendReferrer() == aOther.mSendReferrer &&
         aArgs.preloadKind() == aOther.mPreloadKind;
}

NativeStylePreloadActivationDescriptor DeserializeStyleDescriptor(
    const NativeStylePreloadActivationArgs& aArgs) {
  NativeStylePreloadActivationDescriptor descriptor;
  descriptor.mResourceSpec = aArgs.resourceSpec();
  descriptor.mDocumentSpec = aArgs.documentSpec();
  descriptor.mReferrerSpec = aArgs.referrerSpec();
  descriptor.mOriginAttributesSuffix = aArgs.originAttributesSuffix();
  descriptor.mReferrerPolicy = aArgs.referrerPolicy();
  descriptor.mSendReferrer = aArgs.sendReferrer();
  descriptor.mPreloadKind = aArgs.preloadKind();
  return descriptor;
}

NativeRootReplacementActivationArgs SerializeRootDescriptor(
    uint64_t aRequestId,
    const NativeRootReplacementActivationDescriptor& aDescriptor) {
  NativeRootReplacementActivationArgs args;
  args.requestId() = aRequestId;
  args.channelId() = aDescriptor.mChannelId;
  args.resourceSpec() = aDescriptor.mResourceSpec;
  args.originalSpec() = aDescriptor.mOriginalSpec;
  args.originAttributesSuffix() = aDescriptor.mOriginAttributesSuffix;
  args.referrerSpec() = aDescriptor.mReferrerSpec;
  args.referrerPolicy() = aDescriptor.mReferrerPolicy;
  args.sendReferrer() = aDescriptor.mSendReferrer;
  args.loadFlags() = aDescriptor.mLoadFlags;
  args.contentPolicyType() = aDescriptor.mContentPolicyType;
  args.httpStatus() = aDescriptor.mHttpStatus;
  args.contentType() = aDescriptor.mContentType;
  args.charset() = aDescriptor.mCharset;
  args.generation() = aDescriptor.mGeneration;
  return args;
}

bool RootDescriptorMatches(
    const NativeRootReplacementActivationArgs& aArgs,
    const NativeRootReplacementActivationDescriptor& aOther) {
  return aArgs.channelId() == aOther.mChannelId &&
         aArgs.resourceSpec().Equals(aOther.mResourceSpec) &&
         aArgs.originalSpec().Equals(aOther.mOriginalSpec) &&
         aArgs.originAttributesSuffix().Equals(
             aOther.mOriginAttributesSuffix) &&
         aArgs.referrerSpec().Equals(aOther.mReferrerSpec) &&
         aArgs.referrerPolicy() == aOther.mReferrerPolicy &&
         aArgs.sendReferrer() == aOther.mSendReferrer &&
         aArgs.loadFlags() == aOther.mLoadFlags &&
         aArgs.contentPolicyType() == aOther.mContentPolicyType &&
         aArgs.httpStatus() == aOther.mHttpStatus &&
         aArgs.contentType().Equals(aOther.mContentType) &&
         aArgs.charset().Equals(aOther.mCharset) &&
         aArgs.generation() == aOther.mGeneration;
}

NativeRootReplacementActivationDescriptor DeserializeRootDescriptor(
    const NativeRootReplacementActivationArgs& aArgs) {
  NativeRootReplacementActivationDescriptor descriptor;
  descriptor.mChannelId = aArgs.channelId();
  descriptor.mResourceSpec = aArgs.resourceSpec();
  descriptor.mOriginalSpec = aArgs.originalSpec();
  descriptor.mOriginAttributesSuffix = aArgs.originAttributesSuffix();
  descriptor.mReferrerSpec = aArgs.referrerSpec();
  descriptor.mReferrerPolicy = aArgs.referrerPolicy();
  descriptor.mSendReferrer = aArgs.sendReferrer();
  descriptor.mLoadFlags = aArgs.loadFlags();
  descriptor.mContentPolicyType = aArgs.contentPolicyType();
  descriptor.mHttpStatus = aArgs.httpStatus();
  descriptor.mContentType = aArgs.contentType();
  descriptor.mCharset = aArgs.charset();
  descriptor.mGeneration = aArgs.generation();
  return descriptor;
}

struct RequestActors final {
  RefPtr<ActivationRequestChild> mPrimaryChild;
  RefPtr<ActivationRequestParent> mPrimaryParent;
  RefPtr<ActivationRequestChild> mBackgroundChild;
  RefPtr<ActivationRequestParent> mBackgroundParent;
  uint8_t mExpectedActors = 0;
  uint8_t mDestroyedActors = 0;
  bool mPrimaryBound = false;
  bool mBackgroundBound = false;
  bool mBackgroundCreationPending = false;
  bool mPrimaryDeleteSent = false;
  bool mBackgroundDeleteSent = false;
  bool mPrimaryDestroyedLogged = false;
  bool mBackgroundDestroyedLogged = false;
};

class ActivationState final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationState)

  struct StyleEntry final {
    NativeStylePreloadActivationDescriptor mDescriptor;
    NativeStylePreloadPrimaryCallback mPrimaryCallback;
    NativeStylePreloadFinalCallback mFinalCallback;
    RequestActors mActors;
    bool mPrimaryReady = false;
    bool mBackgroundReady = false;
    bool mReleased = false;
    bool mTerminal = false;
  };

  struct RootEntry final {
    struct ForwardEvent final {
      nsCString mData;
      nsresult mStatus = NS_OK;
      bool mIsStop = false;
    };

    NativeRootReplacementActivationDescriptor mDescriptor;
    NativeRootReplacementActivationDescriptor mReceivedDescriptor;
    NativeRootReplacementPrimaryCallback mPrimaryCallback;
    NativeRootReplacementSetupCallback mSetupCallback;
    NativeRootReplacementForwardedStartCallback mForwardedStartCallback;
    NativeRootReplacementDataCallback mDataCallback;
    NativeRootReplacementStopCallback mStopCallback;
    nsTArray<ForwardEvent> mPendingForwardEvents;
    RequestActors mActors;
    bool mBeginReceived = false;
    bool mPrimaryLinked = false;
    bool mVerificationStarted = false;
    bool mVerificationRun = false;
    bool mVerificationResolved = false;
    bool mBackgroundReady = false;
    bool mReadyToVerifySent = false;
    bool mSetupFinished = false;
    bool mForwardSendPending = false;
    bool mForwardSent = false;
    bool mForwardReceived = false;
    bool mForwardDelivered = false;
    bool mForwardedStartCallbackSucceeded = false;
    bool mForwardStopQueued = false;
    bool mForwardStopReceived = false;
    bool mForwardStopDelivered = false;
    bool mReleased = false;
    bool mSetupCallbackSucceeded = false;
    bool mTerminal = false;
  };

  nsresult Initialize();
  bool IsReady() const;
  void Shutdown();

  nsresult RegisterStyle(NativeStylePreloadActivationDescriptor&& aDescriptor,
                         NativeStylePreloadPrimaryCallback&& aPrimaryCallback,
                         NativeStylePreloadFinalCallback&& aFinalCallback,
                         uint64_t& aRequestId);
  nsresult RegisterRoot(
      NativeRootReplacementActivationDescriptor&& aDescriptor,
      NativeRootReplacementPrimaryCallback&& aPrimaryCallback,
      NativeRootReplacementSetupCallback&& aSetupCallback,
      NativeRootReplacementForwardedStartCallback&& aForwardedStartCallback,
      NativeRootReplacementDataCallback&& aDataCallback,
      NativeRootReplacementStopCallback&& aStopCallback, uint64_t& aRequestId);
  nsresult ForwardRootData(uint64_t aRequestId, nsCString&& aData);
  nsresult ForwardRootStop(uint64_t aRequestId, nsresult aStatus);
  void CompleteStyleRequest(uint64_t aRequestId, nsresult aStatus);
  void CompleteRootRequest(uint64_t aRequestId, nsresult aStatus);
  void CancelStyle(uint64_t aRequestId);
  void CancelRoot(uint64_t aRequestId);
  void NotifyRootVerificationRun(uint64_t aRequestId);
  void ResolveRootVerification(uint64_t aRequestId, nsresult aStatus);

  void RecvWarmupAck(ActivationLeg aLeg);
  bool ValidateRequest(uint64_t aRequestId, ActivationKind aKind) const;
  void RequestParentBound(uint64_t aRequestId, ActivationKind aKind,
                          ActivationLeg aLeg, ActivationRequestParent* aActor);
  void RequestActorBound(uint64_t aRequestId, ActivationKind aKind,
                         ActivationLeg aLeg);
  void RequestActorDestroyed(uint64_t aRequestId, ActivationKind aKind,
                             uint8_t aActorBit);
  void BackgroundRequestConstructed(uint64_t aRequestId, ActivationKind aKind,
                                    ActivationRequestChild* aActor,
                                    bool aConstructorSucceeded);
  void RequestTransportFailed(uint64_t aRequestId, ActivationKind aKind,
                              nsresult aStatus);

  void RecvStyleOpen(NativeStylePreloadActivationArgs&& aArgs);
  void RecvStyleBackgroundReady(uint64_t aRequestId);
  void SendRootBegin(uint64_t aRequestId, ActivationRequestParent* aActor);
  void RecvRootBegin(NativeRootReplacementActivationArgs&& aArgs);
  void RecvRootConnect(uint64_t aRequestId, uint64_t aChannelId,
                       uint64_t aGeneration);
  void RecvRootBackgroundReady(uint64_t aRequestId, uint64_t aChannelId,
                               uint64_t aGeneration);
  void RecvRootReadyToVerify(uint64_t aRequestId, uint64_t aChannelId,
                             uint64_t aGeneration);
  void RecvRootSetupFinished(uint64_t aRequestId, uint64_t aChannelId,
                             uint64_t aGeneration);
  void RecvRootForwardedStart(uint64_t aRequestId, uint64_t aChannelId,
                              uint64_t aGeneration);
  void RecvRootData(uint64_t aRequestId, uint64_t aChannelId,
                    uint64_t aGeneration, nsCString&& aData);
  void RecvRootStop(uint64_t aRequestId, uint64_t aChannelId,
                    uint64_t aGeneration, nsresult aStatus);
  void RootForwardSent(uint64_t aRequestId, nsresult aStatus);

  void ActorFailed(ActivationLeg aLeg);

 private:
  ~ActivationState() = default;

  nsresult CreatePrimaryRequest(uint64_t aRequestId, ActivationKind aKind);
  nsresult DispatchBackgroundRequest(uint64_t aRequestId, ActivationKind aKind);
  RequestActors* LookupActors(uint64_t aRequestId, ActivationKind aKind);
  const RequestActors* LookupActors(uint64_t aRequestId,
                                    ActivationKind aKind) const;
  RootEntry* LookupRootEntry(uint64_t aRequestId);
  const RootEntry* LookupRootEntry(uint64_t aRequestId) const;
  void MaybeActivateStyle(uint64_t aRequestId);
  void ContinueRootVerification(uint64_t aRequestId);
  void ReleaseStyle(uint64_t aRequestId);
  void FailStyle(uint64_t aRequestId, nsresult aStatus, bool aInvokeCallback);
  void ReleaseRootSetup(uint64_t aRequestId);
  void MaybeDeliverRootForwardedStart(uint64_t aRequestId);
  void MaybeDrainRootForwardEvents(uint64_t aRequestId);
  void FailRoot(uint64_t aRequestId, nsresult aStatus);
  void BeginStyleTeardown(uint64_t aRequestId, nsresult aStatus,
                          bool aInvokeCallback);
  void BeginRootTeardown(uint64_t aRequestId, nsresult aStatus,
                         bool aInvokeCallback);
  void DestroyRequestActors(uint64_t aRequestId, ActivationKind aKind);
  void MaybeRemoveTerminalEntry(uint64_t aRequestId, ActivationKind aKind);
  void FailAll(nsresult aStatus);
  bool AllManagerActorsDestroyed() const;

  nsTHashMap<nsUint64HashKey, StyleEntry> mStyleEntries;
  // PLDHashTable stores its entry size in uint8_t and deliberately crashes
  // for inline entries larger than 255 bytes. Root replacement state carries
  // immutable channel metadata and callbacks, so keep only a pointer inline.
  nsTHashMap<nsUint64HashKey, UniquePtr<RootEntry>> mRootEntries;
  RefPtr<ActivationChild> mPrimaryChild;
  RefPtr<ActivationParent> mPrimaryParent;
  RefPtr<ActivationChild> mBackgroundChild;
  RefPtr<ActivationParent> mBackgroundParent;
  nsCOMPtr<nsISerialEventTarget> mSocketTarget;
  nsCOMPtr<nsIThread> mBackgroundThread;
  uint64_t mNextRequestId = 1;
  bool mPrimaryWarm = false;
  bool mBackgroundWarm = false;
  bool mFailed = false;
  bool mShuttingDown = false;
  std::atomic<bool> mBackgroundParentSetupPending{false};
  std::atomic<bool> mBackgroundChildSetupPending{false};
};

void DispatchManagerFailure(ActivationLeg aLeg) {
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NativeActivationManagerFailed", [aLeg]() {
        if (sActivationState) {
          sActivationState->ActorFailed(aLeg);
        }
      }));
}

void DispatchRequestDestroyed(uint64_t aRequestId, ActivationKind aKind,
                              uint8_t aActorBit) {
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NativeActivationRequestDestroyed",
      [aRequestId, aKind, aActorBit]() {
        if (sActivationState) {
          sActivationState->RequestActorDestroyed(aRequestId, aKind, aActorBit);
        }
      }));
}

class ActivationRequestParent final
    : public PNativeStylePreloadActivationRequestParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationRequestParent, override)

  ActivationRequestParent(uint64_t aRequestId, ActivationKind aKind,
                          ActivationLeg aLeg)
      : mRequestId(aRequestId), mKind(aKind), mLeg(aLeg) {}

 private:
  ~ActivationRequestParent() = default;

  bool IdentityMatches(uint64_t aRequestId, uint8_t aKind, uint8_t aLeg) const {
    return mRequestId == aRequestId && static_cast<uint8_t>(mKind) == aKind &&
           static_cast<uint8_t>(mLeg) == aLeg;
  }

  IPCResult RecvBind(const uint64_t& aRequestId, const uint8_t& aKind,
                     const uint8_t& aLeg) final {
    if (!IdentityMatches(aRequestId, aKind, aLeg) ||
        (mLeg == ActivationLeg::Primary) != NS_IsMainThread()) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (mLeg == ActivationLeg::Primary && sActivationState) {
      if (!sActivationState->ValidateRequest(mRequestId, mKind)) {
        return IPC_FAIL_NO_REASON(this);
      }
      sActivationState->RequestParentBound(mRequestId, mKind, mLeg, this);
    } else if (mLeg == ActivationLeg::Background) {
      RefPtr<ActivationRequestParent> self = this;
      nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::NativeActivationBackgroundRequestParentBound",
          [self = std::move(self), requestId = mRequestId, kind = mKind]() {
            if (sActivationState &&
                sActivationState->ValidateRequest(requestId, kind)) {
              sActivationState->RequestParentBound(
                  requestId, kind, ActivationLeg::Background, self);
            }
          }));
      if (NS_FAILED(rv)) {
        return IPC_FAIL_NO_REASON(this);
      }
    }
    if (!SendBound(mRequestId, static_cast<uint8_t>(mKind),
                   static_cast<uint8_t>(mLeg))) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (mKind == ActivationKind::RootReplacement &&
        mLeg == ActivationLeg::Primary && sActivationState) {
      sActivationState->SendRootBegin(mRequestId, this);
    }
    return IPC_OK();
  }

  IPCResult RecvStyleOpen(const NativeStylePreloadActivationArgs& aArgs) final {
    if (mKind != ActivationKind::Style || mLeg != ActivationLeg::Primary ||
        !NS_IsMainThread() || aArgs.requestId() != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (sActivationState) {
      sActivationState->RecvStyleOpen(NativeStylePreloadActivationArgs(aArgs));
    }
    return IPC_OK();
  }

  IPCResult RecvStyleBackgroundReady(const uint64_t& aRequestId) final {
    if (mKind != ActivationKind::Style || mLeg != ActivationLeg::Background ||
        NS_IsMainThread() || aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeStyleRequestBackgroundReady", [aRequestId]() {
          if (sActivationState) {
            sActivationState->RecvStyleBackgroundReady(aRequestId);
          }
        }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  IPCResult RecvConnectRootReplacement(const uint64_t& aRequestId,
                                       const uint64_t& aChannelId,
                                       const uint64_t& aGeneration) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Primary || !NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (sActivationState) {
      sActivationState->RecvRootConnect(aRequestId, aChannelId, aGeneration);
    }
    return IPC_OK();
  }

  IPCResult RecvRootReplacementSetupFinished(
      const uint64_t& aRequestId, const uint64_t& aChannelId,
      const uint64_t& aGeneration) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Primary || !NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (sActivationState) {
      sActivationState->RecvRootSetupFinished(aRequestId, aChannelId,
                                              aGeneration);
    }
    return IPC_OK();
  }

  IPCResult RecvRootReplacementBackgroundReady(
      const uint64_t& aRequestId, const uint64_t& aChannelId,
      const uint64_t& aGeneration) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Background || NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    nsresult rv = NS_DispatchToMainThread(
        NS_NewRunnableFunction("NaiveFox::NativeRootRequestBackgroundReady",
                               [aRequestId, aChannelId, aGeneration]() {
                                 if (sActivationState) {
                                   sActivationState->RecvRootBackgroundReady(
                                       aRequestId, aChannelId, aGeneration);
                                 }
                               }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    DispatchRequestDestroyed(mRequestId, mKind,
                             mLeg == ActivationLeg::Primary
                                 ? kPrimaryParentActor
                                 : kBackgroundParentActor);
  }

  const uint64_t mRequestId;
  const ActivationKind mKind;
  const ActivationLeg mLeg;
};

class ActivationRequestChild final
    : public PNativeStylePreloadActivationRequestChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationRequestChild, override)

  ActivationRequestChild(uint64_t aRequestId, ActivationKind aKind,
                         ActivationLeg aLeg)
      : mRequestId(aRequestId), mKind(aKind), mLeg(aLeg) {}

 private:
  ~ActivationRequestChild() = default;

  bool IdentityMatches(uint64_t aRequestId, uint8_t aKind, uint8_t aLeg) const {
    return mRequestId == aRequestId && static_cast<uint8_t>(mKind) == aKind &&
           static_cast<uint8_t>(mLeg) == aLeg;
  }

  IPCResult RecvBound(const uint64_t& aRequestId, const uint8_t& aKind,
                      const uint8_t& aLeg) final {
    if (!IdentityMatches(aRequestId, aKind, aLeg) ||
        (mLeg == ActivationLeg::Primary) != NS_IsMainThread()) {
      return IPC_FAIL_NO_REASON(this);
    }
    auto notify = [aRequestId, kind = mKind, leg = mLeg]() {
      if (sActivationState) {
        sActivationState->RequestActorBound(aRequestId, kind, leg);
      }
    };
    if (NS_IsMainThread()) {
      notify();
      return IPC_OK();
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeActivationRequestBound", std::move(notify)));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  IPCResult RecvBeginRootReplacement(
      const NativeRootReplacementActivationArgs& aArgs) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Primary || !NS_IsMainThread() ||
        aArgs.requestId() != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (sActivationState) {
      sActivationState->RecvRootBegin(
          NativeRootReplacementActivationArgs(aArgs));
    }
    return IPC_OK();
  }

  IPCResult RecvReadyToVerifyRootReplacement(
      const uint64_t& aRequestId, const uint64_t& aChannelId,
      const uint64_t& aGeneration) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Primary || !NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (sActivationState) {
      sActivationState->RecvRootReadyToVerify(aRequestId, aChannelId,
                                              aGeneration);
    }
    return IPC_OK();
  }

  IPCResult RecvForwardRootOnStart(const uint64_t& aRequestId,
                                   const uint64_t& aChannelId,
                                   const uint64_t& aGeneration) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Background || NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    nsresult rv = NS_DispatchToMainThread(
        NS_NewRunnableFunction("NaiveFox::NativeRootForwardOnStartReceived",
                               [aRequestId, aChannelId, aGeneration]() {
                                 if (sActivationState) {
                                   sActivationState->RecvRootForwardedStart(
                                       aRequestId, aChannelId, aGeneration);
                                 }
                               }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  IPCResult RecvForwardRootData(const uint64_t& aRequestId,
                                const uint64_t& aChannelId,
                                const uint64_t& aGeneration,
                                const nsACString& aData) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Background || NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeRootForwardDataReceived",
        [aRequestId, aChannelId, aGeneration,
         data = nsCString(aData)]() mutable {
          if (sActivationState) {
            sActivationState->RecvRootData(aRequestId, aChannelId, aGeneration,
                                           std::move(data));
          }
        }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  IPCResult RecvForwardRootStop(const uint64_t& aRequestId,
                                const uint64_t& aChannelId,
                                const uint64_t& aGeneration,
                                const nsresult& aStatus) final {
    if (mKind != ActivationKind::RootReplacement ||
        mLeg != ActivationLeg::Background || NS_IsMainThread() ||
        aRequestId != mRequestId) {
      return IPC_FAIL_NO_REASON(this);
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeRootForwardStopReceived",
        [aRequestId, aChannelId, aGeneration, aStatus]() {
          if (sActivationState) {
            sActivationState->RecvRootStop(aRequestId, aChannelId, aGeneration,
                                           aStatus);
          }
        }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    DispatchRequestDestroyed(mRequestId, mKind,
                             mLeg == ActivationLeg::Primary
                                 ? kPrimaryChildActor
                                 : kBackgroundChildActor);
  }

  const uint64_t mRequestId;
  const ActivationKind mKind;
  const ActivationLeg mLeg;
};

class ActivationParent final : public PNativeStylePreloadActivationParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationParent, override)

  explicit ActivationParent(ActivationLeg aLeg) : mLeg(aLeg) {}

  void MarkBound() { mBound.store(true); }
  bool WasBound() const { return mBound.load(); }
  bool WasDestroyed() const { return mDestroyed.load(); }

 private:
  ~ActivationParent() = default;

  IPCResult RecvWarmup(const uint8_t& aLeg) final {
    if (aLeg != static_cast<uint8_t>(mLeg) || !SendWarmupAck(aLeg)) {
      return IPC_FAIL_NO_REASON(this);
    }
    return IPC_OK();
  }

  already_AddRefed<PNativeStylePreloadActivationRequestParent>
  AllocPNativeStylePreloadActivationRequestParent(const uint64_t& aRequestId,
                                                  const uint8_t& aKind,
                                                  const uint8_t& aLeg) final {
    if (!aRequestId || !IsKnownKind(aKind) || !IsKnownLeg(aLeg) ||
        aLeg != static_cast<uint8_t>(mLeg) ||
        (mLeg == ActivationLeg::Primary) != NS_IsMainThread()) {
      return nullptr;
    }
    return MakeAndAddRef<ActivationRequestParent>(
        aRequestId, static_cast<ActivationKind>(aKind), mLeg);
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    mDestroyed.store(true);
    DispatchManagerFailure(mLeg);
  }

  const ActivationLeg mLeg;
  std::atomic<bool> mBound{false};
  std::atomic<bool> mDestroyed{false};
};

class ActivationChild final : public PNativeStylePreloadActivationChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationChild, override)

  explicit ActivationChild(ActivationLeg aLeg) : mLeg(aLeg) {}

  void MarkBound() { mBound.store(true); }
  bool WasBound() const { return mBound.load(); }
  bool WasDestroyed() const { return mDestroyed.load(); }

 private:
  ~ActivationChild() = default;

  IPCResult RecvWarmupAck(const uint8_t& aLeg) final {
    if (aLeg != static_cast<uint8_t>(mLeg)) {
      return IPC_FAIL_NO_REASON(this);
    }
    auto notify = [leg = mLeg]() {
      if (sActivationState) {
        sActivationState->RecvWarmupAck(leg);
      }
    };
    if (mLeg == ActivationLeg::Primary) {
      if (!NS_IsMainThread()) {
        return IPC_FAIL_NO_REASON(this);
      }
      notify();
      return IPC_OK();
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeActivationBackgroundWarm", std::move(notify)));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    mDestroyed.store(true);
    DispatchManagerFailure(mLeg);
  }

  const ActivationLeg mLeg;
  std::atomic<bool> mBound{false};
  std::atomic<bool> mDestroyed{false};
};

nsresult ActivationState::Initialize() {
  MOZ_ASSERT(NS_IsMainThread());

  if (!ipc::IOThread::Get()) {
    ipc::IOThread::Startup();
  }
  mSocketTarget = do_GetService(NS_SOCKETTRANSPORTSERVICE_CONTRACTID);
  if (!mSocketTarget) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(
      NS_NewNamedThread("NF Style IPC BG", getter_AddRefs(mBackgroundThread)));

  Endpoint<PNativeStylePreloadActivationParent> primaryParentEndpoint;
  Endpoint<PNativeStylePreloadActivationChild> primaryChildEndpoint;
  MOZ_TRY(PNativeStylePreloadActivation::CreateEndpoints(
      &primaryParentEndpoint, &primaryChildEndpoint));
  mPrimaryParent = new ActivationParent(ActivationLeg::Primary);
  mPrimaryChild = new ActivationChild(ActivationLeg::Primary);
  if (!primaryParentEndpoint.Bind(mPrimaryParent)) {
    return NS_ERROR_FAILURE;
  }
  mPrimaryParent->MarkBound();
  if (!primaryChildEndpoint.Bind(mPrimaryChild)) {
    return NS_ERROR_FAILURE;
  }
  mPrimaryChild->MarkBound();
  if (!mPrimaryChild->SendWarmup(
          static_cast<uint8_t>(ActivationLeg::Primary))) {
    return NS_ERROR_FAILURE;
  }

  Endpoint<PNativeStylePreloadActivationParent> backgroundParentEndpoint;
  Endpoint<PNativeStylePreloadActivationChild> backgroundChildEndpoint;
  MOZ_TRY(PNativeStylePreloadActivation::CreateEndpoints(
      &backgroundParentEndpoint, &backgroundChildEndpoint));
  mBackgroundParent = new ActivationParent(ActivationLeg::Background);
  mBackgroundChild = new ActivationChild(ActivationLeg::Background);

  mBackgroundParentSetupPending.store(true);
  RefPtr<ActivationParent> backgroundParent = mBackgroundParent;
  RefPtr<ActivationState> backgroundParentState = this;
  nsresult rv = mBackgroundThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::BindNativeActivationBackgroundManagerParent",
      [actor = std::move(backgroundParent),
       state = std::move(backgroundParentState),
       endpoint = std::move(backgroundParentEndpoint)]() mutable {
        if (!endpoint.Bind(actor)) {
          DispatchManagerFailure(ActivationLeg::Background);
        } else {
          actor->MarkBound();
        }
        state->mBackgroundParentSetupPending.store(false);
      }));
  if (NS_FAILED(rv)) {
    mBackgroundParentSetupPending.store(false);
    return rv;
  }

  mBackgroundChildSetupPending.store(true);
  RefPtr<ActivationChild> backgroundChild = mBackgroundChild;
  RefPtr<ActivationState> backgroundChildState = this;
  rv = mSocketTarget->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::BindNativeActivationBackgroundManagerChild",
      [actor = std::move(backgroundChild),
       state = std::move(backgroundChildState),
       endpoint = std::move(backgroundChildEndpoint)]() mutable {
        if (!endpoint.Bind(actor)) {
          DispatchManagerFailure(ActivationLeg::Background);
        } else {
          actor->MarkBound();
          if (!actor->SendWarmup(
                  static_cast<uint8_t>(ActivationLeg::Background))) {
            DispatchManagerFailure(ActivationLeg::Background);
          }
        }
        state->mBackgroundChildSetupPending.store(false);
      }));
  if (NS_FAILED(rv)) {
    mBackgroundChildSetupPending.store(false);
    return rv;
  }

  RuntimeLogEvent("Native style activation phase=initialize-started\n");
  return NS_OK;
}

bool ActivationState::IsReady() const {
  MOZ_ASSERT(NS_IsMainThread());
  return !mFailed && !mShuttingDown && mPrimaryWarm && mBackgroundWarm;
}

void ActivationState::RecvWarmupAck(ActivationLeg aLeg) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mFailed || mShuttingDown) {
    return;
  }
  bool& warm = aLeg == ActivationLeg::Primary ? mPrimaryWarm : mBackgroundWarm;
  if (warm) {
    ActorFailed(aLeg);
    return;
  }
  warm = true;
  RuntimeLogEvent("Native style activation phase=%s-ready\n", LegLogName(aLeg));
  if (IsReady()) {
    RuntimeLogEvent("Native style activation phase=bridge-ready\n");
  }
}

RequestActors* ActivationState::LookupActors(uint64_t aRequestId,
                                             ActivationKind aKind) {
  if (aKind == ActivationKind::Style) {
    StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
    return entry ? &entry->mActors : nullptr;
  }
  RootEntry* entry = LookupRootEntry(aRequestId);
  return entry ? &entry->mActors : nullptr;
}

const RequestActors* ActivationState::LookupActors(uint64_t aRequestId,
                                                   ActivationKind aKind) const {
  if (aKind == ActivationKind::Style) {
    const StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
    return entry ? &entry->mActors : nullptr;
  }
  const RootEntry* entry = LookupRootEntry(aRequestId);
  return entry ? &entry->mActors : nullptr;
}

ActivationState::RootEntry* ActivationState::LookupRootEntry(
    uint64_t aRequestId) {
  UniquePtr<RootEntry>* entry = mRootEntries.Lookup(aRequestId).DataPtrOrNull();
  return entry ? entry->get() : nullptr;
}

const ActivationState::RootEntry* ActivationState::LookupRootEntry(
    uint64_t aRequestId) const {
  const UniquePtr<RootEntry>* entry =
      mRootEntries.Lookup(aRequestId).DataPtrOrNull();
  return entry ? entry->get() : nullptr;
}

bool ActivationState::ValidateRequest(uint64_t aRequestId,
                                      ActivationKind aKind) const {
  MOZ_ASSERT(NS_IsMainThread());
  return LookupActors(aRequestId, aKind) != nullptr;
}

nsresult ActivationState::CreatePrimaryRequest(uint64_t aRequestId,
                                               ActivationKind aKind) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors || actors->mPrimaryChild) {
    return NS_ERROR_UNEXPECTED;
  }
  RefPtr<ActivationRequestChild> actor =
      new ActivationRequestChild(aRequestId, aKind, ActivationLeg::Primary);
  actors->mPrimaryChild = actor;
  actors->mExpectedActors |= kPrimaryChildActor;
  RuntimeLogEvent("%s phase=request-primary-actor-created request=%llu\n",
                  KindLogPrefix(aKind),
                  static_cast<unsigned long long>(aRequestId));
  if (!mPrimaryChild->SendPNativeStylePreloadActivationRequestConstructor(
          actor, aRequestId, static_cast<uint8_t>(aKind),
          static_cast<uint8_t>(ActivationLeg::Primary))) {
    return NS_ERROR_FAILURE;
  }
  actors = LookupActors(aRequestId, aKind);
  if (!actors) {
    return NS_ERROR_ABORT;
  }
  actors->mExpectedActors |= kPrimaryParentActor;
  if (!actor->SendBind(aRequestId, static_cast<uint8_t>(aKind),
                       static_cast<uint8_t>(ActivationLeg::Primary))) {
    return NS_ERROR_FAILURE;
  }
  return NS_OK;
}

nsresult ActivationState::DispatchBackgroundRequest(uint64_t aRequestId,
                                                    ActivationKind aKind) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors || actors->mBackgroundCreationPending ||
      actors->mBackgroundChild) {
    return NS_ERROR_UNEXPECTED;
  }
  actors->mBackgroundCreationPending = true;
  RefPtr<ActivationChild> manager = mBackgroundChild;
  nsresult rv = mSocketTarget->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::CreateNativeActivationBackgroundRequest",
      [manager = std::move(manager), aRequestId, aKind]() {
        RefPtr<ActivationRequestChild> actor = new ActivationRequestChild(
            aRequestId, aKind, ActivationLeg::Background);
        RuntimeLogEvent(
            "%s phase=request-background-actor-created request=%llu\n",
            KindLogPrefix(aKind), static_cast<unsigned long long>(aRequestId));
        const bool constructed =
            manager->SendPNativeStylePreloadActivationRequestConstructor(
                actor, aRequestId, static_cast<uint8_t>(aKind),
                static_cast<uint8_t>(ActivationLeg::Background));
        nsresult dispatchRv = NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::NativeActivationBackgroundRequestConstructed",
            [actor, aRequestId, aKind, constructed]() {
              if (sActivationState) {
                sActivationState->BackgroundRequestConstructed(
                    aRequestId, aKind, actor, constructed);
              }
            }));
        if (NS_FAILED(dispatchRv)) {
          if (constructed) {
            (void)PNativeStylePreloadActivationRequestChild::Send__delete__(
                actor);
          }
          return;
        }
        if (constructed &&
            !actor->SendBind(aRequestId, static_cast<uint8_t>(aKind),
                             static_cast<uint8_t>(ActivationLeg::Background))) {
          (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
              "NaiveFox::NativeActivationBackgroundBindFailed",
              [aRequestId, aKind]() {
                if (sActivationState) {
                  sActivationState->RequestTransportFailed(aRequestId, aKind,
                                                           NS_ERROR_FAILURE);
                }
              }));
        }
      }));
  if (NS_FAILED(rv)) {
    actors->mBackgroundCreationPending = false;
  }
  return rv;
}

void ActivationState::BackgroundRequestConstructed(
    uint64_t aRequestId, ActivationKind aKind, ActivationRequestChild* aActor,
    bool aConstructorSucceeded) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors) {
    return;
  }
  actors->mBackgroundCreationPending = false;
  actors->mBackgroundChild = aActor;
  actors->mExpectedActors |= kBackgroundChildActor;
  if (aConstructorSucceeded) {
    actors->mExpectedActors |= kBackgroundParentActor;
  } else {
    RequestTransportFailed(aRequestId, aKind, NS_ERROR_FAILURE);
  }
  DestroyRequestActors(aRequestId, aKind);
  MaybeRemoveTerminalEntry(aRequestId, aKind);
}

nsresult ActivationState::RegisterStyle(
    NativeStylePreloadActivationDescriptor&& aDescriptor,
    NativeStylePreloadPrimaryCallback&& aPrimaryCallback,
    NativeStylePreloadFinalCallback&& aFinalCallback, uint64_t& aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  aRequestId = 0;
  if (!IsReady() || !aPrimaryCallback || !aFinalCallback ||
      aDescriptor.mResourceSpec.IsEmpty() ||
      aDescriptor.mDocumentSpec.IsEmpty()) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  if (!mNextRequestId) {
    return NS_ERROR_OUT_OF_MEMORY;
  }
  const uint64_t requestId = mNextRequestId++;
  StyleEntry entry{std::move(aDescriptor), std::move(aPrimaryCallback),
                   std::move(aFinalCallback)};
  mStyleEntries.InsertOrUpdate(requestId, std::move(entry));
  aRequestId = requestId;
  RuntimeLogEvent(
      "Native style activation phase=descriptor-frozen request=%llu\n",
      static_cast<unsigned long long>(requestId));

  nsresult rv = CreatePrimaryRequest(requestId, ActivationKind::Style);
  if (NS_FAILED(rv)) {
    FailStyle(requestId, rv, true);
    aRequestId = 0;
    return rv;
  }
  rv = DispatchBackgroundRequest(requestId, ActivationKind::Style);
  if (NS_FAILED(rv)) {
    FailStyle(requestId, rv, true);
    aRequestId = 0;
    return rv;
  }
  RuntimeLogEvent(
      "Native style activation phase=background-dispatched request=%llu\n",
      static_cast<unsigned long long>(requestId));
  return NS_OK;
}

nsresult ActivationState::RegisterRoot(
    NativeRootReplacementActivationDescriptor&& aDescriptor,
    NativeRootReplacementPrimaryCallback&& aPrimaryCallback,
    NativeRootReplacementSetupCallback&& aSetupCallback,
    NativeRootReplacementForwardedStartCallback&& aForwardedStartCallback,
    NativeRootReplacementDataCallback&& aDataCallback,
    NativeRootReplacementStopCallback&& aStopCallback, uint64_t& aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  aRequestId = 0;
  if (!IsReady() || !aPrimaryCallback || !aSetupCallback ||
      !aForwardedStartCallback || !aDataCallback || !aStopCallback ||
      !aDescriptor.mChannelId || aDescriptor.mResourceSpec.IsEmpty() ||
      aDescriptor.mOriginalSpec.IsEmpty()) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  if (!mNextRequestId) {
    return NS_ERROR_OUT_OF_MEMORY;
  }
  const uint64_t requestId = mNextRequestId++;
  auto entry = MakeUnique<RootEntry>();
  entry->mDescriptor = std::move(aDescriptor);
  entry->mPrimaryCallback = std::move(aPrimaryCallback);
  entry->mSetupCallback = std::move(aSetupCallback);
  entry->mForwardedStartCallback = std::move(aForwardedStartCallback);
  entry->mDataCallback = std::move(aDataCallback);
  entry->mStopCallback = std::move(aStopCallback);
  mRootEntries.InsertOrUpdate(requestId, std::move(entry));
  aRequestId = requestId;
  RuntimeLogEvent(
      "Native root replacement activation phase=descriptor-frozen "
      "request=%llu\n",
      static_cast<unsigned long long>(requestId));

  nsresult rv =
      CreatePrimaryRequest(requestId, ActivationKind::RootReplacement);
  if (NS_FAILED(rv)) {
    FailRoot(requestId, rv);
    aRequestId = 0;
    return rv;
  }
  return NS_OK;
}

nsresult ActivationState::ForwardRootData(uint64_t aRequestId,
                                          nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  if (!entry->mReleased || !entry->mSetupCallbackSucceeded ||
      entry->mForwardStopQueued || !entry->mActors.mBackgroundParent) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return NS_ERROR_UNEXPECTED;
  }

  const uint64_t channelId = entry->mDescriptor.mChannelId;
  const uint64_t generation = entry->mDescriptor.mGeneration;
  RefPtr<ActivationRequestParent> actor = entry->mActors.mBackgroundParent;
  RefPtr<ActivationState> self = this;
  nsresult rv = mBackgroundThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::NativeRootForwardDataSend",
      [self = std::move(self), actor = std::move(actor), aRequestId, channelId,
       generation, data = std::move(aData)]() mutable {
        if (actor->SendForwardRootData(aRequestId, channelId, generation,
                                       data)) {
          return;
        }
        (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::NativeRootForwardDataFailed",
            [self = std::move(self), aRequestId]() {
              self->RequestTransportFailed(aRequestId,
                                           ActivationKind::RootReplacement,
                                           NS_ERROR_FAILURE);
            }));
      }));
  if (NS_FAILED(rv)) {
    FailRoot(aRequestId, rv);
  }
  return rv;
}

nsresult ActivationState::ForwardRootStop(uint64_t aRequestId,
                                          nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  if (!entry->mReleased || !entry->mSetupCallbackSucceeded ||
      entry->mForwardStopQueued || !entry->mActors.mBackgroundParent) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return NS_ERROR_UNEXPECTED;
  }
  entry->mForwardStopQueued = true;

  const uint64_t channelId = entry->mDescriptor.mChannelId;
  const uint64_t generation = entry->mDescriptor.mGeneration;
  RefPtr<ActivationRequestParent> actor = entry->mActors.mBackgroundParent;
  RefPtr<ActivationState> self = this;
  nsresult rv = mBackgroundThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::NativeRootForwardStopSend",
      [self = std::move(self), actor = std::move(actor), aRequestId, channelId,
       generation, aStatus]() {
        if (actor->SendForwardRootStop(aRequestId, channelId, generation,
                                       aStatus)) {
          return;
        }
        (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::NativeRootForwardStopFailed",
            [self = std::move(self), aRequestId]() {
              self->RequestTransportFailed(aRequestId,
                                           ActivationKind::RootReplacement,
                                           NS_ERROR_FAILURE);
            }));
      }));
  if (NS_FAILED(rv)) {
    FailRoot(aRequestId, rv);
  }
  return rv;
}

void ActivationState::RequestParentBound(uint64_t aRequestId,
                                         ActivationKind aKind,
                                         ActivationLeg aLeg,
                                         ActivationRequestParent* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors) {
    return;
  }
  if (aLeg == ActivationLeg::Primary) {
    actors->mPrimaryParent = aActor;
  } else {
    actors->mBackgroundParent = aActor;
    if (aKind == ActivationKind::RootReplacement) {
      RootEntry* entry = LookupRootEntry(aRequestId);
      if (entry && !entry->mTerminal) {
        ContinueRootVerification(aRequestId);
        entry = LookupRootEntry(aRequestId);
        if (entry && !entry->mTerminal && entry->mSetupFinished) {
          ReleaseRootSetup(aRequestId);
        }
      }
    }
  }
}

void ActivationState::RequestActorBound(uint64_t aRequestId,
                                        ActivationKind aKind,
                                        ActivationLeg aLeg) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors) {
    return;
  }
  bool& bound = aLeg == ActivationLeg::Primary ? actors->mPrimaryBound
                                               : actors->mBackgroundBound;
  if (bound) {
    RequestTransportFailed(aRequestId, aKind, NS_ERROR_UNEXPECTED);
    return;
  }
  bound = true;
  RuntimeLogEvent("%s phase=request-%s-actor-bound request=%llu\n",
                  KindLogPrefix(aKind), LegLogName(aLeg),
                  static_cast<unsigned long long>(aRequestId));

  if (aKind == ActivationKind::Style) {
    StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
    if (!entry || entry->mTerminal) {
      DestroyRequestActors(aRequestId, aKind);
      return;
    }
    if (aLeg == ActivationLeg::Primary) {
      NativeStylePreloadActivationArgs args =
          SerializeStyleDescriptor(aRequestId, entry->mDescriptor);
      if (!actors->mPrimaryChild->SendStyleOpen(args)) {
        FailStyle(aRequestId, NS_ERROR_FAILURE, true);
        return;
      }
      RuntimeLogEvent(
          "Native style activation phase=child-open-sent request=%llu\n",
          static_cast<unsigned long long>(aRequestId));
    } else {
      RefPtr<ActivationRequestChild> actor = actors->mBackgroundChild;
      nsresult rv = mSocketTarget->Dispatch(NS_NewRunnableFunction(
          "NaiveFox::NativeStyleRequestSendBackgroundReady",
          [actor = std::move(actor), aRequestId]() {
            if (!actor->SendStyleBackgroundReady(aRequestId)) {
              (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
                  "NaiveFox::NativeStyleRequestBackgroundSendFailed",
                  [aRequestId]() {
                    if (sActivationState) {
                      sActivationState->RequestTransportFailed(
                          aRequestId, ActivationKind::Style, NS_ERROR_FAILURE);
                    }
                  }));
              return;
            }
            RuntimeLogEvent(
                "Native style activation phase=bg-ready-sent request=%llu\n",
                static_cast<unsigned long long>(aRequestId));
          }));
      if (NS_FAILED(rv)) {
        FailStyle(aRequestId, rv, true);
      }
    }
    return;
  }

  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    DestroyRequestActors(aRequestId, aKind);
    return;
  }
  if (aLeg == ActivationLeg::Background) {
    RefPtr<ActivationRequestChild> actor = actors->mBackgroundChild;
    const uint64_t channelId = entry->mDescriptor.mChannelId;
    const uint64_t generation = entry->mDescriptor.mGeneration;
    nsresult rv = mSocketTarget->Dispatch(NS_NewRunnableFunction(
        "NaiveFox::NativeRootRequestSendBackgroundReady",
        [actor = std::move(actor), aRequestId, channelId, generation]() {
          if (!actor->SendRootReplacementBackgroundReady(aRequestId, channelId,
                                                         generation)) {
            (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
                "NaiveFox::NativeRootRequestBackgroundSendFailed",
                [aRequestId]() {
                  if (sActivationState) {
                    sActivationState->RequestTransportFailed(
                        aRequestId, ActivationKind::RootReplacement,
                        NS_ERROR_FAILURE);
                  }
                }));
          }
        }));
    if (NS_FAILED(rv)) {
      FailRoot(aRequestId, rv);
    }
  }
}

void ActivationState::SendRootBegin(uint64_t aRequestId,
                                    ActivationRequestParent* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  NativeRootReplacementActivationArgs args =
      SerializeRootDescriptor(aRequestId, entry->mDescriptor);
  if (!aActor->SendBeginRootReplacement(args)) {
    FailRoot(aRequestId, NS_ERROR_FAILURE);
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation phase=begin-sent request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
}

void ActivationState::RecvStyleOpen(NativeStylePreloadActivationArgs&& aArgs) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aArgs.requestId()).DataPtrOrNull();
  if (!entry || entry->mTerminal) {
    return;
  }
  if (entry->mPrimaryReady ||
      !StyleDescriptorMatches(aArgs, entry->mDescriptor)) {
    FailStyle(aArgs.requestId(), NS_ERROR_UNEXPECTED, true);
    return;
  }
  NativeStylePreloadPrimaryCallback callback =
      std::move(entry->mPrimaryCallback);
  NativeStylePreloadActivationDescriptor receivedDescriptor =
      DeserializeStyleDescriptor(aArgs);
  nsresult rv = callback(receivedDescriptor);
  entry = mStyleEntries.Lookup(aArgs.requestId()).DataPtrOrNull();
  if (!entry || entry->mTerminal) {
    return;
  }
  if (NS_FAILED(rv)) {
    FailStyle(aArgs.requestId(), rv, true);
    return;
  }
  entry->mPrimaryReady = true;
  RuntimeLogEvent(
      "Native style activation phase=parent-channel-created request=%llu\n",
      static_cast<unsigned long long>(aArgs.requestId()));
  MaybeActivateStyle(aArgs.requestId());
}

void ActivationState::RecvStyleBackgroundReady(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
  if (!entry || entry->mTerminal) {
    return;
  }
  if (entry->mBackgroundReady) {
    FailStyle(aRequestId, NS_ERROR_UNEXPECTED, true);
    return;
  }
  entry->mBackgroundReady = true;
  RuntimeLogEvent(
      "Native style activation phase=background-ready-received request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  MaybeActivateStyle(aRequestId);
}

void ActivationState::MaybeActivateStyle(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
  if (entry && !entry->mTerminal && entry->mPrimaryReady &&
      entry->mBackgroundReady && entry->mActors.mPrimaryBound &&
      entry->mActors.mBackgroundBound) {
    ReleaseStyle(aRequestId);
  }
}

void ActivationState::RecvRootBegin(
    NativeRootReplacementActivationArgs&& aArgs) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aArgs.requestId());
  if (!entry || entry->mTerminal) {
    return;
  }
  if (entry->mBeginReceived ||
      !RootDescriptorMatches(aArgs, entry->mDescriptor)) {
    FailRoot(aArgs.requestId(), NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mBeginReceived = true;
  entry->mReceivedDescriptor = DeserializeRootDescriptor(aArgs);
  RuntimeLogEvent(
      "Native root replacement activation phase=begin-received request=%llu\n",
      static_cast<unsigned long long>(aArgs.requestId()));

  if (!entry->mActors.mPrimaryChild->SendConnectRootReplacement(
          aArgs.requestId(), aArgs.channelId(), aArgs.generation())) {
    FailRoot(aArgs.requestId(), NS_ERROR_FAILURE);
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation phase=connect-parent-sent "
      "request=%llu\n",
      static_cast<unsigned long long>(aArgs.requestId()));
  nsresult rv = DispatchBackgroundRequest(aArgs.requestId(),
                                          ActivationKind::RootReplacement);
  if (NS_FAILED(rv)) {
    FailRoot(aArgs.requestId(), rv);
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation phase=background-dispatched "
      "request=%llu\n",
      static_cast<unsigned long long>(aArgs.requestId()));
}

void ActivationState::RecvRootConnect(uint64_t aRequestId, uint64_t aChannelId,
                                      uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (!entry->mBeginReceived || entry->mPrimaryLinked ||
      entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mVerificationStarted = true;
  RuntimeLogEvent(
      "Native root replacement activation "
      "phase=redirect-verification-started request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  NativeRootReplacementPrimaryCallback callback =
      std::move(entry->mPrimaryCallback);
  nsresult rv = callback(entry->mReceivedDescriptor);
  entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (NS_FAILED(rv)) {
    FailRoot(aRequestId, rv);
    return;
  }
  entry->mPrimaryLinked = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=connect-parent-linked "
      "request=%llu same_channel=1\n",
      static_cast<unsigned long long>(aRequestId));
  RuntimeLogEvent(
      "Native root replacement activation phase=redirect-verification-queued "
      "request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  ContinueRootVerification(aRequestId);
}

void ActivationState::NotifyRootVerificationRun(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (!entry->mVerificationStarted || entry->mVerificationRun ||
      entry->mVerificationResolved) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mVerificationRun = true;
  RuntimeLogEvent(
      "Native root replacement activation "
      "phase=redirect-verification-run request=%llu channel=%llu "
      "generation=%llu\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned long long>(entry->mDescriptor.mChannelId),
      static_cast<unsigned long long>(entry->mDescriptor.mGeneration));
}

void ActivationState::ResolveRootVerification(uint64_t aRequestId,
                                              nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (!entry->mVerificationStarted || !entry->mVerificationRun ||
      entry->mVerificationResolved) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation "
      "phase=redirect-verification-callback request=%llu channel=%llu "
      "generation=%llu status=0x%08x\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned long long>(entry->mDescriptor.mChannelId),
      static_cast<unsigned long long>(entry->mDescriptor.mGeneration),
      static_cast<unsigned>(aStatus));
  RuntimeLogEvent(
      "Native root replacement activation "
      "phase=redirect-verification-resolved request=%llu channel=%llu "
      "generation=%llu status=0x%08x\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned long long>(entry->mDescriptor.mChannelId),
      static_cast<unsigned long long>(entry->mDescriptor.mGeneration),
      static_cast<unsigned>(aStatus));
  if (NS_FAILED(aStatus)) {
    FailRoot(aRequestId, aStatus);
    return;
  }
  entry->mVerificationResolved = true;
  ContinueRootVerification(aRequestId);
}

void ActivationState::RecvRootBackgroundReady(uint64_t aRequestId,
                                              uint64_t aChannelId,
                                              uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (entry->mBackgroundReady || entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration ||
      !entry->mActors.mBackgroundBound) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mBackgroundReady = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=background-ready "
      "request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  RuntimeLogEvent(
      "Native root replacement activation phase=bg-linked request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  ContinueRootVerification(aRequestId);
}

void ActivationState::ContinueRootVerification(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal || entry->mReadyToVerifySent ||
      !entry->mPrimaryLinked || !entry->mVerificationResolved) {
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation phase=continue-verification "
      "request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  if (!entry->mBackgroundReady || !entry->mActors.mBackgroundBound) {
    RuntimeLogEvent(
        "Native root replacement activation phase=background-wait "
        "request=%llu\n",
        static_cast<unsigned long long>(aRequestId));
    return;
  }
  ActivationRequestParent* actor = entry->mActors.mPrimaryParent;
  if (!actor) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mReadyToVerifySent = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=ready-to-verify request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  if (!actor->SendReadyToVerifyRootReplacement(
          aRequestId, entry->mDescriptor.mChannelId,
          entry->mDescriptor.mGeneration)) {
    FailRoot(aRequestId, NS_ERROR_FAILURE);
  }
}

void ActivationState::RecvRootReadyToVerify(uint64_t aRequestId,
                                            uint64_t aChannelId,
                                            uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (!entry->mReadyToVerifySent || entry->mSetupFinished ||
      entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  if (!entry->mActors.mPrimaryChild->SendRootReplacementSetupFinished(
          aRequestId, aChannelId, aGeneration)) {
    FailRoot(aRequestId, NS_ERROR_FAILURE);
  }
}

void ActivationState::RecvRootSetupFinished(uint64_t aRequestId,
                                            uint64_t aChannelId,
                                            uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (!entry->mReadyToVerifySent || entry->mSetupFinished ||
      entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mSetupFinished = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=setup-finished request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  ReleaseRootSetup(aRequestId);
}

void ActivationState::ReleaseStyle(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
  if (!entry || entry->mTerminal || entry->mReleased) {
    return;
  }
  RuntimeLogEvent(
      "Native style activation phase=activation-released request=%llu "
      "status=0x%08x\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned>(NS_OK));
  entry->mReleased = true;
  nsresult callbackRv = entry->mFinalCallback(NS_OK);
  if (NS_FAILED(callbackRv)) {
    RuntimeLogEvent(
        "Native style activation phase=activation-callback-failed "
        "request=%llu status=0x%08x\n",
        static_cast<unsigned long long>(aRequestId),
        static_cast<unsigned>(callbackRv));
    BeginStyleTeardown(aRequestId, callbackRv, false);
    return;
  }
  RuntimeLogEvent("Native style activation phase=async-open request=%llu\n",
                  static_cast<unsigned long long>(aRequestId));
}

void ActivationState::FailStyle(uint64_t aRequestId, nsresult aStatus,
                                bool aInvokeCallback) {
  BeginStyleTeardown(aRequestId, aStatus, aInvokeCallback);
}

void ActivationState::BeginStyleTeardown(uint64_t aRequestId, nsresult aStatus,
                                         bool aInvokeCallback) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
  if (!entry || entry->mTerminal) {
    return;
  }
  entry->mTerminal = true;
  if (NS_FAILED(aStatus)) {
    RuntimeLogEvent(
        "Native style activation phase=request-failed request=%llu "
        "status=0x%08x\n",
        static_cast<unsigned long long>(aRequestId),
        static_cast<unsigned>(aStatus));
  }
  if (aInvokeCallback && !entry->mReleased) {
    (void)entry->mFinalCallback(aStatus);
  }
  DestroyRequestActors(aRequestId, ActivationKind::Style);
  MaybeRemoveTerminalEntry(aRequestId, ActivationKind::Style);
}

void ActivationState::ReleaseRootSetup(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal || entry->mReleased ||
      entry->mForwardSendPending || entry->mForwardSent) {
    return;
  }
  ActivationRequestParent* actor = entry->mActors.mBackgroundParent;
  if (!actor) {
    return;
  }
  entry->mForwardSendPending = true;
  const uint64_t channelId = entry->mDescriptor.mChannelId;
  const uint64_t generation = entry->mDescriptor.mGeneration;
  RefPtr<ActivationRequestParent> backgroundActor = actor;
  RefPtr<ActivationState> self = this;
  nsresult rv = mBackgroundThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::NativeRootForwardOnStartSend",
      [self = std::move(self), actor = std::move(backgroundActor), aRequestId,
       channelId, generation]() {
        nsresult status =
            actor->SendForwardRootOnStart(aRequestId, channelId, generation)
                ? NS_OK
                : NS_ERROR_FAILURE;
        (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::NativeRootForwardOnStartSent",
            [self = std::move(self), aRequestId, status]() {
              self->RootForwardSent(aRequestId, status);
            }));
      }));
  if (NS_FAILED(rv)) {
    entry->mForwardSendPending = false;
    FailRoot(aRequestId, rv);
  }
}

void ActivationState::RootForwardSent(uint64_t aRequestId, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  entry->mForwardSendPending = false;
  if (NS_FAILED(aStatus)) {
    FailRoot(aRequestId, aStatus);
    return;
  }
  entry->mForwardSent = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=forward-sent request=%llu "
      "channel=%llu generation=%llu\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned long long>(entry->mDescriptor.mChannelId),
      static_cast<unsigned long long>(entry->mDescriptor.mGeneration));
  entry->mReleased = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=activation-released "
      "request=%llu status=0x%08x\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned>(NS_OK));
  nsresult callbackRv = entry->mSetupCallback(NS_OK);
  if (NS_FAILED(callbackRv)) {
    RuntimeLogEvent(
        "Native root replacement activation "
        "phase=activation-callback-failed request=%llu status=0x%08x\n",
        static_cast<unsigned long long>(aRequestId),
        static_cast<unsigned>(callbackRv));
    BeginRootTeardown(aRequestId, callbackRv, false);
    return;
  }
  entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal || !entry->mReleased || !entry->mForwardSent) {
    return;
  }
  entry->mSetupCallbackSucceeded = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=resume request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  MaybeDeliverRootForwardedStart(aRequestId);
}

void ActivationState::RecvRootForwardedStart(uint64_t aRequestId,
                                             uint64_t aChannelId,
                                             uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if ((!entry->mForwardSent && !entry->mForwardSendPending) ||
      entry->mForwardReceived || entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mForwardReceived = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=forward-received "
      "request=%llu channel=%llu generation=%llu\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned long long>(aChannelId),
      static_cast<unsigned long long>(aGeneration));
  MaybeDeliverRootForwardedStart(aRequestId);
}

void ActivationState::RecvRootData(uint64_t aRequestId, uint64_t aChannelId,
                                   uint64_t aGeneration, nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration || !entry->mForwardSent ||
      entry->mForwardStopReceived) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  RootEntry::ForwardEvent event;
  event.mData = std::move(aData);
  entry->mPendingForwardEvents.AppendElement(std::move(event));
  RuntimeLogEvent(
      "Native root replacement activation phase=forward-data-received "
      "request=%llu bytes=%zu\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<size_t>(
          entry->mPendingForwardEvents.LastElement().mData.Length()));
  MaybeDrainRootForwardEvents(aRequestId);
}

void ActivationState::RecvRootStop(uint64_t aRequestId, uint64_t aChannelId,
                                   uint64_t aGeneration, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  if (entry->mDescriptor.mChannelId != aChannelId ||
      entry->mDescriptor.mGeneration != aGeneration || !entry->mForwardSent ||
      !entry->mForwardStopQueued || entry->mForwardStopReceived) {
    FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mForwardStopReceived = true;
  RootEntry::ForwardEvent event;
  event.mStatus = aStatus;
  event.mIsStop = true;
  entry->mPendingForwardEvents.AppendElement(std::move(event));
  RuntimeLogEvent(
      "Native root replacement activation phase=forward-stop-received "
      "request=%llu status=0x%08x\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned>(aStatus));
  MaybeDrainRootForwardEvents(aRequestId);
}

void ActivationState::MaybeDeliverRootForwardedStart(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal || entry->mForwardDelivered ||
      !entry->mForwardSent || !entry->mReleased ||
      !entry->mSetupCallbackSucceeded || !entry->mForwardReceived) {
    return;
  }
  entry->mForwardDelivered = true;
  RuntimeLogEvent(
      "Native root replacement activation phase=forward-start request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  nsresult rv = entry->mForwardedStartCallback();
  if (NS_FAILED(rv)) {
    FailRoot(aRequestId, rv);
    return;
  }
  entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal || !entry->mForwardDelivered) {
    return;
  }
  entry->mForwardedStartCallbackSucceeded = true;
  MaybeDrainRootForwardEvents(aRequestId);
}

void ActivationState::MaybeDrainRootForwardEvents(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  while (true) {
    RootEntry* entry = LookupRootEntry(aRequestId);
    if (!entry || entry->mTerminal || !entry->mForwardDelivered ||
        !entry->mForwardedStartCallbackSucceeded ||
        entry->mPendingForwardEvents.IsEmpty()) {
      return;
    }

    RootEntry::ForwardEvent event = std::move(entry->mPendingForwardEvents[0]);
    entry->mPendingForwardEvents.RemoveElementAt(0);
    if (event.mIsStop) {
      if (entry->mForwardStopDelivered) {
        FailRoot(aRequestId, NS_ERROR_UNEXPECTED);
        return;
      }
      entry->mForwardStopDelivered = true;
      RuntimeLogEvent(
          "Native root replacement activation phase=forward-stop request=%llu "
          "status=0x%08x\n",
          static_cast<unsigned long long>(aRequestId),
          static_cast<unsigned>(event.mStatus));
      nsresult rv = entry->mStopCallback(event.mStatus);
      entry = LookupRootEntry(aRequestId);
      if (NS_FAILED(rv) && entry && !entry->mTerminal) {
        FailRoot(aRequestId, rv);
      }
      return;
    }

    RuntimeLogEvent(
        "Native root replacement activation phase=forward-data request=%llu "
        "bytes=%zu\n",
        static_cast<unsigned long long>(aRequestId),
        static_cast<size_t>(event.mData.Length()));
    nsresult rv = entry->mDataCallback(std::move(event.mData));
    entry = LookupRootEntry(aRequestId);
    if (NS_FAILED(rv)) {
      if (entry && !entry->mTerminal) {
        FailRoot(aRequestId, rv);
      }
      return;
    }
  }
}

void ActivationState::FailRoot(uint64_t aRequestId, nsresult aStatus) {
  BeginRootTeardown(aRequestId, aStatus, true);
}

void ActivationState::BeginRootTeardown(uint64_t aRequestId, nsresult aStatus,
                                        bool aInvokeCallback) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  entry->mTerminal = true;
  if (NS_FAILED(aStatus)) {
    RuntimeLogEvent(
        "Native root replacement activation phase=request-failed "
        "request=%llu status=0x%08x generation=%llu\n",
        static_cast<unsigned long long>(aRequestId),
        static_cast<unsigned>(aStatus),
        static_cast<unsigned long long>(entry->mDescriptor.mGeneration));
  }
  if (aInvokeCallback && !entry->mReleased) {
    (void)entry->mSetupCallback(aStatus);
  }
  DestroyRequestActors(aRequestId, ActivationKind::RootReplacement);
  MaybeRemoveTerminalEntry(aRequestId, ActivationKind::RootReplacement);
}

void ActivationState::DestroyRequestActors(uint64_t aRequestId,
                                           ActivationKind aKind) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors) {
    return;
  }
  const bool terminal = aKind == ActivationKind::Style
                            ? mStyleEntries.Lookup(aRequestId).Data().mTerminal
                            : LookupRootEntry(aRequestId)->mTerminal;
  if (!terminal) {
    return;
  }
  if (actors->mPrimaryChild && !actors->mPrimaryDeleteSent &&
      !(actors->mDestroyedActors & kPrimaryChildActor)) {
    actors->mPrimaryDeleteSent = true;
    RuntimeLogEvent("%s phase=request-primary-actor-delete-sent request=%llu\n",
                    KindLogPrefix(aKind),
                    static_cast<unsigned long long>(aRequestId));
    (void)PNativeStylePreloadActivationRequestChild::Send__delete__(
        actors->mPrimaryChild);
  }
  if (actors->mBackgroundChild && !actors->mBackgroundDeleteSent &&
      !(actors->mDestroyedActors & kBackgroundChildActor)) {
    actors->mBackgroundDeleteSent = true;
    RefPtr<ActivationRequestChild> actor = actors->mBackgroundChild;
    MOZ_ALWAYS_SUCCEEDS(mSocketTarget->Dispatch(NS_NewRunnableFunction(
        "NaiveFox::DeleteNativeActivationBackgroundRequest",
        [actor = std::move(actor), aRequestId, aKind]() {
          RuntimeLogEvent(
              "%s phase=request-background-actor-delete-sent request=%llu\n",
              KindLogPrefix(aKind),
              static_cast<unsigned long long>(aRequestId));
          (void)PNativeStylePreloadActivationRequestChild::Send__delete__(
              actor);
        })));
  }
}

void ActivationState::RequestActorDestroyed(uint64_t aRequestId,
                                            ActivationKind aKind,
                                            uint8_t aActorBit) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors || (actors->mDestroyedActors & aActorBit)) {
    return;
  }
  const bool terminal = aKind == ActivationKind::Style
                            ? mStyleEntries.Lookup(aRequestId).Data().mTerminal
                            : LookupRootEntry(aRequestId)->mTerminal;
  actors->mDestroyedActors |= aActorBit;
  const uint8_t primaryExpected = actors->mExpectedActors & kPrimaryActorMask;
  if (primaryExpected && !actors->mPrimaryDestroyedLogged &&
      (actors->mDestroyedActors & primaryExpected) == primaryExpected) {
    actors->mPrimaryDestroyedLogged = true;
    RuntimeLogEvent("%s phase=request-primary-actor-destroyed request=%llu\n",
                    KindLogPrefix(aKind),
                    static_cast<unsigned long long>(aRequestId));
  }
  const uint8_t backgroundExpected =
      actors->mExpectedActors & kBackgroundActorMask;
  if (backgroundExpected && !actors->mBackgroundDestroyedLogged &&
      (actors->mDestroyedActors & backgroundExpected) == backgroundExpected) {
    actors->mBackgroundDestroyedLogged = true;
    RuntimeLogEvent(
        "%s phase=request-background-actor-destroyed request=%llu\n",
        KindLogPrefix(aKind), static_cast<unsigned long long>(aRequestId));
  }
  if (!terminal) {
    RequestTransportFailed(aRequestId, aKind, NS_ERROR_FAILURE);
    return;
  }
  MaybeRemoveTerminalEntry(aRequestId, aKind);
}

void ActivationState::MaybeRemoveTerminalEntry(uint64_t aRequestId,
                                               ActivationKind aKind) {
  MOZ_ASSERT(NS_IsMainThread());
  RequestActors* actors = LookupActors(aRequestId, aKind);
  if (!actors || actors->mBackgroundCreationPending ||
      (actors->mDestroyedActors & actors->mExpectedActors) !=
          actors->mExpectedActors) {
    return;
  }
  if (aKind == ActivationKind::Style) {
    StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
    if (entry && entry->mTerminal) {
      mStyleEntries.Remove(aRequestId);
    }
    return;
  }
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (entry && entry->mTerminal) {
    mRootEntries.Remove(aRequestId);
  }
}

void ActivationState::RequestTransportFailed(uint64_t aRequestId,
                                             ActivationKind aKind,
                                             nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (aKind == ActivationKind::Style) {
    FailStyle(aRequestId, aStatus, true);
  } else {
    FailRoot(aRequestId, aStatus);
  }
}

void ActivationState::CompleteStyleRequest(uint64_t aRequestId,
                                           nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
  if (!entry || entry->mTerminal) {
    return;
  }
  RuntimeLogEvent(
      "Native style activation phase=on-stop request=%llu status=0x%08x\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned>(aStatus));
  if (!entry->mReleased && NS_SUCCEEDED(aStatus)) {
    aStatus = NS_ERROR_UNEXPECTED;
  }
  BeginStyleTeardown(aRequestId, aStatus, !entry->mReleased);
}

void ActivationState::CompleteRootRequest(uint64_t aRequestId,
                                          nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation phase=on-stop request=%llu "
      "status=0x%08x generation=%llu\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned>(aStatus),
      static_cast<unsigned long long>(entry->mDescriptor.mGeneration));
  if ((!entry->mReleased || !entry->mForwardDelivered ||
       !entry->mForwardStopDelivered) &&
      NS_SUCCEEDED(aStatus)) {
    aStatus = NS_ERROR_UNEXPECTED;
  }
  BeginRootTeardown(aRequestId, aStatus, !entry->mReleased);
}

void ActivationState::CancelStyle(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  StyleEntry* entry = mStyleEntries.Lookup(aRequestId).DataPtrOrNull();
  if (!entry || entry->mTerminal) {
    return;
  }
  RuntimeLogEvent(
      "Native style activation phase=request-cancelled request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  BeginStyleTeardown(aRequestId, NS_ERROR_ABORT, !entry->mReleased);
}

void ActivationState::CancelRoot(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  RootEntry* entry = LookupRootEntry(aRequestId);
  if (!entry || entry->mTerminal) {
    return;
  }
  RuntimeLogEvent(
      "Native root replacement activation phase=request-cancelled "
      "request=%llu generation=%llu\n",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned long long>(entry->mDescriptor.mGeneration));
  BeginRootTeardown(aRequestId, NS_ERROR_ABORT, !entry->mReleased);
}

void ActivationState::ActorFailed(ActivationLeg aLeg) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mFailed || mShuttingDown) {
    return;
  }
  mFailed = true;
  RuntimeLogEvent("Native style activation phase=actor-failed leg=%s\n",
                  LegLogName(aLeg));
  FailAll(NS_ERROR_FAILURE);
}

void ActivationState::FailAll(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  nsTArray<uint64_t> requestIds;
  for (auto iter = mStyleEntries.ConstIter(); !iter.Done(); iter.Next()) {
    requestIds.AppendElement(iter.Key());
  }
  for (uint64_t requestId : requestIds) {
    FailStyle(requestId, aStatus, true);
  }
  requestIds.Clear();
  for (auto iter = mRootEntries.ConstIter(); !iter.Done(); iter.Next()) {
    requestIds.AppendElement(iter.Key());
  }
  for (uint64_t requestId : requestIds) {
    FailRoot(requestId, aStatus);
  }
}

bool ActivationState::AllManagerActorsDestroyed() const {
  auto done = [](const auto& aActor) {
    return !aActor || !aActor->WasBound() || aActor->WasDestroyed();
  };
  return done(mPrimaryChild) && done(mPrimaryParent) &&
         done(mBackgroundChild) && done(mBackgroundParent);
}

void ActivationState::Shutdown() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mShuttingDown) {
    return;
  }
  mShuttingDown = true;
  FailAll(NS_ERROR_ABORT);
  RuntimeLogEvent("Native style activation phase=shutdown\n");

  MOZ_ALWAYS_TRUE(SpinEventLoopUntil(
      "NativeActivation::DrainRequestActors"_ns,
      [this]() { return mStyleEntries.IsEmpty() && mRootEntries.IsEmpty(); }));
  MOZ_ALWAYS_TRUE(
      SpinEventLoopUntil("NativeActivation::DrainManagerSetup"_ns, [this]() {
        return !mBackgroundParentSetupPending.load() &&
               !mBackgroundChildSetupPending.load();
      }));

  if (mPrimaryChild && mPrimaryChild->WasBound() &&
      !mPrimaryChild->WasDestroyed()) {
    mPrimaryChild->Close();
  } else if (mPrimaryParent && mPrimaryParent->WasBound() &&
             !mPrimaryParent->WasDestroyed()) {
    mPrimaryParent->Close();
  }
  if (mBackgroundChild && mBackgroundChild->WasBound() &&
      !mBackgroundChild->WasDestroyed() && mSocketTarget) {
    RefPtr<ActivationChild> actor = mBackgroundChild;
    MOZ_ALWAYS_SUCCEEDS(mSocketTarget->Dispatch(NS_NewRunnableFunction(
        "NaiveFox::CloseNativeActivationBackgroundManagerChild",
        [actor = std::move(actor)]() { actor->Close(); })));
  } else if (mBackgroundParent && mBackgroundParent->WasBound() &&
             !mBackgroundParent->WasDestroyed() && mBackgroundThread) {
    RefPtr<ActivationParent> actor = mBackgroundParent;
    MOZ_ALWAYS_SUCCEEDS(mBackgroundThread->Dispatch(NS_NewRunnableFunction(
        "NaiveFox::CloseNativeActivationBackgroundManagerParent",
        [actor = std::move(actor)]() { actor->Close(); })));
  }

  MOZ_ALWAYS_TRUE(
      SpinEventLoopUntil("NativeActivation::DrainManagerActors"_ns,
                         [this]() { return AllManagerActorsDestroyed(); }));
  if (mBackgroundThread) {
    (void)mBackgroundThread->Shutdown();
  }
  mPrimaryChild = nullptr;
  mPrimaryParent = nullptr;
  mBackgroundChild = nullptr;
  mBackgroundParent = nullptr;
  mSocketTarget = nullptr;
  mBackgroundThread = nullptr;
}

}  // namespace

nsresult NativeStylePreloadActivation::Initialize() {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    return NS_OK;
  }
  RefPtr<ActivationState> state = new ActivationState();
  nsresult rv = state->Initialize();
  if (NS_FAILED(rv)) {
    state->Shutdown();
    return rv;
  }
  sActivationState = std::move(state);
  return NS_OK;
}

bool NativeStylePreloadActivation::IsReady() {
  MOZ_ASSERT(NS_IsMainThread());
  return sActivationState && sActivationState->IsReady();
}

void NativeStylePreloadActivation::Shutdown() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sActivationState) {
    return;
  }
  sActivationState->Shutdown();
  sActivationState = nullptr;
}

nsresult NativeStylePreloadActivation::RegisterAndDispatch(
    NativeStylePreloadActivationDescriptor&& aDescriptor,
    NativeStylePreloadPrimaryCallback&& aPrimaryCallback,
    NativeStylePreloadFinalCallback&& aFinalCallback, uint64_t& aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sActivationState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sActivationState->RegisterStyle(std::move(aDescriptor),
                                         std::move(aPrimaryCallback),
                                         std::move(aFinalCallback), aRequestId);
}

void NativeStylePreloadActivation::CompleteStyle(uint64_t aRequestId,
                                                 nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->CompleteStyleRequest(aRequestId, aStatus);
  }
}

void NativeStylePreloadActivation::Cancel(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->CancelStyle(aRequestId);
  }
}

nsresult NativeStylePreloadActivation::RegisterRootReplacement(
    NativeRootReplacementActivationDescriptor&& aDescriptor,
    NativeRootReplacementPrimaryCallback&& aPrimaryCallback,
    NativeRootReplacementSetupCallback&& aSetupCallback,
    NativeRootReplacementForwardedStartCallback&& aForwardedStartCallback,
    NativeRootReplacementDataCallback&& aDataCallback,
    NativeRootReplacementStopCallback&& aStopCallback, uint64_t& aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sActivationState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sActivationState->RegisterRoot(
      std::move(aDescriptor), std::move(aPrimaryCallback),
      std::move(aSetupCallback), std::move(aForwardedStartCallback),
      std::move(aDataCallback), std::move(aStopCallback), aRequestId);
}

nsresult NativeStylePreloadActivation::ForwardRootReplacementData(
    uint64_t aRequestId, nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sActivationState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sActivationState->ForwardRootData(aRequestId, std::move(aData));
}

nsresult NativeStylePreloadActivation::ForwardRootReplacementStop(
    uint64_t aRequestId, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sActivationState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sActivationState->ForwardRootStop(aRequestId, aStatus);
}

void NativeStylePreloadActivation::NotifyRootReplacementRedirectVerificationRun(
    uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->NotifyRootVerificationRun(aRequestId);
  }
}

void NativeStylePreloadActivation::ResolveRootReplacementRedirectVerification(
    uint64_t aRequestId, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->ResolveRootVerification(aRequestId, aStatus);
  }
}

void NativeStylePreloadActivation::CompleteRootReplacement(uint64_t aRequestId,
                                                           nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->CompleteRootRequest(aRequestId, aStatus);
  }
}

void NativeStylePreloadActivation::CancelRootReplacement(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->CancelRoot(aRequestId);
  }
}

}  // namespace mozilla::naivefox
