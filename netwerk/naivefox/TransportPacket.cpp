/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "TransportPacket.h"

#include <algorithm>
#include <array>
#include <deque>
#include <utility>

#include "Config.h"
#include "OriginChannel.h"
#include "TransportCodec.h"
#include "TransportCookies.h"
#include "TransportTls.h"
#include "mozilla/TimeStamp.h"
#include "nsIChannel.h"
#include "nsIHttpChannel.h"
#include "nsIInputStream.h"
#include "nsIStreamListener.h"
#include "nsITimer.h"
#include "nsIUploadChannel2.h"
#include "nsNetUtil.h"
#include "nsStringStream.h"
#include "pk11pub.h"
#include "secport.h"

namespace mozilla::naivefox {
namespace {

using Bytes = TransportPacket::Bytes;
using Headers = std::vector<std::pair<nsCString, nsCString>>;
constexpr size_t kPacketLimit = 65536;
constexpr size_t kPacketSlots = 8;

void Put32(Bytes& out, uint32_t value) {
  for (int shift = 24; shift >= 0; shift -= 8) out.push_back(value >> shift);
}
void Put64(Bytes& out, uint64_t value) {
  for (int shift = 56; shift >= 0; shift -= 8) out.push_back(value >> shift);
}
uint32_t Get32(const uint8_t* in) {
  return (uint32_t(in[0]) << 24) | (uint32_t(in[1]) << 16) |
         (uint32_t(in[2]) << 8) | in[3];
}
uint64_t Get64(const uint8_t* in) {
  return (uint64_t(Get32(in)) << 32) | Get32(in + 4);
}
nsCString Hex(const uint8_t* data, size_t length) {
  static constexpr char digits[] = "0123456789abcdef";
  nsCString result;
  for (size_t i = 0; i < length; ++i) {
    result.Append(digits[data[i] >> 4]);
    result.Append(digits[data[i] & 15]);
  }
  return result;
}

class PacketRequest final : public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER
  using Done = std::function<void(nsresult, uint32_t, const Bytes&)>;
  using Chunk = std::function<bool(const uint8_t*, size_t)>;

  PacketRequest(Done&& aDone, Chunk&& aChunk = nullptr)
      : mDone(std::move(aDone)), mChunk(std::move(aChunk)) {}
  nsresult Start(const TransportConfig& config,
                 const std::shared_ptr<TransportCookies>& cookies,
                 const nsACString& path, const Bytes* body,
                 const Headers& headers) {
    nsresult rv = StartInternal(config, cookies, path, body, headers);
    if (NS_FAILED(rv)) {
      Cancel(rv);
      mChannel = nullptr;
      mDone = nullptr;
      mChunk = nullptr;
    }
    return rv;
  }
  nsresult StartInternal(const TransportConfig& config,
                         const std::shared_ptr<TransportCookies>& cookies,
                         const nsACString& path, const Bytes* body,
                         const Headers& headers) {
    MOZ_TRY(CreateOriginChannel(config.mProxyUrl, path, ProxyProtocol::H2,
                                config.mHostResolverRule,
                                getter_AddRefs(mChannel)));
    nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(mChannel);
    if (!http) return NS_ERROR_FAILURE;
    MOZ_TRY(http->SetRequestHeader("Accept-Encoding"_ns, "identity"_ns, false));
    mCookies = cookies;
    nsCOMPtr<nsIURI> uri;
    MOZ_TRY(mChannel->GetURI(getter_AddRefs(uri)));
    nsAutoCString cookie;
    MOZ_TRY(cookies->RequestHeader(uri, cookie));
    if (!cookie.IsEmpty()) {
      MOZ_TRY(http->SetRequestHeader("Cookie"_ns, cookie, false));
    }
    for (const auto& header : headers) {
      MOZ_TRY(http->SetRequestHeader(header.first, header.second, false));
    }
    if (body) {
      nsCOMPtr<nsIInputStream> input;
      nsCString bytes(reinterpret_cast<const char*>(body->data()),
                      body->size());
      MOZ_TRY(
          NS_NewCStringInputStream(getter_AddRefs(input), std::move(bytes)));
      nsCOMPtr<nsIUploadChannel2> upload = do_QueryInterface(mChannel);
      if (!upload) return NS_ERROR_FAILURE;
      MOZ_TRY(upload->ExplicitSetUploadStream(
          input, "application/octet-stream"_ns, body->size(), "POST"_ns));
    } else {
      MOZ_TRY(http->SetRequestMethod("GET"_ns));
    }
    RefPtr self = this;
    auto timer = NS_NewTimerWithCallback(
        [self](nsITimer*) { self->Cancel(NS_ERROR_NET_TIMEOUT); }, 15000,
        nsITimer::TYPE_ONE_SHOT, "NaiveFox::PacketRequestDeadline"_ns);
    if (timer.isErr()) return timer.unwrapErr();
    mTimer = timer.unwrap();
    nsresult rv = mChannel->AsyncOpen(this);
    if (NS_FAILED(rv)) {
      Cancel(rv);
      mChannel = nullptr;
      mDone = nullptr;
      mChunk = nullptr;
    }
    return rv;
  }
  void Refresh() {
    if (mTimer) mTimer->SetDelay(15000);
  }
  void Cancel(nsresult status = NS_BINDING_ABORTED) {
    if (mTimer) {
      mTimer->Cancel();
      mTimer = nullptr;
    }
    if (mChannel) mChannel->Cancel(status);
  }

