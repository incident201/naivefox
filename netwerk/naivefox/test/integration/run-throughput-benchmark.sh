#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

readonly block_size=$((64 * 1024 * 1024))
readonly sequential_requests=4
readonly parallel_requests=4
readonly heavy_parallel_requests=8
readonly upload_requests=2
readonly parallel_upload_requests=4
readonly trials=3
readonly connections_per_client=$((
  2 + trials * (sequential_requests + parallel_requests +
    heavy_parallel_requests + upload_requests + parallel_upload_requests)
))

run_dir=
active_pid=
active_log=
reference_config=
cleanup() {
  local status=$?
  if [[ -n $active_pid ]] && kill -0 "$active_pid" 2>/dev/null; then
    kill "$active_pid" 2>/dev/null || true
    wait "$active_pid" 2>/dev/null || true
  fi
  if ((status != 0)) && [[ -n $active_log && -f $active_log ]]; then
    sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" <"$active_log" \
      >"$SOURCE_ROOT/artifacts/throughput-client-failure.log"
    chmod 0600 "$SOURCE_ROOT/artifacts/throughput-client-failure.log"
  fi
  [[ -z $reference_config ]] || rm -f -- "$reference_config"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
"$SOURCE_ROOT/netwerk/naivefox/tools/fetch-naiveproxy-reference.sh"
reference_binary="$OBJDIR/naiveproxy-reference/naiveproxy-v150.0.7871.63-1-linux-x64/naive"
[[ -x $reference_binary ]]

metrics="$run_dir/throughput.tsv"
printf 'client\tphase\ttrial\tbytes\tseconds\tmib_per_second\n' >"$metrics"
upload_file="$run_dir/upload-64m.bin"
truncate -s "$block_size" "$upload_file"
upload_sha=$(sha256sum "$upload_file" | cut -d' ' -f1)
direct_file="$run_dir/direct-64m.bin"
curl --fail --silent --show-error \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=$block_size" \
  --output "$direct_file"
direct_sha=$(sha256sum "$direct_file" | cut -d' ' -f1)

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

wait_for_listener() {
  local pid=$1
  local log=$2
  local pattern=$3
  for ((i = 0; i < 150; i++)); do
    if rg -q "$pattern" "$log"; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'benchmark client exited before readiness\n' >&2
      return 1
    fi
    sleep 0.1
  done
  printf 'timed out waiting for benchmark client\n' >&2
  return 1
}

record_metric() {
  local client=$1
  local phase=$2
  local trial=$3
  local bytes=$4
  local start_ns=$5
  local end_ns=$6
  awk -v client="$client" -v phase="$phase" -v trial="$trial" \
    -v bytes="$bytes" -v elapsed_ns="$((end_ns - start_ns))" \
    'BEGIN {
      seconds = elapsed_ns / 1000000000;
      rate = bytes / 1048576 / seconds;
      printf "%s\t%s\t%d\t%d\t%.6f\t%.3f\n", client, phase, trial,
             bytes, seconds, rate;
    }' >>"$metrics"
}

validate_upload_response() {
  python3 - "$1" "$block_size" "$upload_sha" <<'PY'
import json
import sys

actual = json.loads(sys.argv[1])
expected = {"bytes": int(sys.argv[2]), "sha256": sys.argv[3]}
if actual != expected:
    raise SystemExit(f"upload integrity mismatch: {actual!r}")
PY
}

benchmark_direct() {
  local download_url="http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=$block_size"
  local trial
  local request
  local start_ns
  local end_ns
  local direct_pids
  for ((trial = 1; trial <= trials; trial++)); do
    start_ns=$(date +%s%N)
    for ((request = 0; request < sequential_requests; request++)); do
      curl --fail --silent --show-error "$download_url" --output /dev/null
    done
    end_ns=$(date +%s%N)
    record_metric direct sequential_download "$trial" \
      "$((block_size * sequential_requests))" "$start_ns" "$end_ns"

    start_ns=$(date +%s%N)
    direct_pids=()
    for ((request = 0; request < heavy_parallel_requests; request++)); do
      curl --fail --silent --show-error "$download_url" --output /dev/null &
      direct_pids+=("$!")
    done
    for request in "${direct_pids[@]}"; do
      wait "$request"
    done
    end_ns=$(date +%s%N)
    record_metric direct parallel_8_download "$trial" \
      "$((block_size * heavy_parallel_requests))" "$start_ns" "$end_ns"
  done
}

