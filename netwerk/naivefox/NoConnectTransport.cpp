/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NoConnectTransport.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <deque>
#include <limits>
#include <utility>
#include <vector>

#include "NeckoTunnel.h"
#include "NoConnectCodec.h"
#include "NoConnectHash.h"
#include "NoConnectSite.h"
#include "NoConnectWebSocket.h"
#include "RuntimeLogging.h"
#include "TunnelSession.h"
#include "mozilla/Base64.h"
#include "mozilla/RefPtr.h"
#include "nsIChannel.h"
#include "nsIHttpChannel.h"
#include "nsIInputStream.h"
#include "nsIStreamListener.h"
#include "nsITimer.h"
#include "nsIUploadChannel2.h"
#include "nsNetUtil.h"
#include "nsStringStream.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

using noconnect::Frame;
using noconnect::Kind;
using Bytes = std::vector<uint8_t>;

namespace {

constexpr size_t kInitialBuffer = 64 * 1024;
constexpr std::array<size_t, 20> kStartupSlots = {
    8192,  8192,  8192,  8192,  32768, 32768, 65536, 65536, 65536, 65536,
    65536, 65536, 65536, 65536, 65536, 65536, 65536, 65536, 8192,  8192};

enum class CarrierBody { Fixed, Document, Style, Script, Image };

class CarrierRequest final : public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  using Callback = std::function<void(CarrierRequest*, nsresult)>;
  CarrierRequest(size_t aSize, uint32_t aStatus, bool aCell,
                 ProxyProtocol aProtocol, Callback&& aDone,
                 CarrierBody aBodyKind)
      : mExpectedSize(aSize),
        mExpectedStatus(aStatus),
        mCell(aCell),
        mProtocol(aProtocol),
        mDone(std::move(aDone)),
        mBodyKind(aBodyKind) {
    if (aBodyKind == CarrierBody::Document) {
      mSite = MakeUnique<NoConnectSite>();
    }
  }

  nsresult Start(const TunnelConfig& aConfig, const nsACString& aPath,
                 const nsACString& aCookie, const Bytes* aUpload) {
    nsContentPolicyType policy = nsIContentPolicy::TYPE_OTHER;
    switch (mBodyKind) {
      case CarrierBody::Document:
        policy = nsIContentPolicy::TYPE_DOCUMENT;
        break;
      case CarrierBody::Style:
        policy = nsIContentPolicy::TYPE_STYLESHEET;
        break;
      case CarrierBody::Script:
        policy = nsIContentPolicy::TYPE_SCRIPT;
        break;
      case CarrierBody::Image:
        policy = nsIContentPolicy::TYPE_IMAGE;
        break;
      case CarrierBody::Fixed:
        break;
    }
    MOZ_TRY(CreateNoConnectChannel(aConfig.mProxyUrl, aPath, mProtocol,
                                   aConfig.mHostResolverRule,
                                   getter_AddRefs(mChannel), policy));
    nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(mChannel);
    if (!http) {
      return NS_ERROR_FAILURE;
    }
    MOZ_TRY(http->SetRequestHeader("Accept-Encoding"_ns, "identity"_ns, false));
    if (!aCookie.IsEmpty()) {
      MOZ_TRY(http->SetRequestHeader("Cookie"_ns, aCookie, false));
    }
    if (aUpload) {
      nsCOMPtr<nsIInputStream> input;
      nsCString body(reinterpret_cast<const char*>(aUpload->data()),
                     aUpload->size());
      MOZ_TRY(NS_NewCStringInputStream(getter_AddRefs(input), std::move(body)));
      nsCOMPtr<nsIUploadChannel2> upload = do_QueryInterface(mChannel);
      if (!upload) {
        return NS_ERROR_FAILURE;
      }
      MOZ_TRY(upload->ExplicitSetUploadStream(
          input, "application/octet-stream"_ns, aUpload->size(), "POST"_ns));
    } else {
      MOZ_TRY(http->SetRequestMethod("GET"_ns));
    }
    RefPtr self = this;
    auto timer = NS_NewTimerWithCallback(
        [self](nsITimer*) { self->Cancel(NS_ERROR_NET_TIMEOUT); }, 45000,
        nsITimer::TYPE_ONE_SHOT, "NaiveFox::NoConnectRequestDeadline"_ns);
    if (timer.isErr()) {
      return timer.unwrapErr();
    }
    mTimer = timer.unwrap();
    nsresult rv = mChannel->AsyncOpen(this);
    if (NS_FAILED(rv)) {
      mTimer->Cancel();
      mTimer = nullptr;
      mChannel = nullptr;
      mDone = nullptr;
    }
    return rv;
  }

  void Cancel(nsresult aStatus) {
    if (mTimer) {
      mTimer->Cancel();
      mTimer = nullptr;
    }
    if (mChannel) {
      mChannel->Cancel(aStatus);
    }
  }

  Bytes mBody;
  nsCString mCookie;
  nsCString mMime;
  nsCString mDigest;
  UniquePtr<NoConnectSite> mSite;

 private:
  ~CarrierRequest() = default;
  const size_t mExpectedSize;
  const uint32_t mExpectedStatus;
  const bool mCell;
  const ProxyProtocol mProtocol;
  Callback mDone;
  nsCOMPtr<nsIChannel> mChannel;
  nsCOMPtr<nsITimer> mTimer;
  const CarrierBody mBodyKind;
  NoConnectHash mHash;
  uint64_t mPublicLength = 0;
  uint64_t mPublicReceived = 0;
};

NS_IMPL_ISUPPORTS(CarrierRequest, nsIStreamListener, nsIRequestObserver)

