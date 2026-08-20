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

class TransactionObserverResult final {
 public:
  bool& versionOk() { return mVersionOk; }
  bool& authOk() { return mAuthOk; }
  nsresult& closeReason() { return mCloseReason; }
  const bool& versionOk() const { return mVersionOk; }
  const bool& authOk() const { return mAuthOk; }
  const nsresult& closeReason() const { return mCloseReason; }

 private:
  bool mVersionOk = false;
  bool mAuthOk = false;
  nsresult mCloseReason = NS_OK;
};

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

class HttpConnectionInfoCloneArgs final {
 public:
#define NF_FIELD(type, name)                 \
  type& name() { return m_##name; }          \
  const type& name() const { return m_##name; }
  NF_FIELD(nsCString, host)
  NF_FIELD(int32_t, port)
  NF_FIELD(nsCString, npnToken)
  NF_FIELD(nsCString, username)
  NF_FIELD(OriginAttributes, originAttributes)
  NF_FIELD(bool, endToEndSSL)
  NF_FIELD(nsCString, routedHost)
  NF_FIELD(int32_t, routedPort)
  NF_FIELD(bool, anonymous)
  NF_FIELD(bool, aPrivate)
  NF_FIELD(bool, insecureScheme)
  NF_FIELD(bool, noSpdy)
  NF_FIELD(bool, beConservative)
  NF_FIELD(bool, bypassProxy)
  NF_FIELD(bool, anonymousAllowClientCert)
  NF_FIELD(bool, fallbackConnection)
  NF_FIELD(uint32_t, tlsFlags)
  NF_FIELD(bool, isolated)
  NF_FIELD(bool, isTrrServiceChannel)
  NF_FIELD(uint8_t, trrMode)
  NF_FIELD(bool, isIPv4Disabled)
  NF_FIELD(bool, isIPv6Disabled)
  NF_FIELD(bool, isHttp3Disabled)
  NF_FIELD(nsCString, topWindowOrigin)
  NF_FIELD(bool, isHttp3)
  NF_FIELD(bool, webTransport)
  NF_FIELD(uint64_t, webTransportId)
  NF_FIELD(bool, hasIPHintAddress)
  NF_FIELD(bool, http3Only)
  NF_FIELD(nsCString, echConfig)
  NF_FIELD(bool, happyEyeballsEnabled)
  NF_FIELD(nsTArray<ProxyInfoCloneArgs>, proxyInfo)
#undef NF_FIELD

 private:
  nsCString m_host;
  int32_t m_port = -1;
  nsCString m_npnToken;
  nsCString m_username;
  OriginAttributes m_originAttributes;
  bool m_endToEndSSL = false;
  nsCString m_routedHost;
  int32_t m_routedPort = -1;
  bool m_anonymous = false;
  bool m_aPrivate = false;
  bool m_insecureScheme = false;
  bool m_noSpdy = false;
  bool m_beConservative = false;
  bool m_bypassProxy = false;
  bool m_anonymousAllowClientCert = false;
  bool m_fallbackConnection = false;
  uint32_t m_tlsFlags = 0;
  bool m_isolated = false;
  bool m_isTrrServiceChannel = false;
  uint8_t m_trrMode = 0;
  bool m_isIPv4Disabled = false;
  bool m_isIPv6Disabled = false;
  bool m_isHttp3Disabled = false;
  nsCString m_topWindowOrigin;
  bool m_isHttp3 = false;
  bool m_webTransport = false;
  uint64_t m_webTransportId = 0;
  bool m_hasIPHintAddress = false;
  bool m_http3Only = false;
  nsCString m_echConfig;
  bool m_happyEyeballsEnabled = false;
  nsTArray<ProxyInfoCloneArgs> m_proxyInfo;
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

class HttpActivity final {
 public:
  HttpActivity() = default;
  HttpActivity(nsCString aHost, int32_t aPort, bool aEndToEndSSL)
      : mHost(std::move(aHost)),
        mPort(aPort),
        mEndToEndSSL(aEndToEndSSL) {}
  const nsCString& host() const { return mHost; }
  int32_t port() const { return mPort; }
  bool endToEndSSL() const { return mEndToEndSSL; }

 private:
  nsCString mHost;
  int32_t mPort = -1;
  bool mEndToEndSSL = false;
};

class HttpConnectionActivity final {
 public:
  HttpConnectionActivity() = default;
  HttpConnectionActivity(nsCString aKey, nsCString aHost, int32_t aPort,
                         bool aSSL, bool aHasECH, bool aIsHttp3)
      : mKey(std::move(aKey)),
        mHost(std::move(aHost)),
        mPort(aPort),
        mSSL(aSSL),
        mHasECH(aHasECH),
        mIsHttp3(aIsHttp3) {}
  const nsCString& connInfoKey() const { return mKey; }
  const nsCString& host() const { return mHost; }
  int32_t port() const { return mPort; }
  bool ssl() const { return mSSL; }
  bool hasECH() const { return mHasECH; }
  bool isHttp3() const { return mIsHttp3; }

 private:
  nsCString mKey;
  nsCString mHost;
  int32_t mPort = -1;
  bool mSSL = false;
  bool mHasECH = false;
  bool mIsHttp3 = false;
};

class HttpActivityArgs final {
 public:
  enum Type { Tuint64_t, THttpActivity, THttpConnectionActivity };
  explicit HttpActivityArgs(uint64_t aId) : mType(Tuint64_t), mId(aId) {}
  HttpActivityArgs(HttpActivity aActivity)
      : mType(THttpActivity), mActivity(std::move(aActivity)) {}
  HttpActivityArgs(HttpConnectionActivity aActivity)
      : mType(THttpConnectionActivity),
        mConnectionActivity(std::move(aActivity)) {}
  Type type() const { return mType; }
  uint64_t get_uint64_t() const { return mId; }
  const HttpActivity& get_HttpActivity() const { return mActivity; }
  const HttpConnectionActivity& get_HttpConnectionActivity() const {
    return mConnectionActivity;
  }

 private:
  Type mType;
  uint64_t mId = 0;
  HttpActivity mActivity;
  HttpConnectionActivity mConnectionActivity;
};

}  // namespace mozilla::net

#endif  // mozilla_net_NeckoChannelParams_h
