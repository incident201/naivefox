#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
original_args=("$@")

arm=root
while [[ $# -gt 0 ]]; do
  case $1 in
    --arm) arm=${2:-}; shift 2 ;;
    --help)
      printf 'usage: %s [--arm gate|root|document-complete|tree-complete|tree-early-overlap|tree-overlap]\n' "$0"
      exit 0
      ;;
    *) printf 'unknown H2 comparison argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
case $arm in
  gate | root | document-complete | tree-complete | tree-early-overlap | tree-overlap) ;;
  *) printf 'unsupported H2 diagnostic arm: %s\n' "$arm" >&2; exit 2 ;;
esac

if [[ ${NAIVEFOX_CAPTURE_MODE:-quick} != same-base ]]; then
  printf 'H2 decrypted parity requires NAIVEFOX_CAPTURE_MODE=same-base\n' >&2
  exit 2
fi
isolated_entered=${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0}
case $isolated_entered in 0 | 1) ;; *) printf 'invalid isolated-network marker\n' >&2; exit 2 ;; esac
if [[ $isolated_entered == 0 ]]; then
  for tool in unshare ip ethtool; do
    command -v "$tool" >/dev/null 2>&1 || {
      printf 'H2 comparison isolation requires %s\n' "$tool" >&2
      exit 1
    }
  done
  export NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1
  exec unshare --net --mount-proc \
    "$INTEGRATION_DIR/run-camouflage-isolated-network.sh" \
    "$0" "${original_args[@]}"
fi
[[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0} == 1 ]] || {
  printf 'isolated H2 comparison marker is inconsistent\n' >&2
  exit 2
}

for tool in dumpcap tshark getcap openssl python3 readelf rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required H2 capture tool not found: %s\n' "$tool" >&2
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

REFERENCE_BIN=${NAIVEFOX_CAPTURE_REFERENCE_BIN:-}
REFERENCE_OBJDIR=${NAIVEFOX_CAPTURE_REFERENCE_OBJDIR:-}
[[ -n $REFERENCE_BIN && -n $REFERENCE_OBJDIR ]] || {
  printf 'same-base H2 comparison requires reference binary and objdir\n' >&2
  exit 2
}
REFERENCE_LIBDIR=${NAIVEFOX_CAPTURE_REFERENCE_LIBDIR:-$(dirname "$REFERENCE_BIN")}
NAIVEFOX_BIN=${NAIVEFOX_CAPTURE_NAIVEFOX_BIN:-$OBJDIR/dist/bin/naivefox}
NAIVEFOX_LIBDIR=${NAIVEFOX_CAPTURE_NAIVEFOX_LIBDIR:-$OBJDIR/dist/bin}
for artifact in "$REFERENCE_BIN" "$REFERENCE_LIBDIR/libssl3.so" \
                "$REFERENCE_LIBDIR/libxul.so" "$NAIVEFOX_BIN" \
                "$NAIVEFOX_LIBDIR/libssl3.so" "$NAIVEFOX_LIBDIR/libxul.so"; do
  [[ -f $artifact ]] || {
    printf 'required H2 comparison artifact is missing: %s\n' "$artifact" >&2
    exit 1
  }
done
rg -q -- '-DNSS_ALLOW_SSLKEYLOGFILE' \
  "$REFERENCE_OBJDIR/security/nss/lib/ssl/ssl_ssl/backend.mk" || {
  printf 'same-base NSS build does not enable SSLKEYLOGFILE\n' >&2
  exit 1
}

"$INTEGRATION_DIR/start.sh" --mode h2
run_dir=$(<"$ACTIVE_RUN_FILE")
# shellcheck source=/dev/null
source "$run_dir/fixture.env"

capture_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
capture_dir="$STATE_ROOT/h2-captures/$capture_id"
safe_dir=
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-h2-dumpcap.XXXXXX")
mkdir -m 0700 -p "$capture_dir"
chmod 0700 "$capture_stage_dir"

