#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

: "${NAIVEFOX_REAL_PROXY_URL:?set NAIVEFOX_REAL_PROXY_URL}"
: "${NAIVEFOX_REAL_PROXY_USER:?set NAIVEFOX_REAL_PROXY_USER}"
: "${NAIVEFOX_REAL_PROXY_PASS:?set NAIVEFOX_REAL_PROXY_PASS}"

if [[ $NAIVEFOX_REAL_PROXY_URL != https://* ]]; then
  printf 'real proxy URL must use https\n' >&2
  exit 2
fi

runtime=${NAIVEFOX_REAL_RUNTIME:-}
if [[ -z $runtime && -x $OBJDIR/naivefox-linux-x86_64-final/run-naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-final/run-naivefox"
elif [[ -z $runtime ]]; then
  runtime="$OBJDIR/dist/bin/naivefox"
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
if [[ ! -x $runtime ]]; then
  printf 'NaiveFox runtime is not built\n' >&2
  exit 1
fi

for tool in curl python3 rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required soak-test tool not found: %s\n' "$tool" >&2
    exit 1
  }
done

state_root="$OBJDIR/naivefox-real-soak"
mkdir -m 0700 -p "$state_root"
run_dir=$(mktemp -d "$state_root/run.XXXXXX")
profile="$run_dir/profile"
client_log="$run_dir/naivefox.log"
metrics="$run_dir/probes.tsv"
samples="$run_dir/resources.tsv"
summary="$run_dir/summary.txt"
mkdir -m 0700 "$profile"

client_pid=
monitor_pid=
success=0
cleanup() {
  local status=$?
  if [[ -n $monitor_pid ]] && kill -0 "$monitor_pid" 2>/dev/null; then
    kill -TERM "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
  fi
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill -TERM "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ $status -eq 0 && $success -eq 1 ]]; then
    cp -- "$summary" "$SOURCE_ROOT/artifacts/real-server-soak-summary.txt"
    chmod 0600 "$SOURCE_ROOT/artifacts/real-server-soak-summary.txt"
    case $(realpath -- "$run_dir") in
      "$(realpath -- "$state_root")"/run.*) rm -rf -- "$run_dir" ;;
      *) printf 'refusing to remove unexpected soak path\n' >&2 ;;
    esac
  else
    if [[ -f $client_log ]]; then
      sanitize_stream "$NAIVEFOX_REAL_PROXY_USER" \
        "$NAIVEFOX_REAL_PROXY_PASS" <"$client_log" \
        >"$SOURCE_ROOT/artifacts/real-server-soak-client-failure.log"
      chmod 0600 "$SOURCE_ROOT/artifacts/real-server-soak-client-failure.log"
    fi
    printf 'real-server soak failed; private state preserved at %s\n' \
      "$run_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

choose_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

proxy_port=$(python3 - "$NAIVEFOX_REAL_PROXY_URL" <<'PY'
import sys
import urllib.parse

parsed = urllib.parse.urlsplit(sys.argv[1])
print(parsed.port or 443)
PY
)
socks_port=$(choose_port)

env -u SSLKEYLOGFILE MOZ_CRASHREPORTER_DISABLE=1 \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_REAL_PROXY_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_REAL_PROXY_PASS" \
  "$runtime" --profile "$profile" \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "$NAIVEFOX_REAL_PROXY_URL" >"$client_log" 2>&1 &
client_pid=$!

for ((i = 0; i < 150; i++)); do
  if rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"; then
    break
  fi
  kill -0 "$client_pid" 2>/dev/null || {
    printf 'NaiveFox exited before SOCKS readiness\n' >&2
    exit 1
  }
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"

start_monotonic=$(python3 -c 'import time; print(time.monotonic())')
python3 - "$client_pid" "$proxy_port" "$start_monotonic" "$samples" <<'PY' &
import hashlib
import os
import pathlib
import signal
import sys
import time

pid = int(sys.argv[1])
proxy_port = int(sys.argv[2])
started = float(sys.argv[3])
destination = sys.argv[4]
stopping = False

def stop(_signum, _frame):
    global stopping
    stopping = True

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def status_values():
    values = {}
    with open(f"/proc/{pid}/status", encoding="utf-8") as source:
        for line in source:
            if line.startswith(("VmRSS:", "Threads:")):
                key, value = line.split(":", 1)
                values[key] = int(value.split()[0])
    return values

def socket_inodes():
    inodes = set()
    for entry in pathlib.Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[8:-1])
    matches = []
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(table, encoding="ascii").read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            remote_port = int(fields[2].rsplit(":", 1)[1], 16)
            state = fields[3]
            inode = fields[9]
            if inode in inodes and remote_port == proxy_port and state == "01":
                matches.append(inode)
    return sorted(set(matches), key=int)

with open(destination, "w", encoding="utf-8", buffering=1) as output:
    output.write("elapsed_s\trss_kib\tthreads\tfds\tproxy_sockets\tepoch_ids\n")
    while not stopping and os.path.exists(f"/proc/{pid}/status"):
        try:
            values = status_values()
            fds = sum(1 for _ in pathlib.Path(f"/proc/{pid}/fd").iterdir())
            inodes = socket_inodes()
            epochs = ",".join(hashlib.sha256(value.encode()).hexdigest()[:16]
                              for value in inodes)
            output.write(
                f"{time.monotonic() - started:.3f}\t{values['VmRSS']}\t"
                f"{values['Threads']}\t{fds}\t{len(inodes)}\t{epochs}\n")
        except (FileNotFoundError, ProcessLookupError):
            break
        time.sleep(1)
PY
monitor_pid=$!

probe_url=https://raw.githubusercontent.com/klzgrad/forwardproxy/d62c80d3dd2c706b6b87579844d2397bddd18317/README.md
curl --silent --show-error --fail --location --connect-timeout 10 --max-time 30 \
  "$probe_url" --output "$run_dir/baseline.body"
baseline_hash=$(sha256sum "$run_dir/baseline.body" | cut -d ' ' -f 1)
baseline_bytes=$(wc -c <"$run_dir/baseline.body")

printf '%s\n' \
  $'probe\tscheduled_s\tstarted_s\tcompleted_s\tcurl_rc\thttp_code\tbytes\ttime_connect_s\ttime_starttransfer_s\ttime_total_s\tspeed_download_bps\tsha_match' \
  >"$metrics"

schedule=(0 30 60 90 120 240 270 300 330 360 480 510 540 570 600)
failures=0

monotonic_elapsed() {
  python3 - "$start_monotonic" <<'PY'
import sys
import time
print(f"{time.monotonic() - float(sys.argv[1]):.3f}")
PY
}

wait_until() {
  local offset=$1
  while true; do
    kill -0 "$client_pid" 2>/dev/null || {
      printf 'NaiveFox exited during the soak interval\n' >&2
      return 1
    }
    local elapsed
    elapsed=$(monotonic_elapsed)
    if python3 - "$elapsed" "$offset" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)
PY
    then
      return 0
    fi
    sleep 1
  done
}

