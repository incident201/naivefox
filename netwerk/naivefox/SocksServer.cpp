/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "SocksServer.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <functional>
#include <utility>

#include "AutoFallback.h"
#include "HeaderPadding.h"
#include "NeckoTunnel.h"
#include "PaddingNegotiation.h"
#include "Socks5Parser.h"
#include "codec/NaivePadding.h"
#include "mozilla/Assertions.h"
#include "mozilla/Atomics.h"
#include "mozilla/ErrorNames.h"
#include "mozilla/Maybe.h"
#include "mozilla/RefPtr.h"
#include "mozilla/Span.h"
#include "nsCOMPtr.h"
#include "nsComponentManagerUtils.h"
#include "nsError.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIEventTarget.h"
#include "nsIHttpChannel.h"
#include "nsIHttpChannelInternal.h"
#include "nsIInputStream.h"
#include "nsIProxiedChannel.h"
#include "nsIRequest.h"
#include "nsIServerSocket.h"
#include "nsISocketTransport.h"
#include "nsIStreamListener.h"
#include "nsITLSSocketControl.h"
#include "nsITimer.h"
#include "nsITransport.h"
#include "nsITransportSecurityInfo.h"
#include "nsNetCID.h"
#include "nsServiceManagerUtils.h"
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
    // Start() can synchronously observe EOF and close the whole pump. Keep a
    // local strong reference and do not dereference a direction that Close()
    // has already detached.
    if (mClosed || !mUp) {
      return NS_OK;
    }
    RefPtr<PumpDirection> up = mUp;
    rv = up->Start(aInitialLocalPayload);
    if (NS_FAILED(rv)) {
      if (!mClosed) {
        Close(rv);
      }
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

class TunnelAttempt;

class SocksConnection final : public nsIInputStreamCallback,
                              public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  SocksConnection(nsIAsyncInputStream* aLocalIn,
                  nsIAsyncOutputStream* aLocalOut, const nsACString& aProxyUrl,
                  const nsACString& aProxyUser,
                  const nsACString& aProxyPassword, ProxyProtocol aProtocol,
                  nsIEventTarget* aSocketTarget,
                  std::function<void()>&& aOnClose)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mProxyUrl(aProxyUrl),
        mProxyUser(aProxyUser),
        mProxyPassword(aProxyPassword),
        mProtocol(aProtocol),
        mSocketTarget(aSocketTarget),
        mOnClose(std::move(aOnClose)) {}

  nsresult Start() { return WaitForInput(); }

 private:
  friend class TunnelAttempt;

  ~SocksConnection() { Close(NS_BASE_STREAM_CLOSED); }

  nsresult WaitForInput();
  nsresult WaitForOutput();
  nsresult QueueReply(Span<const uint8_t> aBytes, bool aCloseAfter);
  nsresult FlushReplies();
  nsresult StartAttempt(ProxyProtocol aProtocol);
  void OpenAttemptOnMain(uint64_t aGeneration, ProxyProtocol aProtocol,
                         const nsACString& aTargetAuthority);
  void ApplyConnectMetadata(uint64_t aGeneration, ProxyProtocol aProtocol,
                            nsresult aStatus, bool aConnectCodeKnown,
                            int32_t aConnectCode,
                            const Maybe<bool>& aPaddingHeaderPresent,
                            const nsACString& aOuterProtocol);
  void ApplyChannelStop(uint64_t aGeneration, ProxyProtocol aProtocol,
                        nsresult aStatus);
  void ApplyTransport(uint64_t aGeneration, ProxyProtocol aProtocol,
                      nsISocketTransport* aTransport,
                      nsIAsyncInputStream* aSocketIn,
                      nsIAsyncOutputStream* aSocketOut);
  void ApplyUpgradeFailure(uint64_t aGeneration, ProxyProtocol aProtocol,
                           nsresult aStatus);
  void ApplyEstablishmentTimeout(uint64_t aGeneration, ProxyProtocol aProtocol);
  void ApplyOpenFailure(uint64_t aGeneration, ProxyProtocol aProtocol);
  bool IsCurrentAttempt(uint64_t aGeneration, ProxyProtocol aProtocol) const;
  void ResetAttemptState();
  void MaybeStartTunnel();
  void TunnelReady(nsIAsyncInputStream* aTunnelIn,
                   nsIAsyncOutputStream* aTunnelOut);
  void Reject(Socks5Parser::Event aEvent);
  void Close(nsresult aStatus);

  nsCOMPtr<nsIAsyncInputStream> mLocalIn;
  nsCOMPtr<nsIAsyncOutputStream> mLocalOut;
  nsCString mProxyUrl;
  nsCString mProxyUser;
  nsCString mProxyPassword;
  ProxyProtocol mProtocol;
  ProxyProtocol mAttemptProtocol = ProxyProtocol::H2;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  Socks5Parser mParser;
  nsTArray<uint8_t> mReplies;
  size_t mReplyOffset = 0;
  nsTArray<uint8_t> mInitialPayload;
  RefPtr<DuplexPump> mPump;
  std::function<void()> mOnClose;
  bool mTunnelOpening = false;
  bool mTunnelReady = false;
  bool mPumpStarted = false;
  uint64_t mAttemptGeneration = 0;
  bool mFallbackUsed = false;
  nsCString mTargetAuthority;
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
  bool mOutputWaiting = false;
  bool mCloseAfterWrite = false;
  bool mFailureQueued = false;
  bool mClosed = false;
};

