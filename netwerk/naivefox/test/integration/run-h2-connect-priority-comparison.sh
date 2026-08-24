#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
original_args=("$@")

if [[ $# -gt 0 ]]; then
  if [[ $# -eq 1 && $1 == --help ]]; then
    printf 'usage: %s\n' "$0"
    exit 0
  fi
  printf 'H2 CONNECT-priority diagnostic accepts no arguments\n' >&2
  exit 2
fi
if [[ ${NAIVEFOX_CAPTURE_MODE:-quick} != same-base ]]; then
  printf 'H2 CONNECT-priority diagnostic requires same-base mode\n' >&2
  exit 2
fi
case ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0} in
  0)
    for tool in unshare ip ethtool; do
      command -v "$tool" >/dev/null 2>&1 || {
        printf 'H2 CONNECT-priority isolation requires %s\n' "$tool" >&2
        exit 1
      }
    done
    export NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1
    exec unshare --net --mount-proc \
      "$INTEGRATION_DIR/run-camouflage-isolated-network.sh" \
      "$0" "${original_args[@]}"
    ;;
  1) ;;
  *) printf 'invalid isolated-network marker\n' >&2; exit 2 ;;
esac
[[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0} == 1 ]] || {
  printf 'isolated-network state is inconsistent\n' >&2
  exit 2
}

for tool in dumpcap tshark getcap openssl python3 readelf rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required H2 CONNECT-priority tool not found: %s\n' "$tool" >&2
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
  printf 'same-base reference binary and objdir are required\n' >&2
  exit 2
}
REFERENCE_LIBDIR=${NAIVEFOX_CAPTURE_REFERENCE_LIBDIR:-$(dirname "$REFERENCE_BIN")}
NAIVEFOX_BIN=${NAIVEFOX_CAPTURE_NAIVEFOX_BIN:-$OBJDIR/dist/bin/naivefox}
NAIVEFOX_LIBDIR=${NAIVEFOX_CAPTURE_NAIVEFOX_LIBDIR:-$OBJDIR/dist/bin}
browser_python=${NAIVEFOX_CAMOUFLAGE_PYTHON:-}
if [[ -z $browser_python && -x "$OBJDIR/camouflage-venv/bin/python" ]]; then
  browser_python="$OBJDIR/camouflage-venv/bin/python"
fi
browser_python=${browser_python:-$(command -v python3)}
"$browser_python" -c 'import selenium' || {
  printf 'H2 CONNECT-priority diagnostic requires Selenium\n' >&2
  exit 1
}
for artifact in "$REFERENCE_BIN" "$REFERENCE_LIBDIR/libssl3.so" \
                "$REFERENCE_LIBDIR/libxul.so" "$NAIVEFOX_BIN" \
                "$NAIVEFOX_LIBDIR/libssl3.so" "$NAIVEFOX_LIBDIR/libxul.so"; do
  [[ -f $artifact ]] || {
    printf 'required H2 CONNECT-priority artifact is missing: %s\n' "$artifact" >&2
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
capture_dir="$STATE_ROOT/h2-connect-priority-captures/$capture_id"
safe_dir=
stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-h2-priority.XXXXXX")
mkdir -p "$capture_dir"
chmod 0700 "$capture_dir"
chmod 0700 "$stage_dir"

capture_pid=
capture_stage=
capture_destination=
capture_log=
monitor_pid=
monitor_events=
monitor_ready=
monitor_done=
controller_pid=
controller_stop=
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
    while IFS= read -r -d '' path; do
      printf 'untracked:%s\0' "$path"
      sha256sum "$SOURCE_ROOT/$path"
    done < <(git -C "$SOURCE_ROOT" ls-files --others --exclude-standard -z | sort -z)
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
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  [[ -z $pid ]] || wait "$pid" 2>/dev/null || true
}

wait_for_log() {
  local pid=$1 log=$2 pattern=$3
  for ((i = 0; i < 200; i++)); do
    rg -q "$pattern" "$log" && return 0
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}

wait_for_marker() {
  local path=$1 pid=$2 description=$3
  for ((i = 0; i < 400; i++)); do
    [[ -s $path ]] && return 0
    kill -0 "$pid" 2>/dev/null || {
      printf '%s exited before readiness\n' "$description" >&2
      return 1
    }
    sleep 0.1
  done
  printf 'timed out waiting for %s\n' "$description" >&2
  return 1
}

