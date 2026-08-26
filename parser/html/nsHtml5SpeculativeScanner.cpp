/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsHtml5SpeculativeScanner.h"

#include <mutex>

#include "nsHtml5AttributeName.h"
#include "nsHtml5DependentUTF16Buffer.h"
#include "nsHtml5ElementName.h"
#include "nsHtml5HtmlAttributes.h"
#include "nsHtml5NamedCharacters.h"
#include "nsHtml5Portability.h"
#include "nsHtml5StackNode.h"
#include "nsHtml5Tokenizer.h"
#include "nsHtml5TreeBuilder.h"
#include "nsHtml5UTF16Buffer.h"

namespace {
void EnsureHtml5Statics() {
  static std::once_flag sOnce;
  std::call_once(sOnce, [] {
    nsHtml5AttributeName::initializeStatics();
    nsHtml5ElementName::initializeStatics();
    nsHtml5HtmlAttributes::initializeStatics();
    nsHtml5NamedCharacters::initializeStatics();
    nsHtml5Portability::initializeStatics();
    nsHtml5StackNode::initializeStatics();
    nsHtml5Tokenizer::initializeStatics();
    nsHtml5TreeBuilder::initializeStatics();
    nsHtml5UTF16Buffer::initializeStatics();
  });
}
}  // namespace

nsHtml5SpeculativeScanner::nsHtml5SpeculativeScanner(
    nsISerialEventTarget* aParserEventTarget) {
  EnsureHtml5Statics();
#ifdef DEBUG
  // nsHtml5StreamParser is constructed on main, then explicitly permits atom
  // table lookups on its parser target. Preserve that thread contract for the
  // document-handoff arm; target-local callers can retain the default target.
  if (aParserEventTarget) {
    mAtomTable.SetPermittedLookupEventTarget(aParserEventTarget);
  }
#else
  (void)aParserEventTarget;
#endif
  mTreeBuilder = mozilla::MakeUnique<nsHtml5TreeBuilder>(&mStage);
  mTokenizer = mozilla::MakeUnique<nsHtml5Tokenizer>(mTreeBuilder.get(), false);
  mTokenizer->setInterner(&mAtomTable);
  mTreeBuilder->setScriptingEnabled(true);
  mTokenizer->start();
}

nsHtml5SpeculativeScanner::~nsHtml5SpeculativeScanner() {
  if (!mFinished) {
    mTokenizer->end();
  }
}

nsresult nsHtml5SpeculativeScanner::Feed(const nsAString& aChunk) {
  if (mFinished) {
    return NS_ERROR_UNEXPECTED;
  }
  if (aChunk.Length() > INT32_MAX) {
    return NS_ERROR_OUT_OF_MEMORY;
  }

  nsHtml5DependentUTF16Buffer buffer(aChunk);
  while (buffer.hasMore()) {
    buffer.adjust(mLastWasCR);
    mLastWasCR = false;
    if (!buffer.hasMore()) {
      break;
    }
    if (!mTokenizer->EnsureBufferSpace(buffer.getLength())) {
      return NS_ERROR_OUT_OF_MEMORY;
    }
    mLastWasCR = mTokenizer->tokenizeBuffer(&buffer);
    if (NS_FAILED(mTreeBuilder->IsBroken())) {
      return mTreeBuilder->IsBroken();
    }
  }
  mTreeBuilder->FlushLoads();
  return NS_OK;
}

nsresult nsHtml5SpeculativeScanner::Finish() {
  if (mFinished) {
    return NS_ERROR_UNEXPECTED;
  }
  mTokenizer->eof();
  nsresult rv = mTreeBuilder->IsBroken();
  mTokenizer->end();
  mTreeBuilder->FlushLoads();
  mFinished = true;
  mAtomTable.Clear();
  return rv;
}

void nsHtml5SpeculativeScanner::TakeStyleDescriptors(
    nsTArray<nsHtml5StylePreloadDescriptor>& aDescriptors) {
  nsTArray<nsHtml5SpeculativeLoad> loads;
  mStage.MoveSpeculativeLoadsTo(loads);
  for (auto& load : loads) {
    auto descriptor = std::move(load).TakeStyleDescriptor();
    if (descriptor) {
      aDescriptors.AppendElement(std::move(descriptor.ref()));
    }
  }
}
