/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "TunnelSession.h"

#include <algorithm>
#include <array>
#include <utility>

#include "AutoFallback.h"
#include "HeaderPadding.h"
#include "NeckoTunnel.h"
#include "PaddingNegotiation.h"
#include "RuntimeLogging.h"
#include "codec/NaivePadding.h"
#include "mozilla/Assertions.h"
#include "mozilla/Maybe.h"
#include "nsCOMPtr.h"
#include "nsError.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIEventTarget.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsIInputStream.h"
#include "nsIProxiedChannel.h"
#include "nsIRequest.h"
#include "nsISocketTransport.h"
#include "nsIStreamListener.h"
#include "nsITLSSocketControl.h"
#include "nsITimer.h"
#include "nsITransportSecurityInfo.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

namespace {

constexpr size_t kPumpBufferSize = 64 * 1024;

using net::naivefox::NaivePaddingDecoder;
using net::naivefox::NaivePaddingEncoder;
using net::naivefox::PaddingCodecStatus;
using net::naivefox::SystemPaddingLengthGenerator;

class DuplexPump;

class PumpDirection final : public nsIInputStreamCallback,
                            public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  PumpDirection(DuplexPump* aOwner, nsIAsyncInputStream* aInput,
                nsIAsyncOutputStream* aOutput, bool aEncode, bool aDecode);

  nsresult Start(Span<const uint8_t> aInitial = {});
  void Cancel();

 private:
  ~PumpDirection();

  nsresult WaitForInput();
  nsresult WaitForOutput();
  nsresult Flush();
  nsresult Produce();
  void Fail(nsresult aStatus);

  DuplexPump* mOwner;
  nsCOMPtr<nsIAsyncInputStream> mInput;
  nsCOMPtr<nsIAsyncOutputStream> mOutput;
  std::array<uint8_t, kPumpBufferSize> mInputBuffer;
  std::array<uint8_t, kPumpBufferSize> mOutputBuffer;
  size_t mInputOffset = 0;
  size_t mInputLength = 0;
  size_t mOutputOffset = 0;
  size_t mOutputLength = 0;
  SystemPaddingLengthGenerator mPaddingGenerator;
  Maybe<NaivePaddingEncoder> mEncoder;
  Maybe<NaivePaddingDecoder> mDecoder;
};

class DuplexPump final : public RefCounted<DuplexPump> {
 public:
  MOZ_DECLARE_REFCOUNTED_TYPENAME(DuplexPump)

  DuplexPump(nsIAsyncInputStream* aLocalIn, nsIAsyncOutputStream* aLocalOut,
             nsIAsyncInputStream* aTunnelIn, nsIAsyncOutputStream* aTunnelOut,
             bool aPaddingEnabled, std::function<void(nsresult)>&& aOnClose)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mTunnelIn(aTunnelIn),
        mTunnelOut(aTunnelOut),
        mPaddingEnabled(aPaddingEnabled),
        mOnClose(std::move(aOnClose)) {}

  nsresult Start(Span<const uint8_t> aInitialLocalPayload) {
    mUp = new PumpDirection(this, mLocalIn, mTunnelOut, mPaddingEnabled, false);
    mDown =
        new PumpDirection(this, mTunnelIn, mLocalOut, false, mPaddingEnabled);
    RefPtr<PumpDirection> down = mDown;
    nsresult rv = down->Start();
    if (NS_FAILED(rv)) {
      if (!mClosed) {
        Close(rv);
      }
      return rv;
    }
    if (mClosed || !mUp) {
      return NS_OK;
    }
    RefPtr<PumpDirection> up = mUp;
    rv = up->Start(aInitialLocalPayload);
    if (NS_FAILED(rv) && !mClosed) {
      Close(rv);
    }
    return rv;
  }

  void Close(nsresult aStatus) {
    if (mClosed) {
      return;
    }
    mClosed = true;
    if (mUp) {
      mUp->Cancel();
    }
    if (mDown) {
      mDown->Cancel();
    }
    (void)mLocalIn->CloseWithStatus(aStatus);
    (void)mLocalOut->CloseWithStatus(aStatus);
    (void)mTunnelIn->CloseWithStatus(aStatus);
    (void)mTunnelOut->CloseWithStatus(aStatus);
    mUp = nullptr;
    mDown = nullptr;
    if (mOnClose) {
      auto onClose = std::move(mOnClose);
      onClose(aStatus);
    }
  }

