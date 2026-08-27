/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "TunnelSession.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <utility>

#include "AutoFallback.h"
#include "HeaderPadding.h"
#include "NeckoTunnel.h"
#include "OuterSessionGate.h"
#include "PaddingNegotiation.h"
#include "RuntimeLogging.h"
#include "codec/NaivePadding.h"
#include "mozilla/Assertions.h"
#include "mozilla/Maybe.h"
#include "mozilla/StaticPrefs_dom.h"
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
constexpr uint32_t kPreambleTimeoutMs = 1500;
constexpr uint32_t kPreambleDrainTimeoutMs = 1500;

std::atomic<uint64_t> gNextConnectionId{1};

const char* ProtocolName(ProxyProtocol aProtocol) {
  switch (aProtocol) {
    case ProxyProtocol::H2:
      return "h2";
    case ProxyProtocol::H3:
      return "h3";
    case ProxyProtocol::Auto:
      return "auto";
  }
  return "unknown";
}

void AppendGateKeyComponent(nsCString& aKey, const nsACString& aValue) {
  aKey.AppendInt(aValue.Length());
  aKey.Append(':');
  aKey.Append(aValue);
  aKey.Append('|');
}

nsCString MakeOuterGateKey(const TunnelConfig& aConfig,
                           ProxyProtocol aProtocol) {
  nsCString key;
  AppendGateKeyComponent(key, aConfig.mProxyUrl);
  key.AppendInt(static_cast<uint32_t>(aProtocol));
  key.Append('|');
  if (aConfig.mHostResolverRule) {
    key.AppendLiteral("resolver|");
    AppendGateKeyComponent(key, aConfig.mHostResolverRule->mLogicalHost);
    AppendGateKeyComponent(key, aConfig.mHostResolverRule->mPhysicalHost);
  } else {
    key.AppendLiteral("no-resolver|");
  }
  // Proxy credentials are a per-request Proxy-Authorization header and extra
  // CONNECT headers are not part of nsHttpConnectionInfo's wildcard pool
  // hash. Do not split the startup gate on either (or retain their values).
  // Both request kinds use the system principal and this explicit CIK.
  key.AppendLiteral("system-principal|naivefox-raw-tunnel");
  return key;
}

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
             bool aPaddingEnabled,
             std::function<void()>&& aOnUpstreamApplicationActive,
             std::function<void()>&& aOnDownstreamApplicationActive,
             std::function<void(nsresult)>&& aOnClose)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mTunnelIn(aTunnelIn),
        mTunnelOut(aTunnelOut),
        mPaddingEnabled(aPaddingEnabled),
        mOnUpstreamApplicationActive(std::move(aOnUpstreamApplicationActive)),
        mOnDownstreamApplicationActive(
            std::move(aOnDownstreamApplicationActive)),
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

  void UpstreamApplicationActive(PumpDirection* aDirection) {
    if (mClosed || aDirection != mUp.get() || !mOnUpstreamApplicationActive) {
      return;
    }
    auto onUpstreamApplicationActive = std::move(mOnUpstreamApplicationActive);
    onUpstreamApplicationActive();
  }

  void DownstreamApplicationActive(PumpDirection* aDirection) {
    if (mClosed || aDirection != mDown.get() ||
        !mOnDownstreamApplicationActive) {
      return;
    }
    auto onDownstreamApplicationActive =
        std::move(mOnDownstreamApplicationActive);
    onDownstreamApplicationActive();
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
  std::function<void()> mOnUpstreamApplicationActive;
  std::function<void()> mOnDownstreamApplicationActive;
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
    mOwner->UpstreamApplicationActive(this);
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
        mOwner->UpstreamApplicationActive(this);
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
  mOwner->DownstreamApplicationActive(this);
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
  enum class ActiveRequestKind : uint8_t { None, Tunnel };

  Impl(nsIAsyncInputStream* aLocalIn, nsIAsyncOutputStream* aLocalOut,
       const TunnelConfig& aConfig, bool aConnectUrgentStart,
       nsIEventTarget* aSocketTarget, EstablishedCallback&& aOnEstablished,
       FailureCallback&& aOnFailure, ClosedCallback&& aOnClosed)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mConfig(aConfig),
        mConnectUrgentStart(aConnectUrgentStart),
        mSocketTarget(aSocketTarget),
        mConnectionId(
            gNextConnectionId.fetch_add(1, std::memory_order_relaxed)),
        mOnEstablished(std::move(aOnEstablished)),
        mOnFailure(std::move(aOnFailure)),
        mOnClosed(std::move(aOnClosed)) {}

  nsCOMPtr<nsIAsyncInputStream> mLocalIn;
  nsCOMPtr<nsIAsyncOutputStream> mLocalOut;
  TunnelConfig mConfig;
  const bool mConnectUrgentStart;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  const uint64_t mConnectionId;
  EstablishedCallback mOnEstablished;
  FailureCallback mOnFailure;
  ClosedCallback mOnClosed;
  nsCString mTargetAuthority;
  nsTArray<uint8_t> mInitialPayload;
  RefPtr<DuplexPump> mPump;
  ProxyProtocol mAttemptProtocol = ProxyProtocol::H2;
  uint64_t mAttemptGeneration = 0;
  bool mFallbackUsed = false;
  bool mConnectUrgentStartLogged = false;
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
  bool mPreambleFailureDispatched = false;
  std::atomic<bool> mCancelRequested{false};
  std::atomic<bool> mOuterGateRegistered{false};
  std::atomic<bool> mOuterGateReleaseRequested{false};
  nsCString mOuterGateKey;
  nsCOMPtr<nsIRequest> mActiveRequest;
  uint64_t mActiveRequestGeneration = 0;
  ActiveRequestKind mActiveRequestKind = ActiveRequestKind::None;
  nsCOMPtr<nsITimer> mPreambleTimer;
  nsCOMPtr<nsITimer> mPreambleDrainTimer;
  RefPtr<ProxyPreambleOperation> mPreambleOperation;
  uint64_t mPreambleOperationGeneration = 0;
  nsCString mPreambleTargetAuthority;
  detail::PreambleSequenceState mPreambleSequence;
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

TunnelSession::TunnelSession(
    nsIAsyncInputStream* aLocalIn, nsIAsyncOutputStream* aLocalOut,
    const TunnelConfig& aConfig, bool aConnectUrgentStart,
    nsIEventTarget* aSocketTarget, EstablishedCallback&& aOnEstablished,
    FailureCallback&& aOnFailure, ClosedCallback&& aOnClosed)
    : mImpl(MakeUnique<Impl>(aLocalIn, aLocalOut, aConfig, aConnectUrgentStart,
                             aSocketTarget, std::move(aOnEstablished),
                             std::move(aOnFailure), std::move(aOnClosed))) {}

TunnelSession::~TunnelSession() {
  CancelInternal(NS_BASE_STREAM_CLOSED, false);
}

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
  RuntimeLogEvent("Connection %llu target=%s protocol=%s\n",
                  static_cast<unsigned long long>(mImpl->mConnectionId),
                  mImpl->mTargetAuthority.get(), ProtocolName(firstProtocol));
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
  if (mImpl->mCancelRequested.load(std::memory_order_acquire)) {
    return;
  }
  if (mImpl->mPreambleOperation &&
      mImpl->mPreambleOperationGeneration != aGeneration) {
    if (mImpl->mPreambleDrainTimer) {
      (void)mImpl->mPreambleDrainTimer->Cancel();
      mImpl->mPreambleDrainTimer = nullptr;
    }
    RefPtr operation = std::move(mImpl->mPreambleOperation);
    mImpl->mPreambleOperationGeneration = 0;
    mImpl->mPreambleTargetAuthority.Truncate();
    operation->Cancel(NS_ERROR_ABORT);
  }
  // Without the optional gate every tunnel is its own experiment arm and runs
  // the configured preamble. With it, only the cold-route leader does so;
  // queued and already-warm participants proceed directly to CONNECT.
  bool coldLeader = true;
  if (ShouldGateOuterSession(mImpl->mConfig)) {
    if (!mImpl->mOuterGateRegistered.load(std::memory_order_acquire)) {
      mImpl->mOuterGateKey = MakeOuterGateKey(mImpl->mConfig, aProtocol);
      mImpl->mOuterGateRegistered.store(true, std::memory_order_release);
    }
    RefPtr self = this;
    nsCString authority(aTargetAuthority);
    const auto admission = OuterSessionGate::Get().Enter(
        mImpl->mOuterGateKey, mImpl->mConnectionId,
        [self, aGeneration, aProtocol, authority = std::move(authority)]() {
          self->OpenAttemptOnMain(aGeneration, aProtocol, authority);
        });
    if (admission == OuterSessionGate::Admission::Queued) {
      RuntimeLogEvent("Connection %llu queued behind outer session gate\n",
                      static_cast<unsigned long long>(mImpl->mConnectionId));
      return;
    }
    coldLeader = admission == OuterSessionGate::Admission::Leader;
  }

  const PreambleMode preambleMode =
      mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol);
  if (detail::ShouldRunPreamble(preambleMode, coldLeader)) {
    BeginPreambleOnMain(aGeneration, aProtocol, aTargetAuthority);
    return;
  }

  OpenConnectOnMain(aGeneration, aProtocol, aTargetAuthority);
}

