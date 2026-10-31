/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_net_NeckoChannelParams_h
#define mozilla_net_NeckoChannelParams_h

#include <cstdint>

#include "mozilla/BasePrincipal.h"
#include "nsICacheInfoChannel.h"
#include "nsString.h"
#include "nsTArray.h"

namespace mozilla::net {

class PreferredAlternativeDataTypeParams final {
 public:
  using DeliveryType =
      nsICacheInfoChannel::PreferredAlternativeDataDeliveryType;
  PreferredAlternativeDataTypeParams() = default;
  PreferredAlternativeDataTypeParams(
      nsCString aType, nsCString aContentType, DeliveryType aDeliverAltData)
      : mType(std::move(aType)),
        mContentType(std::move(aContentType)),
        mDeliverAltData(aDeliverAltData) {}
  const nsCString& type() const { return mType; }
  const nsCString& contentType() const { return mContentType; }
  DeliveryType deliverAltData() const {
    return mDeliverAltData;
  }

 private:
  nsCString mType;
  nsCString mContentType;
  DeliveryType mDeliverAltData = static_cast<DeliveryType>(0);
};

class EarlyHintConnectArgs final {};

// Parent-only equivalents of the small data records used by common Necko
// code.  Full Firefox generates these records from NeckoChannelParams.ipdlh;
// NaiveFox has no Necko child/socket process and therefore needs neither IPC
// serializers nor the browser-only types carried by that IPDL file.

class ProxyInfoCloneArgs final {
 public:
#define NF_FIELD(type, name)                 \
  type& name() { return m_##name; }          \
  const type& name() const { return m_##name; }
  NF_FIELD(nsCString, type)
  NF_FIELD(nsCString, host)
  NF_FIELD(int32_t, port)
  NF_FIELD(nsCString, masqueTemplate)
  NF_FIELD(nsCString, alpn)
  NF_FIELD(nsCString, username)
  NF_FIELD(nsCString, password)
  NF_FIELD(uint32_t, flags)
  NF_FIELD(uint32_t, timeout)
  NF_FIELD(uint32_t, resolveFlags)
  NF_FIELD(nsCString, proxyAuthorizationHeader)
  NF_FIELD(nsCString, connectionIsolationKey)
#undef NF_FIELD

 private:
  nsCString m_type;
  nsCString m_host;
  int32_t m_port = -1;
  nsCString m_masqueTemplate;
  nsCString m_alpn;
  nsCString m_username;
  nsCString m_password;
  uint32_t m_flags = 0;
  uint32_t m_timeout = 0;
  uint32_t m_resolveFlags = 0;
  nsCString m_proxyAuthorizationHeader;
  nsCString m_connectionIsolationKey;
};

class CookieStruct final {
 public:
  CookieStruct() = default;
  CookieStruct(nsCString aName, nsCString aValue, nsCString aHost,
               nsCString aPath, int64_t aExpiryInMSec,
               int64_t aLastAccessedInUSec, int64_t aCreationTimeInUSec,
               int64_t aUpdateTimeInUSec, bool aIsHttpOnly, bool aIsSession,
               bool aIsSecure, bool aIsPartitioned, int32_t aSameSite,
               uint8_t aSchemeMap)
      : m_name(std::move(aName)),
        m_value(std::move(aValue)),
        m_host(std::move(aHost)),
        m_path(std::move(aPath)),
        m_expiryInMSec(aExpiryInMSec),
        m_lastAccessedInUSec(aLastAccessedInUSec),
        m_creationTimeInUSec(aCreationTimeInUSec),
        m_updateTimeInUSec(aUpdateTimeInUSec),
        m_isHttpOnly(aIsHttpOnly),
        m_isSession(aIsSession),
        m_isSecure(aIsSecure),
        m_isPartitioned(aIsPartitioned),
        m_sameSite(aSameSite),
        m_schemeMap(aSchemeMap) {}

#define NF_FIELD(type, name)                 \
  type& name() { return m_##name; }          \
  const type& name() const { return m_##name; }
  NF_FIELD(nsCString, name)
  NF_FIELD(nsCString, value)
  NF_FIELD(nsCString, host)
  NF_FIELD(nsCString, path)
  NF_FIELD(int64_t, expiryInMSec)
  NF_FIELD(int64_t, lastAccessedInUSec)
  NF_FIELD(int64_t, creationTimeInUSec)
  NF_FIELD(int64_t, updateTimeInUSec)
  NF_FIELD(bool, isHttpOnly)
  NF_FIELD(bool, isSession)
  NF_FIELD(bool, isSecure)
  NF_FIELD(bool, isPartitioned)
  NF_FIELD(int32_t, sameSite)
  NF_FIELD(uint8_t, schemeMap)
#undef NF_FIELD

 private:
  nsCString m_name;
  nsCString m_value;
  nsCString m_host;
  nsCString m_path;
  int64_t m_expiryInMSec = 0;
  int64_t m_lastAccessedInUSec = 0;
  int64_t m_creationTimeInUSec = 0;
  int64_t m_updateTimeInUSec = 0;
  bool m_isHttpOnly = false;
  bool m_isSession = false;
  bool m_isSecure = false;
  bool m_isPartitioned = false;
  int32_t m_sameSite = 0;
  uint8_t m_schemeMap = 0;
};

class CookieStructTable final {
 public:
  OriginAttributes& attrs() { return mAttrs; }
  const OriginAttributes& attrs() const { return mAttrs; }
  nsTArray<CookieStruct>& cookies() { return mCookies; }
  const nsTArray<CookieStruct>& cookies() const { return mCookies; }

 private:
  OriginAttributes mAttrs;
  nsTArray<CookieStruct> mCookies;
};

}  // namespace mozilla::net

#endif  // mozilla_net_NeckoChannelParams_h
