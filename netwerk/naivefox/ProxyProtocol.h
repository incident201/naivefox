/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_ProxyProtocol_h
#define netwerk_naivefox_ProxyProtocol_h

#include <cstdint>

namespace mozilla::naivefox {

enum class ProxyProtocol : uint8_t { H2, H3, Auto };

}  // namespace mozilla::naivefox

#endif
