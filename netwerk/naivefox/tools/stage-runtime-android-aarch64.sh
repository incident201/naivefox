#!/usr/bin/env bash

set -euo pipefail

usage() {
  printf 'Usage: %s [OUTPUT_DIR]\n' "$0"
}

if [[ ${1:-} == --help ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source_root=$(cd "$script_dir/../../.." && pwd)
configured_objdir=${NAIVEFOX_OBJDIR:-${MOZ_OBJDIR:-}}
if [[ -z $configured_objdir ]]; then
  printf 'NAIVEFOX_OBJDIR or MOZ_OBJDIR must name the Android object directory\n' >&2
  exit 2
fi
objdir=$(realpath -m -- "$configured_objdir")
if [[ ! -d $objdir || $objdir == "$source_root" || $objdir == "$source_root"/* ]]; then
  printf 'Android object directory must exist outside the source tree: %s\n' "$objdir" >&2
  exit 1
fi

output_arg=${1:-package/naivefox-android-aarch64}
if [[ $output_arg == /* ]]; then
  output_dir=$(realpath -m -- "$output_arg")
else
  output_dir=$(realpath -m -- "$objdir/$output_arg")
fi
if [[ $output_dir == "$objdir" || $output_dir != "$objdir"/* ]]; then
  printf 'output directory must remain below the Android object directory\n' >&2
  exit 2
fi
if [[ -e $output_dir || -L $output_dir ]]; then
  printf 'refusing to overwrite existing output: %s\n' "$output_dir" >&2
  exit 1
fi
mkdir -p -- "$(dirname "$output_dir")"

stage_dir=$(mktemp -d "$objdir/.naivefox-android-stage.XXXXXX")
chmod 0755 "$stage_dir"
cleanup() {
  if [[ -n ${stage_dir:-} && -d $stage_dir ]]; then
    case "$stage_dir" in
      "$objdir"/.naivefox-android-stage.*) rm -rf -- "$stage_dir" ;;
      *) printf 'refusing to remove unexpected temporary path: %s\n' "$stage_dir" >&2 ;;
    esac
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

python3 "$script_dir/android-runtime-package.py" create \
  --source-root "$source_root" \
  --objdir "$objdir" \
  --stage "$stage_dir"

mv -T -- "$stage_dir" "$output_dir"
stage_dir=
trap - EXIT INT TERM
printf 'staged NaiveFox Android runtime: %s\n' "$output_dir"
