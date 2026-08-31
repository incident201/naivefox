/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifdef MOZ_NAIVEFOX

#  include <atomic>
#  include <chrono>
#  include <condition_variable>
#  include <memory>
#  include <mutex>
#  include <vector>

#  include "gtest/gtest.h"
#  include "mozilla/Components.h"
#  include "mozilla/EndianUtils.h"
#  include "mozilla/ProfileChunkedBuffer.h"
#  include "mozilla/ScopeExit.h"
#  include "mozilla/net/WebSocketChannel.h"
#  include "nsIHttpChannel.h"
#  include "nsIRandomGenerator.h"
#  include "nsIThread.h"
#  include "nsThreadUtils.h"
#  include "nss.h"

namespace mozilla::net {

class NaiveFoxWebSocketTestPeer final {
 public:
  static void Prepare(WebSocketChannel* aChannel,
                      nsIWebSocketListener* aListener,
                      nsIEventTarget* aProducer) {
    aChannel->mIOThread = aProducer;
    aChannel->mMaxMessageSize = 256 * 1024;
    MutexAutoLock lock(aChannel->mMutex);
    aChannel->mListenerMT =
        MakeRefPtr<BaseWebSocketChannel::ListenerAndContextContainer>(aListener,
                                                                      nullptr);
  }
  static uint32_t PendingMessages(WebSocketChannel* aChannel) {
    return aChannel->mNativePendingMessages;
  }
  static uint32_t PendingBytes(WebSocketChannel* aChannel) {
    return aChannel->mNativePendingBytes;
  }
  static void SetRandomGenerator(WebSocketChannel* aChannel,
                                 nsIRandomGenerator* aRandom) {
    aChannel->mRandomGenerator = aRandom;
  }
  static uint32_t PendingPongs(WebSocketChannel* aChannel) {
    return aChannel->PendingNativePongs();
  }
  static nsresult HandleExtensions(WebSocketChannel* aChannel,
                                   nsIHttpChannel* aResponse) {
    aChannel->mHttpChannel = aResponse;
    return aChannel->HandleExtensions();
  }
  static bool CanReadCompressedData(WebSocketChannel* aChannel) {
    MutexAutoLock lock(aChannel->mCompressorMutex);
    return aChannel->mDataStarted || !!aChannel->mPMCECompressor;
  }
};

namespace {

class MessageCounter final : public nsIWebSocketListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_IMETHOD OnStart(nsISupports*) override { return NS_OK; }
  NS_IMETHOD OnStop(nsISupports*, nsresult aStatus) override {
    ++mStops;
    mStatus = aStatus;
    return NS_OK;
  }
  NS_IMETHOD OnMessageAvailable(nsISupports*, const nsACString&) override {
    ++mMessages;
    return NS_OK;
  }
  NS_IMETHOD OnBinaryMessageAvailable(nsISupports*,
                                      const nsACString& aMessage) override {
    ++mMessages;
    mBytes += aMessage.Length();
    return NS_OK;
  }
  NS_IMETHOD OnAcknowledge(nsISupports*, uint32_t) override { return NS_OK; }
  NS_IMETHOD OnServerClose(nsISupports*, uint16_t, const nsACString&) override {
    return NS_OK;
  }
  NS_IMETHOD OnError() override { return NS_OK; }

  uint32_t mMessages = 0;
  uint32_t mStops = 0;
  uint32_t mBytes = 0;
  nsresult mStatus = NS_OK;

 private:
  ~MessageCounter() = default;
};
NS_IMPL_ISUPPORTS(MessageCounter, nsIWebSocketListener)

class FixedMaskGenerator final : public nsIRandomGenerator {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_IMETHOD GenerateRandomBytes(uint32_t aLength, uint8_t** aBuffer) override {
    *aBuffer = static_cast<uint8_t*>(malloc(aLength));
    if (!*aBuffer) {
      return NS_ERROR_OUT_OF_MEMORY;
    }
    return GenerateRandomBytesInto(*aBuffer, aLength);
  }
  NS_IMETHOD GenerateRandomBytesInto(uint8_t* aBuffer,
                                     uint32_t aLength) override {
    memset(aBuffer, 0x5a, aLength);
    return NS_OK;
  }

 private:
  ~FixedMaskGenerator() = default;
};
NS_IMPL_ISUPPORTS(FixedMaskGenerator, nsIRandomGenerator)

class HeaderOnlyChannel : public nsIHttpChannel {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_FORWARD_SAFE_NSIHTTPCHANNEL(mUnused)
  NS_FORWARD_SAFE_NSIIDENTCHANNEL(mUnused)
  NS_FORWARD_SAFE_NSICHANNEL(mUnused)
  NS_FORWARD_SAFE_NSIREQUEST(mUnused)

