/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_codec_NaivePadding_h
#define netwerk_naivefox_codec_NaivePadding_h

#include <array>
#include <cstddef>
#include <cstdint>

#include "mozilla/Span.h"

namespace mozilla::net::naivefox {

inline constexpr size_t kNaivePaddedRecordCount = 8;
inline constexpr size_t kNaiveMaxPayloadLength = 65535;
inline constexpr size_t kNaiveMaxPaddingLength = 255;
inline constexpr size_t kNaiveMaxRecordLength =
    3 + kNaiveMaxPayloadLength + kNaiveMaxPaddingLength;

enum class PaddingCodecStatus : uint8_t {
  Ok,
  RandomFailure,
  TruncatedRecord,
};

struct PaddingCodecResult {
  PaddingCodecStatus status = PaddingCodecStatus::Ok;
  size_t inputConsumed = 0;
  size_t outputProduced = 0;
};

class PaddingLengthGenerator {
 public:
  virtual ~PaddingLengthGenerator() = default;
  virtual bool Generate(uint8_t& aLength) = 0;
};

class SystemPaddingLengthGenerator final : public PaddingLengthGenerator {
 public:
  bool Generate(uint8_t& aLength) override;
};

class NaivePaddingEncoder final {
 public:
  explicit NaivePaddingEncoder(PaddingLengthGenerator& aGenerator);

  PaddingCodecResult Encode(Span<const uint8_t> aInput, Span<uint8_t> aOutput);

  size_t BufferedByteCount() const;
  uint8_t PaddedRecordCount() const { return mPaddedRecordCount; }
  PaddingCodecStatus Status() const { return mStatus; }

 private:
  PaddingLengthGenerator& mGenerator;
  std::array<uint8_t, kNaiveMaxRecordLength> mPending{};
  size_t mPendingOffset = 0;
  size_t mPendingLength = 0;
  uint8_t mPaddedRecordCount = 0;
  PaddingCodecStatus mStatus = PaddingCodecStatus::Ok;
};

class NaivePaddingDecoder final {
 public:
  PaddingCodecResult Decode(Span<const uint8_t> aInput, Span<uint8_t> aOutput);
  PaddingCodecStatus Finish();

  size_t BufferedByteCount() const { return mHeaderLength; }
  uint8_t PaddedRecordCount() const { return mPaddedRecordCount; }
  PaddingCodecStatus Status() const { return mStatus; }

 private:
  enum class State : uint8_t { Header, Payload, Padding, Raw };

  void CompleteRecord();

  std::array<uint8_t, 3> mHeader{};
  size_t mHeaderLength = 0;
  size_t mPayloadRemaining = 0;
  size_t mPaddingRemaining = 0;
  uint8_t mPaddedRecordCount = 0;
  State mState = State::Header;
  PaddingCodecStatus mStatus = PaddingCodecStatus::Ok;
};

}  // namespace mozilla::net::naivefox

#endif  // netwerk_naivefox_codec_NaivePadding_h