capture_pid=
capture_pcap=
capture_stage_raw=
capture_log=
network_monitor_pid=
network_monitor_events=
network_monitor_ready=
network_monitor_done=
firefox_pid=
naivefox_pid=
success=0

source_worktree_dirty() {
  if git -C "$SOURCE_ROOT" diff --quiet --no-ext-diff HEAD -- &&
     [[ -z $(git -C "$SOURCE_ROOT" ls-files --others --exclude-standard) ]]; then
    printf 'no'
  else
    printf 'yes'
  fi
}

source_state_sha256() {
  {
    git -C "$SOURCE_ROOT" diff --binary --no-ext-diff HEAD --
    while IFS= read -r -d '' source_path; do
      printf 'untracked:%s\0' "$source_path"
      sha256sum "$SOURCE_ROOT/$source_path"
    done < <(git -C "$SOURCE_ROOT" ls-files --others --exclude-standard -z |
             sort -z)
  } | sha256sum | cut -d' ' -f1
}

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

start_capture() {
  capture_pcap=$1
  capture_log=$2
  capture_stage_raw="$capture_stage_dir/$(basename "${capture_pcap%.pcapng}.raw.pcapng")"
  : >"$capture_log"
  chmod 0600 "$capture_log"
  dumpcap -q -i any -f "tcp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:60 -a filesize:65536 -w "$capture_stage_raw" \
    >"$capture_log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      printf 'dumpcap exited before H2 capture readiness\n' >&2
      return 1
    }
    [[ -s $capture_stage_raw ]] && return 0
    sleep 0.1
  done
  printf 'timed out waiting for H2 capture readiness\n' >&2
  return 1
}

stop_capture() {
  [[ -n $capture_pid ]] || return 0
  local status=0
  local was_running=0
  if kill -0 "$capture_pid" 2>/dev/null; then
    was_running=1
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  if [[ $was_running -ne 1 ]]; then
    printf 'dumpcap stopped before the H2 workload completed\n' >&2
    status=1
  fi
  python3 "$INTEGRATION_DIR/camouflage_capture_health.py" "$capture_log" || status=1
  if [[ -s $capture_stage_raw ]]; then
    if [[ -n $(tshark -r "$capture_stage_raw" -Y 'sll.pkttype==4' \
      -T fields -e frame.number 2>/dev/null | sed -n '1p') ]]; then
      tshark -r "$capture_stage_raw" -Y 'sll.pkttype==4' \
        -w "$capture_pcap" >/dev/null 2>&1
      rm -f -- "$capture_stage_raw"
    else
      mv -f -- "$capture_stage_raw" "$capture_pcap"
    fi
  fi
  capture_stage_raw=
  capture_pcap=
  capture_log=
  return "$status"
}

start_network_monitor() {
  local label=$1
  network_monitor_events="$capture_dir/$label-network-mutations.log"
  network_monitor_ready="$capture_dir/$label-network-monitor-ready"
  network_monitor_done="$capture_dir/$label-network-monitor-done"
  python3 "$INTEGRATION_DIR/monitor-network-mutations.py" \
    --ready "$network_monitor_ready" --events "$network_monitor_events" \
    --done "$network_monitor_done" &
  network_monitor_pid=$!
  for ((i = 0; i < 100; i++)); do
    [[ -f $network_monitor_ready ]] && return 0
    kill -0 "$network_monitor_pid" 2>/dev/null || {
      printf 'network mutation monitor exited before H2 readiness\n' >&2
      return 1
    }
    sleep 0.1
  done
  printf 'timed out waiting for H2 network monitor\n' >&2
  return 1
}