NS_IMETHODIMP CarrierRequest::OnStartRequest(nsIRequest* aRequest) {
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(aRequest);
  if (!http) {
    return NS_ERROR_UNEXPECTED;
  }
  uint32_t status = 0;
  nsAutoCString protocol;
  MOZ_TRY(http->GetResponseStatus(&status));
  MOZ_TRY(http->GetProtocolVersion(protocol));
  if (status != mExpectedStatus ||
      !(mProtocol == ProxyProtocol::H3 ? protocol.EqualsLiteral("h3")
                                       : protocol.EqualsLiteral("h2"))) {
    return NS_ERROR_FAILURE;
  }
  nsAutoCString encoding;
  if (NS_SUCCEEDED(http->GetResponseHeader("Content-Encoding"_ns, encoding)) &&
      !encoding.IsEmpty() && !encoding.EqualsLiteral("identity")) {
    return NS_ERROR_CORRUPTED_CONTENT;
  }
  if (mExpectedStatus == 200) {
    int64_t length = -1;
    MOZ_TRY(mChannel->GetContentLength(&length));
    if (mBodyKind == CarrierBody::Fixed) {
      if (length != static_cast<int64_t>(mExpectedSize)) {
        return NS_ERROR_CORRUPTED_CONTENT;
      }
    } else {
      if (length < 0 || (mBodyKind == CarrierBody::Document && !length)) {
        return NS_ERROR_CORRUPTED_CONTENT;
      }
      mPublicLength = static_cast<uint64_t>(length);
      nsAutoCString type;
      MOZ_TRY(mChannel->GetContentType(type));
      mMime = type;
      const bool valid =
          (mBodyKind == CarrierBody::Document &&
           type.EqualsLiteral("text/html")) ||
          (mBodyKind == CarrierBody::Style && type.EqualsLiteral("text/css")) ||
          (mBodyKind == CarrierBody::Script &&
           (type.EqualsLiteral("text/javascript") ||
            type.EqualsLiteral("application/javascript"))) ||
          (mBodyKind == CarrierBody::Image &&
           StringBeginsWith(type, "image/"_ns));
      if (!valid) {
        return NS_ERROR_CORRUPTED_CONTENT;
      }
    }
  }
  if (mCell) {
    nsAutoCString type;
    MOZ_TRY(mChannel->GetContentType(type));
    if (!type.EqualsLiteral("application/octet-stream")) {
      return NS_ERROR_CORRUPTED_CONTENT;
    }
  }
  (void)http->GetResponseHeader("Set-Cookie"_ns, mCookie);
  return NS_OK;
}

NS_IMETHODIMP CarrierRequest::OnDataAvailable(nsIRequest*,
                                              nsIInputStream* aInput, uint64_t,
                                              uint32_t aCount) {
  if (mBodyKind != CarrierBody::Fixed) {
    if (aCount > mPublicLength - mPublicReceived) {
      return NS_ERROR_CORRUPTED_CONTENT;
    }
    std::array<uint8_t, 16384> buffer;
    while (aCount) {
      uint32_t read = 0;
      MOZ_TRY(aInput->Read(reinterpret_cast<char*>(buffer.data()),
                           std::min(aCount, uint32_t(buffer.size())), &read));
      if (!read) {
        return NS_ERROR_UNEXPECTED;
      }
      if (mSite) {
        MOZ_TRY(mSite->Feed(Span<const uint8_t>(buffer.data(), read)));
      }
      if (!mHash.Update(Span<const uint8_t>(buffer.data(), read))) {
        return NS_ERROR_OUT_OF_MEMORY;
      }
      mPublicReceived += read;
      aCount -= read;
    }
    return NS_OK;
  }
  if (aCount > mExpectedSize - mBody.size()) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  const size_t offset = mBody.size();
  mBody.resize(offset + aCount);
  size_t readOffset = offset;
  while (aCount) {
    uint32_t read = 0;
    MOZ_TRY(aInput->Read(reinterpret_cast<char*>(mBody.data() + readOffset),
                         aCount, &read));
    if (!read) {
      return NS_ERROR_UNEXPECTED;
    }
    readOffset += read;
    aCount -= read;
  }
  return NS_OK;
}

NS_IMETHODIMP CarrierRequest::OnStopRequest(nsIRequest*, nsresult aStatus) {
  if (mTimer) {
    mTimer->Cancel();
    mTimer = nullptr;
  }
  mChannel = nullptr;
  if (NS_SUCCEEDED(aStatus)) {
    if (mBodyKind == CarrierBody::Fixed) {
      if (mBody.size() != mExpectedSize) {
        aStatus = NS_ERROR_CORRUPTED_CONTENT;
      }
    } else if (mPublicReceived != mPublicLength) {
      aStatus = NS_ERROR_CORRUPTED_CONTENT;
    } else if (mSite) {
      aStatus = mSite->Feed(Span<const uint8_t>(), true);
    }
  }
  if (NS_SUCCEEDED(aStatus) && mBodyKind != CarrierBody::Fixed &&
      !mHash.Finish(mDigest)) {
    aStatus = NS_ERROR_FAILURE;
  }
  if (mDone) {
    auto done = std::move(mDone);
    done(this, aStatus);
  }
  return NS_OK;
}

std::vector<RefPtr<NoConnectCarrier>> sCarriers;

}  // namespace

