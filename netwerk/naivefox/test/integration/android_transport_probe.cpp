/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <arpa/inet.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace {

constexpr uint32_t kMaxBody = 8 * 1024 * 1024;
constexpr size_t kMaxHeader = 16 * 1024;

enum class Reply { Accepted, Rejected, Invalid };
enum class Operation { Download, Upload, Idle, Reset, Reject };

struct Options {
  bool socks = false;
  uint16_t localPort = 0;
  std::string targetHost;
  uint16_t targetPort = 0;
  Operation operation = Operation::Download;
  uint32_t length = 0;
  std::array<uint8_t, 40> uploadAck{};
  bool slow = false;
};

class Socket final {
 public:
  Socket() : mFd(socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0)) {}
  ~Socket() {
    if (mFd >= 0) {
      close(mFd);
    }
  }
  Socket(const Socket&) = delete;
  Socket& operator=(const Socket&) = delete;
  int Get() const { return mFd; }

 private:
  int mFd;
};

void Timeout(int) {
  constexpr char error[] = "FAIL: operation timed out\n";
  (void)write(STDERR_FILENO, error, sizeof(error) - 1);
  _exit(1);
}

int Fail(const char* aMessage) {
  std::fprintf(stderr, "FAIL: %s\n", aMessage);
  return 1;
}

int Usage() {
  std::fputs(
      "Usage: android_transport_probe socks|http LOCALPORT TARGETHOST "
      "TARGETPORT download|upload|idle|reset|reject LENGTH "
      "[ACK_HEX|-] [slow]\n",
      stderr);
  return 2;
}

bool Number(const char* aText, uint32_t aMaximum, uint32_t& aValue) {
  if (!*aText) {
    return false;
  }
  uint64_t value = 0;
  for (const char* next = aText; *next; ++next) {
    if (*next < '0' || *next > '9') {
      return false;
    }
    value = value * 10 + static_cast<unsigned>(*next - '0');
    if (value > aMaximum) {
      return false;
    }
  }
  aValue = static_cast<uint32_t>(value);
  return true;
}

int Hex(char aValue) {
  if (aValue >= '0' && aValue <= '9') {
    return aValue - '0';
  }
  if (aValue >= 'a' && aValue <= 'f') {
    return aValue - 'a' + 10;
  }
  if (aValue >= 'A' && aValue <= 'F') {
    return aValue - 'A' + 10;
  }
  return -1;
}

