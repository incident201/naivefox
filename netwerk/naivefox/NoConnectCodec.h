/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NoConnectCodec_h
#define netwerk_naivefox_NoConnectCodec_h

#include <cstddef>
#include <cstdint>
#include <vector>

namespace mozilla::naivefox::noconnect {

inline constexpr size_t kCellHeader = 16;
inline constexpr size_t kFrameHeader = 16;
inline constexpr size_t kMaxCell = 256 * 1024;
inline constexpr size_t kMaxFrames = 4096;
inline constexpr size_t kMaxStreams = 32;
inline constexpr uint32_t kReceiveWindow = 512 * 1024;

enum class Kind : uint8_t {
  Open = 1,
  Data,
  Fin,
  Reset,
  Credit,
  Auth,
  Opened,
};

struct Frame {
  Kind kind = Kind::Data;
  uint32_t stream = 0;
  uint32_t sequence = 0;
  std::vector<uint8_t> body;

  size_t Size() const { return kFrameHeader + body.size(); }
};

// Output arguments remain unchanged on failure. Decode requires the complete
// HTTP body, including filler, with the negotiated capacity.
bool Encode(uint32_t aSequence, size_t aCapacity,
            const std::vector<Frame>& aFrames, std::vector<uint8_t>& aOutput);
bool Decode(uint32_t aExpectedSequence, size_t aExpectedCapacity,
            const std::vector<uint8_t>& aInput, std::vector<Frame>& aFrames);

// Client-side state for the selected 512-KiB profile. The owner serializes
// access and reserves queue space before creating a frame. Credit is returned
// only for bytes delivered to the local consumer.
class StreamState final {
 public:
  explicit StreamState(uint32_t aId) : mId(aId), mFailed(aId == 0) {}

  bool Receive(const Frame& aFrame);
  bool MakeData(const uint8_t* aData, size_t aLength, Frame& aFrame);
  bool MakeFin(Frame& aFrame);
  bool MakeReset(Frame& aFrame);
  bool Delivered(uint32_t aBytes);
  bool TakeCredit(Frame& aFrame);

  uint32_t Id() const { return mId; }
  bool IsOpened() const { return mOpened; }
  bool IsReset() const { return mReset || mFailed; }
  bool SentFin() const { return mSentFin; }
  bool ReceivedFin() const { return mReceivedFin; }
  uint32_t SendCredit() const { return mSendCredit; }
  uint32_t ReceiveCredit() const { return mReceiveCredit; }
  uint32_t PendingCredit() const { return mPendingCredit; }
  uint32_t SendSequence() const { return mNextSend; }
  uint32_t ReceiveSequence() const { return mNextReceive; }
  uint32_t BufferedBytes() const { return mBufferedBytes; }
  bool IsDrained() const;

 private:
  bool Fail();

  const uint32_t mId;
  uint32_t mSendCredit = kReceiveWindow;
  uint32_t mReceiveCredit = kReceiveWindow;
  uint32_t mPendingCredit = 0;
  uint32_t mBufferedBytes = 0;
  uint32_t mNextSend = 0;
  uint32_t mNextReceive = 0;
  bool mOpened = false;
  bool mReset = false;
  bool mFailed = false;
  bool mSentFin = false;
  bool mReceivedFin = false;
};

}  // namespace mozilla::naivefox::noconnect

#endif  // netwerk_naivefox_NoConnectCodec_h