class NoConnectStream::Impl final {
 public:
  Impl(nsIAsyncInputStream* aInput, nsIAsyncOutputStream* aOutput,
       const TunnelConfig& aConfig, nsIEventTarget* aTarget,
       std::function<void()>&& aEstablished,
       std::function<void(nsresult)>&& aFailed,
       std::function<void(nsresult)>&& aClosed)
      : input(aInput),
        output(aOutput),
        config(aConfig),
        target(aTarget),
        established(std::move(aEstablished)),
        failed(std::move(aFailed)),
        closed(std::move(aClosed)) {}
  nsCOMPtr<nsIAsyncInputStream> input;
  nsCOMPtr<nsIAsyncOutputStream> output;
  TunnelConfig config;
  nsCOMPtr<nsIEventTarget> target;
  std::function<void()> established;
  std::function<void(nsresult)> failed;
  std::function<void(nsresult)> closed;
  RefPtr<NoConnectCarrier> carrier;
  UniquePtr<noconnect::StreamState> state;
  nsCOMPtr<nsITimer> deadline;
  nsCString authority;
  noconnect::UploadBuffer upload;
  std::deque<Bytes> download;
  size_t downloadOffset = 0;
  std::atomic<bool> cancelled{false};
  bool openPending = true;
  bool pump = false;
  bool eof = false;
  bool outputClosed = false;
  uint32_t finUpload = UINT32_MAX;
  bool finConfirmed = false;
  bool done = false;

  size_t UploadLimit() const { return noconnect::kMaxCell; }
};

class NoConnectCarrier final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(NoConnectCarrier)
  explicit NoConnectCarrier(const TunnelConfig& aConfig) : mConfig(aConfig) {}
  bool Closed() const { return mClosed; }
  bool CanAttach() const {
    return !mClosed && mStreams.size() < noconnect::kMaxStreams &&
           mHighestStream != UINT32_MAX;
  }
  bool Matches(const TunnelConfig& aConfig) const {
    if (mClosed || mConfig.mTransport != aConfig.mTransport ||
        mConfig.mProtocol != aConfig.mProtocol ||
        !mConfig.mProxyUrl.Equals(aConfig.mProxyUrl) ||
        !mConfig.mProxyUser.Equals(aConfig.mProxyUser) ||
        !mConfig.mProxyPassword.Equals(aConfig.mProxyPassword) ||
        mConfig.mHostResolverRule.isSome() !=
            aConfig.mHostResolverRule.isSome()) {
      return false;
    }
    return !mConfig.mHostResolverRule ||
           (mConfig.mHostResolverRule->mLogicalHost.Equals(
                aConfig.mHostResolverRule->mLogicalHost) &&
            mConfig.mHostResolverRule->mPhysicalHost.Equals(
                aConfig.mHostResolverRule->mPhysicalHost));
  }
  void Attach(NoConnectStream* aStream);
  void Detach(NoConnectStream* aStream, bool aReset);
  void Wake();
  void Fail(nsresult aStatus);

 private:
  ~NoConnectCarrier() = default;
  using Callback = CarrierRequest::Callback;
  bool Open(const nsACString& aPath, const Bytes* aUpload, size_t aSize,
            uint32_t aStatus, bool aCell, Callback&& aDone,
            CarrierBody aBodyKind = CarrierBody::Fixed);
  bool Upload(size_t aCapacity, Bytes& aBody);
  bool Receive(CarrierRequest* aRequest);
  bool ReceiveCell(const Bytes& aBody, bool aWebSocket);
  void ConfirmUpload(uint32_t aSequence);
  void RetireStreams();
  void Start();
  void LoadAssets();
  void Tick();
  void Startup();
  void Continue();
  size_t Pressure(bool& aControl) const;
  void StartWebSocket();
  void WebSocketTick(bool aHeartbeat = false);
  void ScheduleWebSocket(uint32_t aDelay, bool aHeartbeat);
  bool HasPendingOpen() const;
  uint32_t WebSocketDelay(size_t aBytes) const;

  TunnelConfig mConfig;
  nsCString mCookie;
  nsCString mSiteIdentity;
  nsCString mRootDigest;
  bool mContractReady = false;
  nsTArray<SiteResource> mResources;
  size_t mNextAsset = 0;
  std::vector<RefPtr<NoConnectStream>> mStreams;
  std::vector<RefPtr<CarrierRequest>> mRequests;
  std::vector<Frame> mResets;
  uint32_t mHighestStream = 0;
  uint32_t mUp = 0;
  uint32_t mDown = 0;
  size_t mCursor = 0;
  size_t mStartup = 0;
  size_t mAssets = 0;
  bool mAuthed = false;
  bool mStarted = false;
  bool mBootstrapped = false;
  bool mBusy = false;
  bool mQueued = false;
  bool mClosed = false;
  RefPtr<NoConnectWebSocket> mWebSocket;
  nsCOMPtr<nsITimer> mWebSocketTimer;
  nsCOMPtr<nsITimer> mWebSocketDeadline;
  size_t mWebSocketPending = 0;
  uint32_t mWebSocketAck = 0;
  uint32_t mWebSocketScheduledDelay = 0;
  bool mWebSocketReady = false;
  bool mWebSocketHeartbeat = false;
};

NS_IMPL_ISUPPORTS(NoConnectStream, nsIInputStreamCallback,
                  nsIOutputStreamCallback)

NoConnectStream::NoConnectStream(nsIAsyncInputStream* aInput,
                                 nsIAsyncOutputStream* aOutput,
                                 const TunnelConfig& aConfig,
                                 nsIEventTarget* aSocketTarget,
                                 std::function<void()>&& aEstablished,
                                 std::function<void(nsresult)>&& aFailed,
                                 std::function<void(nsresult)>&& aClosed)
    : mImpl(MakeUnique<Impl>(aInput, aOutput, aConfig, aSocketTarget,
                             std::move(aEstablished), std::move(aFailed),
                             std::move(aClosed))) {}

NoConnectStream::~NoConnectStream() = default;

