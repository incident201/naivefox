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
#include "mozilla/ipc/Endpoint.h"
#include "mozilla/ipc/IOThread.h"
#include "mozilla/ipc/ProtocolUtils.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationChild.h"
#include "mozilla/naivefox/PNativeStylePreloadActivationParent.h"
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

class ActivationState;
class ActivationChild;
class ActivationParent;

StaticRefPtr<ActivationState> sActivationState;

NativeStylePreloadActivationArgs SerializeDescriptor(
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

bool DescriptorMatches(const NativeStylePreloadActivationArgs& aArgs,
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

NativeStylePreloadActivationDescriptor DeserializeDescriptor(
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

class ActivationState final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ActivationState)

  struct Entry final {
    NativeStylePreloadActivationDescriptor mDescriptor;
    NativeStylePreloadPrimaryCallback mPrimaryCallback;
    NativeStylePreloadFinalCallback mFinalCallback;
    bool mPrimaryReady = false;
    bool mBackgroundReady = false;
  };

  nsresult Initialize();
  bool IsReady() const;
  void Shutdown();
  nsresult RegisterAndDispatch(
      NativeStylePreloadActivationDescriptor&& aDescriptor,
      NativeStylePreloadPrimaryCallback&& aPrimaryCallback,
      NativeStylePreloadFinalCallback&& aFinalCallback, uint64_t& aRequestId);
  void Cancel(uint64_t aRequestId);
  void RecvWarmupAck(ActivationLeg aLeg);
  void RecvPrimaryOpen(NativeStylePreloadActivationArgs&& aArgs);
  void RecvBackgroundReady(uint64_t aRequestId);
  void ActorFailed(ActivationLeg aLeg);
  void Complete(uint64_t aRequestId, nsresult aStatus);

 private:
  ~ActivationState() = default;

  void MaybeActivate(uint64_t aRequestId);
  void FailAll(nsresult aStatus);
  bool AllBoundActorsDestroyed() const;

  nsTHashMap<nsUint64HashKey, Entry> mEntries;
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

void DispatchActorFailure(ActivationLeg aLeg) {
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NativeStyleActivationActorFailed", [aLeg]() {
        if (sActivationState) {
          sActivationState->ActorFailed(aLeg);
        }
      }));
}

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

  IPCResult RecvPrimaryOpen(
      const NativeStylePreloadActivationArgs& aArgs) final {
    if (mLeg != ActivationLeg::Primary || !NS_IsMainThread()) {
      return IPC_FAIL_NO_REASON(this);
    }
    if (sActivationState) {
      sActivationState->RecvPrimaryOpen(
          NativeStylePreloadActivationArgs(aArgs));
    }
    return IPC_OK();
  }

  IPCResult RecvBackgroundReady(const uint64_t& aRequestId) final {
    if (mLeg != ActivationLeg::Background || NS_IsMainThread()) {
      return IPC_FAIL_NO_REASON(this);
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeStyleActivationBackgroundReady", [aRequestId]() {
          if (sActivationState) {
            sActivationState->RecvBackgroundReady(aRequestId);
          }
        }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    mDestroyed.store(true);
    DispatchActorFailure(mLeg);
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
    if (mLeg == ActivationLeg::Primary) {
      if (!NS_IsMainThread()) {
        return IPC_FAIL_NO_REASON(this);
      }
      if (sActivationState) {
        sActivationState->RecvWarmupAck(mLeg);
      }
      return IPC_OK();
    }
    nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::NativeStyleActivationBackgroundWarm", [leg = mLeg]() {
          if (sActivationState) {
            sActivationState->RecvWarmupAck(leg);
          }
        }));
    return NS_SUCCEEDED(rv) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  void ActorDestroy(IProtocol::ActorDestroyReason) final {
    mDestroyed.store(true);
    DispatchActorFailure(mLeg);
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
      "NaiveFox::BindNativeStyleActivationBackgroundParent",
      [actor = std::move(backgroundParent),
       state = std::move(backgroundParentState),
       endpoint = std::move(backgroundParentEndpoint)]() mutable {
        if (!endpoint.Bind(actor)) {
          DispatchActorFailure(ActivationLeg::Background);
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
      "NaiveFox::BindNativeStyleActivationBackgroundChild",
      [actor = std::move(backgroundChild),
       state = std::move(backgroundChildState),
       endpoint = std::move(backgroundChildEndpoint)]() mutable {
        if (!endpoint.Bind(actor)) {
          DispatchActorFailure(ActivationLeg::Background);
        } else {
          actor->MarkBound();
          if (!actor->SendWarmup(
                  static_cast<uint8_t>(ActivationLeg::Background))) {
            DispatchActorFailure(ActivationLeg::Background);
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
  RuntimeLogEvent("Native style activation phase=%s-ready\n",
                  aLeg == ActivationLeg::Primary ? "primary" : "background");
  if (IsReady()) {
    RuntimeLogEvent("Native style activation phase=bridge-ready\n");
  }
}

nsresult ActivationState::RegisterAndDispatch(
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
  if (mNextRequestId == 0) {
    return NS_ERROR_OUT_OF_MEMORY;
  }
  const uint64_t requestId = mNextRequestId++;
  auto descriptorArgs = SerializeDescriptor(requestId, aDescriptor);
  Entry entry{std::move(aDescriptor), std::move(aPrimaryCallback),
              std::move(aFinalCallback)};
  mEntries.InsertOrUpdate(requestId, std::move(entry));
  aRequestId = requestId;
  RuntimeLogEvent(
      "Native style activation phase=descriptor-frozen request=%llu\n",
      static_cast<unsigned long long>(requestId));

  if (!mPrimaryChild->SendPrimaryOpen(std::move(descriptorArgs))) {
    Complete(requestId, NS_ERROR_FAILURE);
    aRequestId = 0;
    return NS_ERROR_FAILURE;
  }
  RuntimeLogEvent(
      "Native style activation phase=child-open-sent request=%llu\n",
      static_cast<unsigned long long>(requestId));

  RefPtr<ActivationChild> backgroundChild = mBackgroundChild;
  nsresult rv = mSocketTarget->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::NativeStyleActivationStartBackground",
      [actor = std::move(backgroundChild), requestId]() {
        if (!actor->SendBackgroundReady(requestId)) {
          (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
              "NaiveFox::NativeStyleActivationBackgroundSendFailed",
              [requestId]() {
                if (sActivationState) {
                  sActivationState->Complete(requestId, NS_ERROR_FAILURE);
                }
              }));
          return;
        }
        RuntimeLogEvent(
            "Native style activation phase=bg-ready-sent request=%llu\n",
            static_cast<unsigned long long>(requestId));
      }));
  if (NS_FAILED(rv)) {
    Complete(requestId, rv);
    aRequestId = 0;
    return rv;
  }
  RuntimeLogEvent(
      "Native style activation phase=background-dispatched request=%llu\n",
      static_cast<unsigned long long>(requestId));
  return NS_OK;
}

void ActivationState::RecvPrimaryOpen(
    NativeStylePreloadActivationArgs&& aArgs) {
  MOZ_ASSERT(NS_IsMainThread());
  Entry* entry = mEntries.Lookup(aArgs.requestId()).DataPtrOrNull();
  if (!entry) {
    return;
  }
  if (entry->mPrimaryReady || !DescriptorMatches(aArgs, entry->mDescriptor)) {
    Complete(aArgs.requestId(), NS_ERROR_UNEXPECTED);
    return;
  }
  NativeStylePreloadPrimaryCallback callback =
      std::move(entry->mPrimaryCallback);
  NativeStylePreloadActivationDescriptor receivedDescriptor =
      DeserializeDescriptor(aArgs);
  nsresult rv = callback(receivedDescriptor);
  entry = mEntries.Lookup(aArgs.requestId()).DataPtrOrNull();
  if (!entry) {
    return;
  }
  if (NS_FAILED(rv)) {
    Complete(aArgs.requestId(), rv);
    return;
  }
  entry->mPrimaryReady = true;
  RuntimeLogEvent(
      "Native style activation phase=parent-channel-created request=%llu\n",
      static_cast<unsigned long long>(aArgs.requestId()));
  MaybeActivate(aArgs.requestId());
}

void ActivationState::RecvBackgroundReady(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  Entry* entry = mEntries.Lookup(aRequestId).DataPtrOrNull();
  if (!entry) {
    return;
  }
  if (entry->mBackgroundReady) {
    Complete(aRequestId, NS_ERROR_UNEXPECTED);
    return;
  }
  entry->mBackgroundReady = true;
  RuntimeLogEvent(
      "Native style activation phase=background-ready-received request=%llu\n",
      static_cast<unsigned long long>(aRequestId));
  MaybeActivate(aRequestId);
}

void ActivationState::MaybeActivate(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  Entry* entry = mEntries.Lookup(aRequestId).DataPtrOrNull();
  if (entry && entry->mPrimaryReady && entry->mBackgroundReady) {
    Complete(aRequestId, NS_OK);
  }
}

void ActivationState::Complete(uint64_t aRequestId, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  Entry entry;
  if (!mEntries.Remove(aRequestId, &entry)) {
    return;
  }
  RuntimeLogEvent(
      "Native style activation phase=%s request=%llu status=0x%08x\n",
      NS_SUCCEEDED(aStatus) ? "activation-released" : "request-failed",
      static_cast<unsigned long long>(aRequestId),
      static_cast<unsigned>(aStatus));
  nsresult callbackRv = entry.mFinalCallback(aStatus);
  if (NS_SUCCEEDED(aStatus) && NS_FAILED(callbackRv)) {
    RuntimeLogEvent(
        "Native style activation phase=activation-callback-failed "
        "request=%llu status=0x%08x\n",
        static_cast<unsigned long long>(aRequestId),
        static_cast<unsigned>(callbackRv));
  } else if (NS_SUCCEEDED(aStatus)) {
    RuntimeLogEvent("Native style activation phase=async-open request=%llu\n",
                    static_cast<unsigned long long>(aRequestId));
  }
}

void ActivationState::Cancel(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!aRequestId) {
    return;
  }
  Entry entry;
  if (mEntries.Remove(aRequestId, &entry)) {
    RuntimeLogEvent(
        "Native style activation phase=request-cancelled request=%llu\n",
        static_cast<unsigned long long>(aRequestId));
  }
}

void ActivationState::ActorFailed(ActivationLeg aLeg) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mFailed || mShuttingDown) {
    return;
  }
  mFailed = true;
  RuntimeLogEvent("Native style activation phase=actor-failed leg=%s\n",
                  aLeg == ActivationLeg::Primary ? "primary" : "background");
  FailAll(NS_ERROR_FAILURE);
}