for index in "${!schedule[@]}"; do
  offset=${schedule[$index]}
  wait_until "$offset"
  started=$(monotonic_elapsed)
  body="$run_dir/probe-$index.body"
  curl_metric="$run_dir/probe-$index.curl"
  set +e
  curl --silent --show-error --fail --location \
    --connect-timeout 10 --max-time 20 --noproxy '' \
    --socks5-hostname "127.0.0.1:$socks_port" \
    "$probe_url?soak=$index" --output "$body" \
    --write-out $'%{http_code}\t%{size_download}\t%{time_connect}\t%{time_starttransfer}\t%{time_total}\t%{speed_download}\n' \
    >"$curl_metric"
  curl_status=$?
  set -e
  completed=$(monotonic_elapsed)

  http_code=000
  bytes=0
  time_connect=0
  time_starttransfer=0
  time_total=0
  speed_download=0
  if [[ -s $curl_metric ]]; then
    IFS=$'\t' read -r http_code bytes time_connect time_starttransfer \
      time_total speed_download <"$curl_metric"
  fi
  sha_match=no
  if [[ $curl_status -eq 0 && $http_code == 200 && -f $body ]]; then
    probe_hash=$(sha256sum "$body" | cut -d ' ' -f 1)
    if [[ $probe_hash == "$baseline_hash" && $bytes -eq $baseline_bytes ]]; then
      sha_match=yes
    fi
  fi
  if [[ $curl_status -ne 0 || $http_code != 200 || $sha_match != yes ]]; then
    ((failures += 1))
  fi
  printf '%d\t%d\t%s\t%s\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$((index + 1))" "$offset" "$started" "$completed" "$curl_status" \
    "$http_code" "$bytes" "$time_connect" "$time_starttransfer" \
    "$time_total" "$speed_download" "$sha_match" >>"$metrics"
  rm -f -- "$body" "$curl_metric"
done

kill -0 "$client_pid" 2>/dev/null || {
  printf 'NaiveFox exited before the final probe completed\n' >&2
  exit 1
}
sleep 2
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

if kill -0 "$monitor_pid" 2>/dev/null; then
  kill -TERM "$monitor_pid" 2>/dev/null || true
fi
wait "$monitor_pid" 2>/dev/null || true
monitor_pid=