bool Parse(int aCount, char** aArguments, Options& aOptions) {
  if (aCount < 7 || aCount > 9) {
    return false;
  }
  if (std::strcmp(aArguments[1], "socks") == 0) {
    aOptions.socks = true;
  } else if (std::strcmp(aArguments[1], "http") != 0) {
    return false;
  }
  uint32_t localPort = 0;
  uint32_t targetPort = 0;
  if (!Number(aArguments[2], 65535, localPort) || !localPort ||
      !Number(aArguments[4], 65535, targetPort) || !targetPort ||
      !Number(aArguments[6], kMaxBody, aOptions.length)) {
    return false;
  }
  aOptions.localPort = static_cast<uint16_t>(localPort);
  aOptions.targetPort = static_cast<uint16_t>(targetPort);
  aOptions.targetHost = aArguments[3];
  if (aOptions.targetHost.empty() || aOptions.targetHost.size() > 255) {
    return false;
  }
  for (char value : aOptions.targetHost) {
    if (!((value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') ||
          (value >= '0' && value <= '9') || value == '.' || value == '-' ||
          value == '_' || value == ':')) {
      return false;
    }
  }

  const char* operation = aArguments[5];
  if (std::strcmp(operation, "download") == 0) {
    aOptions.operation = Operation::Download;
  } else if (std::strcmp(operation, "upload") == 0) {
    aOptions.operation = Operation::Upload;
  } else if (std::strcmp(operation, "idle") == 0) {
    aOptions.operation = Operation::Idle;
  } else if (std::strcmp(operation, "reset") == 0) {
    aOptions.operation = Operation::Reset;
  } else if (std::strcmp(operation, "reject") == 0) {
    aOptions.operation = Operation::Reject;
  } else {
    return false;
  }
  if (aOptions.operation != Operation::Download &&
      aOptions.operation != Operation::Upload && aOptions.length != 0) {
    return false;
  }

  const char* acknowledgement = aCount >= 8 ? aArguments[7] : "-";
  if (std::strcmp(acknowledgement, "slow") == 0 && aCount == 8) {
    aOptions.slow = true;
    acknowledgement = "-";
  }
  if (aCount == 9) {
    if (std::strcmp(aArguments[8], "slow") != 0) {
      return false;
    }
    aOptions.slow = true;
  }
  if (aOptions.operation == Operation::Upload) {
    if (std::strlen(acknowledgement) != 80) {
      return false;
    }
    for (size_t i = 0; i < aOptions.uploadAck.size(); ++i) {
      const int high = Hex(acknowledgement[2 * i]);
      const int low = Hex(acknowledgement[2 * i + 1]);
      if (high < 0 || low < 0) {
        return false;
      }
      aOptions.uploadAck[i] = static_cast<uint8_t>((high << 4) | low);
    }
    uint64_t declared = 0;
    for (size_t i = 0; i < 8; ++i) {
      declared = (declared << 8) | aOptions.uploadAck[i];
    }
    if (declared != aOptions.length) {
      return false;
    }
  } else if (std::strcmp(acknowledgement, "-") != 0) {
    return false;
  }
  return true;
}

bool WriteAll(int aSocket, const uint8_t* aBytes, size_t aLength) {
  while (aLength) {
    const ssize_t written = send(aSocket, aBytes, aLength, MSG_NOSIGNAL);
    if (written < 0 && errno == EINTR) {
      continue;
    }
    if (written <= 0) {
      return false;
    }
    aBytes += static_cast<size_t>(written);
    aLength -= static_cast<size_t>(written);
  }
  return true;
}

bool ReadAll(int aSocket, uint8_t* aBytes, size_t aLength) {
  while (aLength) {
    const ssize_t count = recv(aSocket, aBytes, aLength, 0);
    if (count < 0 && errno == EINTR) {
      continue;
    }
    if (count <= 0) {
      return false;
    }
    aBytes += static_cast<size_t>(count);
    aLength -= static_cast<size_t>(count);
  }
  return true;
}

bool EndOfStream(int aSocket) {
  uint8_t extra = 0;
  ssize_t count;
  do {
    count = recv(aSocket, &extra, 1, 0);
  } while (count < 0 && errno == EINTR);
  return count == 0;
}

bool ConnectLocal(int aSocket, const Options& aOptions) {
  if (aSocket < 0) {
    return false;
  }
  const timeval timeout{60, 0};
  if (setsockopt(aSocket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
      setsockopt(aSocket, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout))) {
    return false;
  }
  if (aOptions.slow) {
    const int buffer = 8192;
    if (setsockopt(aSocket, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer))) {
      return false;
    }
  }
  sockaddr_in local{};
  local.sin_family = AF_INET;
  local.sin_port = htons(aOptions.localPort);
  local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  return connect(aSocket, reinterpret_cast<const sockaddr*>(&local),
                 sizeof(local)) == 0;
}

