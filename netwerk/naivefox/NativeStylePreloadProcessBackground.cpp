/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NativeStylePreloadProcessBackground.h"

#include <atomic>
#include <utility>

#include "RuntimeLogging.h"
#include "base/process.h"
#include "mozilla/MoveOnlyFunction.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessBackgroundChild.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessBackgroundParent.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessBackgroundRequestChild.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessBackgroundRequestParent.h"
#include "nsHashKeys.h"
#include "nsIThread.h"
#include "nsTHashMap.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

using ipc::IPCResult;

namespace {

bool IsValidIdentity(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity) {
  if (!aIdentity.mRootRequestId || !aIdentity.mRootGeneration) {
    return false;
  }
  switch (aIdentity.mKind) {
    case NativeStylePreloadProcessBackgroundKind::Root:
      return !aIdentity.mStyleRequestId && !aIdentity.mDiscoverySequence;
    case NativeStylePreloadProcessBackgroundKind::Style:
      return aIdentity.mStyleRequestId && aIdentity.mDiscoverySequence;
  }
  return false;
}

uint64_t IdentityKey(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity) {
  return aIdentity.mKind == NativeStylePreloadProcessBackgroundKind::Root
             ? aIdentity.mRootRequestId
             : aIdentity.mStyleRequestId;
}

bool IdentityEquals(
    const NativeStylePreloadProcessBackgroundIdentity& aLeft,
    const NativeStylePreloadProcessBackgroundIdentity& aRight) {
  return aLeft.mKind == aRight.mKind &&
         aLeft.mRootRequestId == aRight.mRootRequestId &&
         aLeft.mRootGeneration == aRight.mRootGeneration &&
         aLeft.mStyleRequestId == aRight.mStyleRequestId &&
         aLeft.mDiscoverySequence == aRight.mDiscoverySequence;
}

NativeStylePreloadProcessBackgroundIdentity MakeIdentity(
    uint8_t aKind, uint64_t aRootRequestId, uint64_t aRootGeneration,
    uint64_t aStyleRequestId, uint32_t aDiscoverySequence) {
  NativeStylePreloadProcessBackgroundIdentity identity;
  identity.mKind =
      static_cast<NativeStylePreloadProcessBackgroundKind>(aKind);
  identity.mRootRequestId = aRootRequestId;
  identity.mRootGeneration = aRootGeneration;
  identity.mStyleRequestId = aStyleRequestId;
  identity.mDiscoverySequence = aDiscoverySequence;
  return identity;
}

}  // namespace

class NativeStylePreloadProcessBackgroundRequestParentActor final
    : public PNativeStylePreloadProcessBackgroundRequestParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessBackgroundRequestParentActor, override)

  NativeStylePreloadProcessBackgroundRequestParentActor(
      NativeStylePreloadProcessBackgroundParent* aOwner,
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity)
      : mOwner(aOwner), mIdentity(aIdentity) {}

  bool Matches(const NativeStylePreloadProcessBackgroundIdentity& aIdentity)
      const {
    return IdentityEquals(mIdentity, aIdentity);
  }

  bool MatchesRoot(uint64_t aRootRequestId, uint64_t aRootGeneration) const {
    return mIdentity.mKind == NativeStylePreloadProcessBackgroundKind::Root &&
           mIdentity.mRootRequestId == aRootRequestId &&
           mIdentity.mRootGeneration == aRootGeneration;
  }

  const NativeStylePreloadProcessBackgroundIdentity& Identity() const {
    return mIdentity;
  }

  nsresult Complete(nsresult aStatus) {
    if (mCompleted || !CanSend() ||
        !SendComplete(static_cast<uint8_t>(mIdentity.mKind),
                      mIdentity.mRootRequestId, mIdentity.mRootGeneration,
                      mIdentity.mStyleRequestId,
                      mIdentity.mDiscoverySequence, aStatus)) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    mCompleted = true;
    return NS_OK;
  }

  nsresult ForwardOnStart() {
    if (mIdentity.mKind != NativeStylePreloadProcessBackgroundKind::Root ||
        !mReady || mRootOnStartSent || !CanSend() ||
        !SendForwardRootOnStart(mIdentity.mRootRequestId,
                                mIdentity.mRootGeneration)) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    mRootOnStartSent = true;
    return NS_OK;
  }

  nsresult ForwardData(uint32_t aSequence, const nsACString& aData) {
    if (!mRootOnStartForwarded || mRootStopSent || !aSequence ||
        aSequence != mNextRootSequence || !CanSend() ||
        !SendForwardRootData(mIdentity.mRootRequestId,
                             mIdentity.mRootGeneration, aSequence, aData)) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    ++mNextRootSequence;
    return NS_OK;
  }

  nsresult ForwardStop(uint32_t aSequence, nsresult aStatus) {
    if (!mRootOnStartForwarded || mRootStopSent || !aSequence ||
        aSequence != mNextRootSequence || !CanSend() ||
        !SendForwardRootStop(mIdentity.mRootRequestId,
                             mIdentity.mRootGeneration, aSequence, aStatus)) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    ++mNextRootSequence;
    mRootStopSent = true;
    return NS_OK;
  }

 private:
  ~NativeStylePreloadProcessBackgroundRequestParentActor() = default;

  IPCResult RecvReady(const uint8_t& aKind, const uint64_t& aRootRequestId,
                      const uint64_t& aRootGeneration,
                      const uint64_t& aStyleRequestId,
                      const uint32_t& aDiscoverySequence) final {
    const auto received =
        MakeIdentity(aKind, aRootRequestId, aRootGeneration, aStyleRequestId,
                     aDiscoverySequence);
    if (mReady || !IdentityEquals(received, mIdentity)) {
      return IPC_FAIL_NO_REASON(this);
    }
    mReady = true;
    mOwner->RequestReady(mIdentity);
    return IPC_OK();
  }

  IPCResult RecvRootOnStartForwarded(
      const uint64_t& aRootRequestId,
      const uint64_t& aRootGeneration) final {
    if (mIdentity.mKind != NativeStylePreloadProcessBackgroundKind::Root ||
        !mRootOnStartSent || mRootOnStartForwarded ||
        aRootRequestId != mIdentity.mRootRequestId ||
        aRootGeneration != mIdentity.mRootGeneration) {
      return IPC_FAIL_NO_REASON(this);
    }
    mRootOnStartForwarded = true;
    mOwner->RootOnStartForwarded(mIdentity);
    return IPC_OK();
  }

  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final {
    const bool cleanDelete = aWhy == Deletion || aWhy == NormalShutdown;
    const bool cleanShutdown = mOwner->IsShuttingDownOnBackground() &&
                               (cleanDelete || aWhy == AncestorDeletion);
    mOwner->RequestDestroyed(mIdentity,
                             (mCompleted && cleanDelete) || cleanShutdown);
  }

  const RefPtr<NativeStylePreloadProcessBackgroundParent> mOwner;
  const NativeStylePreloadProcessBackgroundIdentity mIdentity;
  bool mReady = false;
  bool mCompleted = false;
  bool mRootOnStartSent = false;
  bool mRootOnStartForwarded = false;
  bool mRootStopSent = false;
  uint32_t mNextRootSequence = 1;
};

