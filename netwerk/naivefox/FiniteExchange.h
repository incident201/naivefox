/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_FiniteExchange_h
#define netwerk_naivefox_FiniteExchange_h

#include <functional>

#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsStringFwd.h"

namespace mozilla::naivefox {

struct TunnelConfig;

// Experimental, main-thread-owned adapter. The socket-thread DuplexPump sees
// bounded native pipes; Necko owns every actual HTTP request and connection.
class FiniteExchange final : public nsIInputStreamCallback,
                             public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  using ReadyCallback = std::function<void(nsresult, nsIAsyncInputStream*,
                                           nsIAsyncOutputStream*)>;

  FiniteExchange(const TunnelConfig& aConfig, uint64_t aConnectionId,
                 ReadyCallback&& aCallback);
  nsresult Start(const nsACString& aTargetAuthority);
  void Cancel(nsresult aStatus);

 private:
  class Impl;
  class Listener;
  ~FiniteExchange();
  nsresult Open(const char* aOperation, uint64_t aSequence,
                const nsACString& aBody, bool aFin,
                RefPtr<Listener>& aListener);
  void Finished(Listener* aListener, nsresult aStatus);
  nsresult FillDownloads();
  nsresult FlushDownloads();
  nsresult ReadUploads();
  void Fail(nsresult aStatus);

  UniquePtr<Impl> mImpl;
};

}  // namespace mozilla::naivefox

#endif