  void DirectionComplete(PumpDirection* aDirection) {
    if (mClosed) {
      return;
    }
    if (aDirection == mUp.get()) {
      if (!mUpComplete) {
        mUpComplete = true;
        mUp->Cancel();
        (void)mTunnelOut->CloseWithStatus(NS_OK);
      }
      return;
    }
    Close(NS_OK);
  }

  bool Closed() const { return mClosed; }
  ~DuplexPump() { Close(NS_BASE_STREAM_CLOSED); }

 private:
  nsCOMPtr<nsIAsyncInputStream> mLocalIn;
  nsCOMPtr<nsIAsyncOutputStream> mLocalOut;
  nsCOMPtr<nsIAsyncInputStream> mTunnelIn;
  nsCOMPtr<nsIAsyncOutputStream> mTunnelOut;
  RefPtr<PumpDirection> mUp;
  RefPtr<PumpDirection> mDown;
  bool mPaddingEnabled;
  std::function<void(nsresult)> mOnClose;
  bool mUpComplete = false;
  bool mClosed = false;
};

PumpDirection::PumpDirection(DuplexPump* aOwner, nsIAsyncInputStream* aInput,
                             nsIAsyncOutputStream* aOutput, bool aEncode,
                             bool aDecode)
    : mOwner(aOwner), mInput(aInput), mOutput(aOutput) {
  if (aEncode) {
    mEncoder.emplace(mPaddingGenerator);
  }
  if (aDecode) {
    mDecoder.emplace();
  }
}

PumpDirection::~PumpDirection() = default;

NS_IMPL_ISUPPORTS(PumpDirection, nsIInputStreamCallback,
                  nsIOutputStreamCallback)

nsresult PumpDirection::WaitForInput() {
  return mInput->AsyncWait(this, 0, 0, nullptr);
}

nsresult PumpDirection::WaitForOutput() {
  return mOutput->AsyncWait(this, 0, 0, nullptr);
}

void PumpDirection::Fail(nsresult aStatus) {
  if (mOwner) {
    mOwner->Close(aStatus);
  }
}

void PumpDirection::Cancel() {
  mOwner = nullptr;
  (void)mInput->AsyncWait(nullptr, 0, 0, nullptr);
  (void)mOutput->AsyncWait(nullptr, 0, 0, nullptr);
}

nsresult PumpDirection::Start(Span<const uint8_t> aInitial) {
  if (aInitial.Length() > mInputBuffer.size()) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  if (!aInitial.IsEmpty()) {
    std::copy(aInitial.begin(), aInitial.end(), mInputBuffer.begin());
    mInputLength = aInitial.Length();
  }
  return Produce();
}

nsresult PumpDirection::Flush() {
  while (mOutputOffset < mOutputLength) {
    uint32_t written = 0;
    nsresult rv = mOutput->Write(
        reinterpret_cast<const char*>(mOutputBuffer.data() + mOutputOffset),
        mOutputLength - mOutputOffset, &written);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      return WaitForOutput();
    }
    if (NS_FAILED(rv) || written == 0) {
      return NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
    }
    mOutputOffset += written;
  }
  mOutputOffset = 0;
  mOutputLength = 0;
  return Produce();
}

nsresult PumpDirection::Produce() {
  while (mOutputLength == 0) {
    if (mInputOffset == mInputLength) {
      mInputOffset = 0;
      mInputLength = 0;
      if (!mEncoder || mEncoder->BufferedByteCount() == 0) {
        uint32_t read = 0;
        nsresult rv = mInput->Read(reinterpret_cast<char*>(mInputBuffer.data()),
                                   mInputBuffer.size(), &read);
        if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
          return WaitForInput();
        }
        if (rv == NS_BASE_STREAM_CLOSED || (NS_SUCCEEDED(rv) && read == 0)) {
          if (mDecoder && mDecoder->Finish() != PaddingCodecStatus::Ok) {
            mOwner->Close(NS_ERROR_FAILURE);
          } else {
            mOwner->DirectionComplete(this);
          }
          return NS_OK;
        }
        if (NS_FAILED(rv)) {
          return rv;
        }
        mInputLength = read;
      }
    }

    Span<const uint8_t> input(mInputBuffer.data() + mInputOffset,
                              mInputLength - mInputOffset);
    Span<uint8_t> output(mOutputBuffer);
    size_t consumed = 0;
    if (mEncoder) {
      auto result = mEncoder->Encode(input, output);
      if (result.status != PaddingCodecStatus::Ok) {
        return NS_ERROR_FAILURE;
      }
      consumed = result.inputConsumed;
      mOutputLength = result.outputProduced;
    } else if (mDecoder) {
      auto result = mDecoder->Decode(input, output);
      if (result.status != PaddingCodecStatus::Ok) {
        return NS_ERROR_FAILURE;
      }
      consumed = result.inputConsumed;
      mOutputLength = result.outputProduced;
    } else {
      const size_t length = std::min(input.Length(), output.Length());
      std::copy(input.begin(), input.begin() + length, output.begin());
      consumed = length;
      mOutputLength = length;
    }
    mInputOffset += consumed;
    if (consumed == 0 && mOutputLength == 0) {
      return NS_ERROR_UNEXPECTED;
    }
  }
  return Flush();
}