stop_network_monitor() {
  [[ -n $network_monitor_pid ]] || return 0
  local status=0
  if ! kill -0 "$network_monitor_pid" 2>/dev/null; then
    wait "$network_monitor_pid" 2>/dev/null || true
    network_monitor_pid=
    printf 'H2 network monitor stopped before capture completion\n' >&2
    return 1
  fi
  kill -TERM "$network_monitor_pid" 2>/dev/null || true
  wait "$network_monitor_pid" 2>/dev/null || status=$?
  network_monitor_pid=
  if [[ $status -ne 0 || ! -f $network_monitor_done ]]; then
    printf 'H2 network monitor failed to drain cleanly\n' >&2
    return 1
  fi
  if [[ -s $network_monitor_events ]]; then
    printf 'network route/address/link mutation invalidated H2 capture\n' >&2
    return 1
  fi
  network_monitor_events=
  network_monitor_ready=
  network_monitor_done=
}

cleanup() {
  local status=$?
  stop_capture || status=1
  stop_network_monitor || status=1
  stop_pid "$firefox_pid"
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  rm -rf -- "$capture_stage_dir"
  if [[ -n $safe_dir && ($status -ne 0 || $success -ne 1) ]]; then
    local safe_root="$STATE_ROOT/h2-capture-safe"
    if [[ $(dirname -- "$safe_dir") == "$safe_root" ]]; then
      rm -rf -- "$safe_dir"
    else
      printf 'refusing unexpected safe H2 path: %s\n' "$safe_dir" >&2
      status=1
    fi
  fi
  if [[ $status -eq 0 && $success -eq 1 ]]; then
    if [[ $(dirname -- "$capture_dir") == "$STATE_ROOT/h2-captures" ]]; then
      rm -rf -- "$capture_dir"
    else
      printf 'refusing unexpected private H2 path: %s\n' "$capture_dir" >&2
      status=1
    fi
  else
    printf 'H2 comparison failed; private diagnostics preserved at %s\n' \
      "$capture_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_log() {
  local pid=$1 log=$2 pattern=$3
  for ((i = 0; i < 200; i++)); do
    rg -q "$pattern" "$log" && return 0
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}

make_profile() {
  local destination=$1 role=$2 socks_port=${3:-0}
  mkdir -m 0700 "$destination"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$destination/"
  cat >>"$destination/user.js" <<'EOF'
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
user_pref("network.http.http3.enable", false);
EOF
  if [[ $role == socks-browser ]]; then
    python3 "$INTEGRATION_DIR/camouflage_browser_controller.py" \
      --generate-pac-user-js "$socks_port" >>"$destination/user.js"
    cat >>"$destination/user.js" <<'EOF'
user_pref("network.prefetch-next", false);
user_pref("network.http.speculative-parallel-limit", 0);
EOF
  fi
  chmod 0600 "$destination/user.js"
}

firefox_runtime_env=()
if [[ $EUID -eq 0 ]]; then
  runtime_dir="$capture_stage_dir/firefox-runtime"
  mkdir -m 0700 "$runtime_dir"
  firefox_runtime_env=("XDG_RUNTIME_DIR=$runtime_dir")
fi
export MOZ_CRASHREPORTER_DISABLE=1

run_reference() {
  local profile="$capture_dir/reference-profile"
  local keys="$capture_dir/reference.keys"
  local pcap="$capture_dir/reference.pcapng"
  local log="$capture_dir/reference-firefox.log"
  make_profile "$profile" reference
  : >"$keys"; : >"$log"; chmod 0600 "$keys" "$log"
  start_network_monitor reference
  start_capture "$pcap" "$capture_dir/reference-dumpcap.log"
  set +e
  timeout 35 env "SSLKEYLOGFILE=$keys" "${firefox_runtime_env[@]}" \
    "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
    "$REFERENCE_BIN" --headless --new-instance --no-remote \
    --profile "$profile" --screenshot "$capture_dir/reference.png" \
    "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/camouflage/index.html?scenario=browser_page" \
    >"$log" 2>&1 &
  firefox_pid=$!
  wait "$firefox_pid"
  local browser_status=$?
  firefox_pid=
  set -e
  [[ $browser_status -eq 0 || $browser_status -eq 124 ]] || \
    printf 'reference Firefox status=%s; evaluating H2 evidence\n' "$browser_status" >&2
  stop_capture
  stop_network_monitor
}

