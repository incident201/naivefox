/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NativeStylePreloadProcessBridge.h"

#include <algorithm>
#include <atomic>
#include <limits>
#include <utility>

#include "Config.h"
#include "RuntimeLogging.h"
#include "mozilla/Assertions.h"
#include "mozilla/Maybe.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessChild.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessParent.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessRootChild.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessRootParent.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessStyleChild.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessStyleParent.h"
#include "nsCOMPtr.h"
#include "nsHashKeys.h"
#include "nsHtml5SpeculativeScanner.h"
#include "nsHtml5StylePreloadDescriptor.h"
#include "nsIThread.h"
#include "nsTHashMap.h"
#include "nsThreadUtils.h"
#include "prproces.h"

namespace mozilla::naivefox {

void detail::NativeStylePreloadProcessCanceledRoutes::Insert(
    uint64_t aRequestId, uint64_t aGeneration) {
  MOZ_ASSERT(aRequestId);
  MOZ_ASSERT(aGeneration);
  mRoutes.InsertOrUpdate(aRequestId, aGeneration);
}

bool detail::NativeStylePreloadProcessCanceledRoutes::Contains(
    uint64_t aRequestId, uint64_t aGeneration) const {
  return aGeneration && mRoutes.Get(aRequestId) == aGeneration;
}

void detail::NativeStylePreloadProcessCanceledRoutes::Remove(
    uint64_t aRequestId, uint64_t aGeneration) {
  if (Contains(aRequestId, aGeneration)) {
    mRoutes.Remove(aRequestId);
  }
}

namespace {

using ipc::IPCResult;

NativeHtml5StylePreloadDescriptorArgs SerializeStyleDescriptor(
    const nsHtml5StylePreloadDescriptor& aDescriptor) {
  NativeHtml5StylePreloadDescriptorArgs args;
  args.url() = aDescriptor.Url();
  args.charset() = aDescriptor.Charset();
  args.crossOrigin() = aDescriptor.CrossOrigin();
  args.media() = aDescriptor.Media();
  args.referrerPolicy() = aDescriptor.ReferrerPolicy();
  args.nonce() = aDescriptor.Nonce();
  args.integrity() = aDescriptor.Integrity();
  args.fetchPriority() = aDescriptor.FetchPriority();
  args.linkPreload() = aDescriptor.IsLinkPreload();
  return args;
}

class ChildParserState final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ChildParserState)

  ChildParserState(nsISerialEventTarget* aParserTarget,
                   uint32_t aMaximumBodyBytes)
      : mScanner(MakeUnique<nsHtml5SpeculativeScanner>(aParserTarget)),
        mMaximumBodyBytes(aMaximumBodyBytes) {}

  nsresult Feed(nsCString&& aData,
                nsTArray<nsHtml5StylePreloadDescriptor>& aDescriptors) {
    if (mFinished || mFailed ||
        aData.Length() > mMaximumBodyBytes - mBodyBytes) {
      return Fail(aData.Length() > mMaximumBodyBytes - mBodyBytes
                      ? NS_ERROR_FILE_TOO_BIG
                      : NS_ERROR_UNEXPECTED);
    }
    const char* bytes = aData.BeginReading();
    if (!std::all_of(bytes, bytes + aData.Length(), [](char aByte) {
          return static_cast<unsigned char>(aByte) < 0x80;
        })) {
      return Fail(NS_ERROR_ILLEGAL_INPUT);
    }
    mBodyBytes += aData.Length();
    nsresult rv = mScanner->Feed(NS_ConvertASCIItoUTF16(aData));
    if (NS_FAILED(rv)) {
      return Fail(rv);
    }
    mScanner->TakeStyleDescriptors(aDescriptors);
    return NS_OK;
  }

  nsresult Finish(nsTArray<nsHtml5StylePreloadDescriptor>& aDescriptors) {
    if (mFinished || mFailed || !mScanner) {
      return Fail(NS_ERROR_UNEXPECTED);
    }
    mFinished = true;
    nsresult rv = mScanner->Finish();
    if (NS_SUCCEEDED(rv)) {
      mScanner->TakeStyleDescriptors(aDescriptors);
    }
    mScanner = nullptr;
    if (NS_FAILED(rv)) {
      mFailed = true;
    }
    return rv;
  }

  uint32_t BodyBytes() const { return mBodyBytes; }

  void Cancel() {
    mFailed = true;
    mScanner = nullptr;
  }

 private:
  ~ChildParserState() = default;

  nsresult Fail(nsresult aStatus) {
    mFailed = true;
    return aStatus;
  }

  UniquePtr<nsHtml5SpeculativeScanner> mScanner;
  const uint32_t mMaximumBodyBytes;
  uint32_t mBodyBytes = 0;
  bool mFinished = false;
  bool mFailed = false;
};

}  // namespace

