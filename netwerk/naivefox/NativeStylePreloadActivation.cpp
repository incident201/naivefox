/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NativeStylePreloadActivation.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

#include "NativeStylePreloadProcessBridge.h"

#if defined(XP_UNIX)
#  include <limits.h>
#  include <unistd.h>
#endif

#include "RuntimeLogging.h"
#include "base/at_exit.h"
#include "base/message_loop.h"
#include "base/process_util.h"
#include "mozilla/Assertions.h"
#include "mozilla/GeckoArgs.h"
#include "mozilla/Logging.h"
#include "mozilla/RefPtr.h"
#include "mozilla/ScopeExit.h"
#include "mozilla/SpinEventLoopUntil.h"
#include "mozilla/StaticPtr.h"
#include "mozilla/UniquePtr.h"
#include "mozilla/ipc/Endpoint.h"
#include "mozilla/ipc/IOThread.h"
#include "mozilla/ipc/NodeController.h"
#include "mozilla/ipc/ProtocolUtils.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationChild.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationParent.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationRequestChild.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationRequestParent.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessChild.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessParent.h"
#include "nsCOMPtr.h"
#include "nsHashKeys.h"
#include "nsISerialEventTarget.h"
#include "nsISocketTransportService.h"
#include "nsIThread.h"
#include "nsITimer.h"
#include "nsNetCID.h"
#include "nsServiceManagerUtils.h"
#include "nsTHashMap.h"
#include "nsThreadManager.h"
#include "nsThreadUtils.h"
#include "nsXPCOM.h"

namespace mozilla::naivefox {

bool EnterNativeStylePreloadActivationChildRole();

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

namespace {

constexpr uint32_t kActivationProcessAdmissionTimeoutMs = 10000;

class ActivationProcessServiceParent;

class ProcessServiceState final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ProcessServiceState)

  struct Route final {
    uint64_t mGeneration = 0;
    NativeStylePreloadProcessRootCallbacks mCallbacks;
    uint32_t mOutstandingStyles = 0;
    bool mReady = false;
    bool mFinished = false;
  };

  struct StyleOwner final {
    uint64_t mRequestId = 0;
    uint64_t mGeneration = 0;
  };

  nsresult BeginLaunch();
  void SetLaunched(base::ProcessHandle aProcess,
                   RefPtr<ipc::NodeChannel>&& aNodeChannel);
  void SetActor(ActivationProcessServiceParent* aActor);
  void LaunchFailed(nsresult aStatus);
  void HelloAccepted();
  void ActorDestroyed(bool aExpected);
  bool IsReady() const;
  bool HasFailed() const;
  nsresult Status() const;
  nsresult StartRoot(NativeRootReplacementActivationDescriptor&& aDescriptor,
                     uint32_t aMaximumBodyBytes,
                     NativeStylePreloadProcessRootCallbacks&& aCallbacks,
                     uint64_t& aRequestId);
  nsresult SendRootData(uint64_t aRequestId, uint64_t aGeneration,
                        uint32_t aSequence, nsCString&& aData);
  nsresult SendRootStop(uint64_t aRequestId, uint64_t aGeneration,
                        uint32_t aSequence, nsresult aStatus);
  void CancelRoot(uint64_t aRequestId, uint64_t aGeneration, nsresult aStatus);
  nsresult CompleteStyle(uint64_t aStyleRequestId, nsresult aStatus);
  nsresult RootReady(uint64_t aRequestId, uint64_t aGeneration);
  nsresult StyleDiscovered(const NativeStylePreloadProcessArgs& aArgs);
  void RouteFailed(uint64_t aRequestId, uint64_t aGeneration, nsresult aStatus);
  void RootFinished(uint64_t aRequestId, uint64_t aGeneration,
                    uint32_t aLastSequence, uint32_t aBodyBytes,
                    uint32_t aStyleCount, nsresult aStatus);
  void TransportFailed(nsresult aStatus);
  void BeginShutdown();
  bool IsShutdownComplete() const;
  base::ProcessHandle TakeProcess();

 private:
  ~ProcessServiceState() = default;

  Route* LookupRoute(uint64_t aRequestId, uint64_t aGeneration);
  void FailAll(nsresult aStatus);

