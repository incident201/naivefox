#!/usr/bin/env bash

set -euo pipefail
umask 077

# shellcheck source=netwerk/naivefox/test/integration/common.sh
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
original_args=("$@")

mode=smoke
protocol_selection=both
inner_transport=https
naivefox_arm=off
naivefox_arm_explicit=0
naivefox_arm_option_count=0
experiment_design=single
multi_arm_arms_csv=off,gate,root
multi_arm_views_csv=all
scenario_override=
scenario_option_count=0
private_h3_keylog=${NAIVEFOX_CAPTURE_PRIVATE_H3_KEYLOG:-0}
diagnostic_naivefox_only=${NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY:-0}
isolated_network=${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0}
isolated_network_entered=${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0}
samples_per_cohort=
seed=
while [[ $# -gt 0 ]]; do
  case $1 in
    --mode)
      mode=${2:-}
      shift 2
      ;;
    --protocol)
      protocol_selection=${2:-}
      shift 2
      ;;
    --inner-transport)
      inner_transport=${2:-}
      shift 2
      ;;
    --naivefox-arm)
      naivefox_arm=${2:-}
      naivefox_arm_explicit=1
      naivefox_arm_option_count=$((naivefox_arm_option_count + 1))
      shift 2
      ;;
    --multi-arm-superblocks)
      experiment_design=multi_arm_superblocks
      shift
      ;;
    --multi-arm-arms)
      multi_arm_arms_csv=${2:-}
      experiment_design=multi_arm_superblocks
      shift 2
      ;;
    --multi-arm-views)
      multi_arm_views_csv=${2:-}
      experiment_design=multi_arm_superblocks
      shift 2
      ;;
    --scenario)
      scenario_override=${2:-}
      scenario_option_count=$((scenario_option_count + 1))
      shift 2
      ;;
    --samples-per-cohort)
      samples_per_cohort=${2:-}
      shift 2
      ;;
    --seed)
      seed=${2:-}
      shift 2
      ;;
    --help)
      printf 'usage: %s [--mode gate|smoke|standard|research] [--protocol h2|h3|both] [--inner-transport http|https] [--scenario NAME] [--naivefox-arm ARM | --multi-arm-superblocks | --multi-arm-arms ARM,...] [--multi-arm-views VIEW,...] [--samples-per-cohort N] [--seed N]\n' "$0"
      exit 0
      ;;
    *)
      printf 'unknown camouflage-suite argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

case $mode in
  gate)
    default_samples=2
    scenarios=(initial browser_page)
    sample_timeout=45
    permutations=0
    refit_bootstrap=0
    max_features=24
    model_iterations=80
    ;;
  smoke)
    default_samples=10
    scenarios=(
      initial browser_page sequential concurrent_4 burst_8
      bulk_download_256k bulk_upload_256k bidirectional_256k idle_5s
    )
    sample_timeout=55
    permutations=0
    refit_bootstrap=0
    max_features=32
    model_iterations=100
    ;;
  standard)
    default_samples=60
    scenarios=(
      initial browser_page sequential concurrent_2 concurrent_4 concurrent_8
      burst_8 bulk_download_256k bulk_download_1m bulk_download_4m
      bulk_upload_256k bulk_upload_1m bidirectional_1m idle_5s idle_30s
    )
    sample_timeout=75
    permutations=19
    refit_bootstrap=0
    max_features=48
    model_iterations=120
    ;;
  research)
    default_samples=240
    scenarios=(
      initial browser_page sequential concurrent_2 concurrent_4 concurrent_8
      concurrent_16 burst_8 bulk_download_256k bulk_download_1m
      bulk_download_4m bulk_download_16m bulk_upload_256k bulk_upload_1m
      bulk_upload_4m bidirectional_1m bidirectional_4m idle_5s idle_30s idle_120s
    )
    sample_timeout=180
    permutations=99
    refit_bootstrap=99
    max_features=64
    model_iterations=160
    ;;
  *)
    printf 'unsupported camouflage mode: %s\n' "$mode" >&2
    exit 2
    ;;
esac
if [[ -n $scenario_override ]]; then
  case $scenario_override in
    initial | browser_page | sequential | concurrent_2 | concurrent_4 | concurrent_8 | concurrent_16 | burst_8 | bulk_download_256k | bulk_download_1m | bulk_download_4m | bulk_download_16m | bulk_upload_256k | bulk_upload_1m | bulk_upload_4m | bidirectional_256k | bidirectional_1m | bidirectional_4m | idle_5s | idle_30s | idle_120s) ;;
    *)
      printf 'unsupported camouflage scenario: %s\n' "$scenario_override" >&2
      exit 2
      ;;
  esac
  scenarios=("$scenario_override")
fi
case $protocol_selection in
  h2) protocols=(h2) ;;
  h3) protocols=(h3) ;;
  both) protocols=(h2 h3) ;;
  *)
    printf 'unsupported protocol selection: %s\n' "$protocol_selection" >&2
    exit 2
    ;;
esac
case $inner_transport in
  http | https) ;;
  *)
    printf 'unsupported inner transport: %s\n' "$inner_transport" >&2
    exit 2
    ;;
esac
case $private_h3_keylog in
  0 | 1) ;;
  *)
    printf 'NAIVEFOX_CAPTURE_PRIVATE_H3_KEYLOG must be 0 or 1\n' >&2
    exit 2
    ;;
esac
case $diagnostic_naivefox_only in
  0 | 1) ;;
  *)
    printf 'NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY must be 0 or 1\n' >&2
    exit 2
    ;;
esac
case $isolated_network in
  0 | 1) ;;
  *)
    printf 'NAIVEFOX_CAPTURE_ISOLATED_NETWORK must be 0 or 1\n' >&2
    exit 2
    ;;
esac
case $isolated_network_entered in
  0 | 1) ;;
  *)
    printf 'invalid internal isolated-network marker\n' >&2
    exit 2
    ;;
esac
if [[ $isolated_network_entered == 1 && $isolated_network != 1 ]]; then
  printf 'isolated-network marker requires isolated-network mode\n' >&2
  exit 2
fi
if [[ $private_h3_keylog == 1 && $mode != gate && $mode != smoke ]]; then
  printf 'private H3 key logging is restricted to gate/smoke diagnostics\n' >&2
  exit 2
fi
case $naivefox_arm in
  off | gate | root | root-pmtud-control | document-complete | document-carrier-dispatch | document-cold-winner-handoff | document-native-cache-open | document-native-channel-open | document-handshake-confirmed | document-overlap | document-start-overlap | tree-complete | tree-complete-css | tree-early-overlap | tree-root-overlap | tree-root-overlap-css | tree-resource-committed-overlap-css | tree-resource-native-cache-committed-overlap | tree-native-parser-preload-overlap-css | tree-native-parser-document-handoff-overlap-css | tree-native-parser-retarget-overlap-css | tree-native-parser-ipc-rendezvous-overlap-css | tree-native-parser-root-rendezvous-overlap-css | tree-native-parser-process-overlap-css | tree-warm-css-304 | tree-overlap) ;;
  *)
    printf 'unsupported NaiveFox arm: %s\n' "$naivefox_arm" >&2
    exit 2
    ;;
esac
if [[ $naivefox_arm == root-pmtud-control && $protocol_selection != h3 ]]; then
  printf 'root-pmtud-control requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == document-handshake-confirmed &&
      $protocol_selection != h3 ]]; then
  printf 'document-handshake-confirmed requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == document-carrier-dispatch &&
      $protocol_selection != h3 ]]; then
  printf 'document-carrier-dispatch requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == document-cold-winner-handoff &&
      $protocol_selection != h3 ]]; then
  printf 'document-cold-winner-handoff requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == document-native-cache-open &&
      $protocol_selection != h3 ]]; then
  printf 'document-native-cache-open requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == document-native-channel-open &&
      $protocol_selection != h3 ]]; then
  printf 'document-native-channel-open requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-resource-committed-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-resource-committed-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-resource-native-cache-committed-overlap &&
      $protocol_selection != h3 ]]; then
  printf 'tree-resource-native-cache-committed-overlap requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-native-parser-preload-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-native-parser-preload-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-native-parser-document-handoff-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-native-parser-document-handoff-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-native-parser-retarget-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-native-parser-retarget-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-native-parser-ipc-rendezvous-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-native-parser-ipc-rendezvous-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-native-parser-root-rendezvous-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-native-parser-root-rendezvous-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $naivefox_arm == tree-native-parser-process-overlap-css &&
      $protocol_selection != h3 ]]; then
  printf 'tree-native-parser-process-overlap-css requires --protocol h3\n' >&2
  exit 2
fi
if [[ $experiment_design == multi_arm_superblocks && $naivefox_arm_explicit -eq 1 ]]; then
  printf '%s\n' '--naivefox-arm cannot be combined with a multi-arm design' >&2
  exit 2
