/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_Glean_NaiveFoxPing_h
#define mozilla_Glean_NaiveFoxPing_h

#include <stdint.h>

// Generated ping declarations keep their source ABI; NaiveFox never submits.
namespace mozilla::glean::impl {

class Ping final {
 public:
  constexpr explicit Ping(uint32_t = 0) {}

  template <typename... Args>
  constexpr void Submit(Args&&...) const {}
  template <typename... Args>
  constexpr void SetEnabled(Args&&...) const {}
  template <typename... Args>
  constexpr void TestBeforeNextSubmit(Args&&...) const {}
};

}  // namespace mozilla::glean::impl

#endif  // mozilla_Glean_NaiveFoxPing_h
