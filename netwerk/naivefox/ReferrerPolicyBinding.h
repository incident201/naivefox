/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef DOM_REFERRERPOLICYBINDING_H_
#define DOM_REFERRERPOLICYBINDING_H_

#include <stdint.h>

namespace mozilla::dom {
enum class ReferrerPolicy : uint8_t {
  _empty,
  No_referrer,
  No_referrer_when_downgrade,
  Origin,
  Origin_when_cross_origin,
  Unsafe_url,
  Same_origin,
  Strict_origin,
  Strict_origin_when_cross_origin,
};
}  // namespace mozilla::dom

#endif
