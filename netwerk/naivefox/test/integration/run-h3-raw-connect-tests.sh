#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

run_dir=
client_logs=()
cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    {
      if [[ -n $run_dir && -f $run_dir/caddy.log ]]; then
        printf '===== caddy.log =====\n'
        cat "$run_dir/caddy.log"
      fi
      if [[ -n $run_dir && -f $run_dir/target.log ]]; then
        printf '===== target.log =====\n'
        cat "$run_dir/target.log"
      fi
      local client_log
      for client_log in "${client_logs[@]}"; do
        if [[ -f $client_log ]]; then
          printf '===== %s =====\n' "$(basename "$client_log")"
          cat "$client_log"
        fi
      done
    } | sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" \
      >"$SOURCE_ROOT/artifacts/h3-raw-client-failure.log"
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ $status -ne 0 ]]; then
    printf 'raw H3 CONNECT fixture failed; sanitized client log: %s\n' \
      "$SOURCE_ROOT/artifacts/h3-raw-client-failure.log" >&2
  fi
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh" --mode h3
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

[[ $NAIVEFOX_FIXTURE_MODE == h3 ]]
ss -H -lun "sport = :$NAIVEFOX_FIXTURE_PROXY_PORT" |
  grep -F "127.0.0.1:$NAIVEFOX_FIXTURE_PROXY_PORT" >/dev/null
[[ -z $(ss -H -ltn "sport = :$NAIVEFOX_FIXTURE_PROXY_PORT") ]]

export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

run_client() {
  local output=$1
  shift
  timeout 30 env "$@" "$OBJDIR/dist/bin/naivefox" \
    --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
    --protocol h3 \
    --raw-tunnel-smoke "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    "127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT" >"$output" 2>&1
}

valid_log="$run_dir/raw-h3-valid.log"
client_logs+=("$valid_log")
valid_status=0
run_client "$valid_log" \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" || valid_status=$?
if [[ $valid_status -ne 0 ]]; then
  printf 'client_exit_status=%s\n' "$valid_status" >>"$valid_log"
  exit 1
fi
rg -q '^Proxy CONNECT status: 200$' "$valid_log"
rg -q '^Outer protocol: h3$' "$valid_log"
rg -q '^Raw tunnel response marker verified$' "$valid_log"

invalid_log="$run_dir/raw-h3-invalid.log"
client_logs+=("$invalid_log")
if run_client "$invalid_log" \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS=deliberately-invalid; then
  printf 'invalid H3 proxy credentials unexpectedly succeeded\n' >&2
  exit 1
fi
rg -q '^Proxy CONNECT status: 407$' "$invalid_log"
rg -q 'NS_ERROR_PROXY_AUTHENTICATION_FAILED' "$invalid_log"

# Gecko canonicalizes an explicit :80 on the synthetic HTTP carrier. Keep a
# negative-path regression which proves that this still reaches CONNECT rather
# than being rejected as a missing target port.
default_port_log="$run_dir/raw-h3-default-port.log"
client_logs+=("$default_port_log")
if timeout 30 env NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS=deliberately-invalid \
  "$OBJDIR/dist/bin/naivefox" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --protocol h3 \
  --raw-tunnel-smoke "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  "localhost:80" >"$default_port_log" 2>&1; then
  printf 'default-port invalid credentials unexpectedly succeeded\n' >&2
  exit 1
fi
rg -q '^Outer protocol: h3$' "$default_port_log"
rg -q '^Proxy CONNECT status: (200|407)$' "$default_port_log"
! rg -q '0x80070057|NS_ERROR_INVALID_ARG' "$default_port_log"

if rg -F "$NAIVEFOX_FIXTURE_PASS" "$valid_log" "$invalid_log" \
  "$default_port_log"; then
  printf 'proxy password appeared in H3 client output\n' >&2
  exit 1
fi

printf 'NaiveFox strict raw H3 CONNECT and authentication tests passed\n'
