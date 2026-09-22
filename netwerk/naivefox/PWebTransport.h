/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_PWebTransport_NaiveFox_h
#define mozilla_dom_PWebTransport_NaiveFox_h

#include <cstdint>

namespace mozilla::dom {

class WebTransportDatagramStatsData final {
 public:
  uint64_t& droppedIncoming() { return mDroppedIncoming; }
  uint64_t& expiredIncoming() { return mExpiredIncoming; }
  uint64_t& expiredOutgoing() { return mExpiredOutgoing; }
  uint64_t& lostOutgoing() { return mLostOutgoing; }

 private:
  uint64_t mDroppedIncoming = 0;
  uint64_t mExpiredIncoming = 0;
  uint64_t mExpiredOutgoing = 0;
  uint64_t mLostOutgoing = 0;
};

class WebTransportStatsData final {
 public:
  uint64_t& bytesSent() { return mBytesSent; }
  uint64_t& bytesAcknowledged() { return mBytesAcknowledged; }
  uint64_t& packetsSent() { return mPacketsSent; }
  uint64_t& bytesLost() { return mBytesLost; }
  uint64_t& packetsLost() { return mPacketsLost; }
  uint64_t& bytesReceived() { return mBytesReceived; }
  uint64_t& packetsReceived() { return mPacketsReceived; }
  double& smoothedRtt() { return mSmoothedRtt; }
  double& rttVariation() { return mRttVariation; }
  double& minRtt() { return mMinRtt; }
  int64_t& estimatedSendRate() { return mEstimatedSendRate; }
  bool& atSendCapacity() { return mAtSendCapacity; }
  WebTransportDatagramStatsData& datagrams() { return mDatagrams; }

 private:
  uint64_t mBytesSent = 0;
  uint64_t mBytesAcknowledged = 0;
  uint64_t mPacketsSent = 0;
  uint64_t mBytesLost = 0;
  uint64_t mPacketsLost = 0;
  uint64_t mBytesReceived = 0;
  uint64_t mPacketsReceived = 0;
  double mSmoothedRtt = 0;
  double mRttVariation = 0;
  double mMinRtt = 0;
  int64_t mEstimatedSendRate = 0;
  bool mAtSendCapacity = false;
  WebTransportDatagramStatsData mDatagrams;
};

}  // namespace mozilla::dom

#endif
