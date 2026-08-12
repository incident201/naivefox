/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "PaddingNegotiation.h"

#include "nsError.h"

namespace mozilla::naivefox {

nsresult NegotiatePayloadPadding(int32_t aConnectCode,
                                 const Maybe<bool>& aResponseHeaderPresent,
                                 bool& aEnabled) {
  aEnabled = false;
  if (aConnectCode != 200) {
    return NS_ERROR_FAILURE;
  }
  aEnabled = aResponseHeaderPresent.valueOr(false);
  return NS_OK;
}

}  // namespace mozilla::naivefox