run_candidate() {
  local profile="$capture_dir/$arm-naivefox-profile"
  local browser_profile="$capture_dir/$arm-browser-profile"
  local config="$capture_dir/$arm-config.json"
  local keys="$capture_dir/$arm.keys"
  local pcap="$capture_dir/$arm.pcapng"
  local log="$capture_dir/$arm-naivefox.log"
  local browser_log="$capture_dir/$arm-firefox.log"
  local socks_port
  socks_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  make_profile "$profile" naivefox
  make_profile "$browser_profile" socks-browser "$socks_port"
  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
      --output "$config" --arm "$arm" --protocol h2 \
      --socks-port "$socks_port" --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT"
  : >"$keys"; : >"$log"; : >"$browser_log"
  chmod 0600 "$keys" "$log" "$browser_log"
  start_network_monitor "$arm"
  start_capture "$pcap" "$capture_dir/$arm-dumpcap.log"
  env -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
    "SSLKEYLOGFILE=$keys" "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" \
    NAIVEFOX_PROFILE="$profile" "$NAIVEFOX_BIN" "$config" >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  set +e
  timeout 35 env -u SSLKEYLOGFILE "${firefox_runtime_env[@]}" \
    "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
    "$REFERENCE_BIN" --headless --new-instance --no-remote \
    --profile "$browser_profile" --screenshot "$capture_dir/$arm.png" \
    "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/camouflage/index.html?scenario=browser_page&arm=$arm" \
    >"$browser_log" 2>&1 &
  firefox_pid=$!
  wait "$firefox_pid"
  local browser_status=$?
  firefox_pid=
  set -e
  [[ $browser_status -eq 0 || $browser_status -eq 124 ]] || \
    printf 'inner Firefox status=%s; evaluating H2 evidence\n' "$browser_status" >&2
  sleep 0.25
  stop_capture
  stop_network_monitor
  stop_pid "$naivefox_pid"
  naivefox_pid=
  rg -q '^Outer protocol: h2$' "$log"
  rg -q '^Padding negotiated: yes$' "$log"
}

run_reference
run_candidate

semantic_separator=$'\x1f'
TSHARK_FIELDS=(-T fields -E header=y -E separator=, -E quote=d \
  -E occurrence=a -E "aggregator=$semantic_separator")
extract_cohort() {
  local cohort=$1
  local pcap="$capture_dir/$cohort.pcapng"
  local keys="$capture_dir/$cohort.keys"
  local decode=(-d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
    -o "tls.keylog_file:$keys")
  [[ -s $pcap && -s $keys ]] || {
    printf 'missing private H2 capture/keylog for %s\n' "$cohort" >&2
    return 1
  }
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream \
    -e tls.handshake.version -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite -e tls.handshake.extension.type \
    -e tls.handshake.extensions_supported_group -e tls.handshake.sig_hash_alg \
    -e tls.handshake.extensions_key_share_group \
    -e tls.handshake.extensions_alpn_str \
    -e tls.handshake.extensions_server_name \
    >"$capture_dir/$cohort-clienthello.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==2" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream \
    -e tls.handshake.version -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite \
    -e tls.handshake.extensions_key_share_selected_group \
    >"$capture_dir/$cohort-serverhello.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.syn==1 && tcp.flags.ack==0" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream \
    >"$capture_dir/$cohort-syn.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.extensions_alpn_str" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream \
    -e tls.handshake.extensions_alpn_str >"$capture_dir/$cohort-alpn.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.type==4 && http2.flags.ack.settings==0" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.settings.id \
    -e http2.settings.header_table_size -e http2.settings.enable_push \
    -e http2.settings.max_concurrent_streams \
    -e http2.settings.initial_window_size -e http2.settings.max_frame_size \
    -e http2.settings.max_header_list_size -e http2.settings.extended_connect \
    -e http2.settings.no_rfc7540_priorities >"$capture_dir/$cohort-settings.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.type" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.streamid -e http2.flags >"$capture_dir/$cohort-frames.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && (http2.headers.method || http2.headers.status)" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.streamid -e http2.headers.method -e http2.headers.status \
    -e http2.header.name >"$capture_dir/$cohort-headers.csv"
  # Private-only values prove native document/resource semantics.  This CSV is
  # destroyed with capture_dir; the safe summary exports boolean results only.
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.headers.method==\"GET\" && http2.header.name && !(http2.header.name contains \"authorization\")" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.streamid -e http2.headers.method -e http2.header.name \
    -e http2.header.value >"$capture_dir/$cohort-get-header-values.csv"
}

