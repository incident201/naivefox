#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

protocol=${1:-}
case $protocol in
  h2 | h3) ;;
  *)
    printf 'usage: %s h2|h3\n' "$0" >&2
    exit 2
    ;;
esac

runtime=${NAIVEFOX_RUNTIME:-$OBJDIR/dist/bin/naivefox}
external_runtime=false
if [[ -n ${NAIVEFOX_RUNTIME:-} ]]; then
  [[ $runtime == /* && -x $runtime ]] || {
    printf 'NAIVEFOX_RUNTIME must be an absolute executable path\n' >&2
    exit 2
  }
  external_runtime=true
fi

run_dir=
client_pid=
client_log=
config_file=
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ $status -ne 0 && -n $client_log && -f $client_log ]]; then
    sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" <"$client_log" \
      >"$SOURCE_ROOT/artifacts/$protocol-config-client-failure.log"
  fi
  [[ -z $config_file ]] || rm -f -- "$config_file"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh" --mode "$protocol"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

free_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}
socks_port=$(free_port)
http_port=$(free_port)
while [[ $http_port == "$socks_port" ]]; do http_port=$(free_port); done

config_file=${NAIVEFOX_CONFIG_PATH:-$run_dir/config.json}
if [[ $config_file != /* || -e $config_file ]]; then
  printf 'NAIVEFOX_CONFIG_PATH must be an unused absolute path\n' >&2
  exit 2
fi
proxy_scheme=https
[[ $protocol == h3 ]] && proxy_scheme=quic
CONFIG_PATH=$config_file PROXY_SCHEME=$proxy_scheme \
  PROXY_PORT=$NAIVEFOX_FIXTURE_PROXY_PORT \
  PROXY_USER=$NAIVEFOX_FIXTURE_USER PROXY_PASS=$NAIVEFOX_FIXTURE_PASS \
  SOCKS_PORT=$socks_port HTTP_PORT=$http_port python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import quote

user = quote(os.environ["PROXY_USER"], safe="")
password = quote(os.environ["PROXY_PASS"], safe="")
config = {
    "listen": [
        f"socks://127.0.0.1:{os.environ['SOCKS_PORT']}",
        f"http://127.0.0.1:{os.environ['HTTP_PORT']}",
    ],
    "proxy": (
        f"{os.environ['PROXY_SCHEME']}://{user}:{password}"
        f"@localhost:{os.environ['PROXY_PORT']}"
    ),
    "log": "",
}
path = Path(os.environ["CONFIG_PATH"])
path.write_text(json.dumps(config), encoding="utf-8")
path.chmod(0o600)
PY

client_log="$run_dir/config-client.log"
runtime_environment=(env
  -u NAIVEFOX_PROXY_USER
  -u NAIVEFOX_PROXY_PASS
  -u SSLKEYLOGFILE
)
if $external_runtime; then
  runtime_environment+=(-u LD_LIBRARY_PATH -u LD_PRELOAD)
else
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
runtime_environment+=(
  NAIVEFOX_PROFILE="$NAIVEFOX_FIXTURE_TRUSTED_PROFILE"
  MOZ_CRASHREPORTER_DISABLE=1
)
if [[ ${NAIVEFOX_CONFIG_DEFAULT:-0} == 1 ]]; then
  [[ $(basename "$config_file") == config.json ]] || {
    printf 'default config invocation requires a config.json path\n' >&2
    exit 2
  }
  original_dir=$PWD
  cd "$(dirname "$config_file")"
  "${runtime_environment[@]}" "$runtime" >"$client_log" 2>&1 &
  client_pid=$!
  cd "$original_dir"
else
  "${runtime_environment[@]}" "$runtime" "$config_file" \
    >"$client_log" 2>&1 &
  client_pid=$!
fi

for ((i = 0; i < 150; i++)); do
  if rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log" &&
    rg -q "^HTTP CONNECT listening on 127.0.0.1:$http_port$" "$client_log"; then
    break
  fi
  kill -0 "$client_pid" 2>/dev/null || {
    printf 'NaiveFox exited before both config listeners became ready\n' >&2
    exit 1
  }
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"
rg -q "^HTTP CONNECT listening on 127.0.0.1:$http_port$" "$client_log"

if [[ -n ${NAIVEFOX_EXPECT_RUNTIME_DIR:-} ]]; then
  expected_runtime=$(realpath -- "$NAIVEFOX_EXPECT_RUNTIME_DIR")
  [[ $(readlink -f "/proc/$client_pid/exe") == \
    "$expected_runtime/naivefox" ]]
  ! rg -Fq "$OBJDIR" "/proc/$client_pid/maps"
  ! rg -Fq "$SOURCE_ROOT" "/proc/$client_pid/maps"
fi

curl_socks=(
  --silent --show-error --fail --noproxy ''
  --socks5-hostname "127.0.0.1:$socks_port"
)
curl_http=(
  --silent --show-error --fail --noproxy ''
  --proxy "http://127.0.0.1:$http_port"
)
expected=naivefox-fixture-small

[[ $(curl "${curl_socks[@]}" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small") == "$expected" ]]
[[ $(curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
  "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/small") == "$expected" ]]

http_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --noproxy '' --proxy "http://127.0.0.1:$http_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small")
[[ $http_status == 405 ]]

for frontend in socks http; do
  large_file="$run_dir/config-$frontend-large.bin"
  if [[ $frontend == socks ]]; then
    curl "${curl_socks[@]}" --output "$large_file" \
      "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=3145728"
  else
    curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
      --output "$large_file" \
      "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/large?size=3145728"
  fi
  [[ $(wc -c <"$large_file") -eq 3145728 ]]
  [[ $(sha256sum "$large_file" | cut -d' ' -f1) == \
    a1feacf0d812ba4d0b0e463ed45bbd583cea1de55c54693116754b30b5794745 ]]
done

upload_file="$run_dir/config-upload.bin"
head -c 2097152 /dev/zero >"$upload_file"
upload_socks=$(curl "${curl_socks[@]}" --data-binary "@$upload_file" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/upload")
upload_http=$(curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
  --data-binary "@$upload_file" \
  "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/upload")
for response in "$upload_socks" "$upload_http"; do
  python3 -c \
    'import json,sys; assert json.loads(sys.argv[1]) == {"bytes": 2097152, "sha256": "5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee"}' \
    "$response"
done

pids=()
for i in 1 2; do
  curl "${curl_socks[@]}" --output "$run_dir/mixed-socks-$i" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=1000" &
  pids+=("$!")
  curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
    --output "$run_dir/mixed-http-$i" \
    "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/delay?ms=1000" &
  pids+=("$!")
done
sleep 0.3
if [[ $protocol == h2 ]]; then
  [[ $(ss -Htn state established "dport = :$NAIVEFOX_FIXTURE_PROXY_PORT" |
    wc -l) -eq 1 ]]
else
  [[ -z $(ss -Htn "dport = :$NAIVEFOX_FIXTURE_PROXY_PORT") ]]
fi
for pid in "${pids[@]}"; do wait "$pid"; done
for file in "$run_dir"/mixed-*; do
  [[ $(<"$file") == "$expected" ]]
done

[[ $(rg -c "^Outer protocol: $protocol$" "$client_log") -eq 10 ]]
[[ $(rg -c '^Padding negotiated: yes$' "$client_log") -eq 10 ]]
! rg -q '^Padding negotiated: no$' "$client_log"
! rg -Fq "$NAIVEFOX_FIXTURE_PASS" "$client_log"

kill "$client_pid"
set +e
wait "$client_pid"
client_status=$?
set -e
client_pid=
[[ $client_status -eq 0 || $client_status -eq 143 ]]

printf 'NaiveFox config-mode SOCKS + HTTP CONNECT tests passed over %s\n' \
  "$protocol"
