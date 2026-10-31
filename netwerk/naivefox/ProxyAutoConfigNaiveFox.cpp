/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "ProxyAutoConfig.h"

#include "nsISerialEventTarget.h"
#include "nsITimer.h"

namespace mozilla::net {

ProxyAutoConfig::ProxyAutoConfig() = default;

ProxyAutoConfig::~ProxyAutoConfig() = default;

nsresult ProxyAutoConfig::ConfigurePAC(const nsACString&, const nsACString&,
                                       bool, uint32_t, nsISerialEventTarget*) {
  return NS_ERROR_NOT_IMPLEMENTED;
}

void ProxyAutoConfig::SetThreadLocalIndex(uint32_t) {}

void ProxyAutoConfig::Shutdown() {}

void ProxyAutoConfig::GC() {}

void ProxyAutoConfig::GetProxyForURIWithCallback(
    const nsACString&, const nsACString&,
    std::function<void(nsresult aStatus, const nsACString& aResult)>&&
        aCallback) {
  aCallback(NS_ERROR_NOT_IMPLEMENTED, ""_ns);
}

}  // namespace mozilla::net