class TunnelAttempt final : public nsIHttpUpgradeListener,
                            public nsIStreamListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIHTTPUPGRADELISTENER
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  TunnelAttempt(SocksConnection* aOwner, nsIEventTarget* aSocketTarget,
                uint64_t aGeneration, ProxyProtocol aProtocol)
      : mOwner(aOwner),
        mSocketTarget(aSocketTarget),
        mGeneration(aGeneration),
        mProtocol(aProtocol) {}

  nsresult ArmEstablishmentTimeout(nsIRequest* aRequest);

 private:
  ~TunnelAttempt();
  void CancelEstablishmentTimeout();

  RefPtr<SocksConnection> mOwner;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  const uint64_t mGeneration;
  const ProxyProtocol mProtocol;
  nsCOMPtr<nsITimer> mEstablishmentTimer;
};

NS_IMPL_ISUPPORTS(SocksConnection, nsIInputStreamCallback,
                  nsIOutputStreamCallback)
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

nsresult SocksConnection::WaitForInput() {
  return mLocalIn->AsyncWait(this, 0, 0, nullptr);
}

nsresult SocksConnection::WaitForOutput() {
  if (mOutputWaiting) {
    return NS_OK;
  }
  nsresult rv = mLocalOut->AsyncWait(this, 0, 0, nullptr);
  if (NS_SUCCEEDED(rv)) {
    mOutputWaiting = true;
  }
  return rv;
}

nsresult SocksConnection::QueueReply(Span<const uint8_t> aBytes,
                                     bool aCloseAfter) {
  mReplies.AppendElements(aBytes);
  mCloseAfterWrite |= aCloseAfter;
  return WaitForOutput();
}

nsresult SocksConnection::FlushReplies() {
  while (mReplyOffset < mReplies.Length()) {
    uint32_t written = 0;
    nsresult rv = mLocalOut->Write(
        reinterpret_cast<const char*>(mReplies.Elements() + mReplyOffset),
        mReplies.Length() - mReplyOffset, &written);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      return WaitForOutput();
    }
    if (NS_FAILED(rv) || written == 0) {
      return NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
    }
    mReplyOffset += written;
  }
  mReplies.Clear();
  mReplyOffset = 0;
  if (mCloseAfterWrite) {
    Close(NS_OK);
  } else if (mTunnelReady && mPump && !mPumpStarted) {
    mPumpStarted = true;
    nsTArray<uint8_t> initialPayload = std::move(mInitialPayload);
    return mPump->Start(Span(initialPayload));
  }
  return NS_OK;
}

