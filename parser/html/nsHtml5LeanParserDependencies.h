/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsHtml5LeanParserDependencies_h
#define nsHtml5LeanParserDependencies_h

#include "mozilla/Result.h"
#include "nsError.h"

class nsHtml5Highlighter {
 public:
  template <typename... Args>
  void MaybeLinkifyAttributeValue(Args&&...) {}
  template <typename... Args>
  void SetBuffer(Args&&...) {}
  template <typename... Args>
  void DropBuffer(Args&&...) {}
  template <typename... Args>
  void AddErrorToCurrentAmpersand(Args&&...) {}
  template <typename... Args>
  void AddErrorToCurrentNode(Args&&...) {}
  template <typename... Args>
  void AddErrorToCurrentRun(Args&&...) {}
  template <typename... Args>
  void AddErrorToCurrentSlash(Args&&...) {}
  template <typename... Args>
  void SetOpSink(Args&&...) {}
  template <typename... Args>
  void Start(Args&&...) {}
  void StartBodyContents() {}
  void Rewind() {}
  int32_t Transition(int32_t aState, bool, int32_t) { return aState; }
  void CompletedNamedCharacterReference() {}
  bool ShouldFlushOps() { return false; }
  mozilla::Result<bool, nsresult> FlushOps() { return false; }
  bool End() { return true; }
};

class nsHtml5StreamParser {
 public:
  template <typename... Args>
  bool internalEncodingDeclaration(Args&&...) {
    return false;
  }
  bool TemplatePushedOrHeadPopped() { return false; }
  template <typename... Args>
  void RememberGt(Args&&...) {}
};

#endif  // nsHtml5LeanParserDependencies_h