fi
if [[ $experiment_design == multi_arm_superblocks ]]; then
  IFS=, read -r -a multi_arm_arms <<<"$multi_arm_arms_csv"
  if [[ ${#multi_arm_arms[@]} -lt 2 ]]; then
    printf 'multi-arm screening requires at least two arms\n' >&2
    exit 2
  fi
  declare -A seen_multi_arms=()
  for arm in "${multi_arm_arms[@]}"; do
    case $arm in
      off | gate | root | root-pmtud-control | document-complete | document-carrier-dispatch | document-cold-winner-handoff | document-native-cache-open | document-native-channel-open | document-handshake-confirmed | document-overlap | document-start-overlap | tree-complete | tree-complete-css | tree-early-overlap | tree-root-overlap | tree-root-overlap-css | tree-resource-committed-overlap-css | tree-resource-native-cache-committed-overlap | tree-native-parser-preload-overlap-css | tree-native-parser-document-handoff-overlap-css | tree-native-parser-retarget-overlap-css | tree-native-parser-ipc-rendezvous-overlap-css | tree-native-parser-root-rendezvous-overlap-css | tree-native-parser-process-overlap-css | tree-warm-css-304 | tree-overlap) ;;
      *)
        printf 'unsupported multi-arm NaiveFox arm: %s\n' "$arm" >&2
        exit 2
        ;;
    esac
    if [[ -n ${seen_multi_arms[$arm]:-} ]]; then
      printf 'duplicate multi-arm NaiveFox arm: %s\n' "$arm" >&2
      exit 2
    fi
    seen_multi_arms[$arm]=1
  done
  if [[ -n ${seen_multi_arms[tree-warm-css-304]:-} ]]; then
    printf 'tree-warm-css-304 cannot share cold superblock references\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[root]:-} &&
        -n ${seen_multi_arms[document-complete]:-} ]]; then
    printf 'root and document-complete are aliases; select only one\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[root-pmtud-control]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'root-pmtud-control multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[document-handshake-confirmed]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'document-handshake-confirmed multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[document-carrier-dispatch]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'document-carrier-dispatch multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[document-cold-winner-handoff]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'document-cold-winner-handoff multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[document-native-cache-open]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'document-native-cache-open multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[document-native-channel-open]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'document-native-channel-open multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-preload-overlap-css]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'tree-native-parser-preload-overlap-css multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-document-handoff-overlap-css]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'tree-native-parser-document-handoff-overlap-css multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-retarget-overlap-css]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'tree-native-parser-retarget-overlap-css multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-ipc-rendezvous-overlap-css]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'tree-native-parser-ipc-rendezvous-overlap-css multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-root-rendezvous-overlap-css]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'tree-native-parser-root-rendezvous-overlap-css multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-process-overlap-css]:-} &&
        $protocol_selection != h3 ]]; then
    printf 'tree-native-parser-process-overlap-css multi-arm screening requires --protocol h3\n' >&2
    exit 2
  fi
  if [[ -n ${seen_multi_arms[tree-native-parser-process-overlap-css]:-} &&
        -z ${seen_multi_arms[tree-native-parser-root-rendezvous-overlap-css]:-} ]]; then
    printf 'tree-native-parser-process-overlap-css multi-arm screening requires the tree-native-parser-root-rendezvous-overlap-css control\n' >&2
    exit 2
  fi
  if [[ $multi_arm_views_csv != all ]]; then
    IFS=, read -r -a multi_arm_views <<<"$multi_arm_views_csv"
    if [[ ${#multi_arm_views[@]} -eq 0 ]]; then
      printf 'multi-arm feature view list cannot be empty\n' >&2
      exit 2
    fi
    declare -A seen_multi_views=()
    for view in "${multi_arm_views[@]}"; do
      case $view in
        whole | initial_packets_16 | packets_17_32 | initial_packets_32 | initial_packets_64 | initial_packets_128 | initial_time_250ms | initial_time_500ms | initial_time_1000ms | initial_time_2000ms | steady_after_32 | steady_after_2000ms | lifecycle) ;;
        *)
          printf 'unsupported multi-arm feature view: %s\n' "$view" >&2
          exit 2
          ;;
      esac
      if [[ -n ${seen_multi_views[$view]:-} ]]; then
        printf 'duplicate multi-arm feature view: %s\n' "$view" >&2
        exit 2
      fi
      seen_multi_views[$view]=1
    done
  fi
fi
if [[ $naivefox_arm == tree-warm-css-304 ]]; then
  if [[ $experiment_design != single || $protocol_selection != h3 ]]; then
    printf 'tree-warm-css-304 requires a single-arm H3 run\n' >&2
    exit 2
  fi
  if [[ $scenario_override != browser_page ]]; then
    printf 'tree-warm-css-304 requires --scenario browser_page\n' >&2
    exit 2
  fi
  if [[ $mode != gate && $mode != smoke ]]; then
    printf 'tree-warm-css-304 is restricted to diagnostic gate/smoke runs\n' >&2
    exit 2
  fi
  if [[ $private_h3_keylog == 1 ]]; then
    printf 'tree-warm-css-304 does not admit private decrypted captures\n' >&2
    exit 2
  fi
  if [[ $diagnostic_naivefox_only == 1 ]]; then
    printf 'tree-warm-css-304 requires its condition-specific Firefox A/B controls\n' >&2
    exit 2
  fi
fi
if [[ $diagnostic_naivefox_only == 1 ]]; then
  if [[ $mode != gate && $mode != smoke ]]; then
    printf 'NaiveFox-only diagnostics are restricted to gate/smoke mode\n' >&2
    exit 2
  fi
  if [[ $scenario_option_count -ne 1 ]]; then
    printf 'NaiveFox-only diagnostics require exactly one --scenario\n' >&2
    exit 2
  fi
  if [[ $naivefox_arm_option_count -ne 1 ]]; then
    printf 'NaiveFox-only diagnostics require exactly one --naivefox-arm\n' >&2
    exit 2
  fi
  if [[ $experiment_design != single ]]; then
    printf 'NaiveFox-only diagnostics cannot use a multi-arm design\n' >&2
    exit 2
  fi
fi
if [[ -z $samples_per_cohort ]]; then
  samples_per_cohort=$default_samples
fi
if [[ ! $samples_per_cohort =~ ^[1-9][0-9]*$ ]]; then
  printf 'samples per cohort must be a positive integer\n' >&2
  exit 2
fi
if [[ $mode == research && $samples_per_cohort -lt 240 ]]; then
  printf 'research mode requires at least 240 samples per cohort\n' >&2
  exit 2
fi
if [[ -z $seed ]]; then
  seed=$(python3 -c 'import secrets; print(secrets.randbits(31))')
fi
if [[ ! $seed =~ ^[0-9]+$ ]]; then
  printf 'seed must be a non-negative integer\n' >&2
  exit 2
fi

for tool in dumpcap tshark curl getcap openssl python3 readelf rg setsid sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required camouflage tool not found: %s\n' "$tool" >&2
    exit 1
  }
done
browser_python=${NAIVEFOX_CAMOUFLAGE_PYTHON:-}
if [[ -z $browser_python && -x "$OBJDIR/camouflage-venv/bin/python" ]]; then
  browser_python="$OBJDIR/camouflage-venv/bin/python"
fi
browser_python=${browser_python:-$(command -v python3)}
browser_backend=${NAIVEFOX_CAMOUFLAGE_BROWSER_BACKEND:-auto}
case $browser_backend in
  auto | selenium | commandline) ;;
  *)
    printf 'unknown camouflage browser backend: %s\n' "$browser_backend" >&2
    exit 2
    ;;
esac
if [[ $experiment_design == multi_arm_superblocks &&
      $protocol_selection == h3 ]]; then
  [[ $browser_backend != commandline ]] || {
    printf 'H3 multi-arm screening requires a pre-launched Selenium browser\n' >&2
    exit 2
  }
  "$browser_python" -c 'import selenium' || {
    printf 'H3 multi-arm screening requires Selenium\n' >&2
    exit 1
  }
fi
dumpcap_path=$(command -v dumpcap)
dumpcap_caps=$(getcap "$dumpcap_path" 2>/dev/null || true)
if [[ $EUID -ne 0 ]] &&
   [[ $dumpcap_caps != *cap_net_admin* || $dumpcap_caps != *cap_net_raw* ]]; then
  printf '%s needs cap_net_raw and cap_net_admin\n' "$dumpcap_path" >&2
  exit 1
fi

BIN="$OBJDIR/dist/bin"
capture_mode=${NAIVEFOX_CAPTURE_MODE:-quick}
case $capture_mode in
  quick)
    if [[ -n ${NAIVEFOX_CAPTURE_REFERENCE_BIN:-} ||
          -n ${NAIVEFOX_CAPTURE_REFERENCE_OBJDIR:-} ]]; then
      printf 'reference overrides require NAIVEFOX_CAPTURE_MODE=same-base\n' >&2
      exit 2
    fi
    REFERENCE_ROOT=$("$INTEGRATION_DIR/../../tools/fetch-firefox-reference.sh")
    REFERENCE_BIN="$REFERENCE_ROOT/firefox"
    REFERENCE_LIBDIR="$REFERENCE_ROOT"
    ;;
  same-base)
    if [[ -z ${NAIVEFOX_CAPTURE_REFERENCE_BIN:-} ||
          -z ${NAIVEFOX_CAPTURE_REFERENCE_OBJDIR:-} ]]; then
      printf 'same-base mode requires NAIVEFOX_CAPTURE_REFERENCE_BIN and _OBJDIR\n' >&2
      exit 2
    fi
    REFERENCE_BIN="$NAIVEFOX_CAPTURE_REFERENCE_BIN"
    REFERENCE_LIBDIR="${NAIVEFOX_CAPTURE_REFERENCE_LIBDIR:-$(dirname "$REFERENCE_BIN")}"
    ;;
  *)
    printf 'unknown NAIVEFOX_CAPTURE_MODE: %s\n' "$capture_mode" >&2
    exit 2
    ;;
esac
if [[ $experiment_design == multi_arm_superblocks && $capture_mode != same-base ]]; then
  printf '%s\n' '--multi-arm-superblocks requires NAIVEFOX_CAPTURE_MODE=same-base' >&2
  exit 2
fi
NAIVEFOX_BIN="${NAIVEFOX_CAPTURE_NAIVEFOX_BIN:-$BIN/naivefox}"
NAIVEFOX_LIBDIR="${NAIVEFOX_CAPTURE_NAIVEFOX_LIBDIR:-$BIN}"
for artifact in "$REFERENCE_BIN" "$REFERENCE_LIBDIR/libssl3.so" \
                "$REFERENCE_LIBDIR/libxul.so" "$NAIVEFOX_BIN" \
                "$NAIVEFOX_LIBDIR/libssl3.so" "$NAIVEFOX_LIBDIR/libxul.so"; do
  [[ -f $artifact ]] || {
    printf 'required camouflage artifact is missing: %s\n' "$artifact" >&2
    printf 'set NAIVEFOX_OBJDIR to a completed Linux NaiveFox object directory\n' >&2
    exit 1
  }
done

if [[ $isolated_network == 1 && $isolated_network_entered == 0 ]]; then
  if [[ $capture_mode != same-base ]]; then
    printf 'isolated-network capture requires same-base mode\n' >&2
    exit 2
  fi
  for tool in unshare ip ethtool; do
    command -v "$tool" >/dev/null 2>&1 || {
      printf 'isolated-network capture requires %s\n' "$tool" >&2
      exit 1
    }
  done
  exec unshare --net --mount-proc \
    "$INTEGRATION_DIR/run-camouflage-isolated-network.sh" \
    "$0" "${original_args[@]}"
fi

run_id=$(openssl rand -hex 8)
private_dir="$STATE_ROOT/camouflage-captures/$run_id"
safe_dir="$STATE_ROOT/camouflage-safe/$run_id"
feature_fragments="$private_dir/features"
sensitive_values="$private_dir/sensitive-values.txt"
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-camouflage.XXXXXX")
mkdir -p "$private_dir" "$feature_fragments" "$safe_dir"
: >"$sensitive_values"
chmod 0700 "$private_dir" "$feature_fragments" "$safe_dir"
chmod 0700 "$capture_stage_dir"

capture_pid=
capture_stage_pcap=
capture_pcap=
capture_log=
browser_controller_pid=
browser_stop_file=
browser_shutdown_file=
naivefox_pid=
network_monitor_pid=
network_monitor_events=
network_monitor_ready=
network_monitor_done=
network_mutation_validated_samples=0
cache_validated_participants=0
controller_backends="$private_dir/controller-backends.txt"
cache_semantics_records="$private_dir/cache-semantics.txt"
: >"$cache_semantics_records"
proxy_restart_records="$private_dir/proxy-restarts.txt"
: >"$proxy_restart_records"
proxy_restart_count=0
fixture_caddy_child_pid=
success=0

stop_pid() {
  local pid=${1:-}
  if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  [[ -z $pid ]] || wait "$pid" 2>/dev/null || true
}

stop_pid_clean() {
  local pid=${1:-}
  local log=${2:-}
  [[ -n $pid ]] || return 0
  for ((i = 0; i < 100; i++)); do
    local process_state=gone
    if [[ -r /proc/$pid/stat ]]; then
      read -r _ _ process_state _ <"/proc/$pid/stat"
    fi
    if [[ $process_state == gone || $process_state == Z ]]; then
      local status=0
      wait "$pid" 2>/dev/null || status=$?
      if [[ $status -ne 0 ]]; then
        printf 'warm NaiveFox exited with status %s\n' "$status" >&2
        return 1
      fi
      rg -q '^NaiveFox completed successfully$' "$log" || {
        printf 'warm NaiveFox lacks successful completion evidence\n' >&2
        return 1
      }
      return 0
    fi
    sleep 0.1
  done
  printf 'bounded warm NaiveFox did not exit after its tunnel drained\n' >&2
  return 1
}

