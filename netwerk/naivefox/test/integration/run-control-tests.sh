#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

cleanup() {
  status=$?
  if [[ $status -ne 0 && -f "$ACTIVE_RUN_FILE" ]]; then
    run_dir=$(<"$ACTIVE_RUN_FILE")
    if [[ -f "$run_dir/fixture.env" ]]; then
      source "$run_dir/fixture.env"
      {
        printf 'control test failed\n'
        [[ -f "$run_dir/diagnostics.txt" ]] && cat "$run_dir/diagnostics.txt"
        for log in caddy target; do
          if [[ -s "$run_dir/$log.log" ]]; then
            printf '\n[%s.log]\n' "$log"
            tail -n 100 "$run_dir/$log.log"
          fi
        done
      } | sanitize_stream "$NAIVEFOX_FIXTURE_USER" "$NAIVEFOX_FIXTURE_PASS" >&2
    fi
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$INTEGRATION_DIR/start.sh"
RUN_DIR=$(<"$ACTIVE_RUN_FILE")
source "$RUN_DIR/fixture.env"

proxy="https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT"
proxy_options=(
  --silent
  --show-error
  --noproxy ""
  --connect-timeout 3
  --max-time 20
  --proxy "$proxy"
  --proxy-cacert "$NAIVEFOX_FIXTURE_CA"
  --proxy-http2
)
auth_options=(--proxy-user "$NAIVEFOX_FIXTURE_USER:$NAIVEFOX_FIXTURE_PASS")

expected='naivefox-fixture-small'
actual=$(curl "${proxy_options[@]}" "${auth_options[@]}" --fail-with-body \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $actual == "$expected" ]]

actual=$(curl "${proxy_options[@]}" "${auth_options[@]}" --fail-with-body \
  --cacert "$NAIVEFOX_FIXTURE_CA" \
  "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/small")
[[ $actual == "$expected" ]]

if curl --silent --output /dev/null --connect-timeout 2 --max-time 3 \
  "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/"; then
  printf 'proxy certificate unexpectedly validates against system trust\n' >&2
  exit 1
fi

missing_status=$(curl "${proxy_options[@]}" --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $missing_status == 407 ]]
invalid_status=$(curl "${proxy_options[@]}" --proxy-user 'invalid:invalid' \
  --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $invalid_status == 407 ]]
denied_status=$(curl "${proxy_options[@]}" "${auth_options[@]}" \
  --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:1/")
[[ $denied_status == 403 ]]

openssl s_client -connect "127.0.0.1:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  -servername localhost -alpn h2 -CAfile "$NAIVEFOX_FIXTURE_CA" </dev/null 2>/dev/null |
  rg -q '^ALPN protocol: h2$'

large_file="$RUN_DIR/large.bin"
curl "${proxy_options[@]}" "${auth_options[@]}" --fail-with-body \
  --output "$large_file" \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=3145728"
[[ $(wc -c <"$large_file") -eq 3145728 ]]
large_hash=$(sha256sum "$large_file" | cut -d' ' -f1)
[[ $large_hash == a1feacf0d812ba4d0b0e463ed45bbd583cea1de55c54693116754b30b5794745 ]]

upload_file="$RUN_DIR/upload.bin"
head -c 2097152 /dev/zero >"$upload_file"
upload_response=$(curl "${proxy_options[@]}" "${auth_options[@]}" --fail-with-body \
  --data-binary "@$upload_file" \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/upload")
python3 -c \
  'import json,sys; d=json.loads(sys.argv[1]); assert d == {"bytes": 2097152, "sha256": "5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee"}' \
  "$upload_response"

actual=$(curl "${proxy_options[@]}" "${auth_options[@]}" --fail-with-body \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=25")
[[ $actual == "$expected" ]]
if curl "${proxy_options[@]}" "${auth_options[@]}" --output /dev/null \
  "http://127.0.0.1:$NAIVEFOX_FIXTURE_HTTP_PORT/early-close?after=64" \
  2>"$RUN_DIR/expected-early-close.log"; then
  printf 'early-close endpoint unexpectedly completed\n' >&2
  exit 1
fi

source "$INTEGRATION_DIR/common.sh"
find_certutil
run_certutil -L -d "sql:$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  -n 'NaiveFox Fixture Root' >/dev/null
if run_certutil -L -d "sql:$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" \
  -n 'NaiveFox Fixture Root' >/dev/null 2>&1; then
  printf 'untrusted NSS profile contains fixture root\n' >&2
  exit 1
fi

caddy_pid=$NAIVEFOX_FIXTURE_CADDY_PID
target_pid=$NAIVEFOX_FIXTURE_TARGET_PID
"$INTEGRATION_DIR/stop.sh" --quiet
trap - EXIT INT TERM
if kill -0 "$caddy_pid" 2>/dev/null || kill -0 "$target_pid" 2>/dev/null; then
  printf 'fixture child process survived cleanup\n' >&2
  exit 1
fi

printf 'NaiveFox Caddy fixture control tests passed\n'
