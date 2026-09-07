/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef netwerk_naivefox_NoConnectSite_h
#define netwerk_naivefox_NoConnectSite_h

#include "mozilla/Encoding.h"
#include "mozilla/Span.h"
#include "mozilla/UniquePtr.h"
#include "nsCOMPtr.h"
#include "nsHtml5SpeculativeScanner.h"
#include "nsString.h"
#include "nsTArray.h"
#include "nsTHashMap.h"

class nsIURI;

namespace mozilla::naivefox {
enum class SiteResourceKind : uint8_t { Style, Script, Image };
struct SiteResource {
  nsCString mPath;
  SiteResourceKind mKind;
  nsCString mMime;
  nsCString mDigest;
};

// Uses Gecko's generated HTML tokenizer/tree builder, with no DOM, scripting,
// style processing or image decoding. Bodies outside the root are leaves.
class NoConnectSite final : public nsHtml5ElementObserver {
 public:
  NoConnectSite();
  ~NoConnectSite();
  nsresult Feed(Span<const uint8_t> aBytes, bool aLast = false);
  nsTArray<SiteResource> TakeResources();
  nsresult OnElement(int32_t aNamespace, nsAtom* aName,
                     nsHtml5HtmlAttributes* aAttributes) override;

 private:
  nsresult Add(const nsACString& aReference, SiteResourceKind aKind);
  UniquePtr<Decoder> mDecoder;
  UniquePtr<nsHtml5SpeculativeScanner> mScanner;
  nsCOMPtr<nsIURI> mBase;
  nsTArray<SiteResource> mResources;
  nsTHashMap<nsCStringHashKey, SiteResourceKind> mKinds;
  nsresult mStatus = NS_OK;
  bool mFinished = false;
};
}  // namespace mozilla::naivefox
#endif
