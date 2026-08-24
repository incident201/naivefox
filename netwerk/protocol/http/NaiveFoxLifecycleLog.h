/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef NaiveFoxLifecycleLog_h_
#define NaiveFoxLifecycleLog_h_

#ifdef MOZ_NAIVEFOX

#  include "mozilla/Logging.h"

namespace mozilla::net {

extern LazyLogModule gNaiveFoxLifecycleLog;

}  // namespace mozilla::net

#  define NAIVEFOX_LIFECYCLE_LOG(args)                                \
    MOZ_LOG(mozilla::net::gNaiveFoxLifecycleLog,                       \
            mozilla::LogLevel::Debug, args)
#  define NAIVEFOX_LIFECYCLE_LOG_ENABLED() \
    MOZ_LOG_TEST(mozilla::net::gNaiveFoxLifecycleLog, \
                 mozilla::LogLevel::Debug)

#else

#  define NAIVEFOX_LIFECYCLE_LOG(args) ((void)0)
#  define NAIVEFOX_LIFECYCLE_LOG_ENABLED() false

#endif

#endif  // NaiveFoxLifecycleLog_h_
