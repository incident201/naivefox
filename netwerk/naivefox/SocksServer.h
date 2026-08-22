/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_SocksServer_h
#define netwerk_naivefox_SocksServer_h

#include <cstdint>

#include "Config.h"
#include "TunnelSession.h"
#include "mozilla/Atomics.h"
#include "mozilla/Mutex.h"
#include "mozilla/RefPtr.h"
#include "nsCOMPtr.h"
#include "nsISupportsImpl.h"
#include "nsStringFwd.h"
#include "nscore.h"

class nsIEventTarget;

namespace mozilla::naivefox {

class LocalProxyServerControl final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(LocalProxyServerControl)

  void RequestStop();
  bool StopRequested() const { return mStopRequested; }

 private:
  friend nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>&,
                                      const nsTArray<TunnelConfig>&, uint32_t,
                                      LocalProxyServerControl*);

  ~LocalProxyServerControl() = default;
  void SetMainEventTarget(nsIEventTarget* aTarget);
  void ClearMainEventTarget();

  Atomic<bool, Relaxed> mStopRequested{false};
  Mutex mMutex{"LocalProxyServerControl::mMutex"};
  nsCOMPtr<nsIEventTarget> mMainEventTarget MOZ_GUARDED_BY(mMutex);
};

nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>& aListeners,
                             const TunnelConfig& aTunnelConfig,
                             uint32_t aMaxConnections = 0);

nsresult RunLocalProxyServer(const nsTArray<ListenerConfig>& aListeners,
                             const nsTArray<TunnelConfig>& aTunnelConfigs,
                             uint32_t aMaxConnections = 0,
                             LocalProxyServerControl* aControl = nullptr);

nsresult RunSocksServer(uint16_t aListenPort, const nsACString& aProxyUrl,
                        const nsACString& aProxyUser,
                        const nsACString& aProxyPassword,
                        uint32_t aMaxConnections, ProxyProtocol aProtocol);

}  // namespace mozilla::naivefox

#endif
