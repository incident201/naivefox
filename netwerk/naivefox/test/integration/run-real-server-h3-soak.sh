#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

: "${NAIVEFOX_REAL_PROXY_URL:?set NAIVEFOX_REAL_PROXY_URL}"
: "${NAIVEFOX_REAL_PROXY_USER:?set NAIVEFOX_REAL_PROXY_USER}"
: "${NAIVEFOX_REAL_PROXY_PASS:?set NAIVEFOX_REAL_PROXY_PASS}"

for tool in curl python3 realpath rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required H3 soak-test tool not found: %s\n' "$tool" >&2
    exit 1
  }
done

proxy_port=$(python3 - "$NAIVEFOX_REAL_PROXY_URL" <<'PY'
import sys
import urllib.parse

parsed = urllib.parse.urlsplit(sys.argv[1])
if (
    parsed.scheme != "https"
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
    or parsed.path not in ("", "/")
):
    raise SystemExit("real proxy URL must be an HTTPS origin without credentials")
print(parsed.port or 443)
PY
)

runtime=${NAIVEFOX_REAL_RUNTIME:-}
runtime_kind=staged
if [[ -z $runtime && \
      -x $OBJDIR/naivefox-linux-x86_64-h3-final/run-naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-h3-final/run-naivefox"
elif [[ -z $runtime && \
      -x $OBJDIR/naivefox-linux-x86_64-h3-test/run-naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-h3-test/run-naivefox"
elif [[ -z $runtime && -x $OBJDIR/naivefox-linux-x86_64-h3/run-naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-h3/run-naivefox"
elif [[ -z $runtime && -x $OBJDIR/naivefox-linux-x86_64-final/run-naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64-final/run-naivefox"
elif [[ -z $runtime && -x $OBJDIR/naivefox-linux-x86_64/run-naivefox ]]; then
  runtime="$OBJDIR/naivefox-linux-x86_64/run-naivefox"
elif [[ -z $runtime ]]; then
  runtime="$OBJDIR/dist/bin/naivefox"
  runtime_kind=objdir
fi
if [[ $runtime != /* || ! -x $runtime ]]; then
  printf 'NaiveFox runtime must be an absolute executable path\n' >&2
  exit 1
fi
runtime=$(realpath -- "$runtime")
if [[ -e $OBJDIR/dist/bin/naivefox && \
      $runtime == "$(realpath -- "$OBJDIR/dist/bin/naivefox")" ]]; then
  runtime_kind=objdir
fi

runtime_env=(env -u SSLKEYLOGFILE -u LD_PRELOAD)
if [[ $runtime_kind == objdir ]]; then
  runtime_env+=("LD_LIBRARY_PATH=$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
else
  runtime_env+=(-u LD_LIBRARY_PATH)
fi

state_root="$OBJDIR/naivefox-real-h3-soak"
mkdir -m 0700 -p "$state_root" "$SOURCE_ROOT/artifacts"
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
    cp -- "$summary" "$SOURCE_ROOT/artifacts/real-server-h3-soak-summary.txt"
    chmod 0600 "$SOURCE_ROOT/artifacts/real-server-h3-soak-summary.txt"
    case $(realpath -- "$run_dir") in
      "$(realpath -- "$state_root")"/run.*) rm -rf -- "$run_dir" ;;
      *) printf 'refusing to remove unexpected H3 soak path\n' >&2 ;;
    esac
  else
    if [[ -f $client_log ]]; then
      sanitized_client_log="$run_dir/naivefox.sanitized.log"
      sanitize_stream "$NAIVEFOX_REAL_PROXY_USER" \
        "$NAIVEFOX_REAL_PROXY_PASS" <"$client_log" \
        >"$sanitized_client_log"
      mv -- "$sanitized_client_log" "$client_log"
      cp -- "$client_log" \
        "$SOURCE_ROOT/artifacts/real-server-h3-soak-client-failure.log"
      chmod 0600 \
        "$SOURCE_ROOT/artifacts/real-server-h3-soak-client-failure.log"
    fi
    printf 'real-server H3 soak failed; private state preserved at %s\n' \
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

probe_url=https://raw.githubusercontent.com/klzgrad/forwardproxy/d62c80d3dd2c706b6b87579844d2397bddd18317/README.md
normal_url=https://example.com/

curl --silent --show-error --fail --location --noproxy '*' \
  --connect-timeout 10 --max-time 30 "$probe_url" \
  --output "$run_dir/baseline.body"
baseline_hash=$(sha256sum "$run_dir/baseline.body" | cut -d ' ' -f 1)
baseline_bytes=$(wc -c <"$run_dir/baseline.body")

socks_port=$(choose_port)
"${runtime_env[@]}" MOZ_CRASHREPORTER_DISABLE=1 \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_REAL_PROXY_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_REAL_PROXY_PASS" \
  "$runtime" --profile "$profile" --protocol h3 \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "$NAIVEFOX_REAL_PROXY_URL" >"$client_log" 2>&1 &
client_pid=$!

for ((i = 0; i < 150; i++)); do
  if rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"; then
    break
  fi
  kill -0 "$client_pid" 2>/dev/null || {
    printf 'NaiveFox exited before H3 SOCKS readiness\n' >&2
    exit 1
  }
  sleep 0.1
done
rg -q "^SOCKS5 listening on 127.0.0.1:$socks_port$" "$client_log"

curl_socks=(
  --silent --show-error --fail --location --noproxy ''
  --connect-timeout 10 --max-time 30
  --socks5-hostname "127.0.0.1:$socks_port"
)

normal_metric="$run_dir/preflight-normal.curl"
curl "${curl_socks[@]}" "$normal_url" \
  --output "$run_dir/preflight-normal.body" \
  --write-out $'%{http_code}\t%{size_download}\n' >"$normal_metric"
IFS=$'\t' read -r normal_status normal_bytes <"$normal_metric"
if [[ $normal_status != 200 || $normal_bytes -le 0 ]]; then
  printf 'normal HTTPS preflight failed\n' >&2
  exit 1
fi

integrity_metric="$run_dir/preflight-integrity.curl"
curl "${curl_socks[@]}" "$probe_url?preflight=h3" \
  --output "$run_dir/preflight-integrity.body" \
  --write-out $'%{http_code}\t%{size_download}\n' >"$integrity_metric"
IFS=$'\t' read -r integrity_status integrity_bytes <"$integrity_metric"
integrity_hash=$(sha256sum "$run_dir/preflight-integrity.body" | cut -d ' ' -f 1)
if [[ $integrity_status != 200 || $integrity_bytes -ne $baseline_bytes || \
      $integrity_hash != "$baseline_hash" ]]; then
  printf 'integrity preflight failed\n' >&2
  exit 1
fi
preflight_padding_count=$(rg -c '^Padding negotiated: yes$' "$client_log" || true)
preflight_h3_count=$(rg -c '^Outer protocol: h3$' "$client_log" || true)
if [[ $preflight_padding_count -ne 2 || $preflight_h3_count -ne 2 ]]; then
  printf 'strict H3 preflight did not establish two padded H3 tunnels\n' >&2
  exit 1
fi

printf '%s\n' \
  $'probe\tevent\tlane\tscheduled_s\tstarted_s\tcompleted_s\tcurl_rc\thttp_code\tbytes\ttime_connect_s\ttime_starttransfer_s\ttime_total_s\tspeed_download_bps\tsha_match' \
  >"$metrics"

start_monotonic=$(python3 -c 'import time; print(time.monotonic())')
python3 - "$client_pid" "$proxy_port" "$start_monotonic" "$samples" <<'PY' &
import hashlib
import os
import pathlib
import signal
import sys
import time

root_pid = int(sys.argv[1])
proxy_port = int(sys.argv[2])
started = float(sys.argv[3])
destination = sys.argv[4]
stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def process_tree():
    parents = {}
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            with open(entry / "status", encoding="utf-8") as source:
                values = {}
                for line in source:
                    if line.startswith(("PPid:", "VmRSS:", "Threads:")):
                        key, value = line.split(":", 1)
                        values[key] = int(value.split()[0])
            parents[int(entry.name)] = values
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    if root_pid not in parents:
        return [], parents
    tree = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, values in parents.items():
            if pid not in tree and values.get("PPid") in tree:
                tree.add(pid)
                changed = True
    return sorted(tree), parents


def socket_inodes(pids):
    inodes = set()
    fd_count = 0
    for pid in pids:
        try:
            entries = list(pathlib.Path(f"/proc/{pid}/fd").iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        fd_count += len(entries)
        for entry in entries:
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target.startswith("socket:[") and target.endswith("]"):
                inodes.add(target[8:-1])
    return inodes, fd_count


def socket_counts(inodes):
    udp = set()
    udp_proxy_connected = set()
    tcp_proxy = set()
    for protocol, tables in (
        ("udp", ("/proc/net/udp", "/proc/net/udp6")),
        ("tcp", ("/proc/net/tcp", "/proc/net/tcp6")),
    ):
        for table in tables:
            try:
                lines = open(table, encoding="ascii").read().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[9] not in inodes:
                    continue
                inode = fields[9]
                remote_port = int(fields[2].rsplit(":", 1)[1], 16)
                if protocol == "udp":
                    udp.add(inode)
                    if remote_port == proxy_port:
                        udp_proxy_connected.add(inode)
                elif remote_port == proxy_port:
                    tcp_proxy.add(inode)
    return udp, udp_proxy_connected, tcp_proxy


with open(destination, "w", encoding="utf-8", buffering=1) as output:
    output.write(
        "elapsed_s\tpids\trss_kib\tthreads\tfds\tudp_sockets\t"
        "udp_proxy_connected\ttcp_proxy_sockets\tudp_epoch_ids\n"
    )
    sample_number = 0
    while not stopping and sample_number <= 600:
        deadline = started + sample_number
        delay = deadline - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        pids, statuses = process_tree()
        if not pids:
            break
        inodes, fd_count = socket_inodes(pids)
        udp, udp_proxy_connected, tcp_proxy = socket_counts(inodes)
        rss = sum(statuses[pid].get("VmRSS", 0) for pid in pids)
        threads = sum(statuses[pid].get("Threads", 0) for pid in pids)
        epochs = ",".join(
            hashlib.sha256(value.encode()).hexdigest()[:16]
            for value in sorted(udp, key=int)
        )
        output.write(
            f"{time.monotonic() - started:.3f}\t{len(pids)}\t{rss}\t"
            f"{threads}\t{fd_count}\t{len(udp)}\t"
            f"{len(udp_proxy_connected)}\t{len(tcp_proxy)}\t{epochs}\n"
        )
        sample_number += 1
PY
monitor_pid=$!

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
      printf 'NaiveFox exited during the H3 soak interval\n' >&2
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
    sleep 0.25
  done
}

run_probe() {
  local probe=$1
  local event=$2
  local lane=$3
  local scheduled=$4
  local row=$5
  local body="$run_dir/probe-$probe.body"
  local curl_metric="$run_dir/probe-$probe.curl"
  local started completed curl_status

  started=$(monotonic_elapsed)
  set +e
  curl --silent --show-error --fail --location --noproxy '' \
    --connect-timeout 10 --max-time 20 \
    --socks5-hostname "127.0.0.1:$socks_port" \
    "$probe_url?h3soak=$probe" --output "$body" \
    --write-out $'%{http_code}\t%{size_download}\t%{time_connect}\t%{time_starttransfer}\t%{time_total}\t%{speed_download}\n' \
    >"$curl_metric"
  curl_status=$?
  set -e
  completed=$(monotonic_elapsed)

  local http_code=000
  local bytes=0
  local time_connect=0
  local time_starttransfer=0
  local time_total=0
  local speed_download=0
  if [[ -s $curl_metric ]]; then
    IFS=$'\t' read -r http_code bytes time_connect time_starttransfer \
      time_total speed_download <"$curl_metric"
  fi
  local sha_match=no
  if [[ $curl_status -eq 0 && $http_code == 200 && -f $body ]]; then
    local probe_hash
    probe_hash=$(sha256sum "$body" | cut -d ' ' -f 1)
    if [[ $probe_hash == "$baseline_hash" && $bytes -eq $baseline_bytes ]]; then
      sha_match=yes
    fi
  fi
  printf '%d\t%d\t%d\t%d\t%s\t%s\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$probe" "$event" "$lane" "$scheduled" "$started" "$completed" \
    "$curl_status" "$http_code" "$bytes" "$time_connect" \
    "$time_starttransfer" "$time_total" "$speed_download" "$sha_match" \
    >"$row"
  rm -f -- "$body" "$curl_metric"
}

event_offsets=(0 30 60 90 120 240 270 300 330 360 480 510 540 570)
event_widths=(1 1 4 1 1 1 4 1 4 1 1 4 1 1)
probe_number=0

for event_index in "${!event_offsets[@]}"; do
  offset=${event_offsets[$event_index]}
  width=${event_widths[$event_index]}
  event=$((event_index + 1))
  wait_until "$offset"
  probe_pids=()
  probe_rows=()
  for ((lane = 1; lane <= width; lane++)); do
    ((probe_number += 1))
    row="$run_dir/probe-$probe_number.row"
    probe_rows+=("$row")
    run_probe "$probe_number" "$event" "$lane" "$offset" "$row" &
    probe_pids+=("$!")
  done
  for probe_pid in "${probe_pids[@]}"; do
    wait "$probe_pid" || true
  done
  for row in "${probe_rows[@]}"; do
    if [[ ! -s $row ]]; then
      printf 'probe worker did not produce a metric row\n' >&2
      exit 1
    fi
    cat "$row" >>"$metrics"
    rm -f -- "$row"
  done
  kill -0 "$client_pid" 2>/dev/null || {
    printf 'NaiveFox exited during an H3 probe event\n' >&2
    exit 1
  }
done

wait_until 600.000
alive_at_600=yes
observation_completed=$(monotonic_elapsed)

if kill -0 "$monitor_pid" 2>/dev/null; then
  wait "$monitor_pid"
fi
monitor_pid=

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

expected_observation_probes=26
expected_tunnels=$((expected_observation_probes + 2))
padding_count=$(rg -c '^Padding negotiated: yes$' "$client_log" || true)
h3_count=$(rg -c '^Outer protocol: h3$' "$client_log" || true)
if [[ $padding_count -ne $expected_tunnels || $h3_count -ne $expected_tunnels ]]; then
  printf 'unexpected padded H3 tunnel count\n' >&2
  exit 1
fi
if rg -q -e '^Padding negotiated: no$' -e '^Outer protocol: h2$' "$client_log"; then
  printf 'strict H3 soak observed padding or H2 fallback\n' >&2
  exit 1
fi
if rg -F -- "$NAIVEFOX_REAL_PROXY_USER" "$client_log" ||
   rg -F -- "$NAIVEFOX_REAL_PROXY_PASS" "$client_log"; then
  printf 'proxy credentials appeared in client output\n' >&2
  exit 1
fi

python3 - "$metrics" "$samples" "$summary" "$baseline_hash" \
  "$baseline_bytes" "$padding_count" "$h3_count" "$client_status" \
  "$alive_at_600" "$observation_completed" <<'PY'
import csv
import math
import statistics
import sys

(
    metrics_path,
    samples_path,
    summary_path,
    expected_hash,
    expected_bytes,
    padding_count,
    h3_count,
    client_status,
    alive_at_600,
    observation_completed,
) = sys.argv[1:]
expected_bytes = int(expected_bytes)

with open(metrics_path, newline="", encoding="utf-8") as source:
    probes = list(csv.DictReader(source, delimiter="\t"))
with open(samples_path, newline="", encoding="utf-8") as source:
    samples = list(csv.DictReader(source, delimiter="\t"))
if len(probes) != 26:
    raise SystemExit(f"expected 26 observation probes, got {len(probes)}")
if not samples:
    raise SystemExit("resource monitor produced no samples")

successes = sum(
    row["curl_rc"] == "0"
    and row["http_code"] == "200"
    and int(float(row["bytes"])) == expected_bytes
    and row["sha_match"] == "yes"
    for row in probes
)
timeouts = sum(row["curl_rc"] == "28" for row in probes)
latencies = [
    float(row["time_total_s"])
    for row in probes
    if row["curl_rc"] == "0" and row["http_code"] == "200"
]


def percentile(values, fraction):
    if not values:
        return float("nan")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


warm = [row for row in samples if float(row["elapsed_s"]) >= 5]
if not warm:
    warm = samples
rss = [int(row["rss_kib"]) for row in warm]
fds = [int(row["fds"]) for row in warm]
threads = [int(row["threads"]) for row in warm]
pids = [int(row["pids"]) for row in samples]
udp = [int(row["udp_sockets"]) for row in samples]
udp_proxy_connected = [
    int(row["udp_proxy_connected"]) for row in samples
]
tcp_proxy = [int(row["tcp_proxy_sockets"]) for row in samples]
udp_epochs = set()
for row in samples:
    udp_epochs.update(filter(None, row["udp_epoch_ids"].split(",")))

rss_delta = rss[-1] - rss[0]
fd_delta = fds[-1] - fds[0]
thread_delta = threads[-1] - threads[0]
functional_gate = successes == 26 and timeouts == 0
resource_gate = rss_delta <= 32768 and fd_delta <= 8 and thread_delta <= 2
sampling_gate = len(samples) >= 590 and float(samples[-1]["elapsed_s"]) >= 599.9
transport_gate = (
    max(udp) > 0
    and max(tcp_proxy) == 0
)
liveness_gate = alive_at_600 == "yes" and float(observation_completed) >= 600.0

with open(summary_path, "w", encoding="utf-8") as output:
    output.write("test=real-server-strict-h3-periodic-soak\n")
    output.write("protocol=h3\n")
    output.write("duration_target_seconds=600.000\n")
    output.write(f"observation_completed_seconds={float(observation_completed):.3f}\n")
    output.write("processes=1\n")
    output.write("max_connections_option=omitted\n")
    output.write("event_schedule_seconds=0,30,60,90,120,240,270,300,330,360,480,510,540,570\n")
    output.write("parallel4_event_seconds=60,270,330,510\n")
    output.write("idle_windows_seconds=120-240,360-480,570-600\n")
    output.write(f"attempts={len(probes)}\n")
    output.write(f"successes={successes}\n")
    output.write(f"timeouts={timeouts}\n")
    output.write(f"bytes_each={expected_bytes}\n")
    output.write(f"bytes_total={sum(int(float(row['bytes'])) for row in probes)}\n")
    output.write(f"sha256={expected_hash}\n")
    output.write(f"integrity_matches={sum(row['sha_match'] == 'yes' for row in probes)}\n")
    output.write(f"latency_p50_seconds={percentile(latencies, .50):.6f}\n")
    output.write(f"latency_p95_seconds={percentile(latencies, .95):.6f}\n")
    output.write(f"latency_max_seconds={max(latencies) if latencies else float('nan'):.6f}\n")
    output.write(f"resource_samples={len(samples)}\n")
    output.write(f"process_tree_max_pids={max(pids)}\n")
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
    output.write(f"outer_udp_max_simultaneous={max(udp)}\n")
    output.write(
        "outer_udp_proxy_connected_max_simultaneous="
        f"{max(udp_proxy_connected)}\n"
    )
    output.write("outer_udp_connected_metric=diagnostic-only\n")
    output.write(f"outer_udp_socket_epochs={len(udp_epochs)}\n")
    output.write(f"outer_tcp_proxy_max_simultaneous={max(tcp_proxy)}\n")
    output.write(f"padding_negotiated={padding_count}\n")
    output.write("padding_raw_fallback=0\n")
    output.write(f"outer_h3_confirmations={h3_count}\n")
    output.write("outer_h2_confirmations=0\n")
    output.write(f"alive_at_600_seconds={alive_at_600}\n")
    output.write(f"client_exit_status={client_status}\n")
    output.write(f"functional_gate={'pass' if functional_gate else 'fail'}\n")
    output.write(f"resource_gate={'pass' if resource_gate else 'fail'}\n")
    output.write(f"sampling_gate={'pass' if sampling_gate else 'fail'}\n")
    output.write(f"transport_gate={'pass' if transport_gate else 'fail'}\n")
    output.write(f"liveness_gate={'pass' if liveness_gate else 'fail'}\n")

if not functional_gate:
    raise SystemExit("functional H3 soak gate failed")
if not resource_gate:
    raise SystemExit("resource stability gate failed")
if not sampling_gate:
    raise SystemExit("liveness sampling gate failed")
if not transport_gate:
    raise SystemExit("strict UDP/H3 transport gate failed")
if not liveness_gate:
    raise SystemExit("600-second liveness gate failed")
PY

success=1
printf '%s\n' 'NaiveFox strict H3 10-minute real-server soak passed'
printf 'sanitized summary: %s\n' \
  "$SOURCE_ROOT/artifacts/real-server-h3-soak-summary.txt"
