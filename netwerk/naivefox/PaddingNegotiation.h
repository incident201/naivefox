/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_PaddingNegotiation_h
#define netwerk_naivefox_PaddingNegotiation_h

#include "mozilla/Maybe.h"
#include "nscore.h"

namespace mozilla::naivefox {

nsresult NegotiatePayloadPadding(int32_t aConnectCode,
                                 const Maybe<bool>& aResponseHeaderPresent,
                                 bool& aEnabled);

}  // namespace mozilla::naivefox

#endif
