/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NaiveFoxComponents.h"

#include "nsScriptSecurityManager.h"

nsresult Construct_nsIScriptSecurityManagerNaiveFox(const nsIID& aIID,
                                                    void** aResult) {
  if (!aResult) {
    return NS_ERROR_NULL_POINTER;
  }
  *aResult = nullptr;

  nsScriptSecurityManager::InitStatics();
  nsScriptSecurityManager* manager =
      nsScriptSecurityManager::GetScriptSecurityManager();
  return manager ? manager->QueryInterface(aIID, aResult)
                 : NS_ERROR_OUT_OF_MEMORY;
}
