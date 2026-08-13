/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_SocksServer_h
#define netwerk_naivefox_SocksServer_h

#include <cstdint>

#include "Config.h"
#include "TunnelSession.h"
#include "nsStringFwd.h"
#include "nscore.h"

namespace mozilla::naivefox {

nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>& aListeners,
                             const TunnelConfig& aTunnelConfig,
                             uint32_t aMaxConnections = 0);

nsresult RunSocksServer(uint16_t aListenPort, const nsACString& aProxyUrl,
                        const nsACString& aProxyUser,
                        const nsACString& aProxyPassword,
                        uint32_t aMaxConnections, ProxyProtocol aProtocol);

}  // namespace mozilla::naivefox

#endif