void TunnelSession::BeginPreambleOnMain(uint64_t aGeneration,
                                        ProxyProtocol aProtocol,
                                        const nsACString& aTargetAuthority) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelRequested.load(std::memory_order_acquire)) {
    return;
  }
  if (!mImpl->mPreambleSequence.Begin(aGeneration, aProtocol)) {
    if (!mImpl->mPreambleSequence.IsInFlight(aGeneration, aProtocol)) {
      OpenConnectOnMain(aGeneration, aProtocol, aTargetAuthority);
    }
    return;
  }

  RefPtr self = this;
  nsCString authority(aTargetAuthority);
  RefPtr<ProxyPreambleOperation> operation;
  PreambleConfig preamble = mImpl->mConfig.mPreamble;
  preamble.mMode = preamble.ModeForProtocol(aProtocol);
  if (preamble.mMode == PreambleMode::DocumentComplete ||
      preamble.mMode == PreambleMode::DocumentCarrierDispatch ||
      preamble.mMode == PreambleMode::DocumentColdWinnerHandoff ||
      preamble.mMode == PreambleMode::DocumentNativeCacheOpen ||
      preamble.mMode == PreambleMode::DocumentHandshakeConfirmed ||
      preamble.mMode == PreambleMode::DocumentOverlap ||
      preamble.mMode == PreambleMode::DocumentStartOverlap) {
    preamble.mMaxAssets = 0;
  }
  nsresult rv = OpenProxyPreambleOperation(
      mImpl->mConfig.mProxyUrl, mImpl->mConfig.mProxyUser,
      mImpl->mConfig.mProxyPassword, preamble, aProtocol,
      [self, aGeneration, aProtocol,
       authority = std::move(authority)](ProxyPreambleResult aResult) {
        self->FinishPreambleOnMain(
            aGeneration, aProtocol, authority, aResult.mStatus,
            aResult.mHttpStatus, aResult.mBodyBytes, aResult.mStartedResources,
            aResult.mCommittedResources, aResult.mNativeCacheNewResources,
            aResult.mRootDone, aResult.mTerminalFallback);
      },
      [self, aGeneration, aProtocol](ProxyPreambleFinalResult aFinalResult) {
        self->FinishPreambleOperationOnMain(
            aGeneration, aProtocol, aFinalResult.mStatus,
            aFinalResult.mHttpStatus, aFinalResult.mBodyBytes,
            aFinalResult.mRootDone, aFinalResult.mCompletedNormally,
            aFinalResult.mCompletedSuccessfulResources,
            aFinalResult.mNativeCacheNewResources,
            aFinalResult.mNavigationStopStyleCommitted,
            aFinalResult.mNavigationStopStyleResponseStarted,
            aFinalResult.mNavigationStopStyleAborted);
      },
      mImpl->mConfig.mHostResolverRule, mImpl->mConnectionId, operation);
  if (NS_FAILED(rv)) {
    MOZ_ALWAYS_TRUE(mImpl->mPreambleSequence.Complete(aGeneration, aProtocol));
    RuntimeLogEvent(
        "Connection %llu preamble result=open-error status=0x%08x http=0 "
        "bytes=0 protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        static_cast<unsigned>(rv), ProtocolName(aProtocol));
    if (PreambleModeRequiresFailClosed(preamble.mMode)) {
      FailPreambleOnMain(rv);
      return;
    }
    // Non-product diagnostic preamble failures never enter AutoFallback.
    // Continue exactly once with CONNECT on the same generation and protocol.
    OpenConnectOnMain(aGeneration, aProtocol, aTargetAuthority);
    return;
  }

  mImpl->mPreambleOperation = std::move(operation);
  mImpl->mPreambleOperationGeneration = aGeneration;
  mImpl->mPreambleTargetAuthority = aTargetAuthority;
  auto timer = NS_NewTimerWithCallback(
      [self, aGeneration, aProtocol](nsITimer*) {
        self->PreambleTimeoutOnMain(aGeneration, aProtocol);
      },
      kPreambleTimeoutMs, nsITimer::TYPE_ONE_SHOT,
      "NaiveFox::ProxyPreambleTimeout"_ns);
  if (timer.isErr()) {
    rv = timer.unwrapErr();
    MOZ_ALWAYS_TRUE(mImpl->mPreambleSequence.Complete(aGeneration, aProtocol));
    RefPtr operation = std::move(mImpl->mPreambleOperation);
    mImpl->mPreambleOperationGeneration = 0;
    mImpl->mPreambleTargetAuthority.Truncate();
    operation->Cancel(rv);
    RuntimeLogEvent(
        "Connection %llu preamble result=timer-error status=0x%08x http=0 "
        "bytes=0 protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        static_cast<unsigned>(rv), ProtocolName(aProtocol));
    if (PreambleModeRequiresFailClosed(preamble.mMode)) {
      FailPreambleOnMain(rv);
      return;
    }
    OpenConnectOnMain(aGeneration, aProtocol, aTargetAuthority);
    return;
  }
  mImpl->mPreambleTimer = timer.unwrap();
  if (mImpl->mCancelRequested.load(std::memory_order_acquire)) {
    CancelRequestOnMain(NS_ERROR_ABORT);
  }
}

void TunnelSession::FinishPreambleOnMain(
    uint64_t aGeneration, ProxyProtocol aProtocol,
    const nsACString& aTargetAuthority, nsresult aStatus, uint32_t aHttpStatus,
    uint32_t aBodyBytes, uint32_t aStartedResources,
    uint32_t aCommittedResources, uint32_t aNativeCacheNewResources,
    bool aRootDone, bool aTerminalFallback) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mImpl->mPreambleSequence.IsInFlight(aGeneration, aProtocol) ||
      !mImpl->mPreambleOperation ||
      mImpl->mPreambleOperationGeneration != aGeneration) {
    return;
  }
  if (mImpl->mPreambleTimer) {
    (void)mImpl->mPreambleTimer->Cancel();
    mImpl->mPreambleTimer = nullptr;
  }
  MOZ_ALWAYS_TRUE(mImpl->mPreambleSequence.Complete(aGeneration, aProtocol));
  const PreambleMode preambleMode =
      mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol);
  const bool supportsDocumentStartAdmission =
      aProtocol == ProxyProtocol::H3 ||
      (aProtocol == ProxyProtocol::H2 &&
       (preambleMode == PreambleMode::TreeNativeParserDocumentStartOverlap ||
        preambleMode ==
            PreambleMode::TreeNativeParserDocumentStartResourceTree ||
        preambleMode ==
            PreambleMode::TreeNativeParserDocumentStartNavigationStop));
  const bool requestCommittedAdmission =
      PreambleModeUsesNativeParserDocumentStart(preambleMode) &&
      supportsDocumentStartAdmission && NS_SUCCEEDED(aStatus) &&
      aHttpStatus == 0 && aBodyBytes == 0 && aStartedResources == 0 &&
      aCommittedResources == 0 && aNativeCacheNewResources == 0 && !aRootDone &&
      !aTerminalFallback;
  const bool succeeded =
      NS_SUCCEEDED(aStatus) && aHttpStatus >= 200 && aHttpStatus < 300;
  if ((PreambleModeUsesNativeParserDocumentStart(preambleMode) &&
       !requestCommittedAdmission) ||
      (!PreambleModeUsesNativeParserDocumentStart(preambleMode) &&
       PreambleModeRequiresFailClosed(preambleMode) && !succeeded)) {
    FailPreambleOnMain(NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE);
    return;
  }
  if (detail::PreambleOverlapsConnect(preambleMode) &&
      mImpl->mPreambleOperation) {
    RefPtr self = this;
    auto timer = NS_NewTimerWithCallback(
        [self, aGeneration](nsITimer*) {
          self->PreambleDrainTimeoutOnMain(aGeneration);
        },
        kPreambleDrainTimeoutMs, nsITimer::TYPE_ONE_SHOT,
        "NaiveFox::ProxyPreambleDrainTimeout"_ns);
    if (timer.isOk()) {
      mImpl->mPreambleDrainTimer = timer.unwrap();
    } else {
      const nsresult timerStatus = timer.unwrapErr();
      RefPtr operation = std::move(mImpl->mPreambleOperation);
      mImpl->mPreambleOperationGeneration = 0;
      mImpl->mPreambleTargetAuthority.Truncate();
      operation->Cancel(timerStatus);
      if (PreambleModeRequiresFailClosed(preambleMode)) {
        FailPreambleOnMain(timerStatus);
        return;
      }
    }
  }
  if (preambleMode == PreambleMode::TreeRootOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble root-overlap admission=%s root_done=%d "
        "started_resources=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aRootDone && aStartedResources > 0 ? "started-resources"
                                           : "terminal-fallback",
        aRootDone, aStartedResources, ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::TreeResourceCommittedOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble resource-committed-overlap admission=%s "
        "root_done=%d started_resources=%u committed_resources=%u "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aTerminalFallback ? "terminal-fallback" : "request-committed",
        aRootDone, aStartedResources, aCommittedResources,
        ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::TreeResourceNativeCacheCommittedOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble resource-native-cache-committed-overlap "
        "admission=%s root_done=%d started_resources=%u "
        "committed_resources=%u cache_new=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aTerminalFallback ? "terminal-fallback" : "request-committed",
        aRootDone, aStartedResources, aCommittedResources,
        aNativeCacheNewResources, ProtocolName(aProtocol));
  }
  if (PreambleModeUsesNativeParser(preambleMode) && succeeded && aRootDone &&
      !aTerminalFallback && aStartedResources == 1 &&
      aCommittedResources == 1 &&
      (aProtocol == ProxyProtocol::H3 ||
       preambleMode == PreambleMode::TreeNativeParserDocumentStartOverlap)) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-preload "
        "parser=html5-speculative-scanner parsers=1 descriptors=1 "
        "provenance=FromParser internal_type=40 protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-preload "
        "channel=async-open channels=1 protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-preload "
        "admission=request-committed root_done=%d started_resources=%u "
        "committed_resources=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), aRootDone,
        aStartedResources, aCommittedResources, ProtocolName(aProtocol));
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-preload barrier=released "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::DocumentOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble document-overlap admission=%s "
        "response_accepted=%d root_done=%d protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aRootDone ? "terminal-fallback" : "response-headers", !aRootDone,
        aRootDone, ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::DocumentStartOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble document-start-overlap admission=%s "
        "request_committed=%d root_done=%d protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aRootDone ? "terminal-fallback" : "request-committed", !aRootDone,
        aRootDone, ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::TreeNativeParserDocumentStartOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-document-start "
        "admission=request-committed request_committed=1 root_done=0 "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::TreeNativeParserDocumentStartResourceTree) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-resource-tree "
        "admission=request-committed request_committed=1 root_done=0 "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
  }
  if (PreambleModeUsesScopedNavigationStop(preambleMode)) {
    RuntimeLogEvent(
        "Connection %llu preamble "
        "%s "
        "admission=request-committed request_committed=1 root_done=0 "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        preambleMode == PreambleMode::TreeNativeParserDocumentStartResponseStop
            ? "native-parser-document-start-response-stop"
            : "native-parser-document-start-navigation-stop",
        ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::DocumentNativeCacheOpen && succeeded) {
    RuntimeLogEvent(
        "Connection %llu preamble native-cache-open cache=readonly-miss "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
  }
  if (preambleMode == PreambleMode::DocumentColdWinnerHandoff && succeeded) {
    RuntimeLogEvent(
        "Connection %llu preamble cold-winner-handoff "
        "establishment=requestless-single-proxy dispatch=exact-winner "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        ProtocolName(aProtocol));
  }
  if (preambleMode != PreambleMode::DocumentStartOverlap &&
      !PreambleModeUsesNativeParserDocumentStart(preambleMode)) {
    const char* result = aStatus == NS_ERROR_FILE_TOO_BIG ? "oversize"
                         : succeeded                      ? "success"
                         : NS_FAILED(aStatus)             ? "network-error"
                                                          : "http-error";
    RuntimeLogEvent(
        "Connection %llu preamble result=%s status=0x%08x http=%u bytes=%u "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), result,
        static_cast<unsigned>(aStatus), aHttpStatus, aBodyBytes,
        ProtocolName(aProtocol));
  }
  if (!mImpl->mCancelRequested.load(std::memory_order_acquire)) {
    OpenConnectOnMain(aGeneration, aProtocol, aTargetAuthority);
  }
}

