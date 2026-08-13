#!/usr/bin/env bash

set -euo pipefail
umask 077

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

: "${NAIVEFOX_REAL_PROXY_URL:?set NAIVEFOX_REAL_PROXY_URL}"
: "${NAIVEFOX_REAL_PROXY_USER:?set NAIVEFOX_REAL_PROXY_USER}"
: "${NAIVEFOX_REAL_PROXY_PASS:?set NAIVEFOX_REAL_PROXY_PASS}"

runtime=${NAIVEFOX_REAL_RUNTIME:-$OBJDIR/dist/bin/naivefox}
runtime_kind=staged
if [[ $runtime == "$OBJDIR/dist/bin/naivefox" ]]; then
  runtime_kind=objdir
elif [[ ! -x $runtime && -z ${NAIVEFOX_REAL_RUNTIME:-} &&
        -x $OBJDIR/naivefox-linux-x86_64-final/naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-final/naivefox"
fi
if [[ $runtime != /* || ! -x $runtime ]]; then
  printf 'NaiveFox runtime must be an absolute executable path\n' >&2
  exit 1
fi

proxy_authority=$(python3 - "$NAIVEFOX_REAL_PROXY_URL" <<'PY'
import sys
from urllib.parse import urlsplit

value = urlsplit(sys.argv[1])
if (
    value.scheme != "https"
    or not value.hostname
    or value.username is not None
    or value.password is not None
    or value.path not in ("", "/")
    or value.query
    or value.fragment
):
    raise SystemExit("real proxy URL must be an HTTPS origin without credentials")
host = f"[{value.hostname}]" if ":" in value.hostname else value.hostname
print(f"{host}:{value.port or 443}")
PY
)

state_root="$OBJDIR/naivefox-real-config-tests"
mkdir -m 0700 -p "$state_root" "$SOURCE_ROOT/artifacts"
run_dir=$(mktemp -d "$state_root/$protocol.XXXXXX")
config_file="$run_dir/config.json"
client_log="$run_dir/client.log"
summary="$run_dir/summary.txt"
client_pid=
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill -TERM "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  rm -f -- "$config_file"
  if ((status == 0)); then
    cp -- "$summary" "$SOURCE_ROOT/artifacts/real-server-config-$protocol-summary.txt"
    chmod 0600 "$SOURCE_ROOT/artifacts/real-server-config-$protocol-summary.txt"
    case $(realpath -- "$run_dir") in
      "$(realpath -- "$state_root")"/$protocol.*) rm -rf -- "$run_dir" ;;
      *) printf 'refusing to remove unexpected config-test path\n' >&2 ;;
    esac
  else
    if [[ -f $client_log ]]; then
      sanitize_stream "$NAIVEFOX_REAL_PROXY_USER" \
        "$NAIVEFOX_REAL_PROXY_PASS" <"$client_log" \
        >"$SOURCE_ROOT/artifacts/real-server-config-$protocol-failure.log"
      chmod 0600 "$SOURCE_ROOT/artifacts/real-server-config-$protocol-failure.log"
    fi
    printf 'real-server config test failed; private state preserved at %s\n' \
      "$run_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT

free_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}
socks_port=$(free_port)
http_port=$(free_port)
while [[ $http_port == "$socks_port" ]]; do http_port=$(free_port); done

proxy_scheme=https
[[ $protocol == h3 ]] && proxy_scheme=quic
CONFIG_PATH=$config_file PROXY_SCHEME=$proxy_scheme \
  PROXY_AUTHORITY=$proxy_authority PROXY_USER=$NAIVEFOX_REAL_PROXY_USER \
  PROXY_PASS=$NAIVEFOX_REAL_PROXY_PASS SOCKS_PORT=$socks_port \
  HTTP_PORT=$http_port python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import quote

config = {
    "listen": [
        f"socks://127.0.0.1:{os.environ['SOCKS_PORT']}",
        f"http://127.0.0.1:{os.environ['HTTP_PORT']}",
    ],
    "proxy": (
        f"{os.environ['PROXY_SCHEME']}://"
        f"{quote(os.environ['PROXY_USER'], safe='')}:"
        f"{quote(os.environ['PROXY_PASS'], safe='')}@"
        f"{os.environ['PROXY_AUTHORITY']}"
    ),
    "log": "",
}
path = Path(os.environ["CONFIG_PATH"])
path.write_text(json.dumps(config), encoding="utf-8")
path.chmod(0o600)
PY

runtime_environment=(env -u NAIVEFOX_PROFILE -u NAIVEFOX_PROXY_USER
  -u NAIVEFOX_PROXY_PASS -u SSLKEYLOGFILE -u LD_PRELOAD
)
if [[ $runtime_kind == objdir ]]; then
  runtime_environment+=("LD_LIBRARY_PATH=$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
else
  runtime_environment+=(-u LD_LIBRARY_PATH)
fi
runtime_environment+=(XDG_STATE_HOME="$run_dir/state"
  MOZ_CRASHREPORTER_DISABLE=1)
"${runtime_environment[@]}" "$runtime" "$config_file" \
  >"$client_log" 2>&1 &
client_pid=$!

for ((i = 0; i < 150; i++)); do
  if rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log" &&
    rg -q "^HTTP CONNECT listening on 127.0.0.1:$http_port$" "$client_log"; then
    break
  fi
  kill -0 "$client_pid" 2>/dev/null || {
    printf 'NaiveFox exited before real config listeners became ready\n' >&2
    exit 1
  }
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"
rg -q "^HTTP CONNECT listening on 127.0.0.1:$http_port$" "$client_log"
[[ -d $run_dir/state/naivefox/profile ]]
[[ $(stat -c '%a' "$run_dir/state/naivefox/profile") == 700 ]]

curl_socks=(--silent --show-error --fail --location --noproxy ''
  --connect-timeout 15 --max-time 90
  --socks5-hostname "127.0.0.1:$socks_port")
curl_http=(--silent --show-error --fail --location --noproxy ''
  --connect-timeout 15 --max-time 90
  --proxy "http://127.0.0.1:$http_port")

normal_url=https://example.com/
curl "${curl_socks[@]}" "$normal_url" --output "$run_dir/normal-socks.body"
curl "${curl_http[@]}" "$normal_url" --output "$run_dir/normal-http.body"
rg -q 'Example Domain' "$run_dir/normal-socks.body"
rg -q 'Example Domain' "$run_dir/normal-http.body"

integrity_url=https://raw.githubusercontent.com/klzgrad/forwardproxy/d62c80d3dd2c706b6b87579844d2397bddd18317/README.md
curl --silent --show-error --fail --location --noproxy '*' \
  --connect-timeout 15 --max-time 90 "$integrity_url" \
  --output "$run_dir/direct.body"
curl "${curl_socks[@]}" "$integrity_url" --output "$run_dir/socks.body"
curl "${curl_http[@]}" "$integrity_url" --output "$run_dir/http.body"
expected_hash=$(sha256sum "$run_dir/direct.body" | cut -d ' ' -f 1)
[[ $(sha256sum "$run_dir/socks.body" | cut -d ' ' -f 1) == "$expected_hash" ]]
[[ $(sha256sum "$run_dir/http.body" | cut -d ' ' -f 1) == "$expected_hash" ]]

pids=()
for index in 1 2; do
  curl "${curl_socks[@]}" "$integrity_url" \
    --output "$run_dir/parallel-socks-$index.body" &
  pids+=("$!")
  curl "${curl_http[@]}" "$integrity_url" \
    --output "$run_dir/parallel-http-$index.body" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid"; done
for body in "$run_dir"/parallel-*.body; do
  [[ $(sha256sum "$body" | cut -d ' ' -f 1) == "$expected_hash" ]]
done

[[ $(rg -c "^Outer protocol: $protocol$" "$client_log") -eq 8 ]]
[[ $(rg -c '^Padding negotiated: yes$' "$client_log") -eq 8 ]]
! rg -q '^Padding negotiated: no$' "$client_log"
if [[ $protocol == h3 ]]; then
  ! rg -q '^Outer protocol: h2$' "$client_log"
fi
! rg -Fq "$NAIVEFOX_REAL_PROXY_USER" "$client_log"
! rg -Fq "$NAIVEFOX_REAL_PROXY_PASS" "$client_log"

kill -TERM "$client_pid"
set +e
wait "$client_pid"
client_status=$?
set -e
client_pid=
[[ $client_status -eq 0 || $client_status -eq 143 ]]

printf 'protocol=%s tunnels=8 padding=yes normal-pages=2 integrity=match concurrent=4\n' \
  "$protocol" >"$summary"
printf 'NaiveFox real-server config-mode test passed over %s\n' "$protocol"
