/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "Config.h"

#include <arpa/inet.h>

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>

#include "mozilla/Span.h"
#include "mozilla/Utf8.h"
#include "nsError.h"

namespace mozilla::naivefox {

namespace {

constexpr size_t kMaximumConfigSize = 1024 * 1024;

nsresult Fail(nsACString& aError, const char* aMessage,
              nsresult aResult = NS_ERROR_INVALID_ARG) {
  aError.Assign(aMessage);
  return aResult;
}

bool IsWhitespace(char aChar) {
  return aChar == ' ' || aChar == '\t' || aChar == '\r' || aChar == '\n';
}

bool IsHex(char aChar) {
  return (aChar >= '0' && aChar <= '9') || (aChar >= 'a' && aChar <= 'f') ||
         (aChar >= 'A' && aChar <= 'F');
}

uint8_t HexValue(char aChar) {
  if (aChar >= '0' && aChar <= '9') {
    return aChar - '0';
  }
  if (aChar >= 'a' && aChar <= 'f') {
    return aChar - 'a' + 10;
  }
  return aChar - 'A' + 10;
}

bool AppendCodePoint(uint32_t aCodePoint, nsACString& aOutput) {
  if (aCodePoint == 0 || aCodePoint > 0x10ffff ||
      (aCodePoint >= 0xd800 && aCodePoint <= 0xdfff)) {
    return false;
  }
  if (aCodePoint <= 0x7f) {
    aOutput.Append(static_cast<char>(aCodePoint));
  } else if (aCodePoint <= 0x7ff) {
    aOutput.Append(static_cast<char>(0xc0 | (aCodePoint >> 6)));
    aOutput.Append(static_cast<char>(0x80 | (aCodePoint & 0x3f)));
  } else if (aCodePoint <= 0xffff) {
    aOutput.Append(static_cast<char>(0xe0 | (aCodePoint >> 12)));
    aOutput.Append(static_cast<char>(0x80 | ((aCodePoint >> 6) & 0x3f)));
    aOutput.Append(static_cast<char>(0x80 | (aCodePoint & 0x3f)));
  } else {
    aOutput.Append(static_cast<char>(0xf0 | (aCodePoint >> 18)));
    aOutput.Append(static_cast<char>(0x80 | ((aCodePoint >> 12) & 0x3f)));
    aOutput.Append(static_cast<char>(0x80 | ((aCodePoint >> 6) & 0x3f)));
    aOutput.Append(static_cast<char>(0x80 | (aCodePoint & 0x3f)));
  }
  return true;
}

bool IsDomainName(const nsACString& aHost) {
  if (aHost.IsEmpty() || aHost.Length() > 253 || aHost.First() == '.' ||
      aHost.Last() == '.') {
    return false;
  }
  size_t labelLength = 0;
  bool labelStartsWithHyphen = false;
  char previous = 0;
  for (size_t i = 0; i < aHost.Length(); ++i) {
    const char value = aHost.CharAt(i);
    if (value == '.') {
      if (labelLength == 0 || labelStartsWithHyphen || previous == '-') {
        return false;
      }
      labelLength = 0;
      labelStartsWithHyphen = false;
      previous = value;
      continue;
    }
    if (!((value >= '0' && value <= '9') || (value >= 'A' && value <= 'Z') ||
          (value >= 'a' && value <= 'z') || value == '-')) {
      return false;
    }
    if (labelLength == 0) {
      labelStartsWithHyphen = value == '-';
    }
    if (++labelLength > 63) {
      return false;
    }
    previous = value;
  }
  return labelLength != 0 && !labelStartsWithHyphen && previous != '-';
}

class JsonParser final {
 public:
  JsonParser(const nsACString& aInput, nsACString& aError)
      : mInput(aInput), mError(aError) {}

