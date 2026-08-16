/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_Promise_h
#define netwerk_naivefox_Promise_h

#include "js/TypeDecls.h"
#include "nscore.h"

namespace mozilla::dom {

// The lean runtime does not create DOM promises. CycleCollectedJSContext keeps
// its ABI-shaped containers, but its rejection tracking is compiled out.
class Promise final {
 public:
  MozExternalRefCountType AddRef() { return 1; }
  MozExternalRefCountType Release() { return 1; }
  JSObject* PromiseObj() const { return nullptr; }
};

}  // namespace mozilla::dom

#endif
