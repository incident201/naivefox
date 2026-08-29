/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "PaddingNegotiation.h"
#include "gtest/gtest.h"
#include "mozilla/Maybe.h"
#include "mozilla/gtest/MozAssertions.h"

namespace mozilla::naivefox {

TEST(NaiveFoxPaddingNegotiation, PresentEnablesPadding)
{
  bool enabled = false;
  EXPECT_NS_SUCCEEDED(NegotiatePayloadPadding(200, Some(true), enabled));
  EXPECT_TRUE(enabled);
}

TEST(NaiveFoxPaddingNegotiation, AbsentKeepsRawMode)
{
  bool enabled = true;
  EXPECT_NS_SUCCEEDED(NegotiatePayloadPadding(200, Some(false), enabled));
  EXPECT_FALSE(enabled);
}

TEST(NaiveFoxPaddingNegotiation, FailedMetadataOrConnectFails)
{
  bool enabled = true;
  EXPECT_NS_FAILED(NegotiatePayloadPadding(407, Some(true), enabled));
  EXPECT_FALSE(enabled);
  EXPECT_NS_SUCCEEDED(NegotiatePayloadPadding(200, Nothing(), enabled));
  EXPECT_FALSE(enabled);
}

}  // namespace mozilla::naivefox
