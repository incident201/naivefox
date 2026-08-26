/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsHtml5SpeculativeLoadStage.h"

void nsHtml5SpeculativeLoadStage::MoveSpeculativeLoadsFrom(
    nsTArray<nsHtml5SpeculativeLoad>& aSpeculativeLoadQueue) {
  mozilla::MutexAutoLock lock(mMutex);
  mSpeculativeLoadQueue.AppendElements(std::move(aSpeculativeLoadQueue));
}

void nsHtml5SpeculativeLoadStage::MoveSpeculativeLoadsTo(
    nsTArray<nsHtml5SpeculativeLoad>& aSpeculativeLoadQueue) {
  mozilla::MutexAutoLock lock(mMutex);
  aSpeculativeLoadQueue.AppendElements(std::move(mSpeculativeLoadQueue));
}

#ifdef DEBUG
void nsHtml5SpeculativeLoadStage::AssertEmpty() {
  mozilla::MutexAutoLock lock(mMutex);
  MOZ_ASSERT(mSpeculativeLoadQueue.IsEmpty());
}
#endif
