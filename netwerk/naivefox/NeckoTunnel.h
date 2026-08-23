/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NeckoTunnel_h
#define netwerk_naivefox_NeckoTunnel_h

#include "Config.h"
#include "ProxyProtocol.h"
#include "mozilla/Maybe.h"
#include "nsTArray.h"
#include "nsStringFwd.h"
#include "nscore.h"

class nsIHttpUpgradeListener;
class nsIRequest;
class nsIStreamListener;

namespace mozilla::naivefox {

// Internal Naive proxy authentication helper. This is not part of the
// embedded C ABI.
nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization);

nsresult OpenNeckoTunnel(
    const nsACString& aProxyUrl, const nsACString& aTargetAuthority,
    const nsACString& aProxyUser, const nsACString& aProxyPassword,
    nsIHttpUpgradeListener* aUpgradeListener,
    nsIStreamListener* aChannelListener, const nsACString& aConnectPadding,
    ProxyProtocol aProtocol, const Maybe<HostResolverRule>& aHostResolverRule = {},
    const nsTArray<ExtraHeader>& aExtraHeaders = {},
    nsIRequest** aOpenedRequest = nullptr);

nsresult RunRawTunnelSmoke(const nsACString& aProxyUrl,
                           const nsACString& aTargetAuthority,
                           const nsACString& aProxyUser,
                           const nsACString& aProxyPassword,
                           ProxyProtocol aProtocol);

}  // namespace mozilla::naivefox

#endif
