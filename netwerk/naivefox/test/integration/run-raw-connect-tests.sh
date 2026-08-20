#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

run_dir=
cleanup() {
  local status=$?
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ $status -ne 0 && -n $run_dir ]]; then
    printf 'raw CONNECT fixture failed; sanitized diagnostics: %s\n' \
      "$run_dir/diagnostics.txt" >&2
  fi
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

run_client() {
  local output=$1
  shift
  timeout 30 env "$@" "$OBJDIR/dist/bin/naivefox" \
    --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
    --raw-tunnel-smoke "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    "127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT" >"$output" 2>&1
}

valid_log="$run_dir/raw-valid.log"
run_client "$valid_log" \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS"
rg -q '^Proxy CONNECT status: 200$' "$valid_log"
rg -q '^Outer protocol: h2$' "$valid_log"
rg -q '^Raw tunnel response marker verified$' "$valid_log"

for auth_case in missing invalid; do
  failure_log="$run_dir/raw-$auth_case.log"
  if [[ $auth_case == invalid ]]; then
    if run_client "$failure_log" \
      NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
      NAIVEFOX_PROXY_PASS='deliberately-invalid'; then
      printf '%s proxy credentials unexpectedly succeeded\n' "$auth_case" >&2
      exit 1
    fi
  elif run_client "$failure_log"; then
    printf '%s proxy credentials unexpectedly succeeded\n' "$auth_case" >&2
    exit 1
  fi
  rg -q '^Proxy CONNECT status: 407$' "$failure_log"
  rg -q 'NS_ERROR_PROXY_AUTHENTICATION_FAILED' "$failure_log"
done

if rg -F "$NAIVEFOX_FIXTURE_PASS" \
  "$valid_log" "$run_dir/raw-missing.log" "$run_dir/raw-invalid.log"; then
  printf 'proxy password appeared in client output\n' >&2
  exit 1
fi

printf 'NaiveFox raw H2 CONNECT and authentication tests passed\n'
