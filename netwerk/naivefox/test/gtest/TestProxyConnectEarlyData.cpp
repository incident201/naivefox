/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <algorithm>
#include <iterator>

#include "gtest/gtest.h"
#include "nsHttpRequestHead.h"

namespace mozilla::naivefox {

TEST(NaiveFoxProxyConnectEarlyData, HiddenPayloadSurvivesRequestHeadCopies)
{
  const uint8_t payload[] = {0x16, 0x03, 0x01, 0x00, 0x04,
                             0xde, 0xad, 0xbe, 0xef};
  net::nsHttpRequestHead head;
  ASSERT_EQ(head.SetNaiveFoxProxyConnectEarlyData(payload), NS_OK);

  nsTArray<uint8_t> copied;
  head.CopyNaiveFoxProxyConnectEarlyData(copied);
  ASSERT_EQ(copied.Length(), std::size(payload));
  EXPECT_TRUE(std::equal(copied.begin(), copied.end(), std::begin(payload)));

  net::nsHttpRequestHead cloned(head);
  copied.Clear();
  cloned.CopyNaiveFoxProxyConnectEarlyData(copied);
  ASSERT_EQ(copied.Length(), std::size(payload));
  EXPECT_TRUE(std::equal(copied.begin(), copied.end(), std::begin(payload)));
}

TEST(NaiveFoxProxyConnectEarlyData, EmptyPayloadIsRejected)
{
  net::nsHttpRequestHead head;
  EXPECT_EQ(head.SetNaiveFoxProxyConnectEarlyData(Span<const uint8_t>()),
            NS_ERROR_INVALID_ARG);
}

TEST(NaiveFoxProxyConnectEarlyData, OversizedPayloadIsRejected)
{
  nsTArray<uint8_t> payload;
  payload.SetLength(64 * 1024 + 1);
  net::nsHttpRequestHead head;
  EXPECT_EQ(head.SetNaiveFoxProxyConnectEarlyData(payload),
            NS_ERROR_INVALID_ARG);
}

}  // namespace mozilla::naivefox
