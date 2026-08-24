#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

mode=smoke
protocol_selection=both
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
    --samples-per-cohort)
      samples_per_cohort=${2:-}
      shift 2
      ;;
    --seed)
      seed=${2:-}
      shift 2
      ;;
    --help)
      printf 'usage: %s [--mode gate|smoke|standard|research] [--protocol h2|h3|both] [--samples-per-cohort N] [--seed N]\n' "$0"
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
    permutations=50
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
    permutations=200
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
    permutations=1000
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

for tool in dumpcap tshark curl getcap openssl python3 readelf rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required camouflage tool not found: %s\n' "$tool" >&2
    exit 1
  }
done
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
mkdir -m 0700 -p "$private_dir" "$feature_fragments" "$safe_dir"
chmod 0700 "$capture_stage_dir"

capture_pid=
capture_stage_pcap=
capture_pcap=
firefox_pid=
naivefox_pid=
workload_pids=()
success=0

stop_pid() {
  local pid=${1:-}
  if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
  fi
  [[ -z $pid ]] || wait "$pid" 2>/dev/null || true
}

stop_capture() {
  if [[ -n $capture_pid ]] && kill -0 "$capture_pid" 2>/dev/null; then
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  [[ -z $capture_pid ]] || wait "$capture_pid" 2>/dev/null || true
  capture_pid=
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
}

cleanup() {
  local status=$?
  stop_capture
  local pid
  for pid in "${workload_pids[@]}"; do
    stop_pid "$pid"
  done
  stop_pid "$firefox_pid"
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
  capture_stage_pcap="$capture_stage_dir/$(basename "${destination%.pcapng}").raw.pcapng"
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
    [[ -s $capture_stage_pcap ]] && return 0
    sleep 0.1
  done
  printf 'timed out waiting for camouflage capture\n' >&2
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
user_pref("network.http.http3.enable", $( [[ $protocol == h3 ]] && printf true || printf false ));
EOF
  if [[ $protocol == h3 ]]; then
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
  scenario_parameters "$scenario"
  if [[ $scenario_kind == initial ]]; then
    printf '/camouflage/api\n'
  else
    printf '/camouflage/index.html?scenario=%s&size=%s&count=%s&idle_ms=%s\n' \
      "$scenario_kind" "$scenario_size" "$scenario_count" "$scenario_idle_ms"
  fi
}

scenario_connections() {
  local scenario=$1
  scenario_parameters "$scenario"
  case $scenario_kind in
    initial) printf '1\n' ;;
    browser_page) printf '7\n' ;;
    sequential) printf '5\n' ;;
    concurrent | burst) printf '%s\n' "$((scenario_count + 1))" ;;
    bulk_download | bulk_upload) printf '2\n' ;;
    bidirectional | idle) printf '3\n' ;;
    *) printf '1\n' ;;
  esac
}

strict_transport_check() {
  local protocol=$1
  local pcap=$2
  if [[ $protocol == h3 ]]; then
    local udp_count
    local tcp_established
    local tcp_payload
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
  else
    if [[ -z $(tshark -r "$pcap" -d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
      -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
      -T fields -e frame.number | sed -n '1p') ]]; then
      printf 'H2 sample has no visible TLS ClientHello\n' >&2
      return 1
    fi
  fi
}

run_reference_sample() {
  local protocol=$1
  local scenario=$2
  local label=$3
  local session_id=$4
  local sample_dir="$private_dir/$session_id"
  local profile="$sample_dir/profile"
  local pcap="$sample_dir/capture.pcapng"
  local log="$sample_dir/firefox.log"
  mkdir -m 0700 -p "$sample_dir"
  make_profile "$profile" "$protocol"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  local path
  path=$(scenario_path "$scenario")
  timeout "$sample_timeout" env -u SSLKEYLOGFILE \
    "${firefox_runtime_env[@]}" "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
    "$REFERENCE_BIN" --headless --new-instance --no-remote \
    --profile "$profile" --window-size 1280,720 \
    --screenshot "$sample_dir/reference.png" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT$path" >"$log" 2>&1 &
  firefox_pid=$!
  set +e
  wait "$firefox_pid"
  local status=$?
  set -e
  firefox_pid=
  stop_capture
  if [[ $status -ne 0 ]]; then
    printf 'reference Firefox sample %s exited with status %s\n' \
      "$session_id" "$status" >&2
    return 1
  fi
  strict_transport_check "$protocol" "$pcap"
  python3 "$INTEGRATION_DIR/camouflage_features.py" extract \
    --pcap "$pcap" --protocol "$protocol" \
    --server-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --scenario "$scenario" \
    --label "$label" --session-id "$session_id" \
    --output "$feature_fragments/$session_id.json"
}

