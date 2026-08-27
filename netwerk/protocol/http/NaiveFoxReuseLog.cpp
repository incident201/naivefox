/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NaiveFoxReuseLog.h"

namespace mozilla::net {

LazyLogModule gNaiveFoxReuseLog("NaiveFoxReuse");

const void* NaiveFoxConnectionInfoId(const nsHttpConnectionInfo* aConnInfo) {
  return aConnInfo;
}

}  // namespace mozilla::net