stop_process_group() {
  local pid=${1:-}
  [[ -n $pid ]] || return 0
  if kill -0 -- "-$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      kill -0 -- "-$pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 -- "-$pid" 2>/dev/null; then
      kill -KILL -- "-$pid" 2>/dev/null || true
    fi
  fi
  wait "$pid" 2>/dev/null || true
}

start_network_mutation_monitor() {
  local sample_dir=$1
  network_monitor_events="$sample_dir/network-mutations.log"
  network_monitor_ready="$sample_dir/network-monitor-ready"
  network_monitor_done="$sample_dir/network-monitor-done"
  python3 "$INTEGRATION_DIR/monitor-network-mutations.py" \
    --ready "$network_monitor_ready" --events "$network_monitor_events" \
    --done "$network_monitor_done" &
  network_monitor_pid=$!
  wait_for_file "$network_monitor_ready" "$network_monitor_pid" \
    'network mutation monitor' 100
}

stop_network_mutation_monitor() {
  [[ -n $network_monitor_pid ]] || return 0
  local monitor_status=0
  if kill -0 "$network_monitor_pid" 2>/dev/null; then
    kill -TERM "$network_monitor_pid" 2>/dev/null || true
  else
    wait "$network_monitor_pid" 2>/dev/null || true
    network_monitor_pid=
    printf 'network mutation monitor stopped before the sample was complete\n' >&2
    return 1
  fi
  if wait "$network_monitor_pid" 2>/dev/null; then
    monitor_status=0
  else
    monitor_status=$?
  fi
  network_monitor_pid=
  network_monitor_ready=
  if [[ $monitor_status -ne 0 ]]; then
    printf 'network mutation monitor exited unexpectedly (status %s)\n' \
      "$monitor_status" >&2
    return 1
  fi
  if [[ ! -f $network_monitor_done ]]; then
    printf 'network mutation monitor did not confirm a drained stop\n' >&2
    return 1
  fi
  network_monitor_done=
  if [[ -s $network_monitor_events ]]; then
    printf 'network route/address/link mutation invalidated the sample\n' >&2
    return 1
  fi
  network_monitor_events=
  network_mutation_validated_samples=$((network_mutation_validated_samples + 1))
}

stop_capture() {
  [[ -n $capture_pid ]] || return 0
  local was_running=0
  local status=0
  if [[ -n $capture_pid ]] && kill -0 "$capture_pid" 2>/dev/null; then
    was_running=1
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  [[ -z $capture_pid ]] || wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  if [[ $was_running -ne 1 ]]; then
    printf 'dumpcap stopped before the workload capture was complete\n' >&2
    status=1
  fi
  if ! python3 "$INTEGRATION_DIR/camouflage_capture_health.py" "$capture_log"; then
    status=1
  fi
  if [[ -n $capture_stage_pcap && -s $capture_stage_pcap ]]; then
    if [[ -n $(tshark -r "$capture_stage_pcap" -Y 'sll.pkttype==4' \
      -T fields -e frame.number 2>/dev/null | sed -n '1p') ]]; then
      tshark -r "$capture_stage_pcap" -Y 'sll.pkttype==4' -w "$capture_pcap" \
        >/dev/null 2>&1
      rm -f -- "$capture_stage_pcap"
    else
      mv -f -- "$capture_stage_pcap" "$capture_pcap"
    fi
  fi
  capture_stage_pcap=
  capture_pcap=
  capture_log=
  return "$status"
}

