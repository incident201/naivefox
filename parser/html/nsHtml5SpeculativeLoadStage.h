/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsHtml5SpeculativeLoadStage_h
#define nsHtml5SpeculativeLoadStage_h

#include "mozilla/Mutex.h"
#include "nsAHtml5SpeculativeLoadStage.h"
#include "nsHtml5SpeculativeLoad.h"
#include "nsTArray.h"

class nsHtml5SpeculativeLoadStage final
    : public nsAHtml5SpeculativeLoadStage {
 public:
  nsHtml5SpeculativeLoadStage() = default;

  void MoveSpeculativeLoadsFrom(
      nsTArray<nsHtml5SpeculativeLoad>& aSpeculativeLoadQueue) override;

  void MoveSpeculativeLoadsTo(
      nsTArray<nsHtml5SpeculativeLoad>& aSpeculativeLoadQueue);

#ifdef DEBUG
  void AssertEmpty();
#endif

 private:
  nsTArray<nsHtml5SpeculativeLoad> mSpeculativeLoadQueue;
  mozilla::Mutex mMutex MOZ_UNANNOTATED{
      "nsHtml5SpeculativeLoadStage mutex"};
};

#endif  // nsHtml5SpeculativeLoadStage_h