class NativeStylePreloadProcessRootParentActor final
    : public PNativeStylePreloadProcessRootParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessRootParentActor, override)

  NativeStylePreloadProcessRootParentActor(
      NativeStylePreloadProcessParentBridge* aBridge, uint64_t aRequestId,
      uint64_t aGeneration)
      : mBridge(aBridge), mRequestId(aRequestId), mGeneration(aGeneration) {}

  nsresult Start(const NativeRootReplacementActivationArgs& aArgs,
                 uint64_t aExpectedParentPid, uint64_t aExpectedChildPid,
                 uint32_t aMaximumBodyBytes, bool aFullProcess) {
    MOZ_ASSERT(NS_IsMainThread());
    if (mStarted || aArgs.requestId() != mRequestId ||
        aArgs.generation() != mGeneration || !aMaximumBodyBytes ||
        aMaximumBodyBytes > PreambleConfig::kMaximumBytes ||
        !SendStart(aArgs, aExpectedParentPid, aExpectedChildPid,
                   aMaximumBodyBytes, aFullProcess)) {
      return NS_ERROR_FAILURE;
    }
    mStarted = true;
    mMaximumBodyBytes = aMaximumBodyBytes;
    mFullProcess = aFullProcess;
    return NS_OK;
  }

  nsresult Data(uint32_t aSequence, nsCString&& aData) {
    MOZ_ASSERT(NS_IsMainThread());
    if (mFullProcess || !mReady || mTerminal || !mNextSequence ||
        aSequence == std::numeric_limits<uint32_t>::max() ||
        aSequence != mNextSequence ||
        !SendData(mRequestId, mGeneration, aSequence, aData)) {
      return NS_ERROR_UNEXPECTED;
    }
    ++mNextSequence;
    return NS_OK;
  }

  nsresult Stop(uint32_t aSequence, nsresult aStatus) {
    MOZ_ASSERT(NS_IsMainThread());
    if (mFullProcess || !mReady || mTerminal || mStopSent || !mNextSequence ||
        aSequence == std::numeric_limits<uint32_t>::max() ||
        aSequence != mNextSequence ||
        !SendStop(mRequestId, mGeneration, aSequence, aStatus)) {
      return NS_ERROR_UNEXPECTED;
    }
    ++mNextSequence;
    mStopSent = true;
    return NS_OK;
  }

  nsresult Cancel(nsresult aStatus) {
    MOZ_ASSERT(NS_IsMainThread());
    if (mCancelSent || mFinished) {
      return NS_OK;
    }
    mTerminal = true;
    mCancelSent = true;
    return SendCancel(mRequestId, mGeneration, aStatus) ? NS_OK
                                                        : NS_ERROR_FAILURE;
  }

 private:
  friend class NativeStylePreloadProcessParentBridge;
  friend class NativeStylePreloadProcessParentBridge::Impl;
  ~NativeStylePreloadProcessRootParentActor() = default;

  IPCResult RecvReady(const uint64_t& aRequestId, const uint64_t& aGeneration,
                      const uint64_t& aChildPid) final;
  IPCResult RecvParserFinished(const uint64_t& aRequestId,
                               const uint64_t& aGeneration,
                               const uint32_t& aLastSequence,
                               const uint32_t& aBodyBytes,
                               const uint32_t& aStyleCount,
                               const nsresult& aStatus) final;
  IPCResult RecvCanceled(const uint64_t& aRequestId,
                         const uint64_t& aGeneration) final;
  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final;

  NativeStylePreloadProcessParentBridge* const mBridge;
  const uint64_t mRequestId;
  const uint64_t mGeneration;
  uint32_t mNextSequence = 1;
  uint32_t mNextDiscoverySequence = 1;
  uint32_t mDiscoveredStyleCount = 0;
  uint32_t mMaximumBodyBytes = 0;
  bool mStarted = false;
  bool mFullProcess = false;
  bool mReady = false;
  bool mStopSent = false;
  bool mFinished = false;
  bool mTerminal = false;
  bool mCancelSent = false;
  bool mCancelAcknowledged = false;
};

class NativeStylePreloadProcessStyleParentActor final
    : public PNativeStylePreloadProcessStyleParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessStyleParentActor, override)

  NativeStylePreloadProcessStyleParentActor(
      NativeStylePreloadProcessParentBridge* aBridge,
      const NativeStylePreloadProcessArgs& aArgs)
      : mBridge(aBridge),
        mStyleRequestId(aArgs.styleRequestId()),
        mRootRequestId(aArgs.rootRequestId()),
        mRootGeneration(aArgs.rootGeneration()) {}

  nsresult Complete(nsresult aStatus) {
    MOZ_ASSERT(NS_IsMainThread());
    if (mCompleted) {
      return NS_ERROR_UNEXPECTED;
    }
    if (mConstructing) {
      mPendingCompletion = Some(aStatus);
      return NS_OK;
    }
    if (!mActorAlive || !SendCompleteStyle(mStyleRequestId, mRootRequestId,
                                           mRootGeneration, aStatus)) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    mCompleted = true;
    return NS_OK;
  }

  void FinishConstruction();

 private:
  friend class NativeStylePreloadProcessParentBridge;
  friend class NativeStylePreloadProcessParentBridge::Impl;
  ~NativeStylePreloadProcessStyleParentActor() = default;

  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final;

  NativeStylePreloadProcessParentBridge* const mBridge;
  const uint64_t mStyleRequestId;
  const uint64_t mRootRequestId;
  const uint64_t mRootGeneration;
  bool mCompleted = false;
  bool mConstructing = true;
  bool mActorAlive = true;
  Maybe<nsresult> mPendingCompletion;
};

class NativeStylePreloadProcessStyleChildActor final
    : public PNativeStylePreloadProcessStyleChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessStyleChildActor, override)

  NativeStylePreloadProcessStyleChildActor(
      NativeStylePreloadProcessChildBridge* aBridge,
      const NativeStylePreloadProcessArgs& aArgs)
      : mBridge(aBridge),
        mStyleRequestId(aArgs.styleRequestId()),
        mRootRequestId(aArgs.rootRequestId()),
        mRootGeneration(aArgs.rootGeneration()) {}

 private:
  friend class NativeStylePreloadProcessChildBridge;
  friend class NativeStylePreloadProcessChildBridge::Impl;
  ~NativeStylePreloadProcessStyleChildActor() = default;

  IPCResult RecvCompleteStyle(const uint64_t& aStyleRequestId,
                              const uint64_t& aRootRequestId,
                              const uint64_t& aRootGeneration,
                              const nsresult& aStatus) final;
  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final;
  void PrepareForShutdown() { mCompleted = true; }

  NativeStylePreloadProcessChildBridge* const mBridge;
  const uint64_t mStyleRequestId;
  const uint64_t mRootRequestId;
  const uint64_t mRootGeneration;
  bool mCompleted = false;
};