urgent_marker_token='diagnostic-first-socks-tunnel-urgent-start'
urgent_marker_pattern='^\[[0-9]{4}/[0-9]{6}\.[0-9]{6}:INFO:naivefox\] Connection 1 diagnostic-first-socks-tunnel-urgent-start applied=1 incremental=[01] protocol=h2$'

assert_urgent_marker_absent() {
  local log=$1 phase=$2
  if rg -F -q -- "$urgent_marker_token" "$log"; then
    printf 'urgent-start marker appeared during %s, outside captured workload\n' \
      "$phase" >&2
    return 1
  fi
}

validate_urgent_marker_after_workload() {
  local log=$1 urgent=$2
  local -a prefixed=() exact=() established=()
  mapfile -t prefixed < <(rg -n -F -- "$urgent_marker_token" "$log" || true)
  if [[ $urgent == no ]]; then
    [[ ${#prefixed[@]} -eq 0 ]] || {
      printf 'default cohort unexpectedly applied urgent-start diagnostic\n' >&2
      return 1
    }
    return 0
  fi
  mapfile -t exact < <(rg -n -- "$urgent_marker_pattern" "$log" || true)
  mapfile -t established < <(rg -n '^Outer protocol: h2$' "$log" || true)
  [[ ${#prefixed[@]} -eq 1 && ${#exact[@]} -eq 1 ]] || {
    printf 'urgent cohort lacks one unambiguous Connection 1 H2 applied marker\n' >&2
    return 1
  }
  [[ ${#established[@]} -ge 1 ]] || {
    printf 'urgent cohort lacks CONNECT-established H2 evidence\n' >&2
    return 1
  }
  local marker_line=${exact[0]%%:*}
  local established_line=${established[0]%%:*}
  [[ $marker_line =~ ^[0-9]+$ && $established_line =~ ^[0-9]+$ && \
    $marker_line -lt $established_line ]] || {
    printf 'urgent marker is not ordered before first CONNECT-established evidence\n' >&2
    return 1
  }
}

start_capture() {
  local cohort=$1
  capture_destination="$capture_dir/$cohort.pcapng"
  capture_log="$capture_dir/$cohort-dumpcap.log"
  capture_stage="$stage_dir/$cohort.raw.pcapng"
  : >"$capture_log"
  chmod 0600 "$capture_log"
  dumpcap -q -i any -f "tcp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:60 -a filesize:65536 -w "$capture_stage" \
    >"$capture_log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      printf 'dumpcap exited before H2 CONNECT-priority readiness\n' >&2
      return 1
    }
    [[ -s $capture_stage ]] && return 0
    sleep 0.1
  done
  printf 'timed out waiting for H2 CONNECT-priority capture\n' >&2
  return 1
}

stop_capture() {
  [[ -n $capture_pid ]] || return 0
  local was_running=0 status=0
  if kill -0 "$capture_pid" 2>/dev/null; then
    was_running=1
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  [[ $was_running -eq 1 ]] || {
    printf 'dumpcap stopped before workload completion\n' >&2
    status=1
  }
  python3 "$INTEGRATION_DIR/camouflage_capture_health.py" "$capture_log" || status=1
  if [[ -s $capture_stage ]]; then
    if [[ -n $(tshark -r "$capture_stage" -Y 'sll.pkttype==4' \
      -T fields -e frame.number 2>/dev/null | sed -n '1p') ]]; then
      tshark -r "$capture_stage" -Y 'sll.pkttype==4' \
        -w "$capture_destination" >/dev/null 2>&1
      rm -f -- "$capture_stage"
    else
      mv -f -- "$capture_stage" "$capture_destination"
    fi
  fi
  capture_stage=
  capture_destination=
  capture_log=
  return "$status"
}

start_monitor() {
  local cohort=$1
  monitor_events="$capture_dir/$cohort-network-mutations.log"
  monitor_ready="$capture_dir/$cohort-monitor-ready"
  monitor_done="$capture_dir/$cohort-monitor-done"
  python3 "$INTEGRATION_DIR/monitor-network-mutations.py" \
    --events "$monitor_events" --ready "$monitor_ready" --done "$monitor_done" &
  monitor_pid=$!
  wait_for_marker "$monitor_ready" "$monitor_pid" 'network mutation monitor'
}

stop_monitor() {
  [[ -n $monitor_pid ]] || return 0
  local status=0
  kill -0 "$monitor_pid" 2>/dev/null || status=1
  kill -TERM "$monitor_pid" 2>/dev/null || true
  wait "$monitor_pid" 2>/dev/null || status=1
  monitor_pid=
  [[ -f $monitor_done && ! -s $monitor_events ]] || status=1
  if [[ $status -ne 0 ]]; then
    printf 'network route/address/link mutation invalidated H2 diagnostic\n' >&2
  fi
  monitor_events=
  monitor_ready=
  monitor_done=
  return "$status"
}

stop_controller() {
  [[ -n $controller_pid ]] || return 0
  [[ -z $controller_stop ]] || : >"$controller_stop"
  if ! timeout 20 tail --pid="$controller_pid" -f /dev/null; then
    printf 'Firefox diagnostic controller did not stop cleanly\n' >&2
    stop_pid "$controller_pid"
    controller_pid=
    controller_stop=
    return 1
  fi
  wait "$controller_pid"
  controller_pid=
  controller_stop=
}

cleanup() {
  local status=$?
  stop_capture || status=1
  stop_monitor || status=1
  stop_controller || status=1
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  rm -rf -- "$stage_dir"
  if [[ -n $safe_dir && ($status -ne 0 || $success -ne 1) ]]; then
    local safe_root="$STATE_ROOT/h2-connect-priority-safe"
    if [[ $(dirname -- "$safe_dir") == "$safe_root" ]]; then
      rm -rf -- "$safe_dir"
    fi
  fi
  if [[ $status -eq 0 && $success -eq 1 ]]; then
    if [[ $(dirname -- "$capture_dir") == "$STATE_ROOT/h2-connect-priority-captures" ]]; then
      rm -rf -- "$capture_dir"
    else
      printf 'refusing unexpected private capture path\n' >&2
      status=1
    fi
  else
    printf 'H2 CONNECT-priority diagnostic failed; private data preserved at %s\n' \
      "$capture_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

make_profile() {
  local destination=$1 socks_port=${2:-0}
  mkdir -m 0700 "$destination"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$destination/"
  cat >>"$destination/user.js" <<'EOF'
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
user_pref("network.prefetch-next", false);
user_pref("network.http.speculative-parallel-limit", 0);
user_pref("network.http.http3.enable", false);
EOF
  if [[ $socks_port -ne 0 ]]; then
    "$browser_python" "$INTEGRATION_DIR/camouflage_browser_controller.py" \
      --generate-pac-user-js "$socks_port" >>"$destination/user.js"
  fi
  chmod 0600 "$destination/user.js"
}

firefox_runtime_env=()
if [[ $EUID -eq 0 ]]; then
  runtime_dir="$stage_dir/firefox-runtime"
  mkdir -m 0700 "$runtime_dir"
  firefox_runtime_env=("XDG_RUNTIME_DIR=$runtime_dir")
fi
export MOZ_CRASHREPORTER_DISABLE=1

run_firefox_proxied() {
  local cohort=firefox-proxied
  local profile="$capture_dir/$cohort-profile"
  local keys="$capture_dir/$cohort.keys"
  local token
  token=$(openssl rand -hex 16)
  local ready="$capture_dir/$cohort-ready.json"
  local navigate="$capture_dir/$cohort-navigate"
  local done="$capture_dir/$cohort-done"
  controller_stop="$capture_dir/$cohort-stop"
  make_profile "$profile"
  : >"$keys"
  : >"$capture_dir/$cohort-webdriver.log"
  : >"$capture_dir/$cohort-controller.log"
  chmod 0600 "$keys" "$capture_dir/$cohort-webdriver.log" \
    "$capture_dir/$cohort-controller.log"
  env "SSLKEYLOGFILE=$keys" "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" \
    "${firefox_runtime_env[@]}" MOZ_HEADLESS=1 \
    NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    "$browser_python" "$INTEGRATION_DIR/proxied_firefox_controller.py" \
    --binary "$REFERENCE_BIN" --profile "$profile" \
    --proxy-host localhost --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --target-host localhost --target-port "$NAIVEFOX_FIXTURE_HTTPS_PORT" \
    --url "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/camouflage/index.html?scenario=initial&completion=$token" \
    --completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$token" \
    --ready-file "$ready" --navigate-file "$navigate" --done-file "$done" \
    --stop-file "$controller_stop" \
    --webdriver-log "$capture_dir/$cohort-webdriver.log" --timeout 35 \
    >"$capture_dir/$cohort-controller.log" 2>&1 &
  controller_pid=$!
  wait_for_marker "$ready" "$controller_pid" 'privileged proxied Firefox controller'
  start_monitor "$cohort"
  start_capture "$cohort"
  : >"$navigate"
  wait_for_marker "$done" "$controller_pid" 'proxied Firefox navigation'
  sleep 0.25
  stop_capture
  stop_monitor
  stop_controller
}

run_naivefox() {
  local cohort=$1 urgent=$2
  local profile="$capture_dir/$cohort-naivefox-profile"
  local browser_profile="$capture_dir/$cohort-browser-profile"
  local config="$capture_dir/$cohort-config.json"
  local keys="$capture_dir/$cohort.keys"
  local socks_port token
  socks_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  token=$(openssl rand -hex 16)
  local ready="$capture_dir/$cohort-ready.json"
  local navigate="$capture_dir/$cohort-navigate"
  local done="$capture_dir/$cohort-done"
  controller_stop="$capture_dir/$cohort-stop"
  make_profile "$profile"
  make_profile "$browser_profile" "$socks_port"
  local -a urgent_arg=()
  [[ $urgent == yes ]] && urgent_arg=(--diagnostic-first-socks-tunnel-urgent-start)
  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
      --output "$config" --arm off --protocol h2 --socks-port "$socks_port" \
      --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" "${urgent_arg[@]}"
  : >"$keys"
  : >"$capture_dir/$cohort-naivefox.log"
  : >"$capture_dir/$cohort-browser.log"
  : >"$capture_dir/$cohort-webdriver.log"
  : >"$capture_dir/$cohort-controller.log"
  chmod 0600 "$keys" "$capture_dir/$cohort-"*.log
  env -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
    "SSLKEYLOGFILE=$keys" "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" \
    NAIVEFOX_PROFILE="$profile" "$NAIVEFOX_BIN" "$config" \
    >"$capture_dir/$cohort-naivefox.log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$capture_dir/$cohort-naivefox.log" \
    '^SOCKS5 listening on '
  env -u SSLKEYLOGFILE "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" \
    "${firefox_runtime_env[@]}" MOZ_HEADLESS=1 \
    "$browser_python" "$INTEGRATION_DIR/camouflage_browser_controller.py" \
    --binary "$REFERENCE_BIN" --profile "$browser_profile" --backend selenium \
    --protocol h2 --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --socks-port "$socks_port" \
    --url "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/camouflage/index.html?scenario=initial&completion=$token" \
    --completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$token" \
    --ready-file "$ready" --navigate-file "$navigate" --done-file "$done" \
    --stop-file "$controller_stop" --browser-log "$capture_dir/$cohort-browser.log" \
    --webdriver-log "$capture_dir/$cohort-webdriver.log" --timeout 35 \
    >"$capture_dir/$cohort-controller.log" 2>&1 &
  controller_pid=$!
  wait_for_marker "$ready" "$controller_pid" 'SOCKS Firefox controller'
  assert_urgent_marker_absent "$capture_dir/$cohort-naivefox.log" \
    "$cohort pre-capture readiness"
  start_monitor "$cohort"
  start_capture "$cohort"
  : >"$navigate"
  wait_for_marker "$done" "$controller_pid" 'SOCKS Firefox navigation'
  sleep 0.25
  stop_capture
  stop_monitor
  stop_controller
  stop_pid "$naivefox_pid"
  naivefox_pid=
  validate_urgent_marker_after_workload \
    "$capture_dir/$cohort-naivefox.log" "$urgent"
  rg -q '^Outer protocol: h2$' "$capture_dir/$cohort-naivefox.log"
  rg -q '^Padding negotiated: yes$' "$capture_dir/$cohort-naivefox.log"
}

run_firefox_proxied
run_naivefox naivefox-default no
run_naivefox naivefox-urgent yes

separator=$'\x1f'
TSHARK_FIELDS=(-T fields -E header=y -E separator=, -E quote=d \
  -E occurrence=a -E "aggregator=$separator")
extract_cohort() {
  local cohort=$1 pcap="$capture_dir/$1.pcapng" keys="$capture_dir/$1.keys"
  local decode=(-d "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,tls" \
    -o "tls.keylog_file:$keys")
  [[ -s $pcap && -s $keys ]] || {
    printf 'missing private capture material for %s\n' "$cohort" >&2
    return 1
  }
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version -e tls.handshake.ciphersuite \
    -e tls.handshake.extension.type -e tls.handshake.extensions_supported_group \
    -e tls.handshake.sig_hash_alg -e tls.handshake.extensions_key_share_group \
    -e tls.handshake.extensions_alpn_str -e tls.handshake.extensions_server_name \
    >"$capture_dir/$cohort-clienthello.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==2" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version -e tls.handshake.ciphersuite \
    -e tls.handshake.extensions_key_share_selected_group \
    >"$capture_dir/$cohort-serverhello.csv"
  tshark -r "$pcap" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tcp.flags.syn==1 && tcp.flags.ack==0" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream >"$capture_dir/$cohort-syn.csv"
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
    -e http2.settings.max_concurrent_streams -e http2.settings.initial_window_size \
    -e http2.settings.max_frame_size -e http2.settings.max_header_list_size \
    -e http2.settings.extended_connect -e http2.settings.no_rfc7540_priorities \
    >"$capture_dir/$cohort-settings.csv"
  tshark -r "$pcap" "${decode[@]}" -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.type" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.streamid -e http2.flags >"$capture_dir/$cohort-frames.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && (http2.headers.method || http2.headers.status)" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.streamid -e http2.headers.method -e http2.headers.status \
    -e http2.header.name >"$capture_dir/$cohort-headers.csv"
  tshark -r "$pcap" "${decode[@]}" \
    -Y "tcp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http2.headers.method==\"CONNECT\"" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e tcp.srcport -e tcp.dstport -e tcp.stream -e http2.type \
    -e http2.streamid -e http2.headers.method -e http2.flags.priority \
    -e http2.stream_dependency -e http2.headers.weight_real \
    >"$capture_dir/$cohort-connect-priority.csv"
}

for cohort in firefox-proxied naivefox-default naivefox-urgent; do
  extract_cohort "$cohort"
done

safe_dir="$STATE_ROOT/h2-connect-priority-safe/$capture_id"
mkdir -p "$safe_dir"
chmod 0700 "$safe_dir"
python3 "$INTEGRATION_DIR/h2_connect_priority_summary.py" \
  --input-dir "$capture_dir" --events "$safe_dir/outer-h2-events.csv" \
  --summary "$safe_dir/summary.txt" --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT"
{
  printf 'isolated_network=yes\n'
  printf 'network_mutation_monitor=netlink_route_v1_fail_closed\n'
  printf 'capture_offload_policy=namespace_loopback_offload_disabled_and_verified\n'
  printf 'capture_drop_policy=reject_nonzero\n'
  printf 'browser_backend=selenium_all_cohorts\n'
  printf 'browser_start_state=ready_before_capture_navigation_after_capture\n'
  printf 'privileged_filter_scope=exact_https_target_authority\n'
  printf 'proxy_authorization_delivery=private_preemptive_proxy_info\n'
  printf 'capture_revision=%s\n' "$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
  printf 'capture_worktree_dirty=%s\n' "$(source_worktree_dirty)"
  printf 'capture_source_state_sha256=%s\n' "$(source_state_sha256)"
  printf 'reference_binary=%s\n' "$(readelf -n "$REFERENCE_BIN" | sed -n 's/^ *Build ID: //p' | sed -n '1p')"
  printf 'naivefox_binary=%s\n' "$(readelf -n "$NAIVEFOX_BIN" | sed -n 's/^ *Build ID: //p' | sed -n '1p')"
  printf 'reference_libxul_sha256=%s\n' "$(sha256sum "$REFERENCE_LIBDIR/libxul.so" | cut -d' ' -f1)"
  printf 'naivefox_libxul_sha256=%s\n' "$(sha256sum "$NAIVEFOX_LIBDIR/libxul.so" | cut -d' ' -f1)"
} >>"$safe_dir/summary.txt"
find "$safe_dir" -type f -exec chmod 0600 {} +

encoded_user=$(VALUE="$NAIVEFOX_FIXTURE_USER" python3 -c \
  'import os,urllib.parse; print(urllib.parse.quote(os.environ["VALUE"], safe=""))')
encoded_pass=$(VALUE="$NAIVEFOX_FIXTURE_PASS" python3 -c \
  'import os,urllib.parse; print(urllib.parse.quote(os.environ["VALUE"], safe=""))')
if find "$safe_dir" -type f \( -name '*.pcap' -o -name '*.pcapng' -o \
     -name '*.keys' -o -name '*.log' -o -name '*.json' \) -print -quit | rg -q . ||
   rg -F -e "$NAIVEFOX_FIXTURE_USER" -e "$NAIVEFOX_FIXTURE_PASS" \
     -e "$encoded_user" -e "$encoded_pass" "$safe_dir" ||
   rg -i -e proxy-authorization -e authorization: -e 'localhost:' \
     -e traffic_secret "$safe_dir"; then
  printf 'private material reached safe H2 CONNECT-priority output\n' >&2
  exit 1
fi

success=1
printf 'same-base H2 CONNECT-priority comparison passed\n'
printf 'sanitized aggregates: %s\n' "$safe_dir"
