/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "TemporaryTrustStore.h"

#include <climits>
#include <utility>

#include "cert.h"
#include "certdb.h"
#include "certt.h"
#include "mozilla/fallible.h"
#include "nsCOMPtr.h"
#include "nsIEnvironment.h"
#include "nsIFile.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"
#include "prio.h"

#include "nsNSSCertificateDB.h"

namespace mozilla::naivefox {

namespace {

struct CertificatePackage final {
  nsTArray<nsTArray<uint8_t>> mCertificates;
};

bool IsAbsolutePath(const nsAString& aPath) {
#ifdef XP_WIN
  if (aPath.Length() >= 3 &&
      ((aPath.CharAt(0) >= 'A' && aPath.CharAt(0) <= 'Z') ||
       (aPath.CharAt(0) >= 'a' && aPath.CharAt(0) <= 'z')) &&
      aPath.CharAt(1) == ':' && aPath.CharAt(2) == '\\') {
    return true;
  }
  return aPath.Length() >= 2 && aPath.CharAt(0) == '\\' &&
         aPath.CharAt(1) == '\\';
#else
  return !aPath.IsEmpty() && aPath.CharAt(0) == '/';
#endif
}

SECStatus CollectCertificates(void* aArgument, SECItem** aCertificates,
                              int aCount) {
  if (!aArgument || !aCertificates || aCount <= 0) {
    return SECFailure;
  }

  auto& package = *static_cast<CertificatePackage*>(aArgument);
  for (int index = 0; index < aCount; ++index) {
    const SECItem* certificate = aCertificates[index];
    if (!certificate || !certificate->data || certificate->len == 0) {
      return SECFailure;
    }

    nsTArray<uint8_t> der;
    if (!der.AppendElements(certificate->data, certificate->len, fallible) ||
        !package.mCertificates.AppendElement(std::move(der), fallible)) {
      return SECFailure;
    }
  }
  return SECSuccess;
}

nsresult ReadFile(nsIFile* aFile, nsCString& aContents) {
  NS_ENSURE_ARG_POINTER(aFile);

  PRFileDesc* descriptor = nullptr;
  nsresult rv = aFile->OpenNSPRFileDesc(PR_RDONLY, 0, &descriptor);
  if (NS_FAILED(rv) || !descriptor) {
    return NS_FAILED(rv) ? rv : NS_ERROR_FAILURE;
  }

  PRFileInfo fileInfo;
  if (PR_GetOpenFileInfo(descriptor, &fileInfo) != PR_SUCCESS) {
    PR_Close(descriptor);
    return NS_ERROR_FAILURE;
  }
  if (fileInfo.size <= 0 || fileInfo.size > INT_MAX) {
    PR_Close(descriptor);
    return fileInfo.size == 0 ? NS_ERROR_FILE_INVALID_PATH
                              : NS_ERROR_FILE_TOO_BIG;
  }

  if (!aContents.SetLength(static_cast<uint32_t>(fileInfo.size), fallible)) {
    PR_Close(descriptor);
    return NS_ERROR_OUT_OF_MEMORY;
  }

  uint32_t total = 0;
  while (total < aContents.Length()) {
    const int32_t remaining = aContents.Length() - total;
    const int32_t read = PR_Read(descriptor, aContents.BeginWriting() + total,
                                 remaining);
    if (read <= 0) {
      PR_Close(descriptor);
      aContents.Truncate();
      return NS_ERROR_FAILURE;
    }
    total += static_cast<uint32_t>(read);
  }

  PR_Close(descriptor);
  return NS_OK;
}

nsresult DecodeCertificates(nsCString& aContents,
                            CertificatePackage& aPackage) {
  if (aContents.Find("-----BEGIN CERTIFICATE-----") == kNotFound) {
    return NS_ERROR_FAILURE;
  }
  if (CERT_DecodeCertPackage(aContents.BeginWriting(), aContents.Length(),
                             CollectCertificates, &aPackage) != SECSuccess ||
      aPackage.mCertificates.IsEmpty()) {
    return NS_ERROR_FAILURE;
  }
  return NS_OK;
}

}  // namespace

nsresult TemporaryTrustStore::LoadFromEnvironment(nsACString& aError) {
  aError.Truncate();
  mConfigured = false;

  nsCOMPtr<nsIEnvironment> environment =
      do_GetService("@mozilla.org/process/environment;1");
  if (!environment) {
    aError.AssignLiteral("cannot access process environment");
    return NS_ERROR_FAILURE;
  }

  bool exists = false;
  nsresult rv = environment->Exists(u"SSL_CERT_FILE"_ns, &exists);
  if (NS_FAILED(rv)) {
    aError.AssignLiteral("cannot inspect SSL_CERT_FILE");
    return rv;
  }
  if (!exists) {
    return NS_OK;
  }

  nsAutoString path;
  rv = environment->Get(u"SSL_CERT_FILE"_ns, path);
  if (NS_FAILED(rv) || path.IsEmpty()) {
    aError.AssignLiteral("SSL_CERT_FILE must be an absolute PEM path");
    return NS_ERROR_FILE_UNRECOGNIZED_PATH;
  }
  if (!IsAbsolutePath(path)) {
    aError.AssignLiteral("SSL_CERT_FILE must be an absolute PEM path");
    return NS_ERROR_FILE_UNRECOGNIZED_PATH;
  }

  nsCOMPtr<nsIFile> file;
  rv = NS_NewLocalFile(path, getter_AddRefs(file));
  if (NS_FAILED(rv) || !file) {
    aError.AssignLiteral("SSL_CERT_FILE must be an absolute PEM path");
    return NS_ERROR_FILE_UNRECOGNIZED_PATH;
  }

  bool isFile = false;
  rv = file->IsFile(&isFile);
  if (NS_FAILED(rv) || !isFile) {
    aError.AssignLiteral("SSL_CERT_FILE is not a readable file");
    return NS_ERROR_FILE_NOT_FOUND;
  }

  nsCString contents;
  rv = ReadFile(file, contents);
  if (NS_FAILED(rv)) {
    aError.AssignLiteral("cannot read SSL_CERT_FILE");
    return rv;
  }

  CertificatePackage package;
  rv = DecodeCertificates(contents, package);
  if (NS_FAILED(rv)) {
    aError.AssignLiteral("SSL_CERT_FILE is not a valid PEM certificate bundle");
    return NS_ERROR_FAILURE;
  }

  CERTCertTrust trust{CERTDB_VALID_CA | CERTDB_TRUSTED_CA |
                          CERTDB_TRUSTED_CLIENT_CA,
                      0, 0};
  for (const nsTArray<uint8_t>& der : package.mCertificates) {
    SECItem item{siDERCertBuffer, const_cast<unsigned char*>(der.Elements()),
                 static_cast<unsigned int>(der.Length())};
    UniqueCERTCertificate certificate(
        CERT_NewTempCertificate(nullptr, &item, nullptr, false, true));
    if (!certificate || certificate->isperm || certificate->slot) {
      aError.AssignLiteral("SSL_CERT_FILE certificate is not temporary");
      return NS_ERROR_FAILURE;
    }

    bool duplicate = false;
    for (const auto& loaded : mCertificates) {
      if (CERT_CompareCerts(loaded.get(), certificate.get())) {
        duplicate = true;
        break;
      }
    }
    if (duplicate) {
      continue;
    }

    if (ChangeCertTrustWithPossibleAuthentication(certificate, trust,
                                                  nullptr) != SECSuccess) {
      aError.AssignLiteral("cannot trust SSL_CERT_FILE certificate");
      return NS_ERROR_FAILURE;
    }
    if (!mCertificates.AppendElement(std::move(certificate), fallible)) {
      aError.AssignLiteral("cannot retain SSL_CERT_FILE certificate");
      return NS_ERROR_OUT_OF_MEMORY;
    }
  }

  mConfigured = true;
  return NS_OK;
}

}  // namespace mozilla::naivefox
