#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

runtime=${NAIVEFOX_RUNTIME:-$OBJDIR/dist/bin/naivefox}
[[ -x $runtime ]] || {
  printf 'NaiveFox binary not found; run ./mach build first\n' >&2
  exit 1
}

client_pid=
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

runtime_environment=(env -u LD_PRELOAD)
if [[ -n ${NAIVEFOX_RUNTIME:-} ]]; then
  runtime_environment+=(-u LD_LIBRARY_PATH)
else
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

free_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

expect_invalid_cert_file() {
  local mode=$1 config=$2 listen_port=$3 profile=$4 path=$5 output=$6
  "${runtime_environment[@]}" SSL_CERT_FILE="$path" \
    NAIVEFOX_PROFILE="$profile" MOZ_CRASHREPORTER_DISABLE=1 \
    "$runtime" "$config" >"$output" 2>&1 &
  local invalid_pid=$!
  for ((i = 0; i < 100; i++)); do
    if ! kill -0 "$invalid_pid" 2>/dev/null ||
      [[ $(ps -o stat= -p "$invalid_pid" 2>/dev/null) == Z* ]]; then
      break
    fi
    sleep 0.1
  done
  if kill -0 "$invalid_pid" 2>/dev/null &&
    [[ $(ps -o stat= -p "$invalid_pid" 2>/dev/null) != Z* ]]; then
    kill "$invalid_pid" 2>/dev/null || true
    wait "$invalid_pid" 2>/dev/null || true
    printf 'invalid SSL_CERT_FILE did not fail promptly for %s\n' "$mode" >&2
    return 1
  fi
  local invalid_status=0
  if wait "$invalid_pid"; then
    invalid_status=0
  else
    invalid_status=$?
  fi
  [[ $invalid_status -ne 0 ]] || {
    printf 'invalid SSL_CERT_FILE unexpectedly allowed %s to start\n' "$mode" >&2
    return 1
  }
  [[ -z $(ss -Hltn "sport = :$listen_port") ]]
}

run_case() {
  local mode=$1
  "$INTEGRATION_DIR/start.sh" --mode "$mode"
  local run_dir
  run_dir=$(<"$ACTIVE_RUN_FILE")
  source "$run_dir/fixture.env"

  local listen_port
  listen_port=$(free_port)
  local config="$run_dir/ssl-cert-file-$mode.json"
  CONFIG_PATH=$config PROXY_SCHEME=$([[ $mode == h3 ]] && printf quic || printf https) \
    PROXY_PORT=$NAIVEFOX_FIXTURE_PROXY_PORT PROXY_USER=$NAIVEFOX_FIXTURE_USER \
    PROXY_PASS=$NAIVEFOX_FIXTURE_PASS LISTEN_PORT=$listen_port \
    python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import quote

proxy = (
    f"{os.environ['PROXY_SCHEME']}://"
    f"{quote(os.environ['PROXY_USER'], safe='')}:"
    f"{quote(os.environ['PROXY_PASS'], safe='')}@"
    f"localhost:{os.environ['PROXY_PORT']}"
)
config = {
    "listen": f"socks://127.0.0.1:{os.environ['LISTEN_PORT']}",
    "proxy": proxy,
    "log": "",
}
path = Path(os.environ["CONFIG_PATH"])
path.write_text(json.dumps(config), encoding="utf-8")
path.chmod(0o600)
PY

  find_certutil
  if run_certutil -L -d "sql:$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" \
    -n 'NaiveFox Fixture Root' >/dev/null 2>&1; then
    printf 'untrusted fixture profile unexpectedly contains the fixture root\n' >&2
    return 1
  fi

  expect_invalid_cert_file "$mode" "$config" "$listen_port" \
    "$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" "$run_dir/missing.pem" \
    "$run_dir/ssl-cert-file-invalid.log"
  expect_invalid_cert_file "$mode" "$config" "$listen_port" \
    "$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" relative.pem \
    "$run_dir/ssl-cert-file-relative.log"
  printf '%s\n' 'not a certificate bundle' >"$run_dir/malformed.pem"
  expect_invalid_cert_file "$mode" "$config" "$listen_port" \
    "$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" "$run_dir/malformed.pem" \
    "$run_dir/ssl-cert-file-malformed.log"
  set +e
  "${runtime_environment[@]}" -u SSL_CERT_FILE \
    NAIVEFOX_PROFILE="$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" \
    MOZ_CRASHREPORTER_DISABLE=1 "$runtime" "$config" \
    >"$run_dir/ssl-cert-file-unset.log" 2>&1 &
  client_pid=$!
  for ((i = 0; i < 100; i++)); do
    [[ -n $(ss -Hltn "sport = :$listen_port") ]] && break
    kill -0 "$client_pid" 2>/dev/null || break
    sleep 0.1
  done
  [[ -n $(ss -Hltn "sport = :$listen_port") ]]
  curl --silent --show-error --max-time 10 --noproxy '' \
    --socks5-hostname "127.0.0.1:$listen_port" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small" \
    >"$run_dir/ssl-cert-file-unset-response" 2>/dev/null
  unset_status=$?
  kill "$client_pid" 2>/dev/null || true
  wait "$client_pid" 2>/dev/null || true
  client_pid=
  set -e
  [[ $unset_status -ne 0 ]] || {
    printf 'untrusted profile unexpectedly accepted %s without SSL_CERT_FILE\n' "$mode" >&2
    return 1
  }
  listen_port=$(free_port)
  sed "s/127.0.0.1:[0-9]*/127.0.0.1:$listen_port/" "$config" >"$config.tmp"
  mv -- "$config.tmp" "$config"
  "${runtime_environment[@]}" SSL_CERT_FILE="$NAIVEFOX_FIXTURE_CA" \
    NAIVEFOX_PROFILE="$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" \
    MOZ_CRASHREPORTER_DISABLE=1 "$runtime" "$config" \
    >"$run_dir/ssl-cert-file-set.log" 2>&1 &
  client_pid=$!
  for ((i = 0; i < 100; i++)); do
    [[ -n $(ss -Hltn "sport = :$listen_port") ]] && break
    kill -0 "$client_pid" 2>/dev/null || break
    sleep 0.1
  done
  [[ -n $(ss -Hltn "sport = :$listen_port") ]]
  [[ $(curl --silent --show-error --fail --max-time 20 --noproxy '' \
    --socks5-hostname "127.0.0.1:$listen_port" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small") == \
    naivefox-fixture-small ]]
  kill "$client_pid" 2>/dev/null || true
  wait "$client_pid" 2>/dev/null || true
  client_pid=
  if run_certutil -L -d "sql:$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" \
    -n 'NaiveFox Fixture Root' >/dev/null 2>&1; then
    printf 'SSL_CERT_FILE trust was persisted to the NSS profile for %s\n' "$mode" >&2
    return 1
  fi
  if [[ -f $NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE/prefs.js ]] &&
    rg -q 'network\.http\.http3\.disable_when_third_party_roots_found' \
      "$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE/prefs.js"; then
    printf 'SSL_CERT_FILE H3 preference was persisted to the NSS profile for %s\n' \
      "$mode" >&2
    return 1
  fi

  "$INTEGRATION_DIR/stop.sh" --quiet
  printf 'SSL_CERT_FILE temporary trust passed for %s\n' "$mode"
}

run_case h2
run_case h3
printf '%s\n' 'NaiveFox SSL_CERT_FILE integration tests passed'