Reply SocksConnect(int aSocket, const Options& aOptions) {
  const uint8_t greeting[]{5, 1, 0};
  std::array<uint8_t, 2> method{};
  if (!WriteAll(aSocket, greeting, sizeof(greeting)) ||
      !ReadAll(aSocket, method.data(), method.size()) || method[0] != 5 ||
      method[1] != 0) {
    return Reply::Invalid;
  }
  std::array<uint8_t, 262> request{};
  request[0] = 5;
  request[1] = 1;
  request[3] = 3;
  request[4] = static_cast<uint8_t>(aOptions.targetHost.size());
  std::memcpy(request.data() + 5, aOptions.targetHost.data(),
              aOptions.targetHost.size());
  const size_t offset = 5 + aOptions.targetHost.size();
  request[offset] = static_cast<uint8_t>(aOptions.targetPort >> 8);
  request[offset + 1] = static_cast<uint8_t>(aOptions.targetPort);
  std::array<uint8_t, 4> head{};
  if (!WriteAll(aSocket, request.data(), offset + 2) ||
      !ReadAll(aSocket, head.data(), head.size()) || head[0] != 5 ||
      head[1] > 8 || head[2] != 0) {
    return Reply::Invalid;
  }
  std::array<uint8_t, 257> address{};
  size_t length = 0;
  if (head[3] == 1) {
    length = 6;
  } else if (head[3] == 4) {
    length = 18;
  } else if (head[3] == 3) {
    if (!ReadAll(aSocket, address.data(), 1) || address[0] == 0) {
      return Reply::Invalid;
    }
    length = size_t(address[0]) + 2;
  } else {
    return Reply::Invalid;
  }
  if (!ReadAll(aSocket, address.data(), length)) {
    return Reply::Invalid;
  }
  return head[1] == 0 ? Reply::Accepted : Reply::Rejected;
}

Reply HttpConnect(int aSocket, const Options& aOptions) {
  std::string authority = aOptions.targetHost.find(':') == std::string::npos
                              ? aOptions.targetHost
                              : "[" + aOptions.targetHost + "]";
  authority += ":" + std::to_string(aOptions.targetPort);
  const std::string request =
      "CONNECT " + authority + " HTTP/1.1\r\nHost: " + authority + "\r\n\r\n";
  if (!WriteAll(aSocket, reinterpret_cast<const uint8_t*>(request.data()),
                request.size())) {
    return Reply::Invalid;
  }
  std::string header;
  header.reserve(256);
  while (header.size() < kMaxHeader) {
    uint8_t value = 0;
    if (!ReadAll(aSocket, &value, 1)) {
      return Reply::Invalid;
    }
    header.push_back(static_cast<char>(value));
    if (header.size() >= 4 &&
        header.compare(header.size() - 4, 4, "\r\n\r\n") == 0) {
      if (header.size() < 14 ||
          (header.compare(0, 9, "HTTP/1.1 ") != 0 &&
           header.compare(0, 9, "HTTP/1.0 ") != 0) ||
          (header[12] != ' ' && header[12] != '\r')) {
        return Reply::Invalid;
      }
      uint32_t status = 0;
      const std::string number = header.substr(9, 3);
      if (!Number(number.c_str(), 599, status) || status < 100) {
        return Reply::Invalid;
      }
      if (status == 200) {
        return Reply::Accepted;
      }
      return status >= 400 ? Reply::Rejected : Reply::Invalid;
    }
  }
  return Reply::Invalid;
}

bool SendRequest(int aSocket, uint8_t aKind, uint32_t aLength) {
  const uint8_t request[]{aKind, static_cast<uint8_t>(aLength >> 24),
                          static_cast<uint8_t>(aLength >> 16),
                          static_cast<uint8_t>(aLength >> 8),
                          static_cast<uint8_t>(aLength)};
  return WriteAll(aSocket, request, sizeof(request));
}

bool SendPattern(int aSocket, uint32_t aLength) {
  std::array<uint8_t, 65536> buffer;
  for (size_t i = 0; i < buffer.size(); ++i) {
    buffer[i] = static_cast<uint8_t>(i);
  }
  while (aLength) {
    const size_t length = std::min(size_t(aLength), buffer.size());
    if (!WriteAll(aSocket, buffer.data(), length)) {
      return false;
    }
    aLength -= static_cast<uint32_t>(length);
  }
  return true;
}

