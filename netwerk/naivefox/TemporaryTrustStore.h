/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_TemporaryTrustStore_h
#define netwerk_naivefox_TemporaryTrustStore_h

#include "ScopedNSSTypes.h"
#include "nsStringFwd.h"
#include "nsTArray.h"
#include "nscore.h"

namespace mozilla::naivefox {

class TemporaryTrustStore final {
 public:
  TemporaryTrustStore() = default;
  ~TemporaryTrustStore() = default;

  TemporaryTrustStore(const TemporaryTrustStore&) = delete;
  TemporaryTrustStore& operator=(const TemporaryTrustStore&) = delete;

  nsresult LoadFromEnvironment(nsACString& aError);
  bool IsConfigured() const { return mConfigured; }

 private:
  nsTArray<UniqueCERTCertificate> mCertificates;
  bool mConfigured = false;
};

}  // namespace mozilla::naivefox

#endif
