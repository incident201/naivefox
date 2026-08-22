#!/usr/bin/env bash

set -euo pipefail

if (( $# != 1 )); then
  printf 'Usage: %s PACKAGE_DIR\n' "$0" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
package_dir=$(realpath -e -- "$1")
readelf=${NAIVEFOX_READELF:-}
if [[ -z $readelf ]]; then
  if command -v llvm-readelf >/dev/null 2>&1; then
    readelf=$(command -v llvm-readelf)
  elif command -v readelf >/dev/null 2>&1; then
    readelf=$(command -v readelf)
  fi
fi

arguments=(verify "$package_dir")
if [[ -n $readelf ]]; then
  arguments+=(--readelf "$readelf")
fi
python3 "$script_dir/android-runtime-package.py" "${arguments[@]}"
printf 'verified NaiveFox Android runtime: %s\n' "$package_dir"