 protected:
  virtual ~HeaderOnlyChannel() = default;

 private:
  nsCOMPtr<nsIHttpChannel> mUnused;
};
NS_IMPL_ISUPPORTS(HeaderOnlyChannel, nsIHttpChannel, nsIIdentChannel,
                  nsIChannel, nsIRequest)

void HeaderOnlyChannel::SetSource(UniquePtr<ProfileChunkedBuffer>) {
  MOZ_CRASH("Unexpected profiler source on header-only test channel");
}

class UnsolicitedCompression final : public HeaderOnlyChannel {
 public:
  NS_IMETHOD GetResponseHeader(const nsACString& aName,
                               nsACString& aValue) override {
    if (!aName.EqualsLiteral("Sec-WebSocket-Extensions")) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    aValue.AssignLiteral("permessage-deflate");
    return NS_OK;
  }
};

struct FeedState final {
  std::mutex mMutex;
  std::condition_variable mCondition;
  uint32_t mAccepted = 0;
  nsresult mStatus = NS_ERROR_NOT_INITIALIZED;
  std::atomic<bool> mDone{false};
};

std::shared_ptr<FeedState> FeedWhileConsumerBlocked(WebSocketChannel* aChannel,
                                                    nsIThread* aProducer,
                                                    uint32_t aCapacity,
                                                    uint32_t aCount,
                                                    uint8_t aOpcode = 0x82) {
  auto state = std::make_shared<FeedState>();
  RefPtr<WebSocketChannel> channel = aChannel;
  nsresult dispatched = aProducer->Dispatch(NS_NewRunnableFunction(
      "NaiveFox::WebSocketBlockedConsumer",
      [state, channel, aCapacity, aCount, aOpcode]() {
        const uint32_t headerSize = aCapacity < 126     ? 2
                                    : aCapacity < 65536 ? 4
                                                        : 10;
        std::vector<uint8_t> frame(headerSize + aCapacity, 0);
        frame[0] = aOpcode;
        frame[1] = aCapacity < 126 ? aCapacity : aCapacity < 65536 ? 126 : 127;
        if (aCapacity < 126) {
        } else if (aCapacity < 65536) {
          NetworkEndian::writeUint16(frame.data() + 2, aCapacity);
        } else {
          NetworkEndian::writeUint64(frame.data() + 2, aCapacity);
        }
        uint32_t accepted = 0;
        nsresult status = NS_OK;
        for (; accepted < aCount; ++accepted) {
          status = channel->OnDataReceived(frame.data(), frame.size());
          if (NS_FAILED(status)) {
            break;
          }
        }
        std::lock_guard lock(state->mMutex);
        state->mAccepted = accepted;
        state->mStatus = status;
        state->mDone = true;
        state->mCondition.notify_one();
      }));
  EXPECT_EQ(dispatched, NS_OK);
  if (NS_FAILED(dispatched)) {
    return state;
  }
  std::unique_lock lock(state->mMutex);
  EXPECT_TRUE(state->mCondition.wait_for(
      lock, std::chrono::seconds(5), [&]() { return state->mDone.load(); }));
  return state;
}

void DrainMessages(MessageCounter* aListener, uint32_t aExpected) {
  while (aListener->mMessages < aExpected) {
    ASSERT_TRUE(NS_ProcessNextEvent(nullptr, false));
  }
}

TEST(NaiveFoxNoConnectWebSocket, BlockedConsumerRejectsFillerFlood)
{
  RefPtr<WebSocketChannel> channel = new WebSocketSSLChannel();
  RefPtr<MessageCounter> listener = new MessageCounter();
  nsCOMPtr<nsIThread> producer;
  ASSERT_EQ(NS_NewNamedThread("WSIngressTest", getter_AddRefs(producer)),
            NS_OK);
  auto shutdown = MakeScopeExit([&]() { producer->Shutdown(); });
  NaiveFoxWebSocketTestPeer::Prepare(channel, listener, producer);
  ASSERT_EQ(channel->RetargetDeliveryTo(GetMainThreadSerialEventTarget()),
            NS_OK);

  auto result = FeedWhileConsumerBlocked(channel, producer, 512, 33);
  ASSERT_TRUE(result->mDone);
  EXPECT_EQ(result->mAccepted, 32U);
  EXPECT_EQ(result->mStatus, NS_ERROR_FILE_TOO_BIG);
  EXPECT_EQ(listener->mMessages, 0U);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingMessages(channel), 32U);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingBytes(channel), 32U * 512);
  DrainMessages(listener, 32);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingMessages(channel), 0U);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingBytes(channel), 0U);
}