class NativeStylePreloadProcessRootChildActor final
    : public PNativeStylePreloadProcessRootChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(NativeStylePreloadProcessRootChildActor,
                                        override)

  NativeStylePreloadProcessRootChildActor(
      NativeStylePreloadProcessChildBridge* aBridge, uint64_t aRequestId,
      uint64_t aGeneration)
      : mBridge(aBridge), mRequestId(aRequestId), mGeneration(aGeneration) {}

 private:
  friend class NativeStylePreloadProcessChildBridge;
  friend class NativeStylePreloadProcessChildBridge::Impl;
  ~NativeStylePreloadProcessRootChildActor() = default;

  IPCResult RecvStart(const NativeRootReplacementActivationArgs& aArgs,
                      const uint64_t& aExpectedParentPid,
                      const uint64_t& aExpectedChildPid,
                      const uint32_t& aMaximumBodyBytes,
                      const bool& aFullProcess) final;
  IPCResult RecvData(const uint64_t& aRequestId, const uint64_t& aGeneration,
                     const uint32_t& aSequence, const nsACString& aData) final;
  IPCResult RecvStop(const uint64_t& aRequestId, const uint64_t& aGeneration,
                     const uint32_t& aSequence, const nsresult& aStatus) final;
  IPCResult RecvCancel(const uint64_t& aRequestId, const uint64_t& aGeneration,
                       const nsresult& aStatus) final;
  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final;

  void ParserChunkComplete(
      uint32_t aSequence, nsresult aStatus,
      nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors);
  void ParserFinishComplete(
      uint32_t aSequence, nsresult aStatus, uint32_t aBodyBytes,
      nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors);
  void Fail(nsresult aStatus);
  void PrepareForShutdown();
  nsresult ForwardOnStart();
  nsresult AcceptData(uint64_t aRequestId, uint64_t aGeneration,
                      uint32_t aSequence, nsCString&& aData,
                      bool aFromBackground);
  nsresult AcceptStop(uint64_t aRequestId, uint64_t aGeneration,
                      uint32_t aSequence, nsresult aStatus,
                      bool aFromBackground);

  NativeStylePreloadProcessChildBridge* const mBridge;
  const uint64_t mRequestId;
  const uint64_t mGeneration;
  NativeRootReplacementActivationArgs mRootArgs;
  RefPtr<ChildParserState> mParser;
  uint32_t mNextSequence = 1;
  uint32_t mLastSequence = 0;
  uint32_t mNextParserCompletionSequence = 1;
  uint32_t mNextDiscoverySequence = 1;
  uint32_t mStyleCount = 0;
  bool mStarted = false;
  bool mFullProcess = false;
  bool mBackgroundOnStart = false;
  bool mStopReceived = false;
  bool mFinishedSent = false;
  bool mCancelReceived = false;
  bool mActorAlive = true;
};

class NativeStylePreloadProcessParentBridge::Impl final {
 public:
  Impl(PNativeStylePreloadProcessParent* aManager, Callbacks&& aCallbacks)
      : mManager(aManager), mCallbacks(std::move(aCallbacks)) {}

  void Fail(nsresult aStatus) {
    if (!mFailed) {
      mFailed = true;
      if (mCallbacks.mTransportFailed) {
        mCallbacks.mTransportFailed(aStatus);
      }
    }
  }

  PNativeStylePreloadProcessParent* const mManager;
  Callbacks mCallbacks;
  nsTHashMap<nsUint64HashKey, RefPtr<NativeStylePreloadProcessRootParentActor>>
      mRoots;
  nsTHashMap<nsUint64HashKey, RefPtr<NativeStylePreloadProcessStyleParentActor>>
      mStyles;
  detail::NativeStylePreloadProcessCanceledRoutes mCanceledRoots;
  bool mFailed = false;
};

class NativeStylePreloadProcessChildBridge::Impl final {
 public:
  Impl(PNativeStylePreloadProcessChild* aManager,
       NativeStylePreloadProcessChildBridge::RootBackgroundReady&&
           aRootBackgroundReady,
       NativeStylePreloadProcessChildBridge::StyleBackgroundReady&&
           aStyleBackgroundReady)
      : mManager(aManager),
        mRootBackgroundReady(std::move(aRootBackgroundReady)),
        mStyleBackgroundReady(std::move(aStyleBackgroundReady)) {}

  nsresult EnsureParserThread() {
    if (mParserThread) {
      return NS_OK;
    }
    return NS_NewNamedThread("NF HTML5 Parser", getter_AddRefs(mParserThread));
  }

  nsresult CreateStyles(
      NativeStylePreloadProcessRootChildActor* aRoot,
      nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors) {
    MOZ_ASSERT(NS_IsMainThread());
    for (auto& descriptor : aDescriptors) {
      if (!mNextStyleRequestId || !aRoot->mNextDiscoverySequence) {
        return NS_ERROR_OUT_OF_MEMORY;
      }
      NativeStylePreloadProcessArgs args;
      args.rootRequestId() = aRoot->mRequestId;
      args.rootGeneration() = aRoot->mGeneration;
      args.styleRequestId() = mNextStyleRequestId++;
      args.discoverySequence() = aRoot->mNextDiscoverySequence++;
      args.descriptor() = SerializeStyleDescriptor(descriptor);

      RefPtr actor = new NativeStylePreloadProcessStyleChildActor(mOwner, args);
      const uint64_t styleId = args.styleRequestId();
      mStyles.InsertOrUpdate(styleId, actor);
      if (!mManager->SendPNativeStylePreloadProcessStyleConstructor(actor,
                                                                    args)) {
        mStyles.Remove(styleId);
        return NS_ERROR_FAILURE;
      }
      if (aRoot->mFullProcess &&
          (!mStyleBackgroundReady ||
           NS_FAILED(mStyleBackgroundReady(
               args.rootRequestId(), args.rootGeneration(), styleId,
               args.discoverySequence())))) {
        mStyles.Remove(styleId);
        return NS_ERROR_FAILURE;
      }
      ++aRoot->mStyleCount;
      RuntimeLogEvent(
          "Native activation child phase=style-discovered root=%llu "
          "generation=%llu style=%llu sequence=%u pid=%llu\n",
          static_cast<unsigned long long>(aRoot->mRequestId),
          static_cast<unsigned long long>(aRoot->mGeneration),
          static_cast<unsigned long long>(styleId), args.discoverySequence(),
          static_cast<unsigned long long>(base::GetCurrentProcId()));
    }
    return NS_OK;
  }