void ActivationState::FailAll(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  nsTArray<uint64_t> requestIds;
  for (auto iter = mEntries.ConstIter(); !iter.Done(); iter.Next()) {
    requestIds.AppendElement(iter.Key());
  }
  for (uint64_t requestId : requestIds) {
    Complete(requestId, aStatus);
  }
}

bool ActivationState::AllBoundActorsDestroyed() const {
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

  MOZ_ALWAYS_TRUE(
      SpinEventLoopUntil("NativeStyleActivation::DrainSetup"_ns, [this]() {
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
        "NaiveFox::CloseNativeStyleActivationBackgroundChild",
        [actor = std::move(actor)]() { actor->Close(); })));
  } else if (mBackgroundParent && mBackgroundParent->WasBound() &&
             !mBackgroundParent->WasDestroyed() && mBackgroundThread) {
    RefPtr<ActivationParent> actor = mBackgroundParent;
    MOZ_ALWAYS_SUCCEEDS(mBackgroundThread->Dispatch(NS_NewRunnableFunction(
        "NaiveFox::CloseNativeStyleActivationBackgroundParent",
        [actor = std::move(actor)]() { actor->Close(); })));
  }

  MOZ_ALWAYS_TRUE(
      SpinEventLoopUntil("NativeStyleActivation::DrainActors"_ns,
                         [this]() { return AllBoundActorsDestroyed(); }));
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
  return sActivationState->RegisterAndDispatch(
      std::move(aDescriptor), std::move(aPrimaryCallback),
      std::move(aFinalCallback), aRequestId);
}

void NativeStylePreloadActivation::Cancel(uint64_t aRequestId) {
  MOZ_ASSERT(NS_IsMainThread());
  if (sActivationState) {
    sActivationState->Cancel(aRequestId);
  }
}

}  // namespace mozilla::naivefox
