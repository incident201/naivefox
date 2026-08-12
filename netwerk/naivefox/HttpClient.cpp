/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "HttpClient.h"

#include <algorithm>
#include <cstdio>

#include "mozilla/SpinEventLoopUntil.h"
#include "nsCOMPtr.h"
#include "nsIChannel.h"
#include "nsIContentPolicy.h"
#include "nsIHttpChannel.h"
#include "nsIInputStream.h"
#include "nsILoadInfo.h"
#include "nsIPrincipal.h"
#include "nsIRequest.h"
#include "nsIScriptSecurityManager.h"
#include "nsIStreamListener.h"
#include "nsNetUtil.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"

namespace mozilla::naivefox {

namespace {

constexpr uint32_t kBodyLimit = 4096;

class FetchListener final : public nsIStreamListener {
 public:
  NS_DECL_ISUPPORTS
  NS_DECL_NSIREQUESTOBSERVER
  NS_DECL_NSISTREAMLISTENER

  bool Complete() const { return mComplete; }
  nsresult Status() const { return mStatus; }

 private:
  ~FetchListener() = default;

  bool mComplete = false;
  bool mTruncated = false;
  nsresult mStatus = NS_ERROR_NOT_INITIALIZED;
  nsCString mBody;
};

NS_IMPL_ISUPPORTS(FetchListener, nsIStreamListener, nsIRequestObserver)

NS_IMETHODIMP FetchListener::OnStartRequest(nsIRequest* aRequest) {
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(aRequest);
  if (!http) {
    return NS_ERROR_UNEXPECTED;
  }

  uint32_t status = 0;
  MOZ_TRY(http->GetResponseStatus(&status));
  std::printf("HTTP status: %u\n", status);
  return NS_OK;
}

NS_IMETHODIMP FetchListener::OnDataAvailable(nsIRequest* aRequest,
                                             nsIInputStream* aInputStream,
                                             uint64_t aOffset,
                                             uint32_t aCount) {
  char buffer[4096];
  while (aCount > 0) {
    uint32_t read = 0;
    MOZ_TRY(aInputStream->Read(
        buffer, std::min<uint32_t>(aCount, sizeof(buffer)), &read));
    if (read == 0) {
      return NS_ERROR_UNEXPECTED;
    }
    aCount -= read;

    uint32_t remaining = kBodyLimit - mBody.Length();
    uint32_t kept = std::min(remaining, read);
    mBody.Append(buffer, kept);
    mTruncated |= kept != read;
  }
  return NS_OK;
}

NS_IMETHODIMP FetchListener::OnStopRequest(nsIRequest* aRequest,
                                           nsresult aStatus) {
  mStatus = aStatus;
  mComplete = true;

  if (NS_SUCCEEDED(aStatus)) {
    std::printf("Body (%zu bytes%s):\n%.*s\n",
                static_cast<size_t>(mBody.Length()),
                mTruncated ? ", truncated" : "",
                static_cast<int>(mBody.Length()), mBody.get());
  }
  return NS_OK;
}

}  // namespace

nsresult FetchWithNecko(const nsACString& aUrl) {
  nsCOMPtr<nsIURI> uri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(uri), aUrl));

  nsCOMPtr<nsIScriptSecurityManager> securityManager =
      do_GetService(NS_SCRIPTSECURITYMANAGER_CONTRACTID);
  if (!securityManager) {
    return NS_ERROR_FAILURE;
  }
  nsCOMPtr<nsIPrincipal> principal;
  MOZ_TRY(securityManager->GetSystemPrincipal(getter_AddRefs(principal)));

  nsCOMPtr<nsIChannel> channel;
  MOZ_TRY(NS_NewChannel(getter_AddRefs(channel), uri, principal,
                        nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL,
                        nsIContentPolicy::TYPE_OTHER));

  RefPtr<FetchListener> listener = new FetchListener();
  MOZ_TRY(channel->AsyncOpen(listener));

  if (!SpinEventLoopUntil("NaiveFox::FetchWithNecko"_ns,
                          [&listener]() { return listener->Complete(); })) {
    return NS_ERROR_FAILURE;
  }
  return listener->Status();
}

}  // namespace mozilla::naivefox