NS_IMETHODIMP PumpDirection::OnInputStreamReady(nsIAsyncInputStream* aStream) {
  if (!mOwner || mOwner->Closed()) {
    return NS_OK;
  }
  nsresult rv = Produce();
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
  return NS_OK;
}

NS_IMETHODIMP PumpDirection::OnOutputStreamReady(
    nsIAsyncOutputStream* aStream) {
  if (!mOwner || mOwner->Closed()) {
    return NS_OK;
  }
  nsresult rv = Flush();
  if (NS_FAILED(rv)) {
    Fail(rv);
  }
  return NS_OK;
}

}  // namespace

class TunnelSession::Impl final {
 public:
  Impl(nsIAsyncInputStream* aLocalIn, nsIAsyncOutputStream* aLocalOut,
       const TunnelConfig& aConfig, nsIEventTarget* aSocketTarget,
       EstablishedCallback&& aOnEstablished, FailureCallback&& aOnFailure,
       ClosedCallback&& aOnClosed)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mConfig(aConfig),
        mSocketTarget(aSocketTarget),
        mOnEstablished(std::move(aOnEstablished)),
        mOnFailure(std::move(aOnFailure)),
        mOnClosed(std::move(aOnClosed)) {}

  nsCOMPtr<nsIAsyncInputStream> mLocalIn;
  nsCOMPtr<nsIAsyncOutputStream> mLocalOut;
  TunnelConfig mConfig;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  EstablishedCallback mOnEstablished;
  FailureCallback mOnFailure;
  ClosedCallback mOnClosed;
  nsCString mTargetAuthority;
  nsTArray<uint8_t> mInitialPayload;
  RefPtr<DuplexPump> mPump;
  ProxyProtocol mAttemptProtocol = ProxyProtocol::H2;
  uint64_t mAttemptGeneration = 0;
  bool mFallbackUsed = false;
  bool mTransportReady = false;
  bool mMetadataReady = false;
  nsresult mMetadataStatus = NS_ERROR_NOT_INITIALIZED;
  bool mChannelStopped = false;
  nsresult mChannelStatus = NS_ERROR_NOT_INITIALIZED;
  bool mConnectCodeKnown = false;
  int32_t mConnectCode = -1;
  Maybe<bool> mPaddingHeaderPresent;
  nsCString mOuterProtocol;
  bool mPaddingEnabled = false;
  nsCOMPtr<nsIAsyncInputStream> mPendingTunnelIn;
  nsCOMPtr<nsIAsyncOutputStream> mPendingTunnelOut;
  bool mUpgradeFailed = false;
  bool mEstablishmentTimedOut = false;
  bool mStarted = false;
  bool mReady = false;
  bool mPumpStarted = false;
  bool mFailed = false;
  bool mClosed = false;
};

class TunnelAttempt final : public nsIHttpUpgradeListener,
                            public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIHTTPUPGRADELISTENER
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  TunnelAttempt(TunnelSession* aOwner, nsIEventTarget* aSocketTarget,
                uint64_t aGeneration, ProxyProtocol aProtocol)
      : mOwner(aOwner),
        mSocketTarget(aSocketTarget),
        mGeneration(aGeneration),
        mProtocol(aProtocol) {}

  nsresult ArmEstablishmentTimeout(nsIRequest* aRequest);

 private:
  ~TunnelAttempt();
  void CancelEstablishmentTimeout();

  RefPtr<TunnelSession> mOwner;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  const uint64_t mGeneration;
  const ProxyProtocol mProtocol;
  nsCOMPtr<nsITimer> mEstablishmentTimer;
};

