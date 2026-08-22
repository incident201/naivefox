/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "SocksServer.h"

#include <array>
#include <cstring>
#include <functional>
#include <utility>

#include "HttpConnectParser.h"
#include "RuntimeLogging.h"
#include "Socks5Parser.h"
#include "TunnelSession.h"
#include "mozilla/Atomics.h"
#include "mozilla/RefPtr.h"
#include "mozilla/ScopeExit.h"
#include "mozilla/Span.h"
#include "nsCOMPtr.h"
#include "nsComponentManagerUtils.h"
#include "nsError.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsIEventTarget.h"
#include "nsIInputStream.h"
#include "nsIOutputStream.h"
#include "nsIServerSocket.h"
#include "nsISocketTransport.h"
#include "nsISupportsImpl.h"
#include "nsITransport.h"
#include "nsNetCID.h"
#include "nsServiceManagerUtils.h"
#include "nsThreadUtils.h"
#include "prnetdb.h"

namespace mozilla::naivefox {

void LocalProxyServerControl::RequestStop() {
  mStopRequested = true;
  nsCOMPtr<nsIEventTarget> target;
  {
    MutexAutoLock lock(mMutex);
    target = mMainEventTarget;
  }
  if (target) {
    (void)target->Dispatch(
        NS_NewRunnableFunction("NaiveFox::WakeEmbeddedEventLoop", []() {}));
  }
}

void LocalProxyServerControl::SetMainEventTarget(nsIEventTarget* aTarget) {
  {
    MutexAutoLock lock(mMutex);
    mMainEventTarget = aTarget;
  }
  if (mStopRequested) {
    RequestStop();
  }
}

void LocalProxyServerControl::ClearMainEventTarget() {
  MutexAutoLock lock(mMutex);
  mMainEventTarget = nullptr;
}

namespace {

class SocksConnection final : public nsIInputStreamCallback,
                              public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  SocksConnection(nsIAsyncInputStream* aLocalIn,
                  nsIAsyncOutputStream* aLocalOut,
                  const TunnelConfig& aTunnelConfig,
                  nsIEventTarget* aSocketTarget,
                  std::function<void()>&& aOnClose)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mTunnelConfig(aTunnelConfig),
        mSocketTarget(aSocketTarget),
        mOnClose(std::move(aOnClose)) {}

  nsresult Start() { return WaitForInput(); }
  void RequestClose(nsresult aStatus) { Close(aStatus); }

 private:
  static constexpr size_t kMaxReplyBytes = 32;

  ~SocksConnection() { Close(NS_BASE_STREAM_CLOSED); }

  nsresult WaitForInput();
  nsresult WaitForOutput();
  nsresult QueueReply(Span<const uint8_t> aBytes, bool aCloseAfter);
  nsresult FlushReplies();
  nsresult BeginTunnel(const nsACString& aAuthority,
                       Span<const uint8_t> aInitialPayload);
  void TunnelEstablished(const nsACString& aOuterProtocol,
                         bool aPaddingEnabled);
  void TunnelFailed(nsresult aStatus);
  void Close(nsresult aStatus);

  nsCOMPtr<nsIAsyncInputStream> mLocalIn;
  nsCOMPtr<nsIAsyncOutputStream> mLocalOut;
  TunnelConfig mTunnelConfig;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  Socks5Parser mParser;
  std::array<uint8_t, kMaxReplyBytes> mReplies{};
  size_t mReplyLength = 0;
  size_t mReplyOffset = 0;
  RefPtr<TunnelSession> mSession;
  std::function<void()> mOnClose;
  bool mOpening = false;
  bool mEstablished = false;
  bool mPumpStarted = false;
  bool mOutputWaiting = false;
  bool mCloseAfterWrite = false;
  bool mFailureQueued = false;
  bool mInputTerminal = false;
  bool mClosed = false;
};

NS_IMPL_ISUPPORTS(SocksConnection, nsIInputStreamCallback,
                  nsIOutputStreamCallback)

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
  if (mReplyOffset != 0) {
    const size_t pending = mReplyLength - mReplyOffset;
    std::memmove(mReplies.data(), mReplies.data() + mReplyOffset, pending);
    mReplyLength = pending;
    mReplyOffset = 0;
  }
  if (aBytes.Length() > kMaxReplyBytes - mReplyLength) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  std::memcpy(mReplies.data() + mReplyLength, aBytes.Elements(),
              aBytes.Length());
  mReplyLength += aBytes.Length();
  mCloseAfterWrite |= aCloseAfter;
  return WaitForOutput();
}