nsresult NoConnectStream::Start(const nsACString& aAuthority,
                                Span<const uint8_t> aInitial) {
  if (aAuthority.IsEmpty() || aAuthority.Length() > 512 ||
      aInitial.Length() > kInitialBuffer ||
      mImpl->config.mProtocol == ProxyProtocol::Auto) {
    return NS_ERROR_INVALID_ARG;
  }
  RefPtr self = this;
  nsCString authority(aAuthority);
  Bytes initial(aInitial.begin(), aInitial.end());
  return NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NoConnectAttach",
      [self, authority = std::move(authority), initial = std::move(initial)]() {
        auto& state = *self->mImpl;
        if (state.cancelled) {
          self->Finish(NS_BINDING_ABORTED, false);
          return;
        }
        state.authority = authority;
        if (!state.upload.Append(initial.data(), initial.size())) {
          self->Finish(NS_ERROR_OUT_OF_MEMORY, false);
          return;
        }
        sCarriers.erase(std::remove_if(sCarriers.begin(), sCarriers.end(),
                                       [](const auto& carrier) {
                                         return carrier->Closed();
                                       }),
                        sCarriers.end());
        for (const auto& carrier : sCarriers) {
          if (carrier->Matches(state.config) && carrier->CanAttach()) {
            carrier->Attach(self);
            return;
          }
        }
        RefPtr carrier = new NoConnectCarrier(state.config);
        sCarriers.push_back(carrier);
        carrier->Attach(self);
      }));
}

nsresult NoConnectStream::StartPump() {
  RefPtr self = this;
  return NS_DispatchToMainThread(
      NS_NewRunnableFunction("NaiveFox::NoConnectPump", [self]() {
        if (!self->mImpl->done && !self->mImpl->cancelled) {
          self->mImpl->pump = true;
          self->Flush();
          self->ArmRead();
        }
      }));
}

void NoConnectStream::Cancel(nsresult aStatus) {
  mImpl->cancelled = true;
  RefPtr self = this;
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::NoConnectCancel",
      [self, aStatus]() { self->Finish(aStatus, true); }));
}

void NoConnectStream::Finish(nsresult aStatus, bool aReset) {
  MOZ_ASSERT(NS_IsMainThread());
  auto& s = *mImpl;
  if (s.done) {
    return;
  }
  s.done = true;
  if (s.deadline) {
    s.deadline->Cancel();
    s.deadline = nullptr;
  }
  (void)s.input->AsyncWait(nullptr, 0, 0, nullptr);
  (void)s.output->AsyncWait(nullptr, 0, 0, nullptr);
  if (s.carrier) {
    RefPtr carrier = std::move(s.carrier);
    carrier->Detach(this, aReset);
  }
  auto callback = s.state && s.state->IsOpened() ? std::move(s.closed)
                                                 : std::move(s.failed);
  s.established = nullptr;
  s.failed = nullptr;
  s.closed = nullptr;
  s.upload.Clear();
  s.download.clear();
  if (callback) {
    (void)s.target->Dispatch(NS_NewRunnableFunction(
        "NaiveFox::NoConnectClosed",
        [callback = std::move(callback), aStatus]() { callback(aStatus); }));
  }
}

void NoConnectStream::ArmRead() {
  auto& s = *mImpl;
  if (s.done || s.cancelled || !s.pump || s.eof || !s.state ||
      !s.state->IsOpened() || s.upload.Size() >= s.UploadLimit() ||
      s.upload.Size() >= s.state->SendCredit()) {
    return;
  }
  nsresult rv =
      s.input->AsyncWait(this, 0, 0, GetMainThreadSerialEventTarget());
  if (NS_FAILED(rv)) {
    Finish(rv, true);
  }
}

NS_IMETHODIMP NoConnectStream::OnInputStreamReady(nsIAsyncInputStream*) {
  MOZ_ASSERT(NS_IsMainThread());
  auto& s = *mImpl;
  if (s.done || s.cancelled) {
    return NS_OK;
  }
  std::array<uint8_t, 16384> buffer;
  while (s.upload.Size() < s.UploadLimit() &&
         s.upload.Size() < s.state->SendCredit()) {
    const size_t length =
        std::min({buffer.size(), s.UploadLimit() - s.upload.Size(),
                  s.state->SendCredit() - s.upload.Size()});
    uint32_t read = 0;
    nsresult rv =
        s.input->Read(reinterpret_cast<char*>(buffer.data()), length, &read);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      ArmRead();
      break;
    }
    if (rv == NS_BASE_STREAM_CLOSED || (NS_SUCCEEDED(rv) && !read)) {
      s.eof = true;
      break;
    }
    if (NS_FAILED(rv)) {
      Finish(rv, true);
      return NS_OK;
    }
    if (!s.upload.Append(buffer.data(), read)) {
      Finish(NS_ERROR_OUT_OF_MEMORY, true);
      return NS_OK;
    }
  }
  if (s.carrier) {
    s.carrier->Wake();
  }
  return NS_OK;
}

void NoConnectStream::Flush() {
  auto& s = *mImpl;
  if (s.done || s.cancelled || !s.pump) {
    return;
  }
  while (!s.download.empty()) {
    auto& body = s.download.front();
    uint32_t written = 0;
    nsresult rv = s.output->Write(
        reinterpret_cast<const char*>(body.data() + s.downloadOffset),
        body.size() - s.downloadOffset, &written);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      rv = s.output->AsyncWait(this, 0, 0, GetMainThreadSerialEventTarget());
      if (NS_FAILED(rv)) {
        Finish(rv, true);
      }
      return;
    }
    if (NS_FAILED(rv) || !written || !s.state->Delivered(written)) {
      Finish(NS_FAILED(rv) ? rv : NS_ERROR_FAILURE, true);
      return;
    }
    s.downloadOffset += written;
    if (s.downloadOffset == body.size()) {
      s.download.pop_front();
      s.downloadOffset = 0;
    }
  }
  if (s.state->ReceivedFin() && !s.outputClosed) {
    s.outputClosed = true;
    (void)s.output->CloseWithStatus(NS_OK);
  }
  if (s.carrier) {
    s.carrier->Wake();
  }
}

NS_IMETHODIMP NoConnectStream::OnOutputStreamReady(nsIAsyncOutputStream*) {
  MOZ_ASSERT(NS_IsMainThread());
  Flush();
  return NS_OK;
}

