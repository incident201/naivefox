/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "Socks5Parser.h"

#include <cstring>

#include "mozilla/Assertions.h"
#include "mozilla/net/DNS.h"

namespace mozilla::naivefox {

namespace {

constexpr uint8_t kVersion = 0x05;
constexpr uint8_t kNoAuthentication = 0x00;
constexpr uint8_t kNoAcceptableMethods = 0xff;
constexpr uint8_t kConnect = 0x01;
constexpr uint8_t kIPv4 = 0x01;
constexpr uint8_t kDomain = 0x03;
constexpr uint8_t kIPv6 = 0x04;

}  // namespace

nsCString Socks5Target::Authority() const {
  nsCString authority;
  if (mType == Type::IPv6) {
    authority.Append('[');
    authority.Append(mHost);
    authority.Append(']');
  } else {
    authority.Assign(mHost);
  }
  authority.AppendPrintf(":%u", static_cast<unsigned>(mPort));
  return authority;
}

Socks5Parser::Event Socks5Parser::Consume(Span<const uint8_t> aInput,
                                          size_t& aConsumed) {
  aConsumed = 0;
  if (mState == State::Complete) {
    return Event::RequestReady;
  }
  if (mState == State::Failed) {
    return Event::ProtocolError;
  }

  while (aConsumed < aInput.Length()) {
    Event event = ConsumeByte(aInput[aConsumed++]);
    if (event != Event::NeedMore) {
      return event;
    }
  }
  return Event::NeedMore;
}

Socks5Parser::Event Socks5Parser::ConsumeByte(uint8_t aByte) {
  switch (mState) {
    case State::GreetingVersion:
      if (aByte != kVersion) {
        mState = State::Failed;
        return Event::RejectMethods;
      }
      mState = State::GreetingMethodCount;
      return Event::NeedMore;

    case State::GreetingMethodCount:
      if (aByte == 0) {
        mState = State::Failed;
        return Event::RejectMethods;
      }
      mRemaining = aByte;
      mSawNoAuthentication = false;
      mState = State::GreetingMethods;
      return Event::NeedMore;

    case State::GreetingMethods:
      mSawNoAuthentication |= aByte == kNoAuthentication;
      if (--mRemaining != 0) {
        return Event::NeedMore;
      }
      if (!mSawNoAuthentication) {
        mState = State::Failed;
        return Event::RejectMethods;
      }
      mState = State::RequestVersion;
      return Event::SendNoAuthenticationSelection;

    case State::RequestVersion:
      if (aByte != kVersion) {
        mState = State::Failed;
        return Event::ProtocolError;
      }
      mState = State::RequestCommand;
      return Event::NeedMore;

    case State::RequestCommand:
      if (aByte != kConnect) {
        mState = State::Failed;
        return Event::RejectCommand;
      }
      mState = State::RequestReserved;
      return Event::NeedMore;

    case State::RequestReserved:
      if (aByte != 0) {
        mState = State::Failed;
        return Event::ProtocolError;
      }
      mState = State::RequestAddressType;
      return Event::NeedMore;

    case State::RequestAddressType:
      mAddress.Clear();
      if (aByte == kIPv4) {
        mTarget.mType = Socks5Target::Type::IPv4;
        mRemaining = 4;
        mState = State::Address;
      } else if (aByte == kIPv6) {
        mTarget.mType = Socks5Target::Type::IPv6;
        mRemaining = 16;
        mState = State::Address;
      } else if (aByte == kDomain) {
        mTarget.mType = Socks5Target::Type::Domain;
        mState = State::DomainLength;
      } else {
        mState = State::Failed;
        return Event::RejectAddressType;
      }
      return Event::NeedMore;

    case State::DomainLength:
      if (aByte == 0) {
        mState = State::Failed;
        return Event::RejectAddressType;
      }
      mRemaining = aByte;
      mState = State::Address;
      return Event::NeedMore;

    case State::Address:
      mAddress.AppendElement(aByte);
      if (--mRemaining == 0) {
        if (!FinishAddress()) {
          mState = State::Failed;
          return Event::ProtocolError;
        }
        mState = State::PortHigh;
      }
      return Event::NeedMore;

    case State::PortHigh:
      mPortHigh = aByte;
      mState = State::PortLow;
      return Event::NeedMore;

    case State::PortLow:
      mTarget.mPort = (static_cast<uint16_t>(mPortHigh) << 8) | aByte;
      mState = State::Complete;
      return Event::RequestReady;

    case State::Complete:
      return Event::RequestReady;

    case State::Failed:
      return Event::ProtocolError;
  }
  MOZ_CRASH("unreachable SOCKS5 parser state");
}

bool Socks5Parser::FinishAddress() {
  if (mTarget.mType == Socks5Target::Type::Domain) {
    mTarget.mHost.Assign(reinterpret_cast<const char*>(mAddress.Elements()),
                         mAddress.Length());
    return true;
  }

  mozilla::net::NetAddr address;
  if (mTarget.mType == Socks5Target::Type::IPv4) {
    if (mAddress.Length() != 4) {
      return false;
    }
    address.inet.family = AF_INET;
    std::memcpy(&address.inet.ip, mAddress.Elements(), mAddress.Length());
  } else {
    if (mAddress.Length() != 16) {
      return false;
    }
    address.inet6.family = AF_INET6;
    std::memcpy(address.inet6.ip.u8, mAddress.Elements(), mAddress.Length());
  }
  return address.ToString(mTarget.mHost);
}

void Socks5Parser::MakeMethodSelection(bool aAccepted,
                                       nsTArray<uint8_t>& aReply) {
  aReply.ClearAndRetainStorage();
  aReply.AppendElement(kVersion);
  aReply.AppendElement(aAccepted ? kNoAuthentication : kNoAcceptableMethods);
}

void Socks5Parser::MakeReply(uint8_t aReplyCode, nsTArray<uint8_t>& aReply) {
  static constexpr uint8_t kReplyPrefix[] = {kVersion, 0x00, 0x00, kIPv4, 0x00,
                                             0x00,     0x00, 0x00, 0x00,  0x00};
  aReply.ClearAndRetainStorage();
  aReply.AppendElements(kReplyPrefix, sizeof(kReplyPrefix));
  aReply[1] = aReplyCode;
}

}  // namespace mozilla::naivefox
