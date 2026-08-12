/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdio>
#include <cstring>

#include "NaiveFoxAPI.h"
#include "mozilla/Bootstrap.h"

namespace {

constexpr const char* kVersion = "0.1.0-dev";

void PrintUsage(const char* aProgram) {
  std::printf("Usage: %s [--version | OPTIONS]\n", aProgram);
}

}  // namespace

int main(int argc, char* argv[]) {
  if (argc == 2 && std::strcmp(argv[1], "--version") == 0) {
    std::printf("NaiveFox %s\n", kVersion);
    return 0;
  }

  if (argc == 1) {
    PrintUsage(argv[0]);
    return 0;
  }

  auto bootstrapResult = mozilla::GetBootstrap();
  if (bootstrapResult.isErr()) {
    return 1;
  }
  mozilla::Bootstrap::UniquePtr bootstrap = bootstrapResult.unwrap();

  return NaiveFoxMain(argc, argv);
}
