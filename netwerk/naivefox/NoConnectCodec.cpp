/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NoConnectCodec.h"

#include <algorithm>
#include <cstring>
#include <utility>

#include "pk11pub.h"

namespace mozilla::naivefox::noconnect {

namespace {

uint32_t ReadUint32(const uint8_t* aBytes) {
  return (uint32_t(aBytes[0]) << 24) | (uint32_t(aBytes[1]) << 16) |
         (uint32_t(aBytes[2]) << 8) | uint32_t(aBytes[3]);
}

void WriteUint32(uint8_t* aBytes, uint32_t aValue) {
  aBytes[0] = static_cast<uint8_t>(aValue >> 24);
  aBytes[1] = static_cast<uint8_t>(aValue >> 16);
  aBytes[2] = static_cast<uint8_t>(aValue >> 8);
  aBytes[3] = static_cast<uint8_t>(aValue);
}

bool ValidKind(Kind aKind) { return aKind >= Kind::Open && aKind <= Kind::Ack; }

}  // namespace

static bool EncodeImpl(uint32_t aSequence, size_t aCapacity, PressureHint aHint,
                       const std::vector<Frame>& aFrames,
                       std::vector<uint8_t>& aOutput) {
  if (aCapacity < kCellHeader || aCapacity > kMaxCell ||
      aFrames.size() > kMaxFrames || aHint > PressureHint::Bulk) {
    return false;
  }
  size_t used = kCellHeader;
  for (const Frame& frame : aFrames) {
    if (!ValidKind(frame.kind) || aCapacity - used < kFrameHeader ||
        frame.body.size() > aCapacity - used - kFrameHeader) {
      return false;
    }
    used += frame.Size();
  }

  std::vector<uint8_t> output(aCapacity);
  // NSS limits one DRBG request to 65536 bytes.
  for (size_t offset = used; offset < aCapacity;) {
    const size_t length = std::min(aCapacity - offset, size_t{65536});
    if (PK11_GenerateRandom(output.data() + offset, static_cast<int>(length)) !=
        SECSuccess) {
      return false;
    }
    offset += length;
  }
  std::memcpy(output.data(), "NFC1", 4);
  WriteUint32(output.data() + 4, aSequence);
  WriteUint32(output.data() + 8, static_cast<uint32_t>(used));
  output[12] = static_cast<uint8_t>(aFrames.size() >> 8);
  output[13] = static_cast<uint8_t>(aFrames.size());
  output[14] = static_cast<uint8_t>(aHint);
  size_t pos = kCellHeader;
  for (const Frame& frame : aFrames) {
    output[pos] = static_cast<uint8_t>(frame.kind);
    WriteUint32(output.data() + pos + 4, frame.stream);
    WriteUint32(output.data() + pos + 8, frame.sequence);
    WriteUint32(output.data() + pos + 12,
                static_cast<uint32_t>(frame.body.size()));
    if (!frame.body.empty()) {
      std::memcpy(output.data() + pos + kFrameHeader, frame.body.data(),
                  frame.body.size());
    }
    pos += frame.Size();
  }
  aOutput = std::move(output);
  return true;
}

bool Encode(uint32_t aSequence, size_t aCapacity,
            const std::vector<Frame>& aFrames, std::vector<uint8_t>& aOutput) {
  return EncodeImpl(aSequence, aCapacity, PressureHint::Idle, aFrames, aOutput);
}

bool EncodeRealtime(uint32_t aSequence, size_t aCapacity, PressureHint aHint,
                    const std::vector<Frame>& aFrames,
                    std::vector<uint8_t>& aOutput) {
  return EncodeImpl(aSequence, aCapacity, aHint, aFrames, aOutput);
}

static bool DecodeImpl(uint32_t aExpectedSequence, size_t aExpectedCapacity,
                       const std::vector<uint8_t>& aInput, bool aRealtime,
                       PressureHint& aHint, std::vector<Frame>& aFrames) {
  if (aExpectedCapacity < kCellHeader || aExpectedCapacity > kMaxCell ||
      aInput.size() != aExpectedCapacity ||
      std::memcmp(aInput.data(), "NFC1", 4) != 0 ||
      ReadUint32(aInput.data() + 4) != aExpectedSequence || aInput[15] != 0 ||
      (!aRealtime && aInput[14] != 0) ||
      aInput[14] > static_cast<uint8_t>(PressureHint::Bulk)) {
    return false;
  }
  const PressureHint hint = static_cast<PressureHint>(aInput[14]);
  const size_t used = ReadUint32(aInput.data() + 8);
  const size_t count = (size_t(aInput[12]) << 8) | aInput[13];
  if (used < kCellHeader || used > aInput.size() || count > kMaxFrames ||
      count > (used - kCellHeader) / kFrameHeader) {
    return false;
  }

  size_t pos = kCellHeader;
  for (size_t i = 0; i < count; ++i) {
    if (used - pos < kFrameHeader) {
      return false;
    }
    const uint8_t* header = aInput.data() + pos;
    const size_t length = ReadUint32(header + 12);
    if (!ValidKind(static_cast<Kind>(header[0])) || header[1] != 0 ||
        header[2] != 0 || header[3] != 0 ||
        length > used - pos - kFrameHeader) {
      return false;
    }
    pos += kFrameHeader + length;
  }
  if (pos != used) {
    return false;
  }

  std::vector<Frame> frames;
  frames.reserve(count);
  pos = kCellHeader;
  for (size_t i = 0; i < count; ++i) {
    const uint8_t* header = aInput.data() + pos;
    const size_t length = ReadUint32(header + 12);
    frames.push_back({static_cast<Kind>(header[0]), ReadUint32(header + 4),
                      ReadUint32(header + 8),
                      std::vector<uint8_t>(header + kFrameHeader,
                                           header + kFrameHeader + length)});
    pos += kFrameHeader + length;
  }
  aHint = hint;
  aFrames = std::move(frames);
  return true;
}

bool Decode(uint32_t aExpectedSequence, size_t aExpectedCapacity,
            const std::vector<uint8_t>& aInput, std::vector<Frame>& aFrames) {
  PressureHint hint = PressureHint::Idle;
  return DecodeImpl(aExpectedSequence, aExpectedCapacity, aInput, false, hint,
                    aFrames);
}

bool DecodeRealtime(uint32_t aExpectedSequence, size_t aExpectedCapacity,
                    const std::vector<uint8_t>& aInput, PressureHint& aHint,
                    std::vector<Frame>& aFrames) {
  return DecodeImpl(aExpectedSequence, aExpectedCapacity, aInput, true, aHint,
                    aFrames);
}

bool StreamState::Fail() {
  mFailed = true;
  return false;
}

bool StreamState::Receive(const Frame& aFrame) {
  if (IsReset() || aFrame.stream != mId) {
    return Fail();
  }
  switch (aFrame.kind) {
    case Kind::Opened:
      if (mOpened || aFrame.sequence != 0 || !aFrame.body.empty()) {
        return Fail();
      }
      mOpened = true;
      return true;
    case Kind::Data:
      if (!mOpened || mReceivedFin || aFrame.sequence != mNextReceive ||
          aFrame.body.empty() ||
          aFrame.body.size() > kMaxCell - kCellHeader - kFrameHeader ||
          aFrame.body.size() > mReceiveCredit) {
        return Fail();
      }
      // Byte offsets wrap modulo 2^32; outstanding data remains credit-bound.
      mNextReceive += static_cast<uint32_t>(aFrame.body.size());
      mReceiveCredit -= static_cast<uint32_t>(aFrame.body.size());
      mBufferedBytes += static_cast<uint32_t>(aFrame.body.size());
      return true;
    case Kind::Fin:
      if (!mOpened || mReceivedFin || !aFrame.body.empty() ||
          aFrame.sequence != mNextReceive) {
        return Fail();
      }
      mReceivedFin = true;
      return true;
    case Kind::Reset:
      if (aFrame.sequence != 0 || !aFrame.body.empty()) {
        return Fail();
      }
      mReset = true;
      return true;
    case Kind::Credit: {
      if (aFrame.sequence != 0 || aFrame.body.size() != 4) {
        return Fail();
      }
      const uint32_t credit = ReadUint32(aFrame.body.data());
      if (credit == 0 || credit > kReceiveWindow - mSendCredit) {
        return Fail();
      }
      mSendCredit += credit;
      return true;
    }
    default:
      return Fail();
  }
}

bool StreamState::MakeData(const uint8_t* aData, size_t aLength,
                           Frame& aFrame) {
  if (IsReset() || mSentFin || !aData || aLength == 0 ||
      aLength > kMaxCell - kCellHeader - kFrameHeader ||
      aLength > mSendCredit) {
    return false;
  }
  Frame frame{Kind::Data, mId, mNextSend,
              std::vector<uint8_t>(aData, aData + aLength)};
  mNextSend += static_cast<uint32_t>(aLength);
  mSendCredit -= static_cast<uint32_t>(aLength);
  aFrame = std::move(frame);
  return true;
}

bool StreamState::MakeFin(Frame& aFrame) {
  if (IsReset() || mSentFin) {
    return false;
  }
  aFrame = {Kind::Fin, mId, mNextSend, {}};
  mSentFin = true;
  return true;
}

bool StreamState::MakeReset(Frame& aFrame) {
  if (mId == 0 || mReset) {
    return false;
  }
  aFrame = {Kind::Reset, mId, 0, {}};
  mReset = true;
  return true;
}

bool StreamState::Delivered(uint32_t aBytes) {
  if (IsReset() || aBytes == 0 || aBytes > mBufferedBytes) {
    return Fail();
  }
  mBufferedBytes -= aBytes;
  mPendingCredit += aBytes;
  return true;
}

bool StreamState::TakeCredit(Frame& aFrame) {
  if (IsReset() || mPendingCredit == 0) {
    return false;
  }
  Frame frame{Kind::Credit, mId, 0, std::vector<uint8_t>(4)};
  WriteUint32(frame.body.data(), mPendingCredit);
  mReceiveCredit += mPendingCredit;
  mPendingCredit = 0;
  aFrame = std::move(frame);
  return true;
}

bool StreamState::IsDrained() const {
  return IsReset() || (mSentFin && mReceivedFin && mBufferedBytes == 0 &&
                       mPendingCredit == 0);
}

}  // namespace mozilla::naivefox::noconnect