 private:
  ~PacketRequest() = default;
  Done mDone;
  Chunk mChunk;
  nsCOMPtr<nsIChannel> mChannel;
  nsCOMPtr<nsITimer> mTimer;
  std::shared_ptr<TransportCookies> mCookies;
  Bytes mBody;
  uint32_t mStatus = 0;
};

NS_IMPL_ISUPPORTS(PacketRequest, nsIStreamListener, nsIRequestObserver)
NS_IMETHODIMP PacketRequest::OnStartRequest(nsIRequest*) {
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(mChannel);
  nsAutoCString protocol, type, encoding;
  MOZ_TRY(http->GetResponseStatus(&mStatus));
  MOZ_TRY(http->GetProtocolVersion(protocol));
  if (mStatus != 200 || !protocol.EqualsLiteral("h2")) return NS_ERROR_FAILURE;
  MOZ_TRY(mChannel->GetContentType(type));
  if (!type.EqualsLiteral("application/octet-stream")) {
    return NS_ERROR_CORRUPTED_CONTENT;
  }
  if (NS_SUCCEEDED(http->GetResponseHeader("Content-Encoding"_ns, encoding)) &&
      !encoding.IsEmpty() && !encoding.EqualsLiteral("identity")) {
    return NS_ERROR_CORRUPTED_CONTENT;
  }
  int64_t length = -1;
  MOZ_TRY(mChannel->GetContentLength(&length));
  if (!mChunk && length > int64_t(kPacketLimit + 44)) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  return mCookies->ResponseHeaders(mChannel);
}
NS_IMETHODIMP PacketRequest::OnDataAvailable(nsIRequest*, nsIInputStream* input,
                                             uint64_t, uint32_t count) {
  std::array<uint8_t, 16384> buffer;
  while (count) {
    uint32_t read = 0;
    MOZ_TRY(input->Read(reinterpret_cast<char*>(buffer.data()),
                        std::min(count, uint32_t(buffer.size())), &read));
    if (!read) return NS_ERROR_UNEXPECTED;
    if (mChunk) {
      if (!mChunk(buffer.data(), read)) return NS_ERROR_CORRUPTED_CONTENT;
    } else {
      if (read > kPacketLimit + 44 - mBody.size()) {
        return NS_ERROR_FILE_TOO_BIG;
      }
      mBody.insert(mBody.end(), buffer.begin(), buffer.begin() + read);
    }
    count -= read;
  }
  return NS_OK;
}
NS_IMETHODIMP PacketRequest::OnStopRequest(nsIRequest*, nsresult status) {
  if (mTimer) mTimer->Cancel();
  mTimer = nullptr;
  mChannel = nullptr;
  if (mChunk && NS_SUCCEEDED(status)) status = NS_ERROR_NET_RESET;
  mChunk = nullptr;
  if (mDone) {
    auto done = std::move(mDone);
    done(status, mStatus, mBody);
  }
  return NS_OK;
}

bool Retryable(nsresult status, uint32_t http) {
  if (status == NS_ERROR_CORRUPTED_CONTENT || status == NS_ERROR_FILE_TOO_BIG ||
      status == NS_BINDING_ABORTED) {
    return false;
  }
  return http == 0 || http == 200 || http == 429 || http == 502 ||
         http == 503 || http == 504;
}
TimeStamp RetryAt(uint32_t attempts) {
  return TimeStamp::Now() + TimeDuration::FromMilliseconds(std::min(
                                200U << std::min(attempts, 4U), 2000U));
}

}  // namespace

