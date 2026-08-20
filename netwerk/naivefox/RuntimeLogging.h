/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_RuntimeLogging_h
#define netwerk_naivefox_RuntimeLogging_h

#include "Config.h"
#include "nsStringFwd.h"
#include "nscore.h"

namespace mozilla::naivefox {

nsresult ConfigureRuntimeLogging(RuntimeLogMode aMode, const nsACString& aPath,
                                 nsACString& aError);
void ShutdownRuntimeLogging();
void RuntimeLog(const char* aFormat, ...) MOZ_FORMAT_PRINTF(1, 2);

// Emits a Chromium/NaiveProxy-style timestamped INFO record.  The legacy
// RuntimeLog form remains line-stable for machine-readable integration gates.
void RuntimeLogEvent(const char* aFormat, ...) MOZ_FORMAT_PRINTF(1, 2);

}  // namespace mozilla::naivefox

#endif
