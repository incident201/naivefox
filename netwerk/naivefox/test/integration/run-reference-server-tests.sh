#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

: "${NAIVEFOX_REAL_PROXY_URL:?set NAIVEFOX_REAL_PROXY_URL}"
: "${NAIVEFOX_REAL_PROXY_USER:?set NAIVEFOX_REAL_PROXY_USER}"
: "${NAIVEFOX_REAL_PROXY_PASS:?set NAIVEFOX_REAL_PROXY_PASS}"
reference_duration=${NAIVEFOX_REFERENCE_DURATION_SECONDS:-60}
if [[ ! $reference_duration =~ ^[0-9]+$ ]] ||
  ((reference_duration < 30 || reference_duration > 180)); then
  printf 'reference duration must be between 30 and 180 seconds\n' >&2
  exit 2
fi

"$SOURCE_ROOT/netwerk/naivefox/tools/fetch-naiveproxy-reference.sh"
reference_binary="$OBJDIR/naiveproxy-reference/naiveproxy-v150.0.7871.63-1-linux-x64/naive"
if [[ ! -x $reference_binary ]]; then
  printf 'official NaiveProxy reference binary was not found\n' >&2
  exit 1
fi

state_root="$OBJDIR/naiveproxy-reference-tests"
mkdir -m 0700 -p "$state_root"
run_dir=$(mktemp -d "$state_root/run.XXXXXX")
config="$run_dir/config.json"
client_log="$run_dir/naive.log"
summary="$run_dir/summary.txt"

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

socks_port=$(choose_port)
python3 - "$config" "$socks_port" "$NAIVEFOX_REAL_PROXY_URL" \
  "$NAIVEFOX_REAL_PROXY_USER" "$NAIVEFOX_REAL_PROXY_PASS" "$client_log" <<'PY'
import json
import sys
import urllib.parse

config, port, proxy_url, user, password, log = sys.argv[1:]
parsed = urllib.parse.urlsplit(proxy_url)
if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None:
    raise SystemExit("reference proxy URL must be an HTTPS URL without userinfo")
host = parsed.hostname
if ":" in host:
    host = f"[{host}]"
if parsed.port is not None:
    host = f"{host}:{parsed.port}"
userinfo = (
    urllib.parse.quote(user, safe="")
    + ":"
    + urllib.parse.quote(password, safe="")
    + "@"
)
authenticated = urllib.parse.urlunsplit(
    (parsed.scheme, userinfo + host, parsed.path, parsed.query, "")
)
with open(config, "x", encoding="utf-8") as output:
    json.dump(
        {
            "listen": f"socks://127.0.0.1:{port}",
            "proxy": authenticated,
            "log": log,
        },
        output,
    )
    output.write("\n")
PY
chmod 0600 "$config"

client_pid=
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
  rm -f -- "$config"
  if ((status == 0)); then
    cp -- "$summary" "$SOURCE_ROOT/artifacts/reference-server-summary.txt"
    chmod 0600 "$SOURCE_ROOT/artifacts/reference-server-summary.txt"
    case $(realpath -- "$run_dir") in
      "$(realpath -- "$state_root")"/run.*) rm -rf -- "$run_dir" ;;
      *) printf 'refusing unexpected reference cleanup path\n' >&2 ;;
    esac
  else
    if [[ -f $client_log ]]; then
      sanitize_stream "$NAIVEFOX_REAL_PROXY_USER" \
        "$NAIVEFOX_REAL_PROXY_PASS" <"$client_log" \
        >"$SOURCE_ROOT/artifacts/reference-server-client-failure.log"
      chmod 0600 "$SOURCE_ROOT/artifacts/reference-server-client-failure.log"
    fi
    printf 'reference test failed; private diagnostics: %s\n' "$run_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT

"$reference_binary" "$config" >/dev/null 2>&1 &
client_pid=$!
for ((i = 0; i < 150; i++)); do
  if rg -q "Listening on socks://127.0.0.1:$socks_port" "$client_log"; then
    break
  fi
  if ! kill -0 "$client_pid" 2>/dev/null; then
    printf 'official NaiveProxy exited before readiness\n' >&2
    exit 1
  fi
  sleep 0.1
done
rg -q "Listening on socks://127.0.0.1:$socks_port" "$client_log"

curl_socks=(
  --silent --show-error --fail --location
  --connect-timeout 15 --max-time 60
  --noproxy '' --socks5-hostname "127.0.0.1:$socks_port"
)

: >"$summary"
ready=false
for attempt in {1..12}; do
  if curl "${curl_socks[@]}" https://example.com/ \
    --output "$run_dir/warmup.body" \
    --write-out "warmup-attempt=$attempt http=%{http_code} bytes=%{size_download}\n" \
    >"$run_dir/warmup.metric" 2>"$run_dir/warmup.error"; then
    cat "$run_dir/warmup.metric" >>"$summary"
    ready=true
    break
  fi
  printf 'warmup-attempt=%d failed\n' "$attempt" >>"$summary"
  sleep 5
done
if [[ $ready != true ]] || ! rg -q 'Example Domain' "$run_dir/warmup.body"; then
  printf 'official NaiveProxy did not establish a working SOCKS tunnel\n' >&2
  exit 1
fi

urls=(
  https://example.com/
  https://www.iana.org/domains/reserved
  https://github.com/incident201/naivefox
  https://raw.githubusercontent.com/klzgrad/naiveproxy/v150.0.7871.63-1/README.md
)
session_started=$SECONDS
wave=0
while ((SECONDS - session_started < reference_duration)); do
  ((wave += 1))
  wave_pids=()
  for index in "${!urls[@]}"; do
    curl "${curl_socks[@]}" "${urls[$index]}" \
      --output "$run_dir/wave-$wave-$index.body" \
      --write-out "wave-$wave-$index http=%{http_code} bytes=%{size_download} seconds=%{time_total}\n" \
      >"$run_dir/wave-$wave-$index.metric" &
    wave_pids+=("$!")
  done
  background_pids=("${wave_pids[@]}")
  for pid in "${wave_pids[@]}"; do
    wait "$pid"
  done
  background_pids=()
  for index in "${!urls[@]}"; do
    [[ -s $run_dir/wave-$wave-$index.body ]]
    cat "$run_dir/wave-$wave-$index.metric" >>"$summary"
  done
  elapsed=$((SECONDS - session_started))
  if ((elapsed < reference_duration)); then
    remaining=$((reference_duration - elapsed))
    sleep_seconds=$((remaining < 20 ? remaining : 20))
    sleep "$sleep_seconds"
  fi
done
printf 'session-seconds=%d parallel-waves=%d\n' \
  "$((SECONDS - session_started))" "$wave" >>"$summary"

if ! kill -0 "$client_pid" 2>/dev/null; then
  printf 'official NaiveProxy exited before workload completion\n' >&2
  exit 1
fi
kill -TERM "$client_pid"
set +e
wait "$client_pid"
client_status=$?
set -e
client_pid=
if [[ $client_status -ne 0 && $client_status -ne 143 ]]; then
  printf 'official NaiveProxy exited with status %d\n' "$client_status" >&2
  exit 1
fi
if rg -F "$NAIVEFOX_REAL_PROXY_PASS" "$client_log"; then
  printf 'proxy password appeared in official client output\n' >&2
  exit 1
fi
printf '%s\n' 'Official NaiveProxy reference workload passed'