benchmark_client() {
  local label=$1
  local socks_port=$2
  local client_pid=$3
  local client_log=$4
  local curl_socks=(
    --fail --silent --show-error --noproxy ''
    --connect-timeout 10 --max-time 180
    --socks5-hostname "127.0.0.1:$socks_port"
  )
  local download_url="http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=$block_size"
  local upload_url="http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/upload"

  curl "${curl_socks[@]}" "$download_url" --output /dev/null

  local integrity_file="$run_dir/$label-integrity.bin"
  curl "${curl_socks[@]}" "$download_url" --output "$integrity_file"
  [[ $(sha256sum "$integrity_file" | cut -d' ' -f1) == "$direct_sha" ]]
  rm -f -- "$integrity_file"

  local trial
  local request
  local start_ns
  local end_ns
  local response
  local parallel_pids
  local upload_pids
  local response_file
  for ((trial = 1; trial <= trials; trial++)); do
    start_ns=$(date +%s%N)
    for ((request = 0; request < sequential_requests; request++)); do
      curl "${curl_socks[@]}" "$download_url" --output /dev/null
    done
    end_ns=$(date +%s%N)
    record_metric "$label" sequential_download "$trial" \
      "$((block_size * sequential_requests))" "$start_ns" "$end_ns"

    start_ns=$(date +%s%N)
    parallel_pids=()
    for ((request = 0; request < parallel_requests; request++)); do
      curl "${curl_socks[@]}" "$download_url" --output /dev/null &
      parallel_pids+=("$!")
    done
    for request in "${parallel_pids[@]}"; do
      wait "$request"
    done
    end_ns=$(date +%s%N)
    record_metric "$label" parallel_download "$trial" \
      "$((block_size * parallel_requests))" "$start_ns" "$end_ns"

    start_ns=$(date +%s%N)
    parallel_pids=()
    for ((request = 0; request < heavy_parallel_requests; request++)); do
      curl "${curl_socks[@]}" "$download_url" --output /dev/null &
      parallel_pids+=("$!")
    done
    for request in "${parallel_pids[@]}"; do
      wait "$request"
    done
    end_ns=$(date +%s%N)
    record_metric "$label" parallel_8_download "$trial" \
      "$((block_size * heavy_parallel_requests))" "$start_ns" "$end_ns"

    start_ns=$(date +%s%N)
    for ((request = 0; request < upload_requests; request++)); do
      response=$(curl "${curl_socks[@]}" --header 'Expect:' \
        --data-binary "@$upload_file" "$upload_url")
      validate_upload_response "$response"
    done
    end_ns=$(date +%s%N)
    record_metric "$label" sequential_upload "$trial" \
      "$((block_size * upload_requests))" "$start_ns" "$end_ns"

    start_ns=$(date +%s%N)
    upload_pids=()
    for ((request = 0; request < parallel_upload_requests; request++)); do
      response_file="$run_dir/$label-upload-$trial-$request.json"
      curl "${curl_socks[@]}" --header 'Expect:' \
        --data-binary "@$upload_file" "$upload_url" \
        --output "$response_file" &
      upload_pids+=("$!")
    done
    for request in "${upload_pids[@]}"; do
      wait "$request"
    done
    for ((request = 0; request < parallel_upload_requests; request++)); do
      response_file="$run_dir/$label-upload-$trial-$request.json"
      validate_upload_response "$(<"$response_file")"
      rm -f -- "$response_file"
    done
    end_ns=$(date +%s%N)
    record_metric "$label" parallel_upload "$trial" \
      "$((block_size * parallel_upload_requests))" "$start_ns" "$end_ns"
  done

  kill -0 "$client_pid"
  awk -v client="$label" '/^VmHWM:/ {
    printf "%s\tpeak_rss\t0\t%d\t0\t0\n", client, $2 * 1024
  }' "/proc/$client_pid/status" >>"$metrics"
  printf '%s benchmark phases completed\n' "$label"
}