nf_curl() {
  local socks_port=$1
  local method=$2
  local path=$3
  local size=${4:-0}
  if [[ $method == GET ]]; then
    timeout "$sample_timeout" curl --fail --silent --show-error --noproxy '' \
      --socks5-hostname "127.0.0.1:$socks_port" \
      "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT$path" --output /dev/null
  else
    head -c "$size" /dev/zero | timeout "$sample_timeout" \
      curl --fail --silent --show-error --noproxy '' \
      --socks5-hostname "127.0.0.1:$socks_port" \
      --header 'Content-Type: application/octet-stream' --data-binary @- \
      "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT$path" --output /dev/null
  fi
}

run_naivefox_workload() {
  local scenario=$1
  local socks_port=$2
  scenario_parameters "$scenario"
  local page
  page=$(scenario_path "$scenario")
  case $scenario_kind in
    initial)
      nf_curl "$socks_port" GET /camouflage/api
      ;;
    browser_page)
      nf_curl "$socks_port" GET "$page"
      workload_pids=()
      local resource
      for resource in \
        /camouflage/style.css /camouflage/app.js \
        '/camouflage/resource?size=65536' \
        '/camouflage/resource?size=131072' \
        '/camouflage/resource?size=262144' /camouflage/api; do
        nf_curl "$socks_port" GET "$resource" &
        workload_pids+=("$!")
      done
      local pid
      for pid in "${workload_pids[@]}"; do wait "$pid"; done
      workload_pids=()
      ;;
    sequential)
      nf_curl "$socks_port" GET "$page"
      nf_curl "$socks_port" GET /camouflage/api
      sleep 0.1
      nf_curl "$socks_port" GET /camouflage/api
      sleep 0.5
      nf_curl "$socks_port" GET /camouflage/api
      sleep 2
      nf_curl "$socks_port" GET /camouflage/api
      ;;
    concurrent | burst)
      nf_curl "$socks_port" GET "$page"
      workload_pids=()
      for ((i = 0; i < scenario_count; i++)); do
        nf_curl "$socks_port" GET \
          "/camouflage/resource?size=$scenario_size&item=$i" &
        workload_pids+=("$!")
      done
      for pid in "${workload_pids[@]}"; do wait "$pid"; done
      workload_pids=()
      ;;
    bulk_download)
      nf_curl "$socks_port" GET "$page"
      nf_curl "$socks_port" GET "/camouflage/resource?size=$scenario_size"
      ;;
    bulk_upload)
      nf_curl "$socks_port" GET "$page"
      nf_curl "$socks_port" POST /camouflage/upload "$scenario_size"
      ;;
    bidirectional)
      nf_curl "$socks_port" GET "$page"
      nf_curl "$socks_port" POST /camouflage/upload "$scenario_size"
      nf_curl "$socks_port" GET "/camouflage/resource?size=$scenario_size"
      ;;
    idle)
      nf_curl "$socks_port" GET "$page"
      nf_curl "$socks_port" GET /camouflage/api
      python3 -c 'import sys,time; time.sleep(int(sys.argv[1]) / 1000)' \
        "$scenario_idle_ms"
      nf_curl "$socks_port" GET /camouflage/api
      ;;
  esac
}