NS_IMPL_ISUPPORTS(TunnelAttempt, nsIHttpUpgradeListener, nsIStreamListener,
                  nsIRequestObserver)

TunnelAttempt::~TunnelAttempt() { CancelEstablishmentTimeout(); }

nsresult TunnelAttempt::ArmEstablishmentTimeout(nsIRequest* aRequest) {
  constexpr uint32_t kAutoH3EstablishmentTimeoutMs = 5000;
  RefPtr self = this;
  nsCOMPtr<nsIRequest> request = aRequest;
  auto timer = NS_NewTimerWithCallback(
      [self, request = std::move(request)](nsITimer*) {
        self->mEstablishmentTimer = nullptr;
        RefPtr owner = self->mOwner;
        const uint64_t generation = self->mGeneration;
        const ProxyProtocol protocol = self->mProtocol;
        (void)self->mSocketTarget->Dispatch(
            NS_NewRunnableFunction("NaiveFox::AutoH3EstablishmentTimedOut",
                                   [owner, generation, protocol]() {
                                     owner->ApplyEstablishmentTimeout(
                                         generation, protocol);
                                   }),
            NS_DISPATCH_NORMAL);
        (void)request->Cancel(NS_ERROR_NET_TIMEOUT);
      },
      kAutoH3EstablishmentTimeoutMs, nsITimer::TYPE_ONE_SHOT,
      "NaiveFox::AutoH3EstablishmentTimeout"_ns);
  if (timer.isErr()) {
    return timer.unwrapErr();
  }
  mEstablishmentTimer = timer.unwrap();
  return NS_OK;
}

void TunnelAttempt::CancelEstablishmentTimeout() {
  if (mEstablishmentTimer) {
    (void)mEstablishmentTimer->Cancel();
    mEstablishmentTimer = nullptr;
  }
}

TunnelSession::TunnelSession(nsIAsyncInputStream* aLocalIn,
                             nsIAsyncOutputStream* aLocalOut,
                             const TunnelConfig& aConfig,
                             nsIEventTarget* aSocketTarget,
                             EstablishedCallback&& aOnEstablished,
                             FailureCallback&& aOnFailure,
                             ClosedCallback&& aOnClosed)
    : mImpl(MakeUnique<Impl>(aLocalIn, aLocalOut, aConfig, aSocketTarget,
                             std::move(aOnEstablished), std::move(aOnFailure),
                             std::move(aOnClosed))) {}

TunnelSession::~TunnelSession() { Cancel(NS_BASE_STREAM_CLOSED); }

nsresult TunnelSession::Start(const nsACString& aTargetAuthority,
                              Span<const uint8_t> aInitialPayload) {
  if (mImpl->mStarted || mImpl->mClosed || aTargetAuthority.IsEmpty()) {
    return NS_ERROR_INVALID_ARG;
  }
  mImpl->mStarted = true;
  mImpl->mTargetAuthority = aTargetAuthority;
  mImpl->mInitialPayload.AppendElements(aInitialPayload);
  const ProxyProtocol firstProtocol =
      mImpl->mConfig.mProtocol == ProxyProtocol::Auto
          ? ProxyProtocol::H3
          : mImpl->mConfig.mProtocol;
  return StartAttempt(firstProtocol);
}

nsresult TunnelSession::StartAttempt(ProxyProtocol aProtocol) {
  MOZ_ASSERT(!NS_IsMainThread());
  if (mImpl->mClosed || mImpl->mFailed || aProtocol == ProxyProtocol::Auto) {
    return NS_ERROR_INVALID_ARG;
  }
  ++mImpl->mAttemptGeneration;
  mImpl->mAttemptProtocol = aProtocol;
  ResetAttemptState();
  RefPtr self = this;
  const uint64_t generation = mImpl->mAttemptGeneration;
  nsCString authority(mImpl->mTargetAuthority);
  return NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::OpenTunnelAttempt",
      [self, generation, aProtocol, authority = std::move(authority)]() {
        self->OpenAttemptOnMain(generation, aProtocol, authority);
      }));
}