bool NoConnectCarrier::Open(const nsACString& aPath, const Bytes* aUpload,
                            size_t aSize, uint32_t aStatus, bool aCell,
                            Callback&& aDone, CarrierBody aBodyKind) {
  if (mClosed) {
    return false;
  }
  RefPtr self = this;
  const bool uploaded = aUpload != nullptr;
  const uint32_t uploadSequence = uploaded ? mUp - 1 : 0;
  RefPtr request = new CarrierRequest(
      aSize, aStatus, aCell, mConfig.mProtocol,
      [self, uploaded, uploadSequence, done = std::move(aDone)](
          CarrierRequest* aRequest, nsresult aResult) {
        auto& requests = self->mRequests;
        requests.erase(std::remove(requests.begin(), requests.end(), aRequest),
                       requests.end());
        if (!self->mClosed) {
          if (NS_FAILED(aResult)) {
            self->Fail(aResult);
          } else {
            if (uploaded) {
              self->ConfirmUpload(uploadSequence);
            }
            done(aRequest, aResult);
          }
        }
      },
      aBodyKind);
  mRequests.push_back(request);
  nsresult rv = request->Start(mConfig, aPath, mCookie, aUpload);
  if (NS_FAILED(rv)) {
    Fail(rv);
    return false;
  }
  return true;
}

void NoConnectCarrier::Attach(NoConnectStream* aStream) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!CanAttach()) {
    aStream->Finish(NS_ERROR_NOT_AVAILABLE, false);
    return;
  }
  auto& s = *aStream->mImpl;
  s.carrier = this;
  s.state = MakeUnique<noconnect::StreamState>(++mHighestStream);
  mStreams.push_back(aStream);
  RefPtr stream = aStream;
  auto timer = NS_NewTimerWithCallback(
      [stream](nsITimer*) { stream->Finish(NS_ERROR_NET_TIMEOUT, true); },
      30000, nsITimer::TYPE_ONE_SHOT, "NaiveFox::NoConnectOpenDeadline"_ns);
  if (timer.isErr()) {
    aStream->Finish(timer.unwrapErr(), true);
    return;
  }
  s.deadline = timer.unwrap();
  if (!mStarted) {
    Start();
  } else {
    Wake();
  }
}

void NoConnectCarrier::Detach(NoConnectStream* aStream, bool aReset) {
  auto& s = *aStream->mImpl;
  if (aReset && !mClosed && !s.openPending && s.state) {
    Frame reset;
    if (s.state->MakeReset(reset)) {
      if (mResets.size() >= noconnect::kMaxStreams) {
        Fail(NS_ERROR_FILE_TOO_BIG);
        return;
      }
      mResets.push_back(std::move(reset));
    }
  }
  mStreams.erase(std::remove(mStreams.begin(), mStreams.end(), aStream),
                 mStreams.end());
  Wake();
}

void NoConnectCarrier::Fail(nsresult aStatus) {
  if (mClosed) {
    return;
  }
  mClosed = true;
  if (mWebSocketTimer) {
    mWebSocketTimer->Cancel();
    mWebSocketTimer = nullptr;
  }
  if (mWebSocketDeadline) {
    mWebSocketDeadline->Cancel();
    mWebSocketDeadline = nullptr;
  }
  if (mWebSocket) {
    RefPtr socket = std::move(mWebSocket);
    socket->Close(aStatus);
  }
  auto requests = std::move(mRequests);
  for (const auto& request : requests) {
    request->Cancel(aStatus);
  }
  auto streams = mStreams;
  for (const auto& stream : streams) {
    stream->Finish(aStatus, false);
  }
  mStreams.clear();
  mCookie.Truncate();
  mConfig.mProxyUser.Truncate();
  mConfig.mProxyPassword.Truncate();
  mResets.clear();
  RuntimeLogEvent("No-connect carrier closed status=0x%08x\n",
                  static_cast<unsigned>(aStatus));
}

void NoConnectCarrier::Start() {
  mStarted = true;
  mBusy = true;
  RefPtr self = this;
  Open(
      "/"_ns, nullptr, 0, 200, false,
      [self](CarrierRequest* request, nsresult) {
        if (request->mCookie.Length() < 73 ||
            !StringBeginsWith(request->mCookie, "session="_ns) ||
            request->mCookie.CharAt(72) != ';') {
          self->Fail(NS_ERROR_CORRUPTED_CONTENT);
          return;
        }
        for (size_t i = 8; i < 72; ++i) {
          const char c = request->mCookie.CharAt(i);
          if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            self->Fail(NS_ERROR_CORRUPTED_CONTENT);
            return;
          }
        }
        self->mCookie.Assign(Substring(request->mCookie, 0, 72));
        self->mRootDigest = request->mDigest;
        self->mResources = request->mSite->TakeResources();
        self->LoadAssets();
      },
      CarrierBody::Document);
}

void NoConnectCarrier::LoadAssets() {
  if (mClosed) {
    return;
  }
  if (mAssets == mResources.Length()) {
    NoConnectHash snapshot;
    bool valid =
        snapshot.Field("naivefox-site-v2"_ns) && snapshot.Field(mRootDigest);
    for (const auto& resource : mResources) {
      const nsCString kind(resource.mKind == SiteResourceKind::Style ? "style"
                           : resource.mKind == SiteResourceKind::Script
                               ? "script"
                               : "image");
      valid = valid && snapshot.Field(resource.mPath) && snapshot.Field(kind) &&
              snapshot.Field(resource.mMime) &&
              snapshot.Field(resource.mDigest);
    }
    if (!valid || !snapshot.Finish(mSiteIdentity, true)) {
      Fail(NS_ERROR_FAILURE);
      return;
    }
    mBootstrapped = true;
    Continue();
    return;
  }
  RefPtr self = this;
  while (!mClosed && mNextAsset < mResources.Length() && mRequests.size() < 6) {
    const size_t index = mNextAsset++;
    const auto& resource = mResources[index];
    CarrierBody kind = CarrierBody::Image;
    if (resource.mKind == SiteResourceKind::Style) {
      kind = CarrierBody::Style;
    }
    if (resource.mKind == SiteResourceKind::Script) {
      kind = CarrierBody::Script;
    }
    Open(
        resource.mPath, nullptr, 0, 200, false,
        [self, index](CarrierRequest* request, nsresult) {
          self->mResources[index].mMime = request->mMime;
          self->mResources[index].mDigest = request->mDigest;
          ++self->mAssets;
          self->LoadAssets();
        },
        kind);
  }
}

