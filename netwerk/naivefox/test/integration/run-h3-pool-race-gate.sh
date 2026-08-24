#!/usr/bin/env bash

set -euo pipefail
umask 077

# A narrow diagnostic for the H3 connection-pool startup race. It compares two
# simultaneous requests with the outer-session gate disabled and enabled. Only
# the outer Caddy UDP port is captured; successful captures and logs are deleted.

# shellcheck source=netwerk/naivefox/test/integration/common.sh
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

for tool in dumpcap tshark curl getcap openssl python3 rg; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required H3 pool-race tool not found: %s\n' "$tool" >&2
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
NAIVEFOX_BIN="${NAIVEFOX_POOL_GATE_BIN:-${NAIVEFOX_CAPTURE_NAIVEFOX_BIN:-$BIN/naivefox}}"
NAIVEFOX_LIBDIR="${NAIVEFOX_POOL_GATE_LIBDIR:-${NAIVEFOX_CAPTURE_NAIVEFOX_LIBDIR:-$BIN}}"
for artifact in "$NAIVEFOX_BIN" "$NAIVEFOX_LIBDIR/libssl3.so" \
                "$NAIVEFOX_LIBDIR/libxul.so"; do
  [[ -f $artifact ]] || {
    printf 'required NaiveFox artifact is missing: %s\n' "$artifact" >&2
    exit 1
  }
done

"$INTEGRATION_DIR/start.sh" --mode h3
run_dir=$(<"$ACTIVE_RUN_FILE")
# shellcheck disable=SC1090,SC1091
source "$run_dir/fixture.env"
[[ $NAIVEFOX_FIXTURE_MODE == h3 ]]

gate_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
private_dir="$STATE_ROOT/h3-pool-race/$gate_id"
safe_dir="$STATE_ROOT/h3-pool-race-safe/$gate_id"
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-pool-race.XXXXXX")
mkdir -p "$private_dir" "$safe_dir"
chmod 0700 "$private_dir" "$safe_dir"
chmod 0700 "$capture_stage_dir"

capture_pid=
capture_stage_pcap=
capture_pcap=
capture_log=
naivefox_pid=
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