void SocksConnection::Reject(Socks5Parser::Event aEvent) {
  if (mClosed || mFailureQueued) {
    return;
  }
  mFailureQueued = true;
  nsTArray<uint8_t> reply;
  if (aEvent == Socks5Parser::Event::RejectMethods) {
    Socks5Parser::MakeMethodSelection(false, reply);
  } else {
    uint8_t code = aEvent == Socks5Parser::Event::RejectCommand       ? 0x07
                   : aEvent == Socks5Parser::Event::RejectAddressType ? 0x08
                                                                      : 0x01;
    Socks5Parser::MakeReply(code, reply);
  }
  nsresult rv = QueueReply(Span(reply), true);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

nsresult SocksConnection::StartAttempt(ProxyProtocol aProtocol) {
  MOZ_ASSERT(!NS_IsMainThread());
  if (mClosed || aProtocol == ProxyProtocol::Auto) {
    return NS_ERROR_INVALID_ARG;
  }
  ++mAttemptGeneration;
  mAttemptProtocol = aProtocol;
  ResetAttemptState();
  RefPtr self = this;
  const uint64_t generation = mAttemptGeneration;
  nsCString authority(mTargetAuthority);
  return NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::OpenSocksTunnelAttempt",
      [self, generation, aProtocol, authority = std::move(authority)]() {
        self->OpenAttemptOnMain(generation, aProtocol, authority);
      }));
}

void SocksConnection::OpenAttemptOnMain(uint64_t aGeneration,
                                        ProxyProtocol aProtocol,
                                        const nsACString& aTargetAuthority) {
  MOZ_ASSERT(NS_IsMainThread());
  nsAutoCString padding;
  nsresult rv = GenerateHeaderPadding(padding);
  if (NS_FAILED(rv)) {
    RefPtr self = this;
    (void)mSocketTarget->Dispatch(
        NS_NewRunnableFunction("NaiveFox::PaddingGenerationFailure",
                               [self, aGeneration, aProtocol]() {
                                 self->ApplyOpenFailure(aGeneration, aProtocol);
                               }),
        NS_DISPATCH_NORMAL);
    return;
  }
  RefPtr<TunnelAttempt> attempt =
      new TunnelAttempt(this, mSocketTarget, aGeneration, aProtocol);
  nsCOMPtr<nsIRequest> openedRequest;
  rv = OpenNeckoTunnel(mProxyUrl, aTargetAuthority, mProxyUser, mProxyPassword,
                       attempt, attempt, padding, aProtocol,
                       getter_AddRefs(openedRequest));
  if (NS_SUCCEEDED(rv) && mProtocol == ProxyProtocol::Auto &&
      aProtocol == ProxyProtocol::H3) {
    rv = attempt->ArmEstablishmentTimeout(openedRequest);
  }
  if (NS_FAILED(rv)) {
    RefPtr self = this;
    (void)mSocketTarget->Dispatch(
        NS_NewRunnableFunction("NaiveFox::SocksTunnelOpenFailure",
                               [self, aGeneration, aProtocol]() {
                                 self->ApplyOpenFailure(aGeneration, aProtocol);
                               }),
        NS_DISPATCH_NORMAL);
  }
}

