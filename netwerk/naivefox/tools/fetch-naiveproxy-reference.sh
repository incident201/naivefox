#!/usr/bin/env bash

set -euo pipefail
umask 077

version=v150.0.7871.63-1
package="naiveproxy-$version-linux-x64"
archive_name="$package.tar.xz"
archive_sha256=0c4f506ce66a7881892fd6932b542c53fc06ac2351987756096c61e753c687bf
archive_url="https://github.com/klzgrad/naiveproxy/releases/download/$version/$archive_name"
expected_binary_version='naive 150.0.7871.63'

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source_root=$(cd "$script_dir/../../.." && pwd)
environment_json=$("$source_root/mach" environment --format json)
objdir=$(python3 -c '
import json
import os
import sys

data = json.load(sys.stdin)
print(os.path.realpath(data["topobjdir"]))
' <<<"$environment_json")

reference_root="$objdir/naiveproxy-reference"
output_dir="$reference_root/$package"
binary="$output_dir/naive"
if [[ -x $binary ]]; then
  actual_version=$($binary --version)
  [[ $actual_version == "$expected_binary_version" ]]
  printf 'NaiveProxy reference already ready: %s\n' "$binary"
  exit 0
fi
if [[ -e $output_dir || -L $output_dir ]]; then
  printf 'refusing incomplete reference directory: %s\n' "$output_dir" >&2
  exit 1
fi

mkdir -m 0700 -p "$reference_root"
stage_dir=$(mktemp -d "$reference_root/.stage.XXXXXX")
cleanup() {
  if [[ -n ${stage_dir:-} && -d $stage_dir ]]; then
    case $(realpath -- "$stage_dir") in
      "$(realpath -- "$reference_root")"/.stage.*) rm -rf -- "$stage_dir" ;;
      *) printf 'refusing unexpected cleanup path: %s\n' "$stage_dir" >&2 ;;
    esac
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

archive="$stage_dir/$archive_name"
curl --fail --location --silent --show-error "$archive_url" --output "$archive"
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check --status

python3 - "$archive" "$package" <<'PY'
import pathlib
import sys
import tarfile

archive = sys.argv[1]
package = sys.argv[2]
with tarfile.open(archive, "r:xz") as bundle:
    for member in bundle.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if not path.parts or path.parts[0] != package or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported archive entry: {member.name}")
PY

tar -xJf "$archive" -C "$stage_dir"
mv -T -- "$stage_dir/$package" "$output_dir"
printf '%s\n' "$archive_sha256" >"$output_dir/archive.sha256"
chmod 0600 "$output_dir/archive.sha256"

actual_version=$($binary --version)
[[ $actual_version == "$expected_binary_version" ]]
printf 'NaiveProxy reference ready: %s\n' "$binary"
