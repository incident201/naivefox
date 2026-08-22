/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <cstdio>
#include <cstring>

#include "NaiveFoxAPI.h"

int main(int argc, char* argv[]) {
  if (argc == 2 && std::strcmp(argv[1], "--version") == 0) {
    std::printf("NaiveFox %s\n", NaiveFoxVersion());
    return 0;
  }

  return NaiveFoxMain(argc, argv);
}