NS_IMETHODIMP SocksConnection::OnInputStreamReady(
    nsIAsyncInputStream* aStream) {
  if (mClosed || mTunnelOpening) {
    return NS_OK;
  }
  std::array<uint8_t, 4096> buffer;
  uint32_t read = 0;
  nsresult rv = aStream->Read(reinterpret_cast<char*>(buffer.data()),
                              buffer.size(), &read);
  if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
    rv = WaitForInput();
    if (NS_FAILED(rv)) {
      Close(rv);
    }
    return NS_OK;
  }
  if (NS_FAILED(rv) || read == 0) {
    Close(NS_FAILED(rv) ? rv : NS_BASE_STREAM_CLOSED);
    return NS_OK;
  }

  size_t offset = 0;
  while (offset < read && !mTunnelOpening && !mClosed) {
    size_t consumed = 0;
    auto event =
        mParser.Consume(Span(buffer.data() + offset, read - offset), consumed);
    offset += consumed;
    if (event == Socks5Parser::Event::SendNoAuthenticationSelection) {
      nsTArray<uint8_t> reply;
      Socks5Parser::MakeMethodSelection(true, reply);
      rv = QueueReply(Span(reply), false);
      if (NS_FAILED(rv)) {
        Close(rv);
      }
    } else if (event == Socks5Parser::Event::RequestReady) {
      mTunnelOpening = true;
      if (offset < read) {
        mInitialPayload.AppendElements(buffer.data() + offset, read - offset);
      }
      mTargetAuthority = mParser.Target().Authority();
      const ProxyProtocol firstProtocol =
          mProtocol == ProxyProtocol::Auto ? ProxyProtocol::H3 : mProtocol;
      rv = StartAttempt(firstProtocol);
      if (NS_FAILED(rv)) {
        Close(rv);
      }
    } else if (event != Socks5Parser::Event::NeedMore) {
      Reject(event);
    }
  }
  if (!mTunnelOpening && !mClosed) {
    rv = WaitForInput();
    if (NS_FAILED(rv)) {
      Close(rv);
    }
  }
  return NS_OK;
}

NS_IMETHODIMP SocksConnection::OnOutputStreamReady(
    nsIAsyncOutputStream* aStream) {
  mOutputWaiting = false;
  if (mClosed) {
    return NS_OK;
  }
  nsresult rv = FlushReplies();
  if (NS_FAILED(rv)) {
    Close(rv);
  }
  return NS_OK;
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
          "NaiveFox::SocksConnectMetadata",
          [owner, generation, protocol, rv, connectCodeKnown, connectCode,
           paddingHeaderPresent, outerProtocol = std::move(outerProtocol)]() {
            owner->ApplyConnectMetadata(generation, protocol, rv,
                                        connectCodeKnown, connectCode,
                                        paddingHeaderPresent, outerProtocol);
          }),
      NS_DISPATCH_NORMAL);
  // Metadata collection is diagnostic. It must not change the channel result.
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
      NS_NewRunnableFunction("NaiveFox::SocksChannelStop",
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
          "NaiveFox::SocksTunnelTransport",
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
      NS_NewRunnableFunction("NaiveFox::SocksTunnelFailure",
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

void SocksConnection::ApplyTransport(uint64_t aGeneration,
                                     ProxyProtocol aProtocol,
                                     nsISocketTransport* aTransport,
                                     nsIAsyncInputStream* aSocketIn,
                                     nsIAsyncOutputStream* aSocketOut) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mClosed) {
    (void)aSocketIn->CloseWithStatus(NS_ERROR_ABORT);
    (void)aSocketOut->CloseWithStatus(NS_ERROR_ABORT);
    return;
  }
  nsCOMPtr<nsITLSSocketControl> tls;
  nsCOMPtr<nsITransportSecurityInfo> securityInfo;
  nsAutoCString alpn;
  if (aProtocol == ProxyProtocol::H2) {
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
      Reject(Socks5Parser::Event::ProtocolError);
      return;
    }
  }

  mPendingTunnelIn = aSocketIn;
  mPendingTunnelOut = aSocketOut;
  mTransportReady = true;
  MaybeStartTunnel();
}

void SocksConnection::ApplyConnectMetadata(
    uint64_t aGeneration, ProxyProtocol aProtocol, nsresult aStatus,
    bool aConnectCodeKnown, int32_t aConnectCode,
    const Maybe<bool>& aPaddingHeaderPresent,
    const nsACString& aOuterProtocol) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mMetadataReady) {
    return;
  }
  mMetadataReady = true;
  mMetadataStatus = aStatus;
  mConnectCodeKnown = aConnectCodeKnown;
  mConnectCode = aConnectCode;
  mPaddingHeaderPresent = aPaddingHeaderPresent;
  mOuterProtocol = aOuterProtocol;
  MaybeStartTunnel();
}