  PNativeStylePreloadProcessChild* const mManager;
  NativeStylePreloadProcessChildBridge::RootBackgroundReady
      mRootBackgroundReady;
  NativeStylePreloadProcessChildBridge::StyleBackgroundReady
      mStyleBackgroundReady;
  NativeStylePreloadProcessChildBridge* mOwner = nullptr;
  nsCOMPtr<nsIThread> mParserThread;
  nsTHashMap<nsUint64HashKey, RefPtr<NativeStylePreloadProcessRootChildActor>>
      mRoots;
  nsTHashMap<nsUint64HashKey, RefPtr<NativeStylePreloadProcessStyleChildActor>>
      mStyles;
  uint64_t mNextStyleRequestId = 1;
  bool mFailed = false;
  bool mShuttingDown = false;
};

NativeStylePreloadProcessParentBridge::NativeStylePreloadProcessParentBridge(
    PNativeStylePreloadProcessParent* aManager, Callbacks&& aCallbacks)
    : mImpl(MakeUnique<Impl>(aManager, std::move(aCallbacks))) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_RELEASE_ASSERT(aManager);
}

NativeStylePreloadProcessParentBridge::
    ~NativeStylePreloadProcessParentBridge() = default;

void NativeStylePreloadProcessStyleParentActor::FinishConstruction() {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_RELEASE_ASSERT(mConstructing);
  mConstructing = false;
  if (mPendingCompletion.isNothing()) {
    return;
  }
  const nsresult status = mPendingCompletion.extract();
  RefPtr self = this;
  nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::CompleteConstructedActivationProcessStyle", [self, status] {
        if (!self->mActorAlive) {
          return;
        }
        nsresult completeRv = self->Complete(status);
        if (NS_FAILED(completeRv) && self->mActorAlive) {
          self->mBridge->mImpl->Fail(completeRv);
        }
      }));
  if (NS_FAILED(rv)) {
    mBridge->mImpl->Fail(rv);
  }
}

nsresult NativeStylePreloadProcessParentBridge::StartRoot(
    NativeRootReplacementActivationArgs&& aArgs, uint64_t aExpectedParentPid,
    uint64_t aExpectedChildPid, uint32_t aMaximumBodyBytes,
    bool aFullProcess) {
  MOZ_ASSERT(NS_IsMainThread());
  const uint64_t requestId = aArgs.requestId();
  const uint64_t generation = aArgs.generation();
  if (mImpl->mFailed || !requestId || !generation ||
      mImpl->mRoots.Contains(requestId)) {
    return NS_ERROR_UNEXPECTED;
  }
  RefPtr actor =
      new NativeStylePreloadProcessRootParentActor(this, requestId, generation);
  mImpl->mRoots.InsertOrUpdate(requestId, actor);
  if (!mImpl->mManager->SendPNativeStylePreloadProcessRootConstructor(
          actor, requestId, generation)) {
    mImpl->mRoots.Remove(requestId);
    mImpl->Fail(NS_ERROR_FAILURE);
    return NS_ERROR_FAILURE;
  }
  nsresult rv = actor->Start(aArgs, aExpectedParentPid, aExpectedChildPid,
                             aMaximumBodyBytes, aFullProcess);
  if (NS_FAILED(rv)) {
    mImpl->mCanceledRoots.Insert(requestId, generation);
    if (NS_FAILED(actor->Cancel(rv))) {
      mImpl->Fail(rv);
    }
  }
  return rv;
}

nsresult NativeStylePreloadProcessParentBridge::SendRootData(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mRoots.Lookup(aRequestId).DataPtrOrNull();
  if (!actor || (*actor)->mGeneration != aGeneration) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  nsresult rv = (*actor)->Data(aSequence, std::move(aData));
  if (NS_FAILED(rv)) {
    mImpl->Fail(rv);
  }
  return rv;
}

nsresult NativeStylePreloadProcessParentBridge::SendRootStop(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mRoots.Lookup(aRequestId).DataPtrOrNull();
  if (!actor || (*actor)->mGeneration != aGeneration) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  nsresult rv = (*actor)->Stop(aSequence, aStatus);
  if (NS_FAILED(rv)) {
    mImpl->Fail(rv);
  }
  return rv;
}

void NativeStylePreloadProcessParentBridge::CancelRoot(uint64_t aRequestId,
                                                       uint64_t aGeneration,
                                                       nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mRoots.Lookup(aRequestId).DataPtrOrNull();
  if (actor && (*actor)->mGeneration == aGeneration) {
    mImpl->mCanceledRoots.Insert(aRequestId, aGeneration);
    nsresult rv = (*actor)->Cancel(aStatus);
    if (NS_FAILED(rv)) {
      mImpl->Fail(rv);
    }
  }
}

nsresult NativeStylePreloadProcessParentBridge::CompleteStyle(
    uint64_t aStyleRequestId, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mStyles.Lookup(aStyleRequestId).DataPtrOrNull();
  if (!actor) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  nsresult rv = (*actor)->Complete(aStatus);
  if (NS_FAILED(rv)) {
    mImpl->Fail(rv);
  }
  return rv;
}

