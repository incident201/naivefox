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
constexpr char kDirectionalConnectMarker[] = "~7";
constexpr size_t kDirectionalConnectPrefixLength =
    sizeof(kDirectionalConnectMarker) - 1 + 1 + kDirectionalConnectTokenLength;

static_assert(sizeof(kNonIndexedCharacters) - 1 == 17);
static_assert(std::numeric_limits<uint64_t>::max() % kLengthCount == 0);
static_assert(kDirectionalConnectPrefixLength == kHeaderPaddingMinLength);

Maybe<uint64_t> SystemRandomUint64() { return RandomUint64(); }

nsresult RandomHeaderPaddingLength(size_t& aLength,
                                   HeaderPaddingRandom aRandom) {
  Maybe<uint64_t> lengthBits;
  do {
    lengthBits = aRandom();
    if (lengthBits.isNothing()) {
      return NS_ERROR_FAILURE;
    }
  } while (*lengthBits == std::numeric_limits<uint64_t>::max());
  aLength = kHeaderPaddingMinLength + (*lengthBits % kLengthCount);
  return NS_OK;
}

char DirectionalConnectLaneMarker(DirectionalConnectLane aLane) {
  return aLane == DirectionalConnectLane::Upstream ? '!' : '#';
}

void BuildDirectionalConnectHeaderPadding(size_t aLength,
                                          DirectionalConnectLane aLane,
                                          const nsACString& aToken,
                                          nsACString& aPadding) {
  nsCString padding;
  padding.SetLength(aLength);
  char* output = padding.BeginWriting();
  output[0] = kDirectionalConnectMarker[0];
  output[1] = kDirectionalConnectMarker[1];
  output[2] = DirectionalConnectLaneMarker(aLane);
  std::copy(aToken.BeginReading(), aToken.EndReading(), output + 3);
  std::fill(output + kDirectionalConnectPrefixLength, output + aLength,
            kNonIndexedCharacters[16]);
  aPadding = std::move(padding);
}

}  // namespace

nsresult GenerateHeaderPadding(nsACString& aPadding,
                               HeaderPaddingRandom aRandom) {
  size_t length = 0;
  MOZ_TRY(RandomHeaderPaddingLength(length, aRandom));

  Maybe<uint64_t> uniqueBits = aRandom();
  if (uniqueBits.isNothing()) {
    return NS_ERROR_FAILURE;
  }

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

nsresult GenerateDirectionalConnectHeaderPadding(nsACString& aUpstreamPadding,
                                                 nsACString& aDownstreamPadding,
                                                 nsACString& aToken,
                                                 HeaderPaddingRandom aRandom) {
  Maybe<uint64_t> tokenBits = aRandom();
  if (tokenBits.isNothing()) {
    return NS_ERROR_FAILURE;
  }

  size_t upstreamLength = 0;
  MOZ_TRY(RandomHeaderPaddingLength(upstreamLength, aRandom));
  size_t downstreamLength = 0;
  MOZ_TRY(RandomHeaderPaddingLength(downstreamLength, aRandom));

  nsCString token;
  token.SetLength(kDirectionalConnectTokenLength);
  char* tokenOutput = token.BeginWriting();
  for (size_t i = 0; i < kDirectionalConnectTokenLength; ++i) {
    tokenOutput[i] = kNonIndexedCharacters[*tokenBits & 0xf];
    *tokenBits >>= 4;
  }

  nsCString upstreamPadding;
  nsCString downstreamPadding;
  BuildDirectionalConnectHeaderPadding(
      upstreamLength, DirectionalConnectLane::Upstream, token, upstreamPadding);
  BuildDirectionalConnectHeaderPadding(downstreamLength,
                                       DirectionalConnectLane::Downstream,
                                       token, downstreamPadding);
  aUpstreamPadding = std::move(upstreamPadding);
  aDownstreamPadding = std::move(downstreamPadding);
  aToken = std::move(token);
  return NS_OK;
}

nsresult GenerateDirectionalConnectHeaderPadding(nsACString& aUpstreamPadding,
                                                 nsACString& aDownstreamPadding,
                                                 nsACString& aToken) {
  return GenerateDirectionalConnectHeaderPadding(
      aUpstreamPadding, aDownstreamPadding, aToken, SystemRandomUint64);
}

bool MatchesDirectionalConnectHeaderPadding(
    const nsACString& aPadding, DirectionalConnectLane aExpectedLane,
    const nsACString& aExpectedToken) {
  return aPadding.Length() >= kDirectionalConnectPrefixLength &&
         aExpectedToken.Length() == kDirectionalConnectTokenLength &&
         aPadding.CharAt(0) == kDirectionalConnectMarker[0] &&
         aPadding.CharAt(1) == kDirectionalConnectMarker[1] &&
         aPadding.CharAt(2) == DirectionalConnectLaneMarker(aExpectedLane) &&
         Substring(aPadding, 3, kDirectionalConnectTokenLength)
             .Equals(aExpectedToken);
}

}  // namespace mozilla::naivefox