bool NoConnectCarrier::Upload(size_t aCapacity, Bytes& aBody) {
  if (mUp == UINT32_MAX) {
    Fail(NS_ERROR_FILE_TOO_BIG);
    return false;
  }
  std::vector<Frame> frames;
  size_t budget = aCapacity - noconnect::kCellHeader;
  if (!mAuthed) {
    Frame auth;
    auth.kind = Kind::Auth;
    nsAutoCString credentials(mConfig.mProxyUser);
    credentials.Append(':');
    credentials.Append(mConfig.mProxyPassword);
    nsAutoCString authorization("Basic ");
    nsresult rv = Base64EncodeAppend(credentials, authorization);
    if (NS_FAILED(rv)) {
      Fail(rv);
      return false;
    }
    auth.body.assign(authorization.BeginReading(), authorization.EndReading());
    if (auth.Size() > budget) {
      Fail(NS_ERROR_INVALID_ARG);
      return false;
    }
    budget -= auth.Size();
    frames.push_back(std::move(auth));
    mAuthed = true;
    if (!noconnect::Encode(mUp++, aCapacity, frames, aBody)) {
      Fail(NS_ERROR_FAILURE);
      return false;
    }
    return true;
  }
  if (!mContractReady) {
    Fail(NS_ERROR_CORRUPTED_CONTENT);
    return false;
  }
  while (!mResets.empty() && budget >= noconnect::kFrameHeader) {
    budget -= mResets.back().Size();
    frames.push_back(std::move(mResets.back()));
    mResets.pop_back();
  }
  for (const auto& stream : mStreams) {
    auto& s = *stream->mImpl;
    if (!s.openPending) {
      continue;
    }
    if (s.authority.Length() + noconnect::kFrameHeader > budget) {
      break;
    }
    Frame frame;
    frame.kind = Kind::Open;
    frame.stream = s.state->Id();
    frame.body.assign(s.authority.BeginReading(), s.authority.EndReading());
    budget -= frame.Size();
    frames.push_back(std::move(frame));
    s.openPending = false;
  }
  size_t misses = 0;
  while (!mStreams.empty() && budget >= noconnect::kFrameHeader &&
         frames.size() < noconnect::kMaxFrames && misses < mStreams.size()) {
    mCursor %= mStreams.size();
    RefPtr stream = mStreams[mCursor++];
    auto& s = *stream->mImpl;
    Frame frame;
    bool have = false;
    if (!s.openPending && s.state->PendingCredit() &&
        budget >= noconnect::kFrameHeader + 4) {
      have = s.state->TakeCredit(frame);
    } else if (!s.openPending && !s.upload.Empty() &&
               budget > noconnect::kFrameHeader && s.state->SendCredit()) {
      const size_t length = std::min({s.upload.Size(), size_t(16384),
                                      size_t(s.state->SendCredit()),
                                      budget - noconnect::kFrameHeader});
      if (length <= s.upload.ContiguousSize()) {
        have = s.state->MakeData(s.upload.Data(), length, frame);
      } else {
        std::array<uint8_t, 16384> chunk;
        MOZ_ALWAYS_TRUE(s.upload.CopyTo(chunk.data(), length));
        have = s.state->MakeData(chunk.data(), length, frame);
      }
      if (!have) {
        stream->Finish(NS_ERROR_FILE_TOO_BIG, true);
        continue;
      }
      MOZ_ALWAYS_TRUE(s.upload.Consume(length));
      stream->ArmRead();
    } else if (!s.openPending && s.eof && s.upload.Empty() &&
               !s.state->SentFin()) {
      have = s.state->MakeFin(frame);
      s.finUpload = mUp;
    }
    if (have) {
      budget -= frame.Size();
      frames.push_back(std::move(frame));
      misses = 0;
    } else {
      ++misses;
    }
  }
  if (!noconnect::Encode(mUp++, aCapacity, frames, aBody)) {
    Fail(NS_ERROR_FAILURE);
    return false;
  }
  return true;
}

bool NoConnectCarrier::Receive(CarrierRequest* aRequest) {
  if (!ReceiveCell(aRequest->mBody, false)) {
    return false;
  }
  return true;
}

