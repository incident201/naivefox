/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "TransportCookies.h"

#include <algorithm>
#include <limits>
#include <string_view>

#include "mozilla/Try.h"
#include "nsIEffectiveTLDService.h"
#include "nsIHttpChannel.h"
#include "nsIHttpHeaderVisitor.h"
#include "nsIURI.h"
#include "nsNetUtil.h"
#include "prtime.h"

namespace mozilla::naivefox {
namespace {

constexpr size_t kMaxCookies = 32;
constexpr size_t kMaxCookieBytes = 16 * 1024;
constexpr size_t kMaxResponseCookieBytes = 32 * 1024;

std::string_view TrimCookie(std::string_view aText) {
  const auto start = aText.find_first_not_of(" \t");
  if (start == aText.npos) {
    return {};
  }
  return aText.substr(start, aText.find_last_not_of(" \t") - start + 1);
}

bool CookieName(std::string_view aName) {
  return !aName.empty() &&
         std::all_of(aName.begin(), aName.end(), [](unsigned char c) {
           return c > 32 && c < 127 &&
                  std::string_view("()<>@,;:\\\"/[]?={}").find(c) ==
                      std::string_view::npos;
         });
}

bool CookieValue(std::string_view aValue) {
  if (aValue.size() >= 2 && aValue.front() == '"' && aValue.back() == '"') {
    aValue.remove_prefix(1);
    aValue.remove_suffix(1);
  }
  return std::all_of(aValue.begin(), aValue.end(), [](unsigned char c) {
    return c == 0x21 || (c >= 0x23 && c <= 0x2b) || (c >= 0x2d && c <= 0x3a) ||
           (c >= 0x3c && c <= 0x5b) || (c >= 0x5d && c <= 0x7e);
  });
}

bool ValidSession(const nsACString& aValue) {
  return aValue.Length() == 64 &&
         std::all_of(aValue.BeginReading(), aValue.EndReading(), [](char c) {
           return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
         });
}

bool DomainSuffix(const nsACString& aHost, const nsACString& aDomain) {
  return aHost.Equals(aDomain) ||
         (aHost.Length() > aDomain.Length() &&
          aHost.CharAt(aHost.Length() - aDomain.Length() - 1) == '.' &&
          StringEndsWith(aHost, aDomain));
}

class CookieVisitor final : public nsIHttpHeaderVisitor {
 public:
  NS_DECL_THREADSAFE_ISUPPORTS
  CookieVisitor(TransportCookies& aCookies, nsIURI* aURI,
                const nsACString& aDate)
      : mCookies(aCookies), mURI(aURI), mDate(aDate) {}
  NS_IMETHOD VisitHeader(const nsACString& aName,
                         const nsACString& aValue) override {
    if (!aName.LowerCaseEqualsLiteral("set-cookie")) {
      return NS_OK;
    }
    if (aValue.Length() > kMaxResponseCookieBytes - mBytes) {
      return NS_ERROR_FILE_TOO_BIG;
    }
    mBytes += aValue.Length();
    return mCookies.Store(mURI, aValue, mDate, PR_Now());
  }