extract_cohort reference
extract_cohort "$arm"

safe_dir="$STATE_ROOT/h2-capture-safe/$capture_id"
mkdir -m 0700 -p "$safe_dir"
python3 "$INTEGRATION_DIR/h2_decrypted_parity_summary.py" \
  --input-dir "$capture_dir" --events "$safe_dir/outer-h2-events.csv" \
  --summary "$safe_dir/summary.txt" --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --arm "$arm"
{
  printf 'isolated_network=yes\n'
  printf 'network_mutation_monitor=netlink_route_v1_fail_closed\n'
  printf 'capture_offload_policy=namespace_loopback_offload_disabled_and_verified\n'
  printf 'capture_drop_policy=reject_nonzero\n'
  printf 'browser_backend=commandline\n'
  printf 'browser_start_state=cold_after_capture_start_both_cohorts\n'
  printf 'capture_revision=%s\n' "$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
  printf 'capture_worktree_dirty=%s\n' "$(source_worktree_dirty)"
  printf 'capture_source_state_sha256=%s\n' "$(source_state_sha256)"
  printf 'reference_binary=%s\n' "$(readelf -n "$REFERENCE_BIN" | sed -n 's/^ *Build ID: //p' | head -n1)"
  printf 'naivefox_binary=%s\n' "$(readelf -n "$NAIVEFOX_BIN" | sed -n 's/^ *Build ID: //p' | head -n1)"
  printf 'reference_libxul_sha256=%s\n' "$(sha256sum "$REFERENCE_LIBDIR/libxul.so" | cut -d' ' -f1)"
  printf 'naivefox_libxul_sha256=%s\n' "$(sha256sum "$NAIVEFOX_LIBDIR/libxul.so" | cut -d' ' -f1)"
} >>"$safe_dir/summary.txt"
find "$safe_dir" -type f -exec chmod 0600 {} +

encoded_user=$(VALUE="$NAIVEFOX_FIXTURE_USER" python3 -c \
  'import os,urllib.parse; print(urllib.parse.quote(os.environ["VALUE"], safe=""))')
encoded_pass=$(VALUE="$NAIVEFOX_FIXTURE_PASS" python3 -c \
  'import os,urllib.parse; print(urllib.parse.quote(os.environ["VALUE"], safe=""))')
if find "$safe_dir" -type f \( -name '*.pcap' -o -name '*.pcapng' -o \
     -name '*.keys' -o -name '*.log' \) -print -quit | rg -q . ||
   rg -F -e "$NAIVEFOX_FIXTURE_USER" -e "$NAIVEFOX_FIXTURE_PASS" \
     -e "$encoded_user" -e "$encoded_pass" "$safe_dir" ||
   rg -i -e proxy-authorization -e authorization: -e 'localhost:' \
     -e 'traffic_secret' "$safe_dir"; then
  printf 'private material reached safe H2 output\n' >&2
  exit 1
fi

success=1
printf 'Firefox/NaiveFox same-base H2 decrypted comparison passed\n'
printf 'sanitized aggregates: %s\n' "$safe_dir"