  nsTHashMap<nsUint64HashKey, UniquePtr<Route>> mRoutes;
  nsTHashMap<nsUint64HashKey, StyleOwner> mStyleOwners;
  RefPtr<ActivationProcessServiceParent> mActor;
  RefPtr<ipc::NodeChannel> mNodeChannel;
  base::ProcessHandle mProcess = base::kInvalidProcessHandle;
  uint64_t mNextRequestId = 1;
  nsresult mStatus = NS_ERROR_FAILURE;
  bool mReady = false;
  bool mFailed = false;
  bool mShuttingDown = false;
  bool mActorDestroyed = false;
};

StaticRefPtr<ProcessServiceState> sProcessServiceState;

class ActivationProcessServiceParent final
    : public PNativeStylePreloadProcessParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationProcessServiceParent,
                                        override)

  ActivationProcessServiceParent(ProcessServiceState* aState,
                                 base::ProcessId aExpectedChildPid,
                                 base::ProcessId aExpectedParentPid)
      : mState(aState),
        mExpectedChildPid(aExpectedChildPid),
        mExpectedParentPid(aExpectedParentPid) {}

  NativeStylePreloadProcessParentBridge* Bridge() const {
    return mBridge.get();
  }

  nsresult BeginShutdown() {
    MOZ_ASSERT(NS_IsMainThread());
    if (mShutdownSent || !mHelloReceived || !mBridge) {
      return NS_ERROR_UNEXPECTED;
    }
    mShutdownSent = true;
    return SendShutdown() ? NS_OK : NS_ERROR_FAILURE;
  }

  already_AddRefed<PNativeStylePreloadProcessStyleParent>
  AllocPNativeStylePreloadProcessStyleParent(
      const NativeStylePreloadProcessArgs& aArgs) final {
    return mBridge ? mBridge->AllocStyle(aArgs) : nullptr;
  }

 private:
  ~ActivationProcessServiceParent() = default;

  IPCResult RecvHello(const uint64_t& aChildPid,
                      const uint64_t& aObservedParentPid) final {
    MOZ_ASSERT(NS_IsMainThread());
    if (mHelloReceived || aChildPid != uint64_t(mExpectedChildPid) ||
        aObservedParentPid != uint64_t(mExpectedParentPid) ||
        OtherPid() != mExpectedChildPid) {
      return IPC_FAIL_NO_REASON(this);
    }
    NativeStylePreloadProcessParentBridge::Callbacks callbacks;
    callbacks.mRootReady = [state = mState.get()](uint64_t aRequestId,
                                                  uint64_t aGeneration) {
      return state->RootReady(aRequestId, aGeneration);
    };
    callbacks.mStyleDiscovered =
        [state = mState.get()](const NativeStylePreloadProcessArgs& aArgs) {
          return state->StyleDiscovered(aArgs);
        };
    callbacks.mRootFinished = [state = mState.get()](
                                  uint64_t aRequestId, uint64_t aGeneration,
                                  uint32_t aLastSequence, uint32_t aBodyBytes,
                                  uint32_t aStyleCount, nsresult aStatus) {
      state->RootFinished(aRequestId, aGeneration, aLastSequence, aBodyBytes,
                          aStyleCount, aStatus);
    };
    callbacks.mRootFailed = [state = mState.get()](uint64_t aRequestId,
                                                   uint64_t aGeneration,
                                                   nsresult aStatus) {
      state->RouteFailed(aRequestId, aGeneration, aStatus);
    };
    callbacks.mTransportFailed = [state = mState.get()](nsresult aStatus) {
      state->TransportFailed(aStatus);
    };
    mBridge = MakeUnique<NativeStylePreloadProcessParentBridge>(
        this, std::move(callbacks));
    mHelloReceived = true;
    mState->HelloAccepted();
    RuntimeLogEvent(
        "Native activation process phase=hello parent_pid=%llu "
        "child_pid=%llu cross_process=1 persistent=1\n",
        static_cast<unsigned long long>(mExpectedParentPid),
        static_cast<unsigned long long>(mExpectedChildPid));
    return IPC_OK();
  }

  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final {
    MOZ_ASSERT(NS_IsMainThread());
    if (mBridge) {
      mBridge->ProcessActorDestroyed();
      mBridge = nullptr;
    }
    mState->ActorDestroyed(mHelloReceived && mShutdownSent &&
                           (aWhy == Deletion || aWhy == NormalShutdown));
  }

  const RefPtr<ProcessServiceState> mState;
  const base::ProcessId mExpectedChildPid;
  const base::ProcessId mExpectedParentPid;
  UniquePtr<NativeStylePreloadProcessParentBridge> mBridge;
  bool mHelloReceived = false;
  bool mShutdownSent = false;
};

