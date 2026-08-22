#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: build-product.sh linux|windows|android [OPTIONS]

Build and stage one NaiveFox product target.

Options:
  --bootstrap       bootstrap Mozilla build dependencies before building
  --dry-run         print the resolved build without running it
  --jobs N          parallel build jobs (default: NAIVEFOX_JOBS or 4)
  --objdir PATH     external object directory
  --package-dir PATH
                    staged package directory below the object directory

Environment:
  NAIVEFOX_USE_SCCACHE=1
                    opt in to the local sccache daemon (disabled by default)
EOF
}

if (( $# == 0 )); then
  usage >&2
  exit 2
fi

target=$1
shift
case "$target" in
  linux)
    mozconfig_name='mozconfig-minimal'
    package_name='naivefox-linux-x86_64'
    stage_script='stage-runtime.sh'
    ;;
  windows)
    mozconfig_name='mozconfig-windows-x86_64'
    package_name='naivefox-windows-x86_64'
    stage_script='stage-runtime-windows-x86_64.sh'
    ;;
  android)
    mozconfig_name='mozconfig-android-aarch64'
    package_name='naivefox-android-aarch64'
    stage_script='stage-runtime-android-aarch64.sh'
    ;;
  *)
    printf 'unsupported target: %s\n' "$target" >&2
    usage >&2
    exit 2
    ;;
esac

bootstrap=false
dry_run=false
jobs=${NAIVEFOX_JOBS:-4}
objdir=${NAIVEFOX_OBJDIR:-}
package_dir=${NAIVEFOX_PACKAGE_DIR:-}
while (( $# )); do
  case "$1" in
    --bootstrap)
      bootstrap=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --jobs)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      jobs=$2
      shift 2
      ;;
    --objdir)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      objdir=$2
      shift 2
      ;;
    --package-dir)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      package_dir=$2
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! $jobs =~ ^[1-9][0-9]*$ ]]; then
  printf 'jobs must be a positive integer: %s\n' "$jobs" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../../.." && pwd)
mozconfig="$repo_root/netwerk/naivefox/$mozconfig_name"
if [[ ! -f $mozconfig ]]; then
  printf 'mozconfig not found: %s\n' "$mozconfig" >&2
  exit 1
fi

if [[ -z $objdir ]]; then
  objdir="$repo_root/../obj-naivefox-$target"
fi
objdir=$(realpath -m -- "$objdir")
if [[ -z $package_dir ]]; then
  package_dir="$objdir/package/$package_name"
fi
package_dir=$(realpath -m -- "$package_dir")

python3 - "$repo_root" "$objdir" "$package_dir" <<'PY'
import os
import pathlib
import sys

repo, objdir, package = (pathlib.Path(value).resolve() for value in sys.argv[1:])
if os.path.commonpath((repo, objdir)) == str(repo):
    raise SystemExit(f"object directory must be outside the checkout: {objdir}")
if os.path.commonpath((objdir, package)) != str(objdir):
    raise SystemExit(f"package directory must be below the object directory: {package}")
PY

export MOZCONFIG=$mozconfig
export NAIVEFOX_OBJDIR=$objdir

# A product build must not silently depend on a long-lived, possibly stale
# compiler-cache daemon. Opt in explicitly when the daemon is known to be
# healthy; otherwise keep configure and build behavior reproducible.
if [[ ${NAIVEFOX_USE_SCCACHE:-0} == 1 ]]; then
  printf 'compiler cache: sccache (explicitly enabled)\n'
else
  export NAIVEFOX_DISABLE_SCCACHE=1
  export SCCACHE_DISABLE=1
  unset USE_SCCACHE
  printf 'compiler cache: disabled (set NAIVEFOX_USE_SCCACHE=1 to opt in)\n'
fi

if [[ $target == windows && $(uname -s) == Linux ]]; then
  wine_root=${NAIVEFOX_WINE_ROOT:-$HOME/.mozbuild/wine}
  if [[ -d $wine_root/lib/wine ]]; then
    export PATH="$wine_root/bin:$PATH"
    export WINEPREFIX=${WINEPREFIX:-$HOME/.wine}
    export WINEARCH=${WINEARCH:-win64}
    wine_paths=(
      "$wine_root/lib/wine/x86_64-unix"
      "$wine_root/lib/wine/i386-unix"
      "$wine_root/lib/wine/x86_64-windows"
      "$wine_root/lib/wine/i386-windows"
    )
    wine_dllpath=$(IFS=:; printf '%s' "${wine_paths[*]}")
    export WINEDLLPATH="${wine_dllpath}${WINEDLLPATH:+:$WINEDLLPATH}"
    # Mozilla's portable Wine needs the 32-bit builtins in a 64-bit prefix
    # before it can start its helper executable. Copy only missing files; this
    # is idempotent and leaves an existing user prefix untouched.
    syswow64="$WINEPREFIX/drive_c/windows/syswow64"
    if [[ ! -f $syswow64/kernel32.dll &&
          -d $wine_root/lib/wine/i386-windows ]]; then
      mkdir -p -- "$syswow64"
      cp --archive --update=none -- "$wine_root/lib/wine/i386-windows/." "$syswow64/"
    fi
  fi
fi

printf 'NaiveFox %s build\n' "$target"
printf '  source:    %s\n' "$repo_root"
printf '  mozconfig: %s\n' "$mozconfig"
printf '  objdir:    %s\n' "$objdir"
printf '  package:   %s\n' "$package_dir"
printf '  jobs:      %s\n' "$jobs"

if $bootstrap; then
  printf 'Bootstrapping Mozilla build dependencies...\n'
  if ! $dry_run; then
    "$repo_root/mach" --no-interactive bootstrap --application-choice browser
  fi
  if [[ $target == android ]]; then
    if $dry_run; then
      printf 'dry-run: rustup target add aarch64-linux-android\n'
    else
      rustup_bin=$(command -v rustup || true)
      if [[ -z $rustup_bin && -n ${HOME:-} &&
            -x ${HOME}/.cargo/bin/rustup ]]; then
        rustup_bin=${HOME}/.cargo/bin/rustup
      fi
      if [[ -z $rustup_bin ]]; then
        printf '%s\n' \
          "rustup is required to install the Android Rust target; add it to PATH or install it at \$HOME/.cargo/bin/rustup" >&2
        exit 1
      fi
      "$rustup_bin" target add aarch64-linux-android
    fi
  fi
fi

if ! $dry_run; then
  "$repo_root/mach" build "-j$jobs"
  bash "$script_dir/$stage_script" "$package_dir"
  printf 'NAIVEFOX_PACKAGE=%s\n' "$package_dir"
else
  printf 'dry-run: %q build -j%s\n' "$repo_root/mach" "$jobs"
  printf 'dry-run: %q %q\n' "$script_dir/$stage_script" "$package_dir"
fi
