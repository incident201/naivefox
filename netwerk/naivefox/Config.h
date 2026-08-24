/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_Config_h
#define netwerk_naivefox_Config_h

#include <cstdint>

#include "ProxyProtocol.h"
#include "mozilla/Maybe.h"
#include "nsString.h"
#include "nsTArray.h"
#include "nscore.h"

namespace mozilla::naivefox {

enum class ListenerType : uint8_t { Socks5, HttpConnect };

struct ListenerConfig final {
  ListenerType mType = ListenerType::Socks5;
  nsCString mHost;
  nsCString mUser;
  nsCString mPassword;
  uint16_t mPort = 0;
  bool mIPv6 = false;
};

struct UpstreamProxyConfig final {
  nsCString mUrl;
  nsCString mUser;
  nsCString mPassword;
  ProxyProtocol mProtocol = ProxyProtocol::H2;
};

struct HostResolverRule final {
  nsCString mLogicalHost;
  nsCString mPhysicalHost;
};

struct ExtraHeader final {
  nsCString mName;
  nsCString mValue;
};

enum class PreambleMode : uint8_t {
  Off,
  DocumentComplete,
  TreeComplete,
  TreeOverlap,
  TreeEarlyOverlap,

  // Compatibility names for the first experimental configuration surface.
  Root = DocumentComplete,
  Tree = TreeComplete,
};

struct PreambleConfig final {
  static constexpr uint32_t kMaximumAssets = 6;
  static constexpr uint32_t kMaximumBytes = 384 * 1024;

  PreambleMode mMode = PreambleMode::Off;
  nsCString mPath{"/"};
  uint32_t mMaxAssets = 0;
  uint32_t mMaxBytes = 0;
};

enum class RuntimeLogMode : uint8_t { Disabled, Console, File };

struct Config final {
  nsTArray<ListenerConfig> mListeners;
  nsTArray<UpstreamProxyConfig> mProxies;
  Maybe<HostResolverRule> mHostResolverRule;
  nsTArray<ExtraHeader> mExtraHeaders;
  PreambleConfig mPreamble;
  bool mOuterSessionGate = false;
  bool mNoPostQuantum = false;
  RuntimeLogMode mLogMode = RuntimeLogMode::Disabled;
  nsCString mLogPath;
};

class ProfileDirectory final {
 public:
  ProfileDirectory() = default;
  ~ProfileDirectory();

  ProfileDirectory(const ProfileDirectory&) = delete;
  ProfileDirectory& operator=(const ProfileDirectory&) = delete;

  const nsCString& Path() const { return mPath; }
  bool IsTemporary() const { return mTemporary; }

 private:
  friend nsresult ResolveAndCreateProfile(ProfileDirectory& aProfile,
                                          nsACString& aError);

  nsCString mPath;
  bool mTemporary = false;
};

nsresult ParseConfig(const nsACString& aJson, Config& aConfig,
                     nsACString& aError);
nsresult LoadConfigFile(const nsACString& aPath, Config& aConfig,
                        nsACString& aError);
nsresult ResolveAndCreateProfile(ProfileDirectory& aProfile,
                                 nsACString& aError);

}  // namespace mozilla::naivefox

#endif
