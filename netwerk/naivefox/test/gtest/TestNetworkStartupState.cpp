/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "gtest/gtest.h"

#if defined(XP_LINUX) || defined(ANDROID)
#  include "NetlinkService.h"

namespace mozilla::net {

TEST(NaiveFoxNetworkStartupState, OnlyPendingCanTransition) {
  EXPECT_EQ(AdvanceInitialNetworkState(InitialNetworkState::Pending,
                                       InitialNetworkState::Ready),
            InitialNetworkState::Ready);
  EXPECT_EQ(AdvanceInitialNetworkState(InitialNetworkState::Pending,
                                       InitialNetworkState::Failed),
            InitialNetworkState::Failed);

  EXPECT_EQ(AdvanceInitialNetworkState(InitialNetworkState::Ready,
                                       InitialNetworkState::Failed),
            InitialNetworkState::Ready);
  EXPECT_EQ(AdvanceInitialNetworkState(InitialNetworkState::Failed,
                                       InitialNetworkState::Ready),
            InitialNetworkState::Failed);
}

TEST(NaiveFoxNetworkStartupState, FailsClosed) {
  EXPECT_FALSE(InitialNetworkStateIsTerminal(InitialNetworkState::Pending));
  EXPECT_TRUE(InitialNetworkStateIsTerminal(InitialNetworkState::Ready));
  EXPECT_TRUE(InitialNetworkStateIsTerminal(InitialNetworkState::Failed));

  EXPECT_FALSE(InitialNetworkStateAllowsStartup(InitialNetworkState::Pending,
                                                false));
  EXPECT_TRUE(
      InitialNetworkStateAllowsStartup(InitialNetworkState::Ready, false));
  EXPECT_FALSE(
      InitialNetworkStateAllowsStartup(InitialNetworkState::Failed, false));
}

TEST(NaiveFoxNetworkStartupState, AllowsUnavailableAndroidMonitor) {
  EXPECT_FALSE(InitialNetworkStateAllowsStartup(InitialNetworkState::Pending,
                                                true));
  EXPECT_TRUE(
      InitialNetworkStateAllowsStartup(InitialNetworkState::Ready, true));
  EXPECT_TRUE(
      InitialNetworkStateAllowsStartup(InitialNetworkState::Failed, true));
}

}  // namespace mozilla::net
#endif