void TunnelSession::OpenAttemptOnMain(uint64_t aGeneration,
                                      ProxyProtocol aProtocol,
                                      const nsACString& aTargetAuthority) {
  MOZ_ASSERT(NS_IsMainThread());
  nsAutoCString padding;
  nsresult rv = GenerateHeaderPadding(padding);
  if (NS_SUCCEEDED(rv)) {
    RefPtr<TunnelAttempt> attempt =
        new TunnelAttempt(this, mImpl->mSocketTarget, aGeneration, aProtocol);
    nsCOMPtr<nsIRequest> openedRequest;
    rv = OpenNeckoTunnel(mImpl->mConfig.mProxyUrl, aTargetAuthority,
                         mImpl->mConfig.mProxyUser,
                         mImpl->mConfig.mProxyPassword, attempt, attempt,
                         padding, aProtocol, getter_AddRefs(openedRequest));
    if (NS_SUCCEEDED(rv) && mImpl->mConfig.mProtocol == ProxyProtocol::Auto &&
        aProtocol == ProxyProtocol::H3) {
      rv = attempt->ArmEstablishmentTimeout(openedRequest);
    }
  }
  if (NS_FAILED(rv)) {
    RefPtr self = this;
    (void)mImpl->mSocketTarget->Dispatch(
        NS_NewRunnableFunction("NaiveFox::TunnelOpenFailure",
                               [self, aGeneration, aProtocol, rv]() {
                                 self->ApplyOpenFailure(aGeneration, aProtocol,
                                                        rv);
                               }),
        NS_DISPATCH_NORMAL);
  }
}

NS_IMETHODIMP TunnelAttempt::OnStartRequest(nsIRequest* aRequest) {
  CancelEstablishmentTimeout();
  nsCOMPtr<nsIProxiedChannel> proxied = do_QueryInterface(aRequest);
  int32_t connectCode = -1;
  bool connectCodeKnown = false;
  nsresult rv = NS_ERROR_UNEXPECTED;
  Maybe<bool> paddingHeaderPresent;
  nsAutoCString outerProtocol;
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(aRequest);
  if (proxied && http) {
    rv = proxied->GetHttpProxyConnectResponseCode(&connectCode);
    if (NS_SUCCEEDED(rv)) {
      connectCodeKnown = true;
      rv = http->GetProtocolVersion(outerProtocol);
    }
    if (NS_SUCCEEDED(rv)) {
      nsAutoCString padding;
      nsresult headerRv =
          proxied->GetHttpProxyResponseHeader("padding"_ns, padding);
      if (NS_SUCCEEDED(headerRv)) {
        paddingHeaderPresent = Some(true);
      } else if (headerRv == NS_ERROR_NOT_AVAILABLE) {
        paddingHeaderPresent = Some(false);
      } else {
        rv = headerRv;
      }
    }
  }
  RefPtr owner = mOwner;
  const uint64_t generation = mGeneration;
  const ProxyProtocol protocol = mProtocol;
  (void)mSocketTarget->Dispatch(
      NS_NewRunnableFunction(
          "NaiveFox::TunnelConnectMetadata",
          [owner, generation, protocol, rv, connectCodeKnown, connectCode,
           paddingHeaderPresent, outerProtocol = std::move(outerProtocol)]() {
            owner->ApplyConnectMetadata(generation, protocol, rv,
                                        connectCodeKnown, connectCode,
                                        paddingHeaderPresent, outerProtocol);
          }),
      NS_DISPATCH_NORMAL);
  return NS_OK;
}

NS_IMETHODIMP TunnelAttempt::OnDataAvailable(nsIRequest* aRequest,
                                             nsIInputStream* aInput,
                                             uint64_t aOffset,
                                             uint32_t aCount) {
  char discard[512];
  while (aCount) {
    uint32_t read = 0;
    MOZ_TRY(aInput->Read(discard, std::min<uint32_t>(aCount, sizeof(discard)),
                         &read));
    if (!read) {
      return NS_ERROR_UNEXPECTED;
    }
    aCount -= read;
  }
  return NS_OK;
}

NS_IMETHODIMP TunnelAttempt::OnStopRequest(nsIRequest* aRequest,
                                           nsresult aStatus) {
  CancelEstablishmentTimeout();
  RefPtr owner = mOwner;
  const uint64_t generation = mGeneration;
  const ProxyProtocol protocol = mProtocol;
  (void)mSocketTarget->Dispatch(
      NS_NewRunnableFunction("NaiveFox::TunnelChannelStop",
                             [owner, generation, protocol, aStatus]() {
                               owner->ApplyChannelStop(generation, protocol,
                                                       aStatus);
                             }),
      NS_DISPATCH_NORMAL);
  return NS_OK;
}