  nsresult Parse(Config& aConfig) {
    if (!mozilla::IsUtf8(Span(mInput.BeginReading(), mInput.Length()))) {
      return Error("config must be valid UTF-8");
    }
    SkipWhitespace();
    if (!Consume('{')) {
      return Error("config root must be an object");
    }
    SkipWhitespace();
    if (Consume('}')) {
      return Error("config requires listen and proxy fields");
    }

    Config parsed;
    bool sawListen = false;
    bool sawProxy = false;
    bool sawLog = false;
    while (true) {
      nsAutoCString key;
      MOZ_TRY(ParseString(key, "object field name must be a string"));
      SkipWhitespace();
      if (!Consume(':')) {
        return Error("expected ':' after config field name");
      }
      SkipWhitespace();
      if (key.EqualsLiteral("listen")) {
        if (sawListen) {
          return Error("duplicate listen field");
        }
        sawListen = true;
        MOZ_TRY(ParseListeners(parsed.mListeners));
      } else if (key.EqualsLiteral("proxy")) {
        if (sawProxy) {
          return Error("duplicate proxy field");
        }
        sawProxy = true;
        MOZ_TRY(ParseProxies(parsed.mProxies));
      } else if (key.EqualsLiteral("log")) {
        if (sawLog) {
          return Error("duplicate log field");
        }
        sawLog = true;
        MOZ_TRY(ParseString(parsed.mLogPath, "log must be a string"));
        parsed.mLogMode = parsed.mLogPath.IsEmpty() ? RuntimeLogMode::Console
                                                    : RuntimeLogMode::File;
      } else {
        return Error("unsupported config field");
      }

      SkipWhitespace();
      if (Consume('}')) {
        break;
      }
      if (!Consume(',')) {
        return Error("expected ',' or '}' after config field");
      }
      SkipWhitespace();
    }
    SkipWhitespace();
    if (mPosition != mInput.Length()) {
      return Error("unexpected data after config object");
    }
    if (!sawListen || parsed.mListeners.IsEmpty()) {
      return Error("config requires a non-empty listen field");
    }
    if (!sawProxy) {
      return Error("config requires a proxy field");
    }
    if (parsed.mProxies.Length() >= 2 &&
        parsed.mProxies.Length() != parsed.mListeners.Length()) {
      return Error("listen addresses do not match multiple proxies");
    }
    aConfig = std::move(parsed);
    return NS_OK;
  }

 private:
  nsresult Error(const char* aMessage) { return Fail(mError, aMessage); }

  void SkipWhitespace() {
    while (mPosition < mInput.Length() &&
           IsWhitespace(mInput.CharAt(mPosition))) {
      ++mPosition;
    }
  }

  bool Consume(char aExpected) {
    if (mPosition >= mInput.Length() || mInput.CharAt(mPosition) != aExpected) {
      return false;
    }
    ++mPosition;
    return true;
  }

  nsresult ParseHexQuad(uint16_t& aValue) {
    if (mInput.Length() - mPosition < 4) {
      return Error("incomplete JSON unicode escape");
    }
    aValue = 0;
    for (size_t i = 0; i < 4; ++i) {
      const char value = mInput.CharAt(mPosition++);
      if (!IsHex(value)) {
        return Error("invalid JSON unicode escape");
      }
      aValue = (aValue << 4) | HexValue(value);
    }
    return NS_OK;
  }

