/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <algorithm>
#include <array>
#include <cstdint>
#include <numeric>
#include <utility>
#include <vector>

#include "NaivePadding.h"
#include "gtest/gtest.h"

namespace mozilla::net::naivefox {
namespace {

class SequenceGenerator final : public PaddingLengthGenerator {
 public:
  explicit SequenceGenerator(std::vector<uint8_t> aValues)
      : mValues(std::move(aValues)) {}

  bool Generate(uint8_t& aLength) override {
    ++mCalls;
    if (mIndex == mValues.size()) {
      return false;
    }
    aLength = mValues[mIndex++];
    return true;
  }

  size_t Calls() const { return mCalls; }

 private:
  std::vector<uint8_t> mValues;
  size_t mIndex = 0;
  size_t mCalls = 0;
};

class XorShiftGenerator final : public PaddingLengthGenerator {
 public:
  explicit XorShiftGenerator(uint32_t aState) : mState(aState) {}

  bool Generate(uint8_t& aLength) override {
    aLength = static_cast<uint8_t>(Next());
    return true;
  }

  uint32_t Next() {
    mState ^= mState << 13;
    mState ^= mState >> 17;
    mState ^= mState << 5;
    return mState;
  }

 private:
  uint32_t mState;
};

void Append(std::vector<uint8_t>& aDestination, Span<const uint8_t> aSource) {
  aDestination.insert(aDestination.end(), aSource.begin(), aSource.end());
}

std::vector<uint8_t> EncodeChunks(
    const std::vector<std::vector<uint8_t>>& aChunks,
    PaddingLengthGenerator& aGenerator, size_t aOutputCapacity = 70123) {
  NaivePaddingEncoder encoder(aGenerator);
  std::vector<uint8_t> wire;
  std::vector<uint8_t> output(aOutputCapacity);

  for (const auto& chunk : aChunks) {
    if (chunk.empty()) {
      auto result = encoder.Encode({}, Span(output));
      EXPECT_EQ(result.status, PaddingCodecStatus::Ok);
      EXPECT_EQ(result.inputConsumed, 0U);
      EXPECT_EQ(result.outputProduced, 0U);
      continue;
    }

    size_t offset = 0;
    while (offset != chunk.size() || encoder.BufferedByteCount() != 0) {
      Span<const uint8_t> input(chunk.data() + offset, chunk.size() - offset);
      auto result = encoder.Encode(input, Span(output));
      EXPECT_EQ(result.status, PaddingCodecStatus::Ok);
      offset += result.inputConsumed;
      Append(wire, Span<const uint8_t>(output.data(), result.outputProduced));
      if (result.inputConsumed == 0 && result.outputProduced == 0) {
        ADD_FAILURE() << "encoder made no progress";
        return wire;
      }
    }
  }

  return wire;
}

bool FeedDecoder(NaivePaddingDecoder& aDecoder, Span<const uint8_t> aInput,
                 size_t aOutputCapacity, std::vector<uint8_t>& aDecoded) {
  std::vector<uint8_t> output(aOutputCapacity);
  size_t offset = 0;
  while (offset != aInput.Length()) {
    auto result = aDecoder.Decode(aInput.From(offset), Span(output));
    if (result.status != PaddingCodecStatus::Ok) {
      return false;
    }
    offset += result.inputConsumed;
    Append(aDecoded, Span<const uint8_t>(output.data(), result.outputProduced));
    if (result.inputConsumed == 0 && result.outputProduced == 0) {
      return false;
    }
  }
  return true;
}

std::vector<uint8_t> Concatenate(
    const std::vector<std::vector<uint8_t>>& aChunks) {
  std::vector<uint8_t> result;
  for (const auto& chunk : aChunks) {
    result.insert(result.end(), chunk.begin(), chunk.end());
  }
  return result;
}

TEST(NaivePaddingEncoder, OneBytePayload)
{
  SequenceGenerator generator({7});
  NaivePaddingEncoder encoder(generator);
  const std::array<uint8_t, 1> input{0xab};
  std::array<uint8_t, 32> output{};

  auto result = encoder.Encode(Span(input), Span(output));

  ASSERT_EQ(result.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(result.inputConsumed, 1U);
  EXPECT_EQ(result.outputProduced, 11U);
  EXPECT_EQ(output[0], 0U);
  EXPECT_EQ(output[1], 1U);
  EXPECT_EQ(output[2], 7U);
  EXPECT_EQ(output[3], 0xabU);
  EXPECT_TRUE(std::all_of(output.begin() + 4, output.begin() + 11,
                          [](uint8_t aByte) { return aByte == 0; }));
  EXPECT_EQ(encoder.PaddedRecordCount(), 1U);
}

TEST(NaivePaddingEncoder, EmptyInputDoesNotConsumeQuota)
{
  SequenceGenerator generator({19});
  NaivePaddingEncoder encoder(generator);
  std::array<uint8_t, 32> output{};

  auto empty = encoder.Encode({}, Span(output));
  EXPECT_EQ(empty.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(empty.inputConsumed, 0U);
  EXPECT_EQ(empty.outputProduced, 0U);
  EXPECT_EQ(generator.Calls(), 0U);
  EXPECT_EQ(encoder.PaddedRecordCount(), 0U);

  const std::array<uint8_t, 1> input{42};
  auto nonempty = encoder.Encode(Span(input), Span(output));
  EXPECT_EQ(nonempty.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(output[2], 19U);
  EXPECT_EQ(generator.Calls(), 1U);
  EXPECT_EQ(encoder.PaddedRecordCount(), 1U);
}

TEST(NaivePaddingEncoder, PaddingLengthExtremes)
{
  for (uint8_t paddingLength : {uint8_t(0), uint8_t(255)}) {
    SequenceGenerator generator({paddingLength});
    NaivePaddingEncoder encoder(generator);
    const std::array<uint8_t, 1> input{91};
    std::array<uint8_t, kNaiveMaxRecordLength> output{};

    auto result = encoder.Encode(Span(input), Span(output));

    ASSERT_EQ(result.status, PaddingCodecStatus::Ok);
    ASSERT_EQ(result.outputProduced, 4U + paddingLength);
    EXPECT_EQ(output[2], paddingLength);
    EXPECT_EQ(output[3], 91U);
    EXPECT_TRUE(std::all_of(output.begin() + 4,
                            output.begin() + result.outputProduced,
                            [](uint8_t aByte) { return aByte == 0; }));
  }
}

TEST(NaivePaddingEncoder, MaximumPayload)
{
  SequenceGenerator generator({255});
  NaivePaddingEncoder encoder(generator);
  std::vector<uint8_t> input(kNaiveMaxPayloadLength);
  std::iota(input.begin(), input.end(), uint8_t(0));
  std::array<uint8_t, kNaiveMaxRecordLength> output{};

  auto result = encoder.Encode(Span(input), Span(output));

  ASSERT_EQ(result.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(result.inputConsumed, kNaiveMaxPayloadLength);
  EXPECT_EQ(result.outputProduced, kNaiveMaxRecordLength);
  EXPECT_EQ(output[0], 0xffU);
  EXPECT_EQ(output[1], 0xffU);
  EXPECT_EQ(output[2], 0xffU);
  EXPECT_TRUE(std::equal(input.begin(), input.end(), output.begin() + 3));
  EXPECT_LE(encoder.BufferedByteCount(), kNaiveMaxRecordLength);
}

TEST(NaivePaddingEncoder, SplitsPayloadLargerThanMaximum)
{
  std::vector<uint8_t> input(kNaiveMaxPayloadLength + 1, 23);
  SequenceGenerator generator({0, 0});
  auto wire = EncodeChunks({input}, generator);

  ASSERT_EQ(wire.size(), input.size() + 6);
  EXPECT_EQ(wire[0], 0xffU);
  EXPECT_EQ(wire[1], 0xffU);
  EXPECT_EQ(wire[2], 0U);
  const size_t secondHeader = 3 + kNaiveMaxPayloadLength;
  EXPECT_EQ(wire[secondHeader], 0U);
  EXPECT_EQ(wire[secondHeader + 1], 1U);
  EXPECT_EQ(wire[secondHeader + 2], 0U);
  EXPECT_EQ(generator.Calls(), 2U);
}

TEST(NaivePaddingEncoder, PartialDrainDoesNotRegenerateRecord)
{
  SequenceGenerator generator({31});
  NaivePaddingEncoder encoder(generator);
  const std::array<uint8_t, 4> input{1, 2, 3, 4};
  std::array<uint8_t, 1> output{};
  std::vector<uint8_t> wire;

  auto first = encoder.Encode(Span(input), Span(output));
  EXPECT_EQ(first.inputConsumed, input.size());
  EXPECT_EQ(first.outputProduced, 1U);
  Append(wire, Span<const uint8_t>(output.data(), first.outputProduced));
  EXPECT_EQ(generator.Calls(), 1U);
  EXPECT_EQ(encoder.PaddedRecordCount(), 1U);

  while (encoder.BufferedByteCount() != 0) {
    auto result = encoder.Encode({}, Span(output));
    ASSERT_EQ(result.status, PaddingCodecStatus::Ok);
    ASSERT_EQ(result.outputProduced, 1U);
    Append(wire, Span<const uint8_t>(output.data(), result.outputProduced));
    EXPECT_EQ(generator.Calls(), 1U);
  }

  ASSERT_EQ(wire.size(), 38U);
  EXPECT_EQ(wire[0], 0U);
  EXPECT_EQ(wire[1], 4U);
  EXPECT_EQ(wire[2], 31U);
}

TEST(NaivePaddingEncoder, RandomFailureDoesNotConsumeInput)
{
  SequenceGenerator generator({});
  NaivePaddingEncoder encoder(generator);
  const std::array<uint8_t, 1> input{1};
  std::array<uint8_t, 8> output{};

  auto result = encoder.Encode(Span(input), Span(output));

  EXPECT_EQ(result.status, PaddingCodecStatus::RandomFailure);
  EXPECT_EQ(result.inputConsumed, 0U);
  EXPECT_EQ(result.outputProduced, 0U);
  EXPECT_EQ(encoder.PaddedRecordCount(), 0U);
}

TEST(NaivePaddingDecoder, AcceptsZeroLengthRecords)
{
  std::vector<uint8_t> wire(kNaivePaddedRecordCount * 3, 0);
  const std::array<uint8_t, 4> tail{9, 8, 7, 6};
  wire.insert(wire.end(), tail.begin(), tail.end());
  NaivePaddingDecoder decoder;
  std::array<uint8_t, 16> output{};

  auto result = decoder.Decode(Span(wire), Span(output));

  EXPECT_EQ(result.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(result.inputConsumed, wire.size());
  EXPECT_EQ(result.outputProduced, tail.size());
  EXPECT_TRUE(std::equal(tail.begin(), tail.end(), output.begin()));
  EXPECT_EQ(decoder.PaddedRecordCount(), kNaivePaddedRecordCount);
  EXPECT_EQ(decoder.Finish(), PaddingCodecStatus::Ok);
}

TEST(NaivePaddingDecoder, AcceptsArbitraryPaddingBytes)
{
  const std::array<uint8_t, 7> wire{0, 1, 3, 44, 0xaa, 0xbb, 0xcc};
  NaivePaddingDecoder decoder;
  std::array<uint8_t, 8> output{};

  auto result = decoder.Decode(Span(wire), Span(output));

  EXPECT_EQ(result.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(result.inputConsumed, wire.size());
  EXPECT_EQ(result.outputProduced, 1U);
  EXPECT_EQ(output[0], 44U);
  EXPECT_EQ(decoder.PaddedRecordCount(), 1U);
  EXPECT_EQ(decoder.Finish(), PaddingCodecStatus::Ok);
}

TEST(NaivePaddingDecoder, EveryRecordBoundaryFragmentation)
{
  const std::vector<uint8_t> payload{1, 2, 3, 4, 5, 6, 7};
  SequenceGenerator generator({5});
  auto wire = EncodeChunks({payload}, generator);

  for (size_t split = 0; split <= wire.size(); ++split) {
    NaivePaddingDecoder decoder;
    std::vector<uint8_t> decoded;
    EXPECT_TRUE(FeedDecoder(decoder, Span<const uint8_t>(wire.data(), split),
                            32, decoded))
        << "split=" << split;
    EXPECT_TRUE(FeedDecoder(
        decoder, Span<const uint8_t>(wire.data() + split, wire.size() - split),
        32, decoded))
        << "split=" << split;
    EXPECT_EQ(decoder.Finish(), PaddingCodecStatus::Ok) << "split=" << split;
    EXPECT_EQ(decoded, payload) << "split=" << split;
  }

  NaivePaddingDecoder bytewiseDecoder;
  std::vector<uint8_t> decoded;
  for (uint8_t byte : wire) {
    EXPECT_TRUE(FeedDecoder(bytewiseDecoder, Span<const uint8_t>(&byte, 1), 1,
                            decoded));
  }
  EXPECT_EQ(bytewiseDecoder.Finish(), PaddingCodecStatus::Ok);
  EXPECT_EQ(decoded, payload);
}

TEST(NaivePaddingDecoder, CoalescedRecords)
{
  std::vector<std::vector<uint8_t>> chunks;
  for (size_t index = 0; index < kNaivePaddedRecordCount; ++index) {
    chunks.emplace_back(index + 1, static_cast<uint8_t>(index + 10));
  }
  SequenceGenerator generator({0, 1, 2, 3, 4, 5, 6, 7});
  auto wire = EncodeChunks(chunks, generator);
  NaivePaddingDecoder decoder;
  std::vector<uint8_t> decoded;

  ASSERT_TRUE(FeedDecoder(decoder, Span(wire), wire.size(), decoded));

  EXPECT_EQ(decoded, Concatenate(chunks));
  EXPECT_EQ(decoder.PaddedRecordCount(), kNaivePaddedRecordCount);
  EXPECT_EQ(decoder.Finish(), PaddingCodecStatus::Ok);
}

TEST(NaivePaddingDecoder, EighthRecordAndRawTailShareInput)
{
  std::vector<std::vector<uint8_t>> chunks;
  for (size_t index = 0; index < kNaivePaddedRecordCount; ++index) {
    chunks.push_back({static_cast<uint8_t>(index)});
  }
  chunks.push_back({0xf0, 0xf1, 0xf2, 0xf3});
  SequenceGenerator generator({0, 0, 0, 0, 0, 0, 0, 0});
  auto wire = EncodeChunks(chunks, generator);
  NaivePaddingDecoder decoder;
  std::array<uint8_t, 64> output{};

  auto result = decoder.Decode(Span(wire), Span(output));
  const auto expected = Concatenate(chunks);

  EXPECT_EQ(result.status, PaddingCodecStatus::Ok);
  EXPECT_EQ(result.inputConsumed, wire.size());
  EXPECT_EQ(result.outputProduced, kNaivePaddedRecordCount + 4);
  EXPECT_TRUE(std::equal(expected.begin(), expected.end(), output.begin()));
  EXPECT_EQ(decoder.PaddedRecordCount(), kNaivePaddedRecordCount);
}

TEST(NaivePaddingDecoder, CleanAndTruncatedEof)
{
  EXPECT_EQ(NaivePaddingDecoder().Finish(), PaddingCodecStatus::Ok);

  const std::vector<uint8_t> payload{3, 4, 5};
  SequenceGenerator generator({2});
  auto wire = EncodeChunks({payload}, generator);

  for (size_t prefix = 1; prefix < wire.size(); ++prefix) {
    NaivePaddingDecoder decoder;
    std::vector<uint8_t> decoded;
    ASSERT_TRUE(FeedDecoder(decoder, Span<const uint8_t>(wire.data(), prefix),
                            wire.size(), decoded));
    EXPECT_EQ(decoder.Finish(), PaddingCodecStatus::TruncatedRecord)
        << "prefix=" << prefix;
    std::array<uint8_t, 8> output{};
    auto afterError = decoder.Decode(Span(wire), Span(output));
    EXPECT_EQ(afterError.status, PaddingCodecStatus::TruncatedRecord);
    EXPECT_EQ(afterError.inputConsumed, 0U);
  }

  NaivePaddingDecoder complete;
  std::vector<uint8_t> decoded;
  ASSERT_TRUE(FeedDecoder(complete, Span(wire), wire.size(), decoded));
  EXPECT_EQ(complete.Finish(), PaddingCodecStatus::Ok);
  EXPECT_EQ(decoded, payload);
}

TEST(NaivePaddingCodec, DeterministicRandomizedRoundTrips)
{
  XorShiftGenerator dataRandom(0x4e465831);

  for (size_t iteration = 0; iteration < 40; ++iteration) {
    std::vector<std::vector<uint8_t>> chunks;
    for (size_t chunkIndex = 0; chunkIndex < 16; ++chunkIndex) {
      size_t length = dataRandom.Next() % 2048;
      if (chunkIndex == 0 && iteration == 0) {
        length = kNaiveMaxPayloadLength + 17;
      }
      std::vector<uint8_t> chunk(length);
      for (uint8_t& byte : chunk) {
        byte = static_cast<uint8_t>(dataRandom.Next());
      }
      chunks.push_back(std::move(chunk));
    }

    XorShiftGenerator paddingRandom(0x9e3779b9U ^
                                    static_cast<uint32_t>(iteration));
    const size_t encoderOutputCapacity = 1 + (dataRandom.Next() % 521);
    auto wire = EncodeChunks(chunks, paddingRandom, encoderOutputCapacity);
    const auto expected = Concatenate(chunks);

    NaivePaddingDecoder decoder;
    std::vector<uint8_t> decoded;
    size_t wireOffset = 0;
    while (wireOffset != wire.size()) {
      const size_t inputLength = std::min<size_t>(1 + (dataRandom.Next() % 997),
                                                  wire.size() - wireOffset);
      const size_t outputCapacity = 1 + (dataRandom.Next() % 257);
      ASSERT_TRUE(FeedDecoder(
          decoder, Span<const uint8_t>(wire.data() + wireOffset, inputLength),
          outputCapacity, decoded));
      wireOffset += inputLength;
      EXPECT_LE(decoder.BufferedByteCount(), 3U);
    }

    EXPECT_EQ(decoder.Finish(), PaddingCodecStatus::Ok);
    EXPECT_EQ(decoded, expected) << "iteration=" << iteration;
  }
}

}  // namespace
}  // namespace mozilla::net::naivefox
