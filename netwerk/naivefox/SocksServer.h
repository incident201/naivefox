/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_SocksServer_h
#define netwerk_naivefox_SocksServer_h

#include <cstdint>

#include "nsStringFwd.h"
#include "nscore.h"

namespace mozilla::naivefox {

nsresult RunSocksServer(uint16_t aListenPort, const nsACString& aProxyUrl,
                        const nsACString& aProxyUser,
                        const nsACString& aProxyPassword,
                        uint32_t aMaxConnections);

}  // namespace mozilla::naivefox

#endif