class NativeStylePreloadProcessBackgroundRequestChildActor final
    : public PNativeStylePreloadProcessBackgroundRequestChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessBackgroundRequestChildActor, override)

  NativeStylePreloadProcessBackgroundRequestChildActor(
      NativeStylePreloadProcessBackgroundChild* aOwner,
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity)
      : mOwner(aOwner), mIdentity(aIdentity) {}

  bool SendReadyMessage() {
    return SendReady(static_cast<uint8_t>(mIdentity.mKind),
                     mIdentity.mRootRequestId, mIdentity.mRootGeneration,
                     mIdentity.mStyleRequestId, mIdentity.mDiscoverySequence);
  }

 private:
  ~NativeStylePreloadProcessBackgroundRequestChildActor() = default;

  IPCResult RecvComplete(const uint8_t& aKind,
                         const uint64_t& aRootRequestId,
                         const uint64_t& aRootGeneration,
                         const uint64_t& aStyleRequestId,
                         const uint32_t& aDiscoverySequence,
                         const nsresult&) final {
    const auto received =
        MakeIdentity(aKind, aRootRequestId, aRootGeneration, aStyleRequestId,
                     aDiscoverySequence);
    if (mCompleted || !IdentityEquals(received, mIdentity)) {
      return IPC_FAIL_NO_REASON(this);
    }
    mCompleted = true;
    return Send__delete__(this) ? IPC_OK() : IPC_FAIL_NO_REASON(this);
  }

  IPCResult RecvForwardRootOnStart(
      const uint64_t& aRootRequestId,
      const uint64_t& aRootGeneration) final {
    if (mIdentity.mKind != NativeStylePreloadProcessBackgroundKind::Root ||
        mRootOnStartReceived || aRootRequestId != mIdentity.mRootRequestId ||
        aRootGeneration != mIdentity.mRootGeneration) {
      return IPC_FAIL_NO_REASON(this);
    }
    mRootOnStartReceived = true;
    mOwner->ForwardRootOnStart(this, mIdentity);
    return IPC_OK();
  }

  IPCResult RecvForwardRootData(const uint64_t& aRootRequestId,
                                const uint64_t& aRootGeneration,
                                const uint32_t& aSequence,
                                const nsACString& aData) final {
    if (!mRootOnStartReceived || mRootStopReceived || !aSequence ||
        aSequence != mNextRootSequence ||
        aRootRequestId != mIdentity.mRootRequestId ||
        aRootGeneration != mIdentity.mRootGeneration) {
      return IPC_FAIL_NO_REASON(this);
    }
    ++mNextRootSequence;
    mOwner->ForwardRootData(mIdentity, aSequence, nsCString(aData));
    return IPC_OK();
  }

  IPCResult RecvForwardRootStop(const uint64_t& aRootRequestId,
                                const uint64_t& aRootGeneration,
                                const uint32_t& aSequence,
                                const nsresult& aStatus) final {
    if (!mRootOnStartReceived || mRootStopReceived || !aSequence ||
        aSequence != mNextRootSequence ||
        aRootRequestId != mIdentity.mRootRequestId ||
        aRootGeneration != mIdentity.mRootGeneration) {
      return IPC_FAIL_NO_REASON(this);
    }
    ++mNextRootSequence;
    mRootStopReceived = true;
    mOwner->ForwardRootStop(mIdentity, aSequence, aStatus);
    return IPC_OK();
  }

  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final {
    const bool cleanDelete = aWhy == Deletion || aWhy == NormalShutdown;
    const bool cleanShutdown = mOwner->IsShuttingDownOnBackground() &&
                               (cleanDelete || aWhy == AncestorDeletion);
    mOwner->RequestDestroyed(mIdentity,
                             (mCompleted && cleanDelete) || cleanShutdown);
  }

  const RefPtr<NativeStylePreloadProcessBackgroundChild> mOwner;
  const NativeStylePreloadProcessBackgroundIdentity mIdentity;
  bool mCompleted = false;
  bool mRootOnStartReceived = false;
  bool mRootStopReceived = false;
  uint32_t mNextRootSequence = 1;
};