class ActivationProcessChild final : public PNativeStylePreloadProcessChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationProcessChild, override)

  ActivationProcessChild(base::ProcessId aParentPid, bool* aDone)
      : mParentPid(aParentPid), mDone(aDone) {}

  bool Start() {
    mBridge = MakeUnique<NativeStylePreloadProcessChildBridge>(this);
    if (NS_FAILED(mBridge->Initialize())) {
      mBridge = nullptr;
      return false;
    }
    return SendHello(uint64_t(base::GetCurrentProcId()), uint64_t(mParentPid));
  }

  already_AddRefed<PNativeStylePreloadProcessRootChild>
  AllocPNativeStylePreloadProcessRootChild(const uint64_t& aRequestId,
                                           const uint64_t& aGeneration) final {
    return mBridge ? mBridge->AllocRoot(aRequestId, aGeneration) : nullptr;
  }

 private:
  ~ActivationProcessChild() = default;

  IPCResult RecvShutdown() final {
    MOZ_ASSERT(NS_IsMainThread());
    if (mShutdownReceived) {
      return IPC_FAIL_NO_REASON(this);
    }
    mShutdownReceived = true;
    if (mBridge) {
      mBridge->Shutdown();
    }
    Close();
    return IPC_OK();
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    MOZ_ASSERT(NS_IsMainThread());
    if (mBridge) {
      mBridge->ProcessActorDestroyed();
      mBridge = nullptr;
    }
    *mDone = true;
  }

  const base::ProcessId mParentPid;
  bool* const mDone;
  UniquePtr<NativeStylePreloadProcessChildBridge> mBridge;
  bool mShutdownReceived = false;
};

#if defined(XP_UNIX) && !defined(ANDROID)
void LaunchActivationProcessService(ProcessServiceState* aState) {
  MOZ_ASSERT(ipc::IOThread::Get()->GetEventTarget()->IsOnCurrentThread());

  IPC::Channel::ChannelHandle clientHandle;
  ipc::ScopedPort initialPort;
  ipc::NodeChannel* rawNodeChannel = nullptr;
  if (!ipc::NodeController::GetSingleton()->InviteChildProcess(
          nullptr, &clientHandle, &initialPort, &rawNodeChannel)) {
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::ActivationProcessServiceInviteFailed",
        [state = RefPtr{aState}]() { state->LaunchFailed(NS_ERROR_FAILURE); }));
    return;
  }
  RefPtr<ipc::NodeChannel> nodeChannel = dont_AddRef(rawNodeChannel);

  auto* unixHandle = std::get_if<UniqueFileHandle>(&clientHandle);
  if (!unixHandle || !*unixHandle) {
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::ActivationProcessServiceHandleFailed",
        [state = RefPtr{aState}]() { state->LaunchFailed(NS_ERROR_FAILURE); }));
    return;
  }

  geckoargs::ChildProcessArgs childArgs;
  geckoargs::sIPCHandle.Put(std::move(*unixHandle), childArgs);
  const base::ProcessId parentPid = base::GetCurrentProcId();
  geckoargs::sParentPid.Put(uint64_t(parentPid), childArgs);
  const nsID channelId = nsID::GenerateUUID();
  char channelIdString[NSID_LENGTH];
  channelId.ToProvidedString(channelIdString);
  geckoargs::sInitialChannelID.Put(channelIdString, childArgs);

  char executablePath[PATH_MAX + 1];
  const ssize_t executableLength =
      readlink("/proc/self/exe", executablePath, PATH_MAX);
  if (executableLength <= 0 || executableLength > PATH_MAX) {
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::ActivationProcessServiceExecutableFailed",
        [state = RefPtr{aState}]() { state->LaunchFailed(NS_ERROR_FAILURE); }));
    return;
  }
  executablePath[executableLength] = '\0';

  std::vector<std::string> argv{executablePath, "--naivefox-activation-child"};
  argv.insert(argv.end(), childArgs.mArgs.begin(), childArgs.mArgs.end());
  base::LaunchOptions options;
  geckoargs::AddToFdsToRemap(childArgs, options.fds_to_remap);
  base::ProcessHandle childProcess = base::kInvalidProcessHandle;
  if (base::LaunchApp(argv, std::move(options), &childProcess).isErr() ||
      childProcess == base::kInvalidProcessHandle) {
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::ActivationProcessServiceLaunchFailed",
        [state = RefPtr{aState}]() { state->LaunchFailed(NS_ERROR_FAILURE); }));
    return;
  }

  Endpoint<PNativeStylePreloadProcessParent> endpoint(
      ipc::PrivateIPDLInterface{}, std::move(initialPort), channelId,
      ipc::EndpointProcInfo::Current(),
      ipc::EndpointProcInfo{.mPid = childProcess, .mChildID = 1});
  nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::BindActivationProcessServiceParent",
      [state = RefPtr{aState}, endpoint = std::move(endpoint), childProcess,
       parentPid, nodeChannel = std::move(nodeChannel)]() mutable {
        state->SetLaunched(childProcess, std::move(nodeChannel));
        RefPtr actor =
            new ActivationProcessServiceParent(state, childProcess, parentPid);
        state->SetActor(actor);
        if (!endpoint.Bind(actor)) {
          state->LaunchFailed(NS_ERROR_FAILURE);
        }
      }));
  if (NS_FAILED(rv)) {
    (void)base::KillProcess(childProcess, 1);
    int processInfo = 0;
    (void)base::WaitForProcess(childProcess, base::BlockingWait::Yes,
                               &processInfo);
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::ActivationProcessServiceBindDispatchFailed",
        [state = RefPtr{aState}, rv]() { state->LaunchFailed(rv); }));
  }
}
#endif