 private:
  ~CookieVisitor() = default;
  TransportCookies& mCookies;
  nsCOMPtr<nsIURI> mURI;
  nsCString mDate;
  size_t mBytes = 0;
};
NS_IMPL_ISUPPORTS(CookieVisitor, nsIHttpHeaderVisitor)

}  // namespace

nsresult TransportCookies::CheckOrigin(nsIURI* aURI) {
  if (mClosed || !aURI || !aURI->SchemeIs("https")) {
    return NS_ERROR_DOM_BAD_URI;
  }
  nsAutoCString origin;
  MOZ_TRY(aURI->GetPrePath(origin));
  if (mOrigin.IsEmpty()) {
    mOrigin = origin;
  }
  return origin.Equals(mOrigin) ? NS_OK : NS_ERROR_DOM_BAD_URI;
}

void TransportCookies::Expire(int64_t aNow) {
  mCookies.erase(
      std::remove_if(mCookies.begin(), mCookies.end(),
                     [aNow](const auto& c) { return c.expires <= aNow; }),
      mCookies.end());
}

nsresult TransportCookies::Store(nsIURI* aURI, const nsACString& aHeader,
                                 const nsACString& aDate, int64_t aNow) {
  MOZ_TRY(CheckOrigin(aURI));
  if (aHeader.Length() > 4096) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  std::string_view text(aHeader.BeginReading(), aHeader.Length());
  for (unsigned char c : text) {
    if ((c < 32 && c != '\t') || c == 127) {
      return NS_OK;
    }
  }
  auto first = text.substr(0, text.find(';'));
  const auto eq = first.find('=');
  if (eq == first.npos) {
    return NS_OK;
  }
  const auto name = TrimCookie(first.substr(0, eq));
  const auto value = TrimCookie(first.substr(eq + 1));
  if (!CookieName(name) || !CookieValue(value)) {
    return NS_OK;
  }
  Cookie cookie;
  cookie.name.Assign(name.data(), name.size());
  cookie.value.Assign(value.data(), value.size());
  nsAutoCString host;
  MOZ_TRY(aURI->GetAsciiHost(host));
  cookie.domain = host;
  nsAutoCString requestPath;
  MOZ_TRY(aURI->GetFilePath(requestPath));
  const int32_t slash = requestPath.RFindChar('/');
  cookie.path.AssignLiteral("/");
  if (slash > 0) {
    cookie.path = Substring(requestPath, 0, slash);
  }
  bool domainAttribute = false;
  bool explicitRootPath = false;
  nsCString maxAge;
  nsCString expires;
  while (text.find(';') != text.npos) {
    text.remove_prefix(text.find(';') + 1);
    const auto attr = text.substr(0, text.find(';'));
    const auto separator = attr.find('=');
    const auto key = TrimCookie(attr.substr(0, separator));
    const auto val = separator == attr.npos
                         ? std::string_view()
                         : TrimCookie(attr.substr(separator + 1));
    nsAutoCString attribute(key.data(), key.size());
    nsAutoCString content(val.data(), val.size());
    if (attribute.LowerCaseEqualsLiteral("secure")) {
      cookie.secure = true;
    } else if (attribute.LowerCaseEqualsLiteral("domain") &&
               !content.IsEmpty()) {
      domainAttribute = true;
      if (content.First() == '.') {
        content.Cut(0, 1);
      }
      ToLowerCase(content);
      if (!DomainSuffix(host, content)) {
        return NS_OK;
      }
      nsCOMPtr<nsIEffectiveTLDService> tld =
          do_GetService(NS_EFFECTIVETLDSERVICE_CONTRACTID);
      if (!tld) {
        return NS_ERROR_NOT_AVAILABLE;
      }
      nsAutoCString base;
      nsresult rv = tld->GetBaseDomainFromHost(content, 0, base);
      if (NS_FAILED(rv) && !host.Equals(content)) {
        return NS_OK;
      }
      cookie.domain = content;
    } else if (attribute.LowerCaseEqualsLiteral("path") &&
               StringBeginsWith(content, "/"_ns)) {
      cookie.path = content;
      explicitRootPath = content.EqualsLiteral("/");
    } else if (attribute.LowerCaseEqualsLiteral("max-age")) {
      maxAge = content;
    } else if (attribute.LowerCaseEqualsLiteral("expires")) {
      expires = content;
    }
  }
  if ((StringBeginsWith(cookie.name, "__Secure-"_ns) && !cookie.secure) ||
      (StringBeginsWith(cookie.name, "__Host-"_ns) &&
       (!cookie.secure || domainAttribute || !explicitRootPath))) {
    return NS_OK;
  }
  PRTime expiry;
  if (!expires.IsEmpty() &&
      PR_ParseTimeString(expires.get(), true, &expiry) == PR_SUCCESS) {
    PRTime serverDate;
    if (!aDate.IsEmpty() &&
        PR_ParseTimeString(nsCString(aDate).get(), true, &serverDate) ==
            PR_SUCCESS &&
        serverDate > 0 && aNow > 0 && expiry >= 0 && expiry <= INT64_MAX / 2 &&
        serverDate <= INT64_MAX / 2 && aNow <= INT64_MAX / 2) {
      expiry = aNow + (expiry - serverDate);
    }
    cookie.expires = expiry;
  }
  if (!maxAge.IsEmpty()) {
    const std::string_view age(maxAge.BeginReading(), maxAge.Length());
    const bool negative = age.front() == '-';
    const auto digits = age.substr(negative ? 1 : 0);
    if (!digits.empty() &&
        std::all_of(digits.begin(), digits.end(),
                    [](char c) { return c >= '0' && c <= '9'; })) {
      int64_t seconds = 0;
      constexpr int64_t cap = 400LL * 24 * 60 * 60;
      for (char c : digits) {
        seconds = std::min(cap, seconds * 10 + c - '0');
      }
      cookie.expires =
          negative || !seconds ? 0 : aNow + seconds * PR_USEC_PER_SEC;
    }
  }
  if (cookie.name.EqualsLiteral("session") &&
      (!ValidSession(cookie.value) || !cookie.path.EqualsLiteral("/") ||
       !cookie.secure)) {
    return NS_ERROR_CORRUPTED_CONTENT;
  }
  if (cookie.name.EqualsLiteral("session")) {
    cookie.domain = host;
  }
  Expire(aNow);
  auto existing =
      std::find_if(mCookies.begin(), mCookies.end(), [&](const auto& c) {
        return c.name == cookie.name && c.path == cookie.path &&
               c.domain == cookie.domain;
      });
  if (cookie.expires <= aNow) {
    if (existing != mCookies.end()) {
      mCookies.erase(existing);
    }
    return NS_OK;
  }
  if (existing == mCookies.end() && mCookies.size() == kMaxCookies) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  auto size = [](const auto& c) {
    return c.name.Length() + c.value.Length() + c.path.Length() +
           c.domain.Length() + 4;
  };
  size_t bytes = size(cookie);
  for (const auto& c : mCookies) {
    if (&c != (existing == mCookies.end() ? nullptr : &*existing)) {
      bytes += size(c);
    }
  }
  if (bytes > kMaxCookieBytes) {
    return NS_ERROR_FILE_TOO_BIG;
  }
  if (existing != mCookies.end()) {
    *existing = std::move(cookie);
  } else {
    mCookies.push_back(std::move(cookie));
  }
  return NS_OK;
}

nsresult TransportCookies::RequestHeader(nsIURI* aURI, nsACString& aHeader) {
  MOZ_TRY(CheckOrigin(aURI));
  Expire(PR_Now());
  nsAutoCString path;
  MOZ_TRY(aURI->GetFilePath(path));
  std::vector<const Cookie*> matching;
  for (const auto& cookie : mCookies) {
    if (path.Equals(cookie.path) ||
        (StringBeginsWith(path, cookie.path) &&
         (cookie.path.Last() == '/' ||
          (path.Length() > cookie.path.Length() &&
           path.CharAt(cookie.path.Length()) == '/')))) {
      matching.push_back(&cookie);
    }
  }
  std::stable_sort(matching.begin(), matching.end(),
                   [](const auto* a, const auto* b) {
                     return a->path.Length() > b->path.Length();
                   });
  aHeader.Truncate();
  for (const auto* cookie : matching) {
    if (!aHeader.IsEmpty()) {
      aHeader.AppendLiteral("; ");
    }
    aHeader.Append(cookie->name);
    aHeader.Append('=');
    aHeader.Append(cookie->value);
  }
  return NS_OK;
}

nsresult TransportCookies::ResponseHeaders(nsIChannel* aChannel) {
  nsCOMPtr<nsIURI> uri;
  MOZ_TRY(aChannel->GetURI(getter_AddRefs(uri)));
  MOZ_TRY(CheckOrigin(uri));
  nsCOMPtr<nsIHttpChannel> http = do_QueryInterface(aChannel);
  if (!http) {
    return NS_ERROR_NO_INTERFACE;
  }
  nsAutoCString date;
  (void)http->GetResponseHeader("Date"_ns, date);
  RefPtr visitor = new CookieVisitor(*this, uri, date);
  return http->VisitOriginalResponseHeaders(visitor);
}

bool TransportCookies::HasSession() {
  Expire(PR_Now());
  return std::any_of(mCookies.begin(), mCookies.end(), [](const auto& c) {
    return c.name.EqualsLiteral("session") && ValidSession(c.value);
  });
}

void TransportCookies::Clear() {
  mCookies.clear();
  mOrigin.Truncate();
  mClosed = true;
}

}  // namespace mozilla::naivefox
