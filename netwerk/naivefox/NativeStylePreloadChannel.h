/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NativeStylePreloadChannel_h
#define netwerk_naivefox_NativeStylePreloadChannel_h

#include "mozilla/OriginAttributes.h"
#include "nsError.h"
#include "nsIContentPolicy.h"

class nsIChannel;
class nsILoadGroup;
class nsIProxyInfo;
class nsIReferrerInfo;
class nsIURI;

namespace mozilla::css {
enum class StylePreloadKind : uint8_t;
}

namespace mozilla::naivefox {

enum class ProxyProtocol : uint8_t;

nsContentPolicyType StyleContentPolicyType(
    css::StylePreloadKind aPreloadKind);

// Creates, but does not open, the HTTP channel corresponding to an ordinary
// <link rel=stylesheet> discovered by Gecko's speculative HTML parser.  The
// descriptor producer remains responsible for discovery ordering and for
// resolving the effective referrer policy.  Keeping channel construction
// separate ensures that no request can be emitted before that descriptor is
// admitted.
//
// aProxyInfo may be null for unit tests and direct-channel consumers.  The
// NaiveFox preamble caller supplies its explicit proxy route.
nsresult NewNativeStylePreloadChannel(
    nsIURI* aResourceURI, nsIURI* aDocumentURI,
    css::StylePreloadKind aPreloadKind,
    const OriginAttributes& aOriginAttributes,
    nsIReferrerInfo* aResolvedReferrerInfo, nsILoadGroup* aLoadGroup,
    nsIProxyInfo* aProxyInfo, ProxyProtocol aProtocol,
    nsIChannel** aResult);

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_NativeStylePreloadChannel_h