nsresult ProcessServiceState::BeginLaunch() {
  MOZ_ASSERT(NS_IsMainThread());
#if !defined(XP_UNIX) || defined(ANDROID)
  return NS_ERROR_NOT_IMPLEMENTED;
#else
  if (mProcess != base::kInvalidProcessHandle || mActor || mReady || mFailed ||
      mShuttingDown) {
    return NS_ERROR_ALREADY_INITIALIZED;
  }
  if (!ipc::IOThread::Get()) {
    ipc::IOThread::Startup();
  }
  return ipc::IOThread::Get()->GetEventTarget()->Dispatch(
      NS_NewRunnableFunction(
          "NaiveFox::LaunchActivationProcessService",
          [self = RefPtr{this}]() { LaunchActivationProcessService(self); }));
#endif
}

void ProcessServiceState::SetLaunched(base::ProcessHandle aProcess,
                                      RefPtr<ipc::NodeChannel>&& aNodeChannel) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mProcess != base::kInvalidProcessHandle || mFailed || mShuttingDown ||
      aProcess == base::kInvalidProcessHandle || !aNodeChannel) {
    if (aProcess != base::kInvalidProcessHandle) {
      (void)base::KillProcess(aProcess, 1);
      int processInfo = 0;
      (void)base::WaitForProcess(aProcess, base::BlockingWait::Yes,
                                 &processInfo);
    }
    LaunchFailed(NS_ERROR_FAILURE);
    return;
  }
  mProcess = aProcess;
  mNodeChannel = std::move(aNodeChannel);
}

void ProcessServiceState::SetActor(ActivationProcessServiceParent* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!aActor || mActor || mFailed || mShuttingDown) {
    LaunchFailed(NS_ERROR_FAILURE);
    return;
  }
  mActor = aActor;
}

void ProcessServiceState::LaunchFailed(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mFailed || mShuttingDown) {
    return;
  }
  mStatus = NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE;
  mFailed = true;
  FailAll(mStatus);
}

void ProcessServiceState::HelloAccepted() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mReady || mFailed || mShuttingDown || !mActor || !mActor->Bridge()) {
    LaunchFailed(NS_ERROR_UNEXPECTED);
    return;
  }
  mReady = true;
  mStatus = NS_OK;
}

void ProcessServiceState::ActorDestroyed(bool aExpected) {
  MOZ_ASSERT(NS_IsMainThread());
  mActorDestroyed = true;
  mActor = nullptr;
  mNodeChannel = nullptr;
  if (!mShuttingDown || !aExpected) {
    TransportFailed(NS_ERROR_FAILURE);
  }
}

bool ProcessServiceState::IsReady() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mReady && !mFailed && !mShuttingDown && mActor && mActor->Bridge();
}

bool ProcessServiceState::HasFailed() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mFailed;
}

nsresult ProcessServiceState::Status() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mStatus;
}

ProcessServiceState::Route* ProcessServiceState::LookupRoute(
    uint64_t aRequestId, uint64_t aGeneration) {
  auto* route = mRoutes.Lookup(aRequestId).DataPtrOrNull();
  return route && *route && (*route)->mGeneration == aGeneration ? route->get()
                                                                 : nullptr;
}