already_AddRefed<PNativeStylePreloadProcessStyleParent>
NativeStylePreloadProcessParentBridge::AllocStyle(
    const NativeStylePreloadProcessArgs& aArgs) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mFailed || !aArgs.styleRequestId() || !aArgs.rootRequestId() ||
      !aArgs.rootGeneration() || !aArgs.discoverySequence() ||
      mImpl->mStyles.Contains(aArgs.styleRequestId())) {
    return nullptr;
  }
  if (mImpl->mCanceledRoots.Contains(aArgs.rootRequestId(),
                                     aArgs.rootGeneration())) {
    RefPtr actor = new NativeStylePreloadProcessStyleParentActor(this, aArgs);
    mImpl->mStyles.InsertOrUpdate(aArgs.styleRequestId(), actor);
    MOZ_ALWAYS_SUCCEEDS(actor->Complete(NS_ERROR_ABORT));
    actor->FinishConstruction();
    return actor.forget();
  }
  auto* root = mImpl->mRoots.Lookup(aArgs.rootRequestId()).DataPtrOrNull();
  if (!root || (*root)->mGeneration != aArgs.rootGeneration() ||
      !(*root)->mReady || (*root)->mTerminal ||
      aArgs.discoverySequence() != (*root)->mNextDiscoverySequence ||
      !mImpl->mCallbacks.mStyleDiscovered) {
    mImpl->Fail(NS_ERROR_UNEXPECTED);
    return nullptr;
  }
  RefPtr rootActor = *root;
  RefPtr actor = new NativeStylePreloadProcessStyleParentActor(this, aArgs);
  const uint64_t styleRequestId = aArgs.styleRequestId();
  mImpl->mStyles.InsertOrUpdate(styleRequestId, actor);
  ++rootActor->mNextDiscoverySequence;
  ++rootActor->mDiscoveredStyleCount;
  nsresult rv = mImpl->mCallbacks.mStyleDiscovered(aArgs);
  if (NS_FAILED(rv)) {
    if (mImpl->mCallbacks.mRootFailed) {
      mImpl->mCallbacks.mRootFailed(aArgs.rootRequestId(),
                                    aArgs.rootGeneration(), rv);
    } else {
      mImpl->Fail(NS_ERROR_UNEXPECTED);
    }
  }
  actor->FinishConstruction();
  return actor.forget();
}

bool NativeStylePreloadProcessParentBridge::DeallocRoot(
    PNativeStylePreloadProcessRootParent* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = static_cast<NativeStylePreloadProcessRootParentActor*>(aActor);
  mImpl->mCanceledRoots.Remove(actor->mRequestId, actor->mGeneration);
  mImpl->mRoots.Remove(actor->mRequestId);
  return true;
}

bool NativeStylePreloadProcessParentBridge::DeallocStyle(
    PNativeStylePreloadProcessStyleParent* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = static_cast<NativeStylePreloadProcessStyleParentActor*>(aActor);
  mImpl->mStyles.Remove(actor->mStyleRequestId);
  return true;
}

void NativeStylePreloadProcessParentBridge::ProcessActorDestroyed() {
  MOZ_ASSERT(NS_IsMainThread());
  mImpl->Fail(NS_ERROR_FAILURE);
}

IPCResult NativeStylePreloadProcessRootParentActor::RecvReady(
    const uint64_t& aRequestId, const uint64_t& aGeneration,
    const uint64_t& aChildPid) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mStarted || aRequestId != mRequestId || aGeneration != mGeneration ||
      aChildPid != uint64_t(OtherPid())) {
    return IPC_FAIL_NO_REASON(this);
  }
  if (mTerminal) {
    return IPC_OK();
  }
  if (mReady) {
    return IPC_FAIL_NO_REASON(this);
  }
  mReady = true;
  nsresult rv =
      mBridge->mImpl->mCallbacks.mRootReady
          ? mBridge->mImpl->mCallbacks.mRootReady(mRequestId, mGeneration)
          : NS_ERROR_UNEXPECTED;
  if (NS_FAILED(rv)) {
    if (mBridge->mImpl->mCallbacks.mRootFailed) {
      mBridge->mImpl->mCallbacks.mRootFailed(mRequestId, mGeneration, rv);
      return IPC_OK();
    }
    return IPC_FAIL_NO_REASON(this);
  }
  return IPC_OK();
}

IPCResult NativeStylePreloadProcessRootParentActor::RecvParserFinished(
    const uint64_t& aRequestId, const uint64_t& aGeneration,
    const uint32_t& aLastSequence, const uint32_t& aBodyBytes,
    const uint32_t& aStyleCount, const nsresult& aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (aRequestId != mRequestId || aGeneration != mGeneration) {
    return IPC_FAIL_NO_REASON(this);
  }
  if (mTerminal) {
    return IPC_OK();
  }
  if (!mReady || mFinished || !mNextSequence ||
      (!mFullProcess && aLastSequence != mNextSequence - 1) ||
      !mMaximumBodyBytes ||
      aBodyBytes > mMaximumBodyBytes || aStyleCount != mDiscoveredStyleCount ||
      (NS_SUCCEEDED(aStatus) && !mFullProcess && !mStopSent)) {
    return IPC_FAIL_NO_REASON(this);
  }
  mFinished = true;
  mTerminal = true;
  if (mBridge->mImpl->mCallbacks.mRootFinished) {
    mBridge->mImpl->mCallbacks.mRootFinished(mRequestId, mGeneration,
                                             aLastSequence, aBodyBytes,
                                             aStyleCount, aStatus);
  }
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::DeleteFinishedActivationProcessRoot", [self] {
            (void)PNativeStylePreloadProcessRootParent::Send__delete__(self);
          })))) {
    return IPC_FAIL_NO_REASON(this);
  }
  return IPC_OK();
}

IPCResult NativeStylePreloadProcessRootParentActor::RecvCanceled(
    const uint64_t& aRequestId, const uint64_t& aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (aRequestId != mRequestId || aGeneration != mGeneration || !mCancelSent ||
      !mTerminal || mCancelAcknowledged) {
    return IPC_FAIL_NO_REASON(this);
  }
  mCancelAcknowledged = true;
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::DeleteCanceledActivationProcessRoot", [self] {
            (void)PNativeStylePreloadProcessRootParent::Send__delete__(self);
          })))) {
    return IPC_FAIL_NO_REASON(this);
  }
  return IPC_OK();
}

void NativeStylePreloadProcessRootParentActor::ActorDestroy(
    IProtocol::ActorDestroyReason aWhy) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mFinished || (aWhy != Deletion && aWhy != NormalShutdown)) {
    mBridge->mImpl->Fail(NS_ERROR_FAILURE);
  }
  (void)mBridge->DeallocRoot(this);
}

void NativeStylePreloadProcessStyleParentActor::ActorDestroy(
    IProtocol::ActorDestroyReason aWhy) {
  MOZ_ASSERT(NS_IsMainThread());
  mActorAlive = false;
  if (!mCompleted || (aWhy != Deletion && aWhy != NormalShutdown)) {
    mBridge->mImpl->Fail(NS_ERROR_FAILURE);
  }
  (void)mBridge->DeallocStyle(this);
}

