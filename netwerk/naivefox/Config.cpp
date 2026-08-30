/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "Config.h"

#ifdef XP_WIN
#  include <winsock2.h>
#  include <ws2tcpip.h>
#else
#  include <arpa/inet.h>
#endif

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <memory>
#include <string>

#include "mozilla/Span.h"
#include "mozilla/Utf8.h"
#include "nsError.h"

namespace mozilla::naivefox {

namespace {

constexpr size_t kMaximumConfigSize = 1024 * 1024;

int ParseNetworkAddress(int aFamily, const char* aText, void* aAddress) {
#ifdef XP_WIN
  return InetPtonA(aFamily, aText, aAddress);
#else
  return inet_pton(aFamily, aText, aAddress);
#endif
}

nsresult Fail(nsACString& aError, const char* aMessage,
              nsresult aResult = NS_ERROR_INVALID_ARG) {
  aError.Assign(aMessage);
  return aResult;
}

nsresult CreatePersistentProfile(const std::filesystem::path& aPath,
                                 nsACString& aProfilePath, nsACString& aError) {
  std::error_code error;
  std::filesystem::create_directories(aPath, error);
  if (error || !std::filesystem::is_directory(aPath, error) || error) {
    return Fail(aError, "cannot create persistent profile directory",
                NS_ERROR_FILE_ACCESS_DENIED);
  }
  std::filesystem::permissions(aPath, std::filesystem::perms::owner_all,
                               std::filesystem::perm_options::replace, error);
  if (error) {
    return Fail(aError, "cannot secure persistent profile directory",
                NS_ERROR_FILE_ACCESS_DENIED);
  }
  const std::filesystem::path absolute =
      std::filesystem::absolute(aPath, error);
  if (error) {
    return Fail(aError, "cannot resolve persistent profile directory",
                NS_ERROR_FILE_NOT_FOUND);
  }
  const std::string native = absolute.string();
  aProfilePath.Assign(native.c_str(), native.length());
  return NS_OK;
}

bool TryCreateTemporaryProfile(const std::filesystem::path& aBase,
                               nsACString& aProfilePath) {
  if (aBase.empty()) {
    return false;
  }
  std::error_code error;
  const std::filesystem::path absoluteBase =
      std::filesystem::absolute(aBase, error);
  if (error || !std::filesystem::is_directory(absoluteBase, error) || error) {
    return false;
  }

#ifdef XP_WIN
  static std::atomic<uint64_t> counter{0};
  const uint64_t stamp = static_cast<uint64_t>(
      std::chrono::steady_clock::now().time_since_epoch().count());
  std::filesystem::path profile;
  for (uint32_t attempt = 0; attempt < 100; ++attempt) {
    profile = absoluteBase /
              ("naivefox-profile-" + std::to_string(stamp) + "-" +
               std::to_string(counter.fetch_add(1, std::memory_order_relaxed)));
    error.clear();
    if (std::filesystem::create_directory(profile, error)) {
      break;
    }
    if (error && error != std::make_error_code(std::errc::file_exists)) {
      return false;
    }
    profile.clear();
  }
  if (profile.empty()) {
    return false;
  }
#else
  std::string name = (absoluteBase / "naivefox-profile-XXXXXX").string();
  name.push_back('\0');
  char* created = ::mkdtemp(name.data());
  if (!created) {
    return false;
  }

  const std::filesystem::path profile(created);
#endif
  std::filesystem::permissions(profile, std::filesystem::perms::owner_all,
                               std::filesystem::perm_options::replace, error);
  if (error) {
    std::filesystem::remove_all(profile, error);
    return false;
  }
  const std::string native = profile.string();
  aProfilePath.Assign(native.c_str(), native.length());
  return true;
}

nsresult CreateTemporaryProfile(nsACString& aProfilePath, nsACString& aError) {
  const char* runtimeDirectory = std::getenv("XDG_RUNTIME_DIR");
  if (runtimeDirectory && *runtimeDirectory &&
      std::filesystem::path(runtimeDirectory).is_absolute() &&
      TryCreateTemporaryProfile(runtimeDirectory, aProfilePath)) {
    return NS_OK;
  }

  std::error_code error;
  const std::filesystem::path temporary =
      std::filesystem::temp_directory_path(error);
  if (!error && TryCreateTemporaryProfile(temporary, aProfilePath)) {
    return NS_OK;
  }
  if (temporary != std::filesystem::path("/tmp") &&
      TryCreateTemporaryProfile("/tmp", aProfilePath)) {
    return NS_OK;
  }
  return Fail(aError, "cannot create temporary profile directory",
              NS_ERROR_FILE_ACCESS_DENIED);
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

bool IsHostOrAddress(const nsACString& aHost) {
  if (aHost.IsEmpty()) {
    return false;
  }
  in_addr ipv4Address{};
  if (ParseNetworkAddress(AF_INET, PromiseFlatCString(aHost).get(),
                          &ipv4Address) == 1) {
    return true;
  }
  in6_addr ipv6Address{};
  return ParseNetworkAddress(AF_INET6, PromiseFlatCString(aHost).get(),
                             &ipv6Address) == 1 ||
         IsDomainName(aHost);
}

bool IsHeaderTokenCharacter(char aValue) {
  if ((aValue >= '0' && aValue <= '9') || (aValue >= 'A' && aValue <= 'Z') ||
      (aValue >= 'a' && aValue <= 'z')) {
    return true;
  }
  switch (aValue) {
    case '!':
    case '#':
    case '$':
    case '%':
    case '&':
    case '\'':
    case '*':
    case '+':
    case '-':
    case '.':
    case '^':
    case '_':
    case '`':
    case '|':
    case '~':
      return true;
    default:
      return false;
  }
}

bool IsProtectedProxyConnectHeader(const nsACString& aName) {
  return aName.LowerCaseEqualsLiteral("padding") ||
         aName.LowerCaseEqualsLiteral("host") ||
         aName.LowerCaseEqualsLiteral("connection") ||
         aName.LowerCaseEqualsLiteral("proxy-connection") ||
         aName.LowerCaseEqualsLiteral("keep-alive") ||
         aName.LowerCaseEqualsLiteral("transfer-encoding") ||
         aName.LowerCaseEqualsLiteral("te") ||
         aName.LowerCaseEqualsLiteral("trailer") ||
         aName.LowerCaseEqualsLiteral("upgrade") ||
         aName.LowerCaseEqualsLiteral("content-length") ||
         aName.LowerCaseEqualsLiteral("proxy-authorization") ||
         aName.LowerCaseEqualsLiteral("proxy-authenticate") ||
         aName.LowerCaseEqualsLiteral("alpn");
}

bool IsValidPreamblePath(const nsACString& aPath) {
  if (aPath.IsEmpty() || aPath.Length() > 2048 || aPath.First() != '/' ||
      (aPath.Length() >= 2 && aPath.CharAt(1) == '/')) {
    return false;
  }
  for (size_t index = 0; index < aPath.Length(); ++index) {
    const unsigned char value = aPath.CharAt(index);
    if (value <= 0x20 || value >= 0x7f || value == '#' || value == '\\') {
      return false;
    }
    if (value == '%' &&
        (aPath.Length() - index < 3 || !IsHex(aPath.CharAt(index + 1)) ||
         !IsHex(aPath.CharAt(index + 2)))) {
      return false;
    }
    if (value == '%') {
      index += 2;
    }
  }
  return true;
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
    bool sawHostResolverRules = false;
    bool sawExtraHeaders = false;
    bool sawNoPostQuantum = false;
    bool sawInsecureConcurrency = false;
    bool sawMaxConnections = false;
    bool sawPreamble = false;
    bool sawOuterSessionGate = false;
    bool sawDiagnosticFirstSocksTunnelUrgentStart = false;
    bool sawDiagnosticOptimisticLocalReply = false;
    bool sawDiagnosticH2FiniteExchanges = false;
    bool sawDiagnosticH2FiniteReadThrough = false;
    bool sawDiagnosticH2FiniteStreamUploads = false;
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
      } else if (key.EqualsLiteral("host-resolver-rules")) {
        if (sawHostResolverRules) {
          return Error("duplicate host-resolver-rules field");
        }
        sawHostResolverRules = true;
        nsAutoCString value;
        MOZ_TRY(ParseString(value, "host-resolver-rules must be a string"));
        HostResolverRule rule;
        MOZ_TRY(ParseHostResolverRule(value, rule));
        parsed.mHostResolverRule.emplace(std::move(rule));
      } else if (key.EqualsLiteral("extra-headers")) {
        if (sawExtraHeaders) {
          return Error("duplicate extra-headers field");
        }
        sawExtraHeaders = true;
        nsAutoCString value;
        MOZ_TRY(ParseString(value, "extra-headers must be a string"));
        MOZ_TRY(ParseExtraHeaders(value, parsed.mExtraHeaders));
      } else if (key.EqualsLiteral("no-post-quantum")) {
        if (sawNoPostQuantum) {
          return Error("duplicate no-post-quantum field");
        }
        sawNoPostQuantum = true;
        MOZ_TRY(ParseBoolean(parsed.mNoPostQuantum,
                             "no-post-quantum must be a boolean"));
      } else if (key.EqualsLiteral("max-connections")) {
        if (sawMaxConnections) {
          return Error("duplicate max-connections field");
        }
        sawMaxConnections = true;
        MOZ_TRY(ParseBoundedUnsignedInteger(
            parsed.mMaxConnections, std::numeric_limits<uint32_t>::max(),
            "max-connections must be a non-negative integer",
            "max-connections exceeds the supported range"));
      } else if (key.EqualsLiteral("preamble")) {
        if (sawPreamble) {
          return Error("duplicate preamble field");
        }
        sawPreamble = true;
        MOZ_TRY(ParsePreamble(parsed.mPreamble));
      } else if (key.EqualsLiteral("outer-session-gate")) {
        if (sawOuterSessionGate) {
          return Error("duplicate outer-session-gate field");
        }
        sawOuterSessionGate = true;
        MOZ_TRY(ParseBoolean(parsed.mOuterSessionGate,
                             "outer-session-gate must be a boolean"));
      } else if (key.EqualsLiteral(
                     "diagnostic-first-socks-tunnel-urgent-start")) {
        if (sawDiagnosticFirstSocksTunnelUrgentStart) {
          return Error(
              "duplicate diagnostic-first-socks-tunnel-urgent-start field");
        }
        sawDiagnosticFirstSocksTunnelUrgentStart = true;
        MOZ_TRY(ParseBoolean(
            parsed.mDiagnosticFirstSocksTunnelUrgentStart,
            "diagnostic-first-socks-tunnel-urgent-start must be a boolean"));
      } else if (key.EqualsLiteral("diagnostic-optimistic-local-reply")) {
        if (sawDiagnosticOptimisticLocalReply) {
          return Error("duplicate diagnostic-optimistic-local-reply field");
        }
        sawDiagnosticOptimisticLocalReply = true;
        MOZ_TRY(ParseBoolean(
            parsed.mDiagnosticOptimisticLocalReply,
            "diagnostic-optimistic-local-reply must be a boolean"));
      } else if (key.EqualsLiteral("diagnostic-h2-finite-exchanges")) {
        if (sawDiagnosticH2FiniteExchanges) {
          return Error("duplicate diagnostic-h2-finite-exchanges field");
        }
        sawDiagnosticH2FiniteExchanges = true;
        MOZ_TRY(
            ParseBoolean(parsed.mDiagnosticH2FiniteExchanges,
                         "diagnostic-h2-finite-exchanges must be a boolean"));
      } else if (key.EqualsLiteral("diagnostic-h2-finite-read-through")) {
        if (sawDiagnosticH2FiniteReadThrough) {
          return Error("duplicate diagnostic-h2-finite-read-through field");
        }
        sawDiagnosticH2FiniteReadThrough = true;
        MOZ_TRY(ParseBoolean(
            parsed.mDiagnosticH2FiniteReadThrough,
            "diagnostic-h2-finite-read-through must be a boolean"));
      } else if (key.EqualsLiteral("diagnostic-h2-finite-stream-uploads")) {
        if (sawDiagnosticH2FiniteStreamUploads) {
          return Error("duplicate diagnostic-h2-finite-stream-uploads field");
        }
        sawDiagnosticH2FiniteStreamUploads = true;
        MOZ_TRY(ParseBoolean(
            parsed.mDiagnosticH2FiniteStreamUploads,
            "diagnostic-h2-finite-stream-uploads must be a boolean"));
      } else if (key.EqualsLiteral("insecure-concurrency")) {
        if (sawInsecureConcurrency) {
          return Error("duplicate insecure-concurrency field");
        }
        sawInsecureConcurrency = true;
        // NaiveProxy accepts this setting for compatibility, but NaiveFox
        // deliberately keeps connection pooling under Necko's control.
        MOZ_TRY(ParsePositiveCompatibilityInteger(
            "insecure-concurrency must be a positive integer"));
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
    if (!sawPreamble) {
      bool hasExplicitH2Proxy = false;
      bool hasExplicitH3Proxy = false;
      bool hasOnlySocksListeners = true;
      for (const auto& listener : parsed.mListeners) {
        if (listener.mType != ListenerType::Socks5) {
          hasOnlySocksListeners = false;
          break;
        }
      }
      for (const auto& proxy : parsed.mProxies) {
        if (proxy.mProtocol == ProxyProtocol::H2) {
          hasExplicitH2Proxy = true;
        } else if (proxy.mProtocol == ProxyProtocol::H3) {
          hasExplicitH3Proxy = true;
        }
      }
      if (hasExplicitH2Proxy || hasExplicitH3Proxy) {
        // SOCKS-only H2 uses the next-task first-buffer boundary that improved
        // both packets 17--32 and whole flow in the final paired campaign.
        // HTTP CONNECT and mixed listeners retain direct first-buffer
        // admission for H2.  Every H3 listener layout uses the retained
        // six-resource native-parser policy.  Explicit preamble and gate
        // fields remain authoritative.
        if (hasExplicitH2Proxy) {
          parsed.mPreamble.mH2Mode =
              Some(hasOnlySocksListeners
                       ? PreambleMode::DocumentFirstBufferTaskOverlap
                       : PreambleMode::DocumentFirstBufferOverlap);
        }
        if (hasExplicitH3Proxy) {
          parsed.mPreamble.mH3Mode =
              Some(PreambleMode::TreeNativeParserResourceCommittedOverlap);
        }
        parsed.mPreamble.mPath.AssignLiteral("/");
        const bool usesH3ResourceDefault = hasExplicitH3Proxy;
        parsed.mPreamble.mMaxAssets = usesH3ResourceDefault ? 6 : 0;
        parsed.mPreamble.mMaxBytes =
            usesH3ResourceDefault ? PreambleConfig::kMaximumBytes
                                  : PreambleConfig::kDefaultDocumentMaxBytes;
        parsed.mPreamble.mCacheResources = usesH3ResourceDefault;
        parsed.mImplicitPreambleGate = !sawOuterSessionGate;
      }
    }
    if (parsed.mDiagnosticH2FiniteStreamUploads &&
        !parsed.mDiagnosticH2FiniteReadThrough) {
      return Error("finite upload streaming requires receive read-through");
    }
    if (parsed.mDiagnosticH2FiniteReadThrough &&
        !parsed.mDiagnosticH2FiniteExchanges) {
      return Error(
          "finite read-through requires diagnostic-h2-finite-exchanges");
    }
    if (parsed.mDiagnosticH2FiniteExchanges) {
      for (const auto& proxy : parsed.mProxies) {
        if (proxy.mProtocol != ProxyProtocol::H2) {
          return Error("diagnostic-h2-finite-exchanges requires strict H2");
        }
      }
      if (!parsed.mExtraHeaders.IsEmpty() ||
          parsed.mDiagnosticOptimisticLocalReply ||
          parsed.mDiagnosticFirstSocksTunnelUrgentStart ||
          PreambleModeUsesNativeParser(
              parsed.mPreamble.ModeForProtocol(ProxyProtocol::H2))) {
        return Error("finite exchanges cannot combine with other diagnostics");
      }
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

  bool ConsumeLiteral(const char* aExpected) {
    const size_t length = std::strlen(aExpected);
    if (mInput.Length() - mPosition < length ||
        !Substring(mInput, mPosition, length).Equals(aExpected)) {
      return false;
    }
    mPosition += length;
    return true;
  }

  nsresult ParseBoolean(bool& aOutput, const char* aTypeError) {
    if (ConsumeLiteral("true")) {
      aOutput = true;
      return NS_OK;
    }
    if (ConsumeLiteral("false")) {
      aOutput = false;
      return NS_OK;
    }
    return Error(aTypeError);
  }

  nsresult ParseBoundedUnsignedInteger(uint32_t& aOutput, uint32_t aMaximum,
                                       const char* aTypeError,
                                       const char* aRangeError) {
    const size_t start = mPosition;
    if (mPosition == mInput.Length() || mInput.CharAt(mPosition) < '0' ||
        mInput.CharAt(mPosition) > '9') {
      return Error(aTypeError);
    }
    if (mInput.CharAt(mPosition) == '0') {
      ++mPosition;
      if (mPosition < mInput.Length() && mInput.CharAt(mPosition) >= '0' &&
          mInput.CharAt(mPosition) <= '9') {
        return Error(aTypeError);
      }
    } else {
      while (mPosition < mInput.Length() && mInput.CharAt(mPosition) >= '0' &&
             mInput.CharAt(mPosition) <= '9') {
        ++mPosition;
      }
    }
    if (mPosition < mInput.Length() &&
        !IsWhitespace(mInput.CharAt(mPosition)) &&
        mInput.CharAt(mPosition) != ',' && mInput.CharAt(mPosition) != '}') {
      return Error(aTypeError);
    }
    uint64_t parsed = 0;
    for (size_t index = start; index < mPosition; ++index) {
      const uint64_t digit = mInput.CharAt(index) - '0';
      if (parsed > (std::numeric_limits<uint64_t>::max() - digit) / 10) {
        return Error(aRangeError);
      }
      parsed = parsed * 10 + digit;
    }
    if (parsed > aMaximum) {
      return Error(aRangeError);
    }
    aOutput = static_cast<uint32_t>(parsed);
    return NS_OK;
  }

  nsresult ParsePreamble(PreambleConfig& aPreamble) {
    if (!Consume('{')) {
      return Error("preamble must be an object");
    }
    SkipWhitespace();
    if (Consume('}')) {
      return Error("preamble requires a mode field");
    }

    bool sawMode = false;
    bool sawH2Mode = false;
    bool sawH3Mode = false;
    bool sawPath = false;
    bool sawMaxAssets = false;
    bool sawMaxBytes = false;
    bool sawCacheResources = false;
    auto parseMode = [&](PreambleMode& aMode) -> nsresult {
      nsAutoCString mode;
      MOZ_TRY(ParseString(mode, "preamble mode must be a string"));
      if (mode.EqualsLiteral("off")) {
        aMode = PreambleMode::Off;
      } else if (mode.EqualsLiteral("document-complete") ||
                 mode.EqualsLiteral("root")) {
        aMode = PreambleMode::DocumentComplete;
      } else if (mode.EqualsLiteral("document-carrier-dispatch")) {
        aMode = PreambleMode::DocumentCarrierDispatch;
      } else if (mode.EqualsLiteral("document-cold-winner-handoff")) {
        aMode = PreambleMode::DocumentColdWinnerHandoff;
      } else if (mode.EqualsLiteral("document-native-cache-open")) {
        aMode = PreambleMode::DocumentNativeCacheOpen;
      } else if (mode.EqualsLiteral("document-native-channel-open")) {
        return Error(
            "document-native-channel-open was retired because the falsified "
            "diagnostic pulled the full Safe Browsing protobuf/Abseil graph "
            "into the lean product");
      } else if (mode.EqualsLiteral("document-handshake-confirmed")) {
        aMode = PreambleMode::DocumentHandshakeConfirmed;
      } else if (mode.EqualsLiteral("document-overlap")) {
        aMode = PreambleMode::DocumentOverlap;
      } else if (mode.EqualsLiteral("document-headers-task-overlap")) {
        aMode = PreambleMode::DocumentHeadersTaskOverlap;
      } else if (mode.EqualsLiteral("document-first-buffer-overlap")) {
        aMode = PreambleMode::DocumentFirstBufferOverlap;
      } else if (mode.EqualsLiteral("document-first-buffer-task-overlap")) {
        aMode = PreambleMode::DocumentFirstBufferTaskOverlap;
      } else if (mode.EqualsLiteral("document-start-overlap")) {
        aMode = PreambleMode::DocumentStartOverlap;
      } else if (mode.EqualsLiteral("document-start-task-overlap")) {
        aMode = PreambleMode::DocumentStartTaskOverlap;
      } else if (mode.EqualsLiteral("tree-complete") ||
                 mode.EqualsLiteral("tree")) {
        aMode = PreambleMode::TreeComplete;
      } else if (mode.EqualsLiteral("tree-overlap")) {
        aMode = PreambleMode::TreeOverlap;
      } else if (mode.EqualsLiteral("tree-early-overlap")) {
        aMode = PreambleMode::TreeEarlyOverlap;
      } else if (mode.EqualsLiteral("tree-root-overlap")) {
        aMode = PreambleMode::TreeRootOverlap;
      } else if (mode.EqualsLiteral("tree-resource-committed-overlap")) {
        aMode = PreambleMode::TreeResourceCommittedOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-resource-native-cache-committed-overlap")) {
        aMode = PreambleMode::TreeResourceNativeCacheCommittedOverlap;
      } else if (mode.EqualsLiteral("tree-native-parser-preload-overlap")) {
        aMode = PreambleMode::TreeNativeParserPreloadOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-document-start-overlap")) {
        aMode = PreambleMode::TreeNativeParserDocumentStartOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-document-start-resource-tree")) {
        aMode = PreambleMode::TreeNativeParserDocumentStartResourceTree;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-resource-committed-overlap")) {
        aMode = PreambleMode::TreeNativeParserResourceCommittedOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-document-start-navigation-stop")) {
        aMode = PreambleMode::TreeNativeParserDocumentStartNavigationStop;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-document-start-response-stop")) {
        aMode = PreambleMode::TreeNativeParserDocumentStartResponseStop;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-document-handoff-overlap")) {
        aMode = PreambleMode::TreeNativeParserDocumentHandoffOverlap;
      } else if (mode.EqualsLiteral("tree-native-parser-retarget-overlap")) {
        aMode = PreambleMode::TreeNativeParserRetargetOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-ipc-rendezvous-overlap")) {
        aMode = PreambleMode::TreeNativeParserIpcRendezvousOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-root-rendezvous-overlap")) {
        aMode = PreambleMode::TreeNativeParserRootRendezvousOverlap;
      } else if (mode.EqualsLiteral("tree-native-parser-process-overlap")) {
        aMode = PreambleMode::TreeNativeParserProcessOverlap;
      } else if (mode.EqualsLiteral(
                     "tree-native-parser-full-process-overlap")) {
        aMode = PreambleMode::TreeNativeParserFullProcessOverlap;
      } else {
        return Error("unsupported preamble mode");
      }
      return NS_OK;
    };
    while (true) {
      nsAutoCString key;
      MOZ_TRY(ParseString(key, "preamble field name must be a string"));
      SkipWhitespace();
      if (!Consume(':')) {
        return Error("expected ':' after preamble field name");
      }
      SkipWhitespace();
      if (key.EqualsLiteral("mode")) {
        if (sawMode) {
          return Error("duplicate preamble mode field");
        }
        sawMode = true;
        MOZ_TRY(parseMode(aPreamble.mMode));
      } else if (key.EqualsLiteral("h2-mode") || key.EqualsLiteral("h3-mode")) {
        bool& sawProtocolMode =
            key.EqualsLiteral("h2-mode") ? sawH2Mode : sawH3Mode;
        if (sawProtocolMode) {
          return Error("duplicate protocol preamble mode field");
        }
        sawProtocolMode = true;
        PreambleMode mode;
        MOZ_TRY(parseMode(mode));
        if (key.EqualsLiteral("h2-mode")) {
          aPreamble.mH2Mode = Some(mode);
        } else {
          aPreamble.mH3Mode = Some(mode);
        }
      } else if (key.EqualsLiteral("path")) {
        if (sawPath) {
          return Error("duplicate preamble path field");
        }
        sawPath = true;
        MOZ_TRY(ParseString(aPreamble.mPath, "preamble path must be a string"));
        if (!IsValidPreamblePath(aPreamble.mPath)) {
          return Error("preamble path must be an absolute origin-form path");
        }
      } else if (key.EqualsLiteral("max-assets")) {
        if (sawMaxAssets) {
          return Error("duplicate preamble max-assets field");
        }
        sawMaxAssets = true;
        MOZ_TRY(ParseBoundedUnsignedInteger(
            aPreamble.mMaxAssets, PreambleConfig::kMaximumAssets,
            "preamble max-assets must be a non-negative integer",
            "preamble max-assets exceeds the hard limit"));
      } else if (key.EqualsLiteral("max-bytes")) {
        if (sawMaxBytes) {
          return Error("duplicate preamble max-bytes field");
        }
        sawMaxBytes = true;
        MOZ_TRY(ParseBoundedUnsignedInteger(
            aPreamble.mMaxBytes, PreambleConfig::kMaximumBytes,
            "preamble max-bytes must be a non-negative integer",
            "preamble max-bytes exceeds the hard limit"));
      } else if (key.EqualsLiteral("cache-resources")) {
        if (sawCacheResources) {
          return Error("duplicate preamble cache-resources field");
        }
        sawCacheResources = true;
        MOZ_TRY(ParseBoolean(aPreamble.mCacheResources,
                             "preamble cache-resources must be a boolean"));
      } else {
        return Error("unsupported preamble field");
      }

      SkipWhitespace();
      if (Consume('}')) {
        break;
      }
      if (!Consume(',')) {
        return Error("expected ',' or '}' after preamble field");
      }
      SkipWhitespace();
    }

    if (!sawMode) {
      return Error("preamble requires a mode field");
    }
    const PreambleMode h2Mode = aPreamble.ModeForProtocol(ProxyProtocol::H2);
    const PreambleMode h3Mode = aPreamble.ModeForProtocol(ProxyProtocol::H3);
    if (h2Mode == PreambleMode::DocumentHandshakeConfirmed ||
        h2Mode == PreambleMode::DocumentCarrierDispatch ||
        h2Mode == PreambleMode::DocumentColdWinnerHandoff ||
        h2Mode == PreambleMode::DocumentNativeCacheOpen) {
      return Error("selected diagnostic preamble is only supported for H3");
    }
    if (h3Mode == PreambleMode::DocumentHandshakeConfirmed &&
        (!sawH3Mode ||
         aPreamble.mH3Mode != Some(PreambleMode::DocumentHandshakeConfirmed))) {
      return Error(
          "document-handshake-confirmed must be selected explicitly with "
          "h3-mode");
    }
    if (h3Mode == PreambleMode::DocumentCarrierDispatch &&
        (!sawH3Mode ||
         aPreamble.mH3Mode != Some(PreambleMode::DocumentCarrierDispatch))) {
      return Error(
          "document-carrier-dispatch must be selected explicitly with h3-mode");
    }
    if (h3Mode == PreambleMode::DocumentColdWinnerHandoff &&
        (!sawH3Mode ||
         aPreamble.mH3Mode != Some(PreambleMode::DocumentColdWinnerHandoff))) {
      return Error(
          "document-cold-winner-handoff must be selected explicitly with "
          "h3-mode");
    }
    if (h3Mode == PreambleMode::DocumentNativeCacheOpen &&
        (!sawH3Mode ||
         aPreamble.mH3Mode != Some(PreambleMode::DocumentNativeCacheOpen))) {
      return Error(
          "document-native-cache-open must be selected explicitly with "
          "h3-mode");
    }
    if (h3Mode == PreambleMode::TreeResourceCommittedOverlap &&
        (!sawH3Mode || aPreamble.mH3Mode !=
                           Some(PreambleMode::TreeResourceCommittedOverlap))) {
      return Error(
          "tree-resource-committed-overlap must be selected explicitly with "
          "h3-mode");
    }
    if (h3Mode == PreambleMode::TreeResourceNativeCacheCommittedOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeResourceNativeCacheCommittedOverlap))) {
      return Error(
          "tree-resource-native-cache-committed-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserPreloadOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserPreloadOverlap))) {
      return Error(
          "tree-native-parser-preload-overlap must be selected explicitly "
          "with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentStartOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartOverlap))) {
      return Error(
          "tree-native-parser-document-start-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h2Mode == PreambleMode::TreeNativeParserDocumentStartOverlap &&
        (!sawH2Mode ||
         aPreamble.mH2Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartOverlap))) {
      return Error(
          "tree-native-parser-document-start-overlap must be selected "
          "explicitly with h2-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentStartResourceTree &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartResourceTree))) {
      return Error(
          "tree-native-parser-document-start-resource-tree must be selected "
          "explicitly with h3-mode");
    }
    if (h2Mode == PreambleMode::TreeNativeParserDocumentStartResourceTree &&
        (!sawH2Mode ||
         aPreamble.mH2Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartResourceTree))) {
      return Error(
          "tree-native-parser-document-start-resource-tree must be selected "
          "explicitly with h2-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserResourceCommittedOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserResourceCommittedOverlap))) {
      return Error(
          "tree-native-parser-resource-committed-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h2Mode == PreambleMode::TreeNativeParserResourceCommittedOverlap &&
        (!sawH2Mode ||
         aPreamble.mH2Mode !=
             Some(PreambleMode::TreeNativeParserResourceCommittedOverlap))) {
      return Error(
          "tree-native-parser-resource-committed-overlap must be selected "
          "explicitly with h2-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentStartNavigationStop &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartNavigationStop))) {
      return Error(
          "tree-native-parser-document-start-navigation-stop must be "
          "selected explicitly with h3-mode");
    }
    if (h2Mode == PreambleMode::TreeNativeParserDocumentStartNavigationStop &&
        (!sawH2Mode ||
         aPreamble.mH2Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartNavigationStop))) {
      return Error(
          "tree-native-parser-document-start-navigation-stop must be "
          "selected explicitly with h2-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentStartResponseStop &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserDocumentStartResponseStop))) {
      return Error(
          "tree-native-parser-document-start-response-stop must be "
          "selected explicitly with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentHandoffOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserDocumentHandoffOverlap))) {
      return Error(
          "tree-native-parser-document-handoff-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserRetargetOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserRetargetOverlap))) {
      return Error(
          "tree-native-parser-retarget-overlap must be selected explicitly "
          "with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserIpcRendezvousOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserIpcRendezvousOverlap))) {
      return Error(
          "tree-native-parser-ipc-rendezvous-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserRootRendezvousOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserRootRendezvousOverlap))) {
      return Error(
          "tree-native-parser-root-rendezvous-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserProcessOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserProcessOverlap))) {
      return Error(
          "tree-native-parser-process-overlap must be selected explicitly "
          "with h3-mode");
    }
    if (h3Mode == PreambleMode::TreeNativeParserFullProcessOverlap &&
        (!sawH3Mode ||
         aPreamble.mH3Mode !=
             Some(PreambleMode::TreeNativeParserFullProcessOverlap))) {
      return Error(
          "tree-native-parser-full-process-overlap must be selected "
          "explicitly with h3-mode");
    }
    if (h2Mode == PreambleMode::TreeResourceCommittedOverlap ||
        h2Mode == PreambleMode::TreeResourceNativeCacheCommittedOverlap ||
        (PreambleModeUsesNativeParser(h2Mode) &&
         h2Mode != PreambleMode::TreeNativeParserDocumentStartOverlap &&
         h2Mode != PreambleMode::TreeNativeParserDocumentStartResourceTree &&
         h2Mode != PreambleMode::TreeNativeParserResourceCommittedOverlap &&
         h2Mode != PreambleMode::TreeNativeParserDocumentStartNavigationStop)) {
      return Error("selected resource-committed preamble is H3-only");
    }
    const bool anyActive =
        h2Mode != PreambleMode::Off || h3Mode != PreambleMode::Off;
    const bool anyTree =
        PreambleModeUsesResources(h2Mode) || PreambleModeUsesResources(h3Mode);
    if (!anyActive) {
      if (sawPath || sawMaxAssets || sawMaxBytes || sawCacheResources) {
        return Error(
            "disabled preamble must not specify path, budgets, or caching");
      }
      return NS_OK;
    }
    if (!sawPath) {
      return Error("active preamble requires an explicit path");
    }
    if (!sawMaxBytes) {
      aPreamble.mMaxBytes = anyTree ? 256 * 1024 : 64 * 1024;
    }
    if (aPreamble.mMaxBytes == 0) {
      return Error("active preamble max-bytes must be positive");
    }
    if (!anyTree) {
      if (sawCacheResources) {
        return Error("preamble cache-resources requires a tree/resource mode");
      }
      if (aPreamble.mMaxAssets != 0) {
        return Error("document-only preamble max-assets must be zero");
      }
    } else if (!sawMaxAssets) {
      aPreamble.mMaxAssets = 2;
    }
    if (h3Mode == PreambleMode::TreeResourceCommittedOverlap &&
        (aPreamble.mMaxAssets == 0 || aPreamble.mMaxAssets > 6)) {
      return Error(
          "tree-resource-committed-overlap requires one to six assets");
    }
    if (h3Mode == PreambleMode::TreeResourceNativeCacheCommittedOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-resource-native-cache-committed-overlap requires exactly "
            "one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-resource-native-cache-committed-overlap requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserPreloadOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-preload-overlap requires exactly one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-preload-overlap requires "
            "cache-resources=true");
      }
    }
    if (h2Mode == PreambleMode::TreeNativeParserDocumentStartOverlap ||
        h3Mode == PreambleMode::TreeNativeParserDocumentStartOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-document-start-overlap requires exactly one "
            "asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-document-start-overlap requires "
            "cache-resources=true");
      }
    }
    if (h2Mode == PreambleMode::TreeNativeParserDocumentStartResourceTree ||
        h3Mode == PreambleMode::TreeNativeParserDocumentStartResourceTree) {
      if (aPreamble.mMaxAssets != 3) {
        return Error(
            "tree-native-parser-document-start-resource-tree requires "
            "exactly three assets");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-document-start-resource-tree requires "
            "cache-resources=true");
      }
    }
    if (h2Mode == PreambleMode::TreeNativeParserResourceCommittedOverlap ||
        h3Mode == PreambleMode::TreeNativeParserResourceCommittedOverlap) {
      if ((h2Mode == PreambleMode::TreeNativeParserResourceCommittedOverlap &&
           aPreamble.mMaxAssets != 6) ||
          (h3Mode == PreambleMode::TreeNativeParserResourceCommittedOverlap &&
           aPreamble.mMaxAssets != 3 && aPreamble.mMaxAssets != 6)) {
        return Error(
            "tree-native-parser-resource-committed-overlap requires six H2 "
            "assets or three/six H3 assets");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-resource-committed-overlap requires "
            "cache-resources=true");
      }
    }
    if (h2Mode == PreambleMode::TreeNativeParserDocumentStartNavigationStop ||
        h3Mode == PreambleMode::TreeNativeParserDocumentStartNavigationStop) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-document-start-navigation-stop requires "
            "exactly one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-document-start-navigation-stop requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentStartResponseStop) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-document-start-response-stop requires "
            "exactly one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-document-start-response-stop requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserDocumentHandoffOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-document-handoff-overlap requires exactly "
            "one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-document-handoff-overlap requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserRetargetOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-retarget-overlap requires exactly one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-retarget-overlap requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserIpcRendezvousOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-ipc-rendezvous-overlap requires exactly "
            "one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-ipc-rendezvous-overlap requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserRootRendezvousOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-root-rendezvous-overlap requires exactly "
            "one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-root-rendezvous-overlap requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserProcessOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-process-overlap requires exactly one asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-process-overlap requires "
            "cache-resources=true");
      }
    }
    if (h3Mode == PreambleMode::TreeNativeParserFullProcessOverlap) {
      if (aPreamble.mMaxAssets != 1) {
        return Error(
            "tree-native-parser-full-process-overlap requires exactly one "
            "asset");
      }
      if (!aPreamble.mCacheResources) {
        return Error(
            "tree-native-parser-full-process-overlap requires "
            "cache-resources=true");
      }
    }
    return NS_OK;
  }

  nsresult ParsePositiveCompatibilityInteger(const char* aTypeError) {
    nsAutoCString value;
    if (mPosition < mInput.Length() && mInput.CharAt(mPosition) == '"') {
      MOZ_TRY(ParseString(value, aTypeError));
    } else {
      const size_t start = mPosition;
      if (mPosition < mInput.Length() && mInput.CharAt(mPosition) == '-') {
        ++mPosition;
      }
      const size_t digitsStart = mPosition;
      if (mPosition == mInput.Length() || mInput.CharAt(mPosition) < '0' ||
          mInput.CharAt(mPosition) > '9') {
        return Error(aTypeError);
      }
      if (mInput.CharAt(mPosition) == '0') {
        ++mPosition;
        if (mPosition < mInput.Length() && mInput.CharAt(mPosition) >= '0' &&
            mInput.CharAt(mPosition) <= '9') {
          return Error(aTypeError);
        }
      } else {
        while (mPosition < mInput.Length() && mInput.CharAt(mPosition) >= '0' &&
               mInput.CharAt(mPosition) <= '9') {
          ++mPosition;
        }
      }
      if (mPosition < mInput.Length() &&
          (mInput.CharAt(mPosition) == '.' || mInput.CharAt(mPosition) == 'e' ||
           mInput.CharAt(mPosition) == 'E')) {
        return Error(aTypeError);
      }
      if (mPosition < mInput.Length() &&
          !IsWhitespace(mInput.CharAt(mPosition)) &&
          mInput.CharAt(mPosition) != ',' && mInput.CharAt(mPosition) != '}') {
        return Error(aTypeError);
      }
      value.Assign(Substring(mInput, start, mPosition - start));
      if (digitsStart == mPosition) {
        return Error(aTypeError);
      }
    }

    if (value.IsEmpty()) {
      return Error(aTypeError);
    }
    size_t position = 0;
    bool negative = false;
    if (value.CharAt(position) == '+' || value.CharAt(position) == '-') {
      negative = value.CharAt(position) == '-';
      if (++position == value.Length()) {
        return Error(aTypeError);
      }
    }
    uint64_t parsed = 0;
    const uint64_t limit =
        static_cast<uint64_t>(std::numeric_limits<int32_t>::max()) +
        (negative ? 1 : 0);
    for (; position < value.Length(); ++position) {
      const char digit = value.CharAt(position);
      if (digit < '0' || digit > '9') {
        return Error(aTypeError);
      }
      const uint64_t numericDigit = digit - '0';
      if (parsed > (limit - numericDigit) / 10) {
        return Error(aTypeError);
      }
      parsed = parsed * 10 + numericDigit;
    }
    int64_t signedValue = static_cast<int64_t>(parsed);
    if (negative) {
      signedValue = -signedValue;
    }
    if (signedValue <= 0 || signedValue > std::numeric_limits<int32_t>::max()) {
      return Error(aTypeError);
    }
    return NS_OK;
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

  nsresult ParseHostResolverRule(const nsACString& aValue,
                                 HostResolverRule& aRule) {
    nsTArray<nsCString> tokens;
    size_t position = 0;
    while (position < aValue.Length()) {
      while (position < aValue.Length() && (aValue.CharAt(position) == ' ' ||
                                            aValue.CharAt(position) == '\t')) {
        ++position;
      }
      if (position == aValue.Length()) {
        break;
      }
      const size_t start = position;
      while (position < aValue.Length() && aValue.CharAt(position) != ' ' &&
             aValue.CharAt(position) != '\t') {
        if (aValue.CharAt(position) == '\r' ||
            aValue.CharAt(position) == '\n') {
          return Error("host-resolver-rules must contain one exact MAP rule");
        }
        ++position;
      }
      tokens.AppendElement(Substring(aValue, start, position - start));
    }
    if (tokens.Length() != 3 || !tokens[0].EqualsLiteral("MAP") ||
        !IsHostOrAddress(tokens[1]) || !IsHostOrAddress(tokens[2])) {
      return Error("host-resolver-rules must contain one exact MAP rule");
    }
    aRule.mLogicalHost = tokens[1];
    aRule.mPhysicalHost = tokens[2];
    return NS_OK;
  }

  nsresult ParseExtraHeaders(const nsACString& aValue,
                             nsTArray<ExtraHeader>& aHeaders) {
    size_t position = 0;
    while (position < aValue.Length()) {
      const int32_t separator = aValue.Find("\r\n"_ns, position);
      const size_t lineEnd =
          separator < 0 ? aValue.Length() : static_cast<size_t>(separator);
      const nsDependentCSubstring line =
          Substring(aValue, position, lineEnd - position);
      if (line.IsEmpty()) {
        return Error("extra-headers must not contain an empty header line");
      }
      if (line.FindChar('\r') >= 0 || line.FindChar('\n') >= 0) {
        return Error("extra-headers must use CRLF line endings");
      }
      const int32_t colon = line.FindChar(':');
      if (colon <= 0) {
        return Error("extra-headers entries must contain a header name");
      }
      ExtraHeader header;
      header.mName.Assign(Substring(line, 0, colon));
      for (size_t index = 0; index < header.mName.Length(); ++index) {
        if (!IsHeaderTokenCharacter(header.mName.CharAt(index))) {
          return Error("extra-headers contains an invalid header name");
        }
      }
      if (IsProtectedProxyConnectHeader(header.mName)) {
        return Error("extra-headers contains a protected header name");
      }
      size_t valueStart = static_cast<size_t>(colon + 1);
      size_t valueEnd = line.Length();
      while (valueStart < valueEnd && (line.CharAt(valueStart) == ' ' ||
                                       line.CharAt(valueStart) == '\t')) {
        ++valueStart;
      }
      while (valueEnd > valueStart && (line.CharAt(valueEnd - 1) == ' ' ||
                                       line.CharAt(valueEnd - 1) == '\t')) {
        --valueEnd;
      }
      header.mValue.Assign(Substring(line, valueStart, valueEnd - valueStart));
      for (size_t index = 0; index < header.mValue.Length(); ++index) {
        const char value = header.mValue.CharAt(index);
        if ((static_cast<unsigned char>(value) < 0x20 && value != '\t') ||
            value == 0x7f) {
          return Error("extra-headers contains an invalid header value");
        }
      }
      for (const auto& existing : aHeaders) {
        if (existing.mName.Equals(header.mName,
                                  nsCaseInsensitiveCStringComparator)) {
          return Error("extra-headers contains a duplicate header name");
        }
      }
      aHeaders.AppendElement(std::move(header));
      if (separator < 0) {
        return NS_OK;
      }
      position = lineEnd + 2;
      if (position == aValue.Length()) {
        return NS_OK;
      }
    }
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
      if (ParseNetworkAddress(AF_INET6, PromiseFlatCString(aHost).get(),
                              &address) != 1) {
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
    const nsDependentCSubstring authority = Substring(aValue, schemeEnd + 3);
    if (authority.FindChar('/') >= 0 || authority.FindChar('?') >= 0 ||
        authority.FindChar('#') >= 0) {
      return Error("listener URI must contain only host and port");
    }
    const int32_t at = authority.RFindChar('@');
    size_t endpointStart = 0;
    if (at >= 0) {
      if (aListener.mType != ListenerType::Socks5 || at == 0 ||
          authority.FindChar('@') != at) {
        return Error("listener URI contains invalid credentials");
      }
      const nsDependentCSubstring userInfo = Substring(authority, 0, at);
      const int32_t colon = userInfo.FindChar(':');
      if (colon <= 0) {
        return Error("listener URI requires username and password");
      }
      MOZ_TRY(PercentDecode(Substring(userInfo, 0, colon), aListener.mUser));
      MOZ_TRY(
          PercentDecode(Substring(userInfo, colon + 1), aListener.mPassword));
      if (aListener.mUser.IsEmpty() || aListener.mPassword.IsEmpty() ||
          aListener.mUser.Length() > 255 ||
          aListener.mPassword.Length() > 255) {
        return Error("listener URI contains invalid credentials");
      }
      endpointStart = static_cast<size_t>(at + 1);
    }
    MOZ_TRY(ParseHostPort(Substring(authority, endpointStart), true,
                          aListener.mHost, aListener.mIPv6, aListener.mPort));
    in_addr ipv4Address{};
    if (!aListener.mIPv6 && !aListener.mHost.EqualsLiteral("localhost") &&
        ParseNetworkAddress(AF_INET, PromiseFlatCString(aListener.mHost).get(),
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
    size_t endpointStart = 0;
    if (at >= 0) {
      if (at == 0 || authority.FindChar('@') != at) {
        return Error("proxy URI contains invalid credentials");
      }
      const nsDependentCSubstring userInfo = Substring(authority, 0, at);
      const int32_t colon = userInfo.FindChar(':');
      if (colon < 0) {
        return Error("proxy URI requires username and password");
      }
      MOZ_TRY(PercentDecode(Substring(userInfo, 0, colon), aProxy.mUser));
      MOZ_TRY(PercentDecode(Substring(userInfo, colon + 1), aProxy.mPassword));
      if (aProxy.mUser.FindChar(':') >= 0) {
        return Error("proxy URI contains invalid credentials");
      }
      endpointStart = static_cast<size_t>(at + 1);
    }

    nsAutoCString host;
    bool ipv6 = false;
    uint16_t port = 443;
    MOZ_TRY(ParseHostPort(Substring(authority, endpointStart), false, host,
                          ipv6, port));
    in_addr ipv4Address{};
    if (!ipv6 &&
        ParseNetworkAddress(AF_INET, PromiseFlatCString(host).get(),
                            &ipv4Address) != 1 &&
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
  if (aJson.Length() > kMaximumConfigSize) {
    return Fail(aError, "config is too large", NS_ERROR_FILE_TOO_BIG);
  }
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

ProfileDirectory::~ProfileDirectory() {
  if (!mTemporary || mPath.IsEmpty()) {
    return;
  }
  std::error_code error;
  std::filesystem::remove_all(
      std::filesystem::path(PromiseFlatCString(mPath).get()), error);
}

nsresult ResolveAndCreateProfile(ProfileDirectory& aProfile,
                                 nsACString& aError) {
  aProfile.mPath.Truncate();
  aProfile.mTemporary = false;
  aError.Truncate();

  const char* overridePath = std::getenv("NAIVEFOX_PROFILE");
  if (overridePath && *overridePath) {
    return CreatePersistentProfile(overridePath, aProfile.mPath, aError);
  }

  nsresult rv = CreateTemporaryProfile(aProfile.mPath, aError);
  if (NS_SUCCEEDED(rv)) {
    aProfile.mTemporary = true;
  }
  return rv;
}

}  // namespace mozilla::naivefox
