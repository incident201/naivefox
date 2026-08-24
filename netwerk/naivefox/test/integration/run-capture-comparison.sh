#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

capture_pid=
capture_stage_dir=
capture_stage_pcap=
capture_pcap=
firefox_pid=
naivefox_pid=
run_dir=
capture_dir=
success=0

stop_pid() {
  local pid=${1:-}
  if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 0.1
    done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  [[ -z $pid ]] || wait "$pid" 2>/dev/null || true
}

stop_capture() {
  if [[ -n $capture_pid ]] && kill -0 "$capture_pid" 2>/dev/null; then
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  [[ -z $capture_pid ]] || wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  if [[ -n $capture_stage_pcap && -s $capture_stage_pcap ]]; then
    mv -f -- "$capture_stage_pcap" "$capture_pcap"
  fi
  capture_stage_pcap=
}

cleanup() {
  local status=$?
  stop_capture
  stop_pid "$firefox_pid"
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ -n $capture_stage_dir ]]; then
    rm -rf -- "$capture_stage_dir"
  fi
  if [[ $status -eq 0 && $success -eq 1 && -n $capture_dir ]]; then
    case "$capture_dir" in
      "$STATE_ROOT"/captures/*) rm -rf -- "$capture_dir" ;;
      *) printf 'refusing to remove unexpected capture path: %s\n' \
           "$capture_dir" >&2 ;;
    esac
  elif [[ -n $capture_dir ]]; then
    printf 'capture comparison failed; private diagnostics preserved at %s\n' \
      "$capture_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in dumpcap tshark curl getcap openssl python3 readelf rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required capture tool not found: %s\n' "$tool" >&2
    exit 1
  }
done

dumpcap_path=$(command -v dumpcap)
dumpcap_caps=$(getcap "$dumpcap_path" 2>/dev/null || true)
if [[ $EUID -ne 0 ]] &&
   [[ $dumpcap_caps != *cap_net_admin* ||
      $dumpcap_caps != *cap_net_raw* ]]; then
  printf '%s needs cap_net_raw and cap_net_admin for loopback capture\n' \
    "$dumpcap_path" >&2
  printf 'run once as root: setcap cap_net_raw,cap_net_admin=eip %s\n' \
    "$dumpcap_path" >&2
  exit 1
fi

"$INTEGRATION_DIR/start.sh"
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

BIN="$OBJDIR/dist/bin"
capture_mode=${NAIVEFOX_CAPTURE_MODE:-quick}
case "$capture_mode" in
  quick)
    if [[ -n ${NAIVEFOX_CAPTURE_REFERENCE_BIN:-} ||
          -n ${NAIVEFOX_CAPTURE_REFERENCE_OBJDIR:-} ]]; then
      printf 'reference overrides require NAIVEFOX_CAPTURE_MODE=same-base\n' >&2
      exit 2
    fi
    REFERENCE_ROOT=$("$INTEGRATION_DIR/../../tools/fetch-firefox-reference.sh")
    REFERENCE_BIN="$REFERENCE_ROOT/firefox"
    REFERENCE_LIBDIR="$REFERENCE_ROOT"
    REFERENCE_OBJDIR=""
    ;;
  same-base)
    if [[ -z ${NAIVEFOX_CAPTURE_REFERENCE_BIN:-} ||
          -z ${NAIVEFOX_CAPTURE_REFERENCE_OBJDIR:-} ]]; then
      printf 'same-base mode requires NAIVEFOX_CAPTURE_REFERENCE_BIN and _OBJDIR\n' >&2
      exit 2
    fi
    REFERENCE_BIN="$NAIVEFOX_CAPTURE_REFERENCE_BIN"
    REFERENCE_LIBDIR="${NAIVEFOX_CAPTURE_REFERENCE_LIBDIR:-$(dirname "$REFERENCE_BIN")}"
    REFERENCE_OBJDIR="$NAIVEFOX_CAPTURE_REFERENCE_OBJDIR"
    ;;
  *)
    printf 'unknown NAIVEFOX_CAPTURE_MODE: %s (use quick or same-base)\n' \
      "$capture_mode" >&2
    exit 2
    ;;
esac
NAIVEFOX_BIN="${NAIVEFOX_CAPTURE_NAIVEFOX_BIN:-$BIN/naivefox}"
NAIVEFOX_LIBDIR="${NAIVEFOX_CAPTURE_NAIVEFOX_LIBDIR:-$BIN}"
for required in "$REFERENCE_BIN" "$REFERENCE_LIBDIR/libssl3.so" \
  "$REFERENCE_LIBDIR/libxul.so" "$NAIVEFOX_BIN" \
  "$NAIVEFOX_LIBDIR/libssl3.so" "$NAIVEFOX_LIBDIR/libxul.so"; do
  [[ -f $required ]] || {
    printf 'required capture artifact is missing: %s\n' "$required" >&2
    exit 1
  }
done
if [[ -n "$REFERENCE_OBJDIR" ]]; then
  if ! rg -q -- '-DNSS_ALLOW_SSLKEYLOGFILE' \
    "$REFERENCE_OBJDIR/security/nss/lib/ssl/ssl_ssl/backend.mk"; then
    printf 'this NSS build does not enable SSLKEYLOGFILE\n' >&2
    exit 1
  fi
fi

capture_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
capture_dir="$STATE_ROOT/captures/$capture_id"
mkdir -m 0700 -p "$capture_dir"
# WSL's dumpcap/AppArmor combination may deny opening a capture directly below
# /home even when the caller is root.  Capture into a private /tmp staging
# directory, then move the completed file into the private diagnostics tree.
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-dumpcap.XXXXXX")
chmod 0700 "$capture_stage_dir"
firefox_runtime_env=()
if [[ $EUID -eq 0 ]]; then
  firefox_runtime_dir="$capture_stage_dir/firefox-runtime"
  mkdir -m 0700 "$firefox_runtime_dir"
  firefox_runtime_env=("XDG_RUNTIME_DIR=$firefox_runtime_dir")
fi
chmod 0700 "$capture_dir"

export MOZ_CRASHREPORTER_DISABLE=1

start_capture() {
  local pcap=$1
  local log=$2
  capture_pcap=$pcap
  capture_stage_pcap="$capture_stage_dir/$(basename "$pcap")"
  : >"$log"
  chmod 0600 "$log"
  dumpcap -q -i any -f "tcp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:40 -a filesize:10240 -w "$capture_stage_pcap" >"$log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    if ! kill -0 "$capture_pid" 2>/dev/null; then
      cat "$log" >&2
      printf 'dumpcap exited before capture readiness\n' >&2
      return 1
    fi
    [[ -s $capture_stage_pcap ]] && return 0
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

reference_keys="$capture_dir/reference.keys"
reference_pcap="$capture_dir/reference.pcapng"
reference_log="$capture_dir/reference-firefox.log"
: >"$reference_keys"
: >"$reference_log"
chmod 0600 "$reference_keys" "$reference_log"
start_capture "$reference_pcap" "$capture_dir/reference-dumpcap.log"

  timeout 25 env SSLKEYLOGFILE="$reference_keys" \
  "${firefox_runtime_env[@]}" \
  LD_LIBRARY_PATH="$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
  "$REFERENCE_BIN" --headless --new-instance --no-remote \
  --profile "$reference_profile" \
  --screenshot "$capture_dir/reference.png" \
  "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/?capture=reference" \
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

naivefox_keys="$capture_dir/naivefox.keys"
naivefox_pcap="$capture_dir/naivefox.pcapng"
naivefox_log="$capture_dir/naivefox.log"
: >"$naivefox_keys"
: >"$naivefox_log"
chmod 0600 "$naivefox_keys" "$naivefox_log"
socks_port=$(python3 -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
start_capture "$naivefox_pcap" "$capture_dir/naivefox-dumpcap.log"

env SSLKEYLOGFILE="$naivefox_keys" \
  LD_LIBRARY_PATH="$NAIVEFOX_LIBDIR" \
  NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
  NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
  "$NAIVEFOX_BIN" \
  --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --socks-listen "127.0.0.1:$socks_port" \
  --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --max-connections 2 >"$naivefox_log" 2>&1 &
naivefox_pid=$!
wait_for_log "$naivefox_pid" "$naivefox_log" '^SOCKS5 listening on '

for request in 1 2; do
  timeout 30 curl --fail --silent --show-error --noproxy '' \
    --socks5-hostname "127.0.0.1:$socks_port" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small?capture=$request" \
    >/dev/null
done
if ! timeout 30 tail --pid="$naivefox_pid" -f /dev/null; then
  printf 'NaiveFox did not stop after two capture requests\n' >&2
  exit 1
fi
wait "$naivefox_pid"
naivefox_pid=
stop_capture

for artifact in "$reference_keys" "$reference_pcap" \
                "$naivefox_keys" "$naivefox_pcap"; do
  [[ -s $artifact ]] || {
    printf 'capture artifact is empty: %s\n' "$artifact" >&2
    exit 1
  }
done
for keys in "$reference_keys" "$naivefox_keys"; do
  if ! rg -q '^(CLIENT|SERVER)_(HANDSHAKE_)?TRAFFIC_SECRET' "$keys"; then
    printf 'NSS TLS 1.3 key log is missing traffic secrets: %s\n' "$keys" >&2
    exit 1
  fi
done

TSHARK_FIELDS=(-T fields -E header=y -E separator=, -E quote=d \
  -E occurrence=a '-E' 'aggregator=;')

extract_client_hello() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
    "${TSHARK_FIELDS[@]}" \
    -e frame.number -e tcp.stream -e tls.handshake.length \
    -e tls.record.version -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite -e tls.handshake.extension.type \
    -e tls.handshake.extensions_supported_group \
    -e tls.handshake.sig_hash_alg \
    -e tls.handshake.extensions_key_share_group \
    -e tls.handshake.extensions_alpn_str \
    -e tls.handshake.extensions_server_name \
    >"$output"
}

extract_server_hello() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==2" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e tcp.stream \
    -e tls.handshake.version -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite \
    -e tls.handshake.extensions_key_share_selected_group >"$output"
}

extract_tls_records() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.record.length" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream \
    -e tls.record.content_type -e tls.record.length >"$output"
}

extract_tcp_syn() {
  local pcap=$1
  local output=$2
  tshark -r "$pcap" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.syn==1 && tcp.flags.ack==0" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e tcp.stream \
    -e tcp.window_size_value -e tcp.options -e tcp.options.mss_val \
    -e tcp.options.wscale.shift -e tcp.options.timestamp.tsval \
    -e tcp.options.tfo.request -e tcp.options.tfo.cookie \
    -e tcp.flags.ece -e tcp.flags.cwr >"$output"
}

extract_alpn() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.extensions_alpn_str" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e tcp.stream \
    -e tls.handshake.extensions_alpn_str >"$output"
}

extract_settings() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.type==4 && http2.flags.ack.settings==0" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e tcp.stream \
    -e http2.settings.id -e http2.settings.header_table_size \
    -e http2.settings.enable_push -e http2.settings.max_concurrent_streams \
    -e http2.settings.initial_window_size -e http2.settings.max_frame_size \
    -e http2.settings.max_header_list_size \
    -e http2.settings.extended_connect \
    -e http2.settings.no_rfc7540_priorities >"$output"
}

extract_timeline() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "http2 && (tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT || tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT) && (http2.type==1 || http2.type==4 || http2.type==8)" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.flags -e http2.length -e http2.streamid \
    -e http2.window_update.window_size_increment >"$output"
}

extract_lifecycle() {
  local pcap=$1
  local keys=$2
  local output=$3
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "http2 && (tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT || tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT) && (http2.type==0 || http2.type==1 || http2.type==3 || http2.type==4 || http2.type==7 || http2.type==8)" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.flags -e http2.flags.end_stream -e http2.length \
    -e http2.streamid -e http2.rst_stream.error \
    -e http2.goaway.last_stream_id -e http2.goaway.error \
    -e http2.window_update.window_size_increment >"$output"
}

extract_requests() {
  local pcap=$1
  local keys=$2
  local method=$3
  local output=$4
  tshark -r "$pcap" -o "tls.keylog_file:$keys" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.headers.method==\"$method\"" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.stream -e http2.streamid -e http2.headers.method >"$output"
}

extract_names() {
  local pcap=$1
  local keys=$2
  local filter=$3
  local output=$4
  shift 4
  local raw="$output.raw"
  tshark -r "$pcap" -o "tls.keylog_file:$keys" -Y "$filter" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e tcp.stream \
    -e http2.streamid -e http2.header.name >"$raw"
  python3 - "$raw" "$output" "$@" <<'PY'
import csv
import sys

source, destination, *wanted = sys.argv[1:]
wanted = {name.lower() for name in wanted}
with open(source, newline="", encoding="utf-8") as source_file:
    rows = csv.DictReader(source_file)
    with open(destination, "w", newline="", encoding="utf-8") as output_file:
        fields = ["frame.number", "tcp.stream", "http2.streamid", "http2.header.name"]
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            for name in row["http2.header.name"].split(";"):
                if name.lower() in wanted:
                    writer.writerow({**row, "http2.header.name": name.lower()})
PY
  rm -- "$raw"
}

for side in reference naivefox; do
  if [[ $side == reference ]]; then
    pcap=$reference_pcap
    keys=$reference_keys
    method=GET
  else
    pcap=$naivefox_pcap
    keys=$naivefox_keys
    method=CONNECT
  fi
  extract_client_hello "$pcap" "$keys" "$capture_dir/$side-clienthello.csv"
  extract_server_hello "$pcap" "$keys" "$capture_dir/$side-serverhello.csv"
  extract_tls_records "$pcap" "$keys" "$capture_dir/$side-tls-records.csv"
  extract_tcp_syn "$pcap" "$capture_dir/$side-tcp-syn.csv"
  extract_alpn "$pcap" "$keys" "$capture_dir/$side-alpn.csv"
  extract_settings "$pcap" "$keys" "$capture_dir/$side-settings.csv"
  extract_timeline "$pcap" "$keys" "$capture_dir/$side-timeline.csv"
  extract_lifecycle "$pcap" "$keys" "$capture_dir/$side-lifecycle.csv"
  extract_requests "$pcap" "$keys" "$method" "$capture_dir/$side-requests.csv"
done

extract_names "$naivefox_pcap" "$naivefox_keys" \
  'http2.header.name=="alpn" || http2.header.name=="upgrade" || http2.header.name=="connection"' \
  "$capture_dir/naivefox-markers.csv" alpn upgrade connection
extract_names "$naivefox_pcap" "$naivefox_keys" \
  'http2.header.name=="padding"' "$capture_dir/naivefox-padding.csv" padding

for required in reference-clienthello reference-serverhello \
                reference-tls-records reference-tcp-syn reference-alpn \
                reference-settings reference-timeline reference-lifecycle \
                reference-requests naivefox-clienthello naivefox-serverhello \
                naivefox-tls-records naivefox-tcp-syn naivefox-alpn \
                naivefox-settings naivefox-timeline naivefox-lifecycle \
                naivefox-requests; do
  if [[ $(wc -l <"$capture_dir/$required.csv") -lt 2 ]]; then
    printf 'safe capture extract has no data rows: %s\n' "$required" >&2
    exit 1
  fi
done
for alpn_file in reference-alpn.csv naivefox-alpn.csv; do
  if ! rg -q 'h2' "$capture_dir/$alpn_file"; then
    printf 'capture does not show selected h2 ALPN: %s\n' "$alpn_file" >&2
    exit 1
  fi
done
if [[ $(wc -l <"$capture_dir/naivefox-markers.csv") -ne 1 ]]; then
  printf 'unexpected synthetic proxy CONNECT marker was captured\n' >&2
  exit 1
fi
if [[ $(wc -l <"$capture_dir/naivefox-padding.csv") -lt 3 ]]; then
  printf 'Naive padding request/response header names were not both captured\n' >&2
  exit 1
fi

read -r clienthello_semantic_equal clienthello_order_equal \
  serverhello_equal alpn_equal settings_equal tcp_syn_equal < <(
  python3 - "$capture_dir" <<'PY'
import csv
import os
import sys

root = sys.argv[1]

def normalized(name, ignored):
    with open(os.path.join(root, name), newline="", encoding="utf-8") as source:
        return [tuple(value for key, value in row.items() if key not in ignored)
                for row in csv.DictReader(source)]

def grease(value):
    try:
        parsed = int(value, 0)
    except ValueError:
        return value
    return "GREASE" if parsed & 0x0f0f == 0x0a0a else value

def hello(name, ordered):
    result = []
    for row in csv.DictReader(open(os.path.join(root, name), newline="", encoding="utf-8")):
        values = []
        for key, value in row.items():
            if key in {"frame.number", "tcp.stream"}:
                continue
            split = [grease(item) for item in value.split(";") if item]
            if not ordered and key in {
                "tls.handshake.extension.type",
                "tls.handshake.ciphersuite",
                "tls.handshake.extensions_supported_group",
                "tls.handshake.sig_hash_alg",
                "tls.handshake.extensions_key_share_group",
            }:
                split.sort()
            values.append((key, ";".join(split)))
        result.append(tuple(values))
    return result

clienthello_semantic_equal = hello("reference-clienthello.csv", False) == hello(
    "naivefox-clienthello.csv", False
)
clienthello_order_equal = hello("reference-clienthello.csv", True) == hello(
    "naivefox-clienthello.csv", True
)
serverhello_equal = normalized(
    "reference-serverhello.csv", {"frame.number", "tcp.stream"}
) == normalized("naivefox-serverhello.csv", {"frame.number", "tcp.stream"})
alpn_equal = normalized(
    "reference-alpn.csv", {"frame.number", "tcp.stream"}
) == normalized("naivefox-alpn.csv", {"frame.number", "tcp.stream"})
settings_ignored = {"frame.number", "tcp.stream"}
settings_equal = normalized("reference-settings.csv", settings_ignored) == normalized(
    "naivefox-settings.csv", settings_ignored)

def option_kinds(value):
    raw = "".join(character for character in value if character in "0123456789abcdefABCDEF")
    if not raw or len(raw) % 2:
        return ""
    data = bytes.fromhex(raw)
    result = []
    offset = 0
    while offset < len(data):
        kind = data[offset]
        result.append(str(kind))
        if kind == 0:
            break
        if kind == 1:
            offset += 1
            continue
        if offset + 1 >= len(data) or data[offset + 1] < 2:
            break
        offset += data[offset + 1]
    return ",".join(result)

def tcp_syn(name):
    rows = []
    for row in csv.DictReader(open(os.path.join(root, name), newline="", encoding="utf-8")):
        rows.append(tuple(
            (key, option_kinds(value) if key == "tcp.options" else
             "present" if key in {
                 "tcp.options.timestamp.tsval", "tcp.options.tfo.cookie"
             } and value else value)
            for key, value in row.items()
            if key not in {"frame.number", "tcp.stream"}
        ))
    return rows

tcp_syn_equal = tcp_syn("reference-tcp-syn.csv") == tcp_syn("naivefox-tcp-syn.csv")
print(
    "yes" if clienthello_semantic_equal else "no",
    "yes" if clienthello_order_equal else "no",
    "yes" if serverhello_equal else "no",
    "yes" if alpn_equal else "no",
    "yes" if settings_equal else "no",
    "yes" if tcp_syn_equal else "no",
)
PY
)

if [[ $capture_mode == same-base ]]; then
  for comparison in \
    "semantic ClientHello:$clienthello_semantic_equal" \
    "server negotiation:$serverhello_equal" \
    "selected ALPN:$alpn_equal" \
    "HTTP/2 SETTINGS:$settings_equal" \
    "TCP SYN fingerprint:$tcp_syn_equal"; do
    if [[ ${comparison##*:} != yes ]]; then
      printf 'same-base H2 parity mismatch: %s\n' "${comparison%%:*}" >&2
      exit 1
    fi
  done
fi

reuse_summary=$(python3 - "$capture_dir/naivefox-requests.csv" <<'PY'
import csv
import sys

rows = list(csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")))
tcp = {row["tcp.stream"] for row in rows if row["tcp.stream"]}
streams = {row["http2.streamid"] for row in rows if row["http2.streamid"]}
if len(rows) < 2 or len(tcp) != 1 or len(streams) < 2:
    raise SystemExit("NaiveFox did not reuse one outer TCP/H2 connection")
print(f"requests={len(rows)} tcp_streams={len(tcp)} h2_streams={len(streams)}")
PY
)

safe_dir="$STATE_ROOT/capture-safe/$capture_id"
mkdir -m 0700 -p "$safe_dir"
safe_files=(
  reference-clienthello.csv reference-serverhello.csv reference-tls-records.csv
  reference-tcp-syn.csv reference-alpn.csv reference-settings.csv
  reference-timeline.csv reference-lifecycle.csv reference-requests.csv
  naivefox-clienthello.csv naivefox-serverhello.csv naivefox-tls-records.csv
  naivefox-tcp-syn.csv naivefox-alpn.csv naivefox-settings.csv
  naivefox-timeline.csv naivefox-lifecycle.csv naivefox-requests.csv
  naivefox-markers.csv naivefox-padding.csv
)
for file in "${safe_files[@]}"; do
  python3 - "$capture_dir/$file" "$safe_dir/$file" \
    "$NAIVEFOX_FIXTURE_PROXY_PORT" <<'PY'
import csv
import sys

source, destination, proxy_port = sys.argv[1:]
with open(source, newline="", encoding="utf-8") as stream:
    reader = csv.DictReader(stream)
    fieldnames = list(reader.fieldnames or ())
    rows = list(reader)
port_fields = {"tcp.srcport", "tcp.dstport"}
if port_fields <= set(fieldnames):
    fieldnames = [name for name in fieldnames if name not in port_fields]
    fieldnames.append("direction")
    for row in rows:
        if row["tcp.dstport"] == proxy_port:
            row["direction"] = "client"
        elif row["tcp.srcport"] == proxy_port:
            row["direction"] = "server"
        else:
            row["direction"] = "other"
        for name in port_fields:
            row.pop(name, None)
with open(destination, "w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
PY
  chmod 0600 "$safe_dir/$file"
done
cat >"$safe_dir/summary.txt" <<EOF
capture_revision=$(git -C "$SOURCE_ROOT" rev-parse HEAD)
reference_mode=$capture_mode
reference_binary=$(readelf -n "$REFERENCE_BIN" | sed -n 's/^ *Build ID: //p' | head -n 1)
naivefox_binary=$(readelf -n "$NAIVEFOX_BIN" | sed -n 's/^ *Build ID: //p' | head -n 1)
reference_libxul_sha256=$(sha256sum "$REFERENCE_LIBDIR/libxul.so" | cut -d' ' -f1)
reference_libssl3_sha256=$(sha256sum "$REFERENCE_LIBDIR/libssl3.so" | cut -d' ' -f1)
naivefox_libxul_sha256=$(sha256sum "$NAIVEFOX_LIBDIR/libxul.so" | cut -d' ' -f1)
naivefox_libssl3_sha256=$(sha256sum "$NAIVEFOX_LIBDIR/libssl3.so" | cut -d' ' -f1)
endpoint=localhost:$NAIVEFOX_FIXTURE_PROXY_PORT
reference_method=GET
naivefox_method=CONNECT
selected_alpn=h2
semantic_clienthello_fields_equal=$clienthello_semantic_equal
ordered_clienthello_fields_equal=$clienthello_order_equal
server_negotiation_equal=$serverhello_equal
selected_alpn_equal=$alpn_equal
client_settings_equal=$settings_equal
tcp_syn_fingerprint_equal=$tcp_syn_equal
tls_record_layout_recorded=yes
h2_lifecycle_frames_recorded=DATA,HEADERS,RST_STREAM,SETTINGS,GOAWAY,WINDOW_UPDATE
synthetic_marker_names=none
padding_header_name=present
naivefox_reuse=$reuse_summary
raw_capture_material=deleted_after_success
EOF
chmod 0600 "$safe_dir/summary.txt"

if rg -F "$NAIVEFOX_FIXTURE_USER" "$safe_dir" ||
   rg -F "$NAIVEFOX_FIXTURE_PASS" "$safe_dir" ||
   rg -i 'proxy-authorization' "$safe_dir"; then
  printf 'credential-bearing data reached the safe capture output\n' >&2
  exit 1
fi

success=1
printf 'Firefox/NaiveFox capture comparison passed\n'
printf 'sanitized aggregates: %s\n' "$safe_dir"