void TunnelSession::FinishPreambleOperationOnMain(
    uint64_t aGeneration, ProxyProtocol aProtocol, nsresult aStatus,
    uint32_t aHttpStatus, uint32_t aBodyBytes, bool aRootDone,
    bool aCompletedNormally, uint32_t aCompletedSuccessfulResources,
    uint32_t aNativeCacheNewResources, bool aNavigationStopStyleCommitted,
    bool aNavigationStopStyleResponseStarted,
    bool aNavigationStopStyleAborted) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mPreambleOperationGeneration != aGeneration) {
    return;
  }
  if (mImpl->mPreambleDrainTimer) {
    (void)mImpl->mPreambleDrainTimer->Cancel();
    mImpl->mPreambleDrainTimer = nullptr;
  }
  const PreambleMode preambleMode =
      mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol);
  const bool finalSucceeded = aRootDone && aCompletedNormally &&
                              NS_SUCCEEDED(aStatus) && aHttpStatus >= 200 &&
                              aHttpStatus < 300;
  const bool documentStartParserSucceeded =
      finalSucceeded && aCompletedSuccessfulResources == 1;
  const bool resourceTreeSucceeded =
      finalSucceeded && aCompletedSuccessfulResources == 3;
  const bool navigationStopSucceeded =
      detail::PreambleNavigationStopCompletedSuccessfully(
          preambleMode, aRootDone, aCompletedNormally, aStatus, aHttpStatus,
          aCompletedSuccessfulResources, aNavigationStopStyleCommitted,
          aNavigationStopStyleResponseStarted, aNavigationStopStyleAborted);
  if (PreambleModeRequiresFailClosed(preambleMode) &&
      ((preambleMode == PreambleMode::TreeNativeParserDocumentStartOverlap &&
        !documentStartParserSucceeded) ||
       (preambleMode ==
            PreambleMode::TreeNativeParserDocumentStartResourceTree &&
        !resourceTreeSucceeded) ||
       (PreambleModeUsesScopedNavigationStop(preambleMode) &&
        !navigationStopSucceeded) ||
       (!PreambleModeUsesNativeParserDocumentStart(preambleMode) &&
        (!aCompletedNormally || NS_FAILED(aStatus))))) {
    mImpl->mPreambleOperation = nullptr;
    mImpl->mPreambleOperationGeneration = 0;
    mImpl->mPreambleTargetAuthority.Truncate();
    FailPreambleOnMain(NS_FAILED(aStatus) ? aStatus : NS_ERROR_FAILURE);
    return;
  }
  if (preambleMode == PreambleMode::DocumentStartOverlap ||
      PreambleModeUsesNativeParserDocumentStart(preambleMode)) {
    const bool succeeded =
        preambleMode == PreambleMode::TreeNativeParserDocumentStartOverlap
            ? documentStartParserSucceeded
        : preambleMode ==
                PreambleMode::TreeNativeParserDocumentStartResourceTree
            ? resourceTreeSucceeded
        : PreambleModeUsesScopedNavigationStop(preambleMode)
            ? navigationStopSucceeded
            : finalSucceeded;
    const char* result = aStatus == NS_ERROR_FILE_TOO_BIG ? "oversize"
                         : succeeded                      ? "success"
                         : NS_FAILED(aStatus)             ? "network-error"
                                                          : "http-error";
    RuntimeLogEvent(
        "Connection %llu preamble result=%s status=0x%08x http=%u bytes=%u "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), result,
        static_cast<unsigned>(aStatus), aHttpStatus, aBodyBytes,
        ProtocolName(aProtocol));
  }
  if (aCompletedNormally && preambleMode == PreambleMode::TreeRootOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble root-overlap drain=complete "
        "completed_resources=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aCompletedSuccessfulResources, ProtocolName(aProtocol));
  }
  if (aCompletedNormally &&
      preambleMode == PreambleMode::TreeResourceCommittedOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble resource-committed-overlap drain=complete "
        "completed_resources=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aCompletedSuccessfulResources, ProtocolName(aProtocol));
  }
  if (aCompletedNormally &&
      preambleMode == PreambleMode::TreeResourceNativeCacheCommittedOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble resource-native-cache-committed-overlap "
        "drain=complete completed_resources=%u cache_new=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aCompletedSuccessfulResources, aNativeCacheNewResources,
        ProtocolName(aProtocol));
  }
  if (aCompletedNormally && aRootDone && NS_SUCCEEDED(aStatus) &&
      aHttpStatus >= 200 && aHttpStatus < 300 &&
      aCompletedSuccessfulResources == 1 &&
      PreambleModeUsesNativeParser(preambleMode) &&
      (aProtocol == ProxyProtocol::H3 ||
       preambleMode == PreambleMode::TreeNativeParserDocumentStartOverlap)) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-preload "
        "drain=complete completed_resources=%u http=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aCompletedSuccessfulResources, aHttpStatus, ProtocolName(aProtocol));
  }
  if (resourceTreeSucceeded &&
      preambleMode == PreambleMode::TreeNativeParserDocumentStartResourceTree) {
    RuntimeLogEvent(
        "Connection %llu preamble native-parser-resource-tree "
        "drain=complete completed_resources=3 http=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), aHttpStatus,
        ProtocolName(aProtocol));
  }
  if (navigationStopSucceeded &&
      preambleMode ==
          PreambleMode::TreeNativeParserDocumentStartNavigationStop) {
    RuntimeLogEvent(
        "Connection %llu preamble "
        "native-parser-document-start-navigation-stop "
        "drain=complete root_done=1 css_committed=1 css_aborted=1 http=%u "
        "protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId), aHttpStatus,
        ProtocolName(aProtocol));
  }
  if (navigationStopSucceeded &&
      preambleMode == PreambleMode::TreeNativeParserDocumentStartResponseStop) {
    RuntimeLogEvent(
        "Connection %llu preamble "
        "native-parser-document-start-response-stop "
        "drain=complete root_done=1 css_committed=1 css_aborted=%d "
        "css_completed=%d http=%u protocol=h3\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aNavigationStopStyleAborted, aCompletedSuccessfulResources == 1,
        aHttpStatus);
  }
  if (aCompletedNormally && preambleMode == PreambleMode::DocumentOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble document-overlap drain=complete "
        "root_done=1 completed_resources=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aCompletedSuccessfulResources, ProtocolName(aProtocol));
  }
  if (aRootDone && aCompletedNormally &&
      preambleMode == PreambleMode::DocumentStartOverlap) {
    RuntimeLogEvent(
        "Connection %llu preamble document-start-overlap drain=complete "
        "root_done=1 completed_resources=%u protocol=%s\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        aCompletedSuccessfulResources, ProtocolName(aProtocol));
  }
  mImpl->mPreambleOperation = nullptr;
  mImpl->mPreambleOperationGeneration = 0;
  mImpl->mPreambleTargetAuthority.Truncate();
}

