/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_NaiveFoxOriginTrials_h
#define mozilla_NaiveFoxOriginTrials_h

#include <stdint.h>

struct JSContext;
class JSObject;

namespace mozilla {

// NaiveFox has no content globals, so origin trials cannot be enabled.  Keep
// the small type surface required by the generic binding support without
// pulling the browser origin-trial service and its DOM closure into libxul.
enum class OriginTrial : uint8_t {};

class OriginTrials final {
 public:
  static bool IsEnabled(JSContext*, JSObject*, OriginTrial) { return false; }
};

}  // namespace mozilla

#endif  // mozilla_NaiveFoxOriginTrials_h
