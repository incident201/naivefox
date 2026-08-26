/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NativeStylePreloadProcessBackground_h
#define netwerk_naivefox_NativeStylePreloadProcessBackground_h

#include <cstdint>
#include <functional>

#include "mozilla/AlreadyAddRefed.h"
#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "mozilla/ipc/Endpoint.h"
#include "mozilla/naivefox/PNativeStylePreloadProcessBackground.h"
#include "nsError.h"
#include "nsString.h"

namespace mozilla::naivefox {

class NativeStylePreloadProcessBackgroundParentActor;
class NativeStylePreloadProcessBackgroundChildActor;
class NativeStylePreloadProcessBackgroundRequestParentActor;
class NativeStylePreloadProcessBackgroundRequestChildActor;

enum class NativeStylePreloadProcessBackgroundKind : uint8_t {
  Root = 1,
  Style = 2,
};

struct NativeStylePreloadProcessBackgroundIdentity final {
  NativeStylePreloadProcessBackgroundKind mKind =
      NativeStylePreloadProcessBackgroundKind::Root;
  uint64_t mRootRequestId = 0;
  uint64_t mRootGeneration = 0;
  uint64_t mStyleRequestId = 0;
  uint32_t mDiscoverySequence = 0;
};

/**
 * Parent half of the persistent process-background channel.  Its manager and
 * request actors are bound to a dedicated serial event target.  All product
 * callbacks are re-dispatched to the parent main thread.
 */
class NativeStylePreloadProcessBackgroundParent final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessBackgroundParent)

  struct Callbacks final {
    std::function<void()> mReady;
    std::function<nsresult(uint64_t, uint64_t)> mRootReady;
    std::function<nsresult(uint64_t, uint64_t)> mRootOnStartForwarded;
    std::function<nsresult(uint64_t, uint64_t, uint64_t, uint32_t)>
        mStyleReady;
    std::function<void(uint64_t, uint64_t)> mRootDrained;
    std::function<void(nsresult)> mFailed;
    std::function<void()> mShutdownComplete;
  };

  static nsresult Create(
      ipc::EndpointProcInfo aParentProcInfo,
      ipc::EndpointProcInfo aChildProcInfo, uint64_t aExpectedParentPid,
      uint64_t aExpectedChildPid, Callbacks&& aCallbacks,
      RefPtr<NativeStylePreloadProcessBackgroundParent>& aParent,
      ipc::Endpoint<PNativeStylePreloadProcessBackgroundChild>& aChildEndpoint);

  bool IsReady() const;
  nsresult CompleteRoot(uint64_t aRootRequestId, uint64_t aRootGeneration,
                        nsresult aStatus);
  nsresult ForwardRootOnStart(uint64_t aRootRequestId,
                              uint64_t aRootGeneration);
  nsresult ForwardRootData(uint64_t aRootRequestId, uint64_t aRootGeneration,
                           uint32_t aSequence, nsCString&& aData);
  nsresult ForwardRootStop(uint64_t aRootRequestId, uint64_t aRootGeneration,
                           uint32_t aSequence, nsresult aStatus);
  nsresult CompleteStyle(uint64_t aRootRequestId, uint64_t aRootGeneration,
                         uint64_t aStyleRequestId,
                         uint32_t aDiscoverySequence, nsresult aStatus);
  nsresult BeginShutdown();

 private:
  class Impl;
  friend class NativeStylePreloadProcessBackgroundParentActor;
  friend class NativeStylePreloadProcessBackgroundRequestParentActor;

  NativeStylePreloadProcessBackgroundParent(uint64_t aExpectedParentPid,
                                             uint64_t aExpectedChildPid,
                                             Callbacks&& aCallbacks);
  ~NativeStylePreloadProcessBackgroundParent();

  nsresult Bind(
      ipc::Endpoint<PNativeStylePreloadProcessBackgroundParent>&& aEndpoint);
  void ManagerReady();
  void RequestReady(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity);
  void RootOnStartForwarded(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity);
  void RequestDestroyed(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
      bool aExpected);
  void MaybeNotifyRootDrained(uint64_t aRootRequestId,
                              uint64_t aRootGeneration);
  void ManagerDestroyed(bool aExpected);
  bool IsShuttingDownOnBackground() const;
  void Fail(nsresult aStatus);

  UniquePtr<Impl> mImpl;
};

/** Child half. Main-thread callers register one keyed request; construction
 * and Ready transmission occur on the persistent child background target. */
class NativeStylePreloadProcessBackgroundChild final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(
      NativeStylePreloadProcessBackgroundChild)

  struct Callbacks final {
    std::function<void()> mReady;
    std::function<nsresult(uint64_t, uint64_t)> mRootOnStart;
    std::function<nsresult(uint64_t, uint64_t, uint32_t, nsCString&&)>
        mRootData;
    std::function<nsresult(uint64_t, uint64_t, uint32_t, nsresult)> mRootStop;
    std::function<void(nsresult)> mFailed;
    std::function<void()> mShutdownComplete;
  };

  static nsresult Bind(
      ipc::Endpoint<PNativeStylePreloadProcessBackgroundChild>&& aEndpoint,
      uint64_t aExpectedParentPid, uint64_t aExpectedChildPid,
      Callbacks&& aCallbacks,
      RefPtr<NativeStylePreloadProcessBackgroundChild>& aChild);

  bool IsReady() const;
  nsresult SendRootReady(uint64_t aRootRequestId, uint64_t aRootGeneration);
  nsresult SendStyleReady(uint64_t aRootRequestId, uint64_t aRootGeneration,
                          uint64_t aStyleRequestId,
                          uint32_t aDiscoverySequence);

 private:
  class Impl;
  friend class NativeStylePreloadProcessBackgroundChildActor;
  friend class NativeStylePreloadProcessBackgroundRequestChildActor;

  NativeStylePreloadProcessBackgroundChild(uint64_t aExpectedParentPid,
                                            uint64_t aExpectedChildPid,
                                            Callbacks&& aCallbacks);
  ~NativeStylePreloadProcessBackgroundChild();

  nsresult BindOnBackground(
      ipc::Endpoint<PNativeStylePreloadProcessBackgroundChild>&& aEndpoint);
  nsresult SendReady(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity);
  void ManagerReady();
  void RequestDestroyed(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
      bool aExpected);
  void ForwardRootOnStart(
      NativeStylePreloadProcessBackgroundRequestChildActor* aActor,
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity);
  void ForwardRootData(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
      uint32_t aSequence, nsCString&& aData);
  void ForwardRootStop(
      const NativeStylePreloadProcessBackgroundIdentity& aIdentity,
      uint32_t aSequence, nsresult aStatus);
  void ManagerDestroyed(bool aExpected);
  bool IsShuttingDownOnBackground() const;
  void MarkShuttingDownOnBackground();
  void Fail(nsresult aStatus);

  UniquePtr<Impl> mImpl;
};

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_NativeStylePreloadProcessBackground_h
