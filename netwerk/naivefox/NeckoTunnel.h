/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NeckoTunnel_h
#define netwerk_naivefox_NeckoTunnel_h

#include "nsStringFwd.h"
#include "nscore.h"

class nsIHttpUpgradeListener;
class nsIStreamListener;

namespace mozilla::naivefox {

nsresult OpenNeckoTunnel(const nsACString& aProxyUrl,
                         const nsACString& aTargetAuthority,
                         const nsACString& aProxyUser,
                         const nsACString& aProxyPassword,
                         nsIHttpUpgradeListener* aUpgradeListener,
                         nsIStreamListener* aChannelListener);

nsresult RunRawTunnelSmoke(const nsACString& aProxyUrl,
                           const nsACString& aTargetAuthority,
                           const nsACString& aProxyUser,
                           const nsACString& aProxyPassword);

}  // namespace mozilla::naivefox

#endif
