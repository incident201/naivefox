#!/usr/bin/env bash
# Run the production codec's gtests against a warm product NSS/NSPR build.
# NAIVEFOX_CODEC_SANITIZERS=1 enables ASan/UBSan without rebuilding NSS.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  printf 'Usage: %s <existing-linux-objdir> [output-directory]\n' "$0" >&2
  exit 2
fi

source_root=$(cd "$(dirname "$0")/../../../.." && pwd)
objdir=$(cd "$1" && pwd)
output=${2:-"$objdir/no-connect-codec-tests"}
mkdir -p "$output"
output=$(cd "$output" && pwd)
gtest="$source_root/third_party/googletest/googletest"
runtime="$objdir/dist/bin"
compiler=${CXX:-"${MOZBUILD_STATE_PATH:-$HOME/.mozbuild}/clang/bin/clang++"}
if [[ ! -x $compiler ]]; then
  printf 'Set CXX to an existing C++ compiler.\n' >&2
  exit 2
fi
for required in "$objdir/dist/include/nss/nss.h" \
                "$objdir/dist/include/nspr/nspr.h" \
                "$runtime/libnss3.so" "$runtime/libnspr4.so" \
                "$gtest/src/gtest-all.cc"; do
  if [[ ! -f $required ]]; then
    printf 'Missing warm-build dependency: %s\n' "$required" >&2
    exit 2
  fi
done

mkdir -p "$output/include/gtest/internal/custom"
for header in gtest.h gtest-port.h gtest-printers.h; do
  cp "$source_root/testing/gtest/mozilla/gtest-custom/$header" \
     "$output/include/gtest/internal/custom/$header"
done

flags=(-std=c++17 -O2 -g -pthread -Wall -Wextra)
if [[ ${NAIVEFOX_CODEC_SANITIZERS:-0} == 1 ]]; then
  flags+=(-O1 -fno-omit-frame-pointer -fsanitize=address,undefined)
fi
if ! env -u LD_PRELOAD -u LD_LIBRARY_PATH "$compiler" "${flags[@]}" \
    -I"$source_root/netwerk/naivefox" -I"$output/include" \
    -I"$objdir/dist/include" -I"$objdir/dist/include/nss" -I"$objdir/dist/include/nspr" \
    -I"$source_root/nsprpub/pr/include" \
    -I"$gtest/include" -I"$gtest" \
    "$source_root/netwerk/naivefox/NoConnectCodec.cpp" \
    "$source_root/netwerk/naivefox/test/gtest/TestNoConnectCodec.cpp" \
    "$gtest/src/gtest-all.cc" "$gtest/src/gtest_main.cc" \
    -L"$runtime" -Wl,-rpath-link,"$runtime" -lnss3 -lnssutil3 -lnspr4 \
    -o "$output/no-connect-codec-tests" >"$output/build.log" 2>&1; then
  printf 'Codec compile failed; see %s/build.log\n' "$output" >&2
  exit 1
fi

if ! env -u LD_PRELOAD LD_LIBRARY_PATH="$runtime" \
    "$output/no-connect-codec-tests" \
    --gtest_filter="NaiveFoxNoConnectCodec.*" \
    --gtest_output="xml:$output/results.xml" >"$output/results.log" 2>&1; then
  printf 'Codec tests failed; see %s/results.log\n' "$output" >&2
  exit 1
fi
cat "$output/results.log"
