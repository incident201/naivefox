/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "ErrorList.h"
#include "nsHtml5AttributeName.h"
#include "nsHtml5HtmlAttributes.h"
#include <cstring>

nsHtml5TreeBuilder::nsHtml5TreeBuilder(nsAHtml5SpeculativeLoadStage* aStage)
    : mode(0),
      originalMode(0),
      framesetOk(false),
      tokenizer(nullptr),
      scriptingEnabled(false),
      needToDropLF(false),
      fragment(false),
      contextName(nullptr),
      contextNamespace(kNameSpaceID_None),
      contextNode(nullptr),
      templateModePtr(0),
      stackNodesIdx(0),
      numStackNodes(0),
      currentPtr(0),
      listPtr(0),
      formPointer(nullptr),
      headPointer(nullptr),
      charBufferLen(0),
      quirks(false),
      forceNoQuirks(false),
      allowDeclarativeShadowRoots(false),
      keepBuffer(false),
      mBuilder(false),
      mViewSource(false),
      mSpeculativeLoadStage(aStage),
      mBroken(NS_OK),
      mCurrentHtmlScriptCannotDocumentWriteOrBlock(false),
      mPreventScriptExecution(false),
      mGenerateSpeculativeLoads(true)
#ifdef DEBUG
      ,
      mActive(false)
#endif
{
  MOZ_ASSERT(aStage);
  MOZ_COUNT_CTOR(nsHtml5TreeBuilder);
}

nsHtml5TreeBuilder::~nsHtml5TreeBuilder() {
  MOZ_COUNT_DTOR(nsHtml5TreeBuilder);
}

nsIContentHandle* nsHtml5TreeBuilder::AllocateContentHandle() {
  auto handle = mozilla::MakeUnique<uint8_t>(0);
  nsIContentHandle* opaque = reinterpret_cast<nsIContentHandle*>(handle.get());
  mHandles.AppendElement(std::move(handle));
  return opaque;
}