NativeStylePreloadProcessChildBridge::NativeStylePreloadProcessChildBridge(
    PNativeStylePreloadProcessChild* aManager,
    RootBackgroundReady&& aRootBackgroundReady,
    StyleBackgroundReady&& aStyleBackgroundReady)
    : mImpl(MakeUnique<Impl>(aManager, std::move(aRootBackgroundReady),
                             std::move(aStyleBackgroundReady))) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_RELEASE_ASSERT(aManager);
  mImpl->mOwner = this;
}

NativeStylePreloadProcessChildBridge::~NativeStylePreloadProcessChildBridge() {
  MOZ_ASSERT(!mImpl->mParserThread);
}

nsresult NativeStylePreloadProcessChildBridge::Initialize() {
  MOZ_ASSERT(NS_IsMainThread());
  return mImpl->EnsureParserThread();
}

nsresult NativeStylePreloadProcessChildBridge::ForwardRootOnStart(
    uint64_t aRequestId, uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mRoots.Lookup(aRequestId).DataPtrOrNull();
  return actor && (*actor)->mGeneration == aGeneration
             ? (*actor)->ForwardOnStart()
             : NS_ERROR_NOT_AVAILABLE;
}

nsresult NativeStylePreloadProcessChildBridge::ForwardRootData(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mRoots.Lookup(aRequestId).DataPtrOrNull();
  return actor && (*actor)->mGeneration == aGeneration
             ? (*actor)->AcceptData(aRequestId, aGeneration, aSequence,
                                    std::move(aData), true)
             : NS_ERROR_NOT_AVAILABLE;
}

nsresult NativeStylePreloadProcessChildBridge::ForwardRootStop(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = mImpl->mRoots.Lookup(aRequestId).DataPtrOrNull();
  return actor && (*actor)->mGeneration == aGeneration
             ? (*actor)->AcceptStop(aRequestId, aGeneration, aSequence,
                                    aStatus, true)
             : NS_ERROR_NOT_AVAILABLE;
}

void NativeStylePreloadProcessChildBridge::Shutdown() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mShuttingDown) {
    return;
  }
  mImpl->mShuttingDown = true;
  for (auto iter = mImpl->mRoots.Iter(); !iter.Done(); iter.Next()) {
    iter.Data()->PrepareForShutdown();
  }
  for (auto iter = mImpl->mStyles.Iter(); !iter.Done(); iter.Next()) {
    iter.Data()->PrepareForShutdown();
  }
  mImpl->mRoots.Clear();
  mImpl->mStyles.Clear();
  if (mImpl->mParserThread) {
    (void)mImpl->mParserThread->Shutdown();
    mImpl->mParserThread = nullptr;
  }
}

already_AddRefed<PNativeStylePreloadProcessRootChild>
NativeStylePreloadProcessChildBridge::AllocRoot(uint64_t aRequestId,
                                                uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mFailed || mImpl->mShuttingDown || !mImpl->mParserThread ||
      !aRequestId || !aGeneration || mImpl->mRoots.Contains(aRequestId)) {
    return nullptr;
  }
  RefPtr actor = new NativeStylePreloadProcessRootChildActor(this, aRequestId,
                                                             aGeneration);
  mImpl->mRoots.InsertOrUpdate(aRequestId, actor);
  return actor.forget();
}

bool NativeStylePreloadProcessChildBridge::DeallocRoot(
    PNativeStylePreloadProcessRootChild* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = static_cast<NativeStylePreloadProcessRootChildActor*>(aActor);
  mImpl->mRoots.Remove(actor->mRequestId);
  return true;
}

bool NativeStylePreloadProcessChildBridge::DeallocStyle(
    PNativeStylePreloadProcessStyleChild* aActor) {
  MOZ_ASSERT(NS_IsMainThread());
  auto* actor = static_cast<NativeStylePreloadProcessStyleChildActor*>(aActor);
  mImpl->mStyles.Remove(actor->mStyleRequestId);
  return true;
}

void NativeStylePreloadProcessChildBridge::ProcessActorDestroyed() {
  MOZ_ASSERT(NS_IsMainThread());
  mImpl->mFailed = true;
  Shutdown();
}

IPCResult NativeStylePreloadProcessRootChildActor::RecvStart(
    const NativeRootReplacementActivationArgs& aArgs,
    const uint64_t& aExpectedParentPid, const uint64_t& aExpectedChildPid,
    const uint32_t& aMaximumBodyBytes, const bool& aFullProcess) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mStarted || aArgs.requestId() != mRequestId ||
      aArgs.generation() != mGeneration ||
      aExpectedParentPid != uint64_t(Manager()->OtherPid()) ||
      aExpectedChildPid != uint64_t(base::GetCurrentProcId()) ||
      !aMaximumBodyBytes || aMaximumBodyBytes > PreambleConfig::kMaximumBytes ||
      !mBridge->mImpl->mParserThread) {
    return IPC_FAIL_NO_REASON(this);
  }
  mStarted = true;
  mFullProcess = aFullProcess;
  mRootArgs = aArgs;
  mParser =
      new ChildParserState(mBridge->mImpl->mParserThread, aMaximumBodyBytes);
  if (!SendReady(mRequestId, mGeneration, uint64_t(base::GetCurrentProcId()))) {
    return IPC_FAIL_NO_REASON(this);
  }
  if (mFullProcess &&
      (!mBridge->mImpl->mRootBackgroundReady ||
       NS_FAILED(mBridge->mImpl->mRootBackgroundReady(mRequestId,
                                                      mGeneration)))) {
    return IPC_FAIL_NO_REASON(this);
  }
  RuntimeLogEvent(
      "Native activation child phase=root-ready request=%llu "
      "generation=%llu pid=%llu\n",
      static_cast<unsigned long long>(mRequestId),
      static_cast<unsigned long long>(mGeneration),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  return IPC_OK();
}

IPCResult NativeStylePreloadProcessRootChildActor::RecvData(
    const uint64_t& aRequestId, const uint64_t& aGeneration,
    const uint32_t& aSequence, const nsACString& aData) {
  return NS_SUCCEEDED(AcceptData(aRequestId, aGeneration, aSequence,
                                 nsCString(aData), false))
             ? IPC_OK()
             : IPC_FAIL_NO_REASON(this);
}