start_reference() {
  local socks_port=$1
  reference_config="$run_dir/reference-config.json"
  active_log="$run_dir/reference-client.log"
  python3 - "$reference_config" "$socks_port" "$NAIVEFOX_FIXTURE_PROXY_PORT" \
    "$NAIVEFOX_FIXTURE_USER" "$NAIVEFOX_FIXTURE_PASS" "$active_log" <<'PY'
import json
import sys
import urllib.parse

config, port, proxy_port, user, password, log = sys.argv[1:]
proxy = (
    "https://"
    + urllib.parse.quote(user, safe="")
    + ":"
    + urllib.parse.quote(password, safe="")
    + "@localhost:"
    + proxy_port
)
with open(config, "x", encoding="utf-8") as output:
    json.dump(
        {
            "listen": f"socks://127.0.0.1:{port}",
            "proxy": proxy,
            "log": log,
        },
        output,
    )
    output.write("\n")
PY
  chmod 0600 "$reference_config"
  env SSL_CERT_FILE="$NAIVEFOX_FIXTURE_CA" \
    SSL_CERT_DIR="$run_dir/reference-empty-cert-dir" \
    "$reference_binary" "$reference_config" >/dev/null 2>&1 &
  active_pid=$!
  wait_for_listener "$active_pid" "$active_log" \
    "Listening on socks://127.0.0.1:$socks_port"
}

start_naivefox() {
  local socks_port=$1
  active_log="$run_dir/naivefox-client.log"
  env MOZ_CRASHREPORTER_DISABLE=1 \
    LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
    "$OBJDIR/dist/bin/naivefox" \
    --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
    --socks-listen "127.0.0.1:$socks_port" \
    --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    >"$active_log" 2>&1 &
  active_pid=$!
  wait_for_listener "$active_pid" "$active_log" \
    "^SOCKS5 listening on 127.0.0.1:$socks_port$"
}

mkdir -m 0700 "$run_dir/reference-empty-cert-dir"
benchmark_direct

reference_port=$(choose_port)
start_reference "$reference_port"
benchmark_client naiveproxy "$reference_port" "$active_pid" "$active_log"
kill -TERM "$active_pid"
set +e
wait "$active_pid"
reference_status=$?
set -e
active_pid=
[[ $reference_status -eq 0 || $reference_status -eq 143 ]]
rm -f -- "$reference_config"
reference_config=

naivefox_port=$(choose_port)
start_naivefox "$naivefox_port"
benchmark_client naivefox "$naivefox_port" "$active_pid" "$active_log"
[[ $(rg -c '^Padding negotiated: yes$' "$active_log") -eq \
  "$connections_per_client" ]]
! rg -q '^Padding negotiated: no$' "$active_log"
kill -TERM "$active_pid"
set +e
wait "$active_pid"
naivefox_status=$?
set -e
active_pid=
[[ $naivefox_status -eq 0 || $naivefox_status -eq 143 ]]

cp -- "$metrics" "$SOURCE_ROOT/artifacts/throughput-benchmark.tsv"
chmod 0600 "$SOURCE_ROOT/artifacts/throughput-benchmark.tsv"
python3 - "$metrics" >"$SOURCE_ROOT/artifacts/throughput-benchmark-summary.md" <<'PY'
import csv
import statistics
import sys

rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8"), delimiter="\t"))
print("| Client | Phase | Median MiB/s | Trials |")
print("|---|---|---:|---:|")
for client in ("direct", "naiveproxy", "naivefox"):
    phases = ("sequential_download", "parallel_8_download") if client == "direct" else (
        "sequential_download",
        "parallel_download",
        "parallel_8_download",
        "sequential_upload",
        "parallel_upload",
    )
    for phase in phases:
        values = [
            float(row["mib_per_second"])
            for row in rows
            if row["client"] == client and row["phase"] == phase
        ]
        print(f"| {client} | {phase} | {statistics.median(values):.3f} | {len(values)} |")
for client in ("naiveproxy", "naivefox"):
    rss = next(
        int(row["bytes"])
        for row in rows
        if row["client"] == client and row["phase"] == "peak_rss"
    )
    print(f"\n{client} peak RSS: {rss / 1048576:.1f} MiB")
PY
chmod 0600 "$SOURCE_ROOT/artifacts/throughput-benchmark-summary.md"

printf 'NaiveFox/NaiveProxy local throughput benchmark passed\n'