bool NoConnectCarrier::ReceiveCell(const Bytes& aBody, bool aWebSocket) {
  if (mDown == UINT32_MAX) {
    Fail(NS_ERROR_CORRUPTED_CONTENT);
    return false;
  }
  std::vector<Frame> frames;
  if (!noconnect::Decode(mDown, aBody.size(), aBody, frames)) {
    Fail(NS_ERROR_CORRUPTED_CONTENT);
    return false;
  }
  if (!mContractReady) {
    nsAutoCString expected("native-stream-v2\n");
    expected.Append(mSiteIdentity);
    if (aWebSocket || mDown || frames.size() != 1 ||
        frames[0].kind != Kind::Hello || frames[0].stream ||
        frames[0].sequence || frames[0].body.size() != expected.Length() ||
        !std::equal(
            frames[0].body.begin(), frames[0].body.end(),
            reinterpret_cast<const uint8_t*>(expected.BeginReading()))) {
      Fail(NS_ERROR_CORRUPTED_CONTENT);
      return false;
    }
    mContractReady = true;
    ++mDown;
    return true;
  }
  ++mDown;
  for (auto& frame : frames) {
    if (frame.kind == Kind::Ack) {
      if (!aWebSocket || frame.stream || !frame.body.empty() ||
          frame.sequence < mWebSocketAck || frame.sequence >= mUp) {
        Fail(NS_ERROR_CORRUPTED_CONTENT);
        return false;
      }
      mWebSocketAck = frame.sequence;
      ConfirmUpload(frame.sequence);
      continue;
    }
    if (!frame.stream || frame.stream > mHighestStream ||
        frame.kind == Kind::Open || frame.kind == Kind::Auth ||
        frame.kind == Kind::Hello) {
      Fail(NS_ERROR_CORRUPTED_CONTENT);
      return false;
    }
    auto it = std::find_if(
        mStreams.begin(), mStreams.end(),
        [&](const auto& s) { return s->mImpl->state->Id() == frame.stream; });
    if (it == mStreams.end()) {
      continue;
    }
    RefPtr stream = *it;
    auto& s = *stream->mImpl;
    if (s.openPending || !s.state->Receive(frame)) {
      Fail(NS_ERROR_CORRUPTED_CONTENT);
      return false;
    }
    if (frame.kind == Kind::Opened) {
      if (s.deadline) {
        s.deadline->Cancel();
        s.deadline = nullptr;
      }
      auto established = std::move(s.established);
      if (established) {
        nsresult rv = s.target->Dispatch(NS_NewRunnableFunction(
            "NaiveFox::NoConnectOpened",
            [established = std::move(established)]() { established(); }));
        if (NS_FAILED(rv)) {
          stream->Finish(rv, true);
        }
      }
    } else if (frame.kind == Kind::Reset) {
      stream->Finish(NS_ERROR_CONNECTION_REFUSED, false);
      continue;
    } else if (frame.kind == Kind::Data) {
      size_t offset = 0;
      while (offset < frame.body.size()) {
        if (s.download.empty() || s.download.back().size() == 16384) {
          s.download.emplace_back();
          s.download.back().reserve(16384);
        }
        auto& tail = s.download.back();
        const size_t length =
            std::min(size_t(16384) - tail.size(), frame.body.size() - offset);
        tail.insert(tail.end(), frame.body.begin() + offset,
                    frame.body.begin() + offset + length);
        offset += length;
      }
    }
    stream->Flush();
    stream->ArmRead();
  }
  RetireStreams();
  return true;
}

void NoConnectCarrier::ConfirmUpload(uint32_t aSequence) {
  for (const auto& stream : mStreams) {
    auto& s = *stream->mImpl;
    s.finConfirmed |= s.finUpload != UINT32_MAX && s.finUpload <= aSequence;
  }
  RetireStreams();
}

void NoConnectCarrier::RetireStreams() {
  auto streams = mStreams;
  for (const auto& stream : streams) {
    const auto& s = *stream->mImpl;
    if (s.finConfirmed && s.outputClosed && s.state->IsDrained()) {
      stream->Finish(NS_OK, false);
    }
  }
}

size_t NoConnectCarrier::Pressure(bool& aControl) const {
  size_t bytes = 0;
  aControl = !mResets.empty();
  for (const auto& stream : mStreams) {
    const auto& s = *stream->mImpl;
    bytes += std::min(s.upload.Size(), size_t(s.state->SendCredit()));
    aControl |= s.openPending || s.state->PendingCredit() ||
                (s.eof && s.upload.Empty() && !s.state->SentFin());
  }
  return bytes;
}

void NoConnectCarrier::Wake() {
  if (mQueued || mClosed) {
    return;
  }
  mQueued = true;
  RefPtr self = this;
  nsresult rv = NS_DispatchToMainThread(
      NS_NewRunnableFunction("NaiveFox::NoConnectSchedule", [self]() {
        self->mQueued = false;
        if (!self->mClosed) {
          self->Tick();
        }
      }));
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
}

void NoConnectCarrier::Continue() {
  mBusy = false;
  Wake();
}

void NoConnectCarrier::Tick() {
  RetireStreams();
  // Keep one warm carrier per route after a burst. Finish acknowledging FIN
  // and RESET uploads before dropping an extra carrier, so the server can
  // release every target connection without waiting for session expiry.
  if (mStreams.empty() && mResets.empty() && mRequests.empty() && !mBusy &&
      !mWebSocketPending) {
    if (!CanAttach()) {
      Fail(NS_OK);
      return;
    }
    for (const auto& carrier : sCarriers) {
      if (carrier.get() != this && carrier->Matches(mConfig)) {
        Fail(NS_OK);
        return;
      }
    }
  }
  if (mWebSocket) {
    if (mWebSocketReady) {
      bool control = false;
      const size_t bytes = Pressure(control);
      if (bytes || control) {
        ScheduleWebSocket(WebSocketDelay(bytes), false);
      }
    }
    return;
  }
  if (mBusy || !mBootstrapped) {
    return;
  }
  mBusy = true;
  if (mStartup < kStartupSlots.size()) {
    Startup();
    return;
  }
  StartWebSocket();
}

void NoConnectCarrier::Startup() {
  Bytes body;
  if (!Upload(4096, body)) {
    return;
  }
  RefPtr self = this;
  Open("/api/sync"_ns, &body, 0, 204, false, [self](CarrierRequest*, nsresult) {
    const size_t capacity = kStartupSlots[self->mStartup];
    nsAutoCString path;
    if (capacity == 8192) {
      path.AssignLiteral("/api/events/brief");
    } else if (capacity == 32768) {
      path.AssignLiteral("/api/events/state");
    } else {
      path.AssignLiteral("/media/chunk/");
      path.AppendInt(static_cast<uint32_t>(self->mStartup));
    }
    self->Open(path, nullptr, capacity, 200, true,
               [self](CarrierRequest* request, nsresult) {
                 if (self->Receive(request)) {
                   ++self->mStartup;
                   self->Continue();
                 }
               });
  });
}