nsresult ProcessServiceState::StartRoot(
    NativeRootReplacementActivationDescriptor&& aDescriptor,
    uint32_t aMaximumBodyBytes,
    NativeStylePreloadProcessRootCallbacks&& aCallbacks, uint64_t& aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  aRequestId = 0;
  if (!IsReady() || !aDescriptor.mGeneration || !aMaximumBodyBytes ||
      !aCallbacks.mReady || !aCallbacks.mStyleDiscovered ||
      !aCallbacks.mFinished || !aCallbacks.mFailed || !mNextRequestId) {
    return NS_ERROR_INVALID_ARG;
  }
  const uint64_t requestId = mNextRequestId++;
  auto route = MakeUnique<Route>();
  route->mGeneration = aDescriptor.mGeneration;
  route->mCallbacks = std::move(aCallbacks);
  mRoutes.InsertOrUpdate(requestId, std::move(route));
  NativeRootReplacementActivationArgs args =
      SerializeRootDescriptor(requestId, aDescriptor);
  nsresult rv = mActor->Bridge()->StartRoot(
      std::move(args), uint64_t(base::GetCurrentProcId()),
      uint64_t(base::GetProcId(mProcess)), aMaximumBodyBytes);
  if (NS_FAILED(rv)) {
    mRoutes.Remove(requestId);
    return rv;
  }
  aRequestId = requestId;
  return NS_OK;
}

nsresult ProcessServiceState::SendRootData(uint64_t aRequestId,
                                           uint64_t aGeneration,
                                           uint32_t aSequence,
                                           nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsReady() || !LookupRoute(aRequestId, aGeneration)) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  return mActor->Bridge()->SendRootData(aRequestId, aGeneration, aSequence,
                                        std::move(aData));
}

nsresult ProcessServiceState::SendRootStop(uint64_t aRequestId,
                                           uint64_t aGeneration,
                                           uint32_t aSequence,
                                           nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsReady() || !LookupRoute(aRequestId, aGeneration)) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  return mActor->Bridge()->SendRootStop(aRequestId, aGeneration, aSequence,
                                        aStatus);
}

void ProcessServiceState::CancelRoot(uint64_t aRequestId, uint64_t aGeneration,
                                     nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  Route* route = LookupRoute(aRequestId, aGeneration);
  if (!route) {
    return;
  }
  if (IsReady()) {
    mActor->Bridge()->CancelRoot(aRequestId, aGeneration,
                                 NS_FAILED(aStatus) ? aStatus : NS_ERROR_ABORT);
  }
  nsTArray<uint64_t> styles;
  for (auto iter = mStyleOwners.Iter(); !iter.Done(); iter.Next()) {
    if (iter.Data().mRequestId == aRequestId &&
        iter.Data().mGeneration == aGeneration) {
      styles.AppendElement(iter.Key());
    }
  }
  for (uint64_t styleId : styles) {
    if (IsReady()) {
      (void)mActor->Bridge()->CompleteStyle(
          styleId, NS_FAILED(aStatus) ? aStatus : NS_ERROR_ABORT);
    }
    mStyleOwners.Remove(styleId);
  }
  mRoutes.Remove(aRequestId);
}

nsresult ProcessServiceState::CompleteStyle(uint64_t aStyleRequestId,
                                            nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* owner = mStyleOwners.Lookup(aStyleRequestId).DataPtrOrNull();
  if (!IsReady() || !owner) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  nsresult rv = mActor->Bridge()->CompleteStyle(aStyleRequestId, aStatus);
  if (NS_SUCCEEDED(rv)) {
    const StyleOwner completedOwner = *owner;
    mStyleOwners.Remove(aStyleRequestId);
    Route* route =
        LookupRoute(completedOwner.mRequestId, completedOwner.mGeneration);
    if (route) {
      MOZ_RELEASE_ASSERT(route->mOutstandingStyles);
      --route->mOutstandingStyles;
      if (route->mFinished && !route->mOutstandingStyles) {
        mRoutes.Remove(completedOwner.mRequestId);
      }
    }
  }
  return rv;
}

nsresult ProcessServiceState::RootReady(uint64_t aRequestId,
                                        uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  Route* route = LookupRoute(aRequestId, aGeneration);
  if (!route || route->mReady) {
    return NS_ERROR_UNEXPECTED;
  }
  route->mReady = true;
  return route->mCallbacks.mReady();
}