nsresult NativeStylePreloadProcessRootChildActor::ForwardOnStart() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mFullProcess || !mStarted || mBackgroundOnStart || mStopReceived ||
      mFinishedSent) {
    return NS_ERROR_UNEXPECTED;
  }
  mBackgroundOnStart = true;
  return NS_OK;
}

nsresult NativeStylePreloadProcessRootChildActor::AcceptData(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsCString&& aData, bool aFromBackground) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mStarted || mStopReceived || mFinishedSent || aRequestId != mRequestId ||
      aGeneration != mGeneration || !mNextSequence ||
      aSequence == std::numeric_limits<uint32_t>::max() ||
      aSequence != mNextSequence || !mParser ||
      aFromBackground != mFullProcess ||
      (aFromBackground && !mBackgroundOnStart)) {
    return NS_ERROR_UNEXPECTED;
  }
  ++mNextSequence;
  mLastSequence = aSequence;
  RuntimeLogEvent(
      "Native activation child phase=root-data-accepted request=%llu "
      "generation=%llu sequence=%u bytes=%u pid=%llu main_thread=1\n",
      static_cast<unsigned long long>(mRequestId),
      static_cast<unsigned long long>(mGeneration), aSequence,
      static_cast<unsigned>(aData.Length()),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  RefPtr self = this;
  RefPtr parser = mParser;
  nsresult rv = mBridge->mImpl->mParserThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::ActivationChildParserData",
      [self, parser, aSequence, data = std::move(aData)]() mutable {
        const uint32_t bodyBytes = data.Length();
        nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
        nsresult status = parser->Feed(std::move(data), descriptors);
        RuntimeLogEvent(
            "Native activation child phase=parser-feed request=%llu "
            "generation=%llu sequence=%u bytes=%u descriptors=%u "
            "status=0x%08x pid=%llu main_thread=%d\n",
            static_cast<unsigned long long>(self->mRequestId),
            static_cast<unsigned long long>(self->mGeneration), aSequence,
            bodyBytes, static_cast<unsigned>(descriptors.Length()),
            static_cast<unsigned>(status),
            static_cast<unsigned long long>(base::GetCurrentProcId()),
            NS_IsMainThread());
        (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::ActivationChildParserDataComplete",
            [self, aSequence, status,
             descriptors = std::move(descriptors)]() mutable {
              self->ParserChunkComplete(aSequence, status,
                                        std::move(descriptors));
            }));
      }));
  return rv;
}

IPCResult NativeStylePreloadProcessRootChildActor::RecvStop(
    const uint64_t& aRequestId, const uint64_t& aGeneration,
    const uint32_t& aSequence, const nsresult& aStatus) {
  return NS_SUCCEEDED(AcceptStop(aRequestId, aGeneration, aSequence, aStatus,
                                 false))
             ? IPC_OK()
             : IPC_FAIL_NO_REASON(this);
}

nsresult NativeStylePreloadProcessRootChildActor::AcceptStop(
    uint64_t aRequestId, uint64_t aGeneration, uint32_t aSequence,
    nsresult aStatus, bool aFromBackground) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mStarted || mStopReceived || mFinishedSent || aRequestId != mRequestId ||
      aGeneration != mGeneration || !mNextSequence ||
      aSequence == std::numeric_limits<uint32_t>::max() ||
      aSequence != mNextSequence || !mParser ||
      aFromBackground != mFullProcess ||
      (aFromBackground && !mBackgroundOnStart)) {
    return NS_ERROR_UNEXPECTED;
  }
  ++mNextSequence;
  mLastSequence = aSequence;
  mStopReceived = true;
  RuntimeLogEvent(
      "Native activation child phase=root-stop-accepted request=%llu "
      "generation=%llu sequence=%u status=0x%08x pid=%llu main_thread=1\n",
      static_cast<unsigned long long>(mRequestId),
      static_cast<unsigned long long>(mGeneration), aSequence,
      static_cast<unsigned>(aStatus),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  RefPtr self = this;
  RefPtr parser = mParser;
  nsresult rv = mBridge->mImpl->mParserThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::ActivationChildParserStop",
      [self, parser, aSequence, aStatus]() mutable {
        nsTArray<nsHtml5StylePreloadDescriptor> descriptors;
        nsresult status = aStatus;
        if (NS_SUCCEEDED(status)) {
          status = parser->Finish(descriptors);
        } else {
          parser->Cancel();
        }
        const uint32_t bodyBytes = parser->BodyBytes();
        RuntimeLogEvent(
            "Native activation child phase=parser-finish request=%llu "
            "generation=%llu sequence=%u bytes=%u descriptors=%u "
            "status=0x%08x pid=%llu main_thread=%d\n",
            static_cast<unsigned long long>(self->mRequestId),
            static_cast<unsigned long long>(self->mGeneration), aSequence,
            bodyBytes, static_cast<unsigned>(descriptors.Length()),
            static_cast<unsigned>(status),
            static_cast<unsigned long long>(base::GetCurrentProcId()),
            NS_IsMainThread());
        (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::ActivationChildParserStopComplete",
            [self, aSequence, status, bodyBytes,
             descriptors = std::move(descriptors)]() mutable {
              self->ParserFinishComplete(aSequence, status, bodyBytes,
                                         std::move(descriptors));
            }));
      }));
  return rv;
}

IPCResult NativeStylePreloadProcessRootChildActor::RecvCancel(
    const uint64_t& aRequestId, const uint64_t& aGeneration,
    const nsresult& aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (aRequestId != mRequestId || aGeneration != mGeneration ||
      mCancelReceived || NS_SUCCEEDED(aStatus)) {
    return IPC_FAIL_NO_REASON(this);
  }
  mCancelReceived = true;
  mFinishedSent = true;
  if (mParser) {
    RefPtr parser = mParser.forget();
    (void)mBridge->mImpl->mParserThread->Dispatch(
        NS_NewRunnableFunction("NaiveFox::ActivationChildParserCancel",
                               [parser] { parser->Cancel(); }));
  }
  if (!SendCanceled(mRequestId, mGeneration)) {
    return IPC_FAIL_NO_REASON(this);
  }
  return IPC_OK();
}

