/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef SpeculativeTransaction_h_
#define SpeculativeTransaction_h_

#include "NullHttpTransaction.h"
#include "mozilla/Atomics.h"
#include "mozilla/Maybe.h"

namespace mozilla {
namespace net {

class HTTPSRecordResolver;

#ifdef MOZ_NAIVEFOX
class H3CarrierDispatchGate final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(H3CarrierDispatchGate)

  void SetCarrierId(uintptr_t aCarrierId) { mCarrierId = aCarrierId; }
  uintptr_t CarrierId() const { return mCarrierId; }

  void MarkCarrierReadComplete(nsresult aResult) {
    mCarrierReadResult = static_cast<uint32_t>(aResult);
    mCarrierReadComplete = true;
  }
  bool CarrierReadComplete() const { return mCarrierReadComplete; }
  nsresult CarrierReadResult() const {
    uint32_t result = mCarrierReadResult;
    return static_cast<nsresult>(result);
  }

  void Complete(nsresult aResult) {
    mResult = static_cast<uint32_t>(aResult);
    mComplete = true;
  }
  bool IsComplete() const { return mComplete; }
  nsresult Result() const {
    uint32_t result = mResult;
    return static_cast<nsresult>(result);
  }

 private:
  ~H3CarrierDispatchGate() = default;

  Atomic<uintptr_t, ReleaseAcquire> mCarrierId{0};
  Atomic<uint32_t, ReleaseAcquire> mCarrierReadResult{0};
  Atomic<uint32_t, ReleaseAcquire> mResult{0};
  Atomic<bool, ReleaseAcquire> mCarrierReadComplete{false};
  Atomic<bool, ReleaseAcquire> mComplete{false};
};
#endif

class SpeculativeTransaction : public NullHttpTransaction {
 public:
  SpeculativeTransaction(nsHttpConnectionInfo* aConnInfo,
                         nsIInterfaceRequestor* aCallbacks, uint32_t aCaps,
                         std::function<void(nsresult)>&& aCallback = nullptr,
                         bool reportActivity = true);

  already_AddRefed<SpeculativeTransaction> CreateWithNewConnInfo(
      nsHttpConnectionInfo* aConnInfo);

  virtual nsresult FetchHTTPSRR() override;

  virtual nsresult OnHTTPSRRAvailable(nsIDNSHTTPSSVCRecord* aHTTPSSVCRecord,
                                      nsISVCBRecord* aHighestPriorityRecord,
                                      const nsACString& aCname) override;

  void SetParallelSpeculativeConnectLimit(uint32_t aLimit) {
    mParallelSpeculativeConnectLimit.emplace(aLimit);
  }
  void SetIgnoreIdle(bool aIgnoreIdle) { mIgnoreIdle.emplace(aIgnoreIdle); }
  void SetAllow1918(bool aAllow1918) { mAllow1918.emplace(aAllow1918); }

  const Maybe<uint32_t>& ParallelSpeculativeConnectLimit() {
    return mParallelSpeculativeConnectLimit;
  }
  const Maybe<bool>& IgnoreIdle() { return mIgnoreIdle; }
  const Maybe<bool>& Allow1918() { return mAllow1918; }

  void Close(nsresult aReason) override;
  nsresult ReadSegments(nsAHttpSegmentReader* aReader, uint32_t aCount,
                        uint32_t* aCountRead) override;
  void InvokeCallback() override;

#ifdef MOZ_NAIVEFOX
  void SetH3CarrierDispatchGate(H3CarrierDispatchGate* aGate) {
    mH3CarrierDispatchGate = aGate;
  }
  H3CarrierDispatchGate* CarrierDispatchGate() const {
    return mH3CarrierDispatchGate;
  }
#endif

 protected:
  virtual ~SpeculativeTransaction();

  Maybe<uint32_t> mParallelSpeculativeConnectLimit;
  Maybe<bool> mIgnoreIdle;
  Maybe<bool> mAllow1918;

  bool mTriedToWrite = false;
  std::function<void(nsresult)> mCloseCallback;
  RefPtr<HTTPSRecordResolver> mResolver;
#ifdef MOZ_NAIVEFOX
  RefPtr<H3CarrierDispatchGate> mH3CarrierDispatchGate;
#endif
};

class FallbackTransaction : public SpeculativeTransaction {
 public:
  FallbackTransaction(nsHttpConnectionInfo* aConnInfo,
                      nsIInterfaceRequestor* aCallbacks, uint32_t aCaps,
                      std::function<void(nsresult)>&& aCallback)
      : SpeculativeTransaction(aConnInfo, aCallbacks, aCaps,
                               std::move(aCallback)) {}

  bool IsForFallback() override { return true; }

 private:
  virtual ~FallbackTransaction() = default;
};

}  // namespace net
}  // namespace mozilla

#endif  // SpeculativeTransaction_h_