bool Download(int aSocket, const Options& aOptions) {
  if (!SendRequest(aSocket, 'D', aOptions.length) ||
      shutdown(aSocket, SHUT_WR) != 0) {
    return false;
  }
  std::array<uint8_t, 65536> buffer{};
  uint32_t total = 0;
  while (true) {
    const ssize_t count =
        recv(aSocket, buffer.data(), aOptions.slow ? 4096 : buffer.size(), 0);
    if (count < 0 && errno == EINTR) {
      continue;
    }
    if (count < 0) {
      return false;
    }
    if (count == 0) {
      return total == aOptions.length;
    }
    if (static_cast<size_t>(count) > aOptions.length - total) {
      return false;
    }
    for (size_t i = 0; i < static_cast<size_t>(count); ++i) {
      if (buffer[i] != static_cast<uint8_t>(total + i)) {
        return false;
      }
    }
    total += static_cast<uint32_t>(count);
    if (aOptions.slow) {
      usleep(500);
    }
  }
}

bool Upload(int aSocket, const Options& aOptions) {
  if (!SendRequest(aSocket, 'U', aOptions.length) ||
      !SendPattern(aSocket, aOptions.length) ||
      shutdown(aSocket, SHUT_WR) != 0) {
    return false;
  }
  std::array<uint8_t, 40> received{};
  return ReadAll(aSocket, received.data(), received.size()) &&
         received == aOptions.uploadAck && EndOfStream(aSocket);
}

bool Idle(int aSocket) {
  const uint8_t kind = 'E';
  if (!WriteAll(aSocket, &kind, 1)) {
    return false;
  }
  std::array<uint8_t, 4096> received{};
  for (unsigned round = 0; round < 2; ++round) {
    if (round) {
      usleep(2000000);
    }
    if (!SendPattern(aSocket, received.size()) ||
        !ReadAll(aSocket, received.data(), received.size())) {
      return false;
    }
    for (size_t i = 0; i < received.size(); ++i) {
      if (received[i] != static_cast<uint8_t>(i)) {
        return false;
      }
    }
  }
  return shutdown(aSocket, SHUT_WR) == 0 && EndOfStream(aSocket);
}

bool Reset(int aSocket) {
  const uint8_t kind = 'C';
  if (!WriteAll(aSocket, &kind, 1)) {
    return false;
  }
  usleep(50000);
  const linger reset{1, 0};
  return setsockopt(aSocket, SOL_SOCKET, SO_LINGER, &reset, sizeof(reset)) == 0;
}

}  // namespace

int main(int argc, char** argv) {
  Options options;
  if (!Parse(argc, argv, options)) {
    return Usage();
  }
  struct sigaction action{};
  action.sa_handler = Timeout;
  sigemptyset(&action.sa_mask);
  if (sigaction(SIGALRM, &action, nullptr) != 0 ||
      signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
    return Fail("timeout setup failed");
  }
  alarm(60);
  Socket local;
  if (!ConnectLocal(local.Get(), options)) {
    return Fail("local proxy connection failed");
  }
  const Reply reply = options.socks ? SocksConnect(local.Get(), options)
                                    : HttpConnect(local.Get(), options);
  if (options.operation == Operation::Reject) {
    if (reply != Reply::Rejected) {
      return Fail("local proxy did not reject the request");
    }
  } else {
    if (reply != Reply::Accepted) {
      return Fail("local proxy did not establish the request");
    }
    bool success = false;
    switch (options.operation) {
      case Operation::Download:
        success = Download(local.Get(), options);
        break;
      case Operation::Upload:
        success = Upload(local.Get(), options);
        break;
      case Operation::Idle:
        success = Idle(local.Get());
        break;
      case Operation::Reset:
        success = Reset(local.Get());
        break;
      case Operation::Reject:
        break;
    }
    if (!success) {
      return Fail("workload integrity or stream lifecycle failed");
    }
  }
  alarm(0);
  std::puts("PASS");
  return 0;
}
