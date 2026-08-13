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
    printf 'unsupported padded-test protocol: %s\n' "$protocol" >&2
    exit 2
    ;;
esac

run_dir=
client_pid=
client_log=
runtime=${NAIVEFOX_RUNTIME:-}
expected_runtime_dir=${NAIVEFOX_EXPECT_RUNTIME_DIR:-}
external_runtime=false
if [[ -n $runtime ]]; then
  if [[ $runtime != /* || ! -x $runtime || -z $expected_runtime_dir ||
        $expected_runtime_dir != /* || ! -d $expected_runtime_dir ]]; then
    printf 'external runtime and expected directory must be absolute and executable\n' >&2
    exit 2
  fi
  external_runtime=true
else
  runtime="$OBJDIR/dist/bin/naivefox"
fi
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ $status -ne 0 && -n $client_log && -f $client_log ]]; then
    sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" <"$client_log" \
      >"$SOURCE_ROOT/artifacts/$protocol-padded-client-failure.log"
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ $status -ne 0 ]]; then
    printf 'padded SOCKS fixture failed; sanitized client log: %s\n' \
      "$SOURCE_ROOT/artifacts/$protocol-padded-client-failure.log" >&2
  fi
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh" --mode "$protocol"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

socks_port=$(python3 -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
client_log="$run_dir/padded-client.log"
export MOZ_CRASHREPORTER_DISABLE=1

runtime_environment=(env)
if $external_runtime; then
  runtime_environment+=(
    -u LD_LIBRARY_PATH
    -u LD_PRELOAD
    -u SSLKEYLOGFILE
  )
else
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

"${runtime_environment[@]}" \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" "$runtime" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --protocol "$protocol" \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --max-connections 6 >"$client_log" 2>&1 &
client_pid=$!
for ((i = 0; i < 100; i++)); do
  rg -q '^SOCKS5 listening on ' "$client_log" && break
  kill -0 "$client_pid" 2>/dev/null || {
    printf 'NaiveFox exited before SOCKS readiness\n' >&2
    exit 1
  }
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"

if $external_runtime; then
  expected_executable=$(readlink -f "$expected_runtime_dir/naivefox")
  actual_executable=$(readlink -f "/proc/$client_pid/exe")
  if [[ $actual_executable != "$expected_executable" ]]; then
    printf 'external test process did not execute the staged binary\n' >&2
    exit 1
  fi
  if grep -Fq "$OBJDIR/" "/proc/$client_pid/maps" ||
     grep -Fq "$SOURCE_ROOT/" "/proc/$client_pid/maps"; then
    printf 'external runtime mapped a build-tree or source-tree file\n' >&2
    exit 1
  fi
fi

curl_socks=(
  --silent --show-error --fail --noproxy ''
  --socks5-hostname "127.0.0.1:$socks_port"
)
expected='naivefox-fixture-small'

actual=$(curl "${curl_socks[@]}" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $actual == "$expected" ]]

actual=$(curl "${curl_socks[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
  "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/small")
[[ $actual == "$expected" ]]

large_file="$run_dir/padded-large.bin"
curl "${curl_socks[@]}" --output "$large_file" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=3145728"
[[ $(wc -c <"$large_file") -eq 3145728 ]]
[[ $(sha256sum "$large_file" | cut -d' ' -f1) == \
  a1feacf0d812ba4d0b0e463ed45bbd583cea1de55c54693116754b30b5794745 ]]

upload_file="$run_dir/padded-upload.bin"
head -c 2097152 /dev/zero >"$upload_file"
upload_response=$(curl "${curl_socks[@]}" --data-binary "@$upload_file" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/upload")
python3 -c \
  'import json,sys; d=json.loads(sys.argv[1]); assert d == {"bytes": 2097152, "sha256": "5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee"}' \
  "$upload_response"

for _ in 1 2; do
  actual=$(curl "${curl_socks[@]}" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
  [[ $actual == "$expected" ]]
done

if ! timeout 30 tail --pid="$client_pid" -f /dev/null; then
  printf 'NaiveFox did not exit after the configured connection limit\n' >&2
  exit 1
fi
wait "$client_pid"
client_pid=

[[ $(rg -c '^Padding negotiated: yes$' "$client_log") -eq 6 ]]
[[ $(rg -c "^Outer protocol: $protocol$" "$client_log") -eq 6 ]]
if rg -q '^Padding negotiated: no$' "$client_log"; then
  printf 'fixture tunnel unexpectedly fell back to raw mode\n' >&2
  exit 1
fi
if rg -F "$NAIVEFOX_FIXTURE_PASS" "$client_log"; then
  printf 'proxy password appeared in client output\n' >&2
  exit 1
fi

printf 'NaiveFox padded SOCKS HTTP, HTTPS, and integrity tests passed over %s\n' \
  "$protocol"