class TransportPacket::Impl final {
 public:
  explicit Impl(TransportPacket* owner) : owner(owner) {}
  ~Impl() {
    if (key) PK11_FreeSymKey(key);
  }

  struct Upload {
    uint64_t sequence, cursor;
    Bytes body;
    Headers headers;
    RefPtr<PacketRequest> request;
    TimeStamp started, retry;
    uint32_t attempts = 0;
  };

  void Fail(nsresult status) {
    if (closed) return;
    auto callback = failed;
    owner->Close();
    if (callback) callback(status);
  }

  bool MAC(const char* operation, uint64_t sequence, uint64_t cursor,
           const Bytes& body, std::array<uint8_t, 32>& tag) {
    if (!key) return false;
    Bytes message;
    for (const nsCString& field :
         {nsCString("naivefox-packet"), nsCString(operation), id}) {
      Put32(message, field.Length());
      message.insert(message.end(), field.BeginReading(), field.EndReading());
    }
    Put64(message, sequence);
    Put64(message, cursor);
    std::array<uint8_t, 32> hash{};
    static const uint8_t empty = 0;
    if (PK11_HashBuf(SEC_OID_SHA256, hash.data(),
                     body.empty() ? &empty : body.data(),
                     body.size()) != SECSuccess)
      return false;
    message.insert(message.end(), hash.begin(), hash.end());
    SECItem input{siBuffer, message.data(), unsigned(message.size())};
    SECItem output{siBuffer, tag.data(), unsigned(tag.size())};
    return PK11_SignWithSymKey(key, CKM_SHA256_HMAC, nullptr, &output,
                               &input) == SECSuccess &&
           output.len == tag.size();
  }

  bool Verify(const char* operation, uint64_t sequence, uint64_t cursor,
              const Bytes& body, const uint8_t* received) {
    std::array<uint8_t, 32> tag{};
    return MAC(operation, sequence, cursor, body, tag) &&
           NSS_SecureMemcmp(tag.data(), received, tag.size()) == 0;
  }

  bool SignedHeaders(const char* operation, uint64_t sequence, uint64_t cursor,
                     const Bytes& body, Headers& headers) {
    std::array<uint8_t, 32> tag{};
    if (!MAC(operation, sequence, cursor, body, tag)) return false;
    nsCString value;
    value.AppendInt(cursor);
    headers = {{"NaiveFox-Cursor"_ns, value},
               {"NaiveFox-MAC"_ns, Hex(tag.data(), tag.size())}};
    return true;
  }

  bool PumpPlain(bool realtime) {
    std::array<uint8_t, 16384> buffer;
    int32_t read;
    while ((read = tls.Read(buffer.data(), buffer.size())) > 0) {
      if (!cells.Feed(buffer.data(), read, [this, realtime](const Bytes& body) {
            auto callback = cell;
            if (!callback || !callback(body, realtime)) return false;
            if (!realtime) hello = true;
            return !closed;
          }))
        return false;
    }
    return !tls.Failed() && !closed;
  }

  void StartSetup() {
    if (closed || setupRequest) return;
    if ((TimeStamp::Now() - setupStarted).ToSeconds() >= 30) {
      Fail(NS_ERROR_NET_TIMEOUT);
      return;
    }
    RefPtr self = owner;
    setupRequest = new PacketRequest(
        [self](nsresult status, uint32_t http, const Bytes& body) {
          auto& p = *self->mImpl;
          p.setupRequest = nullptr;
          if (p.closed) return;
          if (NS_FAILED(status)) {
            if (!Retryable(status, http)) {
              p.Fail(status);
              return;
            }
            p.setupRetry = RetryAt(p.setupAttempts++);
            p.Retry();
            return;
          }
          if (!p.SetupReply(body)) p.Fail(NS_ERROR_CORRUPTED_CONTENT);
        });
    nsresult rv = setupRequest->Start(config, cookies, setupPath, &setupBody,
                                      setupHeaders);
    if (NS_FAILED(rv)) Fail(rv);
  }

