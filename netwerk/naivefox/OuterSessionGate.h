/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_OuterSessionGate_h
#define netwerk_naivefox_OuterSessionGate_h

#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "nsStringFwd.h"

namespace mozilla::naivefox {

// Serializes only the cold establishment edge for an outer connection-pool
// route. Once the leader has established a tunnel, every waiter is released
// together and subsequent users pass through. A ready marker intentionally
// remains until process shutdown: this is a startup-burst experiment and is
// not yet tied to the exact lifetime of a Necko pooled transport. All methods
// must be called on the main thread.
class OuterSessionGate final {
 public:
  using ParticipantId = uint64_t;
  using OpenCallback = std::function<void()>;

  enum class Admission : uint8_t {
    Leader,
    Queued,
    Warm,
  };

  static OuterSessionGate& Get();

  OuterSessionGate() = default;

  Admission Enter(const nsACString& aRouteKey, ParticipantId aParticipant,
                  OpenCallback&& aOpen);
  void MarkReady(const nsACString& aRouteKey, ParticipantId aParticipant);
  void Leave(const nsACString& aRouteKey, ParticipantId aParticipant);

  size_t RouteCountForTesting() const;
  size_t ParticipantCountForTesting(const nsACString& aRouteKey) const;
  bool RouteReadyForTesting(const nsACString& aRouteKey) const;
  ParticipantId LeaderForTesting(const nsACString& aRouteKey) const;

 private:
  struct Waiter final {
    ParticipantId mParticipant;
    OpenCallback mOpen;
  };

  struct RouteState final {
    ParticipantId mLeader = 0;
    bool mReady = false;
    std::unordered_set<ParticipantId> mParticipants;
    std::deque<Waiter> mWaiters;
  };

  static std::string Key(const nsACString& aRouteKey);

  std::unordered_map<std::string, RouteState> mRoutes;
};

}  // namespace mozilla::naivefox

#endif
