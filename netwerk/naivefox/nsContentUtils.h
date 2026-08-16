/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef naivefox_nsContentUtils_h
#define naivefox_nsContentUtils_h

#include "nsCOMPtr.h"
#include "mozilla/Maybe.h"
#include "nsIContentPolicy.h"
#include "nsIScriptSecurityManager.h"
#include "nsServiceManagerUtils.h"
#include "nsString.h"
#include "nsReadableUtils.h"
#include "nsUnicharUtils.h"

enum class PropertiesFile : uint8_t {
  SECURITY_PROPERTIES,
  NECKO_PROPERTIES,
};

// Parent-only subset used by Necko.  Browser document reporting, permissions,
// and fingerprinting policy deliberately do not belong to the NaiveFox
// runtime; certificate and principal operations still use the real security
// manager.
class nsContentUtils final {
 public:
  class ParsedRange {
   public:
    ParsedRange(mozilla::Maybe<uint64_t> aStart,
                mozilla::Maybe<uint64_t> aEnd)
        : mStart(aStart), mEnd(aEnd) {}
    mozilla::Maybe<uint64_t> Start() const { return mStart; }
    mozilla::Maybe<uint64_t> End() const { return mEnd; }

   private:
    mozilla::Maybe<uint64_t> mStart;
    mozilla::Maybe<uint64_t> mEnd;
  };

  static ExtContentPolicyType InternalContentPolicyTypeToExternal(
      nsContentPolicyType aType) {
    return static_cast<ExtContentPolicyType>(aType);
  }
  static nsIScriptSecurityManager* GetSecurityManager() {
    static nsCOMPtr<nsIScriptSecurityManager> sSecurityManager =
        do_GetService(NS_SCRIPTSECURITYMANAGER_CONTRACTID);
    return sSecurityManager;
  }

  static nsIPrincipal* GetSystemPrincipal() {
    static nsCOMPtr<nsIPrincipal> sSystemPrincipal = [] {
      nsCOMPtr<nsIPrincipal> principal;
      if (auto* securityManager = GetSecurityManager()) {
        securityManager->GetSystemPrincipal(getter_AddRefs(principal));
      }
      return principal;
    }();
    return sSystemPrincipal;
  }

  template <typename... Args>
  static bool ComputeIsSecureContext(Args&&...) {
    return true;
  }

  template <typename... Args>
  static bool ShouldResistFingerprinting(Args&&...) {
    return false;
  }

  static bool IsJavascriptMIMEType(const nsAString& aType) {
    return aType.LowerCaseEqualsLiteral("application/javascript") ||
           aType.LowerCaseEqualsLiteral("text/javascript");
  }

  static bool IsJsonMimeType(const nsAString& aType) {
    return aType.LowerCaseEqualsLiteral("application/json") ||
           StringEndsWith(aType, u"+json"_ns,
                          nsCaseInsensitiveStringComparator);
  }

  static bool IsNonSubresourceInternalPolicyType(nsContentPolicyType) {
    return false;
  }
  static bool IsExpandedPrincipal(nsIPrincipal*) { return false; }
  template <typename... Args>
  static bool ShouldResistFingerprinting_dangerous(Args&&...) {
    return false;
  }
  static bool HtmlObjectContentTypeForMIMEType(const nsACString&) {
    return false;
  }
  static void ASCIIToLower(nsACString& aValue) { ToLowerCase(aValue); }
  static void ASCIIToLower(nsAString& aValue) { ToLowerCase(aValue); }

  static bool HasWasmMimeTypeEssence(const nsACString& aType) {
    return aType.LowerCaseEqualsLiteral("application/wasm");
  }
  static bool HasWasmMimeTypeEssence(const nsAString& aType) {
    return aType.LowerCaseEqualsLiteral("application/wasm");
  }

  template <typename PropertiesFile>
  static nsresult GetLocalizedString(PropertiesFile, const char* aKey,
                                     nsAString& aResult) {
    CopyASCIItoUTF16(nsDependentCString(aKey), aResult);
    return NS_OK;
  }

  template <typename... Args>
  static void ReportToConsole(Args&&...) {}

  template <typename... Args>
  static void ReportToConsoleByWindowID(Args&&...) {}

  template <typename... Args>
  static void LogSimpleConsoleError(Args&&...) {}
};

#endif
