/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_net_UnknownProtocolHandler_h
#define mozilla_net_UnknownProtocolHandler_h

#include "nsIProtocolHandler.h"

namespace mozilla::net {

// The full Firefox application delegates unknown schemes to the operating
// system. NaiveFox has no browser UI or external-application launcher, so its
// default handler rejects them deterministically.
class UnknownProtocolHandler final : public nsIProtocolHandler {
 public:
  NS_DECL_ISUPPORTS
  NS_DECL_NSIPROTOCOLHANDLER

  UnknownProtocolHandler() = default;

 private:
  ~UnknownProtocolHandler() = default;
};

}  // namespace mozilla::net

#endif  // mozilla_net_UnknownProtocolHandler_h