run_naivefox_sample() {
  local protocol=$1
  local scenario=$2
  local session_id=$3
  local sample_dir="$private_dir/$session_id"
  local profile="$sample_dir/profile"
  local pcap="$sample_dir/capture.pcapng"
  local log="$sample_dir/naivefox.log"
  local socks_port
  local connection_count
  socks_port=$(choose_port)
  connection_count=$(scenario_connections "$scenario")
  mkdir -m 0700 -p "$sample_dir"
  make_profile "$profile" "$protocol"
  start_capture "$pcap" "$sample_dir/dumpcap.log"
  env -u SSLKEYLOGFILE "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" \
    NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
    "$NAIVEFOX_BIN" --profile "$profile" --protocol "$protocol" \
    --socks-listen "127.0.0.1:$socks_port" \
    --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --max-connections "$connection_count" >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  run_naivefox_workload "$scenario" "$socks_port"
  if ! timeout "$sample_timeout" tail --pid="$naivefox_pid" -f /dev/null; then
    printf 'NaiveFox sample %s did not exit after its controlled workload\n' \
      "$session_id" >&2
    return 1
  fi
  wait "$naivefox_pid"
  naivefox_pid=
  stop_capture
  [[ $(rg -c "^Outer protocol: $protocol$" "$log") -eq $connection_count ]]
  [[ $(rg -c '^Padding negotiated: yes$' "$log") -eq $connection_count ]]
  strict_transport_check "$protocol" "$pcap"
  python3 "$INTEGRATION_DIR/camouflage_features.py" extract \
    --pcap "$pcap" --protocol "$protocol" \
    --server-port "$NAIVEFOX_FIXTURE_PROXY_PORT" --scenario "$scenario" \
    --label naivefox --session-id "$session_id" \
    --output "$feature_fragments/$session_id.json"
}

scenario_csv=$(IFS=,; printf '%s' "${scenarios[*]}")
session_counter=0
for protocol in "${protocols[@]}"; do
  "$INTEGRATION_DIR/start.sh" --mode "$protocol"
  run_dir=$(<"$ACTIVE_RUN_FILE")
  source "$run_dir/fixture.env"
  schedule="$private_dir/$protocol-schedule.tsv"
  python3 - "$seed" "$samples_per_cohort" "$scenario_csv" >"$schedule" <<'PY'
import random
import sys

seed = int(sys.argv[1])
count = int(sys.argv[2])
scenarios = sys.argv[3].split(",")
items = []
for label in ("firefox_a", "firefox_b", "naivefox"):
    for index in range(count):
        items.append((label, scenarios[index % len(scenarios)]))
random.Random(seed).shuffle(items)
for label, scenario in items:
    print(label, scenario, sep="\t")
PY
  while IFS=$'\t' read -r label scenario; do
    session_counter=$((session_counter + 1))
    session_id=$(printf '%s_s%06d' "$protocol" "$session_counter")
    printf 'Collecting %s %s %s (%d/%d)\n' \
      "$protocol" "$label" "$scenario" "$session_counter" \
      "$((samples_per_cohort * 3 * ${#protocols[@]}))"
    if [[ $label == naivefox ]]; then
      run_naivefox_sample "$protocol" "$scenario" "$session_id"
    else
      run_reference_sample "$protocol" "$scenario" "$label" "$session_id"
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
  --max-features "$max_features" --iterations "$model_iterations"

reference_version=$(LD_LIBRARY_PATH="$REFERENCE_LIBDIR" "$REFERENCE_BIN" --version 2>/dev/null)
naivefox_version=$(LD_LIBRARY_PATH="$NAIVEFOX_LIBDIR" "$NAIVEFOX_BIN" --version 2>/dev/null)
cat >"$safe_dir/metadata.txt" <<EOF
schema_version=1
revision=$(git -C "$SOURCE_ROOT" rev-parse HEAD)
mode=$mode
seed=$seed
protocol_selection=$protocol_selection
samples_per_cohort=$samples_per_cohort
reference_mode=$capture_mode
os_id=$(source /etc/os-release && printf '%s' "$ID")
os_version=$(source /etc/os-release && printf '%s' "$VERSION_ID")
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
