#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

protocol=h2
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || $1 != --protocol ]]; then
    printf 'usage: %s [--protocol h2|h3]\n' "$0" >&2
    exit 2
  fi
  protocol=$2
fi
case $protocol in
  h2 | h3) ;;
  *)
    printf 'unsupported robustness-test protocol: %s\n' "$protocol" >&2
    exit 2
    ;;
esac

run_dir=
client_pid=
client_log=
client_logs=()
background_pids=()
cleanup() {
  local status=$?
  local pid
  for pid in "${background_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ $status -ne 0 ]]; then
    {
      for client_log in "${client_logs[@]}"; do
        if [[ -f $client_log ]]; then
          printf '%s\n' "===== $(basename "$client_log") ====="
          cat "$client_log"
        fi
      done
    } | sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" | \
      sanitize_stream '' "${invalid_password:-}" \
      >"$SOURCE_ROOT/artifacts/$protocol-robustness-client-failure.log"
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ $status -ne 0 ]]; then
    printf 'robustness fixture failed; sanitized client log: %s\n' \
      "$SOURCE_ROOT/artifacts/$protocol-robustness-client-failure.log" >&2
  fi
  return "$status"
}
trap cleanup EXIT

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

padding_count() {
  awk '$0 == "Padding negotiated: yes" { count++ } END { print count + 0 }' \
    "$client_log"
}

start_client() {
  local user=$1
  local password=$2
  local max_connections=$3
  local log=$4
  local socks_port=$5
  client_log=$log
  client_logs+=("$log")
  env NAIVEFOX_PROXY_USER="$user" NAIVEFOX_PROXY_PASS="$password" \
    "$OBJDIR/dist/bin/naivefox" \
    --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
    --protocol "$protocol" \
    --socks-listen "127.0.0.1:$socks_port" \
    --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --max-connections "$max_connections" >"$log" 2>&1 &
  client_pid=$!
  for ((i = 0; i < 100; i++)); do
    if rg -q '^SOCKS5 listening on ' "$log"; then
      break
    fi
    if ! kill -0 "$client_pid" 2>/dev/null; then
      printf 'NaiveFox exited before SOCKS readiness\n' >&2
      return 1
    fi
    sleep 0.1
  done
  rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$log"
}

wait_for_client_exit() {
  local description=$1
  if ! timeout 30 tail --pid="$client_pid" -f /dev/null; then
    printf 'NaiveFox did not exit after %s\n' "$description" >&2
    return 1
  fi
  wait "$client_pid"
  client_pid=
}

monitor_rss() {
  local label=$1
  shift
  local baseline_rss
  local current_rss
  local peak_rss
  local workload_pid
  baseline_rss=$(awk '/VmRSS:/ { print $2 }' "/proc/$client_pid/status")
  peak_rss=$baseline_rss
  timeout 90 "$@" &
  workload_pid=$!
  background_pids+=("$workload_pid")
  while kill -0 "$workload_pid" 2>/dev/null; do
    if ! kill -0 "$client_pid" 2>/dev/null; then
      printf 'NaiveFox exited during %s\n' "$label" >&2
      return 1
    fi
    current_rss=$(awk '/VmRSS:/ { print $2 }' "/proc/$client_pid/status")
    if ((current_rss > peak_rss)); then
      peak_rss=$current_rss
    fi
    sleep 0.05
  done
  wait "$workload_pid"
  background_pids=()
  if ((peak_rss - baseline_rss >= 32768)); then
    printf '%s grew VmRSS by %d KiB (limit: <32768 KiB)\n' \
      "$label" "$((peak_rss - baseline_rss))" >&2
    return 1
  fi
}

proxy_flow_count() {
  if [[ $protocol == h3 ]]; then
    # Neqo's UDP socket is not connect(2)-bound to the Caddy peer, so ss lists
    # a wildcard remote endpoint. PID ownership plus the H3-only fixture gives
    # us the exact outer socket count without relying on a missing dport.
    ss -Haunp | awk -v owner="pid=$client_pid," \
      'index($0, owner) { count++ } END { print count + 0 }'
  else
    ss -Htn state established \
      "( dport = :$NAIVEFOX_FIXTURE_PROXY_PORT )" | wc -l
  fi
}

assert_no_secret() {
  local log=$1
  local extra_secret=${2:-}
  if rg -F "$NAIVEFOX_FIXTURE_PASS" "$log"; then
    printf 'proxy credential appeared in client output\n' >&2
    return 1
  fi
  if [[ -n $extra_secret ]] && rg -F "$extra_secret" "$log"; then
    printf 'proxy credential appeared in client output\n' >&2
    return 1
  fi
}

command -v ss >/dev/null
"$INTEGRATION_DIR/start.sh" --mode "$protocol"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

socks_port=$(choose_port)
start_client "$NAIVEFOX_FIXTURE_USER" "$NAIVEFOX_FIXTURE_PASS" 12 \
  "$run_dir/robustness-client.log" "$socks_port"

curl --silent --show-error --fail --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small" \
  --output "$run_dir/warmup.out"
[[ $(<"$run_dir/warmup.out") == 'naivefox-fixture-small' ]]

monitor_rss 'slow download backpressure' \
  python3 "$INTEGRATION_DIR/robustness_client.py" slow-download \
  --socks-port "$socks_port" --target-port "$NAIVEFOX_FIXTURE_HTTP_PORT" \
  --size 33554432
monitor_rss 'slow upload backpressure' \
  python3 "$INTEGRATION_DIR/robustness_client.py" stalled-upload \
  --socks-port "$socks_port" --target-port "$NAIVEFOX_FIXTURE_HTTP_PORT" \
  --size 33554432

