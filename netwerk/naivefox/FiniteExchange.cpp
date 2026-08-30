/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "FiniteExchange.h"

#include <array>

#include "HeaderPadding.h"
#include "NeckoTunnel.h"
#include "RuntimeLogging.h"
#include "TunnelSession.h"
#include "mozilla/ScopeExit.h"
#include "mozilla/Try.h"
#include "nsIHttpChannel.h"
#include "nsIPipe.h"
#include "nsIRequest.h"
#include "nsIStreamListener.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

namespace {
constexpr uint32_t kExchangeBytes = 64 * 1024;
constexpr uint32_t kUploads = 2;
constexpr uint32_t kDownloads = 4;

bool ValidSession(const nsACString& aSession) {
  if (aSession.Length() != 32) {
    return false;
  }
  for (uint32_t index = 0; index < aSession.Length(); ++index) {
    const char ch = aSession.CharAt(index);
    if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) {
      return false;
    }
  }
  return true;
}
}  // namespace

class FiniteExchange::Listener final : public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  Listener(FiniteExchange* aOwner, const char* aOperation, uint64_t aSequence)
      : mOwner(aOwner), mOperation(aOperation), mSequence(aSequence) {}

  RefPtr<FiniteExchange> mOwner;
  nsCOMPtr<nsIRequest> mRequest;
  nsCString mOperation;
  nsCString mBody;
  nsCString mSession;
  const uint64_t mSequence;
  uint32_t mOffset = 0;
  uint32_t mHttpStatus = 0;
  bool mValidated = false;
  bool mDone = false;

 private:
  ~Listener() = default;
};

class FiniteExchange::Impl final {
 public:
  Impl(const TunnelConfig& aConfig, uint64_t aId, ReadyCallback&& aCallback)
      : mConfig(aConfig), mConnectionId(aId), mCallback(std::move(aCallback)) {}

  TunnelConfig mConfig;
  const uint64_t mConnectionId;
  ReadyCallback mCallback;
  nsCString mTarget;
  nsCString mSession;
  nsCOMPtr<nsIAsyncInputStream> mUploadInput;
  nsCOMPtr<nsIAsyncOutputStream> mUploadOutput;
  nsCOMPtr<nsIAsyncInputStream> mDownloadInput;
  nsCOMPtr<nsIAsyncOutputStream> mDownloadOutput;
  RefPtr<Listener> mOpening;
  std::array<RefPtr<Listener>, kUploads> mUploads;
  std::array<RefPtr<Listener>, kDownloads> mDownloads;
  uint64_t mNextUpload = 0;
  uint64_t mNextDownload = 0;
  uint64_t mNextDelivery = 0;
  uint64_t mUploadBytes = 0;
  uint64_t mDownloadBytes = 0;
  uint64_t mUploadCompleted = 0;
  uint64_t mDownloadCompleted = 0;
  bool mUploadFin = false;
  bool mDownloadFin = false;
  bool mReading = false;
  bool mFlushing = false;
  bool mStreamedBeforeStop = false;
  bool mClosed = false;
};

NS_IMPL_ISUPPORTS(FiniteExchange, nsIInputStreamCallback,
                  nsIOutputStreamCallback)
NS_IMPL_ISUPPORTS(FiniteExchange::Listener, nsIStreamListener,
                  nsIRequestObserver)

FiniteExchange::FiniteExchange(const TunnelConfig& aConfig,
                               uint64_t aConnectionId,
                               ReadyCallback&& aCallback)
    : mImpl(MakeUnique<Impl>(aConfig, aConnectionId, std::move(aCallback))) {}

FiniteExchange::~FiniteExchange() = default;

nsresult FiniteExchange::Start(const nsACString& aTargetAuthority) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mConfig.mProtocol != ProxyProtocol::H2 || mImpl->mOpening ||
      mImpl->mClosed || !mImpl->mCallback) {
    return NS_ERROR_INVALID_ARG;
  }
  mImpl->mTarget = aTargetAuthority;
  NS_NewPipe2(getter_AddRefs(mImpl->mUploadInput),
              getter_AddRefs(mImpl->mUploadOutput), true, true, 4096, 16);
  NS_NewPipe2(getter_AddRefs(mImpl->mDownloadInput),
              getter_AddRefs(mImpl->mDownloadOutput), true, true, 4096, 16);
  return Open("open", 0, EmptyCString(), false, mImpl->mOpening);
}

