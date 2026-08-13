#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

capture_pid=
firefox_pid=
naivefox_pid=
capture_dir=
success=0

stop_pid() {
  local pid=${1:-}
  if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
  fi
  [[ -z $pid ]] || wait "$pid" 2>/dev/null || true
}

stop_capture() {
  if [[ -n $capture_pid ]] && kill -0 "$capture_pid" 2>/dev/null; then
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  [[ -z $capture_pid ]] || wait "$capture_pid" 2>/dev/null || true
  capture_pid=
}

cleanup() {
  local status=$?
  stop_capture
  stop_pid "$firefox_pid"
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ -n $capture_dir ]]; then
    case "$capture_dir" in
      "$STATE_ROOT"/observer-captures/*)
        if [[ $status -eq 0 && $success -eq 1 ]]; then
          rm -rf -- "$capture_dir"
        else
          printf 'observer comparison failed; private diagnostics preserved at %s\n' \
            "$capture_dir" >&2
        fi
        ;;
      *) printf 'refusing to remove unexpected capture path: %s\n' \
           "$capture_dir" >&2 ;;
    esac
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in dumpcap tshark curl getcap openssl python3 readelf rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required observer tool not found: %s\n' "$tool" >&2
    exit 1
  }
done

dumpcap_path=$(command -v dumpcap)
dumpcap_caps=$(getcap "$dumpcap_path" 2>/dev/null || true)
if [[ $EUID -ne 0 ]] &&
   [[ $dumpcap_caps != *cap_net_admin* || $dumpcap_caps != *cap_net_raw* ]]; then
  printf '%s needs cap_net_raw and cap_net_admin\n' "$dumpcap_path" >&2
  exit 1
fi

"$INTEGRATION_DIR/start.sh"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

BIN="$OBJDIR/dist/bin"
for binary in firefox naivefox libssl3.so libxul.so; do
  [[ -f $BIN/$binary ]] || {
    printf 'required Firefox build artifact is missing: %s\n' "$BIN/$binary" >&2
    exit 1
  }
done

capture_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
capture_dir="$STATE_ROOT/observer-captures/$capture_id"
mkdir -m 0700 -p "$capture_dir"

export LD_LIBRARY_PATH="$BIN${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

start_capture() {
  local pcap=$1
  local log=$2
  : >"$log"
  chmod 0600 "$log"
  # WSL's synthetic loopback is not exposed reliably through dumpcap's `lo`
  # device. `any` observes the same loopback flow once with Linux cooked
  # framing and is the interface used by the project's decrypted comparison.
  dumpcap -q -i any -f "tcp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:40 -a filesize:10240 -w "$pcap" >"$log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      cat "$log" >&2
      printf 'dumpcap exited before capture readiness\n' >&2
      return 1
    }
    [[ -s $pcap ]] && return 0
    sleep 0.1
  done
  printf 'timed out waiting for dumpcap capture file\n' >&2
  return 1
}

wait_for_log() {
  local pid=$1
  local log=$2
  local pattern=$3
  for ((i = 0; i < 100; i++)); do
    rg -q "$pattern" "$log" && return 0
    kill -0 "$pid" 2>/dev/null || {
      printf 'process exited before readiness marker: %s\n' "$pattern" >&2
      return 1
    }
    sleep 0.1
  done
  printf 'timed out waiting for readiness marker: %s\n' "$pattern" >&2
  return 1
}

reference_profile="$capture_dir/reference-profile"
mkdir -m 0700 "$reference_profile"
cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$reference_profile/"
cat >"$reference_profile/user.js" <<'EOF'
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
EOF
chmod 0600 "$reference_profile/user.js"

canary="observer-$(openssl rand -hex 16)"
reference_pcap="$capture_dir/reference.pcapng"
reference_log="$capture_dir/reference-firefox.log"
: >"$reference_log"
chmod 0600 "$reference_log"
start_capture "$reference_pcap" "$capture_dir/reference-dumpcap.log"
timeout 25 env -u SSLKEYLOGFILE MOZ_HEADLESS=1 \
  "$BIN/firefox" --headless --new-instance --no-remote \
  --profile "$reference_profile" \
  --screenshot "$capture_dir/reference.png" \
  "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/observer?size=4194304&canary=$canary" \
  >"$reference_log" 2>&1 &
firefox_pid=$!
set +e
wait "$firefox_pid"
firefox_status=$?
set -e
firefox_pid=
stop_capture
if [[ $firefox_status -ne 0 && $firefox_status -ne 124 ]]; then
  printf 'reference Firefox exited with status %s; evaluating capture\n' \
    "$firefox_status" >&2
fi

naivefox_pcap="$capture_dir/naivefox.pcapng"
naivefox_log="$capture_dir/naivefox.log"
: >"$naivefox_log"
chmod 0600 "$naivefox_log"
socks_port=$(python3 -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
start_capture "$naivefox_pcap" "$capture_dir/naivefox-dumpcap.log"
env -u SSLKEYLOGFILE \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
  "$BIN/naivefox" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --max-connections 2 >"$naivefox_log" 2>&1 &
naivefox_pid=$!
wait_for_log "$naivefox_pid" "$naivefox_log" '^SOCKS5 listening on '
for request in 1 2; do
  timeout 30 curl --fail --silent --show-error --noproxy '' \
    --socks5-hostname "127.0.0.1:$socks_port" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/observer?size=2097152&canary=$canary&request=$request" \
    >/dev/null
done
if ! timeout 30 tail --pid="$naivefox_pid" -f /dev/null; then
  printf 'NaiveFox did not stop after observer requests\n' >&2
  exit 1
fi
wait "$naivefox_pid"
naivefox_pid=
stop_capture

for pcap in "$reference_pcap" "$naivefox_pcap"; do
  [[ -s $pcap ]] || {
    printf 'observer capture is empty: %s\n' "$pcap" >&2
    exit 1
  }
  if rg -a -F "$canary" "$pcap"; then
    printf 'encrypted request canary was visible in capture plaintext\n' >&2
    exit 1
  fi
done

TSHARK_FIELDS=(-T fields -E header=y -E separator=, -E quote=d \
  -E occurrence=a '-E' 'aggregator=;')

extract_client_hello() {
  local pcap=$1
  local output=$2
  tshark -r "$pcap" -d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
    "${TSHARK_FIELDS[@]}" -e tcp.stream -e tls.handshake.length \
    -e tls.record.version -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite -e tls.handshake.extension.type \
    -e tls.handshake.extensions_supported_group \
    -e tls.handshake.sig_hash_alg -e tls.handshake.extensions_key_share_group \
    -e tls.handshake.extensions_alpn_str \
    -e tls.handshake.extensions_server_name >"$output"
}

extract_server_hello() {
  local pcap=$1
  local output=$2
  tshark -r "$pcap" -d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
    -Y "tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==2" \
    "${TSHARK_FIELDS[@]}" -e tcp.stream -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite \
    -e tls.handshake.extensions_key_share_selected_group >"$output"
}

extract_records() {
  local pcap=$1
  local output=$2
  tshark -r "$pcap" -d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
    -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && (tcp.len>0 || tcp.flags.reset==1)" \
    "${TSHARK_FIELDS[@]}" -e frame.time_relative -e tcp.stream \
    -e tcp.srcport -e tcp.dstport -e tcp.len -e tcp.flags.reset \
    -e tcp.analysis.retransmission -e tls.record.content_type \
    -e tls.record.length >"$output"
}

for side in reference naivefox; do
  pcap="$capture_dir/$side.pcapng"
  extract_client_hello "$pcap" "$capture_dir/$side-clienthello.csv"
  extract_server_hello "$pcap" "$capture_dir/$side-serverhello.csv"
  extract_records "$pcap" "$capture_dir/$side-records.csv"
  for extract in clienthello serverhello records; do
    if [[ $(wc -l <"$capture_dir/$side-$extract.csv") -lt 2 ]]; then
      printf 'observer extract has no data rows: %s-%s\n' "$side" "$extract" >&2
      exit 1
    fi
  done
done

safe_dir="$STATE_ROOT/observer-safe/$capture_id"
mkdir -m 0700 -p "$safe_dir"
python3 - "$capture_dir" "$safe_dir/summary.txt" \
  "$NAIVEFOX_FIXTURE_PROXY_PORT" <<'PY'
import csv
import hashlib
import math
import os
import statistics
import sys

root, destination, proxy_port = sys.argv[1:]

def rows(name):
    with open(os.path.join(root, name), newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))

def canonical(name):
    data = rows(name)
    if len(data) != 1:
        raise SystemExit(f"expected exactly one row in {name}, got {len(data)}")
    return tuple(value for key, value in data[0].items() if key != "tcp.stream")

reference_hello = canonical("reference-clienthello.csv")
naivefox_hello = canonical("naivefox-clienthello.csv")
reference_server = canonical("reference-serverhello.csv")
naivefox_server = canonical("naivefox-serverhello.csv")
if reference_hello != naivefox_hello:
    raise SystemExit("visible ClientHello fields differ")
if reference_server != naivefox_server:
    raise SystemExit("visible ServerHello fields differ")

def percentile(values, fraction):
    if not values:
        return 0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return round(ordered[lower] * (upper - position) +
                 ordered[upper] * (position - lower))

def summarize(name):
    data = rows(f"{name}-records.csv")
    # Capturing a WSL loopback flow through the synthetic `any` device exposes
    # the transmitted packet and its local receive copy. Wireshark labels the
    # latter as a retransmission. It contains no separately decoded TLS record,
    # so exclude those duplicate rows from external-wire aggregates.
    duplicate_rows = sum(bool(row["tcp.analysis.retransmission"])
                         for row in data)
    data = [row for row in data if not row["tcp.analysis.retransmission"]]
    streams = {row["tcp.stream"] for row in data if row["tcp.stream"]}
    resets = sum(row["tcp.flags.reset"].lower() in {"1", "true", "yes"}
                 for row in data)
    times = [float(row["frame.time_relative"]) for row in data]
    client_tcp = []
    server_tcp = []
    client_tls = []
    server_tls = []
    event_times = []
    for row in data:
        outbound = row["tcp.dstport"] == proxy_port
        tcp_length = int(row["tcp.len"] or 0)
        (client_tcp if outbound else server_tcp).append(tcp_length)
        for value in filter(None, row["tls.record.length"].split(";")):
            (client_tls if outbound else server_tls).append(int(value))
            event_times.append(float(row["frame.time_relative"]))
    bursts = 0
    previous = None
    for timestamp in sorted(set(event_times)):
        if previous is None or timestamp - previous > 0.100:
            bursts += 1
        previous = timestamp
    result = {
        "tcp_streams": len(streams),
        "duration_ms": round((max(times) - min(times)) * 1000) if times else 0,
        "resets": resets,
        "wsl_loopback_duplicate_rows_excluded": duplicate_rows,
        "bursts_100ms": bursts,
        "client_tcp_bytes": sum(client_tcp),
        "server_tcp_bytes": sum(server_tcp),
    }
    for direction, values in (("client", client_tls), ("server", server_tls)):
        result[f"{direction}_tls_records"] = len(values)
        result[f"{direction}_tls_bytes"] = sum(values)
        for label, fraction in (("p10", .10), ("p50", .50),
                                ("p90", .90), ("p95", .95),
                                ("p99", .99)):
            result[f"{direction}_tls_length_{label}"] = percentile(values, fraction)
    return result

reference = summarize("reference")
naivefox = summarize("naivefox")
for name, data in (("reference", reference), ("naivefox", naivefox)):
    if data["tcp_streams"] != 1:
        raise SystemExit(f"{name} used {data['tcp_streams']} outer TCP streams")

fingerprint = hashlib.sha256("\x1f".join(reference_hello).encode()).hexdigest()
with open(destination, "w", encoding="utf-8") as output:
    output.write("observer_scope=encrypted_transport_only\n")
    output.write("capture_interface=any_loopback_flow\n")
    output.write("tls_keylog=disabled\n")
    output.write("clienthello_visible_fields_equal=yes\n")
    output.write("serverhello_visible_fields_equal=yes\n")
    output.write(f"clienthello_canonical_sha256={fingerprint}\n")
    for name, data in (("reference", reference), ("naivefox", naivefox)):
        for key, value in data.items():
            output.write(f"{name}_{key}={value}\n")
    output.write("plaintext_canary=absent\n")
    output.write("raw_capture_material=deleted_after_success\n")
PY
chmod 0600 "$safe_dir/summary.txt"

{
  printf 'capture_revision=%s\n' "$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
  printf 'reference_binary=%s\n' \
    "$(readelf -n "$BIN/firefox" | sed -n 's/^ *Build ID: //p' | head -n 1)"
  printf 'naivefox_binary=%s\n' \
    "$(readelf -n "$BIN/naivefox" | sed -n 's/^ *Build ID: //p' | head -n 1)"
  printf 'libxul_sha256=%s\n' "$(sha256sum "$BIN/libxul.so" | cut -d' ' -f1)"
  printf 'libssl3_sha256=%s\n' "$(sha256sum "$BIN/libssl3.so" | cut -d' ' -f1)"
} >>"$safe_dir/summary.txt"

if rg -F "$NAIVEFOX_FIXTURE_USER" "$safe_dir" ||
   rg -F "$NAIVEFOX_FIXTURE_PASS" "$safe_dir" ||
   rg -F "$canary" "$safe_dir"; then
  printf 'sensitive value reached safe observer output\n' >&2
  exit 1
fi

success=1
printf 'Firefox/NaiveFox external-observer comparison passed\n'
printf 'sanitized aggregates: %s\n' "$safe_dir"
