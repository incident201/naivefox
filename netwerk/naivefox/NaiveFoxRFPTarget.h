/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_NaiveFoxRFPTarget_h
#define mozilla_NaiveFoxRFPTarget_h

#include <bitset>
#include <cstdint>

#include "mozilla/EnumSet.h"

namespace mozilla {

// NaiveFox does not run the browser fingerprinting service, but nsILoadInfo's
// ABI retains its fixed-width override set.
enum class RFPTarget : uint8_t {};
using RFPTargetSet = EnumSet<RFPTarget, std::bitset<128>>;

}  // namespace mozilla

#endif  // mozilla_NaiveFoxRFPTarget_h
