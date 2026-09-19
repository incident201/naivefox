/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef netwerk_naivefox_TransportPacket_h
#define netwerk_naivefox_TransportPacket_h

#include <functional>
#include <memory>
#include <vector>

#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "nsString.h"

namespace mozilla::naivefox {
struct TransportConfig;
class TransportCookies;

class TransportPacket final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(TransportPacket)
  using Bytes = std::vector<uint8_t>;
  TransportPacket(std::function<void()>&& aReady,
                  std::function<bool(const Bytes&, bool)>&& aCell,
                  std::function<void()>&& aWritable,
                  std::function<void(nsresult)>&& aFailed);
  nsresult Start(const TransportConfig& aConfig,
                 std::shared_ptr<TransportCookies> aCookies,
                 const Bytes& aAuth);
  bool Send(const Bytes& aBody);
  bool Blocked() const;
  const nsCString& SessionID() const;
  void Close();

 private:
  ~TransportPacket();
  class Impl;
  UniquePtr<Impl> mImpl;
};
}  // namespace mozilla::naivefox
#endif
