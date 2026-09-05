/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <algorithm>
#include <array>
#include <limits>
#include <utility>
#include <vector>

#include "NoConnectCodec.h"
#include "gtest/gtest.h"
#include "nss.h"

namespace mozilla::naivefox::noconnect {
namespace {

class NaiveFoxNoConnectCodec : public ::testing::Test {
 protected:
  static void SetUpTestSuite() {
    sOwnsNSS = !NSS_IsInitialized();
    if (sOwnsNSS) {
      ASSERT_EQ(NSS_NoDB_Init(nullptr), SECSuccess);
    }
  }

  static void TearDownTestSuite() {
    if (sOwnsNSS) {
      EXPECT_EQ(NSS_Shutdown(), SECSuccess);
    }
  }

 private:
  static bool sOwnsNSS;
};

bool NaiveFoxNoConnectCodec::sOwnsNSS = false;

void ExpectFrames(const std::vector<Frame>& aActual,
                  const std::vector<Frame>& aExpected) {
  ASSERT_EQ(aActual.size(), aExpected.size());
  for (size_t i = 0; i < aExpected.size(); ++i) {
    EXPECT_EQ(aActual[i].kind, aExpected[i].kind);
    EXPECT_EQ(aActual[i].stream, aExpected[i].stream);
    EXPECT_EQ(aActual[i].sequence, aExpected[i].sequence);
    EXPECT_EQ(aActual[i].body, aExpected[i].body);
  }
}

Frame Credit(uint32_t aStream, uint32_t aAmount) {
  return {
      Kind::Credit,
      aStream,
      0,
      {static_cast<uint8_t>(aAmount >> 24), static_cast<uint8_t>(aAmount >> 16),
       static_cast<uint8_t>(aAmount >> 8), static_cast<uint8_t>(aAmount)}};
}

TEST_F(NaiveFoxNoConnectCodec, IndependentWireFixture) {
  const std::vector<Frame> frames{
      {Kind::Data, 0x01020304, 0xaabbccdd, {0x00, 0x7f, 0xff}}};
  const std::vector<uint8_t> fixture{
      'N',  'F',  'C',  '1',  0x89, 0xab, 0xcd, 0xef, 0x00, 0x00, 0x00, 0x23,
      0x00, 0x01, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
      0xaa, 0xbb, 0xcc, 0xdd, 0x00, 0x00, 0x00, 0x03, 0x00, 0x7f, 0xff};
  std::vector<uint8_t> encoded;
  ASSERT_TRUE(Encode(0x89abcdef, fixture.size(), frames, encoded));
  EXPECT_EQ(encoded, fixture);
  std::vector<Frame> decoded;
  ASSERT_TRUE(Decode(0x89abcdef, fixture.size(), fixture, decoded));
  ExpectFrames(decoded, frames);
}

TEST_F(NaiveFoxNoConnectCodec, EmptyMaximumAndFrameCountBoundaries) {
  std::vector<uint8_t> encoded;
  std::vector<Frame> decoded;
  ASSERT_TRUE(Encode(0, kCellHeader, {}, encoded));
  ASSERT_TRUE(Decode(0, kCellHeader, encoded, decoded));
  EXPECT_TRUE(decoded.empty());

  const std::vector<Frame> maximum{
      {Kind::Data, 1, 0,
       std::vector<uint8_t>(kMaxCell - kCellHeader - kFrameHeader, 0x5a)}};
  ASSERT_TRUE(Encode(UINT32_MAX, kMaxCell, maximum, encoded));
  ASSERT_TRUE(Decode(UINT32_MAX, kMaxCell, encoded, decoded));
  ExpectFrames(decoded, maximum);
  EXPECT_FALSE(Encode(0, kMaxCell - 1, maximum, encoded));

  std::vector<Frame> count(kMaxFrames, Frame{Kind::Reset, 1, 0, {}});
  ASSERT_TRUE(
      Encode(0, kCellHeader + count.size() * kFrameHeader, count, encoded));
  ASSERT_TRUE(Decode(0, encoded.size(), encoded, decoded));
  ExpectFrames(decoded, count);
  count.push_back({Kind::Reset, 1, 0, {}});
  EXPECT_FALSE(Encode(0, kMaxCell, count, encoded));
  EXPECT_FALSE(Encode(0, kCellHeader - 1, {}, encoded));
  EXPECT_FALSE(Encode(0, kMaxCell + 1, {}, encoded));
  EXPECT_FALSE(Decode(0, kCellHeader - 1, {}, decoded));
  EXPECT_FALSE(Decode(0, kMaxCell + 1, {}, decoded));
}

TEST_F(NaiveFoxNoConnectCodec, AllKindsAndPadding) {
  std::vector<Frame> frames;
  for (uint8_t kind = 1; kind <= 8; ++kind) {
    frames.push_back({static_cast<Kind>(kind), 0, 0, {}});
  }
  std::vector<uint8_t> encoded;
  ASSERT_TRUE(Encode(11, 512, frames, encoded));
  std::vector<Frame> decoded;
  ASSERT_TRUE(Decode(11, 512, encoded, decoded));
  ExpectFrames(decoded, frames);
  std::fill(encoded.begin() + kCellHeader + 8 * kFrameHeader, encoded.end(),
            0xff);
  ASSERT_TRUE(Decode(11, 512, encoded, decoded));
  ExpectFrames(decoded, frames);
  for (uint8_t kind : {uint8_t{0}, uint8_t{9}, uint8_t{255}}) {
    EXPECT_FALSE(Encode(0, 32, {{static_cast<Kind>(kind), 1, 0, {}}}, encoded));
  }
}

TEST_F(NaiveFoxNoConnectCodec, ReadyWebSocketCapacityBoundsFiller) {
  EXPECT_EQ(ReadyRealtimeUpCapacity(0, false), 512U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(0, true), 4096U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(1, false), 4096U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(8191, false), 4096U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(8192, false), 16384U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(65535, false), 16384U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(65536, false), 131072U);
  EXPECT_EQ(ReadyRealtimeUpCapacity(kReceiveWindow, false), 131072U);
  for (size_t bytes = 2048; bytes <= kReceiveWindow; ++bytes) {
    const size_t capacity = ReadyRealtimeUpCapacity(bytes, false);
    ASSERT_TRUE(ValidRealtimeUpCapacity(capacity));
    ASSERT_LE(capacity, 2 * bytes);
  }
}

TEST_F(NaiveFoxNoConnectCodec, WebSocketAckIsNotAStreamMessage) {
  std::vector<uint8_t> cell;
  ASSERT_TRUE(Encode(20, 512, {{Kind::Ack, 0, 23, {}}}, cell));
  std::vector<Frame> frames;
  ASSERT_TRUE(Decode(20, 512, cell, frames));
  ASSERT_EQ(frames.size(), 1U);
  EXPECT_EQ(frames[0].sequence, 23U);
  StreamState state(1);
  EXPECT_FALSE(state.Receive(frames[0]));
}

TEST_F(NaiveFoxNoConnectCodec, DirectionalWebSocketCapacities) {
  for (size_t capacity : {512U, 4096U, 16384U, 131072U}) {
    EXPECT_TRUE(ValidRealtimeUpCapacity(capacity));
  }
  for (size_t capacity : {512U, 8192U, 65536U, 262144U}) {
    EXPECT_TRUE(ValidRealtimeDownCapacity(capacity));
  }
  EXPECT_FALSE(ValidRealtimeUpCapacity(65536));
  EXPECT_FALSE(ValidRealtimeDownCapacity(16384));
}

TEST_F(NaiveFoxNoConnectCodec, EmptyAndPartFilledProfileCapacities) {
  for (size_t capacity : {4096U, 8192U, 32768U, 65536U, 131072U, 262144U}) {
    for (bool payload : {false, true}) {
      std::vector<Frame> frames;
      if (payload) {
        frames.push_back(
            {Kind::Data, 1, 0, std::vector<uint8_t>(capacity / 3, 0x5a)});
      }
      std::vector<uint8_t> first;
      std::vector<uint8_t> second;
      ASSERT_TRUE(Encode(11, capacity, frames, first))
      << capacity << payload;
      ASSERT_TRUE(Encode(11, capacity, frames, second))
      << capacity << payload;
      EXPECT_NE(first, second);
      std::vector<Frame> decoded;
      ASSERT_TRUE(Decode(11, capacity, first, decoded));
      ExpectFrames(decoded, frames);
      ASSERT_TRUE(Decode(11, capacity, second, decoded));
      ExpectFrames(decoded, frames);
    }
  }
}

TEST_F(NaiveFoxNoConnectCodec, EveryTruncationAndExcessByteRejected) {
  std::vector<uint8_t> encoded;
  ASSERT_TRUE(Encode(7, 128, {{Kind::Data, 1, 0, {1, 2, 3}}}, encoded));
  const std::vector<Frame> sentinel{{Kind::Reset, 99, 0, {}}};
  for (size_t n = 0; n < encoded.size(); ++n) {
    std::vector<uint8_t> shortBody(encoded.begin(), encoded.begin() + n);
    auto decoded = sentinel;
    EXPECT_FALSE(Decode(7, 128, shortBody, decoded)) << n;
    ExpectFrames(decoded, sentinel);
  }
  auto decoded = sentinel;
  EXPECT_FALSE(Decode(8, 128, encoded, decoded));
  EXPECT_FALSE(Decode(7, 127, encoded, decoded));
  encoded.push_back(0);
  EXPECT_FALSE(Decode(7, 128, encoded, decoded));
  ExpectFrames(decoded, sentinel);
}

TEST_F(NaiveFoxNoConnectCodec, CorruptHeadersNeverPublishPartialFrames) {
  std::vector<uint8_t> encoded;
  ASSERT_TRUE(Encode(
      7, 128, {{Kind::Data, 1, 0, {1, 2, 3}}, {Kind::Fin, 1, 3, {}}}, encoded));
  const std::vector<Frame> sentinel{{Kind::Reset, 99, 0, {}}};
  const std::vector<std::pair<size_t, uint8_t>> mutations{
      {0, 0},  {7, 8},  {8, 1},  {11, 15},  {11, 52}, {12, 17},
      {13, 0}, {13, 3}, {14, 1}, {15, 1},   {16, 0},  {16, 9},
      {17, 1}, {18, 1}, {19, 1}, {28, 255}, {31, 4},  {35, 9},
      {36, 1}, {37, 1}, {38, 1}, {47, 255}, {50, 1}};
  for (const auto& [offset, value] : mutations) {
    auto bad = encoded;
    bad[offset] = value;
    auto decoded = sentinel;
    EXPECT_FALSE(Decode(7, 128, bad, decoded)) << offset;
    ExpectFrames(decoded, sentinel);
  }
  auto original = encoded;
  EXPECT_FALSE(Encode(7, 32, {{Kind::Data, 1, 0, {1}}}, encoded));
  EXPECT_EQ(encoded, original);
}

TEST_F(NaiveFoxNoConnectCodec, SendCreditTracksBytesAndRejectsInflation) {
  StreamState stream(3);
  std::vector<uint8_t> bytes(kReceiveWindow / 4, 0x7f);
  Frame frame;
  for (uint32_t i = 0; i < 4; ++i) {
    ASSERT_TRUE(stream.MakeData(bytes.data(), bytes.size(), frame));
    EXPECT_EQ(frame.sequence, i * bytes.size());
    EXPECT_EQ(frame.body, bytes);
  }
  EXPECT_EQ(stream.SendCredit(), 0U);
  EXPECT_EQ(stream.SendSequence(), kReceiveWindow);
  EXPECT_FALSE(stream.MakeData(bytes.data(), 1, frame));
  EXPECT_FALSE(stream.IsReset());
  ASSERT_TRUE(stream.Receive(Credit(3, 17)));
  EXPECT_EQ(stream.SendCredit(), 17U);
  ASSERT_TRUE(stream.MakeData(bytes.data(), 17, frame));
  EXPECT_EQ(frame.sequence, kReceiveWindow);
  EXPECT_FALSE(stream.Receive(Credit(3, kReceiveWindow + 1)));
  EXPECT_TRUE(stream.IsReset());
  EXPECT_FALSE(stream.MakeData(bytes.data(), 1, frame));
}

TEST_F(NaiveFoxNoConnectCodec, ReceiveCreditRequiresActualConsumerDelivery) {
  StreamState stream(4);
  ASSERT_TRUE(stream.Receive({Kind::Opened, 4, 0, {}}));
  Frame data{Kind::Data, 4, 0, std::vector<uint8_t>(kReceiveWindow / 4, 0x41)};
  for (uint32_t i = 0; i < 4; ++i) {
    data.sequence = i * data.body.size();
    ASSERT_TRUE(stream.Receive(data));
  }
  EXPECT_EQ(stream.ReceiveCredit(), 0U);
  EXPECT_EQ(stream.BufferedBytes(), kReceiveWindow);
  Frame credit;
  EXPECT_FALSE(stream.TakeCredit(credit));
  ASSERT_TRUE(stream.Delivered(123));
  EXPECT_EQ(stream.ReceiveCredit(), 0U);
  EXPECT_EQ(stream.BufferedBytes(), kReceiveWindow - 123);
  EXPECT_EQ(stream.PendingCredit(), 123U);
  ASSERT_TRUE(stream.TakeCredit(credit));
  EXPECT_EQ(credit.kind, Kind::Credit);
  EXPECT_EQ(credit.body, Credit(4, 123).body);
  EXPECT_EQ(stream.ReceiveCredit(), 123U);
  EXPECT_EQ(stream.PendingCredit(), 0U);
  EXPECT_FALSE(stream.TakeCredit(credit));
  ASSERT_TRUE(stream.Receive({Kind::Data, 4, kReceiveWindow, {0x42}}));
  EXPECT_EQ(stream.ReceiveCredit(), 122U);
  EXPECT_FALSE(stream.Delivered(kReceiveWindow));
  EXPECT_TRUE(stream.IsReset());
}

TEST_F(NaiveFoxNoConnectCodec, UncreditedDataFailsClosed) {
  StreamState stream(1);
  ASSERT_TRUE(stream.Receive({Kind::Opened, 1, 0, {}}));
  Frame data{Kind::Data, 1, 0, std::vector<uint8_t>(kReceiveWindow / 4, 0x41)};
  for (uint32_t i = 0; i < 4; ++i) {
    data.sequence = i * data.body.size();
    ASSERT_TRUE(stream.Receive(data));
  }
  ASSERT_TRUE(stream.Delivered(1));
  EXPECT_FALSE(stream.Receive({Kind::Data, 1, kReceiveWindow, {0x42}}));
  EXPECT_TRUE(stream.IsReset());
}

TEST_F(NaiveFoxNoConnectCodec,
       HalfClosePreservesOtherDirectionAndBufferedData) {
  StreamState stream(9);
  ASSERT_TRUE(stream.Receive({Kind::Opened, 9, 0, {}}));
  ASSERT_TRUE(stream.Receive({Kind::Data, 9, 0, {1, 2}}));
  ASSERT_TRUE(stream.Receive({Kind::Fin, 9, 2, {}}));
  EXPECT_TRUE(stream.ReceivedFin());
  EXPECT_FALSE(stream.SentFin());
  Frame frame;
  const std::array<uint8_t, 3> upload{3, 4, 5};
  ASSERT_TRUE(stream.MakeData(upload.data(), upload.size(), frame));
  ASSERT_TRUE(stream.MakeFin(frame));
  EXPECT_EQ(frame.sequence, 3U);
  EXPECT_FALSE(stream.MakeFin(frame));
  EXPECT_FALSE(stream.MakeData(upload.data(), 1, frame));
  EXPECT_FALSE(stream.IsDrained());
  ASSERT_TRUE(stream.Delivered(1));
  ASSERT_TRUE(stream.Delivered(1));
  EXPECT_FALSE(stream.IsDrained());
  ASSERT_TRUE(stream.TakeCredit(frame));
  EXPECT_TRUE(stream.IsDrained());

  StreamState localFirst(10);
  ASSERT_TRUE(localFirst.Receive({Kind::Opened, 10, 0, {}}));
  ASSERT_TRUE(localFirst.MakeFin(frame));
  ASSERT_TRUE(localFirst.Receive({Kind::Data, 10, 0, {0x42}}));
  ASSERT_TRUE(localFirst.Receive({Kind::Fin, 10, 1, {}}));
  EXPECT_FALSE(localFirst.IsDrained());
}

TEST_F(NaiveFoxNoConnectCodec, StreamControlAndSequenceValidation) {
  const std::vector<Frame> malformed{{Kind::Opened, 1, 1, {}},
                                     {Kind::Opened, 1, 0, {1}},
                                     {Kind::Data, 1, 0, {}},
                                     {Kind::Data, 1, 1, {1}},
                                     {Kind::Fin, 1, 0, {1}},
                                     {Kind::Fin, 1, 1, {}},
                                     {Kind::Reset, 1, 1, {}},
                                     {Kind::Reset, 1, 0, {1}},
                                     {Kind::Credit, 1, 1, {0, 0, 0, 1}},
                                     {Kind::Credit, 1, 0, {0, 0, 1}},
                                     {Kind::Credit, 1, 0, {0, 0, 0, 0}},
                                     {Kind::Credit, 1, 0, {0, 0, 0, 1}},
                                     {Kind::Open, 1, 0, {}},
                                     {Kind::Auth, 0, 0, {}},
                                     {Kind::Data, 2, 0, {1}}};
  for (const Frame& frame : malformed) {
    StreamState stream(1);
    ASSERT_TRUE(stream.Receive({Kind::Opened, 1, 0, {}}));
    EXPECT_FALSE(stream.Receive(frame)) << static_cast<int>(frame.kind);
    EXPECT_TRUE(stream.IsReset());
    EXPECT_FALSE(stream.Receive({Kind::Data, 1, 0, {1}}));
  }

  StreamState early(1);
  EXPECT_FALSE(early.Receive({Kind::Data, 1, 0, {1}}));
  StreamState duplicate(1);
  ASSERT_TRUE(duplicate.Receive({Kind::Opened, 1, 0, {}}));
  EXPECT_FALSE(duplicate.Receive({Kind::Opened, 1, 0, {}}));
  StreamState finished(1);
  ASSERT_TRUE(finished.Receive({Kind::Opened, 1, 0, {}}));
  ASSERT_TRUE(finished.Receive({Kind::Fin, 1, 0, {}}));
  EXPECT_FALSE(finished.Receive({Kind::Data, 1, 0, {1}}));
  StreamState twiceFinished(1);
  ASSERT_TRUE(twiceFinished.Receive({Kind::Opened, 1, 0, {}}));
  ASSERT_TRUE(twiceFinished.Receive({Kind::Fin, 1, 0, {}}));
  EXPECT_FALSE(twiceFinished.Receive({Kind::Fin, 1, 0, {}}));
}

TEST_F(NaiveFoxNoConnectCodec, ResetIsTerminalAndMayRejectOpen) {
  StreamState stream(1);
  ASSERT_TRUE(stream.Receive({Kind::Reset, 1, 0, {}}));
  EXPECT_TRUE(stream.IsReset());
  EXPECT_TRUE(stream.IsDrained());
  Frame frame;
  EXPECT_FALSE(stream.MakeReset(frame));
  EXPECT_FALSE(stream.MakeFin(frame));
  EXPECT_FALSE(stream.Receive({Kind::Opened, 1, 0, {}}));

  StreamState failed(1);
  EXPECT_FALSE(failed.Receive({Kind::Data, 1, 0, {1}}));
  ASSERT_TRUE(failed.MakeReset(frame));
  EXPECT_EQ(frame.kind, Kind::Reset);
  EXPECT_EQ(frame.stream, 1U);
  EXPECT_EQ(frame.sequence, 0U);
  EXPECT_TRUE(frame.body.empty());
  EXPECT_FALSE(failed.MakeReset(frame));

  StreamState zero(0);
  EXPECT_TRUE(zero.IsReset());
  EXPECT_FALSE(zero.MakeReset(frame));
}

TEST_F(NaiveFoxNoConnectCodec, ReceiveSequenceWrapsWithExactDataAndFinOffsets) {
  StreamState stream(1);
  ASSERT_TRUE(stream.Receive({Kind::Opened, 1, 0, {}}));
  Frame data{Kind::Data, 1, 0,
             std::vector<uint8_t>(kMaxCell - kCellHeader - kFrameHeader, 0x41)};
  const uint64_t boundary = uint64_t{1} << 32;
  uint64_t received = 0;
  Frame credit;
  while (received < boundary - 3) {
    const uint32_t length = static_cast<uint32_t>(
        std::min<uint64_t>(data.body.size(), boundary - 3 - received));
    data.body.resize(length);
    data.sequence = static_cast<uint32_t>(received);
    ASSERT_TRUE(stream.Receive(data));
    ASSERT_TRUE(stream.Delivered(length));
    ASSERT_TRUE(stream.TakeCredit(credit));
    received += length;
  }
  ASSERT_TRUE(stream.Receive({Kind::Data,
                              1,
                              static_cast<uint32_t>(received),
                              {1, 2, 3, 4, 5, 6, 7, 8}}));
  received += 8;
  EXPECT_GT(received, boundary);
  EXPECT_EQ(stream.ReceiveSequence(), 5U);
  EXPECT_EQ(stream.ReceiveCredit(), kReceiveWindow - 8);
  EXPECT_EQ(stream.BufferedBytes(), 8U);
  EXPECT_FALSE(stream.IsReset());

  StreamState outOfOrder(stream);
  EXPECT_FALSE(outOfOrder.Receive({Kind::Data, 1, 0, {9}}));
  EXPECT_TRUE(outOfOrder.IsReset());
  StreamState oldOffset(stream);
  EXPECT_FALSE(oldOffset.Receive(
      {Kind::Data, 1, std::numeric_limits<uint32_t>::max() - 2, {9}}));
  StreamState wrongFin(stream);
  EXPECT_FALSE(wrongFin.Receive({Kind::Fin, 1, 0, {}}));

  ASSERT_TRUE(stream.Delivered(3));
  EXPECT_EQ(stream.ReceiveCredit(), kReceiveWindow - 8);
  ASSERT_TRUE(stream.TakeCredit(credit));
  EXPECT_EQ(stream.ReceiveCredit(), kReceiveWindow - 5);
  ASSERT_TRUE(stream.Receive({Kind::Data, 1, 5, {9, 10}}));
  EXPECT_EQ(stream.ReceiveSequence(), 7U);
  ASSERT_TRUE(stream.Receive({Kind::Fin, 1, 7, {}}));
  ASSERT_TRUE(stream.MakeFin(credit));
  EXPECT_FALSE(stream.IsDrained());
  ASSERT_TRUE(stream.Delivered(7));
  ASSERT_TRUE(stream.TakeCredit(credit));
  EXPECT_EQ(stream.ReceiveCredit(), kReceiveWindow);
  EXPECT_TRUE(stream.IsDrained());
}

TEST_F(NaiveFoxNoConnectCodec, SendSequenceWrapsWithoutResettingCreditOrFin) {
  StreamState stream(1);
  const std::vector<uint8_t> data(kMaxCell - kCellHeader - kFrameHeader, 0x41);
  const uint64_t boundary = uint64_t{1} << 32;
  uint64_t sent = 0;
  Frame frame;
  while (sent < boundary - 3) {
    const uint32_t length = static_cast<uint32_t>(
        std::min<uint64_t>(data.size(), boundary - 3 - sent));
    ASSERT_TRUE(stream.MakeData(data.data(), length, frame));
    ASSERT_EQ(frame.sequence, static_cast<uint32_t>(sent));
    ASSERT_TRUE(stream.Receive(Credit(1, length)));
    sent += length;
  }
  ASSERT_TRUE(stream.MakeData(data.data(), 8, frame));
  EXPECT_EQ(frame.sequence, std::numeric_limits<uint32_t>::max() - 2);
  sent += 8;
  EXPECT_GT(sent, boundary);
  EXPECT_EQ(stream.SendSequence(), 5U);
  EXPECT_EQ(stream.SendCredit(), kReceiveWindow - 8);
  EXPECT_FALSE(stream.IsReset());
  ASSERT_TRUE(stream.Receive(Credit(1, 8)));
  ASSERT_TRUE(stream.MakeData(data.data(), 2, frame));
  EXPECT_EQ(frame.sequence, 5U);
  EXPECT_EQ(stream.SendSequence(), 7U);
  EXPECT_EQ(stream.SendCredit(), kReceiveWindow - 2);
  ASSERT_TRUE(stream.MakeFin(frame));
  EXPECT_EQ(frame.kind, Kind::Fin);
  EXPECT_EQ(frame.sequence, 7U);
  EXPECT_FALSE(stream.MakeData(data.data(), 1, frame));
  EXPECT_FALSE(stream.IsReset());
}

TEST_F(NaiveFoxNoConnectCodec, ReceiveWindowRemainsBoundedAcrossSequenceWrap) {
  StreamState stream(1);
  ASSERT_TRUE(stream.Receive({Kind::Opened, 1, 0, {}}));
  Frame data{Kind::Data, 1, 0, std::vector<uint8_t>(kReceiveWindow / 4, 0x41)};
  const uint64_t boundary = uint64_t{1} << 32;
  uint64_t received = 0;
  Frame credit;
  while (received < boundary - data.body.size()) {
    data.sequence = static_cast<uint32_t>(received);
    ASSERT_TRUE(stream.Receive(data));
    ASSERT_TRUE(stream.Delivered(data.body.size()));
    ASSERT_TRUE(stream.TakeCredit(credit));
    received += data.body.size();
  }
  for (unsigned i = 0; i < 4; ++i) {
    data.sequence = static_cast<uint32_t>(received);
    ASSERT_TRUE(stream.Receive(data));
    received += data.body.size();
  }
  EXPECT_GT(received, boundary);
  EXPECT_EQ(stream.ReceiveSequence(), 3 * data.body.size());
  EXPECT_EQ(stream.ReceiveCredit(), 0U);
  EXPECT_EQ(stream.BufferedBytes(), kReceiveWindow);
  EXPECT_FALSE(
      stream.Receive({Kind::Data, 1, static_cast<uint32_t>(received), {1}}));
  EXPECT_TRUE(stream.IsReset());
}

}  // namespace
}  // namespace mozilla::naivefox::noconnect