  bool SetupReply(const Bytes& response) {
    if (response.size() < 44 ||
        Get32(response.data() + 40) != response.size() - 44) {
      return false;
    }
    Bytes flight(response.begin() + 44, response.end());
    if (id.IsEmpty()) {
      id = Hex(response.data(), 32);
      down = Get64(response.data() + 32);
      if (!down || !tls.Feed(flight.data(), flight.size()) ||
          tls.Handshake() != TransportTls::Progress::Ready)
        return false;
      std::array<uint8_t, 32> secret{};
      if (!tls.Export(secret)) return false;
      PK11SlotInfo* slot = PK11_GetInternalSlot();
      if (!slot) return false;
      SECItem item{siBuffer, secret.data(), unsigned(secret.size())};
      key = PK11_ImportSymKey(slot, CKM_SHA256_HMAC, PK11_OriginUnwrap,
                              CKA_SIGN, &item, nullptr);
      PK11_FreeSlot(slot);
      std::fill(secret.begin(), secret.end(), 0);
      if (!key) return false;
      Bytes plain;
      Put32(plain, auth.size());
      plain.insert(plain.end(), auth.begin(), auth.end());
      if (tls.Write(plain.data(), plain.size()) != int32_t(plain.size())) {
        return false;
      }
      auth.clear();
      setupBody = tls.TakeOutput(kPacketLimit);
      if (tls.PendingOutput() || setupBody.empty()) return false;
      setupPath = "/api/packet/"_ns + id + "/auth"_ns;
      if (!SignedHeaders("auth", 1, down, setupBody, setupHeaders))
        return false;
      setupAttempts = 0;
      setupStarted = TimeStamp::Now();
      StartSetup();
      return true;
    }
    const uint64_t cursor = Get64(response.data());
    if (cursor <= down ||
        !Verify("auth-reply", 2, cursor, flight, response.data() + 8) ||
        !tls.Feed(flight.data(), flight.size()) || !PumpPlain(false) ||
        !hello || !cells.Empty())
      return false;
    down = cursor;
    authenticated = true;
    setupBody.clear();
    setupHeaders.clear();
    lastReceive = TimeStamp::Now();
    StartDownload();
    if (!closed && ready) ready();
    PumpUploads();
    return !closed;
  }

  void Retry() {
    if (closed || retryTimer) return;
    RefPtr self = owner;
    auto timer = NS_NewTimerWithCallback(
        [self](nsITimer*) {
          auto& p = *self->mImpl;
          p.retryTimer = nullptr;
          if (p.closed) return;
          if (!p.authenticated) {
            if (TimeStamp::Now() >= p.setupRetry)
              p.StartSetup();
            else
              p.Retry();
            return;
          }
          if (!p.download) {
            if (TimeStamp::Now() >= p.downloadRetry)
              p.StartDownload();
            else
              p.Retry();
          }
          p.PumpUploads();
        },
        200, nsITimer::TYPE_ONE_SHOT, "NaiveFox::PacketRetry"_ns);
    if (timer.isErr()) {
      Fail(timer.unwrapErr());
      return;
    }
    retryTimer = timer.unwrap();
  }

  void StartDownload() {
    if (closed || download) return;
    if ((TimeStamp::Now() - lastReceive).ToSeconds() >= 30 ||
        generation == UINT64_MAX) {
      Fail(NS_ERROR_NET_TIMEOUT);
      return;
    }
    ++generation;
    wire.clear();
    nsCString path("/api/packet/");
    path.Append(id);
    path.AppendLiteral("/download?generation=");
    path.AppendInt(generation);
    path.AppendLiteral("&cursor=");
    path.AppendInt(down);
    Headers headers;
    if (!SignedHeaders("download", generation, down, {}, headers)) {
      Fail(NS_ERROR_FAILURE);
      return;
    }
    RefPtr self = owner;
    download = new PacketRequest(
        [self](nsresult status, uint32_t http, const Bytes&) {
          auto& p = *self->mImpl;
          p.download = nullptr;
          if (p.closed) return;
          if (!Retryable(status, http)) {
            p.Fail(status);
            return;
          }
          p.downloadRetry = RetryAt(p.downloadAttempts++);
          p.Retry();
        },
        [self](const uint8_t* bytes, size_t length) {
          return self->mImpl->ReceiveWire(bytes, length);
        });
    nsresult rv = download->Start(config, cookies, path, nullptr, headers);
    if (NS_FAILED(rv)) Fail(rv);
  }

