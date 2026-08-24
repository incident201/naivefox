#!/usr/bin/env bash

set -euo pipefail
umask 077

# shellcheck source=netwerk/naivefox/test/integration/common.sh
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

mode=smoke
protocol_selection=both
inner_transport=https
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
    --samples-per-cohort)
      samples_per_cohort=${2:-}
      shift 2
      ;;
    --seed)
      seed=${2:-}
      shift 2
      ;;
    --help)
      printf 'usage: %s [--mode gate|smoke|standard|research] [--protocol h2|h3|both] [--inner-transport http|https] [--samples-per-cohort N] [--seed N]\n' "$0"
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
if [[ -z $samples_per_cohort ]]; then
  samples_per_cohort=$default_samples
fi
if [[ ! $samples_per_cohort =~ ^[1-9][0-9]*$ ]]; then
  printf 'samples per cohort must be a positive integer\n' >&2
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

run_id=$(openssl rand -hex 8)
private_dir="$STATE_ROOT/camouflage-captures/$run_id"
safe_dir="$STATE_ROOT/camouflage-safe/$run_id"
feature_fragments="$private_dir/features"
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-camouflage.XXXXXX")
mkdir -p "$private_dir" "$feature_fragments" "$safe_dir"
chmod 0700 "$private_dir" "$feature_fragments" "$safe_dir"
chmod 0700 "$capture_stage_dir"

capture_pid=
capture_stage_pcap=
capture_pcap=
capture_log=
browser_controller_pid=
browser_stop_file=
naivefox_pid=
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

make_profile() {
  local destination=$1
  local protocol=$2
  local socks_port=${3:-}
  local direct_h3=false
  if [[ $protocol == h3 && -z $socks_port ]]; then
    direct_h3=true
  fi
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
user_pref("network.http.http3.enable", $direct_h3);
EOF
  if [[ -n $socks_port ]]; then
    cat >>"$destination/user.js" <<EOF
user_pref("network.proxy.type", 1);
user_pref("network.proxy.socks", "127.0.0.1");
user_pref("network.proxy.socks_port", $socks_port);
user_pref("network.proxy.socks_version", 5);
user_pref("network.proxy.socks_remote_dns", true);
user_pref("network.proxy.no_proxies_on", "");
user_pref("network.proxy.allow_hijacking_localhost", true);
user_pref("network.proxy.failover_direct", false);
EOF
  fi
  if [[ $direct_h3 == true ]]; then
    cat >>"$destination/user.js" <<EOF
user_pref("network.http.http3.disable_when_third_party_roots_found", false);
user_pref("network.http.http3.alt-svc-mapping-for-testing", "localhost;h3=:$NAIVEFOX_FIXTURE_PROXY_PORT");
user_pref("network.http.http3.force-use-alt-svc-mapping-for-testing", true);
EOF
  fi
  chmod 0600 "$destination/user.js"
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
  strict_transport_check "$protocol" "$pcap"
  python3 "$INTEGRATION_DIR/camouflage_features.py" extract \
    --pcap "$pcap" --protocol "$protocol" \
    --server-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --scenario "$scenario" \
    --label "$label" --session-id "$session_id" \
    --experiment-block "$experiment_block" \
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
  make_profile "$profile" "$protocol"
  start_browser_controller "$profile" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT$path" \
    "$completion" "$sample_dir" "$protocol"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  run_browser_workload "$sample_dir"
  stop_capture
  extract_sample "$protocol" "$scenario" "$label" "$session_id" "$pcap" \
    "$experiment_block"
  stop_browser_controller
}

run_naivefox_sample() {
  local protocol=$1
  local scenario=$2
  local session_id=$3
  local experiment_block=$4
  local sample_dir="$private_dir/$session_id"
  local naivefox_profile="$sample_dir/naivefox-profile"
  local browser_profile="$sample_dir/browser-profile"
  local pcap="$sample_dir/capture.pcapng"
  local log="$sample_dir/naivefox.log"
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
  make_profile "$naivefox_profile" "$protocol"
  make_profile "$browser_profile" "$protocol" "$socks_port"
  env -u SSLKEYLOGFILE "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" \
    NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
    "$NAIVEFOX_BIN" --profile "$naivefox_profile" --protocol "$protocol" \
    --socks-listen "127.0.0.1:$socks_port" \
    --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  start_browser_controller "$browser_profile" \
    "$inner_transport://localhost:$target_port$path" \
    "$completion" "$sample_dir" "$protocol" "$socks_port"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  run_browser_workload "$sample_dir"
  stop_capture
  outer_count=$(rg -c "^Outer protocol: $protocol$" "$log" || true)
  padding_count=$(rg -c '^Padding negotiated: yes$' "$log" || true)
  if [[ $outer_count -eq 0 || $padding_count -ne $outer_count ]]; then
    printf 'NaiveFox sample %s has incomplete protocol/padding evidence\n' \
      "$session_id" >&2
    return 1
  fi
  extract_sample "$protocol" "$scenario" naivefox "$session_id" "$pcap" \
    "$experiment_block"
  stop_browser_controller
  stop_pid "$naivefox_pid"
  naivefox_pid=
}

