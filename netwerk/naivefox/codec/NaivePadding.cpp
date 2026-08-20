/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NaivePadding.h"

#include <algorithm>
#include <cstring>

#include "mozilla/RandomNum.h"

namespace mozilla::net::naivefox {

bool SystemPaddingLengthGenerator::Generate(uint8_t& aLength) {
  auto value = RandomUint64();
  if (value.isNothing()) {
    return false;
  }
  aLength = static_cast<uint8_t>(*value);
  return true;
}

NaivePaddingEncoder::NaivePaddingEncoder(PaddingLengthGenerator& aGenerator)
    : mGenerator(aGenerator) {}

size_t NaivePaddingEncoder::BufferedByteCount() const {
  return mPendingLength - mPendingOffset;
}

PaddingCodecResult NaivePaddingEncoder::Encode(Span<const uint8_t> aInput,
                                               Span<uint8_t> aOutput) {
  PaddingCodecResult result{mStatus};
  if (mStatus != PaddingCodecStatus::Ok) {
    return result;
  }

  while (true) {
    if (BufferedByteCount() != 0) {
      const size_t length = std::min(BufferedByteCount(),
                                     aOutput.Length() - result.outputProduced);
      if (length != 0) {
        std::memcpy(aOutput.Elements() + result.outputProduced,
                    mPending.data() + mPendingOffset, length);
        mPendingOffset += length;
        result.outputProduced += length;
      }
      if (BufferedByteCount() != 0) {
        return result;
      }
      mPendingOffset = 0;
      mPendingLength = 0;
    }

    if (result.inputConsumed == aInput.Length()) {
      return result;
    }

    if (mPaddedRecordCount < kNaivePaddedRecordCount) {
      uint8_t paddingLength = 0;
      if (!mGenerator.Generate(paddingLength)) {
        mStatus = PaddingCodecStatus::RandomFailure;
        result.status = mStatus;
        return result;
      }

      const size_t payloadLength = std::min(
          kNaiveMaxPayloadLength, aInput.Length() - result.inputConsumed);
      mPending[0] = static_cast<uint8_t>(payloadLength >> 8);
      mPending[1] = static_cast<uint8_t>(payloadLength);
      mPending[2] = paddingLength;
      std::memcpy(mPending.data() + 3, aInput.Elements() + result.inputConsumed,
                  payloadLength);
      std::memset(mPending.data() + 3 + payloadLength, 0, paddingLength);
      mPendingLength = 3 + payloadLength + paddingLength;
      result.inputConsumed += payloadLength;
      ++mPaddedRecordCount;
      continue;
    }

    const size_t length = std::min(aInput.Length() - result.inputConsumed,
                                   aOutput.Length() - result.outputProduced);
    if (length == 0) {
      return result;
    }
    std::memcpy(aOutput.Elements() + result.outputProduced,
                aInput.Elements() + result.inputConsumed, length);
    result.inputConsumed += length;
    result.outputProduced += length;
  }
}

void NaivePaddingDecoder::CompleteRecord() {
  ++mPaddedRecordCount;
  mHeaderLength = 0;
  mPayloadRemaining = 0;
  mPaddingRemaining = 0;
  mState = mPaddedRecordCount == kNaivePaddedRecordCount ? State::Raw
                                                         : State::Header;
}

PaddingCodecResult NaivePaddingDecoder::Decode(Span<const uint8_t> aInput,
                                               Span<uint8_t> aOutput) {
  PaddingCodecResult result{mStatus};
  if (mStatus != PaddingCodecStatus::Ok) {
    return result;
  }

  while (result.inputConsumed < aInput.Length()) {
    if (mState == State::Raw) {
      const size_t length = std::min(aInput.Length() - result.inputConsumed,
                                     aOutput.Length() - result.outputProduced);
      if (length == 0) {
        return result;
      }
      std::memcpy(aOutput.Elements() + result.outputProduced,
                  aInput.Elements() + result.inputConsumed, length);
      result.inputConsumed += length;
      result.outputProduced += length;
      continue;
    }

    if (mState == State::Header) {
      const size_t length = std::min(mHeader.size() - mHeaderLength,
                                     aInput.Length() - result.inputConsumed);
      std::memcpy(mHeader.data() + mHeaderLength,
                  aInput.Elements() + result.inputConsumed, length);
      mHeaderLength += length;
      result.inputConsumed += length;
      if (mHeaderLength != mHeader.size()) {
        return result;
      }
      mPayloadRemaining = (static_cast<size_t>(mHeader[0]) << 8) | mHeader[1];
      mPaddingRemaining = mHeader[2];
      mState = mPayloadRemaining == 0 ? State::Padding : State::Payload;
      if (mPayloadRemaining == 0 && mPaddingRemaining == 0) {
        CompleteRecord();
      }
      continue;
    }

    if (mState == State::Payload) {
      const size_t length =
          std::min({mPayloadRemaining, aInput.Length() - result.inputConsumed,
                    aOutput.Length() - result.outputProduced});
      if (length == 0) {
        return result;
      }
      std::memcpy(aOutput.Elements() + result.outputProduced,
                  aInput.Elements() + result.inputConsumed, length);
      mPayloadRemaining -= length;
      result.inputConsumed += length;
      result.outputProduced += length;
      if (mPayloadRemaining == 0) {
        mState = State::Padding;
        if (mPaddingRemaining == 0) {
          CompleteRecord();
        }
      }
      continue;
    }

    const size_t length =
        std::min(mPaddingRemaining, aInput.Length() - result.inputConsumed);
    mPaddingRemaining -= length;
    result.inputConsumed += length;
    if (mPaddingRemaining == 0) {
      CompleteRecord();
    }
  }

  return result;
}

PaddingCodecStatus NaivePaddingDecoder::Finish() {
  if (mStatus != PaddingCodecStatus::Ok) {
    return mStatus;
  }
  if (mState == State::Raw || (mState == State::Header && mHeaderLength == 0)) {
    return mStatus;
  }
  mStatus = PaddingCodecStatus::TruncatedRecord;
  return mStatus;
}

}  // namespace mozilla::net::naivefox