python3 "$INTEGRATION_DIR/robustness_client.py" local-disconnect \
  --socks-port "$socks_port" --target-port "$NAIVEFOX_FIXTURE_HTTP_PORT"
python3 "$INTEGRATION_DIR/robustness_client.py" half-close \
  --socks-port "$socks_port" --target-port "$NAIVEFOX_FIXTURE_HTTP_PORT"

curl_socks=(
  --silent --show-error --noproxy ''
  --socks5-hostname "127.0.0.1:$socks_port"
)
if curl "${curl_socks[@]}" --fail \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/early-close?after=64" \
  --output /dev/null; then
  printf 'early-close target unexpectedly completed\n' >&2
  exit 1
fi
if curl "${curl_socks[@]}" --fail --max-time 1 \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=5000" \
  --output /dev/null; then
  printf 'delayed request unexpectedly beat timeout\n' >&2
  exit 1
fi
if curl "${curl_socks[@]}" --fail \
  'http://localhost:9/small' --output /dev/null; then
  printf 'ACL-denied CONNECT unexpectedly succeeded\n' >&2
  exit 1
fi

padding_before=$(padding_count)
concurrent_pids=()
for i in $(seq 1 4); do
  curl "${curl_socks[@]}" --fail \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=2000" \
    >"$run_dir/concurrent-$i.out" 2>"$run_dir/concurrent-$i.err" &
  concurrent_pids+=("$!")
done
background_pids=("${concurrent_pids[@]}")

max_proxy_flows=0
all_streams_ready=0
for ((i = 0; i < 100; i++)); do
  current_flows=$(proxy_flow_count)
  if ((current_flows > max_proxy_flows)); then
    max_proxy_flows=$current_flows
  fi
  if (( $(padding_count) >= padding_before + 4 )); then
    all_streams_ready=1
    break
  fi
  sleep 0.05
done
if ((all_streams_ready == 0)); then
  printf 'concurrent CONNECT streams did not become ready together\n' >&2
  exit 1
fi
current_proxy_flows=$(proxy_flow_count)
if ((current_proxy_flows != 1 || max_proxy_flows != 1)); then
  printf 'expected one pooled %s proxy flow, observed current=%d peak=%d\n' \
    "$protocol" "$current_proxy_flows" "$max_proxy_flows" >&2
  exit 1
fi
for pid in "${concurrent_pids[@]}"; do
  kill -0 "$pid" 2>/dev/null
done

for pid in "${concurrent_pids[@]}"; do
  wait "$pid"
done
background_pids=()
for i in $(seq 1 4); do
  [[ $(<"$run_dir/concurrent-$i.out") == 'naivefox-fixture-small' ]]
done

wait_for_client_exit 'the configured connection limit'
valid_padding_count=$(padding_count)
((valid_padding_count >= 11 && valid_padding_count <= 12))
[[ $(rg -c "^Outer protocol: $protocol$" "$client_log") -eq \
  "$valid_padding_count" ]]
if rg -q '^Padding negotiated: no$' "$client_log"; then
  printf 'fixture tunnel unexpectedly fell back to raw mode\n' >&2
  exit 1
fi
assert_no_secret "$client_log"

invalid_socks_port=$(choose_port)
invalid_password="wrong-$(openssl rand -hex 16)"
start_client "$NAIVEFOX_FIXTURE_USER" "$invalid_password" 1 \
  "$run_dir/invalid-auth-client.log" "$invalid_socks_port"
if curl --silent --show-error --fail --max-time 10 --noproxy '' \
  --socks5-hostname "127.0.0.1:$invalid_socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small" \
  --output /dev/null 2>"$run_dir/invalid-auth-curl.log"; then
  printf 'invalid proxy authentication unexpectedly succeeded\n' >&2
  exit 1
fi
wait_for_client_exit 'the invalid-auth connection failed'
[[ $(padding_count) -eq 0 ]]
assert_no_secret "$client_log" "$invalid_password"

disconnect_socks_port=$(choose_port)
start_client "$NAIVEFOX_FIXTURE_USER" "$NAIVEFOX_FIXTURE_PASS" 1 \
  "$run_dir/proxy-disconnect-client.log" "$disconnect_socks_port"
curl --silent --show-error --fail --max-time 15 --noproxy '' \
  --socks5-hostname "127.0.0.1:$disconnect_socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=5000" \
  --output /dev/null 2>"$run_dir/proxy-disconnect-curl.log" &
disconnect_curl_pid=$!
background_pids=("$disconnect_curl_pid")
for ((i = 0; i < 100; i++)); do
  (( $(padding_count) == 1 )) && break
  kill -0 "$disconnect_curl_pid" 2>/dev/null || {
    printf 'disconnect request ended before tunnel readiness\n' >&2
    exit 1
  }
  sleep 0.05
done
[[ $(padding_count) -eq 1 ]]
[[ $NAIVEFOX_FIXTURE_CADDY_PID =~ ^[0-9]+$ ]]
[[ $(readlink -f "/proc/$NAIVEFOX_FIXTURE_CADDY_PID/exe") == \
  $(readlink -f "$CADDY_BIN") ]]
kill -KILL "$NAIVEFOX_FIXTURE_CADDY_PID"
if wait "$disconnect_curl_pid"; then
  printf 'request unexpectedly survived proxy disconnect\n' >&2
  exit 1
fi
background_pids=()
wait_for_client_exit 'the proxy disconnected'
assert_no_secret "$client_log"

printf '%s\n' \
  "NaiveFox robustness, backpressure, lifecycle, and $protocol multiplexing tests passed"