void TunnelSession::PreambleDrainTimeoutOnMain(uint64_t aGeneration) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mImpl->mPreambleOperation ||
      mImpl->mPreambleOperationGeneration != aGeneration) {
    return;
  }
  mImpl->mPreambleDrainTimer = nullptr;
  RefPtr operation = std::move(mImpl->mPreambleOperation);
  mImpl->mPreambleOperationGeneration = 0;
  mImpl->mPreambleTargetAuthority.Truncate();
  operation->Cancel(NS_ERROR_NET_TIMEOUT);
  RuntimeLogEvent("Connection %llu preamble background drain timed out\n",
                  static_cast<unsigned long long>(mImpl->mConnectionId));
  const PreambleMode mode = mImpl->mConfig.mPreamble.ModeForProtocol(
      mImpl->mAttemptProtocol == ProxyProtocol::Auto ? ProxyProtocol::H3
                                                     : mImpl->mAttemptProtocol);
  if (PreambleDrainTimeoutFailsTunnel(mode)) {
    FailPreambleOnMain(NS_ERROR_NET_TIMEOUT);
  }
}

void TunnelSession::PreambleTimeoutOnMain(uint64_t aGeneration,
                                          ProxyProtocol aProtocol) {
  MOZ_ASSERT(NS_IsMainThread());
  if (!mImpl->mPreambleSequence.IsInFlight(aGeneration, aProtocol) ||
      !mImpl->mPreambleOperation ||
      mImpl->mPreambleOperationGeneration != aGeneration) {
    return;
  }
  mImpl->mPreambleTimer = nullptr;
  MOZ_ALWAYS_TRUE(mImpl->mPreambleSequence.Complete(aGeneration, aProtocol));
  RefPtr operation = std::move(mImpl->mPreambleOperation);
  mImpl->mPreambleOperationGeneration = 0;
  nsCString authority(std::move(mImpl->mPreambleTargetAuthority));
  // ProxyPreambleOperation clears its callbacks before cancelling channels,
  // so no late root/resource stop can become a second CONNECT continuation.
  operation->Cancel(NS_ERROR_NET_TIMEOUT);
  RuntimeLogEvent(
      "Connection %llu preamble result=timeout status=0x%08x http=0 "
      "bytes=0 protocol=%s\n",
      static_cast<unsigned long long>(mImpl->mConnectionId),
      static_cast<unsigned>(NS_ERROR_NET_TIMEOUT), ProtocolName(aProtocol));
  if (PreambleModeRequiresFailClosed(
          mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol))) {
    FailPreambleOnMain(NS_ERROR_NET_TIMEOUT);
    return;
  }
  if (!mImpl->mCancelRequested.load(std::memory_order_acquire)) {
    OpenConnectOnMain(aGeneration, aProtocol, authority);
  }
}

