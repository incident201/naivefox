/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "UnknownProtocolHandler.h"

#include "nsError.h"
#include "nsString.h"

namespace mozilla::net {

NS_IMPL_ISUPPORTS(UnknownProtocolHandler, nsIProtocolHandler)

NS_IMETHODIMP UnknownProtocolHandler::GetScheme(nsACString& aScheme) {
  aScheme.AssignLiteral("default");
  return NS_OK;
}

NS_IMETHODIMP UnknownProtocolHandler::NewChannel(nsIURI*, nsILoadInfo*,
                                                 nsIChannel**) {
  return NS_ERROR_UNKNOWN_PROTOCOL;
}

NS_IMETHODIMP UnknownProtocolHandler::AllowPort(int32_t, const char*,
                                                bool* aAllowed) {
  *aAllowed = false;
  return NS_OK;
}

}  // namespace mozilla::net