void NativeStylePreloadProcessRootChildActor::ParserChunkComplete(
    uint32_t aSequence, nsresult aStatus,
    nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mActorAlive || mFinishedSent) {
    return;
  }
  if (aSequence != mNextParserCompletionSequence) {
    Fail(NS_ERROR_UNEXPECTED);
    return;
  }
  ++mNextParserCompletionSequence;
  if (NS_FAILED(aStatus) ||
      NS_FAILED(mBridge->mImpl->CreateStyles(this, std::move(aDescriptors)))) {
    Fail(NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessRootChildActor::ParserFinishComplete(
    uint32_t aSequence, nsresult aStatus, uint32_t aBodyBytes,
    nsTArray<nsHtml5StylePreloadDescriptor>&& aDescriptors) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mActorAlive || mFinishedSent) {
    return;
  }
  if (!mStopReceived || aSequence != mLastSequence ||
      aSequence != mNextParserCompletionSequence) {
    Fail(NS_ERROR_UNEXPECTED);
    return;
  }
  ++mNextParserCompletionSequence;
  nsresult styleRv =
      mBridge->mImpl->CreateStyles(this, std::move(aDescriptors));
  if (NS_FAILED(styleRv) && NS_SUCCEEDED(aStatus)) {
    aStatus = styleRv;
  }
  mFinishedSent = true;
  mParser = nullptr;
  RuntimeLogEvent(
      "Native activation child phase=parser-finished request=%llu "
      "generation=%llu sequence=%u bytes=%u styles=%u status=0x%08x "
      "pid=%llu main_thread=1\n",
      static_cast<unsigned long long>(mRequestId),
      static_cast<unsigned long long>(mGeneration), mLastSequence, aBodyBytes,
      mStyleCount, static_cast<unsigned>(aStatus),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  if (!SendParserFinished(mRequestId, mGeneration, mLastSequence, aBodyBytes,
                          mStyleCount, aStatus)) {
    mBridge->ProcessActorDestroyed();
  }
}

void NativeStylePreloadProcessRootChildActor::Fail(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mFinishedSent) {
    return;
  }
  mFinishedSent = true;
  if (!SendParserFinished(mRequestId, mGeneration, mLastSequence,
                          mParser ? mParser->BodyBytes() : 0, mStyleCount,
                          NS_FAILED(aStatus) ? aStatus : NS_ERROR_UNEXPECTED)) {
    mBridge->ProcessActorDestroyed();
  }
}

void NativeStylePreloadProcessRootChildActor::PrepareForShutdown() {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mActorAlive) {
    return;
  }
  mActorAlive = false;
  mFinishedSent = true;
  if (mParser && mBridge->mImpl->mParserThread) {
    RefPtr parser = mParser.forget();
    (void)mBridge->mImpl->mParserThread->Dispatch(
        NS_NewRunnableFunction("NaiveFox::ActivationChildParserShutdown",
                               [parser] { parser->Cancel(); }));
  }
}

void NativeStylePreloadProcessRootChildActor::ActorDestroy(
    IProtocol::ActorDestroyReason aWhy) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mFinishedSent || (aWhy != Deletion && aWhy != NormalShutdown)) {
    mBridge->ProcessActorDestroyed();
  }
  RuntimeLogEvent(
      "Native activation child phase=root-actor-destroyed request=%llu "
      "generation=%llu finished=%d reason=%u pid=%llu\n",
      static_cast<unsigned long long>(mRequestId),
      static_cast<unsigned long long>(mGeneration), mFinishedSent,
      static_cast<unsigned>(aWhy),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  mActorAlive = false;
  if (mParser && mBridge->mImpl->mParserThread) {
    RefPtr parser = mParser.forget();
    (void)mBridge->mImpl->mParserThread->Dispatch(
        NS_NewRunnableFunction("NaiveFox::ActivationChildParserActorDestroy",
                               [parser] { parser->Cancel(); }));
  }
  (void)mBridge->DeallocRoot(this);
}

IPCResult NativeStylePreloadProcessStyleChildActor::RecvCompleteStyle(
    const uint64_t& aStyleRequestId, const uint64_t& aRootRequestId,
    const uint64_t& aRootGeneration, const nsresult&) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mCompleted || aStyleRequestId != mStyleRequestId ||
      aRootRequestId != mRootRequestId || aRootGeneration != mRootGeneration) {
    return IPC_FAIL_NO_REASON(this);
  }
  mCompleted = true;
  RuntimeLogEvent(
      "Native activation child phase=style-complete request=%llu "
      "root=%llu generation=%llu pid=%llu main_thread=1\n",
      static_cast<unsigned long long>(mStyleRequestId),
      static_cast<unsigned long long>(mRootRequestId),
      static_cast<unsigned long long>(mRootGeneration),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::DeleteCompletedActivationProcessStyle", [self] {
            (void)PNativeStylePreloadProcessStyleChild::Send__delete__(self);
          })))) {
    return IPC_FAIL_NO_REASON(this);
  }
  return IPC_OK();
}

void NativeStylePreloadProcessStyleChildActor::ActorDestroy(
    IProtocol::ActorDestroyReason aWhy) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mCompleted || (aWhy != Deletion && aWhy != NormalShutdown)) {
    mBridge->ProcessActorDestroyed();
  }
  RuntimeLogEvent(
      "Native activation child phase=style-actor-destroyed request=%llu "
      "root=%llu generation=%llu completed=%d reason=%u pid=%llu\n",
      static_cast<unsigned long long>(mStyleRequestId),
      static_cast<unsigned long long>(mRootRequestId),
      static_cast<unsigned long long>(mRootGeneration), mCompleted,
      static_cast<unsigned>(aWhy),
      static_cast<unsigned long long>(base::GetCurrentProcId()));
  (void)mBridge->DeallocStyle(this);
}

}  // namespace mozilla::naivefox