  nsresult ParseString(nsACString& aOutput, const char* aTypeError) {
    if (!Consume('"')) {
      return Error(aTypeError);
    }
    aOutput.Truncate();
    while (mPosition < mInput.Length()) {
      const unsigned char value = mInput.CharAt(mPosition++);
      if (value == '"') {
        return NS_OK;
      }
      if (value < 0x20) {
        return Error("unescaped control character in JSON string");
      }
      if (value != '\\') {
        aOutput.Append(static_cast<char>(value));
        continue;
      }
      if (mPosition == mInput.Length()) {
        return Error("incomplete JSON string escape");
      }
      const char escaped = mInput.CharAt(mPosition++);
      switch (escaped) {
        case '"':
        case '\\':
        case '/':
          aOutput.Append(escaped);
          break;
        case 'b':
          aOutput.Append('\b');
          break;
        case 'f':
          aOutput.Append('\f');
          break;
        case 'n':
          aOutput.Append('\n');
          break;
        case 'r':
          aOutput.Append('\r');
          break;
        case 't':
          aOutput.Append('\t');
          break;
        case 'u': {
          uint16_t high = 0;
          MOZ_TRY(ParseHexQuad(high));
          uint32_t codePoint = high;
          if (high >= 0xd800 && high <= 0xdbff) {
            if (mInput.Length() - mPosition < 6 ||
                mInput.CharAt(mPosition) != '\\' ||
                mInput.CharAt(mPosition + 1) != 'u') {
              return Error("incomplete JSON surrogate pair");
            }
            mPosition += 2;
            uint16_t low = 0;
            MOZ_TRY(ParseHexQuad(low));
            if (low < 0xdc00 || low > 0xdfff) {
              return Error("invalid JSON surrogate pair");
            }
            codePoint = 0x10000 + ((high - 0xd800) << 10) + (low - 0xdc00);
          }
          if (!AppendCodePoint(codePoint, aOutput)) {
            return Error("invalid JSON unicode code point");
          }
          break;
        }
        default:
          return Error("invalid JSON string escape");
      }
    }
    return Error("unterminated JSON string");
  }

  nsresult ParseListeners(nsTArray<ListenerConfig>& aListeners) {
    if (mPosition < mInput.Length() && mInput.CharAt(mPosition) == '"') {
      nsAutoCString value;
      MOZ_TRY(ParseString(value, "listen must be a string or array"));
      return AppendListener(value, aListeners);
    }
    if (!Consume('[')) {
      return Error("listen must be a string or array");
    }
    SkipWhitespace();
    if (Consume(']')) {
      return Error("listen array must not be empty");
    }
    while (true) {
      nsAutoCString value;
      MOZ_TRY(ParseString(value, "listen array entries must be strings"));
      MOZ_TRY(AppendListener(value, aListeners));
      SkipWhitespace();
      if (Consume(']')) {
        return NS_OK;
      }
      if (!Consume(',')) {
        return Error("expected ',' or ']' in listen array");
      }
      SkipWhitespace();
    }
  }

  nsresult AppendListener(const nsACString& aValue,
                          nsTArray<ListenerConfig>& aListeners) {
    ListenerConfig listener;
    MOZ_TRY(ParseListener(aValue, listener));
    for (const auto& existing : aListeners) {
      if (existing.mIPv6 == listener.mIPv6 &&
          existing.mHost.Equals(listener.mHost) &&
          existing.mPort == listener.mPort) {
        return Error("duplicate listener address");
      }
    }
    aListeners.AppendElement(std::move(listener));
    return NS_OK;
  }

  nsresult ParsePort(const nsACString& aText, uint16_t& aPort) {
    if (aText.IsEmpty()) {
      return Error("endpoint requires an explicit port");
    }
    uint32_t port = 0;
    for (size_t i = 0; i < aText.Length(); ++i) {
      const char value = aText.CharAt(i);
      if (value < '0' || value > '9') {
        return Error("endpoint port must be numeric");
      }
      port = port * 10 + (value - '0');
      if (port > 65535) {
        return Error("endpoint port is out of range");
      }
    }
    if (port == 0) {
      return Error("endpoint port is out of range");
    }
    aPort = static_cast<uint16_t>(port);
    return NS_OK;
  }