class NativeStylePreloadProcessBackgroundParentActor final
    : public PNativeStylePreloadProcessBackgroundParent {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessBackgroundParentActor, override)

  explicit NativeStylePreloadProcessBackgroundParentActor(
      NativeStylePreloadProcessBackgroundParent* aOwner)
      : mOwner(aOwner) {}

  nsresult BeginShutdown() {
    if (mShutdownSent || !mHelloReceived || !CanSend() || !SendShutdown()) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    mShutdownSent = true;
    return NS_OK;
  }

 private:
  ~NativeStylePreloadProcessBackgroundParentActor() = default;

  IPCResult RecvHello(const uint64_t& aChildPid,
                      const uint64_t& aObservedParentPid) final;
  IPCResult RecvShutdownComplete() final {
    if (mShutdownComplete || !mShutdownSent) {
      return IPC_FAIL_NO_REASON(this);
    }
    mShutdownComplete = true;
    Close();
    return IPC_OK();
  }

  already_AddRefed<PNativeStylePreloadProcessBackgroundRequestParent>
  AllocPNativeStylePreloadProcessBackgroundRequestParent(
      const uint8_t& aKind, const uint64_t& aRootRequestId,
      const uint64_t& aRootGeneration, const uint64_t& aStyleRequestId,
      const uint32_t& aDiscoverySequence) final;

  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final {
    mOwner->ManagerDestroyed(
        mShutdownSent && mShutdownComplete &&
        (aWhy == Deletion || aWhy == NormalShutdown));
  }

  const RefPtr<NativeStylePreloadProcessBackgroundParent> mOwner;
  bool mHelloReceived = false;
  bool mShutdownSent = false;
  bool mShutdownComplete = false;
};

class NativeStylePreloadProcessBackgroundChildActor final
    : public PNativeStylePreloadProcessBackgroundChild {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessBackgroundChildActor, override)

  explicit NativeStylePreloadProcessBackgroundChildActor(
      NativeStylePreloadProcessBackgroundChild* aOwner)
      : mOwner(aOwner) {}

  bool Start(uint64_t aChildPid, uint64_t aParentPid) {
    if (!SendHello(aChildPid, aParentPid)) {
      return false;
    }
    mHelloSent = true;
    return true;
  }

 private:
  ~NativeStylePreloadProcessBackgroundChildActor() = default;

  IPCResult RecvShutdown() final {
    if (mShutdownReceived || !mHelloSent || !SendShutdownComplete()) {
      return IPC_FAIL_NO_REASON(this);
    }
    mShutdownReceived = true;
    mOwner->MarkShuttingDownOnBackground();
    Close();
    return IPC_OK();
  }

  void ActorDestroy(IProtocol::ActorDestroyReason aWhy) final {
    mOwner->ManagerDestroyed(
        mShutdownReceived &&
        (aWhy == Deletion || aWhy == NormalShutdown));
  }

  const RefPtr<NativeStylePreloadProcessBackgroundChild> mOwner;
  bool mHelloSent = false;
  bool mShutdownReceived = false;
};

class NativeStylePreloadProcessBackgroundParent::Impl final {
 public:
  Impl(uint64_t aExpectedParentPid, uint64_t aExpectedChildPid,
       Callbacks&& aCallbacks)
      : mExpectedParentPid(aExpectedParentPid),
        mExpectedChildPid(aExpectedChildPid),
        mCallbacks(std::move(aCallbacks)) {}

