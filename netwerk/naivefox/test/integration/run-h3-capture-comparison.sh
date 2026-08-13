#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

capture_pid=
capture_pcap=
capture_raw=
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
  if [[ -n $capture_raw && -s $capture_raw ]]; then
    # The WSL `any` interface records loopback transmit and receive copies.
    # Retain the transmit copy before QUIC dissection so duplicate packet
    # numbers cannot perturb Wireshark's key-phase state machine.  The transmit
    # copy also preserves the sender's handshake/application packet order.
    tshark -r "$capture_raw" -Y 'sll.pkttype==4' -w "$capture_pcap"
    rm -f -- "$capture_raw"
  fi
  capture_pcap=
  capture_raw=
}

cleanup() {
  local status=$?
  stop_capture
  stop_pid "$firefox_pid"
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ -n $capture_dir ]]; then
    case $capture_dir in
      "$STATE_ROOT"/h3-captures/*)
        if [[ $status -eq 0 && $success -eq 1 ]]; then
          rm -rf -- "$capture_dir"
        else
          printf 'H3 capture comparison failed; private diagnostics preserved at %s\n' \
            "$capture_dir" >&2
        fi
        ;;
      *)
        printf 'refusing to remove unexpected capture path: %s\n' \
          "$capture_dir" >&2
        ;;
    esac
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in dumpcap tshark curl getcap openssl python3 readelf rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required H3 capture tool not found: %s\n' "$tool" >&2
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

"$INTEGRATION_DIR/start.sh" --mode h3
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
[[ $NAIVEFOX_FIXTURE_MODE == h3 ]]

BIN="$OBJDIR/dist/bin"
for binary in firefox naivefox libssl3.so libxul.so; do
  [[ -f $BIN/$binary ]] || {
    printf 'required Firefox build artifact is missing: %s\n' "$BIN/$binary" >&2
    exit 1
  }
done
if ! rg -q -- '-DNSS_ALLOW_SSLKEYLOGFILE' \
  "$OBJDIR/security/nss/lib/ssl/ssl_ssl/backend.mk"; then
  printf 'this NSS build does not enable SSLKEYLOGFILE\n' >&2
  exit 1
fi

capture_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
capture_dir="$STATE_ROOT/h3-captures/$capture_id"
mkdir -m 0700 -p "$capture_dir"

export LD_LIBRARY_PATH="$BIN${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1

start_capture() {
  local pcap=$1
  local log=$2
  : >"$log"
  chmod 0600 "$log"
  capture_pcap=$pcap
  capture_raw="${pcap%.pcapng}.raw.pcapng"
  # `any` is the only reliable WSL loopback source here.  stop_capture filters
  # its duplicate cooked receive/transmit views before stateful QUIC decode.
  dumpcap -q -i any \
    -f "udp port $NAIVEFOX_FIXTURE_PROXY_PORT or tcp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:60 -a filesize:65536 -w "$capture_raw" >"$log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      cat "$log" >&2
      printf 'dumpcap exited before capture readiness\n' >&2
      return 1
    }
    [[ -s $capture_raw ]] && return 0
    sleep 0.1
  done
  printf 'timed out waiting for dumpcap capture file\n' >&2
  return 1
}

wait_for_log() {
  local pid=$1
  local log=$2
  local pattern=$3
  for ((i = 0; i < 150; i++)); do
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
cat >"$reference_profile/user.js" <<EOF
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
user_pref("network.http.http3.enable", true);
user_pref("network.http.http3.disable_when_third_party_roots_found", false);
user_pref("network.http.http3.alt-svc-mapping-for-testing", "localhost;h3=:$NAIVEFOX_FIXTURE_PROXY_PORT");
user_pref("network.http.http3.force-use-alt-svc-mapping-for-testing", true);
EOF
chmod 0600 "$reference_profile/user.js"

run_reference() {
  local pass=$1
  local pcap="$capture_dir/$pass-reference.pcapng"
  local log="$capture_dir/$pass-reference-firefox.log"
  local screenshot="$capture_dir/$pass-reference.png"
  local keylog="$capture_dir/$pass-reference.keys"
  local -a command_env=(env -u SSLKEYLOGFILE MOZ_HEADLESS=1)
  if [[ $pass == decrypted ]]; then
    : >"$keylog"
    chmod 0600 "$keylog"
    command_env=(env "SSLKEYLOGFILE=$keylog" MOZ_HEADLESS=1)
  fi
  : >"$log"
  chmod 0600 "$log"
  start_capture "$pcap" "$capture_dir/$pass-reference-dumpcap.log"
  timeout 35 "${command_env[@]}" \
    "$BIN/firefox" --headless --new-instance --no-remote \
    --profile "$reference_profile" --screenshot "$screenshot" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/observer?size=2097152&pass=$pass" \
    >"$log" 2>&1 &
  firefox_pid=$!
  set +e
  wait "$firefox_pid"
  local status=$?
  set -e
  firefox_pid=
  stop_capture
  if [[ $status -ne 0 && $status -ne 124 ]]; then
    printf 'reference Firefox %s pass exited with status %s; evaluating capture\n' \
      "$pass" "$status" >&2
  fi
}

run_naivefox() {
  local pass=$1
  local pcap="$capture_dir/$pass-naivefox.pcapng"
  local log="$capture_dir/$pass-naivefox.log"
  local keylog="$capture_dir/$pass-naivefox.keys"
  local -a command_env=(env -u SSLKEYLOGFILE)
  if [[ $pass == decrypted ]]; then
    : >"$keylog"
    chmod 0600 "$keylog"
    command_env=(env "SSLKEYLOGFILE=$keylog")
  fi
  : >"$log"
  chmod 0600 "$log"
  local socks_port
  socks_port=$(python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  start_capture "$pcap" "$capture_dir/$pass-naivefox-dumpcap.log"
  "${command_env[@]}" \
    NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
    "$BIN/naivefox" \
    --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
    --protocol h3 \
    --socks-listen "127.0.0.1:$socks_port" \
    --proxy "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --max-connections 2 >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  for request in 1 2; do
    timeout 35 curl --fail --silent --show-error --noproxy '' \
      --socks5-hostname "127.0.0.1:$socks_port" \
      "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/observer?size=1048576&pass=$pass&request=$request" \
      >"$capture_dir/$pass-response-$request.bin"
  done
  if ! timeout 35 tail --pid="$naivefox_pid" -f /dev/null; then
    printf 'NaiveFox did not stop after two %s capture requests\n' "$pass" >&2
    exit 1
  fi
  wait "$naivefox_pid"
  naivefox_pid=
  stop_capture
  [[ $(rg -c '^Outer protocol: h3$' "$log") -eq 2 ]]
  [[ $(rg -c '^Padding negotiated: yes$' "$log") -eq 2 ]]
  ! rg -q -e '^Outer protocol: h2$' -e '^Padding negotiated: no$' "$log"
}

run_reference decrypted
run_naivefox decrypted
run_reference passive
run_naivefox passive

for pass in decrypted passive; do
  for side in reference naivefox; do
    pcap="$capture_dir/$pass-$side.pcapng"
    [[ -s $pcap ]] || {
      printf 'H3 capture is empty: %s\n' "$pcap" >&2
      exit 1
    }
    udp_count=$(tshark -r "$pcap" \
      -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && quic" -T fields \
      -e frame.number | wc -l)
    tcp_established=$(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.syn==1 && tcp.flags.ack==1" \
      -T fields -e frame.number | wc -l)
    tcp_payload=$(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.len>0" -T fields \
      -e frame.number | wc -l)
    if [[ $udp_count -eq 0 || $tcp_established -ne 0 || $tcp_payload -ne 0 ]]; then
      printf '%s/%s is not strict QUIC (udp=%s tcp-established=%s tcp-payload=%s)\n' \
        "$pass" "$side" "$udp_count" "$tcp_established" "$tcp_payload" >&2
      exit 1
    fi
  done
done

for keys in "$capture_dir/decrypted-reference.keys" \
            "$capture_dir/decrypted-naivefox.keys"; do
  [[ -s $keys ]]
  rg -q '^(CLIENT|SERVER)_(HANDSHAKE_)?TRAFFIC_SECRET' "$keys"
done
if find "$capture_dir" -maxdepth 1 -name 'passive-*.keys' -print -quit |
   grep -q .; then
  printf 'passive pass unexpectedly created a key log\n' >&2
  exit 1
fi

TSHARK_FIELDS=(-T fields -E header=y -E separator=, -E quote=d \
  -E occurrence=a '-E' 'aggregator=;')

extract_decrypted() {
  local side=$1
  local pcap="$capture_dir/decrypted-$side.pcapng"
  local keys="$capture_dir/decrypted-$side.keys"
  local prefix="$capture_dir/decrypted-$side"
  local decode=(-d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
    -o "tls.keylog_file:$keys")

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e tls.handshake.length -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite -e tls.handshake.extension.type \
    -e tls.handshake.extensions_supported_group \
    -e tls.handshake.sig_hash_alg \
    -e tls.handshake.extensions_key_share_group \
    -e tls.handshake.extensions_alpn_str \
    -e tls.handshake.extensions_server_name >"$prefix-clienthello.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.quic.parameter.type" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e tls.quic.parameter.type -e tls.quic.parameter.max_idle_timeout \
    -e tls.quic.parameter.max_udp_payload_size \
    -e tls.quic.parameter.initial_max_data \
    -e tls.quic.parameter.initial_max_stream_data_bidi_local \
    -e tls.quic.parameter.initial_max_stream_data_bidi_remote \
    -e tls.quic.parameter.initial_max_stream_data_uni \
    -e tls.quic.parameter.initial_max_streams_bidi \
    -e tls.quic.parameter.initial_max_streams_uni \
    -e tls.quic.parameter.ack_delay_exponent \
    -e tls.quic.parameter.max_ack_delay \
    -e tls.quic.parameter.active_connection_id_limit \
    -e tls.quic.parameter.max_datagram_frame_size \
    -e tls.quic.parameter.min_ack_delay >"$prefix-transport.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http3.settings" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e quic.stream.stream_id -e http3.settings.id \
    -e http3.settings.qpack.max_table_capacity \
    -e http3.settings.qpack.blocked_streams \
    -e http3.settings.max_field_section_size \
    -e http3.settings.extended_connect -e http3.settings.h3_datagram \
    -e http3.settings.webtransport >"$prefix-settings.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "http3.headers.method || http3.headers.status" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e udp.srcport -e udp.dstport \
    -e quic.connection.number -e quic.stream.stream_id \
    -e http3.headers.method -e http3.headers.status >"$prefix-requests.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "http3.header.header.name" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e udp.srcport -e udp.dstport \
    -e quic.connection.number -e quic.stream.stream_id \
    -e http3.header.header.name >"$prefix-header-names.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && quic" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e udp.srcport -e udp.dstport -e udp.length \
    -e quic.connection.number -e quic.version \
    -e quic.long.packet_type -e quic.dcil -e quic.scil \
    -e quic.packet_number -e quic.packet_length >"$prefix-packets.csv"
}

extract_passive() {
  local side=$1
  local pcap="$capture_dir/passive-$side.pcapng"
  local prefix="$capture_dir/passive-$side"
  local decode=(-d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic")

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e tls.handshake.length -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite -e tls.handshake.extension.type \
    -e tls.handshake.extensions_supported_group \
    -e tls.handshake.sig_hash_alg \
    -e tls.handshake.extensions_key_share_group \
    -e tls.handshake.extensions_alpn_str \
    -e tls.handshake.extensions_server_name >"$prefix-clienthello.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.quic.parameter.type" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e tls.quic.parameter.type -e tls.quic.parameter.max_idle_timeout \
    -e tls.quic.parameter.max_udp_payload_size \
    -e tls.quic.parameter.initial_max_data \
    -e tls.quic.parameter.initial_max_stream_data_bidi_local \
    -e tls.quic.parameter.initial_max_stream_data_bidi_remote \
    -e tls.quic.parameter.initial_max_stream_data_uni \
    -e tls.quic.parameter.initial_max_streams_bidi \
    -e tls.quic.parameter.initial_max_streams_uni \
    -e tls.quic.parameter.ack_delay_exponent \
    -e tls.quic.parameter.max_ack_delay \
    -e tls.quic.parameter.active_connection_id_limit \
    -e tls.quic.parameter.max_datagram_frame_size \
    -e tls.quic.parameter.min_ack_delay >"$prefix-transport.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e udp.srcport -e udp.dstport -e udp.length \
    -e quic.connection.number -e quic.version \
    -e quic.long.packet_type -e quic.dcil -e quic.scil \
    -e quic.packet_number -e quic.packet_length -e quic.decryption_failed \
    >"$prefix-packets.csv"
}

for side in reference naivefox; do
  extract_decrypted "$side"
  extract_passive "$side"
done

safe_dir="$STATE_ROOT/h3-capture-safe/$capture_id"
mkdir -m 0700 -p "$safe_dir"

python3 - "$capture_dir" "$safe_dir/summary.txt" \
  "$NAIVEFOX_FIXTURE_PROXY_PORT" <<'PY'
import csv
import hashlib
import math
import os
import sys

root, destination, proxy_port = sys.argv[1:]


def rows(name):
    with open(os.path.join(root, name), newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def unique_rows(name, ignored=()):
    ignored = set(ignored)
    return sorted(
        {
            tuple((key, value) for key, value in row.items() if key not in ignored)
            for row in rows(name)
        }
    )


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def config_equal(suffix, ignored=("quic.connection.number",), unordered=()):
    unordered = set(unordered)
    def normalized(name):
        result = []
        for row in rows(name):
            values = []
            for key, value in row.items():
                if key in ignored:
                    continue
                if key in unordered:
                    value = ";".join(sorted(filter(None, value.split(";"))))
                values.append((key, value))
            result.append(tuple(values))
        return sorted(set(result))
    reference = unique_rows(f"decrypted-reference-{suffix}.csv", ignored)
    naivefox = unique_rows(f"decrypted-naivefox-{suffix}.csv", ignored)
    require(reference, f"reference {suffix} extract is empty")
    require(naivefox, f"NaiveFox {suffix} extract is empty")
    return normalized(f"decrypted-reference-{suffix}.csv") == normalized(
        f"decrypted-naivefox-{suffix}.csv"
    )


hello_equal = config_equal(
    "clienthello", unordered=("tls.handshake.extension.type",
                              "tls.handshake.sig_hash_alg")
)
transport_equal = config_equal("transport")
settings_equal = config_equal(
    "settings", ("quic.connection.number", "quic.stream.stream_id")
)
require(hello_equal, "semantic ClientHello configuration differs")
require(transport_equal, "client QUIC transport parameters differ")
require(settings_equal, "client HTTP/3/QPACK settings differ")


def selected_alpn(side):
    hello = rows(f"decrypted-{side}-clienthello.csv")
    values = {
        value
        for row in hello
        for value in row["tls.handshake.extensions_alpn_str"].split(";")
        if value
    }
    require(any(value.startswith("h3") for value in values),
            f"{side} ClientHello did not offer h3")
    # The server selected ALPN is encrypted, but successful HTTP/3 dissection
    # below is stronger than merely observing the offer.
    return sorted(values)


reference_alpn = selected_alpn("reference")
naivefox_alpn = selected_alpn("naivefox")


def request_summary(side, wanted):
    data = rows(f"decrypted-{side}-requests.csv")
    requests = [row for row in data if row["http3.headers.method"] == wanted]
    require(requests, f"{side} has no decrypted HTTP/3 {wanted} request")
    connections = {row["quic.connection.number"] for row in requests
                   if row["quic.connection.number"]}
    streams = {
        stream
        for row in requests
        for stream in row["quic.stream.stream_id"].split(";")
        if stream
    }
    return len(requests), connections, streams


reference_requests, reference_connections, reference_streams = request_summary(
    "reference", "GET"
)
connect_requests, connect_connections, connect_streams = request_summary(
    "naivefox", "CONNECT"
)
require(len(connect_connections) == 1,
        "NaiveFox CONNECT requests did not use one QUIC connection")
require(len(connect_streams) >= 2,
        "NaiveFox did not multiplex at least two CONNECT streams")


headers = rows("decrypted-naivefox-header-names.csv")
padding_client = set()
padding_server = set()
markers = set()
for row in headers:
    names = {name.lower() for name in row["http3.header.header.name"].split(";")}
    stream_ids = {value for value in row["quic.stream.stream_id"].split(";") if value}
    if "padding" in names:
        target = padding_client if row["udp.dstport"] == proxy_port else padding_server
        target.update(stream_ids)
    markers.update(names & {"alpn", "upgrade", "connection"})
require(len(padding_client) >= 2, "padding request header missing from CONNECT streams")
require(len(padding_server) >= 2, "padding response header missing from CONNECT streams")
require(not markers, "synthetic ALPN/Upgrade/Connection marker was captured")


def percentile(values, fraction):
    if not values:
        return 0
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return round(values[lower] * (upper - position) +
                 values[upper] * (position - lower))


def packet_summary(pass_name, side):
    data = rows(f"{pass_name}-{side}-packets.csv")
    require(data, f"{pass_name}/{side} has no QUIC packets")
    # stop_capture already retained only the SLL transmit copy.  Preserve every
    # remaining datagram here: without traffic secrets, short-header packet
    # numbers are intentionally unavailable and are not valid deduplication
    # keys.
    distinct = data
    versions = sorted({value for row in distinct
                       for value in row["quic.version"].split(";") if value})
    require(versions, f"{pass_name}/{side} has no visible QUIC version")
    connections = {value for row in distinct
                   for value in row["quic.connection.number"].split(";") if value}
    initial = [row for row in distinct
               if any(value == "0" for value in row["quic.long.packet_type"].split(";"))]
    vn = [row for row in distinct
          if any(value in {"0x00000000", "0"}
                 for value in row["quic.version"].split(";"))]
    client = [int(row["udp.length"] or 0) for row in distinct
              if row["udp.dstport"] == proxy_port]
    server = [int(row["udp.length"] or 0) for row in distinct
              if row["udp.srcport"] == proxy_port]
    dcid_lengths = sorted({value for row in initial
                           for value in row["quic.dcil"].split(";") if value})
    scid_lengths = sorted({value for row in initial
                           for value in row["quic.scil"].split(";") if value})
    event_order = []
    for row in sorted(distinct, key=lambda item: float(item["frame.time_relative"])):
        direction = "C" if row["udp.dstport"] == proxy_port else "S"
        packet_types = "+".join(filter(None, row["quic.long.packet_type"].split(";")))
        event = f"{direction}:{packet_types or 'short'}"
        if not event_order or event_order[-1] != event:
            event_order.append(event)
        if len(event_order) == 12:
            break
    return {
        "packets": len(distinct),
        "connections": len(connections),
        "versions": "+".join(versions),
        "initial_packets": len(initial),
        "version_negotiation_packets": len(vn),
        "initial_dcid_lengths": "+".join(dcid_lengths),
        "initial_scid_lengths": "+".join(scid_lengths),
        "handshake_order": ",".join(event_order),
        "client_bytes": sum(client),
        "server_bytes": sum(server),
        "client_length_p50": percentile(client, .50),
        "client_length_p95": percentile(client, .95),
        "server_length_p50": percentile(server, .50),
        "server_length_p95": percentile(server, .95),
    }


packet_summaries = {
    (pass_name, side): packet_summary(pass_name, side)
    for pass_name in ("decrypted", "passive")
    for side in ("reference", "naivefox")
}
require(packet_summaries[("passive", "naivefox")]["connections"] == 1,
        "passive NaiveFox capture has more than one QUIC connection")
require(packet_summaries[("decrypted", "reference")]["versions"] ==
        packet_summaries[("decrypted", "naivefox")]["versions"],
        "Firefox and NaiveFox QUIC versions differ")

def normalized_hello(name):
    result = []
    for row in rows(name):
        result.append(tuple(
            (key, ";".join(sorted(filter(None, value.split(";"))))
             if key in {"tls.handshake.extension.type",
                        "tls.handshake.sig_hash_alg"} else value)
            for key, value in row.items() if key != "quic.connection.number"
        ))
    return sorted(set(result))


passive_reference_hellos = normalized_hello("passive-reference-clienthello.csv")
passive_naivefox_hellos = normalized_hello("passive-naivefox-clienthello.csv")
passive_hello_equal = bool(passive_naivefox_hellos) and all(
    hello in passive_reference_hellos for hello in passive_naivefox_hellos
)
passive_reference_transport = unique_rows(
    "passive-reference-transport.csv", ("quic.connection.number",)
)
passive_naivefox_transport = unique_rows(
    "passive-naivefox-transport.csv", ("quic.connection.number",)
)
passive_transport_equal = bool(passive_naivefox_transport) and all(
    item in passive_reference_transport for item in passive_naivefox_transport
)
require(passive_hello_equal, "passively visible ClientHello configuration differs")
require(passive_transport_equal,
        "passively visible client QUIC transport parameters differ")

fingerprint_source = repr(
    unique_rows("decrypted-reference-clienthello.csv", ("quic.connection.number",))
).encode()
fingerprint = hashlib.sha256(fingerprint_source).hexdigest()

with open(destination, "w", encoding="utf-8") as output:
    output.write("capture_scope=local_strict_h3_firefox_vs_naivefox\n")
    output.write("capture_interface=any_sll_transmit_copy\n")
    output.write("strict_udp_quic_only=yes\n")
    output.write("tcp_sessions_established=0\n")
    output.write("tcp_payload_bytes=0\n")
    output.write("decrypted_selected_protocol=h3\n")
    output.write("decrypted_reference_method=GET\n")
    output.write("decrypted_naivefox_method=CONNECT\n")
    output.write(f"quic_versions={packet_summaries[('decrypted', 'reference')]['versions']}\n")
    output.write(f"tls_clienthello_semantic_config_equal={'yes' if hello_equal else 'no'}\n")
    output.write("tls_extension_order_expected_randomized=yes\n")
    output.write(f"client_transport_parameters_equal={'yes' if transport_equal else 'no'}\n")
    output.write(f"h3_settings_equal={'yes' if settings_equal else 'no'}\n")
    output.write("qpack_settings_compared=max_table_capacity,blocked_streams\n")
    output.write(f"clienthello_canonical_sha256={fingerprint}\n")
    output.write("synthetic_marker_names=none\n")
    output.write("padding_request_header_name=present\n")
    output.write("padding_response_header_name=present\n")
    output.write(f"naivefox_quic_connections={len(connect_connections)}\n")
    output.write(f"naivefox_connect_streams={len(connect_streams)}\n")
    output.write("passive_tls_keylog=disabled\n")
    output.write("passive_clienthello_visible_fields_equal=yes\n")
    output.write("passive_client_transport_parameters_equal=yes\n")
    output.write(
        "passive_reference_quic_connections="
        f"{packet_summaries[('passive', 'reference')]['connections']}\n"
    )
    output.write("passive_reference_retry_observed=" +
                 ("yes\n" if packet_summaries[("passive", "reference")]["connections"] > 1
                  else "no\n"))
    for pass_name in ("decrypted", "passive"):
        for side in ("reference", "naivefox"):
            for key, value in packet_summaries[(pass_name, side)].items():
                output.write(f"{pass_name}_{side}_{key}={value}\n")
    output.write("raw_capture_material=deleted_after_success\n")
PY
chmod 0600 "$safe_dir/summary.txt"

{
  printf 'capture_revision=%s\n' "$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
  printf 'tshark_version=%s\n' "$(tshark --version | head -n 1)"
  printf 'reference_binary=%s\n' \
    "$(readelf -n "$BIN/firefox" | sed -n 's/^ *Build ID: //p' | head -n 1)"
  printf 'naivefox_binary=%s\n' \
    "$(readelf -n "$BIN/naivefox" | sed -n 's/^ *Build ID: //p' | head -n 1)"
  printf 'libxul_sha256=%s\n' "$(sha256sum "$BIN/libxul.so" | cut -d' ' -f1)"
  printf 'libssl3_sha256=%s\n' "$(sha256sum "$BIN/libssl3.so" | cut -d' ' -f1)"
  for side in reference naivefox; do
    pcap="$capture_dir/passive-$side.pcapng"
    syn_count=$(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.syn==1 && tcp.flags.ack==0" \
      -T fields -e frame.number | wc -l)
    rst_count=$(tshark -r "$pcap" \
      -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.reset==1" \
      -T fields -e frame.number | wc -l)
    printf 'passive_%s_tcp_syn_probe_packets=%s\n' "$side" "$syn_count"
    printf 'passive_%s_tcp_rst_packets=%s\n' "$side" "$rst_count"
  done
} >>"$safe_dir/summary.txt"

if rg -F "$NAIVEFOX_FIXTURE_USER" "$safe_dir" ||
   rg -F "$NAIVEFOX_FIXTURE_PASS" "$safe_dir" ||
   rg -i -e proxy-authorization -e authorization: "$safe_dir"; then
  printf 'credential-bearing data reached safe H3 capture output\n' >&2
  exit 1
fi

success=1
printf 'Firefox/NaiveFox strict H3 capture comparison passed\n'
printf 'sanitized aggregates: %s\n' "$safe_dir"
