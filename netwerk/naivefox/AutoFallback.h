/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_AutoFallback_h
#define netwerk_naivefox_AutoFallback_h

#include <cstdint>

#include "ProxyProtocol.h"

namespace mozilla::naivefox {

struct AutoFallbackState {
  ProxyProtocol requestedProtocol;
  ProxyProtocol actualProtocol;
  bool fallbackUsed;
  bool ownerClosed;
  bool channelStopped;
  bool channelFailed;
  bool establishmentTimedOut;
  bool connectCodeKnown;
  int32_t connectCode;
  bool transportReady;
};

bool ShouldRetryH2FromH3(const AutoFallbackState& aState);

}  // namespace mozilla::naivefox

#endif
