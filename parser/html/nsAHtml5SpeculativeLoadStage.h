/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsAHtml5SpeculativeLoadStage_h
#define nsAHtml5SpeculativeLoadStage_h

#include "nsTArrayForwardDeclare.h"

class nsHtml5SpeculativeLoad;

/**
 * The parser-thread side of speculative-load staging.
 *
 * Keeping this interface separate from nsHtml5TreeOpStage lets consumers
 * run the upstream tokenizer/tree-builder discovery path without linking the
 * DOM tree-operation backend.
 */
class nsAHtml5SpeculativeLoadStage {
 public:
  virtual void MoveSpeculativeLoadsFrom(
      nsTArray<nsHtml5SpeculativeLoad>& aSpeculativeLoadQueue) = 0;

 protected:
  virtual ~nsAHtml5SpeculativeLoadStage() = default;
};

#endif  // nsAHtml5SpeculativeLoadStage_h
