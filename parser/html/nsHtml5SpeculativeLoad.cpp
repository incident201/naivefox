/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsHtml5SpeculativeLoad.h"
#include "mozilla/Encoding.h"
#ifndef MOZ_NAIVEFOX
#  include "nsHtml5TreeOpExecutor.h"
#endif

using namespace mozilla;

nsHtml5SpeculativeLoad::nsHtml5SpeculativeLoad()
    : mOpCode(eSpeculativeLoadUninitialized),
      mIsAsync(false),
      mIsDefer(false),
      mIsLinkPreload(false),
      mIsError(false),
#ifdef MOZ_NAIVEFOX
      mDescriptorTaken(false),
#endif
      mEncoding(nullptr) {
  MOZ_COUNT_CTOR(nsHtml5SpeculativeLoad);
  new (&mCharsetOrSrcset) nsString;
}

#ifdef MOZ_NAIVEFOX
mozilla::Maybe<nsHtml5StylePreloadDescriptor>
nsHtml5SpeculativeLoad::TakeStyleDescriptor() && {
  if (mOpCode != eSpeculativeLoadStyle || mDescriptorTaken) {
    return mozilla::Nothing();
  }
  mDescriptorTaken = true;
  return mozilla::Some(nsHtml5StylePreloadDescriptor(
      std::move(mUrlOrSizes), std::move(mCharsetOrSrcset),
      std::move(mCrossOrigin), std::move(mMedia),
      std::move(mReferrerPolicyOrIntegrity), std::move(mNonceOrType),
      std::move(mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity),
      mIsLinkPreload, std::move(mFetchPriority)));
}

nsHtml5LeanPreloadDescriptor nsHtml5SpeculativeLoad::TakeLeanDescriptor() && {
  using Kind = nsHtml5LeanPreloadDescriptor::Kind;
  if (mOpCode == eSpeculativeLoadSetDocumentCharset ||
      mOpCode == eSpeculativeLoadMaybeComplainAboutCharset) {
    // These opcodes repurpose the mCharsetOrSrcset union. They affect parser
    // diagnostics/decoding, not network resource activation, and must not be
    // read through the inactive nsString member.
    return nsHtml5LeanPreloadDescriptor(
        mOpCode == eSpeculativeLoadSetDocumentCharset ? Kind::DocumentCharset
                                                      : Kind::CharsetComplaint,
        nsString(), nsString(), nsString(), nsString(), nsString(), nsString(),
        nsString(), nsString(), nsString(), false, false, false);
  }
  Kind kind = Kind::Unsupported;
  switch (mOpCode) {
    case eSpeculativeLoadBase:
      kind = Kind::Base;
      break;
    case eSpeculativeLoadCSP:
      kind = Kind::CSP;
      break;
    case eSpeculativeLoadMetaReferrer:
      kind = Kind::MetaReferrer;
      break;
    case eSpeculativeLoadImage:
      kind = Kind::Image;
      break;
    case eSpeculativeLoadScript:
      kind = Kind::Script;
      break;
    case eSpeculativeLoadScriptFromHead:
      kind = Kind::ScriptFromHead;
      break;
    case eSpeculativeLoadStyle:
      kind = Kind::Style;
      break;
    case eSpeculativeLoadSetDocumentMode:
      kind = Kind::DocumentMode;
      break;
    default:
      break;
  }
  mDescriptorTaken = true;
  return nsHtml5LeanPreloadDescriptor(
      kind, std::move(mUrlOrSizes), std::move(mCharsetOrSrcset),
      std::move(mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity),
      std::move(mCrossOrigin), std::move(mMedia), std::move(mNonceOrType),
      std::move(mReferrerPolicyOrIntegrity), std::move(mScriptReferrerPolicy),
      std::move(mFetchPriority), mIsAsync, mIsDefer, mIsLinkPreload);
}
#endif

nsHtml5SpeculativeLoad::~nsHtml5SpeculativeLoad() {
  MOZ_COUNT_DTOR(nsHtml5SpeculativeLoad);
  NS_ASSERTION(mOpCode != eSpeculativeLoadUninitialized,
               "Uninitialized speculative load.");
  if (!(mOpCode == eSpeculativeLoadSetDocumentCharset ||
        mOpCode == eSpeculativeLoadMaybeComplainAboutCharset)) {
    mCharsetOrSrcset.~nsString();
  }
}