nsIContentHandle* nsHtml5TreeBuilder::createElement(
    int32_t aNamespace, nsAtom* aName, nsHtml5HtmlAttributes* aAttributes,
    nsIContentHandle*, nsHtml5ContentCreatorFunction) {
  MOZ_ASSERT(aAttributes);
  MOZ_ASSERT(aName);

  // This is the network-relevant DOM-free subset of the upstream speculative
  // load wall. The generated tree builder still determines whether the token
  // is in a template/foreign-content context; only DOM allocation, script
  // execution, layout, image decoding, and style processing are replaced.
  if (mGenerateSpeculativeLoads && mode != IN_TEMPLATE &&
      aNamespace == kNameSpaceID_XHTML) {
    if (aName == nsGkAtoms::img) {
      nsHtml5String loading =
          aAttributes->getValue(nsHtml5AttributeName::ATTR_LOADING);
      if (!loading.LowerCaseEqualsASCII("lazy")) {
        nsHtml5String url =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_SRC);
        nsHtml5String srcset =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_SRCSET);
        nsHtml5String crossOrigin =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_CROSSORIGIN);
        nsHtml5String referrerPolicy =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_REFERRERPOLICY);
        nsHtml5String sizes =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_SIZES);
        nsHtml5String fetchPriority =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_FETCHPRIORITY);
        nsHtml5String type =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_TYPE);
        mSpeculativeLoadQueue.AppendElement()->InitImage(
            url, crossOrigin, nullptr, referrerPolicy, srcset, sizes, false,
            fetchPriority, type);
      }
    } else if (aName == nsGkAtoms::script) {
      nsHtml5String type =
          aAttributes->getValue(nsHtml5AttributeName::ATTR_TYPE);
      nsAutoString typeString;
      type.ToString(typeString);
      const bool isModule = typeString.LowerCaseEqualsASCII("module");
      const bool importmap = typeString.LowerCaseEqualsASCII("importmap");
      const bool nomodule =
          aAttributes->contains(nsHtml5AttributeName::ATTR_NOMODULE);
      nsHtml5String url = aAttributes->getValue(nsHtml5AttributeName::ATTR_SRC);
      if (url && !isModule && !importmap && !nomodule) {
        const bool async =
            aAttributes->contains(nsHtml5AttributeName::ATTR_ASYNC);
        const bool defer =
            aAttributes->contains(nsHtml5AttributeName::ATTR_DEFER);
        nsHtml5String charset =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_CHARSET);
        nsHtml5String crossOrigin =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_CROSSORIGIN);
        nsHtml5String nonce =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_NONCE);
        nsHtml5String fetchPriority =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_FETCHPRIORITY);
        nsHtml5String integrity =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_INTEGRITY);
        nsHtml5String referrerPolicy =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_REFERRERPOLICY);
        mSpeculativeLoadQueue.AppendElement()->InitScript(
            url, charset, type, crossOrigin, nullptr, nonce, fetchPriority,
            integrity, referrerPolicy, mode == nsHtml5TreeBuilder::IN_HEAD,
            async, defer, false);
      }
      mCurrentHtmlScriptCannotDocumentWriteOrBlock =
          isModule || importmap ||
          aAttributes->contains(nsHtml5AttributeName::ATTR_ASYNC) ||
          aAttributes->contains(nsHtml5AttributeName::ATTR_DEFER) || nomodule;
    } else if (aName == nsGkAtoms::base) {
      nsHtml5String url =
          aAttributes->getValue(nsHtml5AttributeName::ATTR_HREF);
      if (url) {
        mSpeculativeLoadQueue.AppendElement()->InitBase(url);
      }
    } else if (aName == nsGkAtoms::meta) {
      if (nsHtml5Portability::lowerCaseLiteralEqualsIgnoreAsciiCaseString(
              "content-security-policy",
              aAttributes->getValue(nsHtml5AttributeName::ATTR_HTTP_EQUIV))) {
        nsHtml5String csp =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_CONTENT);
        if (csp) {
          mSpeculativeLoadQueue.AppendElement()->InitMetaCSP(csp);
        }
      } else if (nsHtml5Portability::
                     lowerCaseLiteralEqualsIgnoreAsciiCaseString(
                         "referrer", aAttributes->getValue(
                                         nsHtml5AttributeName::ATTR_NAME))) {
        nsHtml5String referrerPolicy =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_CONTENT);
        if (referrerPolicy) {
          mSpeculativeLoadQueue.AppendElement()->InitMetaReferrerPolicy(
              referrerPolicy);
        }
      }
    } else if (aName == nsGkAtoms::link) {
      nsHtml5String rel = aAttributes->getValue(nsHtml5AttributeName::ATTR_REL);
      if (rel && rel.LowerCaseEqualsASCII("stylesheet")) {
        nsHtml5String url =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_HREF);
        if (url &&
            !aAttributes->getValue(nsHtml5AttributeName::ATTR_DISABLED)) {
          nsHtml5String charset =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_CHARSET);
          nsHtml5String crossOrigin =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_CROSSORIGIN);
          nsHtml5String media =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_MEDIA);
          nsHtml5String referrerPolicy =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_REFERRERPOLICY);
          nsHtml5String nonce =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_NONCE);
          nsHtml5String integrity =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_INTEGRITY);
          nsHtml5String fetchPriority =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_FETCHPRIORITY);
          mSpeculativeLoadQueue.AppendElement()->InitStyle(
              url, charset, crossOrigin, media, referrerPolicy, nonce,
              integrity, false, fetchPriority);
        }
      } else if (rel && rel.LowerCaseEqualsASCII("preload")) {
        nsHtml5String as = aAttributes->getValue(nsHtml5AttributeName::ATTR_AS);
        nsHtml5String url =
            aAttributes->getValue(nsHtml5AttributeName::ATTR_HREF);
        if (url && as.LowerCaseEqualsASCII("style")) {
          nsHtml5String charset =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_CHARSET);
          nsHtml5String crossOrigin =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_CROSSORIGIN);
          nsHtml5String media =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_MEDIA);
          nsHtml5String referrerPolicy =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_REFERRERPOLICY);
          nsHtml5String nonce =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_NONCE);
          nsHtml5String integrity =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_INTEGRITY);
          nsHtml5String fetchPriority =
              aAttributes->getValue(nsHtml5AttributeName::ATTR_FETCHPRIORITY);
          mSpeculativeLoadQueue.AppendElement()->InitStyle(
              url, charset, crossOrigin, media, referrerPolicy, nonce,
              integrity, true, fetchPriority);
        }
      }
    }
  }
  return AllocateContentHandle();
}

