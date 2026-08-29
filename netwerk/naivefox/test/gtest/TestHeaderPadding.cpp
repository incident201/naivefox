/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <algorithm>
#include <cstdint>
#include <iterator>
#include <limits>

#include "HeaderPadding.h"
#include "gtest/gtest.h"
#include "mozilla/Maybe.h"
#include "nsError.h"
#include "nsString.h"

namespace mozilla::naivefox {

TEST(NaiveFoxHeaderPadding, ExactSequentialNibbleVector)
{
  const uint64_t values[] = {0, 0xfedcba9876543210};
  size_t next = 0;
  auto random = [&]() { return Some(values[next++]); };

  nsCString padding;
  ASSERT_EQ(GenerateHeaderPadding(padding, random), NS_OK);
  EXPECT_EQ(padding, "!#$()+<>?@[]^`{}");
  EXPECT_EQ(next, std::size(values));
}

TEST(NaiveFoxHeaderPadding, ExactMaximumLengthVector)
{
  const uint64_t values[] = {16, 0xfedcba9876543210};
  size_t next = 0;
  auto random = [&]() { return Some(values[next++]); };

  nsCString padding;
  ASSERT_EQ(GenerateHeaderPadding(padding, random), NS_OK);
  EXPECT_EQ(padding, "!#$()+<>?@[]^`{}~~~~~~~~~~~~~~~~");
}

TEST(NaiveFoxHeaderPadding, CoversEveryInclusiveLength)
{
  for (uint64_t lengthBits = 0; lengthBits < 17; ++lengthBits) {
    const uint64_t values[] = {lengthBits, 0};
    size_t next = 0;
    auto random = [&]() { return Some(values[next++]); };

    nsCString padding;
    ASSERT_EQ(GenerateHeaderPadding(padding, random), NS_OK);
    EXPECT_EQ(padding.Length(), kHeaderPaddingMinLength + lengthBits);
    EXPECT_GE(padding.Length(), kHeaderPaddingMinLength);
    EXPECT_LE(padding.Length(), kHeaderPaddingMaxLength);
  }
}

TEST(NaiveFoxHeaderPadding, UsesOnlyCompatibleCharactersAndTildeTail)
{
  const uint64_t values[] = {16, 0x0123456789abcdef};
  size_t next = 0;
  auto random = [&]() { return Some(values[next++]); };

  nsCString padding;
  ASSERT_EQ(GenerateHeaderPadding(padding, random), NS_OK);
  const nsCString charset("!#$()+<>?@[]^`{}");
  for (size_t i = 0; i < 16; ++i) {
    EXPECT_NE(charset.FindChar(padding[i]), kNotFound);
  }
  EXPECT_TRUE(std::all_of(padding.BeginReading() + 16, padding.EndReading(),
                          [](char aCharacter) { return aCharacter == '~'; }));
}

TEST(NaiveFoxHeaderPadding, RejectsModuloBiasedEndpoint)
{
  const uint64_t values[] = {std::numeric_limits<uint64_t>::max(), 0,
                             0xfedcba9876543210};
  size_t next = 0;
  auto random = [&]() { return Some(values[next++]); };

  nsCString padding;
  ASSERT_EQ(GenerateHeaderPadding(padding, random), NS_OK);
  EXPECT_EQ(padding, "!#$()+<>?@[]^`{}");
  EXPECT_EQ(next, std::size(values));
}

TEST(NaiveFoxHeaderPadding, PreservesOutputOnRandomFailure)
{
  nsCString padding("unchanged");
  auto failImmediately = []() -> Maybe<uint64_t> { return Nothing(); };
  EXPECT_EQ(GenerateHeaderPadding(padding, failImmediately), NS_ERROR_FAILURE);
  EXPECT_EQ(padding, "unchanged");

  size_t call = 0;
  auto failUniqueBits = [&]() -> Maybe<uint64_t> {
    return call++ == 0 ? Some(uint64_t{0}) : Nothing();
  };
  EXPECT_EQ(GenerateHeaderPadding(padding, failUniqueBits), NS_ERROR_FAILURE);
  EXPECT_EQ(padding, "unchanged");

  call = 0;
  auto failAfterRejectedEndpoint = [&]() -> Maybe<uint64_t> {
    return call++ == 0 ? Some(std::numeric_limits<uint64_t>::max()) : Nothing();
  };
  EXPECT_EQ(GenerateHeaderPadding(padding, failAfterRejectedEndpoint),
            NS_ERROR_FAILURE);
  EXPECT_EQ(padding, "unchanged");
}

TEST(NaiveFoxHeaderPadding, DiagnosticProfilesHaveExactDistinctMarkers)
{
  nsCString shortPadding;
  size_t shortRandomCalls = 0;
  auto shortRandom = [&]() {
    ++shortRandomCalls;
    return Some(uint64_t{0});
  };
  ASSERT_EQ(GenerateDiagnosticHeaderPadding(
                shortPadding, kDiagnosticHeaderPaddingShortLength, shortRandom),
            NS_OK);
  EXPECT_EQ(shortPadding, "~5");
  EXPECT_EQ(shortRandomCalls, 0U);
  EXPECT_EQ(DetectDiagnosticHeaderPaddingLength(shortPadding),
            kDiagnosticHeaderPaddingShortLength);

  const uint64_t values[] = {0, 0xfedcba9876543210};
  size_t next = 0;
  auto longRandom = [&]() { return Some(values[next++]); };
  nsCString longPadding;
  ASSERT_EQ(GenerateDiagnosticHeaderPadding(
                longPadding, kDiagnosticHeaderPaddingLongLength, longRandom),
            NS_OK);
  EXPECT_EQ(longPadding.Length(), kDiagnosticHeaderPaddingLongLength);
  EXPECT_TRUE(Substring(longPadding, 0, 2).EqualsLiteral("~6"));
  EXPECT_TRUE(std::all_of(longPadding.BeginReading() + 16,
                          longPadding.EndReading(),
                          [](char aCharacter) { return aCharacter == '~'; }));
  EXPECT_EQ(next, std::size(values));
  EXPECT_EQ(DetectDiagnosticHeaderPaddingLength(longPadding),
            kDiagnosticHeaderPaddingLongLength);
}

TEST(NaiveFoxHeaderPadding, DiagnosticProfilesRejectNearMisses)
{
  auto random = []() { return Some(uint64_t{0}); };
  nsCString padding("unchanged");
  EXPECT_EQ(GenerateDiagnosticHeaderPadding(padding, 0, random),
            NS_ERROR_INVALID_ARG);
  EXPECT_EQ(padding, "unchanged");
  EXPECT_EQ(GenerateDiagnosticHeaderPadding(padding, 32, random),
            NS_ERROR_INVALID_ARG);
  EXPECT_EQ(padding, "unchanged");

  EXPECT_EQ(DetectDiagnosticHeaderPaddingLength("~6"_ns), 0U);
  nsCString wrongLong;
  wrongLong.SetLength(kDiagnosticHeaderPaddingLongLength);
  std::fill(wrongLong.BeginWriting(),
            wrongLong.BeginWriting() + wrongLong.Length(), '~');
  wrongLong.SetCharAt('5', 1);
  EXPECT_EQ(DetectDiagnosticHeaderPaddingLength(wrongLong), 0U);
}

TEST(NaiveFoxHeaderPadding, OrdinaryPaddingCannotSignalDiagnosticProfile)
{
  for (uint64_t lengthBits = 0; lengthBits < 17; ++lengthBits) {
    const uint64_t values[] = {lengthBits, 0};
    size_t next = 0;
    auto random = [&]() { return Some(values[next++]); };
    nsCString padding;
    ASSERT_EQ(GenerateHeaderPadding(padding, random), NS_OK);
    EXPECT_EQ(DetectDiagnosticHeaderPaddingLength(padding), 0U);
  }
}

}  // namespace mozilla::naivefox
