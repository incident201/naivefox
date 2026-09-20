/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifdef MOZ_NAIVEFOX
#  include <atomic>
#  include <chrono>
#  include <memory>

#  include "Http2Session.h"
#  include "Http2StreamBase.h"
#  include "gtest/gtest.h"
#  include "mozilla/EndianUtils.h"
#  include "mozilla/ScopeExit.h"
#  include "mozilla/SpinEventLoopUntil.h"
#  include "nsIIOService.h"
#  include "nsIInputStream.h"
#  include "nsIProtocolHandler.h"
#  include "nsNetCID.h"
#  include "nsNetUtil.h"
#  include "nsServiceManagerUtils.h"
#  include "nsStringStream.h"
#  include "nsThreadUtils.h"

namespace mozilla::net {
class NaiveFoxHttp2UploadTestPeer final {
 public:
  static void Fill(Http2Session* session) {
    session->mOutputQueueUsed =
        session->mOutputQueueSize - Http2Session::kQueueReserved;
    session->mOutputQueueSent = 0;
  }
  static void Drain(Http2Session* session) {
    session->mOutputQueueUsed = session->mOutputQueueSent = 0;
  }
  static nsCString Output(Http2Session* session) {
    return nsCString(session->mOutputQueueBuffer.get(),
                     session->mOutputQueueUsed);
  }
};

namespace {
class BodyStream final : public Http2StreamBase {
 public:
  BodyStream(Http2Session* session, const nsACString& body)
      : Http2StreamBase(0, session, 0, 0) {
    mStreamID = 3;
    mRequestHeadersDone = mOpenGenerated = 1;
    mUpstreamState = GENERATING_BODY;
    mRequestBodyLenRemaining = body.Length();
    MOZ_ALWAYS_SUCCEEDS(
        NS_NewCStringInputStream(getter_AddRefs(mSource), body));
  }
  void CloseStream(nsresult) override {}
  uint64_t Available() {
    uint64_t size = 0;
    MOZ_ALWAYS_SUCCEEDS(mSource->Available(&size));
    return size;
  }

 protected:
  nsresult CallToReadData(uint32_t count, uint32_t* read) override {
    return mSource->ReadSegments(
        [](nsIInputStream*, void* closure, const char* data, uint32_t,
           uint32_t size, uint32_t* used) {
          return static_cast<BodyStream*>(closure)->OnReadSegment(data, size,
                                                                  used);
        },
        this, count, read);
  }
  nsresult CallToWriteData(uint32_t, uint32_t*) override {
    return NS_ERROR_NOT_IMPLEMENTED;
  }
  nsresult GenerateHeaders(nsCString&, uint8_t&) override {
    return NS_ERROR_NOT_IMPLEMENTED;
  }

 private:
  nsCOMPtr<nsIInputStream> mSource;
};

void CheckBlockedTail(uint32_t size) {
  RefPtr<Http2Session> session =
      Http2Session::CreateSession(nullptr, SpdyVersion::HTTP_2, false);
  nsCString body;
  for (uint32_t i = 0; i < size; ++i) body.Append(char(i * 31));
  RefPtr<BodyStream> stream = new BodyStream(session, body);
  NaiveFoxHttp2UploadTestPeer::Fill(session);
  for (int attempt = 0; attempt < 2; ++attempt) {
    uint32_t read = 0;
    nsresult rv = stream->ReadSegments(session, size, &read);
    EXPECT_TRUE(NS_SUCCEEDED(rv) || rv == NS_BASE_STREAM_WOULD_BLOCK);
    EXPECT_EQ(read, 0u);
    EXPECT_EQ(stream->Available(), uint64_t(size));
  }
  NaiveFoxHttp2UploadTestPeer::Drain(session);
  uint32_t read = 0;
  ASSERT_EQ(stream->ReadSegments(session, size, &read), NS_OK);
  EXPECT_EQ(read, size);
  EXPECT_EQ(stream->Available(), 0u);
  nsCString output = NaiveFoxHttp2UploadTestPeer::Output(session);
  ASSERT_EQ(output.Length(), size + 9);
  const auto* bytes = reinterpret_cast<const uint8_t*>(output.BeginReading());
  EXPECT_EQ((uint32_t(bytes[0]) << 16) | (uint32_t(bytes[1]) << 8) | bytes[2],
            size);
  EXPECT_EQ(bytes[3], Http2Session::FRAME_TYPE_DATA);
  EXPECT_EQ(bytes[4], 1u);
  EXPECT_EQ(NetworkEndian::readUint32(bytes + 5), 3u);
  EXPECT_EQ(Substring(output, 9), body);
  ASSERT_EQ(stream->ReadSegments(session, size, &read), NS_OK);
  EXPECT_EQ(read, 0u);
  EXPECT_EQ(NaiveFoxHttp2UploadTestPeer::Output(session), output);
}

TEST(NaiveFoxHttp2Upload, BlockedQueueKeepsBodyUntilFrameCommitment)
{
  nsCOMPtr<nsIIOService> io = do_GetIOService();
  ASSERT_TRUE(io);
  nsCOMPtr<nsIProtocolHandler> http;
  ASSERT_EQ(io->GetProtocolHandler("http", getter_AddRefs(http)), NS_OK);
  nsCOMPtr<nsIEventTarget> socketThread =
      do_GetService(NS_SOCKETTRANSPORTSERVICE_CONTRACTID);
  ASSERT_TRUE(socketThread);
  auto done = std::make_shared<std::atomic<bool>>(false);
  ASSERT_EQ(socketThread->Dispatch(NS_NewRunnableFunction(
                "NaiveFox::Http2BlockedUpload",
                [done]() {
                  auto finish = MakeScopeExit([done]() { done->store(true); });
                  for (uint32_t size : {70u, 202u, 1296u, 8192u})
                    CheckBlockedTail(size);
                })),
            NS_OK);
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  ASSERT_TRUE(SpinEventLoopUntil("NaiveFox::Http2BlockedUpload"_ns, [&]() {
    return done->load() || std::chrono::steady_clock::now() >= deadline;
  }));
  EXPECT_TRUE(done->load());
}
}  // namespace
}  // namespace mozilla::net
#endif
