#!/usr/bin/env bash

set -euo pipefail

usage() {
  printf 'Usage: %s [--caddy PATH] [STAGED_DIR]\n' "$0"
}

caddy_binary=
staged_arg=
while (( $# )); do
  case "$1" in
    --caddy)
      if (( $# < 2 )) || [[ -z $2 ]]; then
        usage >&2
        exit 2
      fi
      caddy_binary=$2
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    --*)
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n $staged_arg ]]; then
        usage >&2
        exit 2
      fi
      staged_arg=$1
      shift
      ;;
  esac
done

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script_source_root=$(cd "$script_dir/../../.." && pwd)
configured_objdir=${NAIVEFOX_OBJDIR:-${MOZ_OBJDIR:-}}
if [[ -n $configured_objdir ]]; then
  source_root=$script_source_root
  objdir=$(realpath -m -- "$configured_objdir")
else
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
fi

if [[ $source_root != "$script_source_root" || ! -d $objdir ]]; then
  printf 'mach environment does not describe this source tree\n' >&2
  exit 1
fi

staged_arg=${staged_arg:-naivefox-linux-x86_64}
if [[ $staged_arg == /* ]]; then
  requested_stage=$staged_arg
else
  requested_stage="$objdir/$staged_arg"
fi
if [[ -L $requested_stage ]]; then
  printf 'staged runtime path must not be a symbolic link: %s\n' \
    "$requested_stage" >&2
  exit 1
fi
staged_dir=$(python3 -c '
import os
import sys

objdir = os.path.realpath(sys.argv[1])
candidate = sys.argv[2]
if not os.path.isabs(candidate):
    candidate = os.path.join(objdir, candidate)
staged = os.path.realpath(candidate)
if staged == objdir or os.path.commonpath((objdir, staged)) != objdir:
    raise SystemExit("staged directory must resolve below the exact object directory")
print(staged)
' "$objdir" "$staged_arg")

if [[ ! -d $staged_dir ]]; then
  printf 'staged runtime directory not found: %s\n' "$staged_dir" >&2
  exit 1
fi

assert_clean_tree() {
  local tree=$1
  local link forbidden
  link=$(find "$tree" -type l -print -quit)
  if [[ -n $link ]]; then
    printf 'runtime contains a symbolic link: %s\n' "$link" >&2
    return 1
  fi
  forbidden=$(find "$tree" -type f \
    \( -iname '*cert9*' -o -iname '*key4*' -o -iname '*pkcs11*' \
       -o -iname '*.log' -o -iname '*.pcap' -o -iname '*.pcapng' \
       -o -iname '*keylog*' \) -print -quit)
  if [[ -n $forbidden ]]; then
    printf 'runtime contains a forbidden sensitive artifact: %s\n' \
      "$forbidden" >&2
    return 1
  fi
}

assert_clean_tree "$staged_dir"
"$script_dir/runtime-manifest.py" verify "$staged_dir"

verify_work="$objdir/verification"
if [[ -v NAIVEFOX_VERIFY_WORK_DIR ]]; then
  verify_work=$NAIVEFOX_VERIFY_WORK_DIR
fi
verify_parent=$(realpath -m -- "$verify_work")
case "$verify_parent" in
  "$objdir"/*) ;;
  *) printf 'verification work directory must be below objdir\n' >&2; exit 2 ;;
esac
mkdir -p -- "$verify_parent"
verify_root=$(mktemp -d "$verify_parent/naivefox-runtime-verify.XXXXXX")
cleanup() {
  if [[ -n $verify_root && -d $verify_root ]]; then
    case "$verify_root" in
      "$verify_parent"/naivefox-runtime-verify.*) rm -rf -- "$verify_root" ;;
      *) printf 'refusing unexpected cleanup path: %s\n' "$verify_root" >&2 ;;
    esac
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
chmod 0700 "$verify_root"

package_dir="$verify_root/package"
mkdir -m 0700 "$package_dir"
cp -aL -- "$staged_dir/." "$package_dir/"
assert_clean_tree "$package_dir"
"$script_dir/runtime-manifest.py" verify "$package_dir"

for required in naivefox runtime/naivefox \
  runtime/dependentlibs.list runtime/application.ini runtime-manifest.json; do
  if [[ ! -f $package_dir/$required ]]; then
    printf 'staged runtime is missing %s\n' "$required" >&2
    exit 1
  fi
done
if [[ ! -x $package_dir/naivefox ||
      ! -x $package_dir/runtime/naivefox ]]; then
  printf 'staged runtime executables are not executable\n' >&2
  exit 1
fi

ldd_output=$(env -u LD_PRELOAD -u SSLKEYLOGFILE \
  LD_LIBRARY_PATH="$package_dir/runtime" ldd "$package_dir/runtime/naivefox")
if grep -q 'not found' <<<"$ldd_output"; then
  printf '%s\n' "$ldd_output" >&2
  printf 'staged runtime has unresolved ELF dependencies\n' >&2
  exit 1
fi
unrelocated_ldd=$(python3 -c 'import sys; print(sys.stdin.read().replace(sys.argv[1] + "/", "relocated/"))' "$package_dir" <<<"$ldd_output")
if grep -Fq "$objdir" <<<"$unrelocated_ldd" ||
   grep -Fq "$source_root" <<<"$unrelocated_ldd"; then
  printf '%s\n' "$ldd_output" >&2
  printf 'staged runtime still resolves a dependency from the build tree\n' >&2
  exit 1
fi

export MOZ_CRASHREPORTER_DISABLE=1
export PYTHONDONTWRITEBYTECODE=1
env -u LD_PRELOAD -u SSLKEYLOGFILE \
  LD_LIBRARY_PATH="$package_dir/runtime" \
  python3 "$script_dir/runtime_smoke.py" \
    --runtime "$package_dir/runtime/naivefox" --work-dir "$verify_root/runtime-check"
python3 "$source_root/netwerk/naivefox/test/integration/run-transport-cli-tests.py" \
  --runtime "$package_dir/runtime/naivefox" --work-dir "$verify_root/cli-check"

if [[ -n $caddy_binary ]]; then
  NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1 unshare -n -- \
    bash "$source_root/netwerk/naivefox/test/integration/run-camouflage-isolated-network.sh" \
    python3 "$source_root/netwerk/naivefox/test/integration/run-transport-tests.py" \
      --runtime "$package_dir/runtime/naivefox" --caddy "$caddy_binary" \
      --objdir "$objdir" --output "$verify_parent/staged-transport-$$"
fi

assert_clean_tree "$package_dir"
"$script_dir/runtime-manifest.py" verify "$package_dir"
printf 'staged NaiveFox runtime verified from an independent package copy: %s\n' "$package_dir"
