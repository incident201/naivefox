/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "RuntimeLogging.h"

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <ctime>

#ifdef XP_WIN
#  include <io.h>
#else
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

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

void WriteLogPrefix(FILE* aOutput) {
  const auto now = std::chrono::system_clock::now();
  const auto seconds = std::chrono::system_clock::to_time_t(now);
  const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
                          now.time_since_epoch()) %
                      std::chrono::seconds(1);
  std::tm localTime{};
#ifdef XP_WIN
  (void)localtime_s(&localTime, &seconds);
#else
  (void)localtime_r(&seconds, &localTime);
#endif
  std::fprintf(aOutput, "[%02d%02d/%02d%02d%02d.%06lld:INFO:naivefox] ",
               localTime.tm_mon + 1, localTime.tm_mday, localTime.tm_hour,
               localTime.tm_min, localTime.tm_sec,
               static_cast<long long>(micros.count()));
}

void WriteLog(FILE* aOutput, bool aTimestamped, const char* aFormat,
              va_list aArguments) {
  if (aTimestamped) {
    WriteLogPrefix(aOutput);
  }
  std::vfprintf(aOutput, aFormat, aArguments);
  std::fflush(aOutput);
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
#ifdef XP_WIN
    const nsString path = NS_ConvertUTF8toUTF16(aPath);
    gRuntimeLogFile = _wfopen(path.get(), L"a");
#else
    const nsCString path = PromiseFlatCString(aPath);
    int flags = O_WRONLY | O_CREAT | O_APPEND;
#  ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#  endif
    const int fd = open(path.get(), flags, 0600);
    if (fd >= 0) {
      // O_CREAT applies the mode only to a newly-created file.  An existing
      // log may have been created by another process with broader bits, so
      // enforce the private logging contract on every successful open.
      if (fchmod(fd, 0600) != 0) {
        close(fd);
      } else {
        gRuntimeLogFile = fdopen(fd, "a");
        if (!gRuntimeLogFile) {
          close(fd);
        }
      }
    }
#endif
    if (!gRuntimeLogFile) {
      aError.AssignLiteral("cannot open runtime log file");
      return NS_ERROR_FILE_ACCESS_DENIED;
    }
#ifdef XP_WIN
    // UCRT's setvbuf mode/size validation differs from POSIX and can invoke
    // its invalid-parameter handler during process startup.  Windows writes
    // are flushed explicitly by WriteLog, so leave the CRT default buffer.
#else
    (void)std::setvbuf(gRuntimeLogFile, nullptr, _IOLBF, 0);
#endif
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
  WriteLog(output, false, aFormat, arguments);
  va_end(arguments);
}

void RuntimeLogEvent(const char* aFormat, ...) {
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
  WriteLog(output, true, aFormat, arguments);
  va_end(arguments);
}

}  // namespace mozilla::naivefox
