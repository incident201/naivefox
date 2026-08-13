/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "HttpConnectParser.h"

#include <arpa/inet.h>

namespace mozilla::naivefox {

namespace {

bool IsTokenCharacter(char aValue) {
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

bool ParsePort(const nsACString& aText, uint16_t& aPort) {
  if (aText.IsEmpty()) {
    return false;
  }
  uint32_t port = 0;
  for (size_t i = 0; i < aText.Length(); ++i) {
    const char value = aText.CharAt(i);
    if (value < '0' || value > '9') {
      return false;
    }
    port = port * 10 + value - '0';
    if (port > 65535) {
      return false;
    }
  }
  if (port == 0) {
    return false;
  }
  aPort = static_cast<uint16_t>(port);
  return true;
}

bool IsDomain(const nsACString& aHost) {
  if (aHost.IsEmpty() || aHost.Length() > 253 || aHost.First() == '.' ||
      aHost.Last() == '.') {
    return false;
  }
  size_t labelLength = 0;
  bool labelStartsWithHyphen = false;
  bool allNumericOrDots = true;
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
    allNumericOrDots &= value >= '0' && value <= '9';
    previous = value;
  }
  return labelLength != 0 && !labelStartsWithHyphen && aHost.Last() != '-' &&
         !allNumericOrDots;
}

}  // namespace

nsCString HttpConnectTarget::Authority() const {
  nsCString authority;
  if (mIPv6) {
    authority.Append('[');
  }
  authority.Append(mHost);
  if (mIPv6) {
    authority.Append(']');
  }
  authority.AppendPrintf(":%u", static_cast<unsigned>(mPort));
  return authority;
}

HttpConnectParser::Event HttpConnectParser::Consume(Span<const uint8_t> aInput,
                                                    size_t& aConsumed) {
  aConsumed = 0;
  if (mTerminalEvent != Event::NeedMore) {
    return mTerminalEvent;
  }

  static constexpr char kTerminator[] = "\r\n\r\n";
  while (aConsumed < aInput.Length()) {
    if (mHeaders.Length() == kMaximumHeaderBytes) {
      Fail(Event::HeaderTooLarge);
      return mTerminalEvent;
    }
    const char value = static_cast<char>(aInput[aConsumed++]);
    mHeaders.Append(value);
    if (value == kTerminator[mTerminatorMatch]) {
      ++mTerminatorMatch;
      if (mTerminatorMatch == 4) {
        mTerminalEvent = ParseRequest();
        return mTerminalEvent;
      }
    } else {
      mTerminatorMatch = value == '\r' ? 1 : 0;
    }
  }
  return Event::NeedMore;
}

void HttpConnectParser::Fail(Event aEvent) { mTerminalEvent = aEvent; }

HttpConnectParser::Event HttpConnectParser::ParseRequest() {
  const int32_t requestLineEnd = mHeaders.Find("\r\n"_ns);
  if (requestLineEnd <= 0) {
    return Event::ProtocolError;
  }
  const nsDependentCSubstring requestLine =
      Substring(mHeaders, 0, requestLineEnd);
  const int32_t firstSpace = requestLine.FindChar(' ');
  if (firstSpace <= 0) {
    return Event::ProtocolError;
  }
  if (!Substring(requestLine, 0, firstSpace).EqualsLiteral("CONNECT")) {
    return Event::UnsupportedMethod;
  }
  const int32_t secondSpace = requestLine.FindChar(' ', firstSpace + 1);
  if (secondSpace <= firstSpace + 1 ||
      requestLine.FindChar(' ', secondSpace + 1) >= 0) {
    return Event::ProtocolError;
  }
  const nsDependentCSubstring version = Substring(requestLine, secondSpace + 1);
  if (!version.EqualsLiteral("HTTP/1.1") &&
      !version.EqualsLiteral("HTTP/1.0")) {
    return Event::ProtocolError;
  }
  if (!ParseAuthority(Substring(requestLine, firstSpace + 1,
                                secondSpace - firstSpace - 1)) ||
      !ValidateHeaders(requestLineEnd)) {
    return Event::ProtocolError;
  }
  return Event::RequestReady;
}

bool HttpConnectParser::ParseAuthority(const nsACString& aAuthority) {
  nsAutoCString portText;
  if (aAuthority.IsEmpty()) {
    return false;
  }
  if (aAuthority.First() == '[') {
    const int32_t close = aAuthority.FindChar(']');
    if (close <= 1 || static_cast<size_t>(close + 1) >= aAuthority.Length() ||
        aAuthority.CharAt(close + 1) != ':') {
      return false;
    }
    mTarget.mHost.Assign(Substring(aAuthority, 1, close - 1));
    portText.Assign(Substring(aAuthority, close + 2));
    in6_addr address{};
    if (inet_pton(AF_INET6, mTarget.mHost.get(), &address) != 1) {
      return false;
    }
    mTarget.mIPv6 = true;
  } else {
    const int32_t colon = aAuthority.RFindChar(':');
    if (colon <= 0 || aAuthority.FindChar(':') != colon) {
      return false;
    }
    mTarget.mHost.Assign(Substring(aAuthority, 0, colon));
    portText.Assign(Substring(aAuthority, colon + 1));
    in_addr address{};
    if (inet_pton(AF_INET, mTarget.mHost.get(), &address) != 1 &&
        !IsDomain(mTarget.mHost)) {
      return false;
    }
    mTarget.mIPv6 = false;
  }
  return ParsePort(portText, mTarget.mPort);
}

bool HttpConnectParser::ValidateHeaders(size_t aRequestLineEnd) const {
  size_t offset = aRequestLineEnd + 2;
  const size_t end = mHeaders.Length() - 2;
  while (offset < end) {
    const int32_t lineEnd = mHeaders.Find("\r\n"_ns, offset);
    if (lineEnd < 0 || static_cast<size_t>(lineEnd) > end) {
      return false;
    }
    if (static_cast<size_t>(lineEnd) == offset) {
      return static_cast<size_t>(lineEnd) + 2 == end;
    }
    const nsDependentCSubstring line =
        Substring(mHeaders, offset, lineEnd - offset);
    const int32_t colon = line.FindChar(':');
    if (colon <= 0 || line.First() == ' ' || line.First() == '\t') {
      return false;
    }
    const nsDependentCSubstring name = Substring(line, 0, colon);
    for (size_t i = 0; i < name.Length(); ++i) {
      const char value = name.CharAt(i);
      if (!IsTokenCharacter(value)) {
        return false;
      }
    }
    const nsDependentCSubstring valueText = Substring(line, colon + 1);
    for (size_t i = 0; i < valueText.Length(); ++i) {
      const char value = valueText.CharAt(i);
      if ((static_cast<unsigned char>(value) < 0x20 && value != '\t') ||
          value == 0x7f) {
        return false;
      }
    }
    offset = lineEnd + 2;
  }
  return offset == end;
}

}  // namespace mozilla::naivefox
