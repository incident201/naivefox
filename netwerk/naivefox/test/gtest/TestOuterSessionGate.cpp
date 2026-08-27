/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <vector>

#include "OuterSessionGate.h"
#include "gtest/gtest.h"

namespace mozilla::naivefox {

TEST(NaiveFoxOuterSessionGate, LeaderQueuesAndReadyReleasesAll)
{
  OuterSessionGate gate;
  std::vector<uint64_t> opened;
  const nsLiteralCString route = "route-ready"_ns;

  EXPECT_EQ(gate.Enter(route, 1, [&]() { opened.push_back(1); }),
            OuterSessionGate::Admission::Leader);
  EXPECT_EQ(gate.Enter(route, 2, [&]() { opened.push_back(2); }),
            OuterSessionGate::Admission::Queued);
  EXPECT_EQ(gate.Enter(route, 3, [&]() { opened.push_back(3); }),
            OuterSessionGate::Admission::Queued);
  EXPECT_TRUE(opened.empty());
  EXPECT_EQ(gate.LeaderForTesting(route), 1U);

  gate.MarkReady(route, 1);
  EXPECT_EQ(opened, (std::vector<uint64_t>{2, 3}));
  EXPECT_TRUE(gate.RouteReadyForTesting(route));
  EXPECT_EQ(gate.Enter(route, 4, [&]() { opened.push_back(4); }),
            OuterSessionGate::Admission::Warm);
  EXPECT_EQ(gate.ParticipantCountForTesting(route), 4U);

  gate.Leave(route, 1);
  gate.Leave(route, 2);
  gate.Leave(route, 3);
  EXPECT_EQ(gate.RouteCountForTesting(), 1U);
  gate.Leave(route, 4);
  EXPECT_EQ(gate.RouteCountForTesting(), 1U);

  EXPECT_EQ(gate.Enter(route, 5, [&]() { opened.push_back(5); }),
            OuterSessionGate::Admission::Warm);
  EXPECT_TRUE(gate.RouteReadyForTesting(route));
  gate.Leave(route, 5);
}

TEST(NaiveFoxOuterSessionGate, LeaderFailurePromotesOneWaiter)
{
  OuterSessionGate gate;
  std::vector<uint64_t> opened;
  const nsLiteralCString route = "route-promote"_ns;

  EXPECT_EQ(gate.Enter(route, 10, [&]() { opened.push_back(10); }),
            OuterSessionGate::Admission::Leader);
  EXPECT_EQ(gate.Enter(route, 11, [&]() { opened.push_back(11); }),
            OuterSessionGate::Admission::Queued);
  EXPECT_EQ(gate.Enter(route, 12, [&]() { opened.push_back(12); }),
            OuterSessionGate::Admission::Queued);

  gate.Leave(route, 10);
  EXPECT_EQ(opened, (std::vector<uint64_t>{11}));
  EXPECT_EQ(gate.LeaderForTesting(route), 11U);
  EXPECT_FALSE(gate.RouteReadyForTesting(route));

  gate.MarkReady(route, 11);
  EXPECT_EQ(opened, (std::vector<uint64_t>{11, 12}));
  EXPECT_TRUE(gate.RouteReadyForTesting(route));
  gate.Leave(route, 11);
  gate.Leave(route, 12);
  EXPECT_EQ(gate.RouteCountForTesting(), 1U);
}

TEST(NaiveFoxOuterSessionGate, QueuedCancellationIsNotReleasedOrPromoted)
{
  OuterSessionGate gate;
  std::vector<uint64_t> opened;
  const nsLiteralCString route = "route-cancel"_ns;

  EXPECT_EQ(gate.Enter(route, 20, [&]() { opened.push_back(20); }),
            OuterSessionGate::Admission::Leader);
  EXPECT_EQ(gate.Enter(route, 21, [&]() { opened.push_back(21); }),
            OuterSessionGate::Admission::Queued);
  EXPECT_EQ(gate.Enter(route, 22, [&]() { opened.push_back(22); }),
            OuterSessionGate::Admission::Queued);

  gate.Leave(route, 21);
  gate.Leave(route, 20);
  EXPECT_EQ(opened, (std::vector<uint64_t>{22}));
  EXPECT_EQ(gate.LeaderForTesting(route), 22U);
  gate.Leave(route, 22);
  EXPECT_EQ(gate.RouteCountForTesting(), 0U);
}

TEST(NaiveFoxOuterSessionGate, RoutesAreIndependent)
{
  OuterSessionGate gate;
  EXPECT_EQ(gate.Enter("route-a"_ns, 30, []() {}),
            OuterSessionGate::Admission::Leader);
  EXPECT_EQ(gate.Enter("route-b"_ns, 31, []() {}),
            OuterSessionGate::Admission::Leader);
  EXPECT_EQ(gate.RouteCountForTesting(), 2U);
  gate.Leave("route-a"_ns, 30);
  gate.Leave("route-b"_ns, 31);
}

TEST(NaiveFoxOuterSessionGate, PromotedLeaderMayCancelReentrantly)
{
  OuterSessionGate gate;
  std::vector<uint64_t> opened;
  const nsLiteralCString route = "route-reentrant-cancel"_ns;

  EXPECT_EQ(gate.Enter(route, 40, []() {}),
            OuterSessionGate::Admission::Leader);
  EXPECT_EQ(gate.Enter(route, 41,
                       [&]() {
                         opened.push_back(41);
                         gate.Leave(route, 41);
                       }),
            OuterSessionGate::Admission::Queued);
  EXPECT_EQ(gate.Enter(route, 42, [&]() { opened.push_back(42); }),
            OuterSessionGate::Admission::Queued);

  gate.Leave(route, 40);
  EXPECT_EQ(opened, (std::vector<uint64_t>{41, 42}));
  EXPECT_EQ(gate.LeaderForTesting(route), 42U);
  EXPECT_EQ(gate.ParticipantCountForTesting(route), 1U);
  gate.Leave(route, 42);
  EXPECT_EQ(gate.RouteCountForTesting(), 0U);
}

}  // namespace mozilla::naivefox
