/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "OpaqueResponseUtils.h"

#include "nsHttpResponseHead.h"

namespace mozilla::net {

static LazyLogModule gORBLog("ORB");

OpaqueResponseBlockedReason GetOpaqueResponseBlockedReason(
    const nsACString&, uint16_t, bool) {
  return OpaqueResponseBlockedReason::ALLOWED_SAFE_LISTED;
}

OpaqueResponseBlockedReason GetOpaqueResponseBlockedReason(
    nsHttpResponseHead&) {
  return OpaqueResponseBlockedReason::ALLOWED_SAFE_LISTED;
}

Result<std::tuple<int64_t, int64_t, int64_t>, nsresult>
ParseContentRangeHeaderString(const nsAutoCString&) {
  return Err(NS_ERROR_NOT_AVAILABLE);
}

bool IsFirstPartialResponse(nsHttpResponseHead&) { return false; }

LogModule* GetORBLog() { return gORBLog; }

OpaqueResponseFilter::OpaqueResponseFilter(nsIStreamListener* aNext)
    : mNext(aNext) {}

NS_IMETHODIMP OpaqueResponseFilter::OnStartRequest(nsIRequest* aRequest) {
  return mNext->OnStartRequest(aRequest);
}

NS_IMETHODIMP OpaqueResponseFilter::OnDataAvailable(nsIRequest* aRequest,
                                                    nsIInputStream* aStream,
                                                    uint64_t aOffset,
                                                    uint32_t aCount) {
  return mNext->OnDataAvailable(aRequest, aStream, aOffset, aCount);
}

NS_IMETHODIMP OpaqueResponseFilter::OnStopRequest(nsIRequest* aRequest,
                                                  nsresult aStatus) {
  return mNext->OnStopRequest(aRequest, aStatus);
}

NS_IMPL_ISUPPORTS(OpaqueResponseFilter, nsIStreamListener, nsIRequestObserver)

OpaqueResponseBlocker::OpaqueResponseBlocker(nsIStreamListener* aNext,
                                             HttpBaseChannel*,
                                             const nsCString& aContentType,
                                             bool aNoSniff)
    : mNext(aNext), mContentType(aContentType), mNoSniff(aNoSniff) {}

NS_IMETHODIMP OpaqueResponseBlocker::OnStartRequest(nsIRequest* aRequest) {
  mState = State::Allowed;
  return mNext->OnStartRequest(aRequest);
}

NS_IMETHODIMP OpaqueResponseBlocker::OnDataAvailable(nsIRequest* aRequest,
                                                     nsIInputStream* aStream,
                                                     uint64_t aOffset,
                                                     uint32_t aCount) {
  return mNext->OnDataAvailable(aRequest, aStream, aOffset, aCount);
}

NS_IMETHODIMP OpaqueResponseBlocker::OnStopRequest(nsIRequest* aRequest,
                                                   nsresult aStatus) {
  return mNext->OnStopRequest(aRequest, aStatus);
}

bool OpaqueResponseBlocker::IsSniffing() const { return false; }
void OpaqueResponseBlocker::AllowResponse() { mState = State::Allowed; }
void OpaqueResponseBlocker::BlockResponse(HttpBaseChannel*, nsresult aStatus) {
  mState = State::Blocked;
  mStatus = aStatus;
}
void OpaqueResponseBlocker::FilterResponse() {}
nsresult OpaqueResponseBlocker::EnsureOpaqueResponseIsAllowedAfterSniff(
    nsIRequest*) {
  return NS_OK;
}
OpaqueResponse
OpaqueResponseBlocker::EnsureOpaqueResponseIsAllowedAfterJavaScriptValidation(
    HttpBaseChannel*, bool) {
  return OpaqueResponse::Allow;
}
nsresult OpaqueResponseBlocker::ValidateJavaScript(HttpBaseChannel*, nsIURI*,
                                                   nsILoadInfo*) {
  return NS_OK;
}
void OpaqueResponseBlocker::ResolveAndProcessData(
    HttpBaseChannel*, bool, Maybe<mozilla::ipc::Shmem>&) {}
void OpaqueResponseBlocker::MaybeRunOnStopRequest(HttpBaseChannel*) {}

NS_IMPL_ISUPPORTS(OpaqueResponseBlocker, nsIStreamListener, nsIRequestObserver)

}  // namespace mozilla::net
