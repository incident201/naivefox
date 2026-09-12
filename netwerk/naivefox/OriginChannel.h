/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_OriginChannel_h
#define netwerk_naivefox_OriginChannel_h
#include "Config.h"
#include "nsIContentPolicy.h"
class nsIChannel;
namespace mozilla::naivefox {
nsresult CreateOriginChannel(
    const nsACString& aProxyUrl, const nsACString& aPath,
    ProxyProtocol aProtocol, const Maybe<HostResolverRule>& aHostResolverRule,
    nsIChannel** aChannel,
    nsContentPolicyType aContentPolicyType = nsIContentPolicy::TYPE_OTHER);
nsresult BuildProxyAuthorization(const nsACString& aUser,
                                 const nsACString& aPassword,
                                 nsACString& aAuthorization);
}  // namespace mozilla::naivefox
#endif
