/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsHtml5SpeculativeScanner_h
#define nsHtml5SpeculativeScanner_h

#include "mozilla/UniquePtr.h"
#include "nsError.h"
#include "nsHtml5AtomTable.h"
#include "nsHtml5SpeculativeLoadStage.h"
#include "nsHtml5StylePreloadDescriptor.h"
#include "nsStringFwd.h"
#include "nsTArray.h"

class nsHtml5Tokenizer;
class nsHtml5TreeBuilder;
class nsISerialEventTarget;

/**
 * DOM-free facade over Gecko's generated HTML5 tokenizer/tree builder.
 * Feed() preserves chunk boundaries and stages descriptors after each chunk,
 * matching the parser-thread speculative-load flush point.
 */
class nsHtml5SpeculativeScanner final {
 public:
  explicit nsHtml5SpeculativeScanner(
      nsISerialEventTarget* aParserEventTarget = nullptr);
  ~nsHtml5SpeculativeScanner();

  nsresult Feed(const nsAString& aChunk);
  nsresult Finish();

  void TakeStyleDescriptors(
      nsTArray<nsHtml5StylePreloadDescriptor>& aDescriptors);

 private:
  nsHtml5AtomTable mAtomTable;
  nsHtml5SpeculativeLoadStage mStage;
  mozilla::UniquePtr<nsHtml5TreeBuilder> mTreeBuilder;
  mozilla::UniquePtr<nsHtml5Tokenizer> mTokenizer;
  bool mLastWasCR = false;
  bool mFinished = false;
};

#endif  // nsHtml5SpeculativeScanner_h
