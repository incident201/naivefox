#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

run_dir=
client_pid=
probe_pid=
client_logs=()
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ -n $probe_pid ]] && kill -0 "$probe_pid" 2>/dev/null; then
    kill "$probe_pid" 2>/dev/null || true
    wait "$probe_pid" 2>/dev/null || true
  fi
  if [[ $status -ne 0 ]]; then
    local log
    for log in "${client_logs[@]}"; do
      [[ -f $log ]] && cat "$log"
    done | sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" \
      >"$SOURCE_ROOT/artifacts/auto-protocol-client-failure.log"
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

start_client() {
  local log=$1
  local socks_port=$2
  local user=$3
  local password=$4
  client_logs+=("$log")
  env NAIVEFOX_PROXY_USER="$user" NAIVEFOX_PROXY_PASS="$password" \
    "$OBJDIR/dist/bin/naivefox" \
    --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
    --protocol auto \
    --socks-listen "127.0.0.1:$socks_port" \
    --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --max-connections 1 >"$log" 2>&1 &
  client_pid=$!
  for ((i = 0; i < 150; i++)); do
    rg -q '^SOCKS5 listening on ' "$log" && break
    kill -0 "$client_pid" 2>/dev/null || return 1
    sleep 0.1
  done
  rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$log"
}

wait_client() {
  timeout 30 tail --pid="$client_pid" -f /dev/null
  wait "$client_pid"
  client_pid=
}

export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

# H3 has no UDP listener, so Auto must retry once through the H2-only proxy.
"$INTEGRATION_DIR/start.sh" --mode h2
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
socks_port=$(choose_port)
h2_log="$run_dir/auto-h2-fallback.log"
start_client "$h2_log" "$socks_port" "$NAIVEFOX_FIXTURE_USER" \
  "$NAIVEFOX_FIXTURE_PASS"
body=$(curl --silent --show-error --fail --max-time 25 --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $body == 'naivefox-fixture-small' ]]
wait_client
[[ $(rg -c '^Outer protocol: h2$' "$h2_log") -eq 1 ]]
[[ $(rg -c '^Padding negotiated: yes$' "$h2_log") -eq 1 ]]
! rg -q '^Outer protocol: h3$' "$h2_log"

raw_h2_log="$run_dir/auto-raw-h2-fallback.log"
client_logs+=("$raw_h2_log")
timeout 20 env NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
  "$OBJDIR/dist/bin/naivefox" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" --protocol auto \
  --raw-tunnel-smoke "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  "127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT" >"$raw_h2_log" 2>&1
rg -q '^Proxy CONNECT status: 200$' "$raw_h2_log"
rg -q '^Outer protocol: h2$' "$raw_h2_log"
rg -q '^Raw tunnel response marker verified$' "$raw_h2_log"
"$INTEGRATION_DIR/stop.sh" --quiet

# H3 success and logical H3 failures must never touch a simultaneously
# available TCP route on the same numeric proxy port.
"$INTEGRATION_DIR/start.sh" --mode h3
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
probe_ready="$run_dir/tcp-probe.ready"
probe_accepted="$run_dir/tcp-probe.accepted"
python3 "$INTEGRATION_DIR/tcp_accept_probe.py" \
  --port "$NAIVEFOX_FIXTURE_PROXY_PORT" --ready "$probe_ready" \
  --accepted "$probe_accepted" &
probe_pid=$!
for ((i = 0; i < 100; i++)); do
  [[ -e $probe_ready ]] && break
  kill -0 "$probe_pid" 2>/dev/null || exit 1
  sleep 0.05
done
[[ -e $probe_ready ]]

socks_port=$(choose_port)
h3_log="$run_dir/auto-h3-success.log"
start_client "$h3_log" "$socks_port" "$NAIVEFOX_FIXTURE_USER" \
  "$NAIVEFOX_FIXTURE_PASS"
body=$(curl --silent --show-error --fail --max-time 20 --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $body == 'naivefox-fixture-small' ]]
wait_client
[[ $(rg -c '^Outer protocol: h3$' "$h3_log") -eq 1 ]]

raw_h3_log="$run_dir/auto-raw-h3-success.log"
client_logs+=("$raw_h3_log")
timeout 20 env NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
  "$OBJDIR/dist/bin/naivefox" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" --protocol auto \
  --raw-tunnel-smoke "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  "127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT" >"$raw_h3_log" 2>&1
rg -q '^Proxy CONNECT status: 200$' "$raw_h3_log"
rg -q '^Outer protocol: h3$' "$raw_h3_log"
rg -q '^Raw tunnel response marker verified$' "$raw_h3_log"

socks_port=$(choose_port)
auth_log="$run_dir/auto-h3-auth-failure.log"
start_client "$auth_log" "$socks_port" "$NAIVEFOX_FIXTURE_USER" \
  deliberately-invalid
if curl --silent --show-error --fail --max-time 15 --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small" --output /dev/null; then
  printf 'invalid authentication unexpectedly succeeded in auto mode\n' >&2
  exit 1
fi
wait_client
! rg -q '^Outer protocol:' "$auth_log"

socks_port=$(choose_port)
target_log="$run_dir/auto-h3-target-failure.log"
start_client "$target_log" "$socks_port" "$NAIVEFOX_FIXTURE_USER" \
  "$NAIVEFOX_FIXTURE_PASS"
if curl --silent --show-error --fail --max-time 15 --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  'http://localhost:1/' --output /dev/null; then
  printf 'denied target unexpectedly succeeded in auto mode\n' >&2
  exit 1
fi
wait_client
! rg -q '^Outer protocol: h2$' "$target_log"

sleep 0.2
[[ ! -s $probe_accepted ]]
kill "$probe_pid"
wait "$probe_pid"
probe_pid=

printf '%s\n' 'NaiveFox auto H3 preference and bounded H2 fallback tests passed'