NS_IMETHODIMP TunnelAttempt::OnTransportAvailable(
    nsISocketTransport* aTransport, nsIAsyncInputStream* aSocketIn,
    nsIAsyncOutputStream* aSocketOut) {
  RefPtr owner = mOwner;
  nsCOMPtr<nsISocketTransport> transport = aTransport;
  nsCOMPtr<nsIAsyncInputStream> socketIn = aSocketIn;
  nsCOMPtr<nsIAsyncOutputStream> socketOut = aSocketOut;
  const uint64_t generation = mGeneration;
  const ProxyProtocol protocol = mProtocol;
  nsresult rv = mSocketTarget->Dispatch(
      NS_NewRunnableFunction(
          "NaiveFox::TunnelTransport",
          [owner, generation, protocol, transport = std::move(transport),
           socketIn = std::move(socketIn), socketOut = std::move(socketOut)]() {
            owner->ApplyTransport(generation, protocol, transport, socketIn,
                                  socketOut);
          }),
      NS_DISPATCH_NORMAL);
  if (NS_FAILED(rv)) {
    (void)aSocketIn->CloseWithStatus(rv);
    (void)aSocketOut->CloseWithStatus(rv);
  }
  return rv;
}

NS_IMETHODIMP TunnelAttempt::OnUpgradeFailed(nsresult aErrorCode) {
  RefPtr owner = mOwner;
  const uint64_t generation = mGeneration;
  const ProxyProtocol protocol = mProtocol;
  return mSocketTarget->Dispatch(
      NS_NewRunnableFunction("NaiveFox::TunnelFailure",
                             [owner, generation, protocol, aErrorCode]() {
                               owner->ApplyUpgradeFailure(generation, protocol,
                                                          aErrorCode);
                             }),
      NS_DISPATCH_NORMAL);
}

NS_IMETHODIMP TunnelAttempt::OnWebSocketConnectionAvailable(
    mozilla::net::WebSocketConnectionBase* aConnection) {
  return NS_ERROR_NOT_IMPLEMENTED;
}

void TunnelSession::ApplyTransport(uint64_t aGeneration,
                                   ProxyProtocol aProtocol,
                                   nsISocketTransport* aTransport,
                                   nsIAsyncInputStream* aSocketIn,
                                   nsIAsyncOutputStream* aSocketOut) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mImpl->mClosed ||
      mImpl->mFailed) {
    (void)aSocketIn->CloseWithStatus(NS_ERROR_ABORT);
    (void)aSocketOut->CloseWithStatus(NS_ERROR_ABORT);
    return;
  }
  if (aProtocol == ProxyProtocol::H2) {
    nsCOMPtr<nsITLSSocketControl> tls;
    nsCOMPtr<nsITransportSecurityInfo> securityInfo;
    nsAutoCString alpn;
    nsresult rv = aTransport->GetTlsSocketControl(getter_AddRefs(tls));
    if (NS_SUCCEEDED(rv) && tls) {
      rv = tls->GetSecurityInfo(getter_AddRefs(securityInfo));
    }
    if (NS_SUCCEEDED(rv) && securityInfo) {
      rv = securityInfo->GetNegotiatedNPN(alpn);
    }
    if (NS_FAILED(rv) || !alpn.EqualsLiteral("h2")) {
      (void)aSocketIn->CloseWithStatus(NS_ERROR_FAILURE);
      (void)aSocketOut->CloseWithStatus(NS_ERROR_FAILURE);
      Fail(NS_ERROR_FAILURE);
      return;
    }
  }
  mImpl->mPendingTunnelIn = aSocketIn;
  mImpl->mPendingTunnelOut = aSocketOut;
  mImpl->mTransportReady = true;
  MaybeFinishAttempt();
}

void TunnelSession::ApplyConnectMetadata(
    uint64_t aGeneration, ProxyProtocol aProtocol, nsresult aStatus,
    bool aConnectCodeKnown, int32_t aConnectCode,
    const Maybe<bool>& aPaddingHeaderPresent,
    const nsACString& aOuterProtocol) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mImpl->mMetadataReady) {
    return;
  }
  mImpl->mMetadataReady = true;
  mImpl->mMetadataStatus = aStatus;
  mImpl->mConnectCodeKnown = aConnectCodeKnown;
  mImpl->mConnectCode = aConnectCode;
  mImpl->mPaddingHeaderPresent = aPaddingHeaderPresent;
  mImpl->mOuterProtocol = aOuterProtocol;
  MaybeFinishAttempt();
}

