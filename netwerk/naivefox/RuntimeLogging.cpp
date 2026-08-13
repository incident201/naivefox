/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "RuntimeLogging.h"

#include <sys/stat.h>

#include <cstdarg>
#include <cstdio>

#include "mozilla/StaticMutex.h"
#include "nsError.h"
#include "nsString.h"

namespace mozilla::naivefox {

namespace {

StaticMutex gRuntimeLogMutex;
RuntimeLogMode gRuntimeLogMode = RuntimeLogMode::Disabled;
FILE* gRuntimeLogFile = nullptr;

void CloseLogFile() {
  if (gRuntimeLogFile) {
    std::fclose(gRuntimeLogFile);
    gRuntimeLogFile = nullptr;
  }
}

}  // namespace

nsresult ConfigureRuntimeLogging(RuntimeLogMode aMode, const nsACString& aPath,
                                 nsACString& aError) {
  StaticMutexAutoLock lock(gRuntimeLogMutex);
  CloseLogFile();
  gRuntimeLogMode = RuntimeLogMode::Disabled;
  if (aMode == RuntimeLogMode::File) {
    if (aPath.IsEmpty()) {
      aError.AssignLiteral("log file path must not be empty");
      return NS_ERROR_INVALID_ARG;
    }
    const nsCString path = PromiseFlatCString(aPath);
    gRuntimeLogFile = std::fopen(path.get(), "a");
    if (!gRuntimeLogFile) {
      aError.AssignLiteral("cannot open runtime log file");
      return NS_ERROR_FILE_ACCESS_DENIED;
    }
    if (chmod(path.get(), 0600) != 0) {
      CloseLogFile();
      aError.AssignLiteral("cannot secure runtime log file");
      return NS_ERROR_FILE_ACCESS_DENIED;
    }
    (void)std::setvbuf(gRuntimeLogFile, nullptr, _IOLBF, 0);
  }
  gRuntimeLogMode = aMode;
  return NS_OK;
}

void ShutdownRuntimeLogging() {
  StaticMutexAutoLock lock(gRuntimeLogMutex);
  CloseLogFile();
  gRuntimeLogMode = RuntimeLogMode::Disabled;
}

void RuntimeLog(const char* aFormat, ...) {
  StaticMutexAutoLock lock(gRuntimeLogMutex);
  if (gRuntimeLogMode == RuntimeLogMode::Disabled) {
    return;
  }
  FILE* output =
      gRuntimeLogMode == RuntimeLogMode::Console ? stdout : gRuntimeLogFile;
  if (!output) {
    return;
  }
  va_list arguments;
  va_start(arguments, aFormat);
  std::vfprintf(output, aFormat, arguments);
  va_end(arguments);
  std::fflush(output);
}

}  // namespace mozilla::naivefox