stop_capture() {
  [[ -n $capture_pid ]] || return 0
  local was_running=0
  local status=0
  if kill -0 "$capture_pid" 2>/dev/null; then
    was_running=1
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  if [[ $was_running -ne 1 ]]; then
    printf 'dumpcap stopped before the pool-race workload completed\n' >&2
    status=1
  fi
  if ! python3 "$INTEGRATION_DIR/camouflage_capture_health.py" "$capture_log"; then
    status=1
  fi
  if [[ -s $capture_stage_pcap ]]; then
    # WSL's `any` interface observes both loopback copies. Keep only the
    # transmit copy so each outer QUIC packet is counted once.
    if [[ -n $(tshark -r "$capture_stage_pcap" -Y 'sll.pkttype==4' \
      -T fields -e frame.number 2>/dev/null | sed -n '1p') ]]; then
      tshark -r "$capture_stage_pcap" -Y 'sll.pkttype==4' \
        -w "$capture_pcap" >/dev/null 2>&1
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
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  rm -rf -- "$capture_stage_dir"
  if [[ $status -eq 0 && $success -eq 1 ]]; then
    case $private_dir in
      "$STATE_ROOT"/h3-pool-race/*) rm -rf -- "$private_dir" ;;
      *) printf 'refusing unexpected private path: %s\n' "$private_dir" >&2 ;;
    esac
  else
    case $safe_dir in
      "$STATE_ROOT"/h3-pool-race-safe/*) rm -rf -- "$safe_dir" ;;
    esac
    printf 'H3 pool-race gate failed; private diagnostics preserved at %s\n' \
      "$private_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

wait_for_log() {
  local pid=$1
  local log=$2
  local pattern=$3
  for ((i = 0; i < 150; i++)); do
    rg -q "$pattern" "$log" && return 0
    kill -0 "$pid" 2>/dev/null || {
      printf 'NaiveFox exited before readiness marker: %s\n' "$pattern" >&2
      return 1
    }
    sleep 0.1
  done
  printf 'timed out waiting for NaiveFox readiness\n' >&2
  return 1
}

start_capture() {
  local label=$1
  capture_stage_pcap="$capture_stage_dir/$label.raw.pcapng"
  capture_pcap="$private_dir/$label.pcapng"
  capture_log="$private_dir/$label-dumpcap.log"
  : >"$capture_log"
  dumpcap -q -i any \
    -f "udp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:30 -a filesize:16384 \
    -w "$capture_stage_pcap" >"$capture_log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      printf 'dumpcap exited before capture readiness\n' >&2
      return 1
    }
    if rg -q '^Capturing on ' "$capture_log" && rg -q '^File: ' "$capture_log"; then
      return 0
    fi
    sleep 0.1
  done
  printf 'timed out waiting for dumpcap readiness\n' >&2
  return 1
}

run_curls_together() {
  local label=$1
  local requests=$2
  local socks_port=$3
  local release="$private_dir/$label-release"
  local -a worker_pids=()
  for ((request = 1; request <= requests; request++)); do
    (
      while [[ ! -e $release ]]; do sleep 0.001; done
      timeout 20 curl --fail --silent --show-error --noproxy '' \
        --socks5-hostname "127.0.0.1:$socks_port" \
        "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=1000&gate=$label&request=$request" \
        --output /dev/null
    ) >"$private_dir/$label-curl-$request.log" 2>&1 &
    worker_pids+=("$!")
  done
  # Give every worker time to reach the same read-only release condition.
  sleep 0.1
  : >"$release"
  local worker_status=0
  for worker_pid in "${worker_pids[@]}"; do
    wait "$worker_pid" || worker_status=1
  done
  [[ $worker_status -eq 0 ]]
}

measure_case() {
  local label=$1
  local arm=$2
  local expected_connections=$3
  local requests=2
  local socks_port profile config log pcap
  socks_port=$(choose_port)
  profile="$private_dir/$label-profile"
  config="$private_dir/$label-config.json"
  log="$private_dir/$label-naivefox.log"
  pcap="$private_dir/$label.pcapng"
  mkdir -m 0700 "$profile"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$profile/"
  : >"$log"

  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
    --output "$config" --arm "$arm" --protocol h3 \
    --socks-port "$socks_port" --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT"
  start_capture "$label"
  env -u SSLKEYLOGFILE -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
    LD_LIBRARY_PATH="$NAIVEFOX_LIBDIR" \
    NAIVEFOX_PROFILE="$profile" \
    "$NAIVEFOX_BIN" "$config" >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  run_curls_together "$label" "$requests" "$socks_port"
  sleep 0.25
  stop_capture
  stop_pid "$naivefox_pid"
  naivefox_pid=

  [[ -s $pcap ]]
  local outer_count padding_count tcp_count connection_count
  outer_count=$(rg -c '^Outer protocol: h3$' "$log" || true)
  padding_count=$(rg -c '^Padding negotiated: yes$' "$log" || true)
  [[ $outer_count -eq $requests ]]
  [[ $padding_count -eq $requests ]]
  if rg -q ' preamble result=' "$log"; then
    printf '%s case unexpectedly ran a preamble\n' "$label" >&2
    return 1
  fi
  if rg -q -e '^Outer protocol: h2$' -e '^Padding negotiated: no$' "$log"; then
    printf '%s case used an unexpected outer protocol or padding mode\n' \
      "$label" >&2
    return 1
  fi
  tcp_count=$(tshark -r "$pcap" \
    -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT" -T fields -e frame.number | wc -l)
  [[ $tcp_count -eq 0 ]]

  tshark -r "$pcap" -d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
    -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && quic" \
    -T fields -E occurrence=f -e quic.connection.number \
    >"$private_dir/$label-quic-connections.txt"
  connection_count=$(sed '/^$/d' "$private_dir/$label-quic-connections.txt" |
    sort -u | wc -l)
  if [[ $connection_count -ne $expected_connections ]]; then
    printf '%s case has %s QUIC connections, expected %s\n' \
      "$label" "$connection_count" "$expected_connections" >&2
    return 1
  fi
  {
    printf '%s_arm=%s\n' "$label" "$arm"
    printf '%s_requests=%s\n' "$label" "$requests"
    printf '%s_outer_h3_events=%s\n' "$label" "$outer_count"
    printf '%s_quic_connections=%s\n' "$label" "$connection_count"
  } >>"$safe_dir/summary.txt"
}

: >"$safe_dir/summary.txt"
measure_case off_simultaneous off 2
measure_case gate_simultaneous gate 1

{
  printf 'verdict=outer_session_gate_collapses_startup_race\n'
  printf 'capture_scope=outer_udp_port_only\n'
  printf 'sensitive_artifacts_retained=no\n'
} >>"$safe_dir/summary.txt"

success=1
printf 'H3 pool-race gate passed; safe summary: %s\n' "$safe_dir/summary.txt"
cat "$safe_dir/summary.txt"