nsresult SocksConnection::FlushReplies() {
  while (mReplyOffset < mReplyLength) {
    uint32_t written = 0;
    nsresult rv = mLocalOut->Write(
        reinterpret_cast<const char*>(mReplies.data() + mReplyOffset),
        mReplyLength - mReplyOffset, &written);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      return WaitForOutput();
    }
    if (NS_FAILED(rv) || written == 0) {
      return NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
    }
    mReplyOffset += written;
  }
  mReplyLength = 0;
  mReplyOffset = 0;
  if (mCloseAfterWrite) {
    Close(NS_OK);
  } else if (mEstablished && mSession && !mPumpStarted) {
    mPumpStarted = true;
    return mSession->StartPump();
  }
  return NS_OK;
}

nsresult SocksConnection::BeginTunnel(const nsACString& aAuthority,
                                      Span<const uint8_t> aInitialPayload) {
  RefPtr self = this;
  mSession = new TunnelSession(
      mLocalIn, mLocalOut, mTunnelConfig, mSocketTarget,
      [self](const nsACString& aOuterProtocol, bool aPaddingEnabled) {
        self->TunnelEstablished(aOuterProtocol, aPaddingEnabled);
      },
      [self](nsresult aStatus) { self->TunnelFailed(aStatus); },
      [self](nsresult aStatus) { self->Close(aStatus); });
  return mSession->Start(aAuthority, aInitialPayload);
}

