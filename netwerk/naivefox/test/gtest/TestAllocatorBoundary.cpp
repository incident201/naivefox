/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "gtest/gtest.h"
#include "nsTArray.h"

extern "C" bool NaiveFoxRustAllocatorSmoke(
    nsTArray<nsTArray<uint8_t>>* aOutput);

TEST(NaiveFoxAllocator, NestedArraysCrossTheFFIBoundary)
{
  nsTArray<nsTArray<uint8_t>> arrays;
  arrays.AppendElement(nsTArray<uint8_t>{17, 34, 51});
  ASSERT_TRUE(NaiveFoxRustAllocatorSmoke(&arrays));
  ASSERT_EQ(arrays.Length(), 2U);
  const size_t lengths[] = {257, 4097};
  for (size_t i = 0; i < 2; ++i) {
    ASSERT_EQ(arrays[i].Length(), lengths[i]);
    for (size_t j = 0; j < lengths[i]; ++j) {
      ASSERT_EQ(arrays[i][j], static_cast<uint8_t>(j % 251));
    }
  }
  arrays.SetCapacity(64);
}
