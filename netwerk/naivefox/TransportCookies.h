/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef netwerk_naivefox_TransportCookies_h
#define netwerk_naivefox_TransportCookies_h

#include <vector>

#include "nsCOMPtr.h"
#include "nsString.h"

class nsIChannel;
class nsIURI;

namespace mozilla::naivefox {

class TransportCookies final {
 public:
  nsresult RequestHeader(nsIURI* aURI, nsACString& aHeader);
  nsresult ResponseHeaders(nsIChannel* aChannel);
  nsresult Store(nsIURI* aURI, const nsACString& aHeader,
                 const nsACString& aDate, int64_t aNow);
  bool HasSession();
  void Clear();

 private:
  struct Cookie {
    nsCString name;
    nsCString value;
    nsCString path;
    nsCString domain;
    int64_t expires = INT64_MAX;
    bool secure = false;
  };
  nsresult CheckOrigin(nsIURI* aURI);
  void Expire(int64_t aNow);
  std::vector<Cookie> mCookies;
  nsCString mOrigin;
  bool mClosed = false;
};

}  // namespace mozilla::naivefox
#endif
