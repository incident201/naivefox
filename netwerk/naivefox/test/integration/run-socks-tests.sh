#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

run_dir=
client_pid=
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ $status -ne 0 && -n $run_dir ]]; then
    printf 'SOCKS fixture failed; sanitized diagnostics: %s\n' \
      "$run_dir/diagnostics.txt" >&2
  fi
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

socks_port=$(python3 -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
client_log="$run_dir/socks-client.log"
export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

env NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
  "$OBJDIR/dist/bin/naivefox" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --max-connections 2 >"$client_log" 2>&1 &
client_pid=$!
for ((i = 0; i < 100; i++)); do
  rg -q '^SOCKS5 listening on ' "$client_log" && break
  kill -0 "$client_pid" 2>/dev/null || {
    cat "$client_log" >&2
    exit 1
  }
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"

http_body=$(curl --silent --show-error --fail --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $http_body == *naivefox-fixture-small* ]]

https_body=$(curl --silent --show-error --fail --noproxy '' \
  --socks5-hostname "127.0.0.1:$socks_port" \
  --cacert "$NAIVEFOX_FIXTURE_CA" \
  "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/small")
[[ $https_body == *naivefox-fixture-small* ]]

if ! timeout 30 tail --pid="$client_pid" -f /dev/null; then
  printf 'NaiveFox did not exit after the configured connection limit\n' >&2
  exit 1
fi
wait "$client_pid"
client_pid=

if rg -F "$NAIVEFOX_FIXTURE_PASS" "$client_log"; then
  printf 'proxy password appeared in client output\n' >&2
  exit 1
fi

printf 'NaiveFox SOCKS5 HTTP and HTTPS tests passed\n'