scenario_csv=$(IFS=,; printf '%s' "${scenarios[*]}")
session_counter=0
for protocol in "${protocols[@]}"; do
  "$INTEGRATION_DIR/start.sh" --mode "$protocol"
  run_dir=$(<"$ACTIVE_RUN_FILE")
  # shellcheck source=/dev/null
  source "$run_dir/fixture.env"
  schedule="$private_dir/$protocol-schedule.tsv"
  python3 - "$seed" "$protocol" "$samples_per_cohort" "$scenario_csv" \
    >"$schedule" <<'PY'
import random
import sys

seed = int(sys.argv[1])
protocol = sys.argv[2]
count = int(sys.argv[3])
scenarios = sys.argv[4].split(",")
items = []
rng = random.Random(f"{seed}:{protocol}")
for index in range(count):
    labels = ["firefox_a", "firefox_b", "naivefox"]
    rng.shuffle(labels)
    block = f"{protocol}_b{index:06d}"
    for label in labels:
        items.append((label, scenarios[index % len(scenarios)], block))
for label, scenario, block in items:
    print(label, scenario, block, sep="\t")
PY
  while IFS=$'\t' read -r label scenario experiment_block; do
    session_counter=$((session_counter + 1))
    session_id=$(printf '%s_s%06d' "$protocol" "$session_counter")
    printf 'Collecting %s %s %s (%d/%d)\n' \
      "$protocol" "$label" "$scenario" "$session_counter" \
      "$((samples_per_cohort * 3 * ${#protocols[@]}))"
    if [[ $label == naivefox ]]; then
      run_naivefox_sample "$protocol" "$scenario" "$session_id" \
        "$experiment_block"
    else
      run_reference_sample "$protocol" "$scenario" "$label" "$session_id" \
        "$experiment_block"
    fi
  done <"$schedule"
  "$INTEGRATION_DIR/stop.sh" --quiet
done

python3 "$INTEGRATION_DIR/camouflage_features.py" merge \
  --input-dir "$feature_fragments" --output "$safe_dir/features.csv" \
  --expected-per-cohort "$samples_per_cohort"

python3 "$INTEGRATION_DIR/analyze-camouflage.py" \
  --features "$safe_dir/features.csv" --output-json "$safe_dir/metrics.json" \
  --output-summary "$safe_dir/summary.txt" --mode "$mode" --seed "$seed" \
  --bootstrap 1000 --permutations "$permutations" \
  --refit-bootstrap "$refit_bootstrap" \
  --max-features "$max_features" --iterations "$model_iterations"

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
conditional_bootstrap_iterations=1000
refit_bootstrap_iterations=$refit_bootstrap
permutation_iterations=$permutations
protocol_selection=$protocol_selection
inner_transport=$inner_transport
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
process_shutdown_in_primary_capture=no
tls_keylog=disabled
raw_capture_material=deleted_after_success
EOF

chmod 0600 "$safe_dir/features.csv" "$safe_dir/metrics.json" \
  "$safe_dir/summary.txt" "$safe_dir/metadata.txt"
if find "$safe_dir" -type f \( -name '*.pcap' -o -name '*.pcapng' -o \
     -name '*.keys' -o -name '*.log' \) -print -quit | rg -q .; then
  printf 'private capture material reached camouflage-safe output\n' >&2
  exit 1
fi
if rg -F "$NAIVEFOX_FIXTURE_USER" "$safe_dir" ||
   rg -F "$NAIVEFOX_FIXTURE_PASS" "$safe_dir" ||
   rg -i -e proxy-authorization -e sslkeylogfile -e 'localhost:' "$safe_dir"; then
  printf 'sensitive or endpoint-specific data reached camouflage-safe output\n' >&2
  exit 1
fi

success=1
printf 'NaiveFox passive camouflage %s suite completed\n' "$mode"
printf 'sanitized dataset and metrics: %s\n' "$safe_dir"
