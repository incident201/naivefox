#!/usr/bin/env bash
# Explicit campaign closeout. No archives or backups are created.
# Dry-run by default; --apply deletes only the reviewed targets below.
set -euo pipefail
shopt -s nullglob dotglob
cleanup_base=/home/zubastik
carrier_root=/home/zubastik/naivefox-app-carrier-20260830.U0xyrg
apply=0
scope=
while (($#)); do
  case "$1" in
    --apply) apply=1; shift ;;
    --scope) scope=${2:?scope required}; shift 2 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ $scope == legacy || $scope == results || $scope == research ]] || { echo '--scope legacy|results|research required' >&2; exit 2; }
[[ $(realpath -e -- "$cleanup_base") == "$cleanup_base" ]] || exit 2
targets=()
check_target() {
  local target=$1 parent resolved
  [[ -e $target || -L $target ]] || return 1
  parent=$(realpath -e -- "$(dirname -- "$target")")
  if [[ $scope == legacy ]]; then
    [[ $parent == "$cleanup_base" ]] || return 2
  else
    [[ $parent == "$carrier_root" || $parent == "$carrier_root/"* ]] || return 2
  fi
  [[ $target != "$cleanup_base" && $target != "$carrier_root" ]] || return 2
  if [[ ! -L $target ]]; then
    resolved=$(realpath -e -- "$target")
    [[ $resolved == "$target" ]] || return 2
  fi
  [[ ! -e $target/.git && ! -L $target/.git ]] || return 2
}
add_target() {
  local target=$1
  [[ -e $target || -L $target ]] || return 0
  check_target "$target" || { printf 'refused target: %s\n' "$target" >&2; exit 2; }
  targets+=("$target")
}
if [[ $scope == legacy ]]; then
  # Export snapshots/configure products; source checkouts are deliberately absent.
  names=(
    evidence-refresh-aebb8
    naivefox-configure-evidence-51528a58 naivefox-configure-evidence-745d58bf
    naivefox-configure-evidence-c0322e97 naivefox-configure-evidence-windows-af716bf5
    naivefox-configure-trace-linux-20260820
    naivefox-evidence-758-jbX96D naivefox-evidence-clean-f5eb
    naivefox-evidence-final-66f naivefox-evidence-final-f5eb
    naivefox-evidence-netlink-clean.yp5tlN naivefox-evidence-netlink-final.PmkKyV
    naivefox-evidence-netlink.iPRR2X
    naivefox-export-758.7gKpue naivefox-export-clean-f5eb naivefox-export-final-f5eb
    naivefox-export-native-win-22fd5248 naivefox-export-native-win-a7a
    naivefox-export-native-win-aebb8 naivefox-export-netlink-final.wzTGxT
    naivefox-export-netlink.WeYAYs naivefox-export-test
    naivefox-export-v2 naivefox-export-v3 naivefox-export-v4 naivefox-export-v8
    naivefox-export-v9 naivefox-export-v10 naivefox-export-v11 naivefox-export-v12
    naivefox-export-v13 naivefox-export-v14 naivefox-export-v15 naivefox-export-v16
    naivefox-runtime-f5eb naivefox-safe-archive-20260830.PIsQe2
    naivefox-transition-configure-retry-linux naivefox-transition-evidence
    naivefox-transition-evidence-final naivefox-transition-evidence-old-20260821-1740
    naivefox-transition-evidence-retry naivefox-transition-evidence-s4
    naivefox-transition-export naivefox-transition-export-final naivefox-transition-export-final2
    naivefox-transition-product-archive naivefox-transition-product-archive-022
    naivefox-transition-validation-overlay
  )
  for name in "${names[@]}"; do add_target "$cleanup_base/$name"; done
  # Only the previously inventoried family of obsolete top-level build logs.
  for path in "$cleanup_base"/naivefox-*.log "$cleanup_base"/naivefox-*.strace \
      "$cleanup_base"/naivefox-*.pid "$cleanup_base"/no-*.log \
      "$cleanup_base"/normal-*.log "$cleanup_base"/sccache-*.log \
      "$cleanup_base"/spidermonkey-*.log; do
    [[ -f $path && ! -L $path ]] && add_target "$path"
  done