nsIContentHandle* nsHtml5TreeBuilder::createElement(
    int32_t aNamespace, nsAtom* aName, nsHtml5HtmlAttributes* aAttributes,
    nsIContentHandle*, nsIContentHandle* aIntendedParent,
    nsHtml5ContentCreatorFunction aCreator) {
  return createElement(aNamespace, aName, aAttributes, aIntendedParent,
                       aCreator);
}

nsIContentHandle* nsHtml5TreeBuilder::createHtmlElementSetAsRoot(
    nsHtml5HtmlAttributes* aAttributes) {
  return createElement(kNameSpaceID_XHTML, nsGkAtoms::html, aAttributes,
                       nullptr, nsHtml5ContentCreatorFunction{nullptr});
}

nsIContentHandle* nsHtml5TreeBuilder::createAndInsertFosterParentedElement(
    int32_t aNamespace, nsAtom* aName, nsHtml5HtmlAttributes* aAttributes,
    nsIContentHandle*, nsIContentHandle*, nsIContentHandle* aStackParent,
    nsHtml5ContentCreatorFunction aCreator) {
  return createElement(aNamespace, aName, aAttributes, aStackParent, aCreator);
}

void nsHtml5TreeBuilder::optionElementPopped(nsIContentHandle*) {}
void nsHtml5TreeBuilder::detachFromParent(nsIContentHandle*) {}
bool nsHtml5TreeBuilder::hasChildren(nsIContentHandle*) { return false; }
void nsHtml5TreeBuilder::appendElement(nsIContentHandle*, nsIContentHandle*) {}
void nsHtml5TreeBuilder::appendChildrenToNewParent(nsIContentHandle*,
                                                   nsIContentHandle*) {}
void nsHtml5TreeBuilder::insertFosterParentedCharacters(char16_t*, int32_t,
                                                        int32_t,
                                                        nsIContentHandle*,
                                                        nsIContentHandle*) {}
void nsHtml5TreeBuilder::insertFosterParentedChild(nsIContentHandle*,
                                                   nsIContentHandle*,
                                                   nsIContentHandle*) {}
void nsHtml5TreeBuilder::appendCharacters(nsIContentHandle*, char16_t*, int32_t,
                                          int32_t) {}
void nsHtml5TreeBuilder::appendComment(nsIContentHandle*, char16_t*, int32_t,
                                       int32_t) {}
void nsHtml5TreeBuilder::appendCommentToDocument(char16_t*, int32_t, int32_t) {}
void nsHtml5TreeBuilder::addAttributesToElement(nsIContentHandle*,
                                                nsHtml5HtmlAttributes*) {}
void nsHtml5TreeBuilder::markMalformedIfScript(nsIContentHandle*) {}
void nsHtml5TreeBuilder::appendDoctypeToDocument(nsAtom*, nsHtml5String,
                                                 nsHtml5String) {}
void nsHtml5TreeBuilder::elementPushed(int32_t, nsAtom*, nsIContentHandle*) {}
void nsHtml5TreeBuilder::elementPopped(int32_t, nsAtom*, nsIContentHandle*) {}

void nsHtml5TreeBuilder::start(bool) {
#ifdef DEBUG
  mActive = true;
#endif
}
void nsHtml5TreeBuilder::end() {
  FlushLoads();
#ifdef DEBUG
  mActive = false;
#endif
}

void nsHtml5TreeBuilder::accumulateCharacters(const char16_t* aBuf,
                                              int32_t aStart, int32_t aLength) {
  MOZ_RELEASE_ASSERT(EnsureBufferSpace(aLength));
  std::memcpy(charBuffer + charBufferLen, aBuf + aStart,
              sizeof(char16_t) * aLength);
  charBufferLen += aLength;
}

