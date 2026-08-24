#!/usr/bin/env bash

set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: build-firefox-same-base.sh [OPTIONS]

Build one reusable ordinary Firefox reference from the exact merge-base of
the current NaiveFox revision and firefox-upstream.

Options:
  --dry-run         validate and print the resolved build without writing
  --verify          verify an existing completed reference and its hashes
  --reuse           verify an existing completed reference and print paths
  --jobs N          parallel build jobs (default: NAIVEFOX_JOBS or 4)
  --objdir PATH     external reference object directory
  --worktree PATH   external detached Firefox worktree
  --firefox-ref REF clean Firefox mirror reference (default: firefox-upstream)
  --sccache MODE    auto, on, or off (default: auto)
  --help

The script never deletes or clobbers an existing worktree or object directory.
A prepared manifest permits an interrupted exact build to resume. A completed
manifest is fully verified and reused without rebuilding.
EOF
}

action=build
jobs=${NAIVEFOX_JOBS:-4}
objdir=${NAIVEFOX_FIREFOX_REFERENCE_OBJDIR:-}
worktree=${NAIVEFOX_FIREFOX_REFERENCE_WORKTREE:-}
firefox_ref=${NAIVEFOX_FIREFOX_REFERENCE_REF:-firefox-upstream}
sccache_selection=${NAIVEFOX_FIREFOX_REFERENCE_SCCACHE:-auto}
while (( $# )); do
  case "$1" in
    --dry-run|--verify|--reuse)
      [[ $action == build ]] || { printf 'select only one action\n' >&2; exit 2; }
      action=${1#--}
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
    --worktree)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      worktree=$2
      shift 2
      ;;
    --firefox-ref)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      firefox_ref=$2
      shift 2
      ;;
    --sccache)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      sccache_selection=$2
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

[[ $jobs =~ ^[1-9][0-9]*$ ]] || {
  printf 'jobs must be a positive integer: %s\n' "$jobs" >&2
  exit 2
}
case "$sccache_selection" in
  auto|on|off) ;;
  *)
    printf 'sccache mode must be auto, on, or off: %s\n' "$sccache_selection" >&2
    exit 2
    ;;
esac

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../../.." && pwd)
git_root=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z $git_root || $(realpath -- "$git_root") != "$repo_root" ]]; then
  printf 'same-base Firefox requires a full Git checkout rooted at %s\n' "$repo_root" >&2
  exit 1
fi
if [[ $action == build && -n $(git -C "$repo_root" status --porcelain=v1) ]]; then
  printf 'source checkout must be clean before recording same-base provenance\n' >&2
  exit 1
fi

source_revision=$(git -C "$repo_root" rev-parse --verify 'HEAD^{commit}')
firefox_ref_revision=$(git -C "$repo_root" rev-parse --verify "$firefox_ref^{commit}")
base_revision=$(git -C "$repo_root" merge-base HEAD "$firefox_ref")
[[ $base_revision =~ ^[0-9a-f]{40}$ ]] || {
  printf 'could not derive one canonical Firefox merge-base\n' >&2
  exit 1
}

if [[ -z $worktree ]]; then
  worktree="$repo_root/../firefox-same-base-${base_revision:0:12}"
fi
if [[ -z $objdir ]]; then
  objdir="$repo_root/../../obj-firefox-same-base-${base_revision:0:12}"
fi
worktree=$(realpath -m -- "$worktree")
objdir=$(realpath -m -- "$objdir")

sccache_path=''
if [[ $sccache_selection != off ]]; then
  sccache_path=$(command -v sccache || true)
  if [[ -z $sccache_path && -n ${HOME:-} && -x $HOME/.mozbuild/sccache/sccache ]]; then
    sccache_path=$HOME/.mozbuild/sccache/sccache
  fi
  if [[ $sccache_selection == on && -z $sccache_path ]]; then
    printf 'sccache was requested but no executable was found\n' >&2
    exit 1
  fi
fi
if [[ -n $sccache_path ]]; then
  sccache_path=$(realpath -- "$sccache_path")
fi

shell_quote() {
  printf '%q' "$1"
}

mozconfig_content=$(printf '%s\n' \
  "mk_add_options MOZ_OBJDIR=$(shell_quote "$objdir")" \
  'ac_add_options --enable-project=browser' \
  'ac_add_options --enable-optimize' \
  'ac_add_options --disable-debug' \
  'ac_add_options --disable-tests')
if [[ -n $sccache_path ]]; then
  mozconfig_content+=$'\n'
  mozconfig_content+="ac_add_options --with-ccache=$(shell_quote "$sccache_path")"
fi
mozconfig_content+=$'\n'