nsresult ProcessServiceState::StyleDiscovered(
    const NativeStylePreloadProcessArgs& aArgs) {
  MOZ_ASSERT(NS_IsMainThread());
  Route* route = LookupRoute(aArgs.rootRequestId(), aArgs.rootGeneration());
  if (!route || !route->mReady || !aArgs.styleRequestId() ||
      mStyleOwners.Contains(aArgs.styleRequestId())) {
    return NS_ERROR_UNEXPECTED;
  }
  NativeStylePreloadProcessDescriptor descriptor;
  descriptor.mRootRequestId = aArgs.rootRequestId();
  descriptor.mRootGeneration = aArgs.rootGeneration();
  descriptor.mStyleRequestId = aArgs.styleRequestId();
  descriptor.mDiscoverySequence = aArgs.discoverySequence();
  descriptor.mUrl = aArgs.descriptor().url();
  descriptor.mCharset = aArgs.descriptor().charset();
  descriptor.mCrossOrigin = aArgs.descriptor().crossOrigin();
  descriptor.mMedia = aArgs.descriptor().media();
  descriptor.mReferrerPolicy = aArgs.descriptor().referrerPolicy();
  descriptor.mNonce = aArgs.descriptor().nonce();
  descriptor.mIntegrity = aArgs.descriptor().integrity();
  descriptor.mFetchPriority = aArgs.descriptor().fetchPriority();
  descriptor.mLinkPreload = aArgs.descriptor().linkPreload();
  StyleOwner owner{aArgs.rootRequestId(), aArgs.rootGeneration()};
  mStyleOwners.InsertOrUpdate(aArgs.styleRequestId(), owner);
  ++route->mOutstandingStyles;
  return route->mCallbacks.mStyleDiscovered(descriptor);
}

void ProcessServiceState::RouteFailed(uint64_t aRequestId, uint64_t aGeneration,
                                      nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  Route* route = LookupRoute(aRequestId, aGeneration);
  if (!route) {
    return;
  }
  auto failed = std::move(route->mCallbacks.mFailed);
  CancelRoot(aRequestId, aGeneration,
             NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE);
  if (failed) {
    failed(NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE);
  }
}

void ProcessServiceState::RootFinished(uint64_t aRequestId,
                                       uint64_t aGeneration,
                                       uint32_t aLastSequence,
                                       uint32_t aBodyBytes,
                                       uint32_t aStyleCount, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  Route* route = LookupRoute(aRequestId, aGeneration);
  if (!route) {
    TransportFailed(NS_ERROR_UNEXPECTED);
    return;
  }
  if (route->mFinished) {
    TransportFailed(NS_ERROR_UNEXPECTED);
    return;
  }
  route->mFinished = true;
  auto finished = std::move(route->mCallbacks.mFinished);
  finished(aLastSequence, aBodyBytes, aStyleCount, aStatus);
  route = LookupRoute(aRequestId, aGeneration);
  if (route && !route->mOutstandingStyles) {
    mRoutes.Remove(aRequestId);
  }
}

void ProcessServiceState::TransportFailed(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mShuttingDown) {
    return;
  }
  mReady = false;
  mStatus = NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE;
  mFailed = true;
  FailAll(mStatus);
}

void ProcessServiceState::FailAll(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  nsTArray<std::function<void(nsresult)>> callbacks;
  for (auto iter = mRoutes.Iter(); !iter.Done(); iter.Next()) {
    if (iter.Data() && iter.Data()->mCallbacks.mFailed) {
      callbacks.AppendElement(std::move(iter.Data()->mCallbacks.mFailed));
    }
  }
  mRoutes.Clear();
  mStyleOwners.Clear();
  for (auto& callback : callbacks) {
    callback(aStatus);
  }
}

void ProcessServiceState::BeginShutdown() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mShuttingDown) {
    return;
  }
  mShuttingDown = true;
  mReady = false;
  FailAll(NS_ERROR_ABORT);
  if (mActor) {
    if (NS_FAILED(mActor->BeginShutdown())) {
      mActor->Close();
    }
  } else {
    mActorDestroyed = true;
  }
}

bool ProcessServiceState::IsShutdownComplete() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mActorDestroyed;
}