  uint64_t mExpectedParentPid;
  uint64_t mExpectedChildPid;
  Callbacks mCallbacks;
  nsCOMPtr<nsIThread> mThread;
  RefPtr<NativeStylePreloadProcessBackgroundParentActor> mActor;
  nsTHashMap<nsUint64HashKey,
             RefPtr<NativeStylePreloadProcessBackgroundRequestParentActor>>
      mRootRequests;
  nsTHashMap<nsUint64HashKey,
             RefPtr<NativeStylePreloadProcessBackgroundRequestParentActor>>
      mStyleRequests;
  nsTHashMap<nsUint64HashKey, uint64_t> mDrainingRoots;
  std::atomic<bool> mReady{false};
  std::atomic<bool> mFailed{false};
  std::atomic<bool> mShuttingDown{false};
};

class NativeStylePreloadProcessBackgroundChild::Impl final {
 public:
  Impl(uint64_t aExpectedParentPid, uint64_t aExpectedChildPid,
       Callbacks&& aCallbacks)
      : mExpectedParentPid(aExpectedParentPid),
        mExpectedChildPid(aExpectedChildPid),
        mCallbacks(std::move(aCallbacks)) {}

  uint64_t mExpectedParentPid;
  uint64_t mExpectedChildPid;
  Callbacks mCallbacks;
  nsCOMPtr<nsIThread> mThread;
  RefPtr<NativeStylePreloadProcessBackgroundChildActor> mActor;
  nsTHashMap<nsUint64HashKey,
             RefPtr<NativeStylePreloadProcessBackgroundRequestChildActor>>
      mRootRequests;
  nsTHashMap<nsUint64HashKey,
             RefPtr<NativeStylePreloadProcessBackgroundRequestChildActor>>
      mStyleRequests;
  std::atomic<bool> mReady{false};
  std::atomic<bool> mFailed{false};
  std::atomic<bool> mShuttingDown{false};
};

NativeStylePreloadProcessBackgroundParent::
    NativeStylePreloadProcessBackgroundParent(uint64_t aExpectedParentPid,
                                               uint64_t aExpectedChildPid,
                                               Callbacks&& aCallbacks)
    : mImpl(MakeUnique<Impl>(aExpectedParentPid, aExpectedChildPid,
                             std::move(aCallbacks))) {}

NativeStylePreloadProcessBackgroundParent::
    ~NativeStylePreloadProcessBackgroundParent() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mThread) {
    (void)mImpl->mThread->Shutdown();
  }
}

nsresult NativeStylePreloadProcessBackgroundParent::Create(
    ipc::EndpointProcInfo aParentProcInfo,
    ipc::EndpointProcInfo aChildProcInfo, uint64_t aExpectedParentPid,
    uint64_t aExpectedChildPid, Callbacks&& aCallbacks,
    RefPtr<NativeStylePreloadProcessBackgroundParent>& aParent,
    ipc::Endpoint<PNativeStylePreloadProcessBackgroundChild>& aChildEndpoint) {
  MOZ_ASSERT(NS_IsMainThread());
  aParent = nullptr;
  ipc::Endpoint<PNativeStylePreloadProcessBackgroundParent> parentEndpoint;
  MOZ_TRY(PNativeStylePreloadProcessBackground::CreateEndpoints(
      aParentProcInfo, aChildProcInfo, &parentEndpoint, &aChildEndpoint));
  RefPtr parent = new NativeStylePreloadProcessBackgroundParent(
      aExpectedParentPid, aExpectedChildPid, std::move(aCallbacks));
  MOZ_TRY(parent->Bind(std::move(parentEndpoint)));
  aParent = std::move(parent);
  return NS_OK;
}

nsresult NativeStylePreloadProcessBackgroundParent::Bind(
    ipc::Endpoint<PNativeStylePreloadProcessBackgroundParent>&& aEndpoint) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_TRY(NS_NewNamedThread("NF ActProc BG", getter_AddRefs(mImpl->mThread)));
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::BindActivationProcessBackgroundParent",
      [self = std::move(self), endpoint = std::move(aEndpoint)]() mutable {
        RefPtr actor =
            new NativeStylePreloadProcessBackgroundParentActor(self);
        if (!endpoint.Bind(actor)) {
          self->Fail(NS_ERROR_FAILURE);
          return;
        }
        self->mImpl->mActor = std::move(actor);
      }));
}

bool NativeStylePreloadProcessBackgroundParent::IsReady() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mImpl->mReady && !mImpl->mFailed && !mImpl->mShuttingDown;
}

bool NativeStylePreloadProcessBackgroundParent::IsShuttingDownOnBackground()
    const {
  return mImpl->mShuttingDown;
}

IPCResult NativeStylePreloadProcessBackgroundParentActor::RecvHello(
    const uint64_t& aChildPid, const uint64_t& aObservedParentPid) {
  if (mHelloReceived || aChildPid != mOwner->mImpl->mExpectedChildPid ||
      aObservedParentPid != mOwner->mImpl->mExpectedParentPid ||
      uint64_t(OtherPid()) != aChildPid) {
    return IPC_FAIL_NO_REASON(this);
  }
  mHelloReceived = true;
  mOwner->ManagerReady();
  return IPC_OK();
}

