/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "OuterSessionGate.h"

#include <algorithm>
#include <utility>
#include <vector>

#include "mozilla/Assertions.h"
#include "nsString.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

OuterSessionGate& OuterSessionGate::Get() {
  // The process-wide coordinator intentionally has the same lifetime as the
  // Necko connection pool. Avoid static-destruction ordering with XPCOM-held
  // callbacks during shutdown.
  static OuterSessionGate* sGate = new OuterSessionGate();
  return *sGate;
}

std::string OuterSessionGate::Key(const nsACString& aRouteKey) {
  return std::string(aRouteKey.BeginReading(), aRouteKey.Length());
}

OuterSessionGate::Admission OuterSessionGate::Enter(const nsACString& aRouteKey,
                                                    ParticipantId aParticipant,
                                                    OpenCallback&& aOpen) {
  MOZ_ASSERT(NS_IsMainThread());
  MOZ_ASSERT(aParticipant != 0);

  const std::string key = Key(aRouteKey);
  auto [iterator, inserted] = mRoutes.try_emplace(key);
  RouteState& route = iterator->second;
  if (inserted) {
    route.mLeader = aParticipant;
    route.mParticipants.insert(aParticipant);
    return Admission::Leader;
  }

  // Enter is idempotent for a participant. This makes a defensive retry safe
  // without replacing the callback already held for a queued participant.
  if (route.mParticipants.find(aParticipant) != route.mParticipants.end()) {
    if (route.mReady) {
      return Admission::Warm;
    }
    return route.mLeader == aParticipant ? Admission::Leader
                                         : Admission::Queued;
  }

  route.mParticipants.insert(aParticipant);
  if (route.mReady) {
    return Admission::Warm;
  }
  route.mWaiters.push_back({aParticipant, std::move(aOpen)});
  return Admission::Queued;
}

void OuterSessionGate::MarkReady(const nsACString& aRouteKey,
                                 ParticipantId aParticipant) {
  MOZ_ASSERT(NS_IsMainThread());
  auto iterator = mRoutes.find(Key(aRouteKey));
  if (iterator == mRoutes.end()) {
    return;
  }
  RouteState& route = iterator->second;
  if (route.mReady || route.mLeader != aParticipant ||
      route.mParticipants.find(aParticipant) == route.mParticipants.end()) {
    return;
  }

  route.mReady = true;
  std::vector<OpenCallback> callbacks;
  callbacks.reserve(route.mWaiters.size());
  while (!route.mWaiters.empty()) {
    callbacks.push_back(std::move(route.mWaiters.front().mOpen));
    route.mWaiters.pop_front();
  }
  for (auto& callback : callbacks) {
    if (callback) {
      callback();
    }
  }
}

void OuterSessionGate::Leave(const nsACString& aRouteKey,
                             ParticipantId aParticipant) {
  MOZ_ASSERT(NS_IsMainThread());
  auto iterator = mRoutes.find(Key(aRouteKey));
  if (iterator == mRoutes.end()) {
    return;
  }
  RouteState& route = iterator->second;
  if (!route.mParticipants.erase(aParticipant)) {
    return;
  }

  OpenCallback promoted;
  if (!route.mReady && route.mLeader == aParticipant) {
    route.mLeader = 0;
    if (!route.mWaiters.empty()) {
      Waiter waiter = std::move(route.mWaiters.front());
      route.mWaiters.pop_front();
      route.mLeader = waiter.mParticipant;
      promoted = std::move(waiter.mOpen);
    }
  } else if (!route.mReady) {
    auto waiter = std::find_if(route.mWaiters.begin(), route.mWaiters.end(),
                               [aParticipant](const Waiter& aWaiter) {
                                 return aWaiter.mParticipant == aParticipant;
                               });
    if (waiter != route.mWaiters.end()) {
      route.mWaiters.erase(waiter);
    }
  }

  // A ready route represents the process-local Necko pool, not the lifetime
  // of the last inner tunnel.  Short-lived browser connections routinely
  // leave and re-enter while the outer H2/H3 transport remains pooled.  Keep
  // the warm marker so a sequential CONNECT does not run another preamble.
  // Cold routes still disappear when their final participant leaves, which
  // allows a failed leader attempt to be retried.
  if (route.mParticipants.empty() && !route.mReady) {
    MOZ_ASSERT(route.mWaiters.empty());
    mRoutes.erase(iterator);
  }
  if (promoted) {
    promoted();
  }
}

size_t OuterSessionGate::RouteCountForTesting() const {
  MOZ_ASSERT(NS_IsMainThread());
  return mRoutes.size();
}

size_t OuterSessionGate::ParticipantCountForTesting(
    const nsACString& aRouteKey) const {
  MOZ_ASSERT(NS_IsMainThread());
  auto iterator = mRoutes.find(Key(aRouteKey));
  return iterator == mRoutes.end() ? 0 : iterator->second.mParticipants.size();
}

bool OuterSessionGate::RouteReadyForTesting(const nsACString& aRouteKey) const {
  MOZ_ASSERT(NS_IsMainThread());
  auto iterator = mRoutes.find(Key(aRouteKey));
  return iterator != mRoutes.end() && iterator->second.mReady;
}

OuterSessionGate::ParticipantId OuterSessionGate::LeaderForTesting(
    const nsACString& aRouteKey) const {
  MOZ_ASSERT(NS_IsMainThread());
  auto iterator = mRoutes.find(Key(aRouteKey));
  return iterator == mRoutes.end() ? 0 : iterator->second.mLeader;
}

}  // namespace mozilla::naivefox