TEST(NaiveFoxNoConnectWebSocket, BlockedConsumerBoundsBytesAndReleasesBudget)
{
  RefPtr<WebSocketChannel> channel = new WebSocketSSLChannel();
  RefPtr<MessageCounter> listener = new MessageCounter();
  nsCOMPtr<nsIThread> producer;
  ASSERT_EQ(NS_NewNamedThread("WSIngressTest", getter_AddRefs(producer)),
            NS_OK);
  auto shutdown = MakeScopeExit([&]() { producer->Shutdown(); });
  NaiveFoxWebSocketTestPeer::Prepare(channel, listener, producer);
  ASSERT_EQ(channel->RetargetDeliveryTo(GetMainThreadSerialEventTarget()),
            NS_OK);

  auto result = FeedWhileConsumerBlocked(channel, producer, 256 * 1024, 9);
  ASSERT_TRUE(result->mDone);
  EXPECT_EQ(result->mAccepted, 8U);
  EXPECT_EQ(result->mStatus, NS_ERROR_FILE_TOO_BIG);
  EXPECT_EQ(listener->mMessages, 0U);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingMessages(channel), 8U);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingBytes(channel), 2U * 1024 * 1024);
  DrainMessages(listener, 8);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingBytes(channel), 0U);
  result = FeedWhileConsumerBlocked(channel, producer, 256 * 1024, 1);
  ASSERT_TRUE(result->mDone);
  EXPECT_EQ(result->mAccepted, 1U);
  EXPECT_EQ(result->mStatus, NS_OK);
  DrainMessages(listener, 9);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingMessages(channel), 0U);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingBytes(channel), 0U);
}

TEST(NaiveFoxNoConnectWebSocket, BlockedWriterRejectsPingFlood)
{
  RefPtr<WebSocketChannel> channel = new WebSocketSSLChannel();
  RefPtr<MessageCounter> listener = new MessageCounter();
  RefPtr<FixedMaskGenerator> random = new FixedMaskGenerator();
  ASSERT_TRUE(random);
  nsCOMPtr<nsIThread> producer;
  ASSERT_EQ(NS_NewNamedThread("WSIngressTest", getter_AddRefs(producer)),
            NS_OK);
  auto shutdown = MakeScopeExit([&]() { producer->Shutdown(); });
  NaiveFoxWebSocketTestPeer::Prepare(channel, listener, producer);
  NaiveFoxWebSocketTestPeer::SetRandomGenerator(channel, random);
  ASSERT_EQ(channel->RetargetDeliveryTo(GetMainThreadSerialEventTarget()),
            NS_OK);
  auto result = FeedWhileConsumerBlocked(channel, producer, 1, 33, 0x89);
  ASSERT_TRUE(result->mDone);
  EXPECT_EQ(result->mAccepted, 32U);
  EXPECT_EQ(result->mStatus, NS_ERROR_FILE_TOO_BIG);
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::PendingPongs(channel), 32U);
  EXPECT_EQ(listener->mMessages, 0U);
}

TEST(NaiveFoxNoConnectWebSocket, RejectsUnsolicitedCompressionBeforeReading)
{
  RefPtr<WebSocketChannel> channel = new WebSocketSSLChannel();
  RefPtr<MessageCounter> listener = new MessageCounter();
  NaiveFoxWebSocketTestPeer::Prepare(channel, listener,
                                     GetMainThreadSerialEventTarget());
  ASSERT_EQ(channel->RetargetDeliveryTo(GetMainThreadSerialEventTarget()),
            NS_OK);
  RefPtr<UnsolicitedCompression> response = new UnsolicitedCompression();
  EXPECT_EQ(NaiveFoxWebSocketTestPeer::HandleExtensions(channel, response),
            NS_ERROR_ILLEGAL_VALUE);
  EXPECT_FALSE(NaiveFoxWebSocketTestPeer::CanReadCompressedData(channel));
  while (!listener->mStops) {
    ASSERT_TRUE(NS_ProcessNextEvent(nullptr, false));
  }
  EXPECT_EQ(listener->mStatus, NS_ERROR_ILLEGAL_VALUE);
  EXPECT_EQ(listener->mMessages, 0U);
  NS_ProcessPendingEvents(nullptr);
}

}  // namespace
}  // namespace mozilla::net

#endif