already_AddRefed<PNativeStylePreloadProcessBackgroundRequestParent>
NativeStylePreloadProcessBackgroundParentActor::
    AllocPNativeStylePreloadProcessBackgroundRequestParent(
        const uint8_t& aKind, const uint64_t& aRootRequestId,
        const uint64_t& aRootGeneration, const uint64_t& aStyleRequestId,
        const uint32_t& aDiscoverySequence) {
  const auto identity =
      MakeIdentity(aKind, aRootRequestId, aRootGeneration, aStyleRequestId,
                   aDiscoverySequence);
  if (!IsValidIdentity(identity) || !mHelloReceived || mShutdownSent) {
    return nullptr;
  }
  auto& requests =
      identity.mKind == NativeStylePreloadProcessBackgroundKind::Root
          ? mOwner->mImpl->mRootRequests
          : mOwner->mImpl->mStyleRequests;
  const uint64_t key = IdentityKey(identity);
  if (requests.Contains(key)) {
    return nullptr;
  }
  RefPtr actor = new NativeStylePreloadProcessBackgroundRequestParentActor(
      mOwner, identity);
  requests.InsertOrUpdate(key, actor);
  return actor.forget();
}

void NativeStylePreloadProcessBackgroundParent::ManagerReady() {
  if (mImpl->mReady.exchange(true) || mImpl->mFailed ||
      mImpl->mShuttingDown) {
    Fail(NS_ERROR_UNEXPECTED);
    return;
  }
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundReady",
          [self = std::move(self)]() {
            if (self->IsReady() && self->mImpl->mCallbacks.mReady) {
              self->mImpl->mCallbacks.mReady();
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundParent::RequestReady(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity) {
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundRequestReady",
          [self = std::move(self), aIdentity]() {
            if (!self->IsReady()) {
              return;
            }
            nsresult rv = NS_ERROR_NOT_AVAILABLE;
            if (aIdentity.mKind ==
                    NativeStylePreloadProcessBackgroundKind::Root &&
                self->mImpl->mCallbacks.mRootReady) {
              rv = self->mImpl->mCallbacks.mRootReady(
                  aIdentity.mRootRequestId, aIdentity.mRootGeneration);
            } else if (aIdentity.mKind ==
                           NativeStylePreloadProcessBackgroundKind::Style &&
                       self->mImpl->mCallbacks.mStyleReady) {
              rv = self->mImpl->mCallbacks.mStyleReady(
                  aIdentity.mRootRequestId, aIdentity.mRootGeneration,
                  aIdentity.mStyleRequestId, aIdentity.mDiscoverySequence);
            }
            if (NS_FAILED(rv)) {
              self->Fail(rv);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundParent::RootOnStartForwarded(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity) {
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundRootOnStartForwarded",
          [self = std::move(self), aIdentity]() {
            if (!self->IsReady() ||
                !self->mImpl->mCallbacks.mRootOnStartForwarded ||
                NS_FAILED(self->mImpl->mCallbacks.mRootOnStartForwarded(
                    aIdentity.mRootRequestId,
                    aIdentity.mRootGeneration))) {
              self->Fail(NS_ERROR_FAILURE);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundParent::RequestDestroyed(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
    bool aExpected) {
  auto& requests =
      aIdentity.mKind == NativeStylePreloadProcessBackgroundKind::Root
          ? mImpl->mRootRequests
          : mImpl->mStyleRequests;
  requests.Remove(IdentityKey(aIdentity));
  if (!aExpected) {
    Fail(NS_ERROR_FAILURE);
    return;
  }
  if (aIdentity.mKind == NativeStylePreloadProcessBackgroundKind::Root) {
    mImpl->mDrainingRoots.InsertOrUpdate(aIdentity.mRootRequestId,
                                         aIdentity.mRootGeneration);
  }
  MaybeNotifyRootDrained(aIdentity.mRootRequestId,
                         aIdentity.mRootGeneration);
}

void NativeStylePreloadProcessBackgroundParent::MaybeNotifyRootDrained(
    uint64_t aRootRequestId, uint64_t aRootGeneration) {
  auto* draining =
      mImpl->mDrainingRoots.Lookup(aRootRequestId).DataPtrOrNull();
  if (!draining || *draining != aRootGeneration) {
    return;
  }
  for (auto iter = mImpl->mStyleRequests.ConstIter(); !iter.Done();
       iter.Next()) {
    const auto& identity = iter.Data()->Identity();
    if (identity.mRootRequestId == aRootRequestId &&
        identity.mRootGeneration == aRootGeneration) {
      return;
    }
  }
  mImpl->mDrainingRoots.Remove(aRootRequestId);
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundRootDrained",
          [self = std::move(self), aRootRequestId, aRootGeneration]() {
            if (self->mImpl->mCallbacks.mRootDrained) {
              self->mImpl->mCallbacks.mRootDrained(aRootRequestId,
                                                   aRootGeneration);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

nsresult NativeStylePreloadProcessBackgroundParent::CompleteRoot(
    uint64_t aRootRequestId, uint64_t aRootGeneration, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  NativeStylePreloadProcessBackgroundIdentity identity;
  identity.mKind = NativeStylePreloadProcessBackgroundKind::Root;
  identity.mRootRequestId = aRootRequestId;
  identity.mRootGeneration = aRootGeneration;
  if (!IsReady() || !IsValidIdentity(identity)) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::CompleteActivationProcessBackgroundRoot",
      [self = std::move(self), identity, aStatus]() {
        auto* actor = self->mImpl->mRootRequests.Lookup(IdentityKey(identity))
                          .DataPtrOrNull();
        if (!actor || !(*actor)->Matches(identity) ||
            NS_FAILED((*actor)->Complete(aStatus))) {
          self->Fail(NS_ERROR_NOT_AVAILABLE);
        }
      }));
}

nsresult NativeStylePreloadProcessBackgroundParent::ForwardRootOnStart(
    uint64_t aRootRequestId, uint64_t aRootGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsReady() || !aRootRequestId || !aRootGeneration) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::ForwardActivationProcessRootOnStart",
      [self = std::move(self), aRootRequestId, aRootGeneration]() {
        auto* actor =
            self->mImpl->mRootRequests.Lookup(aRootRequestId).DataPtrOrNull();
        if (!actor ||
            !(*actor)->MatchesRoot(aRootRequestId, aRootGeneration) ||
            NS_FAILED((*actor)->ForwardOnStart())) {
          self->Fail(NS_ERROR_NOT_AVAILABLE);
        }
      }));
}

nsresult NativeStylePreloadProcessBackgroundParent::ForwardRootData(
    uint64_t aRootRequestId, uint64_t aRootGeneration, uint32_t aSequence,
    nsCString&& aData) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsReady() || !aRootRequestId || !aRootGeneration || !aSequence) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::ForwardActivationProcessRootData",
      [self = std::move(self), aRootRequestId, aRootGeneration, aSequence,
       data = std::move(aData)]() mutable {
        auto* actor =
            self->mImpl->mRootRequests.Lookup(aRootRequestId).DataPtrOrNull();
        if (!actor ||
            !(*actor)->MatchesRoot(aRootRequestId, aRootGeneration) ||
            NS_FAILED((*actor)->ForwardData(aSequence, data))) {
          self->Fail(NS_ERROR_NOT_AVAILABLE);
        }
      }));
}

nsresult NativeStylePreloadProcessBackgroundParent::ForwardRootStop(
    uint64_t aRootRequestId, uint64_t aRootGeneration, uint32_t aSequence,
    nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsReady() || !aRootRequestId || !aRootGeneration || !aSequence) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::ForwardActivationProcessRootStop",
      [self = std::move(self), aRootRequestId, aRootGeneration, aSequence,
       aStatus]() {
        auto* actor =
            self->mImpl->mRootRequests.Lookup(aRootRequestId).DataPtrOrNull();
        if (!actor ||
            !(*actor)->MatchesRoot(aRootRequestId, aRootGeneration) ||
            NS_FAILED((*actor)->ForwardStop(aSequence, aStatus))) {
          self->Fail(NS_ERROR_NOT_AVAILABLE);
        }
      }));
}