nsresult FiniteExchange::Open(const char* aOperation, uint64_t aSequence,
                              const nsACString& aBody, bool aFin,
                              RefPtr<Listener>& aListener) {
  nsTArray<ExtraHeader> headers;
  auto add = [&headers](const char* name, const nsACString& value) {
    headers.AppendElement(ExtraHeader{nsCString(name), nsCString(value)});
  };
  add("X-Naivefox-Finite",
      mImpl->mConfig.mDiagnosticH2FiniteStreamUploads ? "2"_ns : "1"_ns);
  add("X-Naivefox-Operation", nsDependentCString(aOperation));
  nsAutoCString sequence;
  sequence.AppendInt(aSequence);
  add("X-Naivefox-Sequence", sequence);
  const nsDependentCString operation(aOperation);
  if (operation.EqualsLiteral("open")) {
    add("X-Naivefox-Target", mImpl->mTarget);
    nsAutoCString padding;
    MOZ_TRY(GenerateHeaderPadding(padding));
    add("Padding", padding);
  } else {
    add("X-Naivefox-Session", mImpl->mSession);
  }
  if (aFin) {
    add("X-Naivefox-Fin", "1"_ns);
  }
  RefPtr listener = new Listener(this, aOperation, aSequence);
  MOZ_TRY(OpenFiniteHttpExchange(
      mImpl->mConfig.mProxyUrl, mImpl->mConfig.mProxyUser,
      mImpl->mConfig.mProxyPassword, mImpl->mConfig.mHostResolverRule,
      operation.EqualsLiteral("down") ? "GET"_ns : "POST"_ns, headers, aBody,
      listener, getter_AddRefs(listener->mRequest)));
  aListener = std::move(listener);
  return NS_OK;
}

NS_IMETHODIMP FiniteExchange::Listener::OnStartRequest(nsIRequest* aRequest) {
  MOZ_ASSERT(NS_IsMainThread());
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(aRequest);
  if (!http) {
    return NS_ERROR_NO_INTERFACE;
  }
  nsAutoCString protocol, marker, sequence;
  MOZ_TRY(http->GetProtocolVersion(protocol));
  MOZ_TRY(http->GetResponseStatus(&mHttpStatus));
  MOZ_TRY(http->GetResponseHeader("X-Naivefox-Finite"_ns, marker));
  MOZ_TRY(http->GetResponseHeader("X-Naivefox-Sequence"_ns, sequence));
  nsAutoCString expectedSequence;
  expectedSequence.AppendInt(mSequence);
  if (!protocol.EqualsLiteral("h2") ||
      marker != (mOwner->mImpl->mConfig.mDiagnosticH2FiniteStreamUploads
                     ? "2"_ns
                     : "1"_ns) ||
      sequence != expectedSequence ||
      (mHttpStatus != 200 &&
       !(mOperation.EqualsLiteral("down") && mHttpStatus == 204))) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(http->GetResponseHeader("X-Naivefox-Session"_ns, mSession));
  if (!ValidSession(mSession)) {
    return NS_ERROR_FAILURE;
  }
  if (mOperation.EqualsLiteral("open")) {
    nsAutoCString padding;
    MOZ_TRY(http->GetResponseHeader("Padding"_ns, padding));
    if (padding.IsEmpty()) {
      return NS_ERROR_FAILURE;
    }
  } else if (mSession != mOwner->mImpl->mSession) {
    return NS_ERROR_FAILURE;
  }
  mValidated = true;
  return NS_OK;
}

NS_IMETHODIMP FiniteExchange::Listener::OnDataAvailable(nsIRequest*,
                                                        nsIInputStream* aInput,
                                                        uint64_t,
                                                        uint32_t aCount) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mValidated || !mOperation.EqualsLiteral("down") || mHttpStatus != 200 ||
      aCount > kExchangeBytes - mBody.Length()) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  const uint32_t before = mBody.Length();
  if (!mBody.SetLength(before + aCount, fallible)) {
    return NS_ERROR_OUT_OF_MEMORY;
  }
  uint32_t read = 0;
  MOZ_TRY(aInput->Read(mBody.BeginWriting() + before, aCount, &read));
  if (read != aCount) {
    return NS_ERROR_UNEXPECTED;
  }
  RefPtr owner = mOwner;
  return owner && owner->mImpl->mConfig.mDiagnosticH2FiniteReadThrough
             ? owner->FlushDownloads()
             : NS_OK;
}

NS_IMETHODIMP FiniteExchange::Listener::OnStopRequest(nsIRequest*,
                                                      nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  mRequest = nullptr;
  mDone = true;
  RefPtr owner = std::move(mOwner);
  if (owner) {
    if (NS_FAILED(aStatus) && !owner->mImpl->mClosed) {
      RuntimeLogEvent(
          "Connection %llu finite-exchanges request-stop "
          "operation=%s http=%u validated=%d status=0x%08x\n",
          static_cast<unsigned long long>(owner->mImpl->mConnectionId),
          mOperation.get(), mHttpStatus, mValidated,
          static_cast<unsigned>(aStatus));
    }
    owner->Finished(this, NS_SUCCEEDED(aStatus) && !mValidated
                              ? NS_ERROR_FAILURE
                              : aStatus);
  }
  return NS_OK;
}

