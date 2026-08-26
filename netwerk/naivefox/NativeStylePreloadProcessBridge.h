/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NativeStylePreloadProcessBridge_h
#define netwerk_naivefox_NativeStylePreloadProcessBridge_h

#include <cstdint>
#include <functional>

#include "mozilla/AlreadyAddRefed.h"
#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "nsError.h"
#include "nsHashKeys.h"
#include "nsString.h"
#include "nsTHashMap.h"

namespace mozilla::naivefox {

class PNativeStylePreloadProcessParent;
class PNativeStylePreloadProcessChild;
class PNativeStylePreloadProcessRootParent;
class PNativeStylePreloadProcessRootChild;
class PNativeStylePreloadProcessStyleParent;
class PNativeStylePreloadProcessStyleChild;
class NativeStylePreloadProcessRootParentActor;
class NativeStylePreloadProcessRootChildActor;
class NativeStylePreloadProcessStyleParentActor;
class NativeStylePreloadProcessStyleChildActor;
class NativeRootReplacementActivationArgs;
class NativeStylePreloadProcessArgs;

namespace detail {

class NativeStylePreloadProcessCanceledRoutes final {
 public:
  void Insert(uint64_t aRequestId, uint64_t aGeneration);
  bool Contains(uint64_t aRequestId, uint64_t aGeneration) const;
  void Remove(uint64_t aRequestId, uint64_t aGeneration);

 private:
  nsTHashMap<nsUint64HashKey, uint64_t> mRoutes;
};

}  // namespace detail

/**
 * Parent-side owner for request-scoped activation actors.  The networking
 * caller remains authoritative for root metadata and for every native style
 * channel.  This class only transports the root body to the activation child
 * and returns parser discoveries to the parent process.
 */
class NativeStylePreloadProcessParentBridge final {
 public:
  class Impl;
  struct Callbacks final {
    std::function<nsresult(uint64_t, uint64_t)> mRootReady;
    std::function<nsresult(const NativeStylePreloadProcessArgs&)>
        mStyleDiscovered;
    std::function<void(uint64_t, uint64_t, uint32_t, uint32_t, uint32_t,
                       nsresult)>
        mRootFinished;
    std::function<void(uint64_t, uint64_t, nsresult)> mRootFailed;
    std::function<void(nsresult)> mTransportFailed;
  };

  NativeStylePreloadProcessParentBridge(
      PNativeStylePreloadProcessParent* aManager, Callbacks&& aCallbacks);
  ~NativeStylePreloadProcessParentBridge();

  nsresult StartRoot(NativeRootReplacementActivationArgs&& aArgs,
                     uint64_t aExpectedParentPid, uint64_t aExpectedChildPid,
                     uint32_t aMaximumBodyBytes);
  nsresult SendRootData(uint64_t aRequestId, uint64_t aGeneration,
                        uint32_t aSequence, nsCString&& aData);
  nsresult SendRootStop(uint64_t aRequestId, uint64_t aGeneration,
                        uint32_t aSequence, nsresult aStatus);
  void CancelRoot(uint64_t aRequestId, uint64_t aGeneration, nsresult aStatus);
  nsresult CompleteStyle(uint64_t aStyleRequestId, nsresult aStatus);

  already_AddRefed<PNativeStylePreloadProcessStyleParent> AllocStyle(
      const NativeStylePreloadProcessArgs& aArgs);
  bool DeallocRoot(PNativeStylePreloadProcessRootParent* aActor);
  bool DeallocStyle(PNativeStylePreloadProcessStyleParent* aActor);
  void ProcessActorDestroyed();

 private:
  friend class NativeStylePreloadProcessRootParentActor;
  friend class NativeStylePreloadProcessStyleParentActor;
  UniquePtr<Impl> mImpl;
};

/** Child-side replacement-channel, parser and resource-discovery owner. */
class NativeStylePreloadProcessChildBridge final {
 public:
  class Impl;
  explicit NativeStylePreloadProcessChildBridge(
      PNativeStylePreloadProcessChild* aManager);
  ~NativeStylePreloadProcessChildBridge();

  nsresult Initialize();
  void Shutdown();

  already_AddRefed<PNativeStylePreloadProcessRootChild> AllocRoot(
      uint64_t aRequestId, uint64_t aGeneration);
  bool DeallocRoot(PNativeStylePreloadProcessRootChild* aActor);
  bool DeallocStyle(PNativeStylePreloadProcessStyleChild* aActor);
  void ProcessActorDestroyed();

 private:
  friend class NativeStylePreloadProcessRootChildActor;
  friend class NativeStylePreloadProcessStyleChildActor;
  UniquePtr<Impl> mImpl;
};

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_NativeStylePreloadProcessBridge_h