nsresult NativeStylePreloadProcessBackgroundParent::CompleteStyle(
    uint64_t aRootRequestId, uint64_t aRootGeneration,
    uint64_t aStyleRequestId, uint32_t aDiscoverySequence, nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  NativeStylePreloadProcessBackgroundIdentity identity;
  identity.mKind = NativeStylePreloadProcessBackgroundKind::Style;
  identity.mRootRequestId = aRootRequestId;
  identity.mRootGeneration = aRootGeneration;
  identity.mStyleRequestId = aStyleRequestId;
  identity.mDiscoverySequence = aDiscoverySequence;
  if (!IsReady() || !IsValidIdentity(identity)) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::CompleteActivationProcessBackgroundStyle",
      [self = std::move(self), identity, aStatus]() {
        auto* actor = self->mImpl->mStyleRequests.Lookup(IdentityKey(identity))
                          .DataPtrOrNull();
        if (!actor || !(*actor)->Matches(identity) ||
            NS_FAILED((*actor)->Complete(aStatus))) {
          self->Fail(NS_ERROR_NOT_AVAILABLE);
        }
      }));
}

nsresult NativeStylePreloadProcessBackgroundParent::BeginShutdown() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mShuttingDown.exchange(true) || mImpl->mFailed) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  mImpl->mReady = false;
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::ShutdownActivationProcessBackgroundParent",
      [self = std::move(self)]() {
        if (!self->mImpl->mActor ||
            NS_FAILED(self->mImpl->mActor->BeginShutdown())) {
          self->Fail(NS_ERROR_FAILURE);
        }
      }));
}

