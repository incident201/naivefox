/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_TransportTls_h
#define netwerk_naivefox_TransportTls_h

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

#include "prio.h"
#include "seccomon.h"

namespace mozilla::naivefox {

class TransportTls final {
 public:
  using Bytes = std::vector<uint8_t>;
  static constexpr size_t kBufferLimit = 512 * 1024;
  enum class Progress { Blocked, Ready, Failed };

  TransportTls() = default;
  ~TransportTls();
  TransportTls(const TransportTls&) = delete;
  TransportTls& operator=(const TransportTls&) = delete;

  bool Start(const std::array<uint8_t, 32>& aPin);
  Progress Handshake();
  bool Feed(const uint8_t* aData, size_t aLength);
  Bytes TakeOutput(size_t aLimit = 65536);
  int32_t Read(uint8_t* aData, size_t aLength);
  int32_t Write(const uint8_t* aData, size_t aLength);
  bool Export(std::array<uint8_t, 32>& aSecret) const;
  bool Ready() const { return mReady; }
  bool Failed() const { return mFailed; }
  size_t PendingOutput() const { return mOutputBytes; }
  size_t PendingInput() const { return mInputBytes; }

 private:
  static const PRIOMethods& Methods();
  static PRStatus CloseIO(PRFileDesc*);
  static PRInt32 ReadIO(PRFileDesc*, void*, PRInt32);
  static PRInt32 WriteIO(PRFileDesc*, const void*, PRInt32);
  static PRInt32 RecvIO(PRFileDesc*, void*, PRInt32, PRIntn, PRIntervalTime);
  static PRInt32 SendIO(PRFileDesc*, const void*, PRInt32, PRIntn,
                        PRIntervalTime);
  static PRStatus GetOption(PRFileDesc*, PRSocketOptionData*);
  static PRStatus SetOption(PRFileDesc*, const PRSocketOptionData*);
  static PRStatus GetName(PRFileDesc*, PRNetAddr*);
  static SECStatus Authenticate(void*, PRFileDesc*, PRBool, PRBool);

  PRFileDesc* mSocket = nullptr;
  std::array<uint8_t, 32> mPin{};
  std::deque<Bytes> mInput, mOutput;
  size_t mInputBytes = 0, mOutputBytes = 0;
  size_t mInputOffset = 0, mOutputOffset = 0;
  bool mReady = false, mFailed = false;
};

}  // namespace mozilla::naivefox
#endif
