/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "TransportTls.h"

#include <algorithm>
#include <cstring>
#include <limits>

#include "cert.h"
#include "keyhi.h"
#include "pk11pub.h"
#include "prerror.h"
#include "prtime.h"
#include "secerr.h"
#include "ssl.h"
#include "sslproto.h"

namespace mozilla::naivefox {

TransportTls::~TransportTls() {
  if (mSocket) {
    PR_Close(mSocket);
  }
}

const PRIOMethods& TransportTls::Methods() {
  static const PRIOMethods methods = [] {
    PRIOMethods io = *PR_GetDefaultIOMethods();
    io.file_type = PR_DESC_LAYERED;
    io.close = CloseIO;
    io.read = ReadIO;
    io.write = WriteIO;
    io.recv = RecvIO;
    io.send = SendIO;
    io.getsocketoption = GetOption;
    io.setsocketoption = SetOption;
    io.getsockname = GetName;
    io.getpeername = GetName;
    return io;
  }();
  return methods;
}

bool TransportTls::Start(const std::array<uint8_t, 32>& aPin) {
  if (mSocket || mFailed) {
    return false;
  }
  mPin = aPin;
  static const PRDescIdentity identity =
      PR_GetUniqueIdentity("NaiveFox inner TLS");
  PRFileDesc* io = PR_CreateIOLayerStub(identity, &Methods());
  if (!io) {
    mFailed = true;
    return false;
  }
  io->secret = reinterpret_cast<PRFilePrivate*>(this);
  mSocket = SSL_ImportFD(nullptr, io);
  if (!mSocket) {
    PR_Close(io);
    mFailed = true;
    return false;
  }
  SSLVersionRange versions{SSL_LIBRARY_VERSION_TLS_1_3,
                           SSL_LIBRARY_VERSION_TLS_1_3};
  mFailed =
      SSL_VersionRangeSet(mSocket, &versions) != SECSuccess ||
      SSL_OptionSet(mSocket, SSL_SECURITY, PR_TRUE) != SECSuccess ||
      SSL_OptionSet(mSocket, SSL_HANDSHAKE_AS_CLIENT, PR_TRUE) != SECSuccess ||
      SSL_OptionSet(mSocket, SSL_NO_CACHE, PR_TRUE) != SECSuccess ||
      SSL_OptionSet(mSocket, SSL_ENABLE_SESSION_TICKETS, PR_FALSE) !=
          SECSuccess ||
      SSL_OptionSet(mSocket, SSL_ENABLE_0RTT_DATA, PR_FALSE) != SECSuccess ||
      SSL_AuthCertificateHook(mSocket, Authenticate, this) != SECSuccess ||
      SSL_ResetHandshake(mSocket, PR_FALSE) != SECSuccess;
  return !mFailed;
}

TransportTls::Progress TransportTls::Handshake() {
  if (!mSocket || mFailed) {
    return Progress::Failed;
  }
  if (mReady) {
    return Progress::Ready;
  }
  if (SSL_ForceHandshake(mSocket) == SECSuccess) {
    mReady = true;
    return Progress::Ready;
  }
  if (PR_GetError() == PR_WOULD_BLOCK_ERROR) {
    return Progress::Blocked;
  }
  mFailed = true;
  return Progress::Failed;
}

SECStatus TransportTls::Authenticate(void* aArg, PRFileDesc* aSocket, PRBool,
                                     PRBool aServer) {
  auto* self = static_cast<TransportTls*>(aArg);
  CERTCertificate* certificate = SSL_PeerCertificate(aSocket);
  if (!certificate || aServer) {
    if (certificate) {
      CERT_DestroyCertificate(certificate);
    }
    PR_SetError(SEC_ERROR_UNTRUSTED_CERT, 0);
    return SECFailure;
  }
  SECKEYPublicKey* key = CERT_ExtractPublicKey(certificate);
  SECItem* spki = key ? SECKEY_EncodeDERSubjectPublicKeyInfo(key) : nullptr;
  std::array<uint8_t, 32> digest{};
  bool valid = CERT_CheckCertValidTimes(certificate, PR_Now(), PR_FALSE) ==
                   secCertTimeValid &&
               spki &&
               PK11_HashBuf(SEC_OID_SHA256, digest.data(), spki->data,
                            spki->len) == SECSuccess;
  uint8_t difference = 0;
  for (size_t i = 0; i < digest.size(); ++i) {
    difference |= digest[i] ^ self->mPin[i];
  }
  valid = valid && difference == 0;
  if (spki) {
    SECITEM_FreeItem(spki, PR_TRUE);
  }
  if (key) {
    SECKEY_DestroyPublicKey(key);
  }
  CERT_DestroyCertificate(certificate);
  if (!valid) {
    PR_SetError(SEC_ERROR_UNTRUSTED_CERT, 0);
  }
  return valid ? SECSuccess : SECFailure;
}

bool TransportTls::Feed(const uint8_t* aData, size_t aLength) {
  if (mFailed || !mSocket || aLength > kBufferLimit - mInputBytes) {
    mFailed = true;
    return false;
  }
  if (aLength) {
    mInputBytes += aLength;
    while (aLength) {
      if (mInput.empty() || mInput.back().size() == 16384) {
        mInput.emplace_back();
        mInput.back().reserve(16384);
      }
      const size_t amount =
          std::min(aLength, size_t(16384) - mInput.back().size());
      mInput.back().insert(mInput.back().end(), aData, aData + amount);
      aData += amount;
      aLength -= amount;
    }
  }
  return true;
}

TransportTls::Bytes TransportTls::TakeOutput(size_t aLimit) {
  Bytes result;
  const size_t length = std::min(aLimit, mOutputBytes);
  result.reserve(length);
  while (result.size() < length) {
    auto& front = mOutput.front();
    const size_t amount =
        std::min(length - result.size(), front.size() - mOutputOffset);
    result.insert(result.end(), front.begin() + mOutputOffset,
                  front.begin() + mOutputOffset + amount);
    mOutputOffset += amount;
    mOutputBytes -= amount;
    if (mOutputOffset == front.size()) {
      mOutput.pop_front();
      mOutputOffset = 0;
    }
  }
  return result;
}

int32_t TransportTls::Read(uint8_t* aData, size_t aLength) {
  if (!mReady || mFailed || aLength > INT32_MAX) {
    return -1;
  }
  const int32_t result = PR_Read(mSocket, aData, aLength);
  if (result == 0 || (result < 0 && PR_GetError() != PR_WOULD_BLOCK_ERROR)) {
    mFailed = true;
  }
  return result;
}

int32_t TransportTls::Write(const uint8_t* aData, size_t aLength) {
  if (!mReady || mFailed || aLength > INT32_MAX) {
    return -1;
  }
  const int32_t result = PR_Write(mSocket, aData, aLength);
  if (result < 0 && PR_GetError() != PR_WOULD_BLOCK_ERROR) {
    mFailed = true;
  }
  return result;
}

bool TransportTls::Export(std::array<uint8_t, 32>& aSecret) const {
  static constexpr char label[] = "EXPORTER-NaiveFox-packet";
  static constexpr char context[] = "naivefox/https";
  return mReady && !mFailed &&
         SSL_ExportKeyingMaterial(
             mSocket, label, sizeof(label) - 1, PR_TRUE,
             reinterpret_cast<const unsigned char*>(context), sizeof(context) - 1,
             aSecret.data(), aSecret.size()) == SECSuccess;
}

PRStatus TransportTls::CloseIO(PRFileDesc* aFD) {
  aFD->secret = nullptr;
  aFD->dtor(aFD);
  return PR_SUCCESS;
}

PRInt32 TransportTls::ReadIO(PRFileDesc* aFD, void* aData, PRInt32 aLength) {
  auto* self = reinterpret_cast<TransportTls*>(aFD->secret);
  if (aLength <= 0) {
    return 0;
  }
  if (!self->mInputBytes) {
    PR_SetError(PR_WOULD_BLOCK_ERROR, 0);
    return -1;
  }
  auto& front = self->mInput.front();
  const size_t amount =
      std::min(size_t(aLength), front.size() - self->mInputOffset);
  std::memcpy(aData, front.data() + self->mInputOffset, amount);
  self->mInputOffset += amount;
  self->mInputBytes -= amount;
  if (self->mInputOffset == front.size()) {
    self->mInput.pop_front();
    self->mInputOffset = 0;
  }
  return amount;
}

PRInt32 TransportTls::WriteIO(PRFileDesc* aFD, const void* aData,
                              PRInt32 aLength) {
  auto* self = reinterpret_cast<TransportTls*>(aFD->secret);
  if (aLength <= 0) {
    return 0;
  }
  if (size_t(aLength) > kBufferLimit - self->mOutputBytes) {
    PR_SetError(PR_WOULD_BLOCK_ERROR, 0);
    return -1;
  }
  const auto* data = static_cast<const uint8_t*>(aData);
  self->mOutput.emplace_back(data, data + aLength);
  self->mOutputBytes += aLength;
  return aLength;
}

PRInt32 TransportTls::RecvIO(PRFileDesc* aFD, void* aData, PRInt32 aLength,
                             PRIntn aFlags, PRIntervalTime) {
  if (aFlags) {
    PR_SetError(PR_INVALID_ARGUMENT_ERROR, 0);
    return -1;
  }
  return ReadIO(aFD, aData, aLength);
}

PRInt32 TransportTls::SendIO(PRFileDesc* aFD, const void* aData,
                             PRInt32 aLength, PRIntn aFlags, PRIntervalTime) {
  if (aFlags) {
    PR_SetError(PR_INVALID_ARGUMENT_ERROR, 0);
    return -1;
  }
  return WriteIO(aFD, aData, aLength);
}

PRStatus TransportTls::GetOption(PRFileDesc*, PRSocketOptionData* aData) {
  if (aData->option != PR_SockOpt_Nonblocking) {
    PR_SetError(PR_INVALID_ARGUMENT_ERROR, 0);
    return PR_FAILURE;
  }
  aData->value.non_blocking = PR_TRUE;
  return PR_SUCCESS;
}

PRStatus TransportTls::SetOption(PRFileDesc*, const PRSocketOptionData* aData) {
  if (aData->option != PR_SockOpt_Nonblocking || !aData->value.non_blocking) {
    PR_SetError(PR_INVALID_ARGUMENT_ERROR, 0);
    return PR_FAILURE;
  }
  return PR_SUCCESS;
}

PRStatus TransportTls::GetName(PRFileDesc*, PRNetAddr* aAddress) {
  std::memset(aAddress, 0, sizeof(*aAddress));
  aAddress->inet.family = PR_AF_INET;
  return PR_SUCCESS;
}

}  // namespace mozilla::naivefox