void TunnelSession::ApplyChannelStop(uint64_t aGeneration,
                                     ProxyProtocol aProtocol,
                                     nsresult aStatus) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mImpl->mChannelStopped) {
    return;
  }
  mImpl->mChannelStopped = true;
  mImpl->mChannelStatus = aStatus;
  MaybeFinishAttempt();
}

void TunnelSession::ApplyUpgradeFailure(uint64_t aGeneration,
                                        ProxyProtocol aProtocol,
                                        nsresult aStatus) {
  if (!IsCurrentAttempt(aGeneration, aProtocol)) {
    return;
  }
  mImpl->mUpgradeFailed = true;
  MaybeFinishAttempt();
}

void TunnelSession::ApplyEstablishmentTimeout(uint64_t aGeneration,
                                              ProxyProtocol aProtocol) {
  if (!IsCurrentAttempt(aGeneration, aProtocol)) {
    return;
  }
  mImpl->mEstablishmentTimedOut = true;
  MaybeFinishAttempt();
}

void TunnelSession::ApplyOpenFailure(uint64_t aGeneration,
                                     ProxyProtocol aProtocol,
                                     nsresult aStatus) {
  if (!IsCurrentAttempt(aGeneration, aProtocol)) {
    return;
  }
  Fail(aStatus);
}

bool TunnelSession::IsCurrentAttempt(uint64_t aGeneration,
                                     ProxyProtocol aProtocol) const {
  return aGeneration == mImpl->mAttemptGeneration &&
         aProtocol == mImpl->mAttemptProtocol;
}

void TunnelSession::ResetAttemptState() {
  if (mImpl->mPendingTunnelIn) {
    (void)mImpl->mPendingTunnelIn->CloseWithStatus(NS_ERROR_ABORT);
  }
  if (mImpl->mPendingTunnelOut) {
    (void)mImpl->mPendingTunnelOut->CloseWithStatus(NS_ERROR_ABORT);
  }
  mImpl->mTransportReady = false;
  mImpl->mMetadataReady = false;
  mImpl->mMetadataStatus = NS_ERROR_NOT_INITIALIZED;
  mImpl->mChannelStopped = false;
  mImpl->mChannelStatus = NS_ERROR_NOT_INITIALIZED;
  mImpl->mConnectCodeKnown = false;
  mImpl->mConnectCode = -1;
  mImpl->mPaddingHeaderPresent.reset();
  mImpl->mOuterProtocol.Truncate();
  mImpl->mPaddingEnabled = false;
  mImpl->mPendingTunnelIn = nullptr;
  mImpl->mPendingTunnelOut = nullptr;
  mImpl->mUpgradeFailed = false;
  mImpl->mEstablishmentTimedOut = false;
}

void TunnelSession::MaybeFinishAttempt() {
  if (!mImpl->mChannelStopped ||
      (!mImpl->mMetadataReady && !mImpl->mEstablishmentTimedOut) ||
      mImpl->mReady || mImpl->mClosed || mImpl->mFailed) {
    return;
  }
  const AutoFallbackState fallbackState{
      mImpl->mConfig.mProtocol,      mImpl->mAttemptProtocol,
      mImpl->mFallbackUsed,          mImpl->mClosed,
      mImpl->mChannelStopped,        NS_FAILED(mImpl->mChannelStatus),
      mImpl->mEstablishmentTimedOut, mImpl->mConnectCodeKnown,
      mImpl->mConnectCode,           mImpl->mTransportReady,
  };
  if (ShouldRetryH2FromH3(fallbackState)) {
    mImpl->mFallbackUsed = true;
    nsresult rv = StartAttempt(ProxyProtocol::H2);
    if (NS_FAILED(rv)) {
      Fail(rv);
    }
    return;
  }
  if (!mImpl->mMetadataReady) {
    Fail(NS_FAILED(mImpl->mChannelStatus) ? mImpl->mChannelStatus
                                          : NS_ERROR_FAILURE);
    return;
  }
  const bool protocolMatches = (mImpl->mAttemptProtocol == ProxyProtocol::H2 &&
                                mImpl->mOuterProtocol.EqualsLiteral("h2")) ||
                               (mImpl->mAttemptProtocol == ProxyProtocol::H3 &&
                                mImpl->mOuterProtocol.EqualsLiteral("h3"));
  if (NS_FAILED(mImpl->mMetadataStatus) || NS_FAILED(mImpl->mChannelStatus) ||
      !mImpl->mConnectCodeKnown || !protocolMatches ||
      NS_FAILED(NegotiatePayloadPadding(mImpl->mConnectCode,
                                        mImpl->mPaddingHeaderPresent,
                                        mImpl->mPaddingEnabled))) {
    Fail(NS_FAILED(mImpl->mChannelStatus) ? mImpl->mChannelStatus
                                          : NS_ERROR_FAILURE);
    return;
  }
  if (!mImpl->mTransportReady) {
    if (mImpl->mUpgradeFailed) {
      Fail(NS_ERROR_FAILURE);
    }
    return;
  }
  TunnelReady();
}