void TunnelSession::TunnelApplicationActiveOnMain(uint64_t aGeneration,
                                                  ProxyProtocol aProtocol) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelRequested.load(std::memory_order_acquire) ||
      !mImpl->mPreambleOperation ||
      mImpl->mPreambleOperationGeneration != aGeneration ||
      mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol) !=
          PreambleMode::TreeNativeParserDocumentStartNavigationStop) {
    return;
  }
  nsresult rv = mImpl->mPreambleOperation->NotifyTunnelApplicationActive();
  if (NS_FAILED(rv)) {
    FailPreambleOnMain(rv);
  }
}

void TunnelSession::TunnelServerApplicationActiveOnMain(
    uint64_t aGeneration, ProxyProtocol aProtocol) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelRequested.load(std::memory_order_acquire) ||
      !mImpl->mPreambleOperation ||
      mImpl->mPreambleOperationGeneration != aGeneration ||
      mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol) !=
          PreambleMode::TreeNativeParserDocumentStartResponseStop) {
    return;
  }
  nsresult rv =
      mImpl->mPreambleOperation->NotifyTunnelServerApplicationActive();
  if (NS_FAILED(rv)) {
    FailPreambleOnMain(rv);
  }
}

void TunnelSession::OpenConnectOnMain(uint64_t aGeneration,
                                      ProxyProtocol aProtocol,
                                      const nsACString& aTargetAuthority) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mCancelRequested.load(std::memory_order_acquire) ||
      !mImpl->mPreambleSequence.TryStartConnect(aGeneration)) {
    return;
  }
  nsAutoCString padding;
  nsresult rv = GenerateHeaderPadding(padding);
  if (NS_SUCCEEDED(rv)) {
    RefPtr<TunnelAttempt> attempt =
        new TunnelAttempt(this, mImpl->mSocketTarget, aGeneration, aProtocol);
    nsCOMPtr<nsIRequest> openedRequest;
    const bool useAnonymousConnection = !PreambleModeUsesNativeParser(
        mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol));
    rv = OpenNeckoTunnel(
        mImpl->mConfig.mProxyUrl, aTargetAuthority, mImpl->mConfig.mProxyUser,
        mImpl->mConfig.mProxyPassword, attempt, attempt, padding, aProtocol,
        mImpl->mConfig.mHostResolverRule, mImpl->mConfig.mExtraHeaders,
        mImpl->mConnectUrgentStart, useAnonymousConnection,
        getter_AddRefs(openedRequest));
    if (NS_SUCCEEDED(rv)) {
      if (mImpl->mConnectUrgentStart && !mImpl->mConnectUrgentStartLogged) {
        mImpl->mConnectUrgentStartLogged = true;
        RuntimeLogEvent(
            "Connection %llu diagnostic-first-socks-tunnel-urgent-start "
            "applied=1 incremental=%d protocol=%s\n",
            static_cast<unsigned long long>(mImpl->mConnectionId),
            StaticPrefs::dom_document_priority_incremental(),
            ProtocolName(aProtocol));
      }
      mImpl->mActiveRequest = openedRequest;
      mImpl->mActiveRequestGeneration = aGeneration;
      mImpl->mActiveRequestKind = Impl::ActiveRequestKind::Tunnel;
      if (mImpl->mCancelRequested.load(std::memory_order_acquire)) {
        CancelRequestOnMain(NS_ERROR_ABORT);
      } else {
        if (mImpl->mPreambleOperation &&
            mImpl->mPreambleOperationGeneration == aGeneration &&
            PreambleModeUsesScopedNavigationStop(
                mImpl->mConfig.mPreamble.ModeForProtocol(aProtocol))) {
          rv = mImpl->mPreambleOperation->NotifyConnectHandoffAdmitted();
          if (NS_FAILED(rv)) {
            FailPreambleOnMain(rv);
            return;
          }
        }
        if (mImpl->mConfig.mProtocol == ProxyProtocol::Auto &&
            aProtocol == ProxyProtocol::H3) {
          rv = attempt->ArmEstablishmentTimeout(openedRequest);
        }
      }
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
  owner->ClearRequestOnMain(mGeneration, aRequest);
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

void TunnelSession::FailPreambleOnMain(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mPreambleFailureDispatched) {
    return;
  }
  mImpl->mPreambleFailureDispatched = true;
  mImpl->mCancelRequested.store(true, std::memory_order_release);
  CancelRequestOnMain(aStatus);
  RefPtr self = this;
  nsresult rv = mImpl->mSocketTarget->Dispatch(
      NS_NewRunnableFunction("NaiveFox::FailClosedPreamble",
                             [self, aStatus]() { self->Fail(aStatus); }),
      NS_DISPATCH_NORMAL);
  if (NS_FAILED(rv)) {
    RuntimeLogEvent(
        "Connection %llu failed to dispatch fail-closed preamble "
        "status=0x%08x dispatch=0x%08x\n",
        static_cast<unsigned long long>(mImpl->mConnectionId),
        static_cast<unsigned>(aStatus), static_cast<unsigned>(rv));
  }
}

