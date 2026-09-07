/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef netwerk_naivefox_NoConnectHash_h
#define netwerk_naivefox_NoConnectHash_h

#include <algorithm>
#include <array>
#include <limits>

#include "mozilla/Span.h"
#include "nsString.h"
#include "sechash.h"

namespace mozilla::naivefox {
class NoConnectHash final {
 public:
  NoConnectHash() : mContext(HASH_Create(HASH_AlgSHA256)) {
    if (mContext) {
      HASH_Begin(mContext);
    }
  }
  ~NoConnectHash() {
    if (mContext) {
      HASH_Destroy(mContext);
    }
  }
  NoConnectHash(const NoConnectHash&) = delete;
  NoConnectHash& operator=(const NoConnectHash&) = delete;

  bool Update(Span<const uint8_t> aBytes) {
    if (!mContext) {
      return false;
    }
    while (!aBytes.empty()) {
      const auto count = static_cast<unsigned int>(std::min(
          aBytes.size(), size_t(std::numeric_limits<unsigned int>::max())));
      HASH_Update(mContext, aBytes.data(), count);
      aBytes = aBytes.Subspan(count);
    }
    return true;
  }
  bool Field(const nsACString& aValue) {
    uint64_t length = aValue.Length();
    std::array<uint8_t, 8> prefix{};
    for (size_t i = 0; i < prefix.size(); ++i) {
      prefix[7 - i] = static_cast<uint8_t>(length >> (8 * i));
    }
    return Update(prefix) &&
           Update(Span(reinterpret_cast<const uint8_t*>(aValue.BeginReading()),
                       aValue.Length()));
  }
  bool Finish(nsACString& aDigest, bool aHex = false) {
    if (!mContext) {
      return false;
    }
    std::array<uint8_t, 32> digest{};
    unsigned int length = 0;
    HASH_End(mContext, digest.data(), &length, digest.size());
    HASH_Destroy(mContext);
    mContext = nullptr;
    if (length != digest.size()) {
      return false;
    }
    aDigest.Truncate();
    if (aHex) {
      constexpr char hex[] = "0123456789abcdef";
      for (const auto byte : digest) {
        aDigest.Append(hex[byte >> 4]);
        aDigest.Append(hex[byte & 15]);
      }
    } else {
      aDigest.Assign(reinterpret_cast<const char*>(digest.data()),
                     digest.size());
    }
    return true;
  }

 private:
  HASHContext* mContext;
};
}  // namespace mozilla::naivefox
#endif
