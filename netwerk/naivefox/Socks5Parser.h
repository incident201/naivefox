/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_Socks5Parser_h
#define netwerk_naivefox_Socks5Parser_h

#include <cstddef>
#include <cstdint>

#include "mozilla/Span.h"
#include "nsString.h"
#include "nsTArray.h"

namespace mozilla::naivefox {

struct Socks5Target final {
  enum class Type : uint8_t { IPv4, IPv6, Domain };

  Type mType = Type::Domain;
  nsCString mHost;
  uint16_t mPort = 0;

  nsCString Authority() const;
};

class Socks5Parser final {
 public:
  enum class Event : uint8_t {
    NeedMore,
    SendNoAuthenticationSelection,
    RequestReady,
    RejectMethods,
    RejectCommand,
    RejectAddressType,
    ProtocolError,
  };

  Event Consume(Span<const uint8_t> aInput, size_t& aConsumed);

  const Socks5Target& Target() const { return mTarget; }
  bool IsComplete() const { return mState == State::Complete; }

  static void MakeMethodSelection(bool aAccepted, nsTArray<uint8_t>& aReply);
  static void MakeReply(uint8_t aReplyCode, nsTArray<uint8_t>& aReply);

 private:
  enum class State : uint8_t {
    GreetingVersion,
    GreetingMethodCount,
    GreetingMethods,
    RequestVersion,
    RequestCommand,
    RequestReserved,
    RequestAddressType,
    DomainLength,
    Address,
    PortHigh,
    PortLow,
    Complete,
    Failed,
  };

  Event ConsumeByte(uint8_t aByte);
  bool FinishAddress();

  State mState = State::GreetingVersion;
  uint16_t mRemaining = 0;
  bool mSawNoAuthentication = false;
  Socks5Target mTarget;
  nsTArray<uint8_t> mAddress;
  uint8_t mPortHigh = 0;
};

}  // namespace mozilla::naivefox

#endif  // netwerk_naivefox_Socks5Parser_h