void TunnelSession::CancelRequestOnMain(nsresult aStatus) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mPreambleTimer) {
    (void)mImpl->mPreambleTimer->Cancel();
    mImpl->mPreambleTimer = nullptr;
  }
  if (mImpl->mPreambleDrainTimer) {
    (void)mImpl->mPreambleDrainTimer->Cancel();
    mImpl->mPreambleDrainTimer = nullptr;
  }
  if (mImpl->mPreambleOperation) {
    mImpl->mPreambleSequence.Cancel();
    RefPtr operation = std::move(mImpl->mPreambleOperation);
    mImpl->mPreambleOperationGeneration = 0;
    mImpl->mPreambleTargetAuthority.Truncate();
    operation->Cancel(aStatus);
  }
  if (mImpl->mActiveRequest) {
    nsCOMPtr<nsIRequest> request = std::move(mImpl->mActiveRequest);
    mImpl->mActiveRequestGeneration = 0;
    mImpl->mActiveRequestKind = Impl::ActiveRequestKind::None;
    (void)request->Cancel(aStatus);
  }
}

void TunnelSession::ClearRequestOnMain(uint64_t aGeneration,
                                       nsIRequest* aRequest) {
  MOZ_ASSERT(NS_IsMainThread());
  if (mImpl->mActiveRequestGeneration == aGeneration &&
      mImpl->mActiveRequest == aRequest) {
    mImpl->mActiveRequest = nullptr;
    mImpl->mActiveRequestGeneration = 0;
    mImpl->mActiveRequestKind = Impl::ActiveRequestKind::None;
  }
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

bool TunnelSession::ShouldGateOuterSession(const TunnelConfig& aConfig) {
  // A queued Auto attempt captures H3. If its leader falls back and warms an
  // H2 route, releasing that stale H3 callback would recreate the startup
  // race. Keep Auto's existing per-session H3 -> H2 generation semantics
  // until the winning protocol can be propagated to queued attempts.
  const bool implicitProtocolGate = aConfig.mImplicitPreambleGate &&
                                    (aConfig.mProtocol == ProxyProtocol::H2 ||
                                     aConfig.mProtocol == ProxyProtocol::H3);
  const bool explicitGate =
      aConfig.mOuterSessionGate && aConfig.mProtocol != ProxyProtocol::Auto;
  return implicitProtocolGate || explicitGate;
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
  NotifyOuterGateReady();
  RuntimeLogEvent("Connection %llu established target=%s outer=%s padding=%s\n",
                  static_cast<unsigned long long>(mImpl->mConnectionId),
                  mImpl->mTargetAuthority.get(), mImpl->mOuterProtocol.get(),
                  mImpl->mPaddingEnabled ? "yes" : "no");
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
  std::function<void()> onDownstreamApplicationActive;
  if (mImpl->mConfig.mPreamble.ModeForProtocol(mImpl->mAttemptProtocol) ==
      PreambleMode::TreeNativeParserDocumentStartResponseStop) {
    onDownstreamApplicationActive = [self,
                                     generation = mImpl->mAttemptGeneration,
                                     protocol = mImpl->mAttemptProtocol]() {
      nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
          "NaiveFox::TunnelServerApplicationActive",
          [self, generation, protocol]() {
            self->TunnelServerApplicationActiveOnMain(generation, protocol);
          }));
      if (NS_FAILED(rv)) {
        self->Fail(rv);
      }
    };
  }
  mImpl->mPump = new DuplexPump(
      mImpl->mLocalIn, mImpl->mLocalOut, mImpl->mPendingTunnelIn,
      mImpl->mPendingTunnelOut, mImpl->mPaddingEnabled,
      [self, generation = mImpl->mAttemptGeneration,
       protocol = mImpl->mAttemptProtocol]() {
        nsresult rv = NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NaiveFox::TunnelApplicationActive",
            [self, generation, protocol]() {
              self->TunnelApplicationActiveOnMain(generation, protocol);
            }));
        if (NS_FAILED(rv)) {
          self->Fail(rv);
        }
      },
      std::move(onDownstreamApplicationActive),
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
  mImpl->mCancelRequested.store(true, std::memory_order_release);
  ReleaseOuterGate();
  RefPtr self = this;
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::CancelFailedTunnelRequest",
      [self, aStatus]() { self->CancelRequestOnMain(aStatus); }));
  RuntimeLogEvent("Connection %llu failed target=%s status=0x%08x\n",
                  static_cast<unsigned long long>(mImpl->mConnectionId),
                  mImpl->mTargetAuthority.get(),
                  static_cast<unsigned>(aStatus));
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

