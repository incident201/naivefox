/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "CliSignalStop.h"

#if defined(XP_LINUX) && !defined(ANDROID)

#  include <fcntl.h>
#  include <pthread.h>
#  include <unistd.h>

#  include <cerrno>
#  include <csignal>

#  include "nsIEventTarget.h"
#  include "SocksServer.h"
#  include "mozilla/Atomics.h"
#  include "mozilla/RefPtr.h"
#  include "nsError.h"

namespace mozilla::naivefox {

namespace {
volatile sig_atomic_t sStopWriteFd = -1;

void HandleStopSignal(int) {
  const int saved = errno;
  const int descriptor = sStopWriteFd;
  if (descriptor >= 0) {
    const unsigned char request = 1;
    ssize_t written;
    do {
      written = write(descriptor, &request, sizeof(request));
    } while (written < 0 && errno == EINTR);
  }
  errno = saved;
}
}  // namespace

class CliSignalStop::Impl final {
 public:
  explicit Impl(LocalProxyServerControl* aControl) : mControl(aControl) {}

  ~Impl() {
    if (mOwnsHandler) {
      sStopWriteFd = -1;
    }
    if (mInterruptInstalled) {
      (void)sigaction(SIGINT, &mPreviousInterrupt, nullptr);
    }
    if (mTerminateInstalled) {
      (void)sigaction(SIGTERM, &mPreviousTerminate, nullptr);
    }
    if (mReaderStarted) {
      const unsigned char stop = 0;
      ssize_t written;
      do {
        written = write(mPipe[1], &stop, sizeof(stop));
      } while (written < 0 && errno == EINTR);
      (void)pthread_join(mReader, nullptr);
    }
    for (int descriptor : mPipe) {
      if (descriptor >= 0) {
        (void)close(descriptor);
      }
    }
  }

  nsresult Start() {
    if (!mControl || sStopWriteFd >= 0) {
      return NS_ERROR_ALREADY_INITIALIZED;
    }
    if (pipe2(mPipe, O_CLOEXEC) != 0) {
      return NS_ERROR_FAILURE;
    }
    int flags = fcntl(mPipe[1], F_GETFL, 0);
    if (flags < 0 || fcntl(mPipe[1], F_SETFL, flags | O_NONBLOCK) != 0) {
      return NS_ERROR_FAILURE;
    }
    if (pthread_create(&mReader, nullptr, ReadSignal, this) != 0) {
      return NS_ERROR_FAILURE;
    }
    mReaderStarted = true;
    sStopWriteFd = mPipe[1];
    mOwnsHandler = true;
    struct sigaction action{};
    action.sa_handler = HandleStopSignal;
    action.sa_flags = SA_RESTART;
    sigemptyset(&action.sa_mask);
    sigaddset(&action.sa_mask, SIGINT);
    sigaddset(&action.sa_mask, SIGTERM);
    if (sigaction(SIGINT, &action, &mPreviousInterrupt) != 0) {
      return NS_ERROR_FAILURE;
    }
    mInterruptInstalled = true;
    if (sigaction(SIGTERM, &action, &mPreviousTerminate) != 0) {
      return NS_ERROR_FAILURE;
    }
    mTerminateInstalled = true;
    return NS_OK;
  }

  bool Failed() const { return mFailed; }

 private:
  static void* ReadSignal(void* aContext) {
    auto* self = static_cast<Impl*>(aContext);
    unsigned char request = 0;
    ssize_t count;
    do {
      count = read(self->mPipe[0], &request, sizeof(request));
    } while (count < 0 && errno == EINTR);
    if (count != 1) {
      self->mFailed = true;
      self->mControl->RequestStop();
    } else if (request) {
      self->mControl->RequestStop();
    }
    return nullptr;
  }

  RefPtr<LocalProxyServerControl> mControl;
  Atomic<bool, Relaxed> mFailed{false};
  int mPipe[2] = {-1, -1};
  pthread_t mReader{};
  struct sigaction mPreviousInterrupt{};
  struct sigaction mPreviousTerminate{};
  bool mReaderStarted = false;
  bool mOwnsHandler = false;
  bool mInterruptInstalled = false;
  bool mTerminateInstalled = false;
};

CliSignalStop::CliSignalStop() = default;
CliSignalStop::~CliSignalStop() = default;

nsresult CliSignalStop::Start(LocalProxyServerControl* aControl) {
  if (mImpl) {
    return NS_ERROR_ALREADY_INITIALIZED;
  }
  auto state = MakeUnique<Impl>(aControl);
  nsresult rv = state->Start();
  if (NS_FAILED(rv)) {
    return rv;
  }
  mImpl = std::move(state);
  return NS_OK;
}

bool CliSignalStop::Failed() const { return mImpl && mImpl->Failed(); }

}  // namespace mozilla::naivefox
#endif