void NativeStylePreloadProcessBackgroundParent::ManagerDestroyed(
    bool aExpected) {
  mImpl->mActor = nullptr;
  mImpl->mReady = false;
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundParentDestroyed",
          [self = std::move(self), aExpected]() {
            if (aExpected && self->mImpl->mShuttingDown &&
                self->mImpl->mCallbacks.mShutdownComplete) {
              self->mImpl->mCallbacks.mShutdownComplete();
            } else if (!aExpected) {
              self->Fail(NS_ERROR_FAILURE);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundParent::Fail(nsresult aStatus) {
  if (mImpl->mFailed.exchange(true)) {
    return;
  }
  mImpl->mReady = false;
  RefPtr self = this;
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::ActivationProcessBackgroundParentFailed",
      [self = std::move(self), aStatus]() {
        if (self->mImpl->mCallbacks.mFailed) {
          self->mImpl->mCallbacks.mFailed(NS_FAILED(aStatus) ? aStatus
                                                             : NS_ERROR_FAILURE);
        }
      }));
}

NativeStylePreloadProcessBackgroundChild::
    NativeStylePreloadProcessBackgroundChild(uint64_t aExpectedParentPid,
                                              uint64_t aExpectedChildPid,
                                              Callbacks&& aCallbacks)
    : mImpl(MakeUnique<Impl>(aExpectedParentPid, aExpectedChildPid,
                             std::move(aCallbacks))) {}

NativeStylePreloadProcessBackgroundChild::
    ~NativeStylePreloadProcessBackgroundChild() {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mThread) {
    (void)mImpl->mThread->Shutdown();
  }
}

nsresult NativeStylePreloadProcessBackgroundChild::Bind(
    ipc::Endpoint<PNativeStylePreloadProcessBackgroundChild>&& aEndpoint,
    uint64_t aExpectedParentPid, uint64_t aExpectedChildPid,
    Callbacks&& aCallbacks,
    RefPtr<NativeStylePreloadProcessBackgroundChild>& aChild) {
  MOZ_ASSERT(NS_IsMainThread());
  aChild = nullptr;
  RefPtr child = new NativeStylePreloadProcessBackgroundChild(
      aExpectedParentPid, aExpectedChildPid, std::move(aCallbacks));
  MOZ_TRY(child->BindOnBackground(std::move(aEndpoint)));
  aChild = std::move(child);
  return NS_OK;
}

nsresult NativeStylePreloadProcessBackgroundChild::BindOnBackground(
    ipc::Endpoint<PNativeStylePreloadProcessBackgroundChild>&& aEndpoint) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_TRY(NS_NewNamedThread("NF ActChild BG", getter_AddRefs(mImpl->mThread)));
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::BindActivationProcessBackgroundChild",
      [self = std::move(self), endpoint = std::move(aEndpoint)]() mutable {
        RefPtr actor = new NativeStylePreloadProcessBackgroundChildActor(self);
        if (!endpoint.Bind(actor) ||
            !actor->Start(self->mImpl->mExpectedChildPid,
                          self->mImpl->mExpectedParentPid)) {
          self->Fail(NS_ERROR_FAILURE);
          return;
        }
        self->mImpl->mActor = std::move(actor);
        self->ManagerReady();
      }));
}

bool NativeStylePreloadProcessBackgroundChild::IsReady() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mImpl->mReady && !mImpl->mFailed && !mImpl->mShuttingDown;
}

bool NativeStylePreloadProcessBackgroundChild::IsShuttingDownOnBackground()
    const {
  return mImpl->mShuttingDown;
}

void NativeStylePreloadProcessBackgroundChild::MarkShuttingDownOnBackground() {
  mImpl->mShuttingDown = true;
  mImpl->mReady = false;
}

void NativeStylePreloadProcessBackgroundChild::ManagerReady() {
  if (mImpl->mReady.exchange(true) || mImpl->mFailed ||
      mImpl->mShuttingDown) {
    Fail(NS_ERROR_UNEXPECTED);
    return;
  }
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundChildReady",
          [self = std::move(self)]() {
            if (self->IsReady() && self->mImpl->mCallbacks.mReady) {
              self->mImpl->mCallbacks.mReady();
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

nsresult NativeStylePreloadProcessBackgroundChild::SendRootReady(
    uint64_t aRootRequestId, uint64_t aRootGeneration) {
  NativeStylePreloadProcessBackgroundIdentity identity;
  identity.mKind = NativeStylePreloadProcessBackgroundKind::Root;
  identity.mRootRequestId = aRootRequestId;
  identity.mRootGeneration = aRootGeneration;
  return SendReady(identity);
}

nsresult NativeStylePreloadProcessBackgroundChild::SendStyleReady(
    uint64_t aRootRequestId, uint64_t aRootGeneration,
    uint64_t aStyleRequestId, uint32_t aDiscoverySequence) {
  NativeStylePreloadProcessBackgroundIdentity identity;
  identity.mKind = NativeStylePreloadProcessBackgroundKind::Style;
  identity.mRootRequestId = aRootRequestId;
  identity.mRootGeneration = aRootGeneration;
  identity.mStyleRequestId = aStyleRequestId;
  identity.mDiscoverySequence = aDiscoverySequence;
  return SendReady(identity);
}

nsresult NativeStylePreloadProcessBackgroundChild::SendReady(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!IsReady() || !IsValidIdentity(aIdentity)) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  RefPtr self = this;
  return mImpl->mThread->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::SendActivationProcessBackgroundReady",
      [self = std::move(self), aIdentity]() {
        auto& requests =
            aIdentity.mKind == NativeStylePreloadProcessBackgroundKind::Root
                ? self->mImpl->mRootRequests
                : self->mImpl->mStyleRequests;
        const uint64_t key = IdentityKey(aIdentity);
        if (!self->mImpl->mActor || requests.Contains(key)) {
          self->Fail(NS_ERROR_UNEXPECTED);
          return;
        }
        RefPtr actor =
            new NativeStylePreloadProcessBackgroundRequestChildActor(
                self, aIdentity);
        requests.InsertOrUpdate(key, actor);
        if (!self->mImpl->mActor
                 ->SendPNativeStylePreloadProcessBackgroundRequestConstructor(
                     actor, static_cast<uint8_t>(aIdentity.mKind),
                     aIdentity.mRootRequestId, aIdentity.mRootGeneration,
                     aIdentity.mStyleRequestId,
                     aIdentity.mDiscoverySequence) ||
            !actor->SendReadyMessage()) {
          requests.Remove(key);
          self->Fail(NS_ERROR_FAILURE);
        }
      }));
}

