/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NoConnectTransport_h
#define netwerk_naivefox_NoConnectTransport_h

#include <functional>

#include "mozilla/Span.h"
#include "mozilla/UniquePtr.h"
#include "nsIAsyncInputStream.h"
#include "nsIAsyncOutputStream.h"
#include "nsStringFwd.h"

class nsIEventTarget;

namespace mozilla::naivefox {

struct TunnelConfig;
class NoConnectCarrier;

class NoConnectStream final : public nsIInputStreamCallback,
                              public nsIOutputStreamCallback {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  NS_DECL_NSIINPUTSTREAMCALLBACK
  NS_DECL_NSIOUTPUTSTREAMCALLBACK

  NoConnectStream(nsIAsyncInputStream* aInput, nsIAsyncOutputStream* aOutput,
                  const TunnelConfig& aConfig, nsIEventTarget* aSocketTarget,
                  std::function<void()>&& aEstablished,
                  std::function<void(nsresult)>&& aFailed,
                  std::function<void(nsresult)>&& aClosed);
  nsresult Start(const nsACString& aAuthority, Span<const uint8_t> aInitial);
  nsresult StartPump();
  void Cancel(nsresult aStatus);

 private:
  friend class NoConnectCarrier;
  class Impl;
  ~NoConnectStream();
  void ArmRead();
  void Flush();
  void Finish(nsresult aStatus, bool aReset);
  UniquePtr<Impl> mImpl;
};

void ShutdownNoConnectCarriers();

}  // namespace mozilla::naivefox
#endif