  nsresult ParseHostPort(const nsACString& aValue, bool aPortRequired,
                         nsACString& aHost, bool& aIPv6, uint16_t& aPort) {
    aHost.Truncate();
    aIPv6 = false;
    if (aValue.IsEmpty()) {
      return Error("endpoint host is empty");
    }
    nsAutoCString portText;
    if (aValue.First() == '[') {
      const int32_t close = aValue.FindChar(']');
      if (close <= 1) {
        return Error("invalid bracketed IPv6 endpoint");
      }
      aHost.Assign(Substring(aValue, 1, close - 1));
      in6_addr address{};
      if (inet_pton(AF_INET6, PromiseFlatCString(aHost).get(), &address) != 1) {
        return Error("invalid IPv6 endpoint");
      }
      aIPv6 = true;
      if (static_cast<size_t>(close + 1) == aValue.Length()) {
        if (aPortRequired) {
          return Error("endpoint requires an explicit port");
        }
        aPort = 443;
        return NS_OK;
      }
      if (aValue.CharAt(close + 1) != ':') {
        return Error("invalid data after IPv6 endpoint");
      }
      portText.Assign(Substring(aValue, close + 2));
    } else {
      const int32_t colon = aValue.RFindChar(':');
      if (colon < 0) {
        if (aPortRequired) {
          return Error("endpoint requires an explicit port");
        }
        aHost.Assign(aValue);
        aPort = 443;
        return NS_OK;
      }
      if (aValue.FindChar(':') != colon) {
        return Error("IPv6 endpoints must use brackets");
      }
      aHost.Assign(Substring(aValue, 0, colon));
      portText.Assign(Substring(aValue, colon + 1));
    }
    if (aHost.IsEmpty()) {
      return Error("endpoint host is empty");
    }
    return ParsePort(portText, aPort);
  }

  nsresult ParseListener(const nsACString& aValue, ListenerConfig& aListener) {
    const int32_t schemeEnd = aValue.Find("://"_ns);
    if (schemeEnd <= 0) {
      return Error("listener must be an absolute URI");
    }
    const nsDependentCSubstring scheme = Substring(aValue, 0, schemeEnd);
    if (scheme.EqualsLiteral("socks")) {
      aListener.mType = ListenerType::Socks5;
    } else if (scheme.EqualsLiteral("http")) {
      aListener.mType = ListenerType::HttpConnect;
    } else {
      return Error("unsupported listener scheme");
    }
    const nsDependentCSubstring endpoint = Substring(aValue, schemeEnd + 3);
    if (endpoint.FindChar('/') >= 0 || endpoint.FindChar('@') >= 0 ||
        endpoint.FindChar('?') >= 0 || endpoint.FindChar('#') >= 0) {
      return Error("listener URI must contain only host and port");
    }
    MOZ_TRY(ParseHostPort(endpoint, true, aListener.mHost, aListener.mIPv6,
                          aListener.mPort));
    in_addr ipv4Address{};
    if (!aListener.mIPv6 && !aListener.mHost.EqualsLiteral("localhost") &&
        inet_pton(AF_INET, PromiseFlatCString(aListener.mHost).get(),
                  &ipv4Address) != 1) {
      return Error("listener host must be an IPv4 or IPv6 address");
    }
    return NS_OK;
  }

  nsresult ParseProxies(nsTArray<UpstreamProxyConfig>& aProxies) {
    auto appendProxy = [&](const nsACString& aValue) -> nsresult {
      if (aValue.IsEmpty()) {
        return Error("proxy must not be empty");
      }
      if (aValue.FindChar(',') >= 0) {
        return Error("multi-hop proxy chains are not supported");
      }
      UpstreamProxyConfig proxy;
      MOZ_TRY(ParseProxy(aValue, proxy));
      aProxies.AppendElement(std::move(proxy));
      return NS_OK;
    };

    if (mPosition < mInput.Length() && mInput.CharAt(mPosition) == '"') {
      nsAutoCString value;
      MOZ_TRY(ParseString(value, "proxy must be a string or array"));
      return appendProxy(value);
    }
    if (!Consume('[')) {
      return Error("proxy must be a string or array");
    }
    SkipWhitespace();
    if (Consume(']')) {
      return Error("proxy array must not be empty");
    }
    while (true) {
      nsAutoCString value;
      MOZ_TRY(ParseString(value, "proxy array entries must be strings"));
      MOZ_TRY(appendProxy(value));
      SkipWhitespace();
      if (Consume(']')) {
        return NS_OK;
      }
      if (!Consume(',')) {
        return Error("expected ',' or ']' in proxy array");
      }
      SkipWhitespace();
    }
  }

