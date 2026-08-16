/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsIMemoryReporter.h"

#include "nsCOMPtr.h"

namespace mozilla {

nsresult RegisterStrongMemoryReporter(
    already_AddRefed<nsIMemoryReporter> aReporter) {
  nsCOMPtr<nsIMemoryReporter> reporter = aReporter;
  return NS_OK;
}

nsresult RegisterStrongAsyncMemoryReporter(
    already_AddRefed<nsIMemoryReporter> aReporter) {
  nsCOMPtr<nsIMemoryReporter> reporter = aReporter;
  return NS_OK;
}

nsresult RegisterWeakMemoryReporter(nsIMemoryReporter*) { return NS_OK; }
nsresult RegisterWeakAsyncMemoryReporter(nsIMemoryReporter*) { return NS_OK; }
nsresult UnregisterStrongMemoryReporter(nsIMemoryReporter*) { return NS_OK; }
nsresult UnregisterWeakMemoryReporter(nsIMemoryReporter*) { return NS_OK; }

#define DEFINE_AMOUNT_STUB(name)                                     \
  nsresult Register##name##DistinguishedAmount(InfallibleAmountFn) { \
    return NS_OK;                                                     \
  }

DEFINE_AMOUNT_STUB(JSMainRuntimeGCHeap)
DEFINE_AMOUNT_STUB(JSMainRuntimeTemporaryPeak)
DEFINE_AMOUNT_STUB(JSMainRuntimeCompartmentsSystem)
DEFINE_AMOUNT_STUB(JSMainRuntimeCompartmentsUser)
DEFINE_AMOUNT_STUB(JSMainRuntimeRealmsSystem)
DEFINE_AMOUNT_STUB(JSMainRuntimeRealmsUser)
DEFINE_AMOUNT_STUB(ImagesContentUsedUncompressed)
DEFINE_AMOUNT_STUB(StorageSQLite)
DEFINE_AMOUNT_STUB(LowMemoryEventsPhysical)
DEFINE_AMOUNT_STUB(GhostWindows)

#undef DEFINE_AMOUNT_STUB

nsresult UnregisterImagesContentUsedUncompressedDistinguishedAmount() {
  return NS_OK;
}

nsresult UnregisterStorageSQLiteDistinguishedAmount() { return NS_OK; }

nsresult RegisterJSSizeOfTab(JSSizeOfTabFn) { return NS_OK; }
nsresult RegisterNonJSSizeOfTab(NonJSSizeOfTabFn) { return NS_OK; }

}  // namespace mozilla
