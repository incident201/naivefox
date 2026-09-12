/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "TransportSite.h"

#include <array>
#include <initializer_list>

#include "NameSpaceConstants.h"
#include "nsAtom.h"
#include "nsEscape.h"
#include "nsHtml5HtmlAttributes.h"
#include "nsIURI.h"
#include "nsNetUtil.h"
#include "nsUnicharUtils.h"

namespace mozilla::naivefox {
TransportSite::TransportSite()
    : mDecoder(UTF_8_ENCODING->NewDecoderWithoutBOMHandling()),
      mScanner(MakeUnique<nsHtml5SpeculativeScanner>(nullptr, this)) {
  mStatus = NS_NewURI(getter_AddRefs(mBase), "https://naivefox.invalid/"_ns);
}
TransportSite::~TransportSite() = default;

nsresult TransportSite::Feed(Span<const uint8_t> aBytes, bool aLast) {
  if (NS_FAILED(mStatus) || mFinished) {
    return NS_FAILED(mStatus) ? mStatus : NS_ERROR_UNEXPECTED;
  }
  std::array<char16_t, 8192> output;
  for (;;) {
    const auto [result, read, written] =
        mDecoder->DecodeToUTF16WithoutReplacement(aBytes, Span(output), aLast);
    if (result != kInputEmpty && result != kOutputFull) {
      return mStatus = NS_ERROR_ILLEGAL_INPUT;
    }
    for (size_t i = 0; i < written; ++i) {
      if (!output[i]) {
        return mStatus = NS_ERROR_ILLEGAL_INPUT;
      }
    }
    mStatus = mScanner->Feed(nsDependentSubstring(output.data(), written));
    if (NS_FAILED(mStatus)) {
      return mStatus;
    }
    aBytes = aBytes.From(read);
    if (result == kInputEmpty) {
      break;
    }
  }
  if (aLast) {
    mStatus = mScanner->Finish();
    mFinished = true;
    mScanner = nullptr;
  }
  return mStatus;
}
nsTArray<SiteResource> TransportSite::TakeResources() {
  MOZ_ASSERT(mFinished && NS_SUCCEEDED(mStatus));
  return std::move(mResources);
}

nsresult TransportSite::OnElement(int32_t aNamespace, nsAtom* aName,
                                  nsHtml5HtmlAttributes* aAttributes) {
  auto has = [&](const char* aKey) {
    for (const auto& entry : *aAttributes) {
      if (entry.NameHTML()->Equals(NS_ConvertASCIItoUTF16(aKey))) {
        return true;
      }
    }
    return false;
  };
  auto get = [&](const char* aKey) {
    nsAutoString value;
    for (const auto& entry : *aAttributes) {
      if (entry.NameHTML()->Equals(NS_ConvertASCIItoUTF16(aKey))) {
        entry.Value().ToString(value);
        break;
      }
    }
    return NS_ConvertUTF16toUTF8(value);
  };
  auto lower = [&](const char* aKey) {
    nsCString value(get(aKey));
    ToLowerCase(value);
    return value;
  };
  auto any = [&](std::initializer_list<const char*> aKeys) {
    for (const auto* key : aKeys) {
      if (has(key)) {
        return true;
      }
    }
    return false;
  };
  const nsresult bad = NS_ERROR_CORRUPTED_CONTENT;
  if (aNamespace != kNameSpaceID_XHTML) {
    return any({"href", "src"}) ? bad : NS_OK;
  }
  nsAutoCString tag;
  aName->ToUTF8String(tag);
  if (tag.EqualsLiteral("base")) {
    return bad;
  }
  if (tag.EqualsLiteral("meta") &&
      (lower("http-equiv").EqualsLiteral("refresh") ||
       lower("http-equiv").EqualsLiteral("content-security-policy") ||
       lower("name").EqualsLiteral("referrer"))) {
    return bad;
  }
  for (const char* unsupported : {"iframe", "frame", "object", "embed", "audio",
                                  "video", "source", "picture"}) {
    if (tag.Equals(unsupported)) {
      return bad;
    }
  }
  nsCString ref;
  SiteResourceKind kind;
  if (tag.EqualsLiteral("link")) {
    nsCString rel(lower("rel"));
    rel.CompressWhitespace();
    if (rel.EqualsLiteral("stylesheet")) {
      if (any({"disabled", "media", "title"})) {
        return bad;
      }
      kind = SiteResourceKind::Style;
    } else if (rel.EqualsLiteral("preload")) {
      if (!lower("as").EqualsLiteral("image") ||
          any({"imagesrcset", "imagesizes", "media"})) {
        return bad;
      }
      kind = SiteResourceKind::Image;
    } else if (rel.EqualsLiteral("icon") ||
               rel.EqualsLiteral("shortcut icon")) {
      if (any({"media", "sizes"})) {
        return bad;
      }
      kind = SiteResourceKind::Image;
    } else {
      for (const char* ignored :
           {"", "canonical", "alternate", "author", "help", "license", "next",
            "prev", "search"}) {
        if (rel.Equals(ignored)) {
          return NS_OK;
        }
      }
      return bad;
    }
    ref = get("href");
  } else if (tag.EqualsLiteral("script")) {
    nsCString type(lower("type"));
    if (!has("src")) {
      return type.EqualsLiteral("module") || type.EqualsLiteral("importmap") ||
                     type.EqualsLiteral("speculationrules")
                 ? bad
                 : NS_OK;
    }
    if (!has("defer") || any({"async", "nomodule"}) ||
        (!type.IsEmpty() && !type.EqualsLiteral("text/javascript") &&
         !type.EqualsLiteral("application/javascript"))) {
      return bad;
    }
    kind = SiteResourceKind::Script;
    ref = get("src");
  } else if (tag.EqualsLiteral("img")) {
    if (any({"srcset", "sizes"}) ||
        (has("loading") && !lower("loading").EqualsLiteral("eager"))) {
      return bad;
    }
    kind = SiteResourceKind::Image;
    ref = get("src");
  } else if (tag.EqualsLiteral("input")) {
    return lower("type").EqualsLiteral("image") ? bad : NS_OK;
  } else {
    return NS_OK;
  }
  if (any({"crossorigin", "integrity", "nonce", "referrerpolicy",
           "fetchpriority", "charset"})) {
    return bad;
  }
  return Add(ref, kind);
}

nsresult TransportSite::Add(const nsACString& aReference,
                            SiteResourceKind aKind) {
  nsAutoCString ref(aReference);
  ref.Trim(" \t\r\n\f");
  nsAutoCString lower(ref);
  ToLowerCase(lower);
  if (StringBeginsWith(lower, "data:"_ns)) {
    return NS_OK;
  }
  if (ref.IsEmpty() || StringBeginsWith(ref, "//"_ns) ||
      ref.FindChar('\\') >= 0 || ref.FindChar('\0') >= 0 ||
      ref.FindChar('\r') >= 0 || ref.FindChar('\n') >= 0 ||
      ref.FindChar('\t') >= 0) {
    return NS_ERROR_DOM_BAD_URI;
  }
  int32_t fragment = ref.FindChar('#');
  if (fragment >= 0) {
    ref.Truncate(fragment);
  }
  nsAutoCString rawPath(ref);
  int32_t query = rawPath.FindChar('?');
  if (query >= 0) {
    rawPath.Truncate(query);
  }
  // Scheme-bearing URLs are outside the root-relative application contract.
  const int32_t colon = rawPath.FindChar(':');
  const int32_t slash = rawPath.FindChar('/');
  if (colon >= 0 && (slash < 0 || colon < slash)) {
    return NS_ERROR_DOM_BAD_URI;
  }
  nsAutoCString decoded;
  NS_UnescapeURL(rawPath, esc_AlwaysCopy, decoded);
  if (decoded.FindChar('\\') >= 0 || decoded.FindChar('\0') >= 0) {
    return NS_ERROR_DOM_BAD_URI;
  }
  nsAutoCString delimited("/"_ns);
  delimited.Append(decoded);
  delimited.Append('/');
  if (delimited.Find("/../") >= 0) {
    return NS_ERROR_DOM_BAD_URI;
  }
  nsCOMPtr<nsIURI> uri;
  MOZ_TRY(NS_NewURI(getter_AddRefs(uri), ref, nullptr, mBase));
  nsAutoCString prePath;
  MOZ_TRY(uri->GetPrePath(prePath));
  if (!prePath.EqualsLiteral("https://naivefox.invalid")) {
    return NS_ERROR_DOM_BAD_URI;
  }
  nsCString target;
  MOZ_TRY(uri->GetPathQueryRef(target));
  nsAutoCString filePath(target);
  query = filePath.FindChar('?');
  if (query >= 0) {
    filePath.Truncate(query);
  }
  nsAutoCString file;
  NS_UnescapeURL(filePath, esc_AlwaysCopy, file);
  if (file.EqualsLiteral("/") || file.EqualsLiteral("/index.html") ||
      file.EqualsLiteral("/api/sync") || file.EqualsLiteral("/api/realtime") ||
      file.EqualsLiteral("/api/events/brief") ||
      file.EqualsLiteral("/api/events/state") ||
      StringBeginsWith(file, "/media/chunk/"_ns) ||
      StringBeginsWith(file, "/__lab/"_ns) || StringEndsWith(file, "/"_ns)) {
    return NS_ERROR_DOM_BAD_URI;
  }
  if (auto existing = mKinds.Lookup(target)) {
    return existing.Data() == aKind ? NS_OK : NS_ERROR_CORRUPTED_CONTENT;
  }
  mKinds.InsertOrUpdate(target, aKind);
  mResources.AppendElement(SiteResource{std::move(target), aKind});
  return NS_OK;
}
}  // namespace mozilla::naivefox
