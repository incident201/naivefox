/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "CacheCrypto.h"
#include "gtest/gtest.h"
#include "mozilla/RefPtr.h"

#ifdef MOZ_NAIVEFOX
namespace mozilla::net {

TEST(NaiveFoxCacheCrypto, UnavailableKeystoreDoesNotCreateCipher)
{
  const bool enabled = CacheCrypto::IsEnabled();
  RefPtr<CacheCrypto> crypto = CacheCrypto::LoadFromKeystore(nullptr);
  EXPECT_FALSE(crypto);
  EXPECT_EQ(enabled, CacheCrypto::IsEnabled());
}

}  // namespace mozilla::net
#endif
