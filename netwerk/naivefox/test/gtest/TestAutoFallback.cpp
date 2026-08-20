/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "AutoFallback.h"
#include "gtest/gtest.h"

namespace mozilla::naivefox {

namespace {

AutoFallbackState EligibleState() {
  return {ProxyProtocol::Auto,
          ProxyProtocol::H3,
          false,
          false,
          true,
          true,
          false,
          true,
          0,
          false};
}

}  // namespace

TEST(NaiveFoxAutoFallback, RetriesOnlyBeforeAProxyResponse)
{
  EXPECT_TRUE(ShouldRetryH2FromH3(EligibleState()));

  for (int32_t code : {200, 403, 407, 502, 504}) {
    auto state = EligibleState();
    state.connectCode = code;
    EXPECT_FALSE(ShouldRetryH2FromH3(state));
  }
}

TEST(NaiveFoxAutoFallback, RequiresTerminalOuterH3Failure)
{
  auto state = EligibleState();
  state.channelFailed = false;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));

  state = EligibleState();
  state.channelStopped = false;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));

  state = EligibleState();
  state.connectCodeKnown = false;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));

  state.establishmentTimedOut = true;
  EXPECT_TRUE(ShouldRetryH2FromH3(state));

  state = EligibleState();
  state.transportReady = true;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));
}

TEST(NaiveFoxAutoFallback, IsSingleUseAndAutoOnly)
{
  auto state = EligibleState();
  state.fallbackUsed = true;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));

  state = EligibleState();
  state.ownerClosed = true;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));

  state = EligibleState();
  state.requestedProtocol = ProxyProtocol::H3;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));

  state = EligibleState();
  state.actualProtocol = ProxyProtocol::H2;
  EXPECT_FALSE(ShouldRetryH2FromH3(state));
}

}  // namespace mozilla::naivefox