  bool ReceiveWire(const uint8_t* data, size_t length) {
    while (length) {
      size_t target = 44;
      if (wire.size() >= 44) {
        const uint32_t size = Get32(wire.data() + 8);
        if (!size || size > kPacketLimit) return false;
        target += size;
      }
      const size_t amount = std::min(length, target - wire.size());
      wire.insert(wire.end(), data, data + amount);
      data += amount;
      length -= amount;
      if (wire.size() < 44) continue;
      const uint32_t size = Get32(wire.data() + 8);
      if (!size || size > kPacketLimit) return false;
      if (wire.size() < 44 + size) continue;
      Bytes body(wire.begin() + 44, wire.end());
      if (Get64(wire.data()) != down || down == UINT64_MAX ||
          !Verify("down", down, 0, body, wire.data() + 12) ||
          !tls.Feed(body.data(), body.size()) || !PumpPlain(true))
        return false;
      ++down;
      wire.clear();
      lastReceive = TimeStamp::Now();
      downloadAttempts = 0;
      if (download) download->Refresh();
      PumpUploads();
    }
    return !closed;
  }

  void StartUpload(const std::shared_ptr<Upload>& upload) {
    RefPtr self = owner;
    upload->request = new PacketRequest(
        [self, upload](nsresult status, uint32_t http, const Bytes& response) {
          auto& p = *self->mImpl;
          upload->request = nullptr;
          if (p.closed || upload->sequence < p.acknowledged) return;
          if (NS_FAILED(status)) {
            if (!Retryable(status, http)) {
              p.Fail(status);
              return;
            }
            upload->retry = RetryAt(upload->attempts++);
            p.Retry();
            return;
          }
          if (response.size() != 48 ||
              Get64(response.data() + 8) != upload->sequence) {
            p.Fail(NS_ERROR_CORRUPTED_CONTENT);
            return;
          }
          const uint64_t next = Get64(response.data());
          if (next < 2 || next > p.nextUpload ||
              !p.Verify("upload-reply", next, upload->sequence, upload->body,
                        response.data() + 16)) {
            p.Fail(NS_ERROR_CORRUPTED_CONTENT);
            return;
          }
          p.acknowledged = std::max(p.acknowledged, next);
          while (!p.uploads.empty() &&
                 p.uploads.front()->sequence < p.acknowledged) {
            auto retired = p.uploads.front();
            p.uploads.pop_front();
            if (retired->request) retired->request->Cancel();
          }
          if (upload->sequence >= p.acknowledged) {
            upload->retry = RetryAt(upload->attempts++);
            p.Retry();
          }
          p.PumpUploads();
          if (!p.closed && p.writable) p.writable();
        });
    nsCString path("/api/packet/");
    path.Append(id);
    path.AppendLiteral("/upload/");
    path.AppendInt(upload->sequence);
    nsresult rv = upload->request->Start(config, cookies, path, &upload->body,
                                         upload->headers);
    if (NS_FAILED(rv)) Fail(rv);
  }

  void PumpUploads() {
    if (closed || !authenticated) return;
    while (nextUpload - acknowledged < kPacketSlots && tls.PendingOutput()) {
      if (nextUpload == UINT64_MAX) {
        Fail(NS_ERROR_FILE_TOO_BIG);
        return;
      }
      auto upload = std::make_shared<Upload>();
      upload->sequence = nextUpload++;
      upload->cursor = down;
      upload->body = tls.TakeOutput(kPacketLimit);
      upload->started = TimeStamp::Now();
      upload->retry = upload->started;
      if (!SignedHeaders("upload", upload->sequence, upload->cursor,
                         upload->body, upload->headers)) {
        Fail(NS_ERROR_FAILURE);
        return;
      }
      uploads.push_back(upload);
    }
    for (const auto& upload : uploads) {
      if ((TimeStamp::Now() - upload->started).ToSeconds() >= 30) {
        Fail(NS_ERROR_NET_TIMEOUT);
        return;
      }
      if (upload->request) continue;
      if (TimeStamp::Now() >= upload->retry)
        StartUpload(upload);
      else
        Retry();
      if (closed) return;
    }
  }