  nsresult PercentDecode(const nsACString& aInput, nsACString& aOutput) {
    aOutput.Truncate();
    for (size_t i = 0; i < aInput.Length(); ++i) {
      const char value = aInput.CharAt(i);
      if (value == '%') {
        if (aInput.Length() - i < 3 || !IsHex(aInput.CharAt(i + 1)) ||
            !IsHex(aInput.CharAt(i + 2))) {
          return Error("invalid percent escape in proxy credentials");
        }
        const char decoded =
            static_cast<char>((HexValue(aInput.CharAt(i + 1)) << 4) |
                              HexValue(aInput.CharAt(i + 2)));
        if (decoded == 0 || decoded == '\r' || decoded == '\n') {
          return Error("invalid control character in proxy credentials");
        }
        aOutput.Append(decoded);
        i += 2;
      } else {
        if (static_cast<unsigned char>(value) < 0x20 || value == 0x7f) {
          return Error("invalid control character in proxy credentials");
        }
        aOutput.Append(value);
      }
    }
    if (!mozilla::IsUtf8(Span(aOutput.BeginReading(), aOutput.Length()))) {
      return Error("proxy credentials must be valid UTF-8");
    }
    return NS_OK;
  }

  nsresult ParseProxy(const nsACString& aValue, UpstreamProxyConfig& aProxy) {
    const int32_t schemeEnd = aValue.Find("://"_ns);
    if (schemeEnd <= 0) {
      return Error("proxy must be an absolute URI");
    }
    const nsDependentCSubstring scheme = Substring(aValue, 0, schemeEnd);
    if (scheme.EqualsLiteral("https")) {
      aProxy.mProtocol = ProxyProtocol::H2;
    } else if (scheme.EqualsLiteral("quic")) {
      aProxy.mProtocol = ProxyProtocol::H3;
    } else {
      return Error("unsupported proxy scheme");
    }
    const nsDependentCSubstring authority = Substring(aValue, schemeEnd + 3);
    if (authority.FindChar('/') >= 0 || authority.FindChar('?') >= 0 ||
        authority.FindChar('#') >= 0) {
      return Error("proxy URI must not contain a path, query, or fragment");
    }
    const int32_t at = authority.RFindChar('@');
    if (at <= 0 || authority.FindChar('@') != at) {
      return Error("proxy URI requires username and password");
    }
    const nsDependentCSubstring userInfo = Substring(authority, 0, at);
    const int32_t colon = userInfo.FindChar(':');
    if (colon <= 0) {
      return Error("proxy URI requires username and password");
    }
    MOZ_TRY(PercentDecode(Substring(userInfo, 0, colon), aProxy.mUser));
    MOZ_TRY(PercentDecode(Substring(userInfo, colon + 1), aProxy.mPassword));
    if (aProxy.mUser.IsEmpty() || aProxy.mPassword.IsEmpty() ||
        aProxy.mUser.FindChar(':') >= 0) {
      return Error("proxy URI contains invalid credentials");
    }

    nsAutoCString host;
    bool ipv6 = false;
    uint16_t port = 443;
    MOZ_TRY(
        ParseHostPort(Substring(authority, at + 1), false, host, ipv6, port));
    in_addr ipv4Address{};
    if (!ipv6 &&
        inet_pton(AF_INET, PromiseFlatCString(host).get(), &ipv4Address) != 1 &&
        !IsDomainName(host)) {
      return Error("proxy URI contains an invalid host");
    }
    aProxy.mUrl.AssignLiteral("https://");
    if (ipv6) {
      aProxy.mUrl.Append('[');
    }
    aProxy.mUrl.Append(host);
    if (ipv6) {
      aProxy.mUrl.Append(']');
    }
    aProxy.mUrl.AppendPrintf(":%u", static_cast<unsigned>(port));
    return NS_OK;
  }

