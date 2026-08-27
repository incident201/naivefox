/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsHtml5LeanPreloadDescriptor_h
#define nsHtml5LeanPreloadDescriptor_h

#include <cstdint>
#include <utility>

#include "nsString.h"

/**
 * Move-only, DOM-free representation of speculative-load operations which
 * directly affect NaiveFox's bounded network preamble. Parser context and
 * resources remain in one ordered stream so consumers must apply or reject
 * them instead of synthesizing their resulting request headers.
 */
class nsHtml5LeanPreloadDescriptor final {
 public:
  enum class Kind : uint8_t {
    Base,
    CSP,
    MetaReferrer,
    Image,
    Script,
    ScriptFromHead,
    Style,
    DocumentCharset,
    DocumentMode,
    CharsetComplaint,
    Unsupported,
  };

  nsHtml5LeanPreloadDescriptor(
      Kind aKind, nsString&& aUrlOrSizes, nsString&& aCharsetOrSrcset,
      nsString&& aTypeOrSizesOrIntegrity, nsString&& aCrossOrigin,
      nsString&& aMedia, nsString&& aNonceOrType,
      nsString&& aReferrerPolicyOrIntegrity, nsString&& aScriptReferrerPolicy,
      nsString&& aFetchPriority, bool aAsync, bool aDefer, bool aLinkPreload)
      : mKind(aKind),
        mUrlOrSizes(std::move(aUrlOrSizes)),
        mCharsetOrSrcset(std::move(aCharsetOrSrcset)),
        mTypeOrSizesOrIntegrity(std::move(aTypeOrSizesOrIntegrity)),
        mCrossOrigin(std::move(aCrossOrigin)),
        mMedia(std::move(aMedia)),
        mNonceOrType(std::move(aNonceOrType)),
        mReferrerPolicyOrIntegrity(std::move(aReferrerPolicyOrIntegrity)),
        mScriptReferrerPolicy(std::move(aScriptReferrerPolicy)),
        mFetchPriority(std::move(aFetchPriority)),
        mAsync(aAsync),
        mDefer(aDefer),
        mLinkPreload(aLinkPreload) {}

  nsHtml5LeanPreloadDescriptor(nsHtml5LeanPreloadDescriptor&&) = default;
  nsHtml5LeanPreloadDescriptor& operator=(nsHtml5LeanPreloadDescriptor&&) =
      default;
  nsHtml5LeanPreloadDescriptor(const nsHtml5LeanPreloadDescriptor&) = delete;
  nsHtml5LeanPreloadDescriptor& operator=(const nsHtml5LeanPreloadDescriptor&) =
      delete;

  Kind GetKind() const { return mKind; }
  const nsString& UrlOrSizes() const { return mUrlOrSizes; }
  const nsString& CharsetOrSrcset() const { return mCharsetOrSrcset; }
  const nsString& TypeOrSizesOrIntegrity() const {
    return mTypeOrSizesOrIntegrity;
  }
  const nsString& CrossOrigin() const { return mCrossOrigin; }
  const nsString& Media() const { return mMedia; }
  const nsString& NonceOrType() const { return mNonceOrType; }
  const nsString& ReferrerPolicyOrIntegrity() const {
    return mReferrerPolicyOrIntegrity;
  }
  const nsString& ScriptReferrerPolicy() const { return mScriptReferrerPolicy; }
  const nsString& FetchPriority() const { return mFetchPriority; }
  bool IsAsync() const { return mAsync; }
  bool IsDefer() const { return mDefer; }
  bool IsLinkPreload() const { return mLinkPreload; }

 private:
  Kind mKind;
  nsString mUrlOrSizes;
  nsString mCharsetOrSrcset;
  nsString mTypeOrSizesOrIntegrity;
  nsString mCrossOrigin;
  nsString mMedia;
  nsString mNonceOrType;
  nsString mReferrerPolicyOrIntegrity;
  nsString mScriptReferrerPolicy;
  nsString mFetchPriority;
  bool mAsync;
  bool mDefer;
  bool mLinkPreload;
};

#endif  // nsHtml5LeanPreloadDescriptor_h
