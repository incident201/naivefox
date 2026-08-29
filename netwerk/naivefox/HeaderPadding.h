/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_HeaderPadding_h
#define netwerk_naivefox_HeaderPadding_h

#include <cstddef>
#include <cstdint>

#include "mozilla/FunctionRef.h"
#include "mozilla/Maybe.h"
#include "nsStringFwd.h"
#include "nscore.h"

namespace mozilla::naivefox {

inline constexpr size_t kHeaderPaddingMinLength = 16;
inline constexpr size_t kHeaderPaddingMaxLength = 32;

using HeaderPaddingRandom = FunctionRef<Maybe<uint64_t>()>;

[[nodiscard]] nsresult GenerateHeaderPadding(nsACString& aPadding,
                                             HeaderPaddingRandom aRandom);
[[nodiscard]] nsresult GenerateHeaderPadding(nsACString& aPadding);

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_HeaderPadding_h
