/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NoConnectWebSocket_h
#define netwerk_naivefox_NoConnectWebSocket_h

#include <functional>

#include "mozilla/RefPtr.h"
#include "nsIWebSocketListener.h"
#include "nsString.h"

namespace mozilla::net {
class WebSocketChannel;
}

namespace mozilla::naivefox {

struct TunnelConfig;

class NoConnectWebSocket final : public nsIWebSocketListener {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIWEBSOCKETLISTENER

  NoConnectWebSocket(std::function<void()> aStarted,
                     std::function<void(const nsACString&)> aMessage,
                     std::function<void(uint32_t)> aAcknowledged,
                     std::function<void(nsresult)> aStopped);

  nsresult Start(const TunnelConfig& aConfig, const nsACString& aCookie,
                 const nsACString& aPath, const nsACString& aProtocol);
  nsresult Send(const nsACString& aMessage);
  void Close(nsresult aStatus);

 private:
  ~NoConnectWebSocket();
  RefPtr<net::WebSocketChannel> mChannel;
  std::function<void()> mStarted;
  std::function<void(const nsACString&)> mMessage;
  std::function<void(uint32_t)> mAcknowledged;
  std::function<void(nsresult)> mStopped;
  nsresult mCloseStatus = NS_OK;
  nsCString mProtocol;
  bool mOpen = false;
  bool mClosing = false;
};

void ShutdownNoConnectWebSockets();

}  // namespace mozilla::naivefox

#endif