helper="$script_dir/firefox_same_base_manifest.py"
[[ -f $helper ]] || { printf 'same-base manifest helper is missing: %s\n' "$helper" >&2; exit 1; }
common_args=(
  --repo "$repo_root"
  --source-revision "$source_revision"
  --firefox-ref "$firefox_ref"
  --firefox-ref-revision "$firefox_ref_revision"
  --base-revision "$base_revision"
  --worktree "$worktree"
  --objdir "$objdir"
  --jobs "$jobs"
  --sccache-selection "$sccache_selection"
  --sccache-path "$sccache_path"
)

printf 'Firefox same-base reference\n'
printf '  NaiveFox revision: %s\n' "$source_revision"
printf '  Firefox ref:       %s (%s)\n' "$firefox_ref" "$firefox_ref_revision"
printf '  Firefox base:      %s\n' "$base_revision"
printf '  worktree:          %s\n' "$worktree"
printf '  objdir:            %s\n' "$objdir"
printf '  jobs:              %s\n' "$jobs"
if [[ -n $sccache_path ]]; then
  printf '  sccache:           %s\n' "$sccache_path"
else
printf '  sccache:           disabled (%s)\n' "$sccache_selection"
fi

# Validate all user-controlled paths before git worktree or objdir creation.
python3 "$helper" check-layout "${common_args[@]}" >/dev/null

if [[ $action == dry-run ]]; then
  python3 - "$repo_root" "$worktree" "$objdir" <<'PY'
import os
import pathlib
import sys

repo, worktree, objdir = (pathlib.Path(value).resolve() for value in sys.argv[1:])
if os.path.commonpath((str(repo), str(worktree))) == str(repo):
    raise SystemExit(f"reference worktree must be outside the source checkout: {worktree}")
if os.path.commonpath((str(repo), str(objdir))) == str(repo):
    raise SystemExit(f"reference object directory must be outside the source checkout: {objdir}")
if worktree == objdir or os.path.commonpath((str(worktree), str(objdir))) in (
    str(worktree), str(objdir)
):
    raise SystemExit("reference worktree and object directory must not contain each other")
PY
  if [[ ! -e $worktree && -e $objdir ]]; then
    printf 'dry-run refuses an existing object directory without a verifiable completed manifest: %s\n' "$objdir" >&2
    exit 1
  elif [[ -e $worktree ]]; then
    python3 "$helper" check-worktree "${common_args[@]}" >/dev/null
    if [[ -e $objdir ]]; then
      state=$(python3 "$helper" prepare "${common_args[@]}" --mozconfig-content "$mozconfig_content")
      if [[ $state == complete ]]; then
        python3 "$helper" verify "${common_args[@]}" >/dev/null
        printf 'dry-run: completed manifest would be verified and reused\n'
      else
        printf 'dry-run: prepared manifest would resume the exact build\n'
      fi
    else
      printf 'dry-run: existing detached pristine worktree would be reused\n'
      printf '%s' "$mozconfig_content" | sed 's/^/  mozconfig: /'
      printf 'dry-run: MOZCONFIG=%q %q build -j%s\n' "$objdir/firefox-same-base.mozconfig" "$worktree/mach" "$jobs"
      printf 'dry-run: MOZCONFIG=%q %q package\n' "$objdir/firefox-same-base.mozconfig" "$worktree/mach"
    fi
  else
    printf 'dry-run: git -C %q worktree add --detach %q %s\n' "$repo_root" "$worktree" "$base_revision"
    printf '%s' "$mozconfig_content" | sed 's/^/  mozconfig: /'
    printf 'dry-run: MOZCONFIG=%q %q build -j%s\n' "$objdir/firefox-same-base.mozconfig" "$worktree/mach" "$jobs"
    printf 'dry-run: MOZCONFIG=%q %q package\n' "$objdir/firefox-same-base.mozconfig" "$worktree/mach"
  fi
  exit 0
fi

if [[ $action == verify ]]; then
  python3 "$helper" verify "${common_args[@]}"
  exit 0
fi
if [[ $action == reuse ]]; then
  python3 "$helper" show "${common_args[@]}"
  exit 0
fi

command -v readelf >/dev/null || {
  printf 'readelf is required to record Firefox ELF build IDs\n' >&2
  exit 1
}
if [[ ! -e $worktree && -e $objdir ]]; then
  printf 'refusing existing object directory without its registered worktree: %s\n' "$objdir" >&2
  exit 1
fi
if [[ ! -e $worktree ]]; then
  git -C "$repo_root" worktree add --detach "$worktree" "$base_revision"
fi
state=$(python3 "$helper" prepare "${common_args[@]}" --mozconfig-content "$mozconfig_content")
if [[ $state == complete ]]; then
  python3 "$helper" show "${common_args[@]}"
  exit 0
fi
[[ $state == prepared ]] || {
  printf 'unexpected reference preparation state: %s\n' "$state" >&2
  exit 1
}

export MOZCONFIG="$objdir/firefox-same-base.mozconfig"
"$worktree/mach" build "-j$jobs"
"$worktree/mach" package
python3 "$helper" complete "${common_args[@]}" >/dev/null
python3 "$helper" show "${common_args[@]}"
