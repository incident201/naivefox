/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_StoragePrincipalHelper_h
#define mozilla_StoragePrincipalHelper_h

#include "mozilla/OriginAttributes.h"
#include "nsIChannel.h"
#include "nsILoadInfo.h"

namespace mozilla {

class StoragePrincipalHelper final {
 public:
  template <typename... Args>
  static void GetRegularPrincipalOriginAttributes(Args&&...) {}
  enum PrincipalType { eRegularPrincipal };

  static bool GetOriginAttributesForHSTS(nsIChannel* aChannel,
                                         OriginAttributes& aAttrs) {
    return Copy(aChannel, aAttrs);
  }
  static bool GetOriginAttributesForHTTPSRR(nsIChannel* aChannel,
                                            OriginAttributes& aAttrs) {
    return Copy(aChannel, aAttrs);
  }
  static bool GetOriginAttributes(nsIChannel* aChannel,
                                  OriginAttributes& aAttrs,
                                  PrincipalType) {
    return Copy(aChannel, aAttrs);
  }
  static bool GetOriginAttributesForNetworkState(nsIChannel* aChannel,
                                                 OriginAttributes& aAttrs) {
    return Copy(aChannel, aAttrs);
  }

 private:
  static bool Copy(nsIChannel* aChannel, OriginAttributes& aAttrs) {
    nsCOMPtr<nsILoadInfo> loadInfo = aChannel ? aChannel->LoadInfo() : nullptr;
    if (!loadInfo) {
      return false;
    }
    aAttrs = loadInfo->GetOriginAttributes();
    return true;
  }
};

}  // namespace mozilla

#endif
