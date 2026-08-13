/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "SocksServer.h"

#include <array>
#include <functional>
#include <utility>

#include "HttpConnectParser.h"
#include "RuntimeLogging.h"
#include "Socks5Parser.h"
#include "TunnelSession.h"
#include "mozilla/Atomics.h"
#include "mozilla/RefPtr.h"
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
#include "nsITransport.h"
#include "nsNetCID.h"
#include "nsServiceManagerUtils.h"
#include "nsThreadUtils.h"

namespace mozilla::naivefox {

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

 private:
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
  nsTArray<uint8_t> mReplies;
  size_t mReplyOffset = 0;
  RefPtr<TunnelSession> mSession;
  std::function<void()> mOnClose;
  bool mOpening = false;
  bool mEstablished = false;
  bool mPumpStarted = false;
  bool mOutputWaiting = false;
  bool mCloseAfterWrite = false;
  bool mFailureQueued = false;
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
  if (mClosed || mOpening) {
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
  while (offset < read && !mOpening && !mClosed) {
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
  if (!mOpening && !mClosed) {
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
  if (mClosed || mOpening) {
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

class ServerState final : public RefCounted<ServerState> {
 public:
  MOZ_DECLARE_REFCOUNTED_TYPENAME(ServerState)

  explicit ServerState(uint32_t aMaxConnections)
      : mMaxConnections(aMaxConnections) {}

  void AddSocket(nsIServerSocket* aSocket) { mSockets.AppendElement(aSocket); }

  bool ConnectionAccepted() {
    const uint32_t accepted = ++mAcceptedConnections;
    if (mMaxConnections && accepted > mMaxConnections) {
      return false;
    }
    if (mMaxConnections && accepted == mMaxConnections) {
      CloseListeners();
    }
    return true;
  }

  void ConnectionClosed() { ++mCompletedConnections; }

  bool Complete() const {
    return mMaxConnections && mAcceptedConnections == mMaxConnections &&
           mCompletedConnections == mAcceptedConnections;
  }

  void CloseListeners() {
    for (const auto& socket : mSockets) {
      (void)socket->Close();
    }
  }

  void Shutdown() {
    CloseListeners();
    mSockets.Clear();
  }

  ~ServerState() { Shutdown(); }

 private:
  nsTArray<nsCOMPtr<nsIServerSocket>> mSockets;
  uint32_t mMaxConnections;
  Atomic<uint32_t, Relaxed> mAcceptedConnections{0};
  Atomic<uint32_t, Relaxed> mCompletedConnections{0};
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
  if (!mState->ConnectionAccepted()) {
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
    mState->ConnectionClosed();
    return NS_OK;
  }

  RefPtr state = mState;
  auto onClose = [state]() {
    (void)NS_DispatchToMainThread(
        NS_NewRunnableFunction("NaiveFox::LocalConnectionClosed",
                               [state]() { state->ConnectionClosed(); }));
  };
  if (mListener.mType == ListenerType::Socks5) {
    RefPtr connection = new SocksConnection(localIn, localOut, mTunnelConfig,
                                            mSocketTarget, std::move(onClose));
    rv = connection->Start();
  } else {
    RefPtr connection = new HttpConnectConnection(
        localIn, localOut, mTunnelConfig, mSocketTarget, std::move(onClose));
    rv = connection->Start();
  }
  if (NS_FAILED(rv)) {
    (void)aTransport->Close(rv);
  }
  return NS_OK;
}

NS_IMETHODIMP LocalListener::OnStopListening(nsIServerSocket* aServer,
                                             nsresult aStatus) {
  return NS_OK;
}

}  // namespace

nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>& aListeners,
                             const TunnelConfig& aTunnelConfig,
                             uint32_t aMaxConnections) {
  if (aListeners.IsEmpty()) {
    return NS_ERROR_INVALID_ARG;
  }
  nsCOMPtr<nsIEventTarget> socketTarget =
      do_GetService(NS_SOCKETTRANSPORTSERVICE_CONTRACTID);
  if (!socketTarget) {
    return NS_ERROR_FAILURE;
  }
  RefPtr state = new ServerState(aMaxConnections);
  for (const auto& config : aListeners) {
    nsCOMPtr<nsIServerSocket> server =
        do_CreateInstance("@mozilla.org/network/server-socket;1");
    if (!server) {
      state->Shutdown();
      return NS_ERROR_FAILURE;
    }
    nsresult rv = config.mIPv6 ? server->InitIPv6(config.mPort, true, -1)
                               : server->Init(config.mPort, true, -1);
    if (NS_FAILED(rv)) {
      state->Shutdown();
      return rv;
    }
    RefPtr listener =
        new LocalListener(config, aTunnelConfig, socketTarget, state);
    rv = server->AsyncListen(listener);
    if (NS_FAILED(rv)) {
      state->Shutdown();
      return rv;
    }
    state->AddSocket(server);
    RuntimeLog("%s listening on %s%s%s:%u\n",
               config.mType == ListenerType::Socks5 ? "SOCKS5" : "HTTP CONNECT",
               config.mIPv6 ? "[" : "", config.mHost.get(),
               config.mIPv6 ? "]" : "", static_cast<unsigned>(config.mPort));
  }
  while ((!aMaxConnections || !state->Complete()) &&
         NS_ProcessNextEvent(nullptr, true)) {
  }
  state->Shutdown();
  return NS_OK;
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
