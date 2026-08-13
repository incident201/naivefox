/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_Config_h
#define netwerk_naivefox_Config_h

#include <cstdint>

#include "ProxyProtocol.h"
#include "nsString.h"
#include "nsTArray.h"
#include "nscore.h"

namespace mozilla::naivefox {

enum class ListenerType : uint8_t { Socks5, HttpConnect };

struct ListenerConfig final {
  ListenerType mType = ListenerType::Socks5;
  nsCString mHost;
  uint16_t mPort = 0;
  bool mIPv6 = false;
};

struct UpstreamProxyConfig final {
  nsCString mUrl;
  nsCString mUser;
  nsCString mPassword;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
};

enum class RuntimeLogMode : uint8_t { Disabled, Console, File };

struct Config final {
  nsTArray<ListenerConfig> mListeners;
  nsTArray<UpstreamProxyConfig> mProxies;
  RuntimeLogMode mLogMode = RuntimeLogMode::Disabled;
  nsCString mLogPath;
};

nsresult ParseConfig(const nsACString& aJson, Config& aConfig,
                     nsACString& aError);
nsresult LoadConfigFile(const nsACString& aPath, Config& aConfig,
                        nsACString& aError);
nsresult ResolveAndCreateProfile(nsACString& aProfilePath, nsACString& aError);

}  // namespace mozilla::naivefox

#endif
