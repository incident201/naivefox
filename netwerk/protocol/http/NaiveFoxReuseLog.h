/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef NaiveFoxReuseLog_h_
#define NaiveFoxReuseLog_h_

#ifdef MOZ_NAIVEFOX

#  include "mozilla/Logging.h"

namespace mozilla::net {

class nsHttpConnectionInfo;

extern LazyLogModule gNaiveFoxReuseLog;

// Return an opaque process-local identity.  Never expose HashKey(): it may
// contain proxy authentication and network-isolation material.
const void* NaiveFoxConnectionInfoId(const nsHttpConnectionInfo* aConnInfo);

}  // namespace mozilla::net

#  define NAIVEFOX_REUSE_LOG(args)                                      \
    MOZ_LOG(mozilla::net::gNaiveFoxReuseLog, mozilla::LogLevel::Debug, args)
#  define NAIVEFOX_REUSE_LOG_ENABLED() \
    MOZ_LOG_TEST(mozilla::net::gNaiveFoxReuseLog, mozilla::LogLevel::Debug)

#else

#  define NAIVEFOX_REUSE_LOG(args) ((void)0)
#  define NAIVEFOX_REUSE_LOG_ENABLED() false

#endif

#endif  // NaiveFoxReuseLog_h_