cleanup() {
  local status=$?
  stop_capture || true
  if ! stop_network_mutation_monitor; then
    status=1
  fi
  [[ -z $browser_stop_file ]] || : >"$browser_stop_file"
  stop_process_group "$browser_controller_pid"
  stop_pid "$naivefox_pid"
  stop_pid "$fixture_caddy_child_pid"
  fixture_caddy_child_pid=
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  rm -rf -- "$capture_stage_dir"
  if [[ $status -eq 0 && $success -eq 1 ]]; then
    case $private_dir in
      "$STATE_ROOT"/camouflage-captures/*) rm -rf -- "$private_dir" ;;
      *) printf 'refusing unexpected private capture path: %s\n' "$private_dir" >&2 ;;
    esac
  else
    case $safe_dir in
      "$STATE_ROOT"/camouflage-safe/*) rm -rf -- "$safe_dir" ;;
    esac
    printf 'camouflage suite failed; private diagnostics preserved at %s\n' \
      "$private_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export MOZ_CRASHREPORTER_DISABLE=1
firefox_runtime_env=()
if [[ $EUID -eq 0 ]]; then
  firefox_runtime_dir="$capture_stage_dir/firefox-runtime"
  mkdir -m 0700 "$firefox_runtime_dir"
  firefox_runtime_env=("XDG_RUNTIME_DIR=$firefox_runtime_dir")
fi

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

restart_fixture_proxy() {
  local phase=$1
  local pid_file="$NAIVEFOX_FIXTURE_RUN_DIR/caddy.pid"
  local old_pid
  local old_status=not_child
  local old_starttime
  local current_starttime
  local process_state
  local actual_executable
  local expected_executable
  local caddy_log="$NAIVEFOX_FIXTURE_RUN_DIR/caddy.log"
  local log_offset
  local target_pid
  local target_starttime
  local target_executable
  local current_target_starttime
  local current_target_executable
  local journal_identity
  local journal_size
  local current_journal_identity
  local current_journal_size
  [[ $NAIVEFOX_FIXTURE_MODE == h3 && -r $pid_file ]] || {
    printf 'proxy restart requires an active H3 fixture\n' >&2
    return 1
  }
  old_pid=$(<"$pid_file")
  [[ $old_pid =~ ^[1-9][0-9]*$ ]] || {
    printf 'proxy restart found an invalid Caddy pid\n' >&2
    return 1
  }
  actual_executable=$(readlink -f "/proc/$old_pid/exe" 2>/dev/null || true)
  expected_executable=$(readlink -f "$CADDY_BIN")
  if [[ $actual_executable != "$expected_executable" ]]; then
    printf 'proxy restart pid does not identify the fixture Caddy binary\n' >&2
    return 1
  fi
  old_starttime=$(awk '{print $22}' "/proc/$old_pid/stat")
  target_pid=$(<"$NAIVEFOX_FIXTURE_RUN_DIR/target.pid")
  target_starttime=$(awk '{print $22}' "/proc/$target_pid/stat" 2>/dev/null || true)
  target_executable=$(readlink -f "/proc/$target_pid/exe" 2>/dev/null || true)
  if [[ -z $target_starttime || -z $target_executable ]]; then
    printf 'proxy restart found no exact target process identity\n' >&2
    return 1
  fi
  journal_identity=absent
  journal_size=0
  if [[ -e $NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL ]]; then
    journal_identity=$(stat -c '%d:%i' "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL")
    journal_size=$(stat -c %s "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL")
  fi
  kill -TERM "$old_pid" 2>/dev/null || {
    printf 'proxy restart could not stop Caddy\n' >&2
    return 1
  }
  for ((i = 0; i < 100; i++)); do
    process_state=gone
    if [[ -r /proc/$old_pid/stat ]]; then
      read -r _ _ process_state _ <"/proc/$old_pid/stat"
      current_starttime=$(awk '{print $22}' "/proc/$old_pid/stat")
      if [[ $current_starttime != "$old_starttime" ]]; then
        process_state=gone
      fi
    fi
    if [[ $process_state == gone || $process_state == Z ]]; then
      break
    fi
    sleep 0.1
  done
  if [[ $process_state != gone && $process_state != Z ]]; then
    printf 'proxy restart timed out waiting for Caddy shutdown\n' >&2
    return 1
  fi
  if [[ $old_pid == "$fixture_caddy_child_pid" ]]; then
    old_status=0
    wait "$old_pid" 2>/dev/null || old_status=$?
    fixture_caddy_child_pid=
  fi
  if [[ -n $(ss -H -lun "sport = :$NAIVEFOX_FIXTURE_PROXY_PORT") ]]; then
    printf 'proxy restart found the old UDP listener after Caddy shutdown\n' >&2
    return 1
  fi
  log_offset=$(stat -c %s "$caddy_log")
  env XDG_DATA_HOME="$NAIVEFOX_FIXTURE_RUN_DIR/xdg-data" \
    XDG_CONFIG_HOME="$NAIVEFOX_FIXTURE_RUN_DIR/xdg-config" \
    "$CADDY_BIN" run --config "$NAIVEFOX_FIXTURE_RUN_DIR/adapted.json" \
    >>"$caddy_log" 2>&1 &
  local new_pid=$!
  fixture_caddy_child_pid=$new_pid
  printf '%s\n' "$new_pid" >"$pid_file.tmp"
  mv -f -- "$pid_file.tmp" "$pid_file"
  if [[ $new_pid == "$old_pid" ]]; then
    printf 'proxy restart reused the old process id\n' >&2
    return 1
  fi
  local ready=0
  for ((i = 0; i < 150; i++)); do
    kill -0 "$new_pid" 2>/dev/null || {
      printf 'restarted Caddy exited before H3 readiness\n' >&2
      return 1
    }
    if [[ -n $(ss -H -lun "sport = :$NAIVEFOX_FIXTURE_PROXY_PORT") &&
          -z $(ss -H -ltn "sport = :$NAIVEFOX_FIXTURE_PROXY_PORT") ]] &&
       tail -c "+$((log_offset + 1))" "$caddy_log" |
         rg -q 'enabling HTTP/3 listener'; then
      ready=1
      break
    fi
    sleep 0.1
  done
  if [[ $ready -ne 1 ]]; then
    printf 'restarted Caddy did not publish fresh H3 readiness\n' >&2
    return 1
  fi
  current_journal_identity=absent
  current_journal_size=0
  if [[ -e $NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL ]]; then
    current_journal_identity=$(stat -c '%d:%i' \
      "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL")
    current_journal_size=$(stat -c %s \
      "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL")
  fi
  current_target_starttime=$(awk '{print $22}' "/proc/$target_pid/stat" \
    2>/dev/null || true)
  current_target_executable=$(readlink -f "/proc/$target_pid/exe" \
    2>/dev/null || true)
  if [[ $(<"$NAIVEFOX_FIXTURE_RUN_DIR/target.pid") != "$target_pid" ]] ||
     ! kill -0 "$target_pid" 2>/dev/null ||
     [[ $current_target_starttime != "$target_starttime" ]] ||
     [[ $current_target_executable != "$target_executable" ]] ||
     [[ $current_journal_identity != "$journal_identity" ]] ||
     [[ $current_journal_size != "$journal_size" ]]; then
    printf 'proxy restart changed target or request-journal identity\n' >&2
    return 1
  fi
  proxy_restart_count=$((proxy_restart_count + 1))
  printf '%s\t%s\told_pid=%s\told_status=%s\tnew_pid=%s\tport=%s\tconfig_sha256=%s\n' \
    "$proxy_restart_count" "$phase" "$old_pid" "$old_status" "$new_pid" \
    "$NAIVEFOX_FIXTURE_PROXY_PORT" \
    "$(sha256sum "$NAIVEFOX_FIXTURE_RUN_DIR/adapted.json" | cut -d' ' -f1)" \
    >>"$proxy_restart_records"
}

start_capture() {
  local destination=$1
  local log=$2
  capture_pcap=$destination
  capture_stage_pcap="$capture_stage_dir/$(basename "$(dirname "$destination")").raw.pcapng"
  capture_log=$log
  : >"$log"
  dumpcap -q -i any \
    -f "tcp port $NAIVEFOX_FIXTURE_PROXY_PORT or udp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a "duration:$sample_timeout" -a filesize:131072 \
    -w "$capture_stage_pcap" >"$log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      printf 'dumpcap exited before camouflage capture readiness\n' >&2
      return 1
    }
    if rg -q '^Capturing on ' "$log" && rg -q '^File: ' "$log"; then
      return 0
    fi
    sleep 0.1
  done
  printf 'timed out waiting for dumpcap readiness marker\n' >&2
  return 1
}

wait_for_log() {
  local pid=$1
  local log=$2
  local pattern=$3
  for ((i = 0; i < 150; i++)); do
    rg -q "$pattern" "$log" && return 0
    kill -0 "$pid" 2>/dev/null || {
      printf 'process exited before readiness marker: %s\n' "$pattern" >&2
      return 1
    }
    sleep 0.1
  done
  printf 'timed out waiting for readiness marker: %s\n' "$pattern" >&2
  return 1
}

wait_for_cache_log() {
  wait_for_log "$@"
}

validate_no_tls_token_persistence() {
  local profile=$1
  if find "$profile" -maxdepth 1 -type f -name 'ssl_tokens_cache*' \
       -print -quit | rg -q .; then
    printf 'warm-cache profile persisted TLS resumption tokens\n' >&2
    return 1
  fi
}

validate_native_channel_fresh_cache() {
  local profile=$1
  local participant=$2
  if [[ -e $profile/cache2 || -L $profile/cache2 ]]; then
    printf 'document-native-channel-open %s profile is not cache-cold: cache2 exists\n' \
      "$participant" >&2
    return 1
  fi
}

validate_profile_role() {
  local destination=$1
  local protocol=$2
  local participant=$3
  local arm=${4:-}
  local mapping_pref='network.http.http3.alt-svc-mapping-for-testing'
  local force_pref='network.http.http3.force-use-alt-svc-mapping-for-testing'
  local pmtud_pref='network.http.http3.pmtud'
  local expect_test_mapping=false
  [[ $participant == reference && $protocol == h3 ]] && expect_test_mapping=true

  if [[ -e $destination/AlternateServices.bin ]]; then
    printf 'fresh %s profile unexpectedly contains AlternateServices.bin\n' \
      "$participant" >&2
    return 1
  fi
  if [[ $expect_test_mapping == true ]]; then
    rg -q -F "$mapping_pref" "$destination/user.js" &&
      rg -q -F "$force_pref" "$destination/user.js" || {
        printf 'direct H3 reference profile lacks its test Alt-Svc mapping\n' >&2
        return 1
      }
  elif find "$destination" -maxdepth 1 -type f \
       \( -name user.js -o -name prefs.js \) \
       -exec rg -q -F -e "$mapping_pref" -e "$force_pref" {} +; then
    printf '%s profile is contaminated by a test Alt-Svc mapping\n' \
      "$participant" >&2
    return 1
  fi

  if [[ $participant == naivefox && $protocol == h3 ]]; then
    rg -q -F 'user_pref("network.http.http3.enable", true);' \
      "$destination/user.js" || {
        printf 'NaiveFox H3 profile does not enable the real H3 stack\n' >&2
        return 1
      }
  fi
  if [[ $participant == naivefox && $protocol == h3 &&
        $arm == root-pmtud-control ]]; then
    rg -q -F 'user_pref("network.http.http3.pmtud", true);' \
      "$destination/user.js" || {
        printf 'PMTUD control profile does not enable the global H3 preference\n' >&2
        return 1
      }
  elif find "$destination" -maxdepth 1 -type f \
       \( -name user.js -o -name prefs.js \) \
       -exec rg -q -F "$pmtud_pref" {} +; then
    printf '%s profile unexpectedly overrides the global H3 PMTUD preference\n' \
      "$participant" >&2
    return 1
  fi
}

make_profile() {
  local destination=$1
  local protocol=$2
  local participant=$3
  local socks_port=${4:-}
  local arm=${5:-}
  local direct_h3=false
  local enable_h3=false
  case $participant in
    reference)
      [[ -z $socks_port ]] || {
        printf 'reference profile must not receive a SOCKS port\n' >&2
        return 2
      }
      [[ $protocol == h3 ]] && direct_h3=true
      enable_h3=$direct_h3
      ;;
    naivefox)
      [[ -z $socks_port ]] || {
        printf 'NaiveFox profile must not receive a SOCKS port\n' >&2
        return 2
      }
      [[ $protocol == h3 ]] && enable_h3=true
      ;;
    socks-browser)
      [[ -n $socks_port ]] || {
        printf 'SOCKS browser profile requires a SOCKS port\n' >&2
        return 2
      }
      ;;
    *)
      printf 'unknown profile participant: %s\n' "$participant" >&2
      return 2
      ;;
  esac
  mkdir -m 0700 "$destination"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$destination/"
  cat >"$destination/user.js" <<EOF
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
user_pref("network.prefetch-next", false);
user_pref("network.http.speculative-parallel-limit", 0);
user_pref("network.http.http3.enable", $enable_h3);
EOF
  if [[ $participant == reference || $participant == naivefox ]]; then
    cat >>"$destination/user.js" <<'EOF'
user_pref("browser.safebrowsing.realTime.enabled", false);
user_pref("browser.safebrowsing.globalCache.enabled", false);
user_pref("browser.safebrowsing.provider.google5.enabled", false);
EOF
  fi
  if [[ $arm == tree-warm-css-304 &&
        ( $participant == reference || $participant == naivefox ) ]]; then
    cat >>"$destination/user.js" <<'EOF'
user_pref("network.ssl_tokens_cache_persistence", false);
user_pref("network.http.http3.enable_0rtt", false);
user_pref("security.tls.enable_0rtt_data", false);
EOF
  fi
  if [[ $participant == socks-browser ]]; then
    "$browser_python" \
      "$INTEGRATION_DIR/camouflage_browser_controller.py" \
      --generate-pac-user-js "$socks_port" >>"$destination/user.js"
  fi
  if [[ $protocol == h3 && $participant != socks-browser ]]; then
    cat >>"$destination/user.js" <<'EOF'
user_pref("network.http.http3.disable_when_third_party_roots_found", false);
EOF
  fi
  if [[ $participant == naivefox && $arm == root-pmtud-control ]]; then
    cat >>"$destination/user.js" <<'EOF'
user_pref("network.http.http3.pmtud", true);
EOF
  fi
  if [[ $direct_h3 == true ]]; then
    cat >>"$destination/user.js" <<EOF
user_pref("network.http.http3.alt-svc-mapping-for-testing", "localhost;h3=:$NAIVEFOX_FIXTURE_PROXY_PORT");
user_pref("network.http.http3.force-use-alt-svc-mapping-for-testing", true);
EOF
  fi
  chmod 0600 "$destination/user.js"
  validate_profile_role "$destination" "$protocol" "$participant" "$arm"
}

scenario_parameters() {
  local scenario=$1
  scenario_kind=$scenario
  scenario_count=1
  scenario_size=262144
  scenario_idle_ms=0
  case $scenario in
    concurrent_*) scenario_kind=concurrent; scenario_count=${scenario#concurrent_} ;;
    burst_*) scenario_kind=burst; scenario_count=${scenario#burst_} ;;
    bulk_download_256k) scenario_kind=bulk_download; scenario_size=262144 ;;
    bulk_download_1m) scenario_kind=bulk_download; scenario_size=1048576 ;;
    bulk_download_4m) scenario_kind=bulk_download; scenario_size=4194304 ;;
    bulk_download_16m) scenario_kind=bulk_download; scenario_size=16777216 ;;
    bulk_upload_256k) scenario_kind=bulk_upload; scenario_size=262144 ;;
    bulk_upload_1m) scenario_kind=bulk_upload; scenario_size=1048576 ;;
    bulk_upload_4m) scenario_kind=bulk_upload; scenario_size=4194304 ;;
    bidirectional_256k) scenario_kind=bidirectional; scenario_size=262144 ;;
    bidirectional_1m) scenario_kind=bidirectional; scenario_size=1048576 ;;
    bidirectional_4m) scenario_kind=bidirectional; scenario_size=4194304 ;;
    idle_5s) scenario_kind=idle; scenario_idle_ms=5000 ;;
    idle_30s) scenario_kind=idle; scenario_idle_ms=30000 ;;
    idle_120s) scenario_kind=idle; scenario_idle_ms=120000 ;;
  esac
}

scenario_path() {
  local scenario=$1
  local completion=$2
  scenario_parameters "$scenario"
  printf '/camouflage/index.html?scenario=%s&size=%s&count=%s&idle_ms=%s&completion=%s\n' \
    "$scenario_kind" "$scenario_size" "$scenario_count" "$scenario_idle_ms" \
    "$completion"
}

normalize_h3_capture_origin() {
  local pcap=$1
  local first_initial_frame
  local measured_udp_stream
  local foreign_udp_frame
  local invalid_prefix_frame
  local prefix_count
  local full_pcap="${pcap%.pcapng}.before-origin-trim.pcapng"
  local trimmed_pcap="${pcap%.pcapng}.origin-trim.tmp.pcapng"
  local report="$(dirname "$pcap")/capture-origin.txt"
  first_initial_frame=$(tshark -r "$pcap" \
    -d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && ((quic.long.packet_type==0) || (quic.long.packet_type_v2==1))" \
    -T fields -e frame.number | sed -n '1p')
  if [[ -z $first_initial_frame ]]; then
    printf 'warm-cache H3 capture has no client Initial\n' >&2
    return 1
  fi
  measured_udp_stream=$(tshark -r "$pcap" \
    -Y "frame.number==$first_initial_frame" -T fields -e udp.stream |
    sed -n '1p')
  if [[ -z $measured_udp_stream ]]; then
    printf 'warm-cache H3 Initial has no UDP flow identity\n' >&2
    return 1
  fi
  invalid_prefix_frame=$(tshark -r "$pcap" \
    -Y "frame.number<$first_initial_frame && (tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT || udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT)" \
    -T fields -e frame.number | sed -n '1p')
  if [[ -n $invalid_prefix_frame ]]; then
    printf 'warm-cache H3 capture has client traffic before Initial at frame %s\n' \
      "$invalid_prefix_frame" >&2
    return 1
  fi
  foreign_udp_frame=$(tshark -r "$pcap" \
    -Y "frame.number>=$first_initial_frame && udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && udp.stream!=$measured_udp_stream" \
    -T fields -e frame.number | sed -n '1p')
  if [[ -n $foreign_udp_frame ]]; then
    printf 'warm-cache H3 capture has a foreign UDP flow after Initial at frame %s\n' \
      "$foreign_udp_frame" >&2
    return 1
  fi
  prefix_count=$(tshark -r "$pcap" -Y "frame.number<$first_initial_frame" \
    -T fields -e frame.number | wc -l)
  if ! tshark -r "$pcap" -Y "frame.number>=$first_initial_frame" \
       -w "$trimmed_pcap" >/dev/null 2>&1; then
    rm -f -- "$trimmed_pcap"
    printf 'failed to normalize warm-cache H3 capture origin\n' >&2
    return 1
  fi
  mv -f -- "$pcap" "$full_pcap"
  mv -f -- "$trimmed_pcap" "$pcap"
  printf 'first_client_initial_frame=%s\nmeasured_udp_stream=%s\ndiscarded_server_only_prefix_packets=%s\nfull_capture=%s\n' \
    "$first_initial_frame" "$measured_udp_stream" "$prefix_count" \
    "$(basename "$full_pcap")" >"$report"
}

strict_transport_check() {
  local protocol=$1
  local pcap=$2
  if [[ $protocol == h3 ]]; then
    local udp_count
    local tcp_established
    local tcp_payload
    local first_frame
    local initial_frame
    local oversized_udp_frame
    local stream
    udp_count=$(tshark -r "$pcap" -d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
      -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && quic" -T fields -e frame.number | wc -l)
    tcp_established=$(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.syn==1 && tcp.flags.ack==1" \
      -T fields -e frame.number | wc -l)
    tcp_payload=$(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.len>0" \
      -T fields -e frame.number | wc -l)
    if [[ $udp_count -eq 0 || $tcp_established -ne 0 || $tcp_payload -ne 0 ]]; then
      printf 'strict H3 sample failed transport check (udp=%s tcp=%s payload=%s)\n' \
        "$udp_count" "$tcp_established" "$tcp_payload" >&2
      return 1
    fi
    oversized_udp_frame=$(tshark -r "$pcap" \
      -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && udp.length>1500" \
      -T fields -e frame.number | sed -n '1p')
    if [[ -n $oversized_udp_frame ]]; then
      printf 'strict H3 sample contains a UDP offload superframe at frame %s\n' \
        "$oversized_udp_frame" >&2
      return 1
    fi
    mapfile -t udp_streams < <(tshark -r "$pcap" \
      -d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
      -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && quic" \
      -T fields -e udp.stream | sed '/^$/d' | sort -nu)
    if [[ ${#udp_streams[@]} -eq 0 ]]; then
      printf 'strict H3 sample has no identifiable QUIC flow\n' >&2
      return 1
    fi
    for stream in "${udp_streams[@]}"; do
      first_frame=$(tshark -r "$pcap" \
        -d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
        -Y "udp.stream==$stream && udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT" \
        -T fields -e frame.number | sed -n '1p')
      initial_frame=$(tshark -r "$pcap" \
        -d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
        -Y "udp.stream==$stream && udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && ((quic.long.packet_type==0) || (quic.long.packet_type_v2==1))" \
        -T fields -e frame.number | sed -n '1p')
      if [[ -z $initial_frame || $first_frame != "$initial_frame" ]]; then
        printf 'strict H3 flow %s does not begin with a client Initial\n' \
          "$stream" >&2
        return 1
      fi
    done
    mapfile -t tcp_streams < <(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT" \
      -T fields -e tcp.stream | sed '/^$/d' | sort -nu)
    for stream in "${tcp_streams[@]}"; do
      IFS=$'\t' read -r destination syn ack < <(tshark -r "$pcap" \
        -Y "tcp.stream==$stream" -T fields -E separator=$'\t' \
        -e tcp.dstport -e tcp.flags.syn -e tcp.flags.ack | sed -n '1p')
      if [[ $destination != "$NAIVEFOX_FIXTURE_PROXY_PORT" ||
            $syn != True || $ack != False ]]; then
        printf 'strict H3 TCP probe %s does not begin with a client SYN\n' \
          "$stream" >&2
        return 1
      fi
    done
  else
    local ack
    local destination
    local stream
    local syn
    mapfile -t tcp_streams < <(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT" \
      -T fields -e tcp.stream | sed '/^$/d' | sort -nu)
    if [[ ${#tcp_streams[@]} -eq 0 ]]; then
      printf 'H2 sample has no identifiable TCP flow\n' >&2
      return 1
    fi
    for stream in "${tcp_streams[@]}"; do
      IFS=$'\t' read -r destination syn ack < <(tshark -r "$pcap" \
        -Y "tcp.stream==$stream" -T fields -E separator=$'\t' \
        -e tcp.dstport -e tcp.flags.syn -e tcp.flags.ack | sed -n '1p')
      if [[ $destination != "$NAIVEFOX_FIXTURE_PROXY_PORT" ||
            $syn != True || $ack != False ]]; then
        printf 'H2 flow %s does not begin with a client SYN\n' "$stream" >&2
        return 1
      fi
      if [[ -z $(tshark -r "$pcap" \
        -d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
        -Y "tcp.stream==$stream && tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
        -T fields -e frame.number | sed -n '1p') ]]; then
        printf 'H2 flow %s has no visible TLS ClientHello\n' "$stream" >&2
        return 1
      fi
    done
  fi
}

start_browser_controller() {
  local profile=$1
  local url=$2
  local completion=$3
  local sample_dir=$4
  local protocol=$5
  local socks_port=${6:-0}
  local effective_backend=$browser_backend
  if [[ $effective_backend == auto && $protocol == h3 &&
        $experiment_design == multi_arm_superblocks ]]; then
    effective_backend=selenium
  fi
  local ready_file="$sample_dir/browser-ready.json"
  local navigate_file="$sample_dir/browser-navigate"
  local done_file="$sample_dir/browser-done"
  browser_stop_file="$sample_dir/browser-stop"
  browser_shutdown_file="$sample_dir/browser-shutdown.json"
  rm -f -- "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion"
  local -a warmup_args=()
  if [[ $effective_backend == selenium && $protocol == h3 &&
        $experiment_design == multi_arm_superblocks && $socks_port -eq 0 ]]; then
    local warmup_completion
    warmup_completion=$(openssl rand -hex 16)
    local warmup_completion_file="$NAIVEFOX_FIXTURE_RUN_DIR/completions/$warmup_completion"
    rm -f -- "$warmup_completion_file"
    warmup_args=(
      --warmup-url
      "https://127.0.0.1:$NAIVEFOX_FIXTURE_HTTPS_PORT/camouflage/index.html?scenario=initial&completion=$warmup_completion"
      --warmup-completion-file "$warmup_completion_file"
    )
  fi
  setsid env -u SSLKEYLOGFILE "${firefox_runtime_env[@]}" \
    "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
    "$browser_python" "$INTEGRATION_DIR/camouflage_browser_controller.py" \
    --binary "$REFERENCE_BIN" --profile "$profile" --backend "$effective_backend" \
    --protocol "$protocol" \
    --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --socks-port "$socks_port" \
    --url "$url" \
    --completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion" \
    "${warmup_args[@]}" \
    --ready-file "$ready_file" --navigate-file "$navigate_file" \
    --done-file "$done_file" --stop-file "$browser_stop_file" \
    --shutdown-file "$browser_shutdown_file" \
    --browser-log "$sample_dir/firefox.log" \
    --webdriver-log "$sample_dir/webdriver.log" \
    --timeout "$sample_timeout" >"$sample_dir/controller.log" 2>&1 &
  browser_controller_pid=$!
  wait_for_file "$ready_file" "$browser_controller_pid" \
    'Firefox browser controller' 300
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backend"])' \
    "$ready_file" >>"$controller_backends"
}

run_browser_workload() {
  local sample_dir=$1
  : >"$sample_dir/browser-navigate"
  wait_for_file "$sample_dir/browser-done" "$browser_controller_pid" \
    'controlled Firefox workload' "$((sample_timeout * 10 + 50))"
}

stop_browser_controller() {
  local controller_pid=$browser_controller_pid
  : >"$browser_stop_file"
  if ! timeout 10 tail --pid="$controller_pid" -f /dev/null; then
    local controller_pgid
    controller_pgid=$(ps -o pgid= -p "$controller_pid" 2>/dev/null |
      tr -d ' ')
    if [[ $controller_pgid != "$controller_pid" ]]; then
      printf 'Firefox browser controller lacks its isolated process group\n' >&2
      stop_process_group "$controller_pid"
      return 1
    fi
    printf 'WebDriver quit timed out; using post-capture process-group SIGTERM\n' \
      >&2
    kill -TERM -- "-$controller_pid" 2>/dev/null || true
    if ! timeout 5 tail --pid="$controller_pid" -f /dev/null; then
      printf 'Firefox browser controller required SIGKILL\n' >&2
      kill -KILL -- "-$controller_pid" 2>/dev/null || true
      wait "$controller_pid" 2>/dev/null || true
      return 1
    fi
    wait "$controller_pid" 2>/dev/null || true
    if kill -0 -- "-$controller_pid" 2>/dev/null; then
      printf 'Firefox browser controller left process-group members\n' >&2
      kill -KILL -- "-$controller_pid" 2>/dev/null || true
      return 1
    fi
    browser_controller_pid=
    browser_stop_file=
    browser_shutdown_file=
    return 0
  fi
  wait "$controller_pid"
  python3 - "$browser_shutdown_file" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    not record["browser_process_exited"]
    or record["forced_kill"]
    or record.get("shutdown_failed", False)
):
    raise SystemExit("Firefox controller required forced or incomplete shutdown")
if record["shutdown_method"] not in ("webdriver_quit", "controlled_sigterm"):
    raise SystemExit("Firefox controller lacks a controlled shutdown method")
if (
    record["shutdown_method"] == "controlled_sigterm"
    and record["process_returncode"] not in (0, -15)
):
    raise SystemExit("Firefox command-line process exited unexpectedly")
PY
  browser_controller_pid=
  browser_stop_file=
  browser_shutdown_file=
}

warm_reference_cache() {
  local profile=$1
  local protocol=$2
  local sample_dir=$3
  local warm_token=$4
  local warm_dir="$sample_dir/cache-warm"
  mkdir -m 0700 -- "$warm_dir"
  start_browser_controller "$profile" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/camouflage/index.html?scenario=warm_css&completion=$warm_token" \
    "$warm_token" "$warm_dir" "$protocol"
  run_browser_workload "$warm_dir"
  stop_browser_controller
}

cold_proxy_reset_applies() {
  local protocol=$1
  [[ $protocol == h3 ]] || return 1
  if [[ $experiment_design == multi_arm_superblocks ]]; then
    [[ ,$multi_arm_arms_csv, == *,tree-root-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-resource-committed-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-resource-native-cache-committed-overlap,* ||
       ,$multi_arm_arms_csv, == *,tree-native-parser-preload-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-native-parser-document-handoff-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-native-parser-retarget-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-native-parser-ipc-rendezvous-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-native-parser-root-rendezvous-overlap-css,* ||
       ,$multi_arm_arms_csv, == *,tree-native-parser-process-overlap-css,* ]]
  else
    [[ $naivefox_arm == tree-root-overlap-css ||
       $naivefox_arm == tree-resource-committed-overlap-css ||
       $naivefox_arm == tree-resource-native-cache-committed-overlap ||
       $naivefox_arm == tree-native-parser-preload-overlap-css ||
       $naivefox_arm == tree-native-parser-document-handoff-overlap-css ||
       $naivefox_arm == tree-native-parser-retarget-overlap-css ||
       $naivefox_arm == tree-native-parser-ipc-rendezvous-overlap-css ||
       $naivefox_arm == tree-native-parser-root-rendezvous-overlap-css ||
       $naivefox_arm == tree-native-parser-process-overlap-css ]]
  fi
}

validate_cache_evidence() {
  local role=$1
  local warm_token=$2
  local measure_token=$3
  local sample_dir=$4
  local start_offset=$5
  local experiment_block=$6
  python3 "$INTEGRATION_DIR/camouflage_cache_validation.py" \
    --journal "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL" \
    --role "$role" --warm-token "$warm_token" \
    --measure-token "$measure_token" \
    --start-offset "$start_offset" \
    --features "$feature_fragments/$(basename "$sample_dir").json" \
    --output "$sample_dir/cache-validation.txt"
  local semantics_hash
  semantics_hash=$(sed -n 's/^semantics_sha256=//p' \
    "$sample_dir/cache-validation.txt")
  [[ $semantics_hash =~ ^[0-9a-f]{64}$ ]] || {
    printf 'cache diagnostic produced invalid semantics digest\n' >&2
    return 1
  }
  printf '%s\t%s\t%s\n' "$experiment_block" "$role" "$semantics_hash" \
    >>"$cache_semantics_records"
  cache_validated_participants=$((cache_validated_participants + 1))
}

extract_sample() {
  local protocol=$1
  local scenario=$2
  local label=$3
  local session_id=$4
  local pcap=$5
  local experiment_block=$6
  local arm=$7
  strict_transport_check "$protocol" "$pcap"
  python3 "$INTEGRATION_DIR/camouflage_features.py" extract \
    --pcap "$pcap" --protocol "$protocol" \
    --server-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --scenario "$scenario" \
    --label "$label" --session-id "$session_id" \
    --experiment-block "$experiment_block" \
    --naivefox-arm "$arm" \
    --output "$feature_fragments/$session_id.json"
}

run_reference_sample() {
  local protocol=$1
  local scenario=$2
  local label=$3
  local session_id=$4
  local experiment_block=$5
  local completion=$6
  local sample_dir="$private_dir/$session_id"
  local profile="$sample_dir/profile"
  local pcap="$sample_dir/capture.pcapng"
  local path
  local warm_token=
  local cache_journal_start=0
  path=$(scenario_path "$scenario" "$completion")
  mkdir -m 0700 -- "$sample_dir"
  make_profile "$profile" "$protocol" reference "" "$naivefox_arm"
  if [[ $naivefox_arm == tree-warm-css-304 ]]; then
    cache_journal_start=$(stat -c %s \
      "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL" 2>/dev/null || printf 0)
    warm_token=$(openssl rand -hex 16)
    warm_reference_cache "$profile" "$protocol" "$sample_dir" "$warm_token"
    validate_no_tls_token_persistence "$profile"
    restart_fixture_proxy "$session_id:reference_measure"
  elif cold_proxy_reset_applies "$protocol"; then
    restart_fixture_proxy "$session_id:reference_cold_measure"
  fi
  if [[ $naivefox_arm == document-native-channel-open ]]; then
    validate_native_channel_fresh_cache "$profile" reference
  fi
  # Fixture resets are pre-measure setup. Start the fail-closed mutation
  # monitor only after that setup has converged, but before Firefox can create
  # the measured connection.
  start_network_mutation_monitor "$sample_dir"
  start_browser_controller "$profile" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT$path" \
    "$completion" "$sample_dir" "$protocol"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  run_browser_workload "$sample_dir"
  sleep 0.25
  stop_capture
  if [[ $naivefox_arm == tree-warm-css-304 ]]; then
    normalize_h3_capture_origin "$pcap"
  fi
  stop_network_mutation_monitor
  extract_sample "$protocol" "$scenario" "$label" "$session_id" "$pcap" \
    "$experiment_block" reference
  stop_browser_controller
  if [[ $naivefox_arm == tree-warm-css-304 ]]; then
    validate_cache_evidence reference "$warm_token" "$completion" \
      "$sample_dir" "$cache_journal_start" "$experiment_block"
    validate_no_tls_token_persistence "$profile"
  fi
}

run_naivefox_sample() {
  local protocol=$1
  local scenario=$2
  local session_id=$3
  local experiment_block=$4
  local arm=$5
  local completion=$6
  local sample_dir="$private_dir/$session_id"
  local naivefox_profile="$sample_dir/naivefox-profile"
  local naivefox_config="$sample_dir/naivefox-config.json"
  local browser_profile="$sample_dir/browser-profile"
  local pcap="$sample_dir/capture.pcapng"
  local log="$sample_dir/naivefox.log"
  local keylog="$sample_dir/naivefox.keys"
  local -a sslkeylog_unset=(-u SSLKEYLOGFILE)
  local -a sslkeylog_assignment=()
  local drain_pattern=
  local drain_ready=1
  local outer_count
  local padding_count
  local path
  local socks_port
  local target_port
  local warm_browser_profile="$sample_dir/warm-browser-profile"
  local warm_config="$sample_dir/naivefox-warm-config.json"
  local warm_log="$sample_dir/naivefox-warm.log"
  local warm_outer_token=
  local warm_trigger_token=
  local cache_journal_start=0
  socks_port=$(choose_port)
  path=$(scenario_path "$scenario" "$completion")
  if [[ $inner_transport == https ]]; then
    target_port=$NAIVEFOX_FIXTURE_HTTPS_PORT
  else
    target_port=$NAIVEFOX_FIXTURE_HTTP_PORT
  fi
  mkdir -m 0700 -- "$sample_dir"
  make_profile "$naivefox_profile" "$protocol" naivefox "" "$arm"
  make_profile "$browser_profile" "$protocol" socks-browser "$socks_port"
  if [[ $private_h3_keylog == 1 && $protocol == h3 ]]; then
    : >"$keylog"
    chmod 0600 "$keylog"
    sslkeylog_unset=()
    sslkeylog_assignment=("SSLKEYLOGFILE=$keylog")
  fi
  if [[ $arm == tree-warm-css-304 ]]; then
    local warm_socks_port
    local warm_target_port=$target_port
    local warm_dir="$sample_dir/cache-warm"
    warm_socks_port=$(choose_port)
    cache_journal_start=$(stat -c %s \
      "$NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL" 2>/dev/null || printf 0)
    warm_outer_token=$(openssl rand -hex 16)
    warm_trigger_token=$(openssl rand -hex 16)
    mkdir -m 0700 -- "$warm_dir"
    make_profile "$warm_browser_profile" "$protocol" socks-browser \
      "$warm_socks_port"
    NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
      NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
      --output "$warm_config" --arm "$arm" --protocol "$protocol" \
      --socks-port "$warm_socks_port" \
      --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
      --max-connections 1 \
      --preamble-path "/camouflage/index.html?scenario=warm_css&completion=$warm_outer_token"
    env "${sslkeylog_unset[@]}" \
      -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
      "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" \
      NAIVEFOX_PROFILE="$naivefox_profile" \
      "$NAIVEFOX_BIN" "$warm_config" >"$warm_log" 2>&1 &
    naivefox_pid=$!
    wait_for_cache_log "$naivefox_pid" "$warm_log" '^SOCKS5 listening on '
    start_browser_controller "$warm_browser_profile" \
      "$inner_transport://localhost:$warm_target_port/camouflage/index.html?scenario=initial&completion=$warm_trigger_token" \
      "$warm_trigger_token" "$warm_dir" "$protocol" "$warm_socks_port"
    run_browser_workload "$warm_dir"
    wait_for_cache_log "$naivefox_pid" "$warm_log" \
      ' preamble root-overlap drain=complete completed_resources=1 protocol=h3$'
    wait_for_cache_log "$naivefox_pid" "$warm_log" \
      ' preamble result=success status=0x[0-9a-fA-F]+ http=2[0-9][0-9] bytes=[0-9]+ protocol=h3$'
    stop_browser_controller
    stop_pid_clean "$naivefox_pid" "$warm_log"
    naivefox_pid=
    validate_no_tls_token_persistence "$naivefox_profile"
    restart_fixture_proxy "$session_id:naivefox_measure"
  elif cold_proxy_reset_applies "$protocol"; then
    restart_fixture_proxy "$session_id:naivefox_cold_measure"
  fi
  # The cold reset is not part of the sample. Any mutation after this marker,
  # including during NaiveFox startup, capture, or drain, invalidates it.
  start_network_mutation_monitor "$sample_dir"
  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
  python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
    --output "$naivefox_config" --arm "$arm" \
    --protocol "$protocol" --socks-port "$socks_port" \
    --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --preamble-path "$path"
  if [[ $arm == document-native-channel-open ||
        $arm == tree-resource-native-cache-committed-overlap ||
        $arm == tree-native-parser-preload-overlap-css ||
        $arm == tree-native-parser-document-handoff-overlap-css ||
        $arm == tree-native-parser-retarget-overlap-css ||
        $arm == tree-native-parser-ipc-rendezvous-overlap-css ||
        $arm == tree-native-parser-root-rendezvous-overlap-css ||
        $arm == tree-native-parser-process-overlap-css ]]; then
    validate_native_channel_fresh_cache "$naivefox_profile" naivefox
  fi
  env "${sslkeylog_unset[@]}" \
    -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
    "${sslkeylog_assignment[@]}" \
    "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" \
    NAIVEFOX_PROFILE="$naivefox_profile" \
    "$NAIVEFOX_BIN" "$naivefox_config" >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  start_browser_controller "$browser_profile" \
    "$inner_transport://localhost:$target_port$path" \
    "$completion" "$sample_dir" "$protocol" "$socks_port"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  run_browser_workload "$sample_dir"
  if [[ $arm == tree-root-overlap || $arm == tree-root-overlap-css ||
        $arm == tree-warm-css-304 ]]; then
    local expected_resources=2
    if [[ $arm == tree-root-overlap-css || $arm == tree-warm-css-304 ]]; then
      expected_resources=1
    fi
    drain_pattern=" preamble root-overlap drain=complete completed_resources=$expected_resources protocol=$protocol$"
  elif [[ $arm == tree-resource-committed-overlap-css ]]; then
    drain_pattern=" preamble resource-committed-overlap drain=complete completed_resources=1 protocol=$protocol$"
  elif [[ $arm == tree-resource-native-cache-committed-overlap ]]; then
    drain_pattern=" preamble resource-native-cache-committed-overlap drain=complete completed_resources=1 cache_new=1 protocol=$protocol$"
  elif [[ $arm == tree-native-parser-preload-overlap-css ||
          $arm == tree-native-parser-document-handoff-overlap-css ||
          $arm == tree-native-parser-retarget-overlap-css ||
          $arm == tree-native-parser-ipc-rendezvous-overlap-css ||
          $arm == tree-native-parser-root-rendezvous-overlap-css ||
          $arm == tree-native-parser-process-overlap-css ]]; then
    drain_pattern=" preamble native-parser-preload drain=complete completed_resources=1 http=2[0-9][0-9] protocol=$protocol$"
  elif [[ $arm == document-overlap ]]; then
    drain_pattern=" preamble document-overlap drain=complete root_done=1 completed_resources=0 protocol=$protocol$"
  elif [[ $arm == document-start-overlap ]]; then
    drain_pattern=" preamble document-start-overlap drain=complete root_done=1 completed_resources=0 protocol=$protocol$"
  fi
  sleep 0.25
  if [[ -n $drain_pattern ]] && ! rg -q "$drain_pattern" "$log"; then
    drain_ready=0
  fi
  stop_capture
  if [[ $arm == tree-warm-css-304 ]]; then
    normalize_h3_capture_origin "$pcap"
  fi
  stop_network_mutation_monitor
  if [[ $drain_ready -eq 0 ]]; then
    stop_browser_controller
    stop_pid "$naivefox_pid"
    naivefox_pid=
    printf 'NaiveFox sample %s did not drain its preamble by the fixed capture cutoff\n' \
      "$session_id" >&2
    return 1
  fi
  if [[ $arm == tree-native-parser-ipc-rendezvous-overlap-css ||
        $arm == tree-native-parser-root-rendezvous-overlap-css ]]; then
    wait_for_log "$naivefox_pid" "$log" \
      'Native style activation phase=request-primary-actor-destroyed request=[0-9]+$'
    wait_for_log "$naivefox_pid" "$log" \
      'Native style activation phase=request-background-actor-destroyed request=[0-9]+$'
  fi
  if [[ $arm == tree-native-parser-root-rendezvous-overlap-css ]]; then
    wait_for_log "$naivefox_pid" "$log" \
      'Native root replacement activation phase=request-primary-actor-destroyed request=[0-9]+$'
    wait_for_log "$naivefox_pid" "$log" \
      'Native root replacement activation phase=request-background-actor-destroyed request=[0-9]+$'
  fi
  outer_count=$(rg -c "^Outer protocol: $protocol$" "$log" || true)
  padding_count=$(rg -c '^Padding negotiated: yes$' "$log" || true)
  if [[ $outer_count -eq 0 || $padding_count -ne $outer_count ]]; then
    printf 'NaiveFox sample %s has incomplete protocol/padding evidence\n' \
      "$session_id" >&2
    return 1
  fi
  extract_sample "$protocol" "$scenario" naivefox "$session_id" "$pcap" \
    "$experiment_block" "$arm"
  python3 "$INTEGRATION_DIR/camouflage_sample_validation.py" \
    --arm "$arm" --protocol "$protocol" --log "$log" \
    --features "$feature_fragments/$session_id.json"
  stop_browser_controller
  stop_pid "$naivefox_pid"
  naivefox_pid=
  if [[ $arm == tree-warm-css-304 ]]; then
    validate_cache_evidence naivefox "$warm_outer_token" "$completion" \
      "$sample_dir" "$cache_journal_start" "$experiment_block"
    validate_no_tls_token_persistence "$naivefox_profile"
  fi
}

scenario_csv=$(IFS=,; printf '%s' "${scenarios[*]}")
session_counter=0
if [[ $experiment_design == multi_arm_superblocks ]]; then
  members_per_block=$((2 + ${#multi_arm_arms[@]}))
elif [[ $diagnostic_naivefox_only == 1 ]]; then
  members_per_block=1
else
  members_per_block=3
fi
for protocol in "${protocols[@]}"; do
  "$INTEGRATION_DIR/start.sh" --mode "$protocol"
  run_dir=$(<"$ACTIVE_RUN_FILE")
  # shellcheck source=/dev/null
  source "$run_dir/fixture.env"
  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    python3 - "$sensitive_values" <<'PY'
import os
import sys
from urllib.parse import quote

with open(sys.argv[1], "a", encoding="utf-8") as stream:
    for name in ("NAIVEFOX_FIXTURE_USER", "NAIVEFOX_FIXTURE_PASS"):
        value = os.environ[name]
        stream.write(value + "\n")
        encoded = quote(value, safe="")
        if encoded != value:
            stream.write(encoded + "\n")
PY
  schedule="$private_dir/$protocol-schedule.tsv"
  if [[ $diagnostic_naivefox_only == 1 ]]; then
    python3 - "$protocol" "$samples_per_cohort" "$scenario_override" \
      "$naivefox_arm" >"$schedule" <<'PY'
import sys

protocol = sys.argv[1]
count = int(sys.argv[2])
scenario = sys.argv[3]
arm = sys.argv[4]
for index in range(count):
    print("naivefox", arm, scenario, f"{protocol}_d{index:06d}", sep="\t")
PY
  elif [[ $experiment_design == multi_arm_superblocks ]]; then
    python3 "$INTEGRATION_DIR/camouflage_superblocks.py" schedule \
      --seed "$seed" --protocol "$protocol" --blocks "$samples_per_cohort" \
      --scenarios "$scenario_csv" --arms "$multi_arm_arms_csv" >"$schedule"
  else
    python3 - "$seed" "$protocol" "$samples_per_cohort" "$scenario_csv" \
      "$naivefox_arm" >"$schedule" <<'PY'
import random
import sys

seed = int(sys.argv[1])
protocol = sys.argv[2]
count = int(sys.argv[3])
scenarios = sys.argv[4].split(",")
naivefox_arm = sys.argv[5]
items = []
rng = random.Random(f"{seed}:{protocol}")
for index in range(count):
    labels = ["firefox_a", "firefox_b", "naivefox"]
    rng.shuffle(labels)
    block = f"{protocol}_b{index:06d}"
    for label in labels:
        arm = naivefox_arm if label == "naivefox" else "reference"
        items.append((label, arm, scenarios[index % len(scenarios)], block))
for label, arm, scenario, block in items:
    print(label, arm, scenario, block, sep="\t")
PY
  fi
  declare -A block_completion_tokens=()
  while IFS=$'\t' read -r label sample_arm scenario experiment_block; do
    session_counter=$((session_counter + 1))
    session_id=$(printf '%s_s%06d' "$protocol" "$session_counter")
    printf 'Collecting %s %s %s (%d/%d)\n' \
      "$protocol" "$label" "$scenario" "$session_counter" \
      "$((samples_per_cohort * members_per_block * ${#protocols[@]}))"
    if [[ -z ${block_completion_tokens[$experiment_block]:-} ]]; then
      block_completion_tokens[$experiment_block]=$(openssl rand -hex 16)
    fi
    completion=${block_completion_tokens[$experiment_block]}
    if [[ $label == naivefox ]]; then
      run_naivefox_sample "$protocol" "$scenario" "$session_id" \
        "$experiment_block" "$sample_arm" "$completion"
    else
      run_reference_sample "$protocol" "$scenario" "$label" "$session_id" \
        "$experiment_block" "$completion"
    fi
  done <"$schedule"
  "$INTEGRATION_DIR/stop.sh" --quiet
  if [[ -n $fixture_caddy_child_pid ]]; then
    wait "$fixture_caddy_child_pid" 2>/dev/null || true
    fixture_caddy_child_pid=
  fi
done

expected_proxy_restart_count=0
if [[ " ${protocols[*]} " == *" h3 "* ]]; then
  if [[ $naivefox_arm == tree-warm-css-304 ]] ||
     [[ $naivefox_arm == tree-root-overlap-css ]] ||
     [[ $naivefox_arm == tree-resource-committed-overlap-css ]] ||
     [[ $naivefox_arm == tree-resource-native-cache-committed-overlap ]] ||
     [[ $naivefox_arm == tree-native-parser-preload-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-document-handoff-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-retarget-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-ipc-rendezvous-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-root-rendezvous-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-process-overlap-css ]] ||
     [[ $experiment_design == multi_arm_superblocks &&
        ( ,$multi_arm_arms_csv, == *,tree-root-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-resource-committed-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-resource-native-cache-committed-overlap,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-preload-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-document-handoff-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-retarget-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-ipc-rendezvous-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-root-rendezvous-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-process-overlap-css,* ) ]]; then
    expected_proxy_restart_count=$((samples_per_cohort * members_per_block))
  fi
fi
if [[ $proxy_restart_count -ne $expected_proxy_restart_count ]]; then
  printf 'fixture proxy restarted %s times, expected %s\n' \
    "$proxy_restart_count" "$expected_proxy_restart_count" >&2
  exit 1
fi

cache_condition=cold_default
if [[ $naivefox_arm == tree-root-overlap-css ]]; then
  cache_condition=cold_css_200_control
elif [[ $naivefox_arm == tree-resource-committed-overlap-css ]]; then
  cache_condition=cold_css_200_resource_committed
elif [[ $naivefox_arm == tree-resource-native-cache-committed-overlap ]]; then
  cache_condition=cold_css_200_native_cache_committed
elif [[ $naivefox_arm == tree-native-parser-preload-overlap-css ]]; then
  cache_condition=cold_css_200_native_parser_preload
elif [[ $naivefox_arm == tree-native-parser-document-handoff-overlap-css ]]; then
  cache_condition=cold_css_200_native_parser_document_handoff
elif [[ $naivefox_arm == tree-native-parser-retarget-overlap-css ]]; then
  cache_condition=cold_css_200_native_parser_retarget
elif [[ $naivefox_arm == tree-native-parser-ipc-rendezvous-overlap-css ]]; then
  cache_condition=cold_css_200_native_parser_ipc_rendezvous
elif [[ $naivefox_arm == tree-native-parser-root-rendezvous-overlap-css ]]; then
  cache_condition=cold_css_200_native_parser_root_rendezvous
elif [[ $naivefox_arm == tree-native-parser-process-overlap-css ]]; then
  cache_condition=cold_css_200_native_parser_process
elif [[ $naivefox_arm == tree-warm-css-304 ]]; then
  cache_condition=warm_css_304
  expected_cache_participants=$((samples_per_cohort * 3))
  if [[ $cache_validated_participants -ne $expected_cache_participants ]]; then
    printf 'cache diagnostic validated %s participants, expected %s\n' \
      "$cache_validated_participants" "$expected_cache_participants" >&2
    exit 1
  fi
  if [[ $cache_validated_participants -ne $session_counter ]]; then
    printf 'cache diagnostic did not validate every collected participant\n' >&2
    exit 1
  fi
  python3 - "$cache_semantics_records" "$samples_per_cohort" <<'PY'
import collections
import sys

groups = collections.defaultdict(list)
with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        block, role, digest = line.rstrip("\n").split("\t")
        groups[block].append((role, digest))
if len(groups) != int(sys.argv[2]):
    raise SystemExit("cache semantics evidence has incomplete block coverage")
for block, rows in groups.items():
    if len(rows) != 3 or sorted(role for role, _ in rows) != [
        "naivefox", "reference", "reference"
    ]:
        raise SystemExit(f"cache semantics block {block} has incomplete roles")
    if len({digest for _, digest in rows}) != 1:
        raise SystemExit(
            f"warm Firefox A/B and NaiveFox CSS semantics differ in block {block}"
        )
PY
  cat >"$safe_dir/cache-diagnostic.txt" <<EOF
cache_condition=warm_css_304
validated_participants=$cache_validated_participants
warm_response=200_with_stable_etag
measured_outer_response=304_after_gecko_if_none_match
measured_inner_response=fresh_200
outer_request_semantics=equal_across_firefox_a_firefox_b_naivefox
outer_quic_identity=one_per_measured_participant
outer_client_hello=one_per_measured_participant
outer_h3_0rtt=absent
tls_token_persistence=disabled_and_absent
profile_scope=participant_sample_warm_measure_then_deleted
inference=diagnostic_causal_screening_only
EOF
fi

if [[ $isolated_network == 1 ]]; then
  capture_offload_policy=namespace_loopback_gro_gso_tso_udp_gso_disabled
else
  capture_offload_policy=host_interface_offload_state_unmodified
fi

if [[ $diagnostic_naivefox_only == 1 ]]; then
  diagnostic_protocols=$(IFS=,; printf '%s' "${protocols[*]}")
  cat >"$safe_dir/diagnostic-summary.txt" <<EOF
diagnostic=naivefox_only_lifecycle
sample_count=$session_counter
samples_per_protocol=$samples_per_cohort
naivefox_arm=$naivefox_arm
scenario=$scenario_override
protocols=$diagnostic_protocols
isolated_network=$isolated_network
network_mutation_policy=reject_route_address_link
network_mutation_monitor=netlink_route_v1_fail_closed
network_mutation_validated_samples=$network_mutation_validated_samples
capture_offload_policy=$capture_offload_policy
h3_udp_superframe_policy=reject_udp_length_gt_1500
EOF
  find "$safe_dir" -type d -exec chmod 0700 {} +
  find "$safe_dir" -type f -exec chmod 0600 {} +
  if find "$safe_dir" -type f ! -name diagnostic-summary.txt -print -quit |
     rg -q .; then
    printf 'unexpected output reached NaiveFox-only diagnostic summary\n' >&2
    exit 1
  fi
  if rg -F -f "$sensitive_values" "$safe_dir" ||
     rg -i -e proxy-authorization -e sslkeylogfile -e 'localhost:' "$safe_dir"; then
    printf 'sensitive or endpoint-specific data reached diagnostic summary\n' >&2
    exit 1
  fi
  success=1
  printf 'NaiveFox-only lifecycle diagnostic completed\n'
  printf 'sanitized diagnostic summary: %s\n' "$safe_dir"
  exit 0
fi

analyze_dataset() {
  local features=$1
  local output_dir=$2
  local analysis_role=${3:-confirmatory}
  local -a analyzer_args=(
    --features "$features" --output-json "$output_dir/metrics.json"
    --output-summary "$output_dir/summary.txt" --mode "$mode" --seed "$seed"
    --bootstrap 1000 --permutations "$permutations"
    --refit-bootstrap "$refit_bootstrap"
    --max-features "$max_features" --iterations "$model_iterations"
  )
  if [[ $analysis_role == screening ]]; then
    analyzer_args+=(--screening-only)
  fi
  python3 "$INTEGRATION_DIR/analyze-camouflage.py" "${analyzer_args[@]}"
}

if [[ $experiment_design == multi_arm_superblocks ]]; then
  superblock_features="$safe_dir/features-superblocks.csv"
  python3 "$INTEGRATION_DIR/camouflage_features.py" merge \
    --input-dir "$feature_fragments" --output "$superblock_features" \
    --expected-superblocks "$samples_per_cohort" \
    --expected-superblock-arms "$multi_arm_arms_csv"
  python3 "$INTEGRATION_DIR/camouflage_superblocks.py" materialize \
    --features "$superblock_features" --output-dir "$safe_dir/arms" \
    --expected-blocks "$samples_per_cohort" --arms "$multi_arm_arms_csv"
  for arm in "${multi_arm_arms[@]}"; do
    analyze_dataset \
      "$safe_dir/arms/$arm/features.csv" "$safe_dir/arms/$arm" screening
  done
  arm_analyzer_args=(
    --features "$superblock_features" \
    --output-json "$safe_dir/arm-comparison.json" \
    --output-summary "$safe_dir/arm-comparison.txt" \
    --mode "$mode" --seed "$seed" --bootstrap 2000 --permutations 9999
  )
  if [[ $multi_arm_views_csv != all ]]; then
    arm_analyzer_args+=(--views "$multi_arm_views_csv")
  fi
  python3 "$INTEGRATION_DIR/analyze-camouflage-arms.py" \
    "${arm_analyzer_args[@]}"
  metadata_naivefox_arm=multi
  metadata_naivefox_arms=$multi_arm_arms_csv
  metadata_arm_comparison_views=$multi_arm_views_csv
  metadata_arm_specific_analysis=screening_only
else
  python3 "$INTEGRATION_DIR/camouflage_features.py" merge \
    --input-dir "$feature_fragments" --output "$safe_dir/features.csv" \
    --expected-per-cohort "$samples_per_cohort"
  single_arm_analysis=confirmatory
  if [[ $naivefox_arm == tree-complete ||
        $naivefox_arm == document-overlap ||
        $naivefox_arm == document-carrier-dispatch ||
        $naivefox_arm == document-cold-winner-handoff ||
        $naivefox_arm == document-native-cache-open ||
        $naivefox_arm == document-native-channel-open ||
        $naivefox_arm == document-handshake-confirmed ||
        $naivefox_arm == document-start-overlap ||
        $naivefox_arm == root-pmtud-control ||
        $naivefox_arm == tree-complete-css ||
        $naivefox_arm == tree-early-overlap ||
        $naivefox_arm == tree-root-overlap ||
        $naivefox_arm == tree-root-overlap-css ||
        $naivefox_arm == tree-resource-committed-overlap-css ||
        $naivefox_arm == tree-resource-native-cache-committed-overlap ||
        $naivefox_arm == tree-native-parser-preload-overlap-css ||
        $naivefox_arm == tree-native-parser-document-handoff-overlap-css ||
        $naivefox_arm == tree-native-parser-retarget-overlap-css ||
        $naivefox_arm == tree-native-parser-ipc-rendezvous-overlap-css ||
        $naivefox_arm == tree-native-parser-root-rendezvous-overlap-css ||
        $naivefox_arm == tree-native-parser-process-overlap-css ||
        $naivefox_arm == tree-warm-css-304 ||
        $naivefox_arm == tree-overlap ]]; then
    single_arm_analysis=screening
  fi
  analyze_dataset "$safe_dir/features.csv" "$safe_dir" "$single_arm_analysis"
  metadata_naivefox_arm=$naivefox_arm
  metadata_naivefox_arms=$naivefox_arm
  metadata_arm_comparison_views=not_applicable
  if [[ $single_arm_analysis == screening ]]; then
    metadata_arm_specific_analysis=screening_only
  else
    metadata_arm_specific_analysis=single_arm
  fi
fi

reference_version=$(LD_LIBRARY_PATH="$REFERENCE_LIBDIR" "$REFERENCE_BIN" --version 2>/dev/null)
naivefox_version=$(LD_LIBRARY_PATH="$NAIVEFOX_LIBDIR" "$NAIVEFOX_BIN" --version 2>/dev/null)
fixture_proxy_reset_policy=not_applicable
if [[ $naivefox_arm == tree-warm-css-304 ]]; then
  fixture_proxy_reset_policy=warm_after_drain_and_symmetric_cold_before_measure
elif [[ $naivefox_arm == tree-root-overlap-css ]] ||
     [[ $naivefox_arm == tree-resource-committed-overlap-css ]] ||
     [[ $naivefox_arm == tree-resource-native-cache-committed-overlap ]] ||
     [[ $naivefox_arm == tree-native-parser-preload-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-document-handoff-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-retarget-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-ipc-rendezvous-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-root-rendezvous-overlap-css ]] ||
     [[ $naivefox_arm == tree-native-parser-process-overlap-css ]] ||
     [[ $experiment_design == multi_arm_superblocks &&
        ( ,$multi_arm_arms_csv, == *,tree-root-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-resource-committed-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-resource-native-cache-committed-overlap,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-preload-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-document-handoff-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-retarget-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-ipc-rendezvous-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-root-rendezvous-overlap-css,* ||
          ,$multi_arm_arms_csv, == *,tree-native-parser-process-overlap-css,* ) ]]; then
  fixture_proxy_reset_policy=cold_before_measure
fi
# shellcheck source=/etc/os-release
source /etc/os-release
os_id=$ID
os_version=$VERSION_ID
cat >"$safe_dir/metadata.txt" <<EOF
schema_version=1
revision=$(git -C "$SOURCE_ROOT" rev-parse HEAD)
mode=$mode
seed=$seed
scenarios=$scenario_csv
conditional_bootstrap_iterations=1000
refit_bootstrap_iterations=$refit_bootstrap
permutation_iterations=$permutations
protocol_selection=$protocol_selection
inner_transport=$inner_transport
camouflage_style_size=$NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE
camouflage_script_size=$NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE
cache_condition=$cache_condition
fixture_proxy_reset_policy=$fixture_proxy_reset_policy
fixture_proxy_restart_count=$proxy_restart_count
fixture_proxy_expected_restart_count=$expected_proxy_restart_count
private_h3_keylog=$private_h3_keylog
isolated_network=$isolated_network
network_mutation_policy=reject_route_address_link
network_mutation_monitor=netlink_route_v1_fail_closed
network_mutation_validated_samples=$network_mutation_validated_samples
capture_offload_policy=$capture_offload_policy
h3_udp_superframe_policy=reject_udp_length_gt_1500
experiment_design=$experiment_design
naivefox_arm=$metadata_naivefox_arm
naivefox_arms=$metadata_naivefox_arms
arm_comparison_views=$metadata_arm_comparison_views
arm_specific_analysis=$metadata_arm_specific_analysis
samples_per_cohort=$samples_per_cohort
reference_mode=$capture_mode
os_id=$os_id
os_version=$os_version
kernel=$(uname -sr)
architecture=$(uname -m)
reference_version=$reference_version
naivefox_version=$naivefox_version
reference_binary_build_id=$(readelf -n "$REFERENCE_BIN" | sed -n 's/^ *Build ID: //p' | sed -n '1p')
naivefox_binary_build_id=$(readelf -n "$NAIVEFOX_BIN" | sed -n 's/^ *Build ID: //p' | sed -n '1p')
reference_libxul_sha256=$(sha256sum "$REFERENCE_LIBDIR/libxul.so" | cut -d' ' -f1)
reference_libssl3_sha256=$(sha256sum "$REFERENCE_LIBDIR/libssl3.so" | cut -d' ' -f1)
naivefox_libxul_sha256=$(sha256sum "$NAIVEFOX_LIBDIR/libxul.so" | cut -d' ' -f1)
naivefox_libssl3_sha256=$(sha256sum "$NAIVEFOX_LIBDIR/libssl3.so" | cut -d' ' -f1)
tshark_version=$(tshark --version | sed -n '1p')
capture_interface=any_sll_transmit_copy_when_available
capture_readiness=dumpcap_runtime_marker
capture_drop_policy=reject_nonzero
workload_driver=controlled_firefox_navigation
workload_completion=target_server_marker
completion_token_scope=experiment_block_wire_url
completion_marker_reset=before_each_participant
capture_cutoff=browser_done_plus_250ms
preamble_drain_policy=reject_if_incomplete_at_capture_cutoff
preamble_cache_policy=$([[ $naivefox_arm == tree-warm-css-304 ]] && printf diagnostic_per_sample_warm_304 || printf cold)
cache_profile_scope=$([[ $naivefox_arm == tree-warm-css-304 ]] && printf temporary_participant_sample_warm_measure_then_deleted || printf not_applicable)
cache_header_policy=$([[ $naivefox_arm == tree-warm-css-304 ]] && printf gecko_generated_if_none_match || printf not_applicable)
cache_capture_policy=$([[ $naivefox_arm == tree-warm-css-304 ]] && printf warm_traffic_excluded_measure_only || printf not_applicable)
cache_validated_participants=$cache_validated_participants
preamble_root_url_parity=reference_and_candidate_outer_exact_path
browser_controller_backends=$(sort -u "$controller_backends" | paste -sd, -)
naivefox_browser_proxy_policy=fail_closed_pac_loopback_only
process_shutdown_in_primary_capture=no
tls_keylog=disabled
raw_capture_material=deleted_after_success
EOF

find "$safe_dir" -type d -exec chmod 0700 {} +
find "$safe_dir" -type f -exec chmod 0600 {} +
if find "$safe_dir" -type f \( -name '*.pcap' -o -name '*.pcapng' -o \
     -name '*.keys' -o -name '*.log' \) -print -quit | rg -q .; then
  printf 'private capture material reached camouflage-safe output\n' >&2
  exit 1
fi
if rg -F -f "$sensitive_values" "$safe_dir" ||
   rg -i -e proxy-authorization -e sslkeylogfile -e 'localhost:' "$safe_dir"; then
  printf 'sensitive or endpoint-specific data reached camouflage-safe output\n' >&2
  exit 1
fi

success=1
printf 'NaiveFox passive camouflage %s suite completed\n' "$mode"
printf 'sanitized dataset and metrics: %s\n' "$safe_dir"
