/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_HttpConnectParser_h
#define netwerk_naivefox_HttpConnectParser_h

#include <cstddef>
#include <cstdint>

#include "mozilla/Span.h"
#include "nsString.h"

namespace mozilla::naivefox {

struct HttpConnectTarget final {
  nsCString mHost;
  uint16_t mPort = 0;
  bool mIPv6 = false;

  nsCString Authority() const;
};

class HttpConnectParser final {
 public:
  static constexpr size_t kMaximumHeaderBytes = 16 * 1024;

  enum class Event : uint8_t {
    NeedMore,
    RequestReady,
    UnsupportedMethod,
    HeaderTooLarge,
    ProtocolError,
  };

  Event Consume(Span<const uint8_t> aInput, size_t& aConsumed);
  const HttpConnectTarget& Target() const { return mTarget; }

 private:
  Event ParseRequest();
  bool ParseAuthority(const nsACString& aAuthority);
  bool ValidateHeaders(size_t aRequestLineEnd) const;
  void Fail(Event aEvent);

  nsCString mHeaders;
  HttpConnectTarget mTarget;
  Event mTerminalEvent = Event::NeedMore;
  uint8_t mTerminatorMatch = 0;
};

}  // namespace mozilla::naivefox

#endif