void SocksConnection::TunnelEstablished(const nsACString& aOuterProtocol,
                                        bool aPaddingEnabled) {
  if (mClosed || mFailureQueued) {
    return;
  }
  mEstablished = true;
  nsTArray<uint8_t> reply;
  Socks5Parser::MakeReply(0x00, reply);
  nsresult rv = QueueReply(Span(reply), false);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

void SocksConnection::TunnelFailed(nsresult aStatus) {
  if (mClosed || mFailureQueued) {
    return;
  }
  mFailureQueued = true;
  nsTArray<uint8_t> reply;
  Socks5Parser::MakeReply(0x01, reply);
  nsresult rv = QueueReply(Span(reply), true);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

NS_IMETHODIMP SocksConnection::OnInputStreamReady(
    nsIAsyncInputStream* aStream) {
  if (mClosed || mOpening || mInputTerminal) {
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
  while (offset < read && !mOpening && !mClosed && !mInputTerminal) {
    size_t consumed = 0;
    auto event =
        mParser.Consume(Span(buffer.data() + offset, read - offset), consumed);
    offset += consumed;
    if (event == Socks5Parser::Event::SendNoAuthenticationSelection) {
      nsTArray<uint8_t> reply;
      Socks5Parser::MakeMethodSelection(true, reply);
      rv = QueueReply(Span(reply), false);
    } else if (event == Socks5Parser::Event::RequestReady) {
      mOpening = true;
      rv = BeginTunnel(mParser.Target().Authority(),
                       Span(buffer.data() + offset, read - offset));
    } else if (event != Socks5Parser::Event::NeedMore) {
      mInputTerminal = true;
      mFailureQueued = true;
      nsTArray<uint8_t> reply;
      if (event == Socks5Parser::Event::RejectMethods) {
        Socks5Parser::MakeMethodSelection(false, reply);
      } else {
        const uint8_t code = event == Socks5Parser::Event::RejectCommand ? 0x07
                             : event == Socks5Parser::Event::RejectAddressType
                                 ? 0x08
                                 : 0x01;
        Socks5Parser::MakeReply(code, reply);
      }
      rv = QueueReply(Span(reply), true);
    }
    if (NS_FAILED(rv)) {
      Close(rv);
      return NS_OK;
    }
  }
  if (!mOpening && !mClosed && !mInputTerminal) {
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
  if (!mClosed) {
    nsresult rv = FlushReplies();
    if (NS_FAILED(rv)) {
      Close(rv);
    }
  }
  return NS_OK;
}

void SocksConnection::Close(nsresult aStatus) {
  if (mClosed) {
    return;
  }
  mClosed = true;
  RefPtr<TunnelSession> session = std::move(mSession);
  if (session) {
    session->Cancel(aStatus);
  }
  (void)mLocalIn->CloseWithStatus(aStatus);
  (void)mLocalOut->CloseWithStatus(aStatus);
  if (mOnClose) {
    auto onClose = std::move(mOnClose);
    onClose();
  }
}

class HttpConnectConnection final : public nsIInputStreamCallback,
                                    public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  HttpConnectConnection(nsIAsyncInputStream* aLocalIn,
                        nsIAsyncOutputStream* aLocalOut,
                        const TunnelConfig& aTunnelConfig,
                        nsIEventTarget* aSocketTarget,
                        std::function<void()>&& aOnClose)
      : mLocalIn(aLocalIn),
        mLocalOut(aLocalOut),
        mTunnelConfig(aTunnelConfig),
        mSocketTarget(aSocketTarget),
        mOnClose(std::move(aOnClose)) {}

  nsresult Start() { return WaitForInput(); }
  void RequestClose(nsresult aStatus) { Close(aStatus); }

 private:
  ~HttpConnectConnection() { Close(NS_BASE_STREAM_CLOSED); }

  nsresult WaitForInput();
  nsresult WaitForOutput();
  nsresult QueueResponse(const nsACString& aResponse, bool aCloseAfter);
  nsresult FlushResponse();
  nsresult BeginTunnel(const nsACString& aAuthority,
                       Span<const uint8_t> aInitialPayload);
  void TunnelEstablished(const nsACString& aOuterProtocol,
                         bool aPaddingEnabled);
  void TunnelFailed(nsresult aStatus);
  void Reject(HttpConnectParser::Event aEvent);
  void Close(nsresult aStatus);

  nsCOMPtr<nsIAsyncInputStream> mLocalIn;
  nsCOMPtr<nsIAsyncOutputStream> mLocalOut;
  TunnelConfig mTunnelConfig;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  HttpConnectParser mParser;
  nsCString mResponse;
  size_t mResponseOffset = 0;
  RefPtr<TunnelSession> mSession;
  std::function<void()> mOnClose;
  bool mOpening = false;
  bool mEstablished = false;
  bool mPumpStarted = false;
  bool mOutputWaiting = false;
  bool mCloseAfterWrite = false;
  bool mInputTerminal = false;
  bool mClosed = false;
};

NS_IMPL_ISUPPORTS(HttpConnectConnection, nsIInputStreamCallback,
                  nsIOutputStreamCallback)

nsresult HttpConnectConnection::WaitForInput() {
  return mLocalIn->AsyncWait(this, 0, 0, nullptr);
}

nsresult HttpConnectConnection::WaitForOutput() {
  if (mOutputWaiting) {
    return NS_OK;
  }
  nsresult rv = mLocalOut->AsyncWait(this, 0, 0, nullptr);
  if (NS_SUCCEEDED(rv)) {
    mOutputWaiting = true;
  }
  return rv;
}

nsresult HttpConnectConnection::QueueResponse(const nsACString& aResponse,
                                              bool aCloseAfter) {
  if (!mResponse.IsEmpty()) {
    return NS_ERROR_ALREADY_INITIALIZED;
  }
  mResponse = aResponse;
  mCloseAfterWrite = aCloseAfter;
  return WaitForOutput();
}

nsresult HttpConnectConnection::FlushResponse() {
  while (mResponseOffset < mResponse.Length()) {
    uint32_t written = 0;
    nsresult rv =
        mLocalOut->Write(mResponse.get() + mResponseOffset,
                         mResponse.Length() - mResponseOffset, &written);
    if (rv == NS_BASE_STREAM_WOULD_BLOCK) {
      return WaitForOutput();
    }
    if (NS_FAILED(rv) || written == 0) {
      return NS_FAILED(rv) ? rv : NS_ERROR_UNEXPECTED;
    }
    mResponseOffset += written;
  }
  mResponse.Truncate();
  mResponseOffset = 0;
  if (mCloseAfterWrite) {
    Close(NS_OK);
  } else if (mEstablished && mSession && !mPumpStarted) {
    mPumpStarted = true;
    return mSession->StartPump();
  }
  return NS_OK;
}

nsresult HttpConnectConnection::BeginTunnel(
    const nsACString& aAuthority, Span<const uint8_t> aInitialPayload) {
  RefPtr self = this;
  mSession = new TunnelSession(
      mLocalIn, mLocalOut, mTunnelConfig, mSocketTarget,
      [self](const nsACString& aOuterProtocol, bool aPaddingEnabled) {
        self->TunnelEstablished(aOuterProtocol, aPaddingEnabled);
      },
      [self](nsresult aStatus) { self->TunnelFailed(aStatus); },
      [self](nsresult aStatus) { self->Close(aStatus); });
  return mSession->Start(aAuthority, aInitialPayload);
}

void HttpConnectConnection::TunnelEstablished(const nsACString& aOuterProtocol,
                                              bool aPaddingEnabled) {
  if (mClosed) {
    return;
  }
  mEstablished = true;
  nsresult rv =
      QueueResponse("HTTP/1.1 200 Connection Established\r\n\r\n"_ns, false);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

void HttpConnectConnection::TunnelFailed(nsresult aStatus) {
  if (mClosed) {
    return;
  }
  nsresult rv = QueueResponse(
      "HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: "
      "0\r\n\r\n"_ns,
      true);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

void HttpConnectConnection::Reject(HttpConnectParser::Event aEvent) {
  if (mClosed || mInputTerminal) {
    return;
  }
  mInputTerminal = true;
  nsAutoCString response(
      "HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: "
      "0\r\n\r\n"_ns);
  if (aEvent == HttpConnectParser::Event::UnsupportedMethod) {
    response =
        "HTTP/1.1 405 Method Not Allowed\r\nAllow: CONNECT\r\nConnection: "
        "close\r\nContent-Length: 0\r\n\r\n"_ns;
  } else if (aEvent == HttpConnectParser::Event::HeaderTooLarge) {
    response =
        "HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: "
        "close\r\nContent-Length: 0\r\n\r\n"_ns;
  }
  nsresult rv = QueueResponse(response, true);
  if (NS_FAILED(rv)) {
    Close(rv);
  }
}

NS_IMETHODIMP HttpConnectConnection::OnInputStreamReady(
    nsIAsyncInputStream* aStream) {
  if (mClosed || mOpening || mInputTerminal) {
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
  size_t consumed = 0;
  const auto event = mParser.Consume(Span(buffer.data(), read), consumed);
  if (event == HttpConnectParser::Event::RequestReady) {
    mOpening = true;
    rv = BeginTunnel(mParser.Target().Authority(),
                     Span(buffer.data() + consumed, read - consumed));
    if (NS_FAILED(rv)) {
      TunnelFailed(rv);
    }
  } else if (event == HttpConnectParser::Event::NeedMore) {
    rv = WaitForInput();
    if (NS_FAILED(rv)) {
      Close(rv);
    }
  } else {
    Reject(event);
  }
  return NS_OK;
}

NS_IMETHODIMP HttpConnectConnection::OnOutputStreamReady(
    nsIAsyncOutputStream* aStream) {
  mOutputWaiting = false;
  if (!mClosed) {
    nsresult rv = FlushResponse();
    if (NS_FAILED(rv)) {
      Close(rv);
    }
  }
  return NS_OK;
}

void HttpConnectConnection::Close(nsresult aStatus) {
  if (mClosed) {
    return;
  }
  mClosed = true;
  RefPtr<TunnelSession> session = std::move(mSession);
  if (session) {
    session->Cancel(aStatus);
  }
  (void)mLocalIn->CloseWithStatus(aStatus);
  (void)mLocalOut->CloseWithStatus(aStatus);
  if (mOnClose) {
    auto onClose = std::move(mOnClose);
    onClose();
  }
}

class ServerState final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ServerState)

  explicit ServerState(uint32_t aMaxConnections)
      : mMutex("NaiveFox::ServerState::mMutex"),
        mMaxConnections(aMaxConnections) {}

  void AddSocket(nsIServerSocket* aSocket) {
    bool close = false;
    {
      MutexAutoLock lock(mMutex);
      close = mStopping;
      if (!close) {
        mSockets.AppendElement(aSocket);
      }
    }
    if (close) {
      (void)aSocket->Close();
    }
  }

  bool ConnectionAccepted(uint64_t& aConnectionId) {
    bool closeListeners = false;
    {
      MutexAutoLock lock(mMutex);
      if (mStopping ||
          (mMaxConnections && mAcceptedConnections >= mMaxConnections)) {
        return false;
      }
      ++mAcceptedConnections;
      aConnectionId = ++mNextConnectionId;
      mConnections.AppendElement(ActiveConnection{aConnectionId, nullptr});
      closeListeners =
          mMaxConnections && mAcceptedConnections == mMaxConnections;
    }
    if (closeListeners) {
      CloseListeners();
    }
    return true;
  }

  void SetCancellation(uint64_t aConnectionId,
                       std::function<void()>&& aCancellation) {
    std::function<void()> cancellation;
    {
      MutexAutoLock lock(mMutex);
      for (auto& connection : mConnections) {
        if (connection.mId == aConnectionId) {
          connection.mCancel = std::move(aCancellation);
          if (mStopping) {
            cancellation = connection.mCancel;
          }
          break;
        }
      }
    }
    if (cancellation) {
      cancellation();
    }
  }

  void ConnectionClosed(uint64_t aConnectionId) {
    MutexAutoLock lock(mMutex);
    for (size_t index = 0; index < mConnections.Length(); ++index) {
      if (mConnections[index].mId == aConnectionId) {
        mConnections.RemoveElementAt(index);
        break;
      }
    }
  }

  bool Complete() {
    MutexAutoLock lock(mMutex);
    return mConnections.IsEmpty() &&
           (mStopping ||
            (mMaxConnections && mAcceptedConnections == mMaxConnections));
  }

  void CloseListeners() {
    nsTArray<nsCOMPtr<nsIServerSocket>> sockets;
    {
      MutexAutoLock lock(mMutex);
      sockets = mSockets.Clone();
    }
    for (const auto& socket : sockets) {
      (void)socket->Close();
    }
  }

  void RequestStop() {
    nsTArray<nsCOMPtr<nsIServerSocket>> sockets;
    nsTArray<std::function<void()>> cancellations;
    {
      MutexAutoLock lock(mMutex);
      if (mStopping) {
        return;
      }
      mStopping = true;
      sockets = mSockets.Clone();
      for (const auto& connection : mConnections) {
        if (connection.mCancel) {
          cancellations.AppendElement(connection.mCancel);
        }
      }
    }
    for (const auto& socket : sockets) {
      (void)socket->Close();
    }
    for (auto& cancellation : cancellations) {
      cancellation();
    }
  }

  void Shutdown() {
    RequestStop();
    MutexAutoLock lock(mMutex);
    mSockets.Clear();
  }

 private:
  struct ActiveConnection final {
    uint64_t mId;
    std::function<void()> mCancel;
  };

  ~ServerState() { Shutdown(); }

  Mutex mMutex;
  nsTArray<nsCOMPtr<nsIServerSocket>> mSockets MOZ_GUARDED_BY(mMutex);
  nsTArray<ActiveConnection> mConnections MOZ_GUARDED_BY(mMutex);
  uint32_t mMaxConnections MOZ_GUARDED_BY(mMutex);
  uint32_t mAcceptedConnections MOZ_GUARDED_BY(mMutex) = 0;
  uint64_t mNextConnectionId MOZ_GUARDED_BY(mMutex) = 0;
  bool mStopping MOZ_GUARDED_BY(mMutex) = false;
};

class LocalListener final : public nsIServerSocketListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSISERVERSOCKETLISTENER

  LocalListener(const ListenerConfig& aListener,
                const TunnelConfig& aTunnelConfig,
                nsIEventTarget* aSocketTarget, ServerState* aState)
      : mListener(aListener),
        mTunnelConfig(aTunnelConfig),
        mSocketTarget(aSocketTarget),
        mState(aState) {}

 private:
  ~LocalListener() = default;

  ListenerConfig mListener;
  TunnelConfig mTunnelConfig;
  nsCOMPtr<nsIEventTarget> mSocketTarget;
  RefPtr<ServerState> mState;
};

NS_IMPL_ISUPPORTS(LocalListener, nsIServerSocketListener)

NS_IMETHODIMP LocalListener::OnSocketAccepted(nsIServerSocket* aServer,
                                              nsISocketTransport* aTransport) {
  uint64_t connectionId = 0;
  if (!mState->ConnectionAccepted(connectionId)) {
    (void)aTransport->Close(NS_ERROR_ABORT);
    return NS_OK;
  }
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
    mState->ConnectionClosed(connectionId);
    return NS_OK;
  }

  RefPtr state = mState;
  auto onClose = [state, connectionId]() {
    (void)NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NaiveFox::LocalConnectionClosed",
        [state, connectionId]() { state->ConnectionClosed(connectionId); }));
  };
  if (mListener.mType == ListenerType::Socks5) {
    RefPtr connection = new SocksConnection(localIn, localOut, mTunnelConfig,
                                            mSocketTarget, std::move(onClose));
    nsCOMPtr<nsIEventTarget> socketTarget = mSocketTarget;
    mState->SetCancellation(
        connectionId, [socketTarget, connection = RefPtr{connection}]() {
          nsresult rv = socketTarget->Dispatch(NS_NewRunnableFunction(
              "NaiveFox::CloseSocksConnection",
              [connection]() { connection->RequestClose(NS_ERROR_ABORT); }));
          if (NS_FAILED(rv)) {
            connection->RequestClose(rv);
          }
        });
    rv = connection->Start();
    if (NS_FAILED(rv)) {
      connection->RequestClose(rv);
    }
  } else {
    RefPtr connection = new HttpConnectConnection(
        localIn, localOut, mTunnelConfig, mSocketTarget, std::move(onClose));
    nsCOMPtr<nsIEventTarget> socketTarget = mSocketTarget;
    mState->SetCancellation(
        connectionId, [socketTarget, connection = RefPtr{connection}]() {
          nsresult rv = socketTarget->Dispatch(NS_NewRunnableFunction(
              "NaiveFox::CloseHttpConnectConnection",
              [connection]() { connection->RequestClose(NS_ERROR_ABORT); }));
          if (NS_FAILED(rv)) {
            connection->RequestClose(rv);
          }
        });
    rv = connection->Start();
    if (NS_FAILED(rv)) {
      connection->RequestClose(rv);
    }
  }
  return NS_OK;
}

