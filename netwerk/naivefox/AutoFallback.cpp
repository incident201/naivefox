/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "AutoFallback.h"

namespace mozilla::naivefox {

bool ShouldRetryH2FromH3(const AutoFallbackState& aState) {
  return aState.requestedProtocol == ProxyProtocol::Auto &&
         aState.actualProtocol == ProxyProtocol::H3 && !aState.fallbackUsed &&
         !aState.ownerClosed && aState.channelStopped && aState.channelFailed &&
         ((aState.connectCodeKnown && aState.connectCode == 0) ||
          (!aState.connectCodeKnown && aState.establishmentTimedOut)) &&
         !aState.transportReady;
}

}  // namespace mozilla::naivefox
