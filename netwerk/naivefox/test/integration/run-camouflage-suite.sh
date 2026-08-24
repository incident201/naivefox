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
  off | gate | root | document-complete | tree-complete | tree-complete-css | tree-early-overlap | tree-root-overlap | tree-root-overlap-css | tree-overlap) ;;
  *)
    printf 'unsupported NaiveFox arm: %s\n' "$naivefox_arm" >&2
    exit 2
    ;;
esac
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
      off | gate | root | document-complete | tree-complete | tree-complete-css | tree-early-overlap | tree-root-overlap | tree-root-overlap-css | tree-overlap) ;;
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
  if [[ -n ${seen_multi_arms[root]:-} &&
        -n ${seen_multi_arms[document-complete]:-} ]]; then
    printf 'root and document-complete are aliases; select only one\n' >&2
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
naivefox_pid=
network_monitor_pid=
network_monitor_events=
network_monitor_ready=
network_monitor_done=
network_mutation_validated_samples=0
controller_backends="$private_dir/controller-backends.txt"
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

validate_profile_role() {
  local destination=$1
  local protocol=$2
  local participant=$3
  local mapping_pref='network.http.http3.alt-svc-mapping-for-testing'
  local force_pref='network.http.http3.force-use-alt-svc-mapping-for-testing'
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
}

make_profile() {
  local destination=$1
  local protocol=$2
  local participant=$3
  local socks_port=${4:-}
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
  if [[ $direct_h3 == true ]]; then
    cat >>"$destination/user.js" <<EOF
user_pref("network.http.http3.alt-svc-mapping-for-testing", "localhost;h3=:$NAIVEFOX_FIXTURE_PROXY_PORT");
user_pref("network.http.http3.force-use-alt-svc-mapping-for-testing", true);
EOF
  fi
  chmod 0600 "$destination/user.js"
  validate_profile_role "$destination" "$protocol" "$participant"
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
  if [[ $effective_backend == auto && $protocol == h3 ]]; then
    effective_backend=commandline
  fi
  local ready_file="$sample_dir/browser-ready.json"
  local navigate_file="$sample_dir/browser-navigate"
  local done_file="$sample_dir/browser-done"
  browser_stop_file="$sample_dir/browser-stop"
  setsid env -u SSLKEYLOGFILE "${firefox_runtime_env[@]}" \
    "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
    "$browser_python" "$INTEGRATION_DIR/camouflage_browser_controller.py" \
    --binary "$REFERENCE_BIN" --profile "$profile" --backend "$effective_backend" \
    --protocol "$protocol" \
    --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --socks-port "$socks_port" \
    --url "$url" \
    --completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion" \
    --ready-file "$ready_file" --navigate-file "$navigate_file" \
    --done-file "$done_file" --stop-file "$browser_stop_file" \
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
  sleep 0.25
}

stop_browser_controller() {
  : >"$browser_stop_file"
  if ! timeout 20 tail --pid="$browser_controller_pid" -f /dev/null; then
    printf 'Firefox browser controller did not stop cleanly\n' >&2
    return 1
  fi
  wait "$browser_controller_pid"
  browser_controller_pid=
  browser_stop_file=
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
  local sample_dir="$private_dir/$session_id"
  local profile="$sample_dir/profile"
  local pcap="$sample_dir/capture.pcapng"
  local completion
  local path
  completion=$(openssl rand -hex 16)
  path=$(scenario_path "$scenario" "$completion")
  mkdir -m 0700 -- "$sample_dir"
  make_profile "$profile" "$protocol" reference
  start_network_mutation_monitor "$sample_dir"
  start_browser_controller "$profile" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT$path" \
    "$completion" "$sample_dir" "$protocol"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  run_browser_workload "$sample_dir"
  stop_capture
  stop_network_mutation_monitor
  extract_sample "$protocol" "$scenario" "$label" "$session_id" "$pcap" \
    "$experiment_block" reference
  stop_browser_controller
}

run_naivefox_sample() {
  local protocol=$1
  local scenario=$2
  local session_id=$3
  local experiment_block=$4
  local arm=$5
  local sample_dir="$private_dir/$session_id"
  local naivefox_profile="$sample_dir/naivefox-profile"
  local naivefox_config="$sample_dir/naivefox-config.json"
  local browser_profile="$sample_dir/browser-profile"
  local pcap="$sample_dir/capture.pcapng"
  local log="$sample_dir/naivefox.log"
  local keylog="$sample_dir/naivefox.keys"
  local -a sslkeylog_unset=(-u SSLKEYLOGFILE)
  local -a sslkeylog_assignment=()
  local completion
  local outer_count
  local padding_count
  local path
  local socks_port
  local target_port
  socks_port=$(choose_port)
  completion=$(openssl rand -hex 16)
  path=$(scenario_path "$scenario" "$completion")
  if [[ $inner_transport == https ]]; then
    target_port=$NAIVEFOX_FIXTURE_HTTPS_PORT
  else
    target_port=$NAIVEFOX_FIXTURE_HTTP_PORT
  fi
  mkdir -m 0700 -- "$sample_dir"
  make_profile "$naivefox_profile" "$protocol" naivefox
  make_profile "$browser_profile" "$protocol" socks-browser "$socks_port"
  if [[ $private_h3_keylog == 1 && $protocol == h3 ]]; then
    : >"$keylog"
    chmod 0600 "$keylog"
    sslkeylog_unset=()
    sslkeylog_assignment=("SSLKEYLOGFILE=$keylog")
  fi
  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
  python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
    --output "$naivefox_config" --arm "$arm" \
    --protocol "$protocol" --socks-port "$socks_port" \
    --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT"
  start_network_mutation_monitor "$sample_dir"
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
  if [[ $arm == tree-root-overlap || $arm == tree-root-overlap-css ]]; then
    local expected_resources=2
    [[ $arm == tree-root-overlap-css ]] && expected_resources=1
    wait_for_log "$naivefox_pid" "$log" \
      " preamble root-overlap drain=complete completed_resources=$expected_resources protocol=$protocol$"
  fi
  stop_capture
  stop_network_mutation_monitor
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
  while IFS=$'\t' read -r label sample_arm scenario experiment_block; do
    session_counter=$((session_counter + 1))
    session_id=$(printf '%s_s%06d' "$protocol" "$session_counter")
    printf 'Collecting %s %s %s (%d/%d)\n' \
      "$protocol" "$label" "$scenario" "$session_counter" \
      "$((samples_per_cohort * members_per_block * ${#protocols[@]}))"
    if [[ $label == naivefox ]]; then
      run_naivefox_sample "$protocol" "$scenario" "$session_id" \
        "$experiment_block" "$sample_arm"
    else
      run_reference_sample "$protocol" "$scenario" "$label" "$session_id" \
        "$experiment_block"
    fi
  done <"$schedule"
  "$INTEGRATION_DIR/stop.sh" --quiet
done

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
        $naivefox_arm == tree-complete-css ||
        $naivefox_arm == tree-early-overlap ||
        $naivefox_arm == tree-root-overlap ||
        $naivefox_arm == tree-root-overlap-css ||
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