void FiniteExchange::Finished(Listener* aListener, nsresult aStatus) {
  if (mImpl->mClosed) {
    return;
  }
  if (NS_FAILED(aStatus)) {
    Fail(aStatus);
    return;
  }
  if (aListener->mOperation.EqualsLiteral("open")) {
    mImpl->mSession = aListener->mSession;
    mImpl->mOpening = nullptr;
    nsresult rv = FillDownloads();
    if (NS_SUCCEEDED(rv)) {
      rv = ReadUploads();
    }
    if (NS_FAILED(rv)) {
      Fail(rv);
      return;
    }
    RuntimeLogEvent(
        "Connection %llu finite-exchanges ready=1 block-bytes=%u "
        "upload-window=%u download-window=%u\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), kExchangeBytes,
        kUploads, kDownloads);
    if (mImpl->mConfig.mDiagnosticH2FiniteStreamUploads) {
      RuntimeLogEvent(
          "Connection %llu finite-exchanges upload-read-through=1\n",
          static_cast<unsigned long long>(mImpl->mConnectionId));
    }
    auto callback = std::move(mImpl->mCallback);
    callback(NS_OK, mImpl->mDownloadInput, mImpl->mUploadOutput);
    // The pipe endpoints are now owned by TunnelSession/DuplexPump. Keeping
    // only the opposite ends lets ordinary half-close reach this adapter.
    mImpl->mDownloadInput = nullptr;
    mImpl->mUploadOutput = nullptr;
    return;
  }
  if (aListener->mOperation.EqualsLiteral("up")) {
    ++mImpl->mUploadCompleted;
    mImpl->mUploads[aListener->mSequence % kUploads] = nullptr;
    nsresult rv = ReadUploads();
    if (NS_FAILED(rv)) {
      Fail(rv);
    }
    return;
  }
  if (aListener->mOperation.EqualsLiteral("down")) {
    ++mImpl->mDownloadCompleted;
    if (mImpl->mDownloadCompleted == 2) {
      RuntimeLogEvent("Connection %llu finite-exchanges rotated=1\n",
                      static_cast<unsigned long long>(mImpl->mConnectionId));
    }
    nsresult rv = FlushDownloads();
    if (NS_FAILED(rv)) {
      Fail(rv);
    }
  }
}

nsresult FiniteExchange::FillDownloads() {
  if (mImpl->mDownloadFin || mImpl->mClosed) {
    return NS_OK;
  }
  while (mImpl->mNextDownload - mImpl->mNextDelivery < kDownloads) {
    const uint64_t seq = mImpl->mNextDownload;
    MOZ_TRY(Open("down", seq, EmptyCString(), false,
                 mImpl->mDownloads[seq % kDownloads]));
    ++mImpl->mNextDownload;
  }
  return NS_OK;
}

nsresult FiniteExchange::FlushDownloads() {
  if (mImpl->mFlushing || mImpl->mDownloadFin || mImpl->mClosed) {
    return NS_OK;
  }
  mImpl->mFlushing = true;
  auto reset = MakeScopeExit([&] { mImpl->mFlushing = false; });
  while (!mImpl->mClosed) {
    RefPtr item = mImpl->mDownloads[mImpl->mNextDelivery % kDownloads];
    if (!item ||
        (!item->mDone && !mImpl->mConfig.mDiagnosticH2FiniteReadThrough)) {
      break;
    }
    if (item->mHttpStatus == 204) {
      if (!item->mDone) {
        break;
      }
      mImpl->mDownloadFin = true;
      return mImpl->mDownloadOutput->CloseWithStatus(NS_OK);
    }
    if (item->mBody.IsEmpty()) {
      if (item->mDone) {
        return NS_ERROR_UNEXPECTED;
      }
      break;
    }
    while (item->mOffset < item->mBody.Length()) {
      uint32_t written = 0;
      nsresult rv = mImpl->mDownloadOutput->Write(
          item->mBody.BeginReading() + item->mOffset,
          item->mBody.Length() - item->mOffset, &written);
      if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
        return mImpl->mDownloadOutput->AsyncWait(
            this, 0, 0, GetMainThreadSerialEventTarget());
      }
      MOZ_TRY(rv);
      if (!written) {
        return NS_ERROR_UNEXPECTED;
      }
      item->mOffset += written;
      mImpl->mDownloadBytes += written;
      if (!item->mDone && !mImpl->mStreamedBeforeStop) {
        mImpl->mStreamedBeforeStop = true;
        RuntimeLogEvent(
            "Connection %llu finite-exchanges streamed-before-stop=1\n",
            static_cast<unsigned long long>(mImpl->mConnectionId));
      }
    }
    if (!item->mDone) {
      break;
    }
    mImpl->mDownloads[mImpl->mNextDelivery % kDownloads] = nullptr;
    ++mImpl->mNextDelivery;
    MOZ_TRY(FillDownloads());
  }
  return NS_OK;
}

