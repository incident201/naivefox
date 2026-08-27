/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsHtml5StylePreloadDescriptor_h
#define nsHtml5StylePreloadDescriptor_h

#include "nsString.h"

/** A move-only stylesheet descriptor produced by the HTML5 preloader. */
class nsHtml5StylePreloadDescriptor final {
 public:
  nsHtml5StylePreloadDescriptor(
      nsString&& aUrl, nsString&& aCharset, nsString&& aCrossOrigin,
      nsString&& aMedia, nsString&& aReferrerPolicy, nsString&& aNonce,
      nsString&& aIntegrity, bool aLinkPreload, nsString&& aFetchPriority)
      : mUrl(std::move(aUrl)),
        mCharset(std::move(aCharset)),
        mCrossOrigin(std::move(aCrossOrigin)),
        mMedia(std::move(aMedia)),
        mReferrerPolicy(std::move(aReferrerPolicy)),
        mNonce(std::move(aNonce)),
        mIntegrity(std::move(aIntegrity)),
        mFetchPriority(std::move(aFetchPriority)),
        mLinkPreload(aLinkPreload) {}

  nsHtml5StylePreloadDescriptor(nsHtml5StylePreloadDescriptor&&) = default;
  nsHtml5StylePreloadDescriptor& operator=(nsHtml5StylePreloadDescriptor&&) =
      default;
  nsHtml5StylePreloadDescriptor(const nsHtml5StylePreloadDescriptor&) = delete;
  nsHtml5StylePreloadDescriptor& operator=(
      const nsHtml5StylePreloadDescriptor&) = delete;

  const nsString& Url() const { return mUrl; }
  const nsString& Charset() const { return mCharset; }
  const nsString& CrossOrigin() const { return mCrossOrigin; }
  const nsString& Media() const { return mMedia; }
  const nsString& ReferrerPolicy() const { return mReferrerPolicy; }
  const nsString& Nonce() const { return mNonce; }
  const nsString& Integrity() const { return mIntegrity; }
  const nsString& FetchPriority() const { return mFetchPriority; }
  bool IsLinkPreload() const { return mLinkPreload; }

 private:
  nsString mUrl;
  nsString mCharset;
  nsString mCrossOrigin;
  nsString mMedia;
  nsString mReferrerPolicy;
  nsString mNonce;
  nsString mIntegrity;
  nsString mFetchPriority;
  bool mLinkPreload;
};

#endif  // nsHtml5StylePreloadDescriptor_h
