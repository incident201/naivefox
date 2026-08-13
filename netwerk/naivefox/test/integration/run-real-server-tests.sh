#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

: "${NAIVEFOX_REAL_PROXY_URL:?set NAIVEFOX_REAL_PROXY_URL}"
: "${NAIVEFOX_REAL_PROXY_USER:?set NAIVEFOX_REAL_PROXY_USER}"
: "${NAIVEFOX_REAL_PROXY_PASS:?set NAIVEFOX_REAL_PROXY_PASS}"
real_duration=${NAIVEFOX_REAL_DURATION_SECONDS:-120}

if [[ $NAIVEFOX_REAL_PROXY_URL != https://* ]]; then
  printf 'real proxy URL must use https\n' >&2
  exit 2
fi
if [[ ! $real_duration =~ ^[0-9]+$ ]] || ((real_duration < 30 || real_duration > 300)); then
  printf 'real workload duration must be between 30 and 300 seconds\n' >&2
  exit 2
fi

runtime=${NAIVEFOX_REAL_RUNTIME:-"$OBJDIR/dist/bin/naivefox"}
if [[ $runtime == "$OBJDIR/dist/bin/naivefox" ]]; then
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
if [[ ! -x $runtime && -z ${NAIVEFOX_REAL_RUNTIME:-} ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-final/run-naivefox"
fi
if [[ ! -x $runtime ]]; then
  printf 'NaiveFox runtime is not built\n' >&2
  exit 1
fi

state_root="$OBJDIR/naivefox-real-tests"
mkdir -m 0700 -p "$state_root"
run_dir=$(mktemp -d "$state_root/run.XXXXXX")
profile="$run_dir/profile"
client_log="$run_dir/naivefox.log"
summary="$run_dir/summary.txt"
mkdir -m 0700 "$profile"

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
  if ((status == 0)); then
    cp -- "$summary" "$SOURCE_ROOT/artifacts/real-server-summary.txt"
    chmod 0600 "$SOURCE_ROOT/artifacts/real-server-summary.txt"
    case $(realpath -- "$run_dir") in
      "$(realpath -- "$state_root")"/run.*) rm -rf -- "$run_dir" ;;
      *) printf 'refusing to remove unexpected real-test path\n' >&2 ;;
    esac
  else
    if [[ -f $client_log ]]; then
      sanitize_stream "$NAIVEFOX_REAL_PROXY_USER" \
        "$NAIVEFOX_REAL_PROXY_PASS" <"$client_log" \
        >"$SOURCE_ROOT/artifacts/real-server-client-failure.log"
      chmod 0600 "$SOURCE_ROOT/artifacts/real-server-client-failure.log"
    fi
    printf 'real-server test failed; private state preserved at %s\n' \
      "$run_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

socks_port=$(choose_port)
env MOZ_CRASHREPORTER_DISABLE=1 \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_REAL_PROXY_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_REAL_PROXY_PASS" \
  "$runtime" --profile "$profile" \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "$NAIVEFOX_REAL_PROXY_URL" --max-connections 128 \
  >"$client_log" 2>&1 &
client_pid=$!

for ((i = 0; i < 150; i++)); do
  if rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"; then
    break
  fi
  if ! kill -0 "$client_pid" 2>/dev/null; then
    printf 'NaiveFox exited before SOCKS readiness\n' >&2
    exit 1
  fi
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"

curl_socks=(
  --silent --show-error --fail --location
  --connect-timeout 15 --max-time 60
  --noproxy '' --socks5-hostname "127.0.0.1:$socks_port"
)

fetch_page() {
  local name=$1
  local url=$2
  local output="$run_dir/$name.body"
  curl "${curl_socks[@]}" "$url" --output "$output" \
    --write-out "$name http=%{http_code} bytes=%{size_download} seconds=%{time_total}\n" \
    >>"$summary"
  [[ -s $output ]]
}

: >"$summary"
fetch_page example https://example.com/
rg -q 'Example Domain' "$run_dir/example.body"
fetch_page mozilla https://www.mozilla.org/
fetch_page github https://github.com/incident201/naivefox
fetch_page forwardproxy-readme \
  https://raw.githubusercontent.com/klzgrad/forwardproxy/d62c80d3dd2c706b6b87579844d2397bddd18317/README.md
rg -qi 'forward proxy' "$run_dir/forwardproxy-readme.body"

archive_url=https://codeload.github.com/caddyserver/caddy/tar.gz/ffb6ab0644f24c5ee6542aca6bd59b7a1b0a8f91
curl --silent --show-error --fail --location --connect-timeout 15 \
  --max-time 120 "$archive_url" --output "$run_dir/caddy-direct.tar.gz"
curl "${curl_socks[@]}" --max-time 120 "$archive_url" \
  --output "$run_dir/caddy-proxied.tar.gz" \
  --write-out 'github-archive http=%{http_code} bytes=%{size_download} seconds=%{time_total}\n' \
  >>"$summary"
direct_hash=$(sha256sum "$run_dir/caddy-direct.tar.gz" | cut -d ' ' -f 1)
proxied_hash=$(sha256sum "$run_dir/caddy-proxied.tar.gz" | cut -d ' ' -f 1)
[[ $direct_hash == "$proxied_hash" ]]
printf 'github-archive sha256=%s integrity=match\n' "$proxied_hash" >>"$summary"

parallel_urls=(
  https://example.com/
  https://www.iana.org/domains/reserved
  https://github.com/caddyserver/caddy
  https://raw.githubusercontent.com/klzgrad/naiveproxy/v150.0.7871.63-1/README.md
)
workload_started=$SECONDS
wave=0
while ((SECONDS - workload_started < real_duration)); do
  ((wave += 1))
  parallel_pids=()
  for index in "${!parallel_urls[@]}"; do
    curl "${curl_socks[@]}" "${parallel_urls[$index]}" \
      --output "$run_dir/wave-$wave-$index.body" \
      --write-out "wave-$wave-$index http=%{http_code} bytes=%{size_download} seconds=%{time_total}\n" \
      >"$run_dir/wave-$wave-$index.metric" &
    parallel_pids+=("$!")
  done
  background_pids=("${parallel_pids[@]}")
  for pid in "${parallel_pids[@]}"; do
    wait "$pid"
  done
  background_pids=()
  for index in "${!parallel_urls[@]}"; do
    [[ -s $run_dir/wave-$wave-$index.body ]]
    cat "$run_dir/wave-$wave-$index.metric" >>"$summary"
  done

  elapsed=$((SECONDS - workload_started))
  if ((elapsed < real_duration)); then
    remaining=$((real_duration - elapsed))
    sleep_seconds=$((remaining < 20 ? remaining : 20))
    sleep "$sleep_seconds"
  fi
done
printf 'session-seconds=%d parallel-waves=%d\n' \
  "$((SECONDS - workload_started))" "$wave" >>"$summary"

if ! kill -0 "$client_pid" 2>/dev/null; then
  printf 'NaiveFox exited before the workload completed\n' >&2
  exit 1
fi
kill -TERM "$client_pid"
set +e
wait "$client_pid"
client_status=$?
set -e
client_pid=
if [[ $client_status -ne 0 && $client_status -ne 143 ]]; then
  printf 'NaiveFox exited with unexpected status %d\n' "$client_status" >&2
  exit 1
fi

padding_count=$(rg -c '^Padding negotiated: yes$' "$client_log")
expected_min=$((5 + wave * ${#parallel_urls[@]}))
if [[ $padding_count -lt $expected_min || $padding_count -gt 128 ]]; then
  printf 'unexpected negotiated tunnel count: %d\n' "$padding_count" >&2
  exit 1
fi
if rg -q '^Padding negotiated: no$' "$client_log"; then
  printf 'real server unexpectedly fell back to raw payload mode\n' >&2
  exit 1
fi
if rg -F "$NAIVEFOX_REAL_PROXY_PASS" "$client_log"; then
  printf 'proxy password appeared in client output\n' >&2
  exit 1
fi
printf 'padding-negotiated=%d\n' "$padding_count" >>"$summary"
printf '%s\n' 'NaiveFox supplied real-server workload passed'