bool nsHtml5TreeBuilder::EnsureBufferSpace(int32_t aLength) {
  if (!charBuffer) {
    charBuffer = jArray<char16_t, int32_t>::newJArray(std::max(aLength, 1024));
    return !!charBuffer;
  }
  if (charBufferLen + aLength > charBuffer.length) {
    int32_t newLength =
        std::max(charBuffer.length << 1, charBufferLen + aLength);
    jArray<char16_t, int32_t> newBuffer =
        jArray<char16_t, int32_t>::newJArray(newLength);
    if (!newBuffer) {
      return false;
    }
    nsHtml5ArrayCopy::arraycopy(charBuffer, newBuffer, charBuffer.length);
    charBuffer = newBuffer;
  }
  return true;
}

bool nsHtml5TreeBuilder::HasScriptThatMayDocumentWriteOrBlock() {
  return false;
}

mozilla::Result<bool, nsresult> nsHtml5TreeBuilder::Flush(bool) {
  FlushLoads();
  return false;
}

void nsHtml5TreeBuilder::FlushLoads() {
  if (mSpeculativeLoadStage && !mSpeculativeLoadQueue.IsEmpty()) {
    mSpeculativeLoadStage->MoveSpeculativeLoadsFrom(mSpeculativeLoadQueue);
  }
}

void nsHtml5TreeBuilder::SetDocumentCharset(NotNull<const Encoding*> aEncoding,
                                            nsCharsetSource aCharsetSource,
                                            bool aCommitEncodingSpeculation) {
  mSpeculativeLoadQueue.AppendElement()->InitSetDocumentCharset(
      aEncoding, aCharsetSource, aCommitEncodingSpeculation);
}
void nsHtml5TreeBuilder::UpdateCharsetSource(nsCharsetSource) {}
void nsHtml5TreeBuilder::StreamEnded() { FlushLoads(); }
void nsHtml5TreeBuilder::NeedsCharsetSwitchTo(NotNull<const Encoding*>, int32_t,
                                              int32_t) {}
void nsHtml5TreeBuilder::MaybeComplainAboutCharset(const char*, bool, int32_t) {
}
void nsHtml5TreeBuilder::TryToEnableEncodingMenu() {}
void nsHtml5TreeBuilder::AddSnapshotToScript(nsAHtml5TreeBuilderState*,
                                             int32_t) {}
void nsHtml5TreeBuilder::DropHandles() { mHandles.Clear(); }
void nsHtml5TreeBuilder::MarkAsBroken(nsresult aRv) {
  if (NS_SUCCEEDED(mBroken)) {
    mBroken = aRv;
  }
}
void nsHtml5TreeBuilder::MarkAsBrokenFromPortability(nsresult aRv) {
  MarkAsBroken(aRv);
}
void nsHtml5TreeBuilder::StartPlainTextViewSource(const nsAutoString&) {}
void nsHtml5TreeBuilder::StartPlainText() {}
void nsHtml5TreeBuilder::StartPlainTextBody() {}
void nsHtml5TreeBuilder::documentMode(nsHtml5DocumentMode aMode) {
  mSpeculativeLoadQueue.AppendElement()->InitSetDocumentMode(aMode);
}
nsIContentHandle* nsHtml5TreeBuilder::getDocumentFragmentForTemplate(
    nsIContentHandle*) {
  return AllocateContentHandle();
}
void nsHtml5TreeBuilder::setDocumentFragmentForTemplate(nsIContentHandle*,
                                                        nsIContentHandle*) {}
nsIContentHandle* nsHtml5TreeBuilder::getShadowRootFromHost(
    nsIContentHandle*, nsIContentHandle*, nsHtml5String, bool, bool, bool, bool,
    nsHtml5String, nsHtml5String) {
  return AllocateContentHandle();
}
nsIContentHandle* nsHtml5TreeBuilder::getFormPointerForContext(
    nsIContentHandle*) {
  return nullptr;
}
void nsHtml5TreeBuilder::EnableViewSource(nsHtml5Highlighter*) {}

#define HTML5_LEAN_ERROR_0(aName) \
  void nsHtml5TreeBuilder::aName() {}
#define HTML5_LEAN_ERROR_1(aName, aType) \
  void nsHtml5TreeBuilder::aName(aType) {}
#define HTML5_LEAN_ERROR_2(aName, aType1, aType2) \
  void nsHtml5TreeBuilder::aName(aType1, aType2) {}
