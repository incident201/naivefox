/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_Transport_h
#define netwerk_naivefox_Transport_h

#include <functional>

#include "mozilla/Span.h"
#include "mozilla/UniquePtr.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsStringFwd.h"

class nsIEventTarget;

namespace mozilla::naivefox {

struct TransportConfig;
class TransportCarrier;

class TransportStream final : public nsIInputStreamCallback,
                              public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  TransportStream(nsIAsyncInputStream* aInput, nsIAsyncOutputStream* aOutput,
                  const TransportConfig& aConfig, nsIEventTarget* aSocketTarget,
                  std::function<void()>&& aEstablished,
                  std::function<void(nsresult)>&& aFailed,
                  std::function<void(nsresult)>&& aClosed);
  nsresult Start(const nsACString& aAuthority, Span<const uint8_t> aInitial);
  nsresult StartPump();
  void Cancel(nsresult aStatus);

 private:
  friend class TransportCarrier;
  class Impl;
  ~TransportStream();
  void ArmRead();
  void Flush();
  void Finish(nsresult aStatus, bool aReset);
  UniquePtr<Impl> mImpl;
};

void ShutdownTransportCarriers();

}  // namespace mozilla::naivefox
#endif