void SocksConnection::ApplyChannelStop(uint64_t aGeneration,
                                       ProxyProtocol aProtocol,
                                       nsresult aStatus) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mChannelStopped) {
    return;
  }
  mChannelStopped = true;
  mChannelStatus = aStatus;
  MaybeStartTunnel();
}

void SocksConnection::ApplyUpgradeFailure(uint64_t aGeneration,
                                          ProxyProtocol aProtocol,
                                          nsresult aStatus) {
  if (!IsCurrentAttempt(aGeneration, aProtocol)) {
    return;
  }
  mUpgradeFailed = true;
  MaybeStartTunnel();
}

void SocksConnection::ApplyEstablishmentTimeout(uint64_t aGeneration,
                                                ProxyProtocol aProtocol) {
  if (!IsCurrentAttempt(aGeneration, aProtocol)) {
    return;
  }
  mEstablishmentTimedOut = true;
  MaybeStartTunnel();
}

void SocksConnection::ApplyOpenFailure(uint64_t aGeneration,
                                       ProxyProtocol aProtocol) {
  if (!IsCurrentAttempt(aGeneration, aProtocol) || mClosed) {
    return;
  }
  // A synchronous failure is a local configuration/API failure, not an outer
  // H3 establishment result. Auto mode must not hide it with an H2 retry.
  Reject(Socks5Parser::Event::ProtocolError);
}

bool SocksConnection::IsCurrentAttempt(uint64_t aGeneration,
                                       ProxyProtocol aProtocol) const {
  return aGeneration == mAttemptGeneration && aProtocol == mAttemptProtocol;
}

void SocksConnection::ResetAttemptState() {
  if (mPendingTunnelIn) {
    (void)mPendingTunnelIn->CloseWithStatus(NS_ERROR_ABORT);
  }
  if (mPendingTunnelOut) {
    (void)mPendingTunnelOut->CloseWithStatus(NS_ERROR_ABORT);
  }
  mTransportReady = false;
  mMetadataReady = false;
  mMetadataStatus = NS_ERROR_NOT_INITIALIZED;
  mChannelStopped = false;
  mChannelStatus = NS_ERROR_NOT_INITIALIZED;
  mConnectCodeKnown = false;
  mConnectCode = -1;
  mPaddingHeaderPresent.reset();
  mOuterProtocol.Truncate();
  mPaddingEnabled = false;
  mPendingTunnelIn = nullptr;
  mPendingTunnelOut = nullptr;
  mUpgradeFailed = false;
  mEstablishmentTimedOut = false;
}