base::ProcessHandle ProcessServiceState::TakeProcess() {
  MOZ_ASSERT(NS_IsMainThread());
  return std::exchange(mProcess, base::kInvalidProcessHandle);
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

static nsresult ShutdownActivationProcessService() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sProcessServiceState) {
    return NS_OK;
  }
  RefPtr<ProcessServiceState> state = sProcessServiceState.forget();
  state->BeginShutdown();

  nsCOMPtr<nsITimer> deadline;
  bool timedOut = false;
  if (NS_SUCCEEDED(NS_NewTimerWithCallback(
          getter_AddRefs(deadline), [&timedOut](nsITimer*) { timedOut = true; },
          kActivationProcessAdmissionTimeoutMs, nsITimer::TYPE_ONE_SHOT,
          "NaiveFox::ActivationProcessShutdownTimeout"_ns))) {
    (void)SpinEventLoopUntil("NaiveFox::ActivationProcessShutdown"_ns, [&]() {
      return state->IsShutdownComplete() || timedOut;
    });
    (void)deadline->Cancel();
  }

  bool graceful = state->IsShutdownComplete();
  const base::ProcessHandle childProcess = state->TakeProcess();
  if (childProcess != base::kInvalidProcessHandle) {
    if (!graceful) {
      (void)base::KillProcess(childProcess, 1);
    }
    int processInfo = 0;
    const base::ProcessStatus processStatus = base::WaitForProcess(
        childProcess, base::BlockingWait::Yes, &processInfo);
    graceful &=
        processStatus == base::ProcessStatus::Exited && processInfo == 0;
  }
  return graceful ? NS_OK : NS_ERROR_FAILURE;
}

nsresult NativeStylePreloadActivation::InitializeProcess() {
  MOZ_ASSERT(NS_IsMainThread());
#if !defined(XP_UNIX) || defined(ANDROID)
  return NS_ERROR_NOT_IMPLEMENTED;
#else
  if (sProcessServiceState) {
    return sProcessServiceState->IsReady() ? NS_OK
                                           : NS_ERROR_ALREADY_INITIALIZED;
  }
  RefPtr<ProcessServiceState> state = new ProcessServiceState();
  MOZ_TRY(state->BeginLaunch());
  sProcessServiceState = state;

  nsCOMPtr<nsITimer> deadline;
  nsresult rv = NS_NewTimerWithCallback(
      getter_AddRefs(deadline),
      [state](nsITimer*) { state->LaunchFailed(NS_ERROR_NET_TIMEOUT); },
      kActivationProcessAdmissionTimeoutMs, nsITimer::TYPE_ONE_SHOT,
      "NaiveFox::ActivationProcessStartupTimeout"_ns);
  if (NS_FAILED(rv)) {
    state->LaunchFailed(rv);
  }
  const bool processed = SpinEventLoopUntil(
      "NaiveFox::ActivationProcessStartup"_ns,
      [&]() { return state->IsReady() || state->HasFailed(); });
  if (deadline) {
    (void)deadline->Cancel();
  }
  if (!processed || !state->IsReady()) {
    const nsresult status = processed ? state->Status() : NS_ERROR_FAILURE;
    ShutdownProcess();
    return status;
  }
  return NS_OK;
#endif
}

bool NativeStylePreloadActivation::IsProcessReady() {
  MOZ_ASSERT(NS_IsMainThread());
  return sProcessServiceState && sProcessServiceState->IsReady();
}

void NativeStylePreloadActivation::ShutdownProcess() {
  MOZ_ASSERT(NS_IsMainThread());
  (void)ShutdownActivationProcessService();
}

nsresult NativeStylePreloadActivation::StartProcessRoot(
    NativeRootReplacementActivationDescriptor&& aDescriptor,
    uint32_t aMaximumBodyBytes,
    NativeStylePreloadProcessRootCallbacks&& aCallbacks, uint64_t& aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sProcessServiceState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sProcessServiceState->StartRoot(std::move(aDescriptor),
                                         aMaximumBodyBytes,
                                         std::move(aCallbacks), aRequestId);
}

nsresult NativeStylePreloadActivation::ForwardProcessRootData(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sProcessServiceState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sProcessServiceState->SendRootData(aRequestId, aGeneration, aSequence,
                                            std::move(aData));
}

nsresult NativeStylePreloadActivation::ForwardProcessRootStop(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sProcessServiceState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sProcessServiceState->SendRootStop(aRequestId, aGeneration, aSequence,
                                            aStatus);
}

void NativeStylePreloadActivation::CancelProcessRoot(uint64_t aRequestId,
                                                     uint64_t aGeneration,
                                                     nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sProcessServiceState) {
    sProcessServiceState->CancelRoot(aRequestId, aGeneration, aStatus);
  }
}