  TransportPacket* owner;
  TransportConfig config;
  std::shared_ptr<TransportCookies> cookies;
  TransportTls tls;
  wire::CellStreamDecoder cells;
  PK11SymKey* key = nullptr;
  nsCString id, setupPath;
  Bytes auth, setupBody, wire;
  Headers setupHeaders;
  RefPtr<PacketRequest> setupRequest, download;
  nsCOMPtr<nsITimer> retryTimer;
  std::deque<std::shared_ptr<Upload>> uploads;
  uint64_t down = 0, generation = 0, nextUpload = 2, acknowledged = 2;
  uint32_t setupAttempts = 0, downloadAttempts = 0;
  TimeStamp setupStarted, setupRetry, lastReceive, downloadRetry;
  bool closed = false, authenticated = false, hello = false;
  std::function<void()> ready, writable;
  std::function<bool(const Bytes&, bool)> cell;
  std::function<void(nsresult)> failed;
};

TransportPacket::TransportPacket(
    std::function<void()>&& aReady,
    std::function<bool(const Bytes&, bool)>&& aCell,
    std::function<void()>&& aWritable, std::function<void(nsresult)>&& aFailed)
    : mImpl(MakeUnique<Impl>(this)) {
  mImpl->ready = std::move(aReady);
  mImpl->cell = std::move(aCell);
  mImpl->writable = std::move(aWritable);
  mImpl->failed = std::move(aFailed);
}
TransportPacket::~TransportPacket() = default;

nsresult TransportPacket::Start(const TransportConfig& config,
                                std::shared_ptr<TransportCookies> cookies,
                                const Bytes& auth) {
  RefPtr self = this;
  auto& p = *mImpl;
  if (config.mProtocol != ProxyProtocol::H2 ||
      config.mServerPin.Length() != 64 || p.setupRequest || p.closed)
    return NS_ERROR_INVALID_ARG;
  p.config = config;
  p.cookies = std::move(cookies);
  p.auth = auth;
  std::array<uint8_t, 32> pin{};
  auto digit = [](char ch) -> int {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    return -1;
  };
  for (size_t i = 0; i < pin.size(); ++i) {
    int hi = digit(config.mServerPin.CharAt(i * 2));
    int lo = digit(config.mServerPin.CharAt(i * 2 + 1));
    if (hi < 0 || lo < 0) return NS_ERROR_INVALID_ARG;
    pin[i] = uint8_t(hi * 16 + lo);
  }
  if (!p.tls.Start(pin) ||
      p.tls.Handshake() != TransportTls::Progress::Blocked) {
    return NS_ERROR_FAILURE;
  }
  p.setupBody.resize(32);
  if (PK11_GenerateRandom(p.setupBody.data(), p.setupBody.size()) !=
      SECSuccess) {
    return NS_ERROR_FAILURE;
  }
  Bytes hello = p.tls.TakeOutput(kPacketLimit - 32);
  if (hello.empty() || p.tls.PendingOutput()) return NS_ERROR_FAILURE;
  p.setupBody.insert(p.setupBody.end(), hello.begin(), hello.end());
  p.setupPath = "/api/packet"_ns;
  p.setupStarted = TimeStamp::Now();
  p.StartSetup();
  return p.closed ? NS_ERROR_FAILURE : NS_OK;
}

bool TransportPacket::Blocked() const {
  const auto& p = *mImpl;
  return p.closed || !p.authenticated ||
         p.nextUpload - p.acknowledged >= kPacketSlots ||
         p.tls.PendingOutput() > 128 * 1024;
}
bool TransportPacket::Send(const Bytes& body) {
  RefPtr self = this;
  if (Blocked()) return false;
  auto& p = *mImpl;
  Bytes plain;
  Put32(plain, body.size());
  plain.insert(plain.end(), body.begin(), body.end());
  if (p.tls.Write(plain.data(), plain.size()) != int32_t(plain.size())) {
    p.Fail(NS_ERROR_FAILURE);
    return false;
  }
  p.PumpUploads();
  return !p.closed;
}
const nsCString& TransportPacket::SessionID() const { return mImpl->id; }
void TransportPacket::Close() {
  auto& p = *mImpl;
  if (p.closed) return;
  p.closed = true;
  if (p.setupRequest) p.setupRequest->Cancel();
  if (p.download) p.download->Cancel();
  for (const auto& upload : p.uploads) {
    if (upload->request) upload->request->Cancel();
  }
  p.uploads.clear();
  if (p.retryTimer) p.retryTimer->Cancel();
  p.retryTimer = nullptr;
  p.ready = nullptr;
  p.cell = nullptr;
  p.writable = nullptr;
  p.failed = nullptr;
}
}  // namespace mozilla::naivefox