void SocksConnection::MaybeStartTunnel() {
  if (!mChannelStopped || (!mMetadataReady && !mEstablishmentTimedOut) ||
      mTunnelReady || mClosed) {
    return;
  }

  const AutoFallbackState fallbackState{
      mProtocol,
      mAttemptProtocol,
      mFallbackUsed,
      mClosed,
      mChannelStopped,
      NS_FAILED(mChannelStatus),
      mEstablishmentTimedOut,
      mConnectCodeKnown,
      mConnectCode,
      mTransportReady,
  };
  if (ShouldRetryH2FromH3(fallbackState)) {
    mFallbackUsed = true;
    if (NS_FAILED(StartAttempt(ProxyProtocol::H2))) {
      Reject(Socks5Parser::Event::ProtocolError);
    }
    return;
  }

  if (!mMetadataReady) {
    Reject(Socks5Parser::Event::ProtocolError);
    return;
  }

  const bool protocolMatches = (mAttemptProtocol == ProxyProtocol::H2 &&
                                mOuterProtocol.EqualsLiteral("h2")) ||
                               (mAttemptProtocol == ProxyProtocol::H3 &&
                                mOuterProtocol.EqualsLiteral("h3"));
  if (NS_FAILED(mMetadataStatus) || NS_FAILED(mChannelStatus) ||
      !mConnectCodeKnown || !protocolMatches ||
      NS_FAILED(NegotiatePayloadPadding(mConnectCode, mPaddingHeaderPresent,
                                        mPaddingEnabled))) {
    if (mPendingTunnelIn) {
      (void)mPendingTunnelIn->CloseWithStatus(NS_ERROR_FAILURE);
    }
    if (mPendingTunnelOut) {
      (void)mPendingTunnelOut->CloseWithStatus(NS_ERROR_FAILURE);
    }
    mPendingTunnelIn = nullptr;
    mPendingTunnelOut = nullptr;
    Reject(Socks5Parser::Event::ProtocolError);
    return;
  }
  if (!mTransportReady) {
    if (mUpgradeFailed) {
      Reject(Socks5Parser::Event::ProtocolError);
    }
    return;
  }
  nsCOMPtr<nsIAsyncInputStream> tunnelIn = std::move(mPendingTunnelIn);
  nsCOMPtr<nsIAsyncOutputStream> tunnelOut = std::move(mPendingTunnelOut);
  TunnelReady(tunnelIn, tunnelOut);
}

void SocksConnection::TunnelReady(nsIAsyncInputStream* aTunnelIn,
                                  nsIAsyncOutputStream* aTunnelOut) {
  if (mClosed || mFailureQueued) {
    (void)aTunnelIn->CloseWithStatus(NS_ERROR_FAILURE);
    (void)aTunnelOut->CloseWithStatus(NS_ERROR_FAILURE);
    Reject(Socks5Parser::Event::ProtocolError);
    return;
  }
  mTunnelReady = true;
  std::printf("Outer protocol: %s\n", mOuterProtocol.get());
  std::printf("Padding negotiated: %s\n", mPaddingEnabled ? "yes" : "no");
  std::fflush(stdout);
  RefPtr self = this;
  mPump = new DuplexPump(mLocalIn, mLocalOut, aTunnelIn, aTunnelOut,
                         mPaddingEnabled,
                         [self](nsresult aStatus) { self->Close(aStatus); });
  nsTArray<uint8_t> reply;
  Socks5Parser::MakeReply(0x00, reply);
  nsresult rv = QueueReply(Span(reply), false);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

void SocksConnection::Close(nsresult aStatus) {
  if (mClosed) {
    return;
  }
  mClosed = true;
  (void)mLocalIn->CloseWithStatus(aStatus);
  (void)mLocalOut->CloseWithStatus(aStatus);
  if (mPendingTunnelIn) {
    (void)mPendingTunnelIn->CloseWithStatus(aStatus);
    mPendingTunnelIn = nullptr;
  }
  if (mPendingTunnelOut) {
    (void)mPendingTunnelOut->CloseWithStatus(aStatus);
    mPendingTunnelOut = nullptr;
  }
  if (mPump) {
    mPump->Close(aStatus);
  }
  if (mOnClose) {
    auto onClose = std::move(mOnClose);
    onClose();
  }
}

class SocksServer final : public nsIServerSocketListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSISERVERSOCKETLISTENER

  SocksServer(const nsACString& aProxyUrl, const nsACString& aProxyUser,
              const nsACString& aProxyPassword, uint32_t aMaxConnections,
              ProxyProtocol aProtocol, nsIEventTarget* aSocketTarget)
      : mProxyUrl(aProxyUrl),
        mProxyUser(aProxyUser),
        mProxyPassword(aProxyPassword),
        mMaxConnections(aMaxConnections),
        mProtocol(aProtocol),
        mSocketTarget(aSocketTarget) {}

  bool Complete() const { return mComplete; }

  void ConnectionClosed() {
    ++mCompletedConnections;
    if (mMaxConnections && mAcceptedConnections == mMaxConnections &&
        mCompletedConnections == mAcceptedConnections) {
      mComplete = true;
    }
  }

 private:
  ~SocksServer() = default;

  nsCString mProxyUrl;
  nsCString mProxyUser;
  nsCString mProxyPassword;
  uint32_t mMaxConnections;
  ProxyProtocol mProtocol;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  Atomic<uint32_t, Relaxed> mAcceptedConnections{0};
  Atomic<uint32_t, Relaxed> mCompletedConnections{0};
  Atomic<bool, Relaxed> mComplete{false};
};