HTML5_LEAN_ERROR_0(errDeepTree)
HTML5_LEAN_ERROR_1(errStrayStartTag, nsAtom*)
HTML5_LEAN_ERROR_1(errStrayEndTag, nsAtom*)
HTML5_LEAN_ERROR_2(errUnclosedElements, int32_t, nsAtom*)
HTML5_LEAN_ERROR_2(errUnclosedElementsImplied, int32_t, nsAtom*)
HTML5_LEAN_ERROR_1(errUnclosedElementsCell, int32_t)
HTML5_LEAN_ERROR_0(errStrayDoctype)
HTML5_LEAN_ERROR_0(errAlmostStandardsDoctype)
HTML5_LEAN_ERROR_0(errQuirkyDoctype)
HTML5_LEAN_ERROR_0(errNonSpaceInTrailer)
HTML5_LEAN_ERROR_0(errNonSpaceAfterFrameset)
HTML5_LEAN_ERROR_0(errNonSpaceInFrameset)
HTML5_LEAN_ERROR_0(errNonSpaceAfterBody)
HTML5_LEAN_ERROR_0(errNonSpaceInColgroupInFragment)
HTML5_LEAN_ERROR_0(errNonSpaceInNoscriptInHead)
HTML5_LEAN_ERROR_1(errFooBetweenHeadAndBody, nsAtom*)
HTML5_LEAN_ERROR_0(errStartTagWithoutDoctype)
HTML5_LEAN_ERROR_0(errNoSelectInTableScope)
HTML5_LEAN_ERROR_0(errStartSelectWhereEndSelectExpected)
HTML5_LEAN_ERROR_1(errStartTagWithSelectOpen, nsAtom*)
HTML5_LEAN_ERROR_1(errBadStartTagInNoscriptInHead, nsAtom*)
HTML5_LEAN_ERROR_0(errImage)
HTML5_LEAN_ERROR_0(errIsindex)
HTML5_LEAN_ERROR_1(errFooSeenWhenFooOpen, nsAtom*)
HTML5_LEAN_ERROR_0(errHeadingWhenHeadingOpen)
HTML5_LEAN_ERROR_0(errFramesetStart)
HTML5_LEAN_ERROR_0(errNoCellToClose)
HTML5_LEAN_ERROR_1(errStartTagInTable, nsAtom*)
HTML5_LEAN_ERROR_0(errFormWhenFormOpen)
HTML5_LEAN_ERROR_0(errTableSeenWhileTableOpen)
HTML5_LEAN_ERROR_1(errStartTagInTableBody, nsAtom*)
HTML5_LEAN_ERROR_0(errEndTagSeenWithoutDoctype)
HTML5_LEAN_ERROR_0(errEndTagAfterBody)
HTML5_LEAN_ERROR_1(errEndTagSeenWithSelectOpen, nsAtom*)
HTML5_LEAN_ERROR_0(errGarbageInColgroup)
HTML5_LEAN_ERROR_0(errEndTagBr)
HTML5_LEAN_ERROR_1(errNoElementToCloseButEndTagSeen, nsAtom*)
HTML5_LEAN_ERROR_1(errHtmlStartTagInForeignContext, nsAtom*)
HTML5_LEAN_ERROR_0(errNoTableRowToClose)
HTML5_LEAN_ERROR_0(errNonSpaceInTable)
HTML5_LEAN_ERROR_0(errUnclosedChildrenInRuby)
HTML5_LEAN_ERROR_1(errStartTagSeenWithoutRuby, nsAtom*)
HTML5_LEAN_ERROR_0(errSelfClosing)
HTML5_LEAN_ERROR_0(errNoCheckUnclosedElementsOnStack)
HTML5_LEAN_ERROR_2(errEndTagDidNotMatchCurrentOpenElement, nsAtom*, nsAtom*)
HTML5_LEAN_ERROR_1(errEndTagViolatesNestingRules, nsAtom*)
HTML5_LEAN_ERROR_1(errEndWithUnclosedElements, nsAtom*)
HTML5_LEAN_ERROR_1(errListUnclosedStartTags, int32_t)
#undef HTML5_LEAN_ERROR_0
#undef HTML5_LEAN_ERROR_1
#undef HTML5_LEAN_ERROR_2