  const nsACString& mInput;
  nsACString& mError;
  size_t mPosition = 0;
};

}  // namespace

nsresult ParseConfig(const nsACString& aJson, Config& aConfig,
                     nsACString& aError) {
  aError.Truncate();
  return JsonParser(aJson, aError).Parse(aConfig);
}

nsresult LoadConfigFile(const nsACString& aPath, Config& aConfig,
                        nsACString& aError) {
  std::unique_ptr<FILE, decltype(&std::fclose)> file(
      std::fopen(PromiseFlatCString(aPath).get(), "rb"), &std::fclose);
  if (!file) {
    return Fail(aError, "cannot open config file", NS_ERROR_FILE_NOT_FOUND);
  }
  if (std::fseek(file.get(), 0, SEEK_END) != 0) {
    return Fail(aError, "cannot inspect config file", NS_ERROR_FAILURE);
  }
  const long length = std::ftell(file.get());
  if (length < 0 || static_cast<size_t>(length) > kMaximumConfigSize) {
    return Fail(aError, "config file is too large", NS_ERROR_FILE_TOO_BIG);
  }
  if (std::fseek(file.get(), 0, SEEK_SET) != 0) {
    return Fail(aError, "cannot read config file", NS_ERROR_FAILURE);
  }
  nsCString json;
  if (!json.SetLength(static_cast<size_t>(length), fallible)) {
    return Fail(aError, "cannot allocate config buffer",
                NS_ERROR_OUT_OF_MEMORY);
  }
  if (length && std::fread(json.BeginWriting(), 1, length, file.get()) !=
                    static_cast<size_t>(length)) {
    return Fail(aError, "cannot read config file", NS_ERROR_FAILURE);
  }
  return ParseConfig(json, aConfig, aError);
}

nsresult ResolveAndCreateProfile(nsACString& aProfilePath, nsACString& aError) {
  const char* overridePath = std::getenv("NAIVEFOX_PROFILE");
  std::filesystem::path profile;
  if (overridePath && *overridePath) {
    profile = overridePath;
  } else {
    const char* stateHome = std::getenv("XDG_STATE_HOME");
    if (stateHome && *stateHome) {
      profile = std::filesystem::path(stateHome) / "naivefox" / "profile";
    } else {
      const char* home = std::getenv("HOME");
      if (!home || !*home) {
        return Fail(aError, "HOME is required when XDG_STATE_HOME is unset",
                    NS_ERROR_FILE_NOT_FOUND);
      }
      profile = std::filesystem::path(home) / ".local" / "state" / "naivefox" /
                "profile";
    }
  }

  std::error_code error;
  std::filesystem::create_directories(profile, error);
  if (error || !std::filesystem::is_directory(profile, error) || error) {
    return Fail(aError, "cannot create persistent profile directory",
                NS_ERROR_FILE_ACCESS_DENIED);
  }
  std::filesystem::permissions(profile, std::filesystem::perms::owner_all,
                               std::filesystem::perm_options::replace, error);
  if (error) {
    return Fail(aError, "cannot secure persistent profile directory",
                NS_ERROR_FILE_ACCESS_DENIED);
  }
  profile = std::filesystem::absolute(profile, error);
  if (error) {
    return Fail(aError, "cannot resolve persistent profile directory",
                NS_ERROR_FILE_NOT_FOUND);
  }
  const std::string nativeProfile = profile.string();
  aProfilePath.Assign(nativeProfile.c_str(), nativeProfile.length());
  return NS_OK;
}

}  // namespace mozilla::naivefox
