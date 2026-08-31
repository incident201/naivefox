/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <arpa/inet.h>
#include <dlfcn.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#include "NaiveFoxAPI.h"

namespace {

constexpr size_t kMaximumConfigSize = 1024 * 1024;

using RunEmbedded = decltype(&NaiveFoxRunEmbedded);
using RequestStop = decltype(&NaiveFoxRequestStop);
using Version = decltype(&NaiveFoxVersion);

bool ReadConfig(const char* aPath, std::string& aConfig) {
  std::ifstream stream(aPath, std::ios::binary);
  if (!stream) {
    return false;
  }
  stream.seekg(0, std::ios::end);
  std::streamoff size = stream.tellg();
  if (size <= 0 || size > static_cast<std::streamoff>(kMaximumConfigSize)) {
    return false;
  }
  aConfig.resize(static_cast<size_t>(size));
  stream.seekg(0, std::ios::beg);
  stream.read(aConfig.data(), size);
  return stream.good();
}

bool WriteAtomically(const char* aPath, const std::string& aContents) {
  std::string temporary = std::string(aPath) + ".tmp." +
                          std::to_string(static_cast<long long>(getpid()));
  FILE* stream = fopen(temporary.c_str(), "wb");
  if (!stream) {
    return false;
  }
  bool ok =
      fwrite(aContents.data(), 1, aContents.size(), stream) == aContents.size();
  ok = fclose(stream) == 0 && ok;
  if (ok) {
    ok = rename(temporary.c_str(), aPath) == 0;
  }
  if (!ok) {
    unlink(temporary.c_str());
  }
  return ok;
}

int UdpProbe(const char* aHost, const char* aPort) {
  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_DGRAM;
  addrinfo* addresses = nullptr;
  int status = getaddrinfo(aHost, aPort, &hints, &addresses);
  if (status != 0) {
    fprintf(stderr, "UDP probe address resolution failed: %s\n",
            gai_strerror(status));
    return 1;
  }

  constexpr char kMessage[] = "naivefox-android-udp-probe";
  int result = 1;
  for (addrinfo* address = addresses; address; address = address->ai_next) {
    int socketFd =
        socket(address->ai_family, address->ai_socktype, address->ai_protocol);
    if (socketFd < 0) {
      continue;
    }
    timeval timeout{3, 0};
    setsockopt(socketFd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    if (sendto(socketFd, kMessage, sizeof(kMessage) - 1, 0, address->ai_addr,
               address->ai_addrlen) ==
        static_cast<ssize_t>(sizeof(kMessage) - 1)) {
      char response[sizeof(kMessage)]{};
      ssize_t count = recv(socketFd, response, sizeof(response), 0);
      if (count == static_cast<ssize_t>(sizeof(kMessage) - 1) &&
          memcmp(response, kMessage, sizeof(kMessage) - 1) == 0) {
        result = 0;
      }
    }
    close(socketFd);
    if (result == 0) {
      break;
    }
  }
  freeaddrinfo(addresses);
  return result;
}

template <typename T>
T Resolve(void* aLibrary, const char* aName) {
  dlerror();
  void* symbol = dlsym(aLibrary, aName);
  if (const char* error = dlerror()) {
    fprintf(stderr, "cannot resolve %s: %s\n", aName, error);
    return nullptr;
  }
  return reinterpret_cast<T>(symbol);
}

int Run(int aArgc, char* aArgv[]) {
  if (aArgc < 8) {
    fprintf(stderr,
            "usage: %s LIBXUL CONFIG PROFILE RUNTIME STOP READY RESULT "
            "[--transport VALUE] [--reject-first VALUE]...\n",
            aArgv[0]);
    return 2;
  }
  const char* transport = nullptr;
  std::vector<const char*> rejectedTransports;
  for (int index = 8; index < aArgc; index += 2) {
    if (index + 1 >= aArgc) {
      return 2;
    }
    if (strcmp(aArgv[index], "--transport") == 0 && !transport) {
      transport = aArgv[index + 1];
    } else if (strcmp(aArgv[index], "--reject-first") == 0) {
      rejectedTransports.push_back(aArgv[index + 1]);
    } else {
      return 2;
    }
  }

  std::string config;
  if (!ReadConfig(aArgv[2], config) || config.empty() ||
      config.size() > kMaximumConfigSize) {
    fprintf(stderr, "cannot read a non-empty configuration of at most 1 MiB\n");
    return 2;
  }

  void* library = dlopen(aArgv[1], RTLD_NOW | RTLD_GLOBAL);
  if (!library) {
    fprintf(stderr, "cannot load libxul: %s\n", dlerror());
    return 1;
  }
  RunEmbedded run = Resolve<RunEmbedded>(library, "NaiveFoxRunEmbedded");
  RequestStop stop = Resolve<RequestStop>(library, "NaiveFoxRequestStop");
  Version version = Resolve<Version>(library, "NaiveFoxVersion");
  if (!run || !stop || !version) {
    dlclose(library);
    return 1;
  }

  const char* versionValue = version();
  if (!versionValue || !*versionValue) {
    fprintf(stderr, "NaiveFoxVersion returned an empty value\n");
    dlclose(library);
    return 1;
  }
  if (!WriteAtomically(aArgv[6],
                       std::string("version=") + versionValue + "\n")) {
    fprintf(stderr, "cannot write readiness marker\n");
    dlclose(library);
    return 1;
  }

  std::atomic<bool> running{false};
  std::atomic<bool> finished{false};
  std::atomic<bool> stopRequested{false};
  std::thread controller([&] {
    while (!finished.load(std::memory_order_acquire)) {
      if (running.load(std::memory_order_acquire) &&
          access(aArgv[5], F_OK) == 0) {
        stopRequested.store(true, std::memory_order_release);
        stop();
        return;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
  });

  running.store(true, std::memory_order_release);
  bool rejectedAsExpected = true;
  for (const char* rejected : rejectedTransports) {
    if (run(config.c_str(), aArgv[3], aArgv[4], rejected) !=
        NAIVEFOX_STATUS_INVALID_ARGUMENT) {
      rejectedAsExpected = false;
      break;
    }
  }
  int runStatus = rejectedAsExpected
                      ? run(config.c_str(), aArgv[3], aArgv[4], transport)
                      : NAIVEFOX_STATUS_RUNTIME_ERROR;
  finished.store(true, std::memory_order_release);
  controller.join();

  std::string result =
      std::string("version=") + versionValue +
      "\nstatus=" + std::to_string(runStatus) + "\nstop_requested=" +
      (stopRequested.load(std::memory_order_acquire) ? "1\n" : "0\n") +
      "rejected_transports=" +
      std::to_string(rejectedAsExpected ? rejectedTransports.size() : 0) + "\n";
  bool wroteResult = WriteAtomically(aArgv[7], result);
  if (!wroteResult) {
    fprintf(stderr, "cannot write result marker\n");
    return 1;
  }
  return runStatus == NAIVEFOX_STATUS_OK ? 0 : 1;
}

}  // namespace

int main(int argc, char* argv[]) {
  if (argc == 4 && strcmp(argv[1], "--udp-probe") == 0) {
    return UdpProbe(argv[2], argv[3]);
  }
  return Run(argc, argv);
}