void NoConnectCarrier::StartWebSocket() {
  MOZ_ASSERT(mBootstrapped && mAssets == mResources.Length() &&
             mStartup == kStartupSlots.size() && mRequests.empty());
  mWebSocketAck = mUp - 1;
  RefPtr self = this;
  mWebSocket = new NoConnectWebSocket(
      [self]() {
        if (self->mClosed) {
          return;
        }
        self->mWebSocketReady = true;
        self->mBusy = false;
        auto deadline = NS_NewTimerWithCallback(
            [self](nsITimer*) { self->Fail(NS_ERROR_NET_TIMEOUT); }, 75000,
            nsITimer::TYPE_ONE_SHOT, "NaiveFox::WebSocketReceiveDeadline"_ns);
        if (deadline.isErr()) {
          self->Fail(deadline.unwrapErr());
          return;
        }
        self->mWebSocketDeadline = deadline.unwrap();
        RuntimeLogEvent("No-connect websocket ready startup=%zu\n",
                        self->mStartup);
        self->Wake();
        self->ScheduleWebSocket(25000, true);
      },
      [self](const nsACString& aMessage) {
        if (self->mClosed) {
          return;
        }
        const size_t length = aMessage.Length();
        const bool valid = noconnect::ValidRealtimeDownCapacity(length);
        if (!valid) {
          self->Fail(NS_ERROR_CORRUPTED_CONTENT);
          return;
        }
        const auto* data =
            reinterpret_cast<const uint8_t*>(aMessage.BeginReading());
        if (self->ReceiveCell(Bytes(data, data + length), true)) {
          if (self->mWebSocketDeadline) {
            nsresult rv = self->mWebSocketDeadline->SetDelay(75000);
            if (NS_FAILED(rv)) {
              self->Fail(rv);
              return;
            }
          }
          self->Wake();
        }
      },
      [self](uint32_t aSize) {
        if (self->mClosed) {
          return;
        }
        if (aSize > self->mWebSocketPending) {
          self->Fail(NS_ERROR_UNEXPECTED);
          return;
        }
        self->mWebSocketPending -= aSize;
        if (!self->mWebSocketPending) {
          bool control = false;
          const size_t bytes = self->Pressure(control);
          self->ScheduleWebSocket(
              bytes || control ? self->WebSocketDelay(bytes) : 25000,
              !bytes && !control);
          self->Wake();
        }
      },
      [self](nsresult aStatus) {
        self->Fail(NS_FAILED(aStatus) ? aStatus : NS_ERROR_NET_RESET);
      });
  nsresult rv = mWebSocket->Start(mConfig, mCookie, "/api/realtime"_ns,
                                  "nfc1.stream.v1"_ns);
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
}

bool NoConnectCarrier::HasPendingOpen() const {
  return std::any_of(mStreams.begin(), mStreams.end(), [](const auto& stream) {
    return stream->mImpl->openPending;
  });
}

uint32_t NoConnectCarrier::WebSocketDelay(size_t aBytes) const {
  const size_t capacity =
      noconnect::ReadyRealtimeUpCapacity(aBytes, HasPendingOpen());
  return aBytes >= capacity || (!aBytes && !HasPendingOpen()) ? 0 : 2;
}

void NoConnectCarrier::ScheduleWebSocket(uint32_t aDelay, bool aHeartbeat) {
  if (mClosed || !mWebSocketReady || mWebSocketPending) {
    return;
  }
  if (mWebSocketTimer) {
    if (aHeartbeat ||
        (!mWebSocketHeartbeat && aDelay >= mWebSocketScheduledDelay)) {
      return;
    }
    mWebSocketTimer->Cancel();
    mWebSocketTimer = nullptr;
  }
  mWebSocketHeartbeat = aHeartbeat;
  mWebSocketScheduledDelay = aDelay;
  RefPtr self = this;
  auto timer = NS_NewTimerWithCallback(
      [self, aHeartbeat](nsITimer*) {
        self->mWebSocketTimer = nullptr;
        self->WebSocketTick(aHeartbeat);
      },
      aDelay, nsITimer::TYPE_ONE_SHOT, "NaiveFox::WebSocketApplicationSlot"_ns);
  if (timer.isErr()) {
    Fail(timer.unwrapErr());
    return;
  }
  mWebSocketTimer = timer.unwrap();
}

void NoConnectCarrier::WebSocketTick(bool aHeartbeat) {
  if (mClosed || !mWebSocketReady || mWebSocketPending) {
    return;
  }
  bool control = false;
  const size_t bytes = Pressure(control);
  if (!bytes && !control && !aHeartbeat) {
    ScheduleWebSocket(25000, true);
    return;
  }
  const bool opening = HasPendingOpen();
  const size_t capacity = noconnect::ReadyRealtimeUpCapacity(bytes, opening);
  Bytes body;
  if (!Upload(capacity, body)) {
    return;
  }
  mWebSocketPending = body.size();
  nsresult rv = mWebSocket->Send(nsDependentCSubstring(
      reinterpret_cast<const char*>(body.data()), body.size()));
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
}

void ShutdownNoConnectCarriers() {
  MOZ_ASSERT(NS_IsMainThread());
  auto carriers = std::move(sCarriers);
  sCarriers.clear();
  for (const auto& carrier : carriers) {
    carrier->Fail(NS_BINDING_ABORTED);
  }
  ShutdownNoConnectWebSockets();
}

}  // namespace mozilla::naivefox
