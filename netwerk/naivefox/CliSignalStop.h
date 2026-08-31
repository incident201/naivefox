/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_CliSignalStop_h
#define netwerk_naivefox_CliSignalStop_h

#if defined(XP_LINUX) && !defined(ANDROID)
#  include "mozilla/UniquePtr.h"
#  include "nscore.h"

namespace mozilla::naivefox {

class LocalProxyServerControl;

// Keep this scope outside GeckoRuntime so its signal pipe outlives all workers.
class CliSignalStop final {
 public:
  CliSignalStop();
  ~CliSignalStop();

  nsresult Start(LocalProxyServerControl* aControl);
  bool Failed() const;

 private:
  class Impl;
  UniquePtr<Impl> mImpl;
};

}  // namespace mozilla::naivefox
#endif
#endif
