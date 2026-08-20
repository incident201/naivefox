/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_HttpClient_h
#define netwerk_naivefox_HttpClient_h

#include "nsStringFwd.h"
#include "nscore.h"

namespace mozilla::naivefox {

nsresult FetchWithNecko(const nsACString& aUrl);

}  // namespace mozilla::naivefox

#endif