void NativeStylePreloadProcessBackgroundChild::RequestDestroyed(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
    bool aExpected) {
  auto& requests =
      aIdentity.mKind == NativeStylePreloadProcessBackgroundKind::Root
          ? mImpl->mRootRequests
          : mImpl->mStyleRequests;
  requests.Remove(IdentityKey(aIdentity));
  if (!aExpected) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundChild::ForwardRootOnStart(
    NativeStylePreloadProcessBackgroundRequestChildActor* aActor,
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity) {
  RefPtr self = this;
  RefPtr actor = aActor;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessRootOnStart",
          [self = std::move(self), actor = std::move(actor), aIdentity]() {
            if (!self->IsReady() || !self->mImpl->mCallbacks.mRootOnStart ||
                NS_FAILED(self->mImpl->mCallbacks.mRootOnStart(
                    aIdentity.mRootRequestId,
                    aIdentity.mRootGeneration))) {
              self->Fail(NS_ERROR_FAILURE);
              return;
            }
            RefPtr sendSelf = self;
            RefPtr sendActor = actor;
            if (NS_FAILED(self->mImpl->mThread->Dispatch(NS_NewRunnableFunction(
                    "NaiveFox::ActivationProcessRootOnStartAck",
                    [sendSelf = std::move(sendSelf),
                     sendActor = std::move(sendActor), aIdentity]() {
                      if (!sendActor->SendRootOnStartForwarded(
                              aIdentity.mRootRequestId,
                              aIdentity.mRootGeneration)) {
                        sendSelf->Fail(NS_ERROR_FAILURE);
                      }
                    })))) {
              self->Fail(NS_ERROR_FAILURE);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundChild::ForwardRootData(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
    uint32_t aSequence, nsCString&& aData) {
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessRootData",
          [self = std::move(self), aIdentity, aSequence,
           data = std::move(aData)]() mutable {
            if (!self->IsReady() || !self->mImpl->mCallbacks.mRootData ||
                NS_FAILED(self->mImpl->mCallbacks.mRootData(
                    aIdentity.mRootRequestId, aIdentity.mRootGeneration,
                    aSequence, std::move(data)))) {
              self->Fail(NS_ERROR_FAILURE);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundChild::ForwardRootStop(
    const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
    uint32_t aSequence, nsresult aStatus) {
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessRootStop",
          [self = std::move(self), aIdentity, aSequence, aStatus]() {
            if (!self->IsReady() || !self->mImpl->mCallbacks.mRootStop ||
                NS_FAILED(self->mImpl->mCallbacks.mRootStop(
                    aIdentity.mRootRequestId, aIdentity.mRootGeneration,
                    aSequence, aStatus))) {
              self->Fail(NS_ERROR_FAILURE);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundChild::ManagerDestroyed(
    bool aExpected) {
  mImpl->mActor = nullptr;
  mImpl->mReady = false;
  RefPtr self = this;
  if (NS_FAILED(NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::ActivationProcessBackgroundChildDestroyed",
          [self = std::move(self), aExpected]() {
            if (aExpected && self->mImpl->mCallbacks.mShutdownComplete) {
              self->mImpl->mCallbacks.mShutdownComplete();
            } else if (!aExpected) {
              self->Fail(NS_ERROR_FAILURE);
            }
          })))) {
    Fail(NS_ERROR_FAILURE);
  }
}

void NativeStylePreloadProcessBackgroundChild::Fail(nsresult aStatus) {
  if (mImpl->mFailed.exchange(true)) {
    return;
  }
  mImpl->mReady = false;
  RefPtr self = this;
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::ActivationProcessBackgroundChildFailed",
      [self = std::move(self), aStatus]() {
        if (self->mImpl->mCallbacks.mFailed) {
          self->mImpl->mCallbacks.mFailed(NS_FAILED(aStatus) ? aStatus
                                                             : NS_ERROR_FAILURE);
        }
      }));
}

}  // namespace mozilla::naivefox
