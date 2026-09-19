/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_TransportWebSocket_h
#define netwerk_naivefox_TransportWebSocket_h

#include <functional>
#include <memory>

#include "mozilla/RefPtr.h"
#include "nsCOMPtr.h"
#include "nsIWebSocketListener.h"
#include "nsString.h"

class nsIChannel;

namespace mozilla::net {
class WebSocketChannel;
}

namespace mozilla::naivefox {

struct TransportConfig;
class TransportCookies;

class TransportWebSocket final : public nsIWebSocketListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIWEBSOCKETLISTENER

  TransportWebSocket(std::function<void()> aStarted,
                     std::function<void(const nsACString&)> aMessage,
                     std::function<void(uint32_t)> aAcknowledged,
                     std::function<void(nsresult)> aStopped);

  nsresult Start(const TransportConfig& aConfig,
                 std::shared_ptr<TransportCookies> aCookies,
                 const nsACString& aPath, const nsACString& aProtocol);
  nsresult Send(const nsACString& aMessage);
  void Close(nsresult aStatus);

 private:
  ~TransportWebSocket();
  RefPtr<net::WebSocketChannel> mChannel;
  nsCOMPtr<nsIChannel> mHandshake;
  std::shared_ptr<TransportCookies> mCookies;
  std::function<void()> mStarted;
  std::function<void(const nsACString&)> mMessage;
  std::function<void(uint32_t)> mAcknowledged;
  std::function<void(nsresult)> mStopped;
  nsresult mCloseStatus = NS_OK;
  nsCString mProtocol;
  bool mOpen = false;
  bool mClosing = false;
};

void ShutdownTransportWebSockets();

}  // namespace mozilla::naivefox

#endif