void TunnelSession::TunnelReady() {
  if (mImpl->mClosed || mImpl->mFailed || mImpl->mReady) {
    return;
  }
  mImpl->mReady = true;
  RuntimeLog("Outer protocol: %s\n", mImpl->mOuterProtocol.get());
  RuntimeLog("Padding negotiated: %s\n", mImpl->mPaddingEnabled ? "yes" : "no");
  if (mImpl->mOnEstablished) {
    mImpl->mOnEstablished(mImpl->mOuterProtocol, mImpl->mPaddingEnabled);
  }
}

nsresult TunnelSession::StartPump() {
  if (!mImpl->mReady || mImpl->mPumpStarted || mImpl->mClosed ||
      mImpl->mFailed || !mImpl->mPendingTunnelIn || !mImpl->mPendingTunnelOut) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  mImpl->mPumpStarted = true;
  RefPtr self = this;
  mImpl->mPump =
      new DuplexPump(mImpl->mLocalIn, mImpl->mLocalOut, mImpl->mPendingTunnelIn,
                     mImpl->mPendingTunnelOut, mImpl->mPaddingEnabled,
                     [self](nsresult aStatus) { self->Cancel(aStatus); });
  mImpl->mPendingTunnelIn = nullptr;
  mImpl->mPendingTunnelOut = nullptr;
  nsTArray<uint8_t> initialPayload = std::move(mImpl->mInitialPayload);
  return mImpl->mPump->Start(Span(initialPayload));
}

void TunnelSession::Fail(nsresult aStatus) {
  if (mImpl->mClosed || mImpl->mFailed) {
    return;
  }
  mImpl->mFailed = true;
  if (mImpl->mPendingTunnelIn) {
    (void)mImpl->mPendingTunnelIn->CloseWithStatus(aStatus);
    mImpl->mPendingTunnelIn = nullptr;
  }
  if (mImpl->mPendingTunnelOut) {
    (void)mImpl->mPendingTunnelOut->CloseWithStatus(aStatus);
    mImpl->mPendingTunnelOut = nullptr;
  }
  mImpl->mOnEstablished = nullptr;
  mImpl->mOnClosed = nullptr;
  if (mImpl->mOnFailure) {
    auto onFailure = std::move(mImpl->mOnFailure);
    onFailure(aStatus);
  }
}

void TunnelSession::Cancel(nsresult aStatus) {
  if (mImpl->mClosed) {
    return;
  }
  mImpl->mClosed = true;
  (void)mImpl->mLocalIn->CloseWithStatus(aStatus);
  (void)mImpl->mLocalOut->CloseWithStatus(aStatus);
  if (mImpl->mPendingTunnelIn) {
    (void)mImpl->mPendingTunnelIn->CloseWithStatus(aStatus);
    mImpl->mPendingTunnelIn = nullptr;
  }
  if (mImpl->mPendingTunnelOut) {
    (void)mImpl->mPendingTunnelOut->CloseWithStatus(aStatus);
    mImpl->mPendingTunnelOut = nullptr;
  }
  if (mImpl->mPump) {
    mImpl->mPump->Close(aStatus);
    mImpl->mPump = nullptr;
  }
  mImpl->mOnEstablished = nullptr;
  mImpl->mOnFailure = nullptr;
  if (mImpl->mOnClosed) {
    auto onClosed = std::move(mImpl->mOnClosed);
    onClosed(aStatus);
  }
}

}  // namespace mozilla::naivefox
