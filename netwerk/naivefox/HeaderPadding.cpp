/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "HeaderPadding.h"

#include <algorithm>
#include <limits>
#include <utility>

#include "mozilla/RandomNum.h"
#include "nsError.h"
#include "nsString.h"

namespace mozilla::naivefox {

namespace {

constexpr char kNonIndexedCharacters[] = "!#$()+<>?@[]^`{}~";
constexpr uint64_t kLengthCount =
    kHeaderPaddingMaxLength - kHeaderPaddingMinLength + 1;

static_assert(sizeof(kNonIndexedCharacters) - 1 == 17);
static_assert(std::numeric_limits<uint64_t>::max() % kLengthCount == 0);

Maybe<uint64_t> SystemRandomUint64() { return RandomUint64(); }

}  // namespace

nsresult GenerateHeaderPadding(nsACString& aPadding,
                               HeaderPaddingRandom aRandom) {
  Maybe<uint64_t> lengthBits;
  do {
    lengthBits = aRandom();
    if (lengthBits.isNothing()) {
      return NS_ERROR_FAILURE;
    }
  } while (*lengthBits == std::numeric_limits<uint64_t>::max());

  Maybe<uint64_t> uniqueBits = aRandom();
  if (uniqueBits.isNothing()) {
    return NS_ERROR_FAILURE;
  }

  const size_t length = kHeaderPaddingMinLength + (*lengthBits % kLengthCount);
  nsCString padding;
  padding.SetLength(length);
  char* output = padding.BeginWriting();
  const size_t uniqueLength = std::min(length, size_t{16});
  for (size_t i = 0; i < uniqueLength; ++i) {
    output[i] = kNonIndexedCharacters[*uniqueBits & 0xf];
    *uniqueBits >>= 4;
  }
  std::fill(output + uniqueLength, output + length, kNonIndexedCharacters[16]);

  aPadding = std::move(padding);
  return NS_OK;
}

nsresult GenerateHeaderPadding(nsACString& aPadding) {
  return GenerateHeaderPadding(aPadding, SystemRandomUint64);
}

}  // namespace mozilla::naivefox