padding_count=$(rg -c '^Padding negotiated: yes$' "$client_log")
if [[ $padding_count -ne ${#schedule[@]} ]]; then
  printf 'unexpected negotiated tunnel count: %d\n' "$padding_count" >&2
  exit 1
fi
if rg -q '^Padding negotiated: no$' "$client_log"; then
  printf 'real server unexpectedly fell back to raw payload mode\n' >&2
  exit 1
fi
if rg -F "$NAIVEFOX_REAL_PROXY_USER" "$client_log" ||
   rg -F "$NAIVEFOX_REAL_PROXY_PASS" "$client_log"; then
  printf 'proxy credentials appeared in client output\n' >&2
  exit 1
fi

python3 - "$metrics" "$samples" "$summary" "$baseline_hash" \
  "$baseline_bytes" "$padding_count" "$client_status" "$failures" <<'PY'
import csv
import math
import statistics
import sys

(metrics_path, samples_path, summary_path, expected_hash, expected_bytes,
 padding_count, client_status, failure_count) = sys.argv[1:]
expected_bytes = int(expected_bytes)
failure_count = int(failure_count)

with open(metrics_path, newline="", encoding="utf-8") as source:
    probes = list(csv.DictReader(source, delimiter="\t"))
with open(samples_path, newline="", encoding="utf-8") as source:
    samples = list(csv.DictReader(source, delimiter="\t"))
if len(probes) != 15:
    raise SystemExit(f"expected 15 probes, got {len(probes)}")
if not samples:
    raise SystemExit("resource monitor produced no samples")

successes = sum(row["curl_rc"] == "0" and row["http_code"] == "200" and
                int(float(row["bytes"])) == expected_bytes and
                row["sha_match"] == "yes" for row in probes)
timeouts = sum(row["curl_rc"] == "28" for row in probes)
latencies = [float(row["time_total_s"]) for row in probes
             if row["curl_rc"] == "0"]

def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return (ordered[lower] * (upper - position) +
            ordered[upper] * (position - lower))

warm = [row for row in samples if float(row["elapsed_s"]) >= 5]
if not warm:
    warm = samples
rss = [int(row["rss_kib"]) for row in warm]
fds = [int(row["fds"]) for row in warm]
threads = [int(row["threads"]) for row in warm]
epochs = set()
for row in samples:
    epochs.update(filter(None, row["epoch_ids"].split(",")))
max_proxy_sockets = max(int(row["proxy_sockets"]) for row in samples)

rss_delta = rss[-1] - rss[0]
fd_delta = fds[-1] - fds[0]
thread_delta = threads[-1] - threads[0]
resource_gate = rss_delta <= 32768 and fd_delta <= 8 and thread_delta <= 2
functional_gate = successes == 15 and timeouts == 0 and failure_count == 0
sample_gate = len(samples) >= 590

with open(summary_path, "w", encoding="utf-8") as output:
    output.write("test=real-server-periodic-soak\n")
    output.write("duration_target_seconds=600\n")
    output.write("processes=1\n")
    output.write("max_connections_option=omitted\n")
    output.write("schedule_seconds=0,30,60,90,120,240,270,300,330,360,480,510,540,570,600\n")
    output.write(f"attempts={len(probes)}\n")
    output.write(f"successes={successes}\n")
    output.write(f"timeouts={timeouts}\n")
    output.write(f"bytes_each={expected_bytes}\n")
    output.write(f"bytes_total={sum(int(float(row['bytes'])) for row in probes)}\n")
    output.write(f"sha256={expected_hash}\n")
    output.write(f"integrity_matches={sum(row['sha_match'] == 'yes' for row in probes)}\n")
    output.write(f"latency_p50_seconds={percentile(latencies, .50):.6f}\n")
    output.write(f"latency_p95_seconds={percentile(latencies, .95):.6f}\n")
    output.write(f"latency_max_seconds={max(latencies):.6f}\n")
    output.write(f"resource_samples={len(samples)}\n")
    output.write(f"rss_baseline_kib={rss[0]}\n")
    output.write(f"rss_peak_kib={max(rss)}\n")
    output.write(f"rss_final_kib={rss[-1]}\n")
    output.write(f"rss_final_delta_kib={rss_delta}\n")
    output.write(f"fd_baseline={fds[0]}\n")
    output.write(f"fd_peak={max(fds)}\n")
    output.write(f"fd_final={fds[-1]}\n")
    output.write(f"fd_final_delta={fd_delta}\n")
    output.write(f"threads_baseline={threads[0]}\n")
    output.write(f"threads_peak={max(threads)}\n")
    output.write(f"threads_final={threads[-1]}\n")
    output.write(f"threads_final_delta={thread_delta}\n")
    output.write(f"outer_tcp_epochs={len(epochs)}\n")
    output.write(f"outer_tcp_reconnects={max(0, len(epochs) - 1)}\n")
    output.write(f"outer_tcp_max_simultaneous={max_proxy_sockets}\n")
    output.write(f"padding_negotiated={padding_count}\n")
    output.write("padding_raw_fallback=0\n")
    output.write(f"client_exit_status={client_status}\n")
    output.write(f"functional_gate={'pass' if functional_gate else 'fail'}\n")
    output.write(f"resource_gate={'pass' if resource_gate else 'fail'}\n")
    output.write(f"sampling_gate={'pass' if sample_gate else 'fail'}\n")

if not functional_gate:
    raise SystemExit("functional soak gate failed")
if not resource_gate:
    raise SystemExit("resource stability gate failed")
if not sample_gate:
    raise SystemExit("liveness sampling gate failed")
PY

success=1
printf '%s\n' 'NaiveFox 10-minute real-server soak passed'
printf 'sanitized summary: %s\n' "$SOURCE_ROOT/artifacts/real-server-soak-summary.txt"