nsresult NativeStylePreloadActivation::CompleteProcessStyle(
    uint64_t aStyleRequestId, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!sProcessServiceState) {
    return NS_ERROR_NOT_INITIALIZED;
  }
  return sProcessServiceState->CompleteStyle(aStyleRequestId, aStatus);
}

nsresult NativeStylePreloadActivation::RunProcessBootstrapAdmission() {
  MOZ_ASSERT(NS_IsMainThread());
#if !defined(XP_UNIX) || defined(ANDROID)
  return NS_ERROR_NOT_IMPLEMENTED;
#else
  MOZ_TRY(InitializeProcess());
  if (!IsProcessReady()) {
    ShutdownProcess();
    return NS_ERROR_FAILURE;
  }
  return ShutdownActivationProcessService();
#endif
}

int RunNativeStylePreloadActivationChild(int aArgc, char* aArgv[]) {
#if !defined(XP_UNIX) || defined(ANDROID)
  return 2;
#else
  nsAutoCString loggingError;
  if (NS_FAILED(ConfigureRuntimeLogging(RuntimeLogMode::Console, EmptyCString(),
                                        loggingError))) {
    std::fprintf(stderr, "NaiveFox activation child logging error: %s\n",
                 loggingError.get());
    return 2;
  }
  auto runtimeLogging = MakeScopeExit([] { ShutdownRuntimeLogging(); });
  base::AtExitManager atExit;
  MessageLoopForUI mainLoop(MessageLoop::TYPE_MOZILLA_CHILD);
  NS_LogInit();
  auto logging = MakeScopeExit([] { NS_LogTerm(); });
  LogModule::Init(aArgc, aArgv);

  Maybe<UniqueFileHandle> ipcHandle = geckoargs::sIPCHandle.Get(aArgc, aArgv);
  Maybe<uint64_t> parentPid = geckoargs::sParentPid.Get(aArgc, aArgv);
  Maybe<const char*> channelIdString =
      geckoargs::sInitialChannelID.Get(aArgc, aArgv);
  nsID channelId;
  if (ipcHandle.isNothing() || !*ipcHandle || parentPid.isNothing() ||
      *parentPid > uint64_t(INT32_MAX) || channelIdString.isNothing() ||
      !channelId.Parse(nsDependentCString(*channelIdString))) {
    return 2;
  }

  nsresult rv = NS_InitMinimalXPCOM();
  if (NS_FAILED(rv) || !EnterNativeStylePreloadActivationChildRole()) {
    if (NS_SUCCEEDED(rv)) {
      (void)NS_ShutdownXPCOM(nullptr);
    }
    return 2;
  }
  auto ioThread = MakeUnique<ipc::IOThreadChild>(
      IPC::Channel::ChannelHandle{std::move(*ipcHandle)},
      base::ProcessId(*parentPid));
  Endpoint<PNativeStylePreloadProcessChild> endpoint(
      ipc::PrivateIPDLInterface{}, ioThread->TakeInitialPort(), channelId,
      ipc::EndpointProcInfo::Current(),
      ipc::EndpointProcInfo{.mPid = base::ProcessId(*parentPid),
                            .mChildID = 0});

  bool done = false;
  RefPtr<ActivationProcessChild> actor =
      new ActivationProcessChild(base::ProcessId(*parentPid), &done);
  if (!endpoint.Bind(actor)) {
    ioThread = nullptr;
    (void)NS_ShutdownXPCOM(nullptr);
    return 2;
  }
  if (!actor->Start()) {
    actor->Close();
    (void)SpinEventLoopUntil("NaiveFox::ActivationChildBindFailure"_ns,
                             [&]() { return done; });
    actor = nullptr;
    ioThread = nullptr;
    (void)NS_ShutdownXPCOM(nullptr);
    return 2;
  }

  RuntimeLogEvent(
      "Native activation process phase=child-running parent_pid=%llu "
      "child_pid=%llu\n",
      static_cast<unsigned long long>(*parentPid),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  const bool processed = SpinEventLoopUntil(
      "NaiveFox::ActivationChildLifetime"_ns, [&]() { return done; });
  actor = nullptr;
  ioThread = nullptr;
  const nsresult shutdownRv = NS_ShutdownXPCOM(nullptr);
  return processed && NS_SUCCEEDED(shutdownRv) ? 0 : 2;
#endif
}

}  // namespace mozilla::naivefox
