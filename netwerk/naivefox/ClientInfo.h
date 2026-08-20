/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_ClientInfo_h
#define mozilla_dom_ClientInfo_h

#include "nsISupportsImpl.h"

namespace mozilla::dom {

// TRRLoadInfo implements the browser-client methods as inert parent-process
// placeholders. The lean runtime therefore needs the type, not DOM clients.
class ClientInfo final {
 public:
  friend bool operator==(const ClientInfo&, const ClientInfo&) { return true; }
};

class ClientSource final {
 public:
  ClientInfo Info() const { return {}; }
};

class PerformanceStorage {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(PerformanceStorage)

 private:
  ~PerformanceStorage() = default;
};

}  // namespace mozilla::dom

#endif