NS_IMPL_ISUPPORTS(SocksServer, nsIServerSocketListener)

NS_IMETHODIMP SocksServer::OnSocketAccepted(nsIServerSocket* aServer,
                                            nsISocketTransport* aTransport) {
  nsCOMPtr<nsIInputStream> rawIn;
  nsCOMPtr<nsIOutputStream> rawOut;
  nsresult rv = aTransport->OpenInputStream(nsITransport::OPEN_UNBUFFERED, 0, 0,
                                            getter_AddRefs(rawIn));
  if (NS_SUCCEEDED(rv)) {
    rv = aTransport->OpenOutputStream(nsITransport::OPEN_UNBUFFERED, 0, 0,
                                      getter_AddRefs(rawOut));
  }
  nsCOMPtr<nsIAsyncInputStream> localIn = do_QueryInterface(rawIn);
  nsCOMPtr<nsIAsyncOutputStream> localOut = do_QueryInterface(rawOut);
  if (NS_FAILED(rv) || !localIn || !localOut) {
    (void)aTransport->Close(NS_FAILED(rv) ? rv : NS_ERROR_FAILURE);
    return NS_OK;
  }
  ++mAcceptedConnections;
  RefPtr self = this;
  RefPtr connection = new SocksConnection(
      localIn, localOut, mProxyUrl, mProxyUser, mProxyPassword, mProtocol,
      mSocketTarget, [self]() {
        (void)NS_DispatchToMainThread(
            NS_NewRunnableFunction("NaiveFox::SocksConnectionClosed",
                                   [self]() { self->ConnectionClosed(); }));
      });
  if (mMaxConnections && mAcceptedConnections == mMaxConnections) {
    (void)aServer->Close();
  }
  rv = connection->Start();
  if (NS_FAILED(rv)) {
    (void)aTransport->Close(rv);
  }
  return NS_OK;
}

NS_IMETHODIMP SocksServer::OnStopListening(nsIServerSocket* aServer,
                                           nsresult aStatus) {
  return NS_OK;
}

}  // namespace

nsresult RunSocksServer(uint16_t aListenPort, const nsACString& aProxyUrl,
                        const nsACString& aProxyUser,
                        const nsACString& aProxyPassword,
                        uint32_t aMaxConnections, ProxyProtocol aProtocol) {
  nsCOMPtr<nsIServerSocket> server =
      do_CreateInstance("@mozilla.org/network/server-socket;1");
  if (!server) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIEventTarget> socketTarget =
      do_GetService(NS_SOCKETTRANSPORTSERVICE_CONTRACTID);
  if (!socketTarget) {
    return NS_ERROR_FAILURE;
  }
  MOZ_TRY(server->Init(aListenPort, true, -1));
  RefPtr<SocksServer> listener =
      new SocksServer(aProxyUrl, aProxyUser, aProxyPassword, aMaxConnections,
                      aProtocol, socketTarget);
  MOZ_TRY(server->AsyncListen(listener));
  int32_t port = 0;
  MOZ_TRY(server->GetPort(&port));
  std::printf("SOCKS5 listening on 127.0.0.1:%d\n", port);
  std::fflush(stdout);
  while ((!aMaxConnections || !listener->Complete()) &&
         NS_ProcessNextEvent(nullptr, true)) {
  }
  return NS_OK;
}

}  // namespace mozilla::naivefox