nsresult FiniteExchange::ReadUploads() {
  if (mImpl->mReading || mImpl->mUploadFin || mImpl->mClosed) {
    return NS_OK;
  }
  mImpl->mReading = true;
  auto reset = MakeScopeExit([&] { mImpl->mReading = false; });
  while (!mImpl->mClosed && !mImpl->mUploads[mImpl->mNextUpload % kUploads]) {
    nsCString body;
    if (!body.SetLength(kExchangeBytes, fallible)) {
      return NS_ERROR_OUT_OF_MEMORY;
    }
    uint32_t read = 0;
    nsresult rv =
        mImpl->mUploadInput->Read(body.BeginWriting(), body.Length(), &read);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      return mImpl->mUploadInput->AsyncWait(this, 0, 0,
                                            GetMainThreadSerialEventTarget());
    }
    if (rv != NS_BASE_STREAM_CLOSED) {
      MOZ_TRY(rv);
    }
    body.Truncate(read);
    mImpl->mUploadFin = read == 0;
    const uint64_t seq = mImpl->mNextUpload;
    MOZ_TRY(Open("up", seq, body, mImpl->mUploadFin,
                 mImpl->mUploads[seq % kUploads]));
    ++mImpl->mNextUpload;
    mImpl->mUploadBytes += read;
    if (mImpl->mUploadFin) {
      break;
    }
  }
  return NS_OK;
}

NS_IMETHODIMP FiniteExchange::OnInputStreamReady(nsIAsyncInputStream*) {
  MOZ_ASSERT(NS_IsMainThread());
  nsresult rv = ReadUploads();
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
  return NS_OK;
}

NS_IMETHODIMP FiniteExchange::OnOutputStreamReady(nsIAsyncOutputStream*) {
  MOZ_ASSERT(NS_IsMainThread());
  nsresult rv = FlushDownloads();
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
  return NS_OK;
}

void FiniteExchange::Fail(nsresult aStatus) {
  RuntimeLogEvent("Connection %llu finite-exchanges failure=0x%08x\n",
                  static_cast<unsigned long long>(mImpl->mConnectionId),
                  static_cast<unsigned>(aStatus));
  Cancel(aStatus);
}

void FiniteExchange::Cancel(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mClosed) {
    return;
  }
  mImpl->mClosed = true;
  // Explicit session release is independent of cancelling outstanding receive
  // requests. The server also has a bounded idle safety timeout for lost peers.
  if (!mImpl->mSession.IsEmpty()) {
    RefPtr<Listener> closing;
    (void)Open("close", 0, EmptyCString(), false, closing);
  }
  auto cancel = [aStatus](RefPtr<Listener>& entry) {
    RefPtr item = std::move(entry);
    if (item && item->mRequest) {
      (void)item->mRequest->Cancel(NS_FAILED(aStatus) ? aStatus
                                                      : NS_BINDING_ABORTED);
    }
  };
  cancel(mImpl->mOpening);
  for (auto& item : mImpl->mUploads) {
    cancel(item);
  }
  for (auto& item : mImpl->mDownloads) {
    cancel(item);
  }
  if (mImpl->mUploadInput) {
    (void)mImpl->mUploadInput->AsyncWait(nullptr, 0, 0, nullptr);
    (void)mImpl->mUploadInput->CloseWithStatus(aStatus);
    (void)mImpl->mDownloadOutput->AsyncWait(nullptr, 0, 0, nullptr);
    (void)mImpl->mDownloadOutput->CloseWithStatus(aStatus);
  }
  if (mImpl->mUploadOutput) {
    (void)mImpl->mUploadOutput->CloseWithStatus(aStatus);
    (void)mImpl->mDownloadInput->CloseWithStatus(aStatus);
  }
  RuntimeLogEvent(
      "Connection %llu finite-exchanges closed=1 uploads=%llu "
      "downloads=%llu upload-bytes=%llu download-bytes=%llu\n",
      static_cast<unsigned long long>(mImpl->mConnectionId),
      static_cast<unsigned long long>(mImpl->mUploadCompleted),
      static_cast<unsigned long long>(mImpl->mDownloadCompleted),
      static_cast<unsigned long long>(mImpl->mUploadBytes),
      static_cast<unsigned long long>(mImpl->mDownloadBytes));
  auto callback = std::move(mImpl->mCallback);
  if (callback) {
    callback(NS_FAILED(aStatus) ? aStatus : NS_ERROR_ABORT, nullptr, nullptr);
  }
}

}  // namespace mozilla::naivefox