elif [[ $scope == results ]]; then
  [[ $(realpath -e -- "$carrier_root") == "$carrier_root" && -f $carrier_root/matrix.json ]] || exit 2
  [[ -f $carrier_root/final-pipeline-matrix-h2/analysis.json && -f $carrier_root/final-pipeline-matrix-h3/analysis.json ]] || exit 2
  # These are generated copies, not source repositories. The originals' numeric
  # result/feature/provenance files and the current bin directory remain in place.
  for snapshot in "$carrier_root"/*-evidence; do
    [[ -f $snapshot/manifest.json && -f $snapshot/transport.bundle ]] || continue
    check_target "$snapshot" || exit 2
    if ((apply)); then rm -rf --one-file-system -- "$snapshot"; fi
    printf 'old evidence copy: %s\n' "$snapshot"
  done
  if ((apply)); then
    find "$carrier_root" -xdev -mindepth 1 -type d \( -name fixture -o -name runtime -o -name inner-profile -o -name worker-profile -o -name native-profile \) -prune -exec rm -rf --one-file-system -- {} +
    action=(-delete)
  else
    find "$carrier_root" -xdev -mindepth 1 -type d \( -name fixture -o -name runtime -o -name inner-profile -o -name worker-profile -o -name native-profile \) -prune -print
    action=(-print)
  fi
  find "$carrier_root" -xdev -type f \( -name '*.pcapng' -o -name '*.pcap' -o -name '*.bin' -o -name '*.body' -o -name bridge.json -o -name caddy.json -o -name naive.json -o -name bridge-ready.json -o \( -name '*.log' ! -name dumpcap.log ! -name idle-dumpcap.log ! -name build-closeout.log ! -name closeout-tests.log ! -name cleanup-deletions.log \) \) "${action[@]}"
  if ((apply)); then find "$carrier_root" -xdev -mindepth 1 -type d -empty -delete; fi
  printf 'result cleanup complete; numeric evidence and current binaries retained\n'
  exit 0
else
  for research in /home/zubastik/naivefox-h2-server-research-20260829 /home/zubastik/naivefox-h3-http-research-20260829; do
    [[ $(realpath -e -- "$research") == "$research" && ! -e $research/.git ]] || exit 2
    for name in proxy-timing-profile runtime-padding-count-4 runtime-raw-upstream h2-document-channel.xrZhU5 h2-inner-lifecycle.y9DPZ8 h2-paired-lifecycle.NDTrRK same-base-reference; do
      target=$research/$name
      [[ -d $target && ! -L $target ]] || continue
      [[ $(realpath -e -- "$target") == "$target" && ! -e $target/.git ]] || exit 2
      if ((apply)); then rm -rf --one-file-system -- "$target"; fi
      printf 'obsolete runtime: %s\n' "$target"
    done
    for target in "$research"/*; do
      [[ -f $target && ! -L $target ]] || continue
      case "$target" in
        *.log|*.moz_log|*.strace) ;;
        *)
          case "$(file -b --mime-type -- "$target")" in
            application/x-executable|application/x-pie-executable|application/x-sharedlib|application/vnd.microsoft.portable-executable) ;;
            *) continue ;;
          esac ;;
      esac
      if ((apply)); then rm -f -- "$target"; fi
      printf 'obsolete binary/log: %s\n' "$target"
    done
  done
  exit 0
fi
printf 'scope=%s apply=%s targets=%s\n' "$scope" "$apply" "${#targets[@]}"
for target in "${targets[@]}"; do
  check_target "$target" || { printf 'target changed: %s\n' "$target" >&2; exit 2; }
  if ((apply)); then
    if [[ -L $target ]]; then unlink -- "$target";
    else rm -rf --one-file-system -- "$target"; fi
    printf 'removed %s\n' "$target"
  else
    printf 'would remove %s\n' "$target"
  fi
done