NS_IMETHODIMP LocalListener::OnStopListening(nsIServerSocket* aServer,
                                             nsresult aStatus) {
  return NS_OK;
}

}  // namespace

nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>& aListeners,
                             const nsTArray<TunnelConfig>& aTunnelConfigs,
                             uint32_t aMaxConnections,
                             LocalProxyServerControl* aControl) {
  if (aListeners.IsEmpty() || aTunnelConfigs.IsEmpty() ||
      (aTunnelConfigs.Length() >= 2 &&
       aTunnelConfigs.Length() != aListeners.Length())) {
    return NS_ERROR_INVALID_ARG;
  }
  nsCOMPtr<nsIEventTarget> socketTarget =
      do_GetService(NS_SOCKETTRANSPORTSERVICE_CONTRACTID);
  if (!socketTarget) {
    return NS_ERROR_FAILURE;
  }
  if (aControl) {
    aControl->SetMainEventTarget(GetMainThreadSerialEventTarget());
  }
  auto clearControl = MakeScopeExit([aControl]() {
    if (aControl) {
      aControl->ClearMainEventTarget();
    }
  });
  RefPtr state = new ServerState(aMaxConnections);
  if (aControl && aControl->StopRequested()) {
    state->RequestStop();
    return NS_OK;
  }
  for (size_t index = 0; index < aListeners.Length(); ++index) {
    const auto& config = aListeners[index];
    const auto& tunnelConfig =
        aTunnelConfigs[aTunnelConfigs.Length() == 1 ? 0 : index];
    nsCOMPtr<nsIServerSocket> server =
        do_CreateInstance("@mozilla.org/network/server-socket;1");
    if (!server) {
      state->Shutdown();
      return NS_ERROR_FAILURE;
    }
    nsAutoCString bindHost(config.mHost);
    if (bindHost.EqualsLiteral("localhost")) {
      bindHost.AssignLiteral("127.0.0.1");
    }
    PRNetAddr bindAddress{};
    if (PR_StringToNetAddr(bindHost.get(), &bindAddress) != PR_SUCCESS ||
        (bindAddress.raw.family != PR_AF_INET &&
         bindAddress.raw.family != PR_AF_INET6)) {
      state->Shutdown();
      return NS_ERROR_INVALID_ARG;
    }
    if (bindAddress.raw.family == PR_AF_INET6) {
      bindAddress.ipv6.port = PR_htons(config.mPort);
    } else {
      bindAddress.inet.port = PR_htons(config.mPort);
    }
    nsresult rv = server->InitWithAddress(&bindAddress, -1);
    if (NS_FAILED(rv)) {
      state->Shutdown();
      return rv;
    }
    RefPtr listener =
        new LocalListener(config, tunnelConfig, socketTarget, state);
    rv = server->AsyncListen(listener);
    if (NS_FAILED(rv)) {
      state->Shutdown();
      return rv;
    }
    state->AddSocket(server);
    if (aControl && aControl->StopRequested()) {
      state->RequestStop();
      return NS_OK;
    }
    RuntimeLog("%s listening on %s%s%s:%u\n",
               config.mType == ListenerType::Socks5 ? "SOCKS5" : "HTTP CONNECT",
               config.mIPv6 ? "[" : "", config.mHost.get(),
               config.mIPv6 ? "]" : "", static_cast<unsigned>(config.mPort));
    RuntimeLogEvent("Listening on %s://%s%s%s:%u\n",
                    config.mType == ListenerType::Socks5 ? "socks" : "http",
                    config.mIPv6 ? "[" : "", config.mHost.get(),
                    config.mIPv6 ? "]" : "",
                    static_cast<unsigned>(config.mPort));
  }
  while (!state->Complete()) {
    if (aControl && aControl->StopRequested()) {
      state->RequestStop();
      if (state->Complete()) {
        break;
      }
    }
    if (!NS_ProcessNextEvent(nullptr, true)) {
      break;
    }
  }
  state->Shutdown();
  return NS_OK;
}

nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>& aListeners,
                             const TunnelConfig& aTunnelConfig,
                             uint32_t aMaxConnections) {
  nsTArray<TunnelConfig> tunnelConfigs;
  tunnelConfigs.AppendElement(aTunnelConfig);
  return RunLocalProxyServer(aListeners, tunnelConfigs, aMaxConnections);
}

nsresult RunSocksServer(uint16_t aListenPort, const nsACString& aProxyUrl,
                        const nsACString& aProxyUser,
                        const nsACString& aProxyPassword,
                        uint32_t aMaxConnections, ProxyProtocol aProtocol) {
  nsTArray<ListenerConfig> listeners;
  ListenerConfig& listener = *listeners.AppendElement();
  listener.mType = ListenerType::Socks5;
  listener.mHost.AssignLiteral("127.0.0.1");
  listener.mPort = aListenPort;
  TunnelConfig tunnelConfig;
  tunnelConfig.mProxyUrl = aProxyUrl;
  tunnelConfig.mProxyUser = aProxyUser;
  tunnelConfig.mProxyPassword = aProxyPassword;
  tunnelConfig.mProtocol = aProtocol;
  return RunLocalProxyServer(listeners, tunnelConfig, aMaxConnections);
}

}  // namespace mozilla::naivefox