void TunnelSession::Cancel(nsresult aStatus) { CancelInternal(aStatus, true); }

void TunnelSession::CancelInternal(nsresult aStatus, bool aCancelRequest) {
  if (mImpl->mClosed) {
    return;
  }
  mImpl->mClosed = true;
  mImpl->mCancelRequested.store(true, std::memory_order_release);
  ReleaseOuterGate();
  if (aCancelRequest) {
    RefPtr self = this;
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::CancelTunnelRequest",
        [self, aStatus]() { self->CancelRequestOnMain(aStatus); }));
  }
  RuntimeLogEvent("Connection %llu closed status=0x%08x\n",
                  static_cast<unsigned long long>(mImpl->mConnectionId),
                  static_cast<unsigned>(aStatus));
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

void TunnelSession::NotifyOuterGateReady() {
  if (!ShouldGateOuterSession(mImpl->mConfig) ||
      !mImpl->mOuterGateRegistered.load(std::memory_order_acquire) ||
      mImpl->mOuterGateReleaseRequested.load(std::memory_order_acquire)) {
    return;
  }
  RefPtr self = this;
  (void)NS_DispatchToMainThread(
      NS_NewRunnableFunction("NaiveFox::OuterSessionGateReady", [self]() {
        if (!self->mImpl->mOuterGateReleaseRequested.load(
                std::memory_order_acquire)) {
          OuterSessionGate::Get().MarkReady(self->mImpl->mOuterGateKey,
                                            self->mImpl->mConnectionId);
        }
      }));
}

void TunnelSession::ReleaseOuterGate() {
  if (!ShouldGateOuterSession(mImpl->mConfig) ||
      mImpl->mOuterGateReleaseRequested.exchange(true,
                                                 std::memory_order_acq_rel)) {
    return;
  }
  if (!mImpl->mOuterGateRegistered.exchange(false, std::memory_order_acq_rel)) {
    return;
  }
  nsCString routeKey(mImpl->mOuterGateKey);
  const uint64_t participant = mImpl->mConnectionId;
  (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
      "NaiveFox::OuterSessionGateLeave",
      [routeKey = std::move(routeKey), participant]() {
        OuterSessionGate::Get().Leave(routeKey, participant);
      }));
}

}  // namespace mozilla::naivefox