#ifndef MOZ_NAIVEFOX
void nsHtml5SpeculativeLoad::Perform(nsHtml5TreeOpExecutor* aExecutor) {
  switch (mOpCode) {
    case eSpeculativeLoadBase:
      aExecutor->SetSpeculationBase(mUrlOrSizes);
      break;
    case eSpeculativeLoadCSP:
      aExecutor->AddSpeculationCSP(
          mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity);
      break;
    case eSpeculativeLoadMetaReferrer:
      aExecutor->UpdateReferrerInfoFromMeta(mReferrerPolicyOrIntegrity);
      break;
    case eSpeculativeLoadImage:
      aExecutor->PreloadImage(
          mUrlOrSizes, mCrossOrigin, mMedia, mCharsetOrSrcset,
          mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity,
          mReferrerPolicyOrIntegrity, mIsLinkPreload, mFetchPriority,
          mNonceOrType);
      break;
    case eSpeculativeLoadOpenPicture:
      aExecutor->PreloadOpenPicture();
      break;
    case eSpeculativeLoadEndPicture:
      aExecutor->PreloadEndPicture();
      break;
    case eSpeculativeLoadPictureSource:
      aExecutor->PreloadPictureSource(
          mCharsetOrSrcset, mUrlOrSizes,
          mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity,
          mMedia);
      break;
    case eSpeculativeLoadScript:
      aExecutor->PreloadScript(
          mUrlOrSizes, mCharsetOrSrcset,
          mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity,
          mCrossOrigin, mMedia, mNonceOrType, mFetchPriority,
          mReferrerPolicyOrIntegrity, mScriptReferrerPolicy, false, mIsAsync,
          mIsDefer, mIsLinkPreload);
      break;
    case eSpeculativeLoadScriptFromHead:
      aExecutor->PreloadScript(
          mUrlOrSizes, mCharsetOrSrcset,
          mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity,
          mCrossOrigin, mMedia, mNonceOrType, mFetchPriority,
          mReferrerPolicyOrIntegrity, mScriptReferrerPolicy, true, mIsAsync,
          mIsDefer, mIsLinkPreload);
      break;
    case eSpeculativeLoadStyle:
      aExecutor->PreloadStyle(
          mUrlOrSizes, mCharsetOrSrcset, mCrossOrigin, mMedia,
          mReferrerPolicyOrIntegrity, mNonceOrType,
          mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity,
          mIsLinkPreload, mFetchPriority);
      break;
    case eSpeculativeLoadManifest:
      // TODO: remove this
      break;
    case eSpeculativeLoadSetDocumentCharset: {
      MOZ_ASSERT(mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                         .Length() == 1,
                 "Unexpected charset source string");
      nsCharsetSource enumSource =
          (nsCharsetSource)
              mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                  .First();
      aExecutor->SetDocumentCharsetAndSource(WrapNotNull(mEncoding),
                                             enumSource);
      if (mCommitEncodingSpeculation) {
        aExecutor->CommitToInternalEncoding();
      }
    } break;
    case eSpeculativeLoadSetDocumentMode: {
      NS_ASSERTION(mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                           .Length() == 1,
                   "Unexpected document mode string");
      nsHtml5DocumentMode mode =
          (nsHtml5DocumentMode)
              mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                  .First();
      aExecutor->SetDocumentMode(mode);
    } break;
    case eSpeculativeLoadPreconnect:
      aExecutor->Preconnect(mUrlOrSizes, mCrossOrigin);
      break;
    case eSpeculativeLoadFont:
      aExecutor->PreloadFont(mUrlOrSizes, mCrossOrigin, mMedia,
                             mReferrerPolicyOrIntegrity, mFetchPriority);
      break;
    case eSpeculativeLoadFetch:
      aExecutor->PreloadFetch(mUrlOrSizes, mCrossOrigin, mMedia,
                              mReferrerPolicyOrIntegrity, mFetchPriority);
      break;
    case eSpeculativeLoadMaybeComplainAboutCharset: {
      MOZ_ASSERT(mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                         .Length() == 2,
                 "Unexpected line number string");
      uint32_t high =
          (uint32_t)
              mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                  .CharAt(0);
      uint32_t low =
          (uint32_t)
              mTypeOrCharsetSourceOrDocumentModeOrMetaCSPOrSizesOrIntegrity
                  .CharAt(1);
      uint32_t line = (high << 16) | low;
      aExecutor->MaybeComplainAboutCharset(mMsgId, mIsError, (int32_t)line);
    } break;
    default:
      MOZ_ASSERT_UNREACHABLE("Bogus speculative load.");
      break;
  }
}
#endif
