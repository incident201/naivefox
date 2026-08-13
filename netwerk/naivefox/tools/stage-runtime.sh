#!/usr/bin/env bash

set -euo pipefail

usage() {
  printf 'Usage: %s [OUTPUT_DIR]\n' "$0"
}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script_source_root=$(cd "$script_dir/../../.." && pwd)

if [[ ${1:-} == --help ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

environment_json=$("$script_source_root/mach" environment --format json)
environment_paths=$(python3 -c '
import json
import os
import sys

data = json.load(sys.stdin)
source = os.path.realpath(data["topsrcdir"])
objdir = os.path.realpath(data["topobjdir"])
if not os.path.isabs(source) or not os.path.isabs(objdir):
    raise SystemExit("mach returned a non-absolute source or object directory")
print(source + "\t" + objdir)
' <<<"$environment_json")
IFS=$'\t' read -r source_root objdir <<<"$environment_paths"

if [[ $source_root != "$script_source_root" || ! -d $objdir ]]; then
  printf 'mach environment does not describe this source tree\n' >&2
  exit 1
fi

output_arg=${1:-naivefox-linux-x86_64}
if [[ $output_arg == /* ]]; then
  requested_output=$output_arg
else
  requested_output="$objdir/$output_arg"
fi
if [[ -e $requested_output || -L $requested_output ]]; then
  printf 'refusing to overwrite existing output: %s\n' "$requested_output" >&2
  exit 1
fi
output_dir=$(python3 -c '
import os
import sys

objdir = os.path.realpath(sys.argv[1])
candidate = sys.argv[2]
if not os.path.isabs(candidate):
    candidate = os.path.join(objdir, candidate)
output = os.path.realpath(candidate)
if output == objdir or os.path.commonpath((objdir, output)) != objdir:
    raise SystemExit("output directory must resolve below the exact object directory")
print(output)
' "$objdir" "$output_arg")

if [[ ! -d $(dirname "$output_dir") ]]; then
  printf 'output parent does not exist: %s\n' "$(dirname "$output_dir")" >&2
  exit 1
fi

dist_bin="$objdir/dist/bin"
if [[ ! -d $dist_bin ]]; then
  printf 'Firefox runtime directory not found: %s\n' "$dist_bin" >&2
  exit 1
fi

stage_dir=$(mktemp -d "$objdir/.naivefox-stage.XXXXXX")
runtime_dir="$stage_dir/runtime"
mkdir -m 0755 "$runtime_dir"
cleanup() {
  if [[ -n ${stage_dir:-} && -d $stage_dir ]]; then
    case "$stage_dir" in
      "$objdir"/.naivefox-stage.*) rm -rf -- "$stage_dir" ;;
      *) printf 'refusing to remove unexpected temporary path: %s\n' "$stage_dir" >&2 ;;
    esac
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

required_files=(
  naivefox
  dependentlibs.list
  application.ini
  platform.ini
  default.locale
  greprefs.js
  chrome.manifest
)
required_dirs=(
  defaults
  components
  chrome
  res
  modules
  moz-src
  localization
  actors
)

for name in "${required_files[@]}"; do
  if [[ ! -f $dist_bin/$name ]]; then
    printf 'required runtime file is missing: %s\n' "$dist_bin/$name" >&2
    exit 1
  fi
  cp -aL -- "$dist_bin/$name" "$runtime_dir/"
done

while IFS= read -r library || [[ -n $library ]]; do
  if [[ -z $library || $library != "$(basename -- "$library")" ||
        $library == . || $library == .. ]]; then
    printf 'unsafe entry in dependentlibs.list: %q\n' "$library" >&2
    exit 1
  fi
  if [[ ! -f $dist_bin/$library ]]; then
    printf 'dependent runtime library is missing: %s\n' "$dist_bin/$library" >&2
    exit 1
  fi
  cp -aL -- "$dist_bin/$library" "$runtime_dir/"
done <"$dist_bin/dependentlibs.list"

for library in libsoftokn3.so libfreeblpriv3.so; do
  if [[ ! -f $dist_bin/$library ]]; then
    printf 'required NSS library is missing: %s\n' "$dist_bin/$library" >&2
    exit 1
  fi
  cp -aL -- "$dist_bin/$library" "$runtime_dir/"
done

for name in "${required_dirs[@]}"; do
  if [[ ! -d $dist_bin/$name ]]; then
    printf 'required runtime directory is missing: %s\n' "$dist_bin/$name" >&2
    exit 1
  fi
  cp -aL -- "$dist_bin/$name" "$runtime_dir/"
done

printf '%s\n' \
  '#!/usr/bin/env bash' \
  '' \
  'set -euo pipefail' \
  '' \
  'package_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)' \
  'runtime_dir="$package_dir/runtime"' \
  'export LD_LIBRARY_PATH="$runtime_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' \
  'exec "$runtime_dir/naivefox" "$@"' >"$stage_dir/naivefox"
chmod 0755 "$stage_dir/naivefox"

strip_tool=${NAIVEFOX_STRIP:-}
if [[ -n $strip_tool ]]; then
  strip_tool=$(command -v -- "$strip_tool") || {
    printf 'NAIVEFOX_STRIP is not executable: %s\n' "$NAIVEFOX_STRIP" >&2
    exit 1
  }
elif command -v llvm-strip >/dev/null 2>&1; then
  strip_tool=$(command -v llvm-strip)
elif command -v strip >/dev/null 2>&1; then
  strip_tool=$(command -v strip)
else
  printf 'no llvm-strip or strip executable was found\n' >&2
  exit 1
fi

while IFS= read -r -d '' staged_file; do
  file_type=$(LC_ALL=C file -b -- "$staged_file")
  if [[ $file_type == ELF\ * ]]; then
    "$strip_tool" --strip-debug "$staged_file"
  fi
done < <(find "$stage_dir" -type f -print0)

link=$(find "$stage_dir" -type l -print -quit)
if [[ -n $link ]]; then
  printf 'staged runtime contains a symbolic link: %s\n' "$link" >&2
  exit 1
fi

forbidden=$(find "$stage_dir" -type f \
  \( -iname '*cert9*' -o -iname '*key4*' -o -iname '*pkcs11*' \
     -o -iname '*.log' -o -iname '*.pcap' -o -iname '*.pcapng' \
     -o -iname '*keylog*' \) -print -quit)
if [[ -n $forbidden ]]; then
  printf 'forbidden sensitive artifact in staged runtime: %s\n' "$forbidden" >&2
  exit 1
fi

mv -T -- "$stage_dir" "$output_dir"
stage_dir=
trap - EXIT INT TERM
printf 'staged NaiveFox runtime: %s\n' "$output_dir"
