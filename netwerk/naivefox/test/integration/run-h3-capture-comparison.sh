#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
original_args=("$@")

comparison_design=legacy
comparison_arms=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --compare-arms)
      comparison_design=arms
      shift
      ;;
    --compare-arm)
      comparison_design=arms
      comparison_arms+=("${2:-}")
      shift 2
      ;;
    --help)
      printf 'usage: %s [--compare-arms] [--compare-arm off|gate|root|root-pmtud-control|document-complete|tree-complete|tree-complete-css|tree-early-overlap|tree-root-overlap|tree-root-overlap-css|tree-overlap ...]\n' "$0"
      exit 0
      ;;
    *)
      printf 'unknown H3 capture comparison argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ $comparison_design == arms && ${#comparison_arms[@]} -eq 0 ]]; then
  comparison_arms=(off gate root tree-complete tree-overlap)
fi
declare -A seen_comparison_arms=()
for arm in "${comparison_arms[@]}"; do
  case $arm in
    off | gate | root | root-pmtud-control | document-complete | tree-complete | tree-complete-css | tree-early-overlap | tree-root-overlap | tree-root-overlap-css | tree-overlap) ;;
    *) printf 'unsupported comparison arm: %s\n' "$arm" >&2; exit 2 ;;
  esac
  if [[ -n ${seen_comparison_arms[$arm]:-} ]]; then
    printf 'duplicate comparison arm: %s\n' "$arm" >&2
    exit 2
  fi
  seen_comparison_arms[$arm]=1
done
if [[ -n ${seen_comparison_arms[tree-overlap]:-} &&
      -z ${seen_comparison_arms[tree-complete]:-} ]]; then
  printf 'tree-overlap comparison requires tree-complete\n' >&2
  exit 2
fi
if [[ -n ${seen_comparison_arms[tree-early-overlap]:-} &&
      -z ${seen_comparison_arms[tree-complete]:-} ]]; then
  printf 'tree-early-overlap comparison requires tree-complete\n' >&2
  exit 2
fi
if [[ -n ${seen_comparison_arms[root-pmtud-control]:-} &&
      -z ${seen_comparison_arms[root]:-} ]]; then
  printf 'root-pmtud-control comparison requires root\n' >&2
  exit 2
fi
if [[ -n ${seen_comparison_arms[tree-root-overlap]:-} &&
      -z ${seen_comparison_arms[tree-complete]:-} ]]; then
  printf 'tree-root-overlap comparison requires tree-complete\n' >&2
  exit 2
fi
if [[ -n ${seen_comparison_arms[tree-root-overlap-css]:-} &&
      -z ${seen_comparison_arms[tree-complete-css]:-} ]]; then
  printf 'tree-root-overlap-css comparison requires tree-complete-css\n' >&2
  exit 2
fi

capture_pid=
capture_pcap=
capture_raw=
capture_log=
capture_stage_dir=
capture_stage_raw=
firefox_pid=
naivefox_pid=
capture_dir=
safe_dir=
network_monitor_pid=
network_monitor_events=
network_monitor_ready=
network_monitor_done=
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

stop_capture() {
  [[ -n $capture_pid ]] || return 0
  local was_running=0
  local status=0
  if [[ -n $capture_pid ]] && kill -0 "$capture_pid" 2>/dev/null; then
    was_running=1
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  [[ -z $capture_pid ]] || wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  if [[ $was_running -ne 1 ]]; then
    printf 'dumpcap stopped before the H3 workload capture was complete\n' >&2
    status=1
  fi
  if ! python3 "$INTEGRATION_DIR/camouflage_capture_health.py" "$capture_log"; then
    status=1
  fi
  if [[ -n $capture_stage_raw && -s $capture_stage_raw ]]; then
    # The WSL `any` interface records loopback transmit and receive copies.
    # Retain the transmit copy when the capture link type exposes one before
    # QUIC dissection.  A private network namespace can expose loopback without
    # pkttype=4; in that case the raw capture is already the sole usable view.
    if [[ -n $(tshark -r "$capture_stage_raw" -Y 'sll.pkttype==4' \
      -T fields -e frame.number 2>/dev/null | sed -n '1p') ]]; then
      tshark -r "$capture_stage_raw" -Y 'sll.pkttype==4' -w "$capture_pcap" \
        >/dev/null 2>&1
      rm -f -- "$capture_stage_raw"
    else
      mv -f -- "$capture_stage_raw" "$capture_pcap"
    fi
  fi
  capture_pcap=
  capture_raw=
  capture_log=
  capture_stage_raw=
  return "$status"
}

cleanup() {
  local status=$?
  if ! stop_capture; then
    status=1
  fi
  if declare -F stop_network_mutation_monitor >/dev/null && \
     ! stop_network_mutation_monitor; then
    status=1
  fi
  stop_pid "$firefox_pid"
  stop_pid "$naivefox_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  if [[ -n $capture_stage_dir ]]; then
    rm -rf -- "$capture_stage_dir"
  fi
  if [[ -n $safe_dir && ($status -ne 0 || $success -ne 1) ]]; then
    local safe_root="$STATE_ROOT/h3-capture-safe"
    if [[ $(dirname -- "$safe_dir") == "$safe_root" &&
          $(basename -- "$safe_dir") != . &&
          $(basename -- "$safe_dir") != .. ]]; then
      rm -rf -- "$safe_dir"
    else
      printf 'refusing to remove unexpected safe H3 path: %s\n' \
        "$safe_dir" >&2
      status=1
    fi
  fi
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
if [[ $comparison_design == arms && $capture_mode != same-base ]]; then
  printf '%s\n' '--compare-arms requires NAIVEFOX_CAPTURE_MODE=same-base' >&2
  exit 2
fi
if [[ $comparison_design == arms ]]; then
  isolated_network_entered=${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0}
  case $isolated_network_entered in
    0 | 1) ;;
    *) printf 'invalid internal isolated-network marker\n' >&2; exit 2 ;;
  esac
  if [[ $isolated_network_entered == 0 ]]; then
    for tool in unshare ip ethtool; do
      command -v "$tool" >/dev/null 2>&1 || {
        printf 'H3 arm comparison isolation requires %s\n' "$tool" >&2
        exit 1
      }
    done
    export NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1
    exec unshare --net --mount-proc \
      "$INTEGRATION_DIR/run-camouflage-isolated-network.sh" \
      "$0" "${original_args[@]}"
  fi
  if [[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0} != 1 ]]; then
    printf 'isolated H3 arm comparison marker is inconsistent\n' >&2
    exit 2
  fi
fi
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

"$INTEGRATION_DIR/start.sh" --mode h3
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
[[ $NAIVEFOX_FIXTURE_MODE == h3 ]]

capture_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
capture_dir="$STATE_ROOT/h3-captures/$capture_id"
mkdir -m 0700 -p "$capture_dir"
# WSL's dumpcap/AppArmor combination may deny opening a capture directly below
# /home even when the caller is root.  Capture into a private /tmp staging
# directory, then write the filtered result into the private diagnostics tree.
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-dumpcap.XXXXXX")
chmod 0700 "$capture_stage_dir"
firefox_runtime_env=()
if [[ $EUID -eq 0 ]]; then
  firefox_runtime_dir="$capture_stage_dir/firefox-runtime"
  mkdir -m 0700 "$firefox_runtime_dir"
  firefox_runtime_env=("XDG_RUNTIME_DIR=$firefox_runtime_dir")
fi

export MOZ_CRASHREPORTER_DISABLE=1

start_capture() {
  local pcap=$1
  local log=$2
  capture_pcap=$pcap
  capture_log=$log
  capture_stage_raw="$capture_stage_dir/$(basename "${pcap%.pcapng}.raw.pcapng")"
  : >"$log"
  chmod 0600 "$log"
  capture_raw=$capture_stage_raw
  # `any` is the only reliable WSL loopback source here.  stop_capture filters
  # its duplicate cooked receive/transmit views before stateful QUIC decode.
  dumpcap -q -i any \
    -f "udp port $NAIVEFOX_FIXTURE_PROXY_PORT or tcp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
    -a duration:60 -a filesize:65536 -w "$capture_stage_raw" >"$log" 2>&1 &
  capture_pid=$!
  for ((i = 0; i < 100; i++)); do
    kill -0 "$capture_pid" 2>/dev/null || {
      cat "$log" >&2
      printf 'dumpcap exited before capture readiness\n' >&2
      return 1
    }
    [[ -s $capture_stage_raw ]] && return 0
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

start_network_mutation_monitor() {
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
      printf 'network mutation monitor exited before readiness\n' >&2
      return 1
    }
    sleep 0.1
  done
  printf 'timed out waiting for network mutation monitor readiness\n' >&2
  return 1
}

stop_network_mutation_monitor() {
  [[ -n $network_monitor_pid ]] || return 0
  local monitor_status=0
  if kill -0 "$network_monitor_pid" 2>/dev/null; then
    kill -TERM "$network_monitor_pid" 2>/dev/null || true
  else
    wait "$network_monitor_pid" 2>/dev/null || true
    network_monitor_pid=
    printf 'network mutation monitor stopped before capture completion\n' >&2
    return 1
  fi
  if wait "$network_monitor_pid" 2>/dev/null; then
    monitor_status=0
  else
    monitor_status=$?
  fi
  network_monitor_pid=
  if [[ $monitor_status -ne 0 || ! -f $network_monitor_done ]]; then
    printf 'network mutation monitor failed to drain cleanly\n' >&2
    return 1
  fi
  if [[ -s $network_monitor_events ]]; then
    printf 'network route/address/link mutation invalidated H3 capture\n' >&2
    return 1
  fi
  network_monitor_events=
  network_monitor_ready=
  network_monitor_done=
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
  local workload_url="https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/observer?size=2097152&pass=$pass"
  if [[ $comparison_design == arms ]]; then
    workload_url="https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/camouflage/index.html?scenario=browser_page"
  fi
  local -a command_env=(env -u SSLKEYLOGFILE \
    "${firefox_runtime_env[@]}" \
    "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1)
  if [[ $pass == decrypted ]]; then
    : >"$keylog"
    chmod 0600 "$keylog"
    command_env=(env "SSLKEYLOGFILE=$keylog" \
      "${firefox_runtime_env[@]}" \
      "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1)
  fi
  : >"$log"
  chmod 0600 "$log"
  start_network_mutation_monitor "$pass-reference"
  start_capture "$pcap" "$capture_dir/$pass-reference-dumpcap.log"
  timeout 35 "${command_env[@]}" \
    "$REFERENCE_BIN" --headless --new-instance --no-remote \
    --profile "$reference_profile" --screenshot "$screenshot" \
    "$workload_url" \
    >"$log" 2>&1 &
  firefox_pid=$!
  set +e
  wait "$firefox_pid"
  local status=$?
  set -e
  firefox_pid=
  stop_capture
  stop_network_mutation_monitor
  if [[ $status -ne 0 ]]; then
    printf 'reference Firefox %s pass exited with status %s\n' \
      "$pass" "$status" >&2
    return 1
  fi
  if [[ ! -s $screenshot ]]; then
    printf 'reference Firefox %s pass produced no screenshot\n' "$pass" >&2
    return 1
  fi
}

run_naivefox() {
  local pass=$1
  local pcap="$capture_dir/$pass-naivefox.pcapng"
  local log="$capture_dir/$pass-naivefox.log"
  local keylog="$capture_dir/$pass-naivefox.keys"
  local profile="$capture_dir/$pass-naivefox-profile"
  local -a command_env=(env -u SSLKEYLOGFILE \
    "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR")
  if [[ $pass == decrypted ]]; then
    : >"$keylog"
    chmod 0600 "$keylog"
    command_env=(env "SSLKEYLOGFILE=$keylog" \
      "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR")
  fi
  : >"$log"
  chmod 0600 "$log"
  mkdir -m 0700 "$profile"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$profile/"
  local socks_port
  socks_port=$(python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  start_network_mutation_monitor "$pass-naivefox"
  start_capture "$pcap" "$capture_dir/$pass-naivefox-dumpcap.log"
  "${command_env[@]}" \
    NAIVEFOX_PROXY_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_PROXY_PASS="$NAIVEFOX_FIXTURE_PASS" \
    "$NAIVEFOX_BIN" \
    --profile "$profile" \
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
  stop_network_mutation_monitor
  [[ $(rg -c '^Outer protocol: h3$' "$log") -eq 2 ]]
  [[ $(rg -c '^Padding negotiated: yes$' "$log") -eq 2 ]]
  ! rg -q -e '^Outer protocol: h2$' -e '^Padding negotiated: no$' "$log"
}

run_naivefox_arm() {
  local arm=$1
  local pcap="$capture_dir/decrypted-$arm.pcapng"
  local log="$capture_dir/decrypted-$arm-naivefox.log"
  local keylog="$capture_dir/decrypted-$arm.keys"
  local naivefox_profile="$capture_dir/decrypted-$arm-naivefox-profile"
  local browser_profile="$capture_dir/decrypted-$arm-browser-profile"
  local browser_log="$capture_dir/decrypted-$arm-firefox.log"
  local screenshot="$capture_dir/decrypted-$arm.png"
  local config="$capture_dir/decrypted-$arm-config.json"
  local socks_port
  socks_port=$(python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  mkdir -m 0700 "$naivefox_profile" "$browser_profile"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$naivefox_profile/"
  cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$browser_profile/"
  cat >>"$naivefox_profile/user.js" <<EOF
user_pref("network.http.http3.enable", true);
user_pref("network.http.http3.disable_when_third_party_roots_found", false);
EOF
  if [[ $arm == root-pmtud-control ]]; then
    cat >>"$naivefox_profile/user.js" <<'EOF'
user_pref("network.http.http3.pmtud", true);
EOF
  fi
  if [[ $arm == root-pmtud-control ]]; then
    rg -q -F 'user_pref("network.http.http3.pmtud", true);' \
      "$naivefox_profile/user.js"
  else
    ! rg -q -F 'network.http.http3.pmtud' "$naivefox_profile/user.js"
  fi
  ! rg -q -F 'network.http.http3.pmtud' \
    "$reference_profile/user.js" "$browser_profile/user.js"
  chmod 0600 "$naivefox_profile/user.js"
  python3 "$INTEGRATION_DIR/camouflage_browser_controller.py" \
    --generate-pac-user-js "$socks_port" >>"$browser_profile/user.js"
  cat >>"$browser_profile/user.js" <<'EOF'
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
user_pref("network.prefetch-next", false);
user_pref("network.http.speculative-parallel-limit", 0);
user_pref("network.http.http3.enable", false);
EOF
  chmod 0600 "$browser_profile/user.js"
  NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER" \
    NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS" \
    python3 "$INTEGRATION_DIR/camouflage_naivefox_config.py" \
    --output "$config" --arm "$arm" --protocol h3 \
    --socks-port "$socks_port" --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT"
  : >"$keylog"
  : >"$log"
  : >"$browser_log"
  chmod 0600 "$keylog" "$log" "$browser_log"
  start_network_mutation_monitor "decrypted-$arm"
  start_capture "$pcap" "$capture_dir/decrypted-$arm-dumpcap.log"
  env -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
    "SSLKEYLOGFILE=$keylog" \
    "LD_LIBRARY_PATH=$NAIVEFOX_LIBDIR" NAIVEFOX_PROFILE="$naivefox_profile" \
    "$NAIVEFOX_BIN" "$config" >"$log" 2>&1 &
  naivefox_pid=$!
  wait_for_log "$naivefox_pid" "$log" '^SOCKS5 listening on '
  set +e
  timeout 35 env -u SSLKEYLOGFILE "${firefox_runtime_env[@]}" \
    "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
    "$REFERENCE_BIN" --headless --new-instance --no-remote \
    --profile "$browser_profile" --screenshot "$screenshot" \
    "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/camouflage/index.html?scenario=browser_page" \
    >"$browser_log" 2>&1 &
  firefox_pid=$!
  wait "$firefox_pid"
  local browser_status=$?
  firefox_pid=
  set -e
  if [[ $browser_status -ne 0 ]]; then
    printf 'same-base Firefox through %s arm exited with status %s\n' \
      "$arm" "$browser_status" >&2
    return 1
  fi
  if [[ ! -s $screenshot ]]; then
    printf 'same-base Firefox through %s arm produced no screenshot\n' \
      "$arm" >&2
    return 1
  fi
  if [[ $arm == tree-root-overlap || $arm == tree-root-overlap-css ]]; then
    local expected_resources=2
    [[ $arm == tree-root-overlap-css ]] && expected_resources=1
    wait_for_log "$naivefox_pid" "$log" \
      " preamble root-overlap drain=complete completed_resources=$expected_resources protocol=h3$"
  fi
  sleep 0.25
  stop_capture
  stop_network_mutation_monitor
  stop_pid "$naivefox_pid"
  naivefox_pid=
  local outer_count padding_count preamble_count
  outer_count=$(rg -c '^Outer protocol: h3$' "$log" || true)
  padding_count=$(rg -c '^Padding negotiated: yes$' "$log" || true)
  preamble_count=$(rg -c ' preamble result=' "$log" || true)
  [[ $outer_count -ge 1 && $padding_count -eq $outer_count ]]
  if [[ $arm == root || $arm == root-pmtud-control ||
        $arm == document-complete ||
        $arm == tree-complete || $arm == tree-complete-css ||
        $arm == tree-early-overlap ||
        $arm == tree-root-overlap ||
        $arm == tree-root-overlap-css ||
        $arm == tree-overlap ]]; then
    [[ $preamble_count -eq 1 ]]
    rg -q ' preamble result=success .*http=200 .*protocol=h3$' "$log"
    if [[ $arm == tree-early-overlap || $arm == tree-root-overlap ||
          $arm == tree-root-overlap-css ||
          $arm == tree-overlap ]]; then
      ! rg -q ' preamble background drain timed out' "$log"
    fi
    if [[ $arm == tree-root-overlap || $arm == tree-root-overlap-css ]]; then
      local expected_resources=2
      [[ $arm == tree-root-overlap-css ]] && expected_resources=1
      [[ $(rg -c ' preamble root-overlap admission=' "$log" || true) -eq 1 ]]
      [[ $(rg -c ' preamble root-overlap drain=' "$log" || true) -eq 1 ]]
      rg -q " preamble root-overlap admission=started-resources root_done=1 started_resources=$expected_resources protocol=h3$" "$log"
      rg -q " preamble root-overlap drain=complete completed_resources=$expected_resources protocol=h3$" "$log"
      local admission_connection result_connection drain_connection
      admission_connection=$(sed -nE 's/^(\[[^]]+\] )?Connection ([0-9]+) preamble root-overlap admission=.*/\2/p' "$log")
      result_connection=$(sed -nE 's/^(\[[^]]+\] )?Connection ([0-9]+) preamble result=.*/\2/p' "$log")
      drain_connection=$(sed -nE 's/^(\[[^]]+\] )?Connection ([0-9]+) preamble root-overlap drain=.*/\2/p' "$log")
      [[ $admission_connection == "$result_connection" &&
         $admission_connection == "$drain_connection" ]]
      [[ $(rg -c "Connection $admission_connection established target=.* outer=h3 padding=yes$" "$log" || true) -eq 1 ]]
      local admission_line result_line drain_line established_line
      admission_line=$(rg -n -m1 ' preamble root-overlap admission=' "$log" | cut -d: -f1)
      result_line=$(rg -n -m1 ' preamble result=' "$log" | cut -d: -f1)
      drain_line=$(rg -n -m1 ' preamble root-overlap drain=' "$log" | cut -d: -f1)
      established_line=$(rg -n -m1 "Connection $admission_connection established target=.* outer=h3 padding=yes$" "$log" | cut -d: -f1)
      [[ $admission_line -lt $result_line && $result_line -lt $drain_line &&
         $result_line -lt $established_line ]]
    else
      ! rg -q -e ' preamble root-overlap admission=' \
        -e ' preamble root-overlap drain=' "$log"
    fi
  else
    [[ $preamble_count -eq 0 ]]
    ! rg -q -e ' preamble root-overlap admission=' \
      -e ' preamble root-overlap drain=' "$log"
  fi
  ! rg -q -e '^Outer protocol: h2$' -e '^Padding negotiated: no$' "$log"
}

if [[ $comparison_design == arms ]]; then
  run_reference decrypted
  for arm in "${comparison_arms[@]}"; do
    run_naivefox_arm "$arm"
  done
  capture_passes=(decrypted)
  capture_sides=(reference "${comparison_arms[@]}")
else
  run_reference decrypted
  run_naivefox decrypted
  run_reference passive
  run_naivefox passive
  capture_passes=(decrypted passive)
  capture_sides=(reference naivefox)
fi

for pass in "${capture_passes[@]}"; do
  for side in "${capture_sides[@]}"; do
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
    oversized_udp_frame=$(tshark -r "$pcap" \
      -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && udp.length>1500" \
      -T fields -e frame.number | sed -n '1p')
    if [[ $udp_count -eq 0 || $tcp_established -ne 0 || $tcp_payload -ne 0 ]]; then
      printf '%s/%s is not strict QUIC (udp=%s tcp-established=%s tcp-payload=%s)\n' \
        "$pass" "$side" "$udp_count" "$tcp_established" "$tcp_payload" >&2
      exit 1
    fi
    if [[ -n $oversized_udp_frame ]]; then
      printf '%s/%s contains a UDP offload superframe at frame %s\n' \
        "$pass" "$side" "$oversized_udp_frame" >&2
      exit 1
    fi
  done
done

for side in "${capture_sides[@]}"; do
  keys="$capture_dir/decrypted-$side.keys"
  [[ -s $keys ]]
  rg -q '^(CLIENT|SERVER)_(HANDSHAKE_)?TRAFFIC_SECRET' "$keys"
done
if [[ $comparison_design == legacy ]] && \
   find "$capture_dir" -maxdepth 1 -name 'passive-*.keys' -print -quit |
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
    -Y "udp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==2" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e tls.handshake.version \
    -e tls.handshake.extensions.supported_version \
    -e tls.handshake.ciphersuite \
    -e tls.handshake.extensions_key_share_selected_group \
    >"$prefix-serverhello.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.extensions_alpn_str" \
    "${TSHARK_FIELDS[@]}" -e quic.connection.number \
    -e tls.handshake.extensions_alpn_str >"$prefix-alpn.csv"

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
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e udp.srcport -e udp.dstport \
    -e quic.connection.number -e quic.stream.stream_id \
    -e http3.headers.method -e http3.headers.status >"$prefix-requests.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "http3.header.header.name" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e udp.srcport -e udp.dstport \
    -e quic.connection.number -e quic.stream.stream_id \
    -e http3.header.header.name >"$prefix-header-names.csv"

  # Private-only GET semantics for the complete/overlap causal invariant.
  # A unit separator avoids ambiguity with semicolons inside header values.
  local semantic_aggregator=$'\x1f'
  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http3.headers.method==\"GET\" && http3.header.header.name && !(http3.header.header.name contains \"authorization\")" \
    -T fields -E header=y -E separator=, -E quote=d \
    -E occurrence=a -E "aggregator=$semantic_aggregator" \
    -e frame.number -e udp.srcport -e udp.dstport \
    -e quic.connection.number -e quic.stream.stream_id \
    -e http3.headers.method -e http3.header.header.name \
    -e http3.headers.header.value >"$prefix-get-header-values.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && http3.headers.status==200 && http3.header.header.name" \
    -T fields -E header=y -E separator=, -E quote=d \
    -E occurrence=a -E "aggregator=$semantic_aggregator" \
    -e frame.number -e udp.srcport -e udp.dstport \
    -e quic.connection.number -e quic.stream.stream_id \
    -e http3.headers.status -e http3.header.header.name \
    -e http3.headers.header.value >"$prefix-response-header-values.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && quic" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e udp.srcport -e udp.dstport -e udp.length \
    -e quic.connection.number -e quic.version \
    -e quic.long.packet_type -e quic.dcil -e quic.scil \
    -e quic.packet_number -e quic.packet_length >"$prefix-packets.csv"

  tshark -r "$pcap" "${decode[@]}" \
    -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && (quic.rsts.stream_id || quic.ss.stream_id || quic.stream.fin==1 || quic.cc.error_code || quic.cc.error_code.app)" \
    "${TSHARK_FIELDS[@]}" -e frame.number -e frame.time_relative \
    -e udp.srcport -e udp.dstport -e quic.connection.number \
    -e quic.frame_type -e quic.rsts.stream_id \
    -e quic.rsts.application_error_code -e quic.rsts.final_size \
    -e quic.ss.stream_id -e quic.ss.application_error_code \
    -e quic.stream.stream_id -e quic.stream.fin \
    -e quic.cc.error_code -e quic.cc.error_code.app \
    >"$prefix-lifecycle.csv"
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

for side in "${capture_sides[@]}"; do
  extract_decrypted "$side"
  if [[ $comparison_design == legacy ]]; then
    extract_passive "$side"
  fi
done

safe_dir="$STATE_ROOT/h3-capture-safe/$capture_id"
mkdir -m 0700 -p "$safe_dir"

if [[ $comparison_design == arms ]]; then
  python3 "$INTEGRATION_DIR/h3_decrypted_arm_summary.py" \
    --input-dir "$capture_dir" \
    --events "$safe_dir/outer-h3-events.csv" \
    --summary "$safe_dir/summary.txt" \
    --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
    --arms "$(IFS=,; printf '%s' "${comparison_arms[*]}")"
  {
    printf 'isolated_network=yes\n'
    printf 'network_mutation_monitor=netlink_route_v1_fail_closed\n'
    printf 'capture_offload_policy=namespace_loopback_offload_disabled_and_verified\n'
    printf 'h3_udp_superframe_policy=reject_udp_length_gt_1500\n'
    printf 'capture_revision=%s\n' "$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
    printf 'capture_worktree_dirty=%s\n' "$(source_worktree_dirty)"
    printf 'capture_source_state_sha256=%s\n' "$(source_state_sha256)"
    printf 'tshark_version=%s\n' "$(tshark --version | head -n 1)"
    printf 'reference_binary=%s\n' \
      "$(readelf -n "$REFERENCE_BIN" | sed -n 's/^ *Build ID: //p' | head -n 1)"
    printf 'naivefox_binary=%s\n' \
      "$(readelf -n "$NAIVEFOX_BIN" | sed -n 's/^ *Build ID: //p' | head -n 1)"
    printf 'reference_libxul_sha256=%s\n' \
      "$(sha256sum "$REFERENCE_LIBDIR/libxul.so" | cut -d' ' -f1)"
    printf 'naivefox_libxul_sha256=%s\n' \
      "$(sha256sum "$NAIVEFOX_LIBDIR/libxul.so" | cut -d' ' -f1)"
  } >>"$safe_dir/summary.txt"
  find "$safe_dir" -type f -exec chmod 0600 {} +
  if find "$safe_dir" -type f \( -name '*.pcap' -o -name '*.pcapng' -o \
       -name '*.keys' -o -name '*.log' \) -print -quit | grep -q .; then
    printf 'private capture material reached safe H3 arm output\n' >&2
    exit 1
  fi
  fixture_user_encoded=$(python3 -c \
    'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' \
    "$NAIVEFOX_FIXTURE_USER")
  fixture_pass_encoded=$(python3 -c \
    'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' \
    "$NAIVEFOX_FIXTURE_PASS")
  if rg -F "$NAIVEFOX_FIXTURE_USER" "$safe_dir" ||
     rg -F "$NAIVEFOX_FIXTURE_PASS" "$safe_dir" ||
     rg -F "$fixture_user_encoded" "$safe_dir" ||
     rg -F "$fixture_pass_encoded" "$safe_dir" ||
     rg -i -e proxy-authorization -e authorization: -e 'localhost:' \
       -e sslkeylogfile -e client_random -e traffic_secret \
       -e exporter_secret "$safe_dir"; then
    printf 'credential-bearing data reached safe H3 arm output\n' >&2
    exit 1
  fi
  success=1
  printf 'Firefox/NaiveFox same-base H3 arm comparison passed\n'
  printf 'sanitized aggregates: %s\n' "$safe_dir"
  exit 0
fi

python3 - "$capture_dir" "$safe_dir/summary.txt" \
  "$NAIVEFOX_FIXTURE_PROXY_PORT" "$capture_mode" <<'PY'
import csv
import hashlib
import math
import os
import sys

root, destination, proxy_port, reference_mode = sys.argv[1:]


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


def grease(value):
    try:
        parsed = int(value, 0)
    except ValueError:
        return value
    return "GREASE" if parsed <= 0xffff and parsed & 0x0f0f == 0x0a0a else value


def config_equal(
    suffix,
    ignored=("quic.connection.number",),
    unordered=(),
    grease_values=False,
):
    unordered = set(unordered)
    def normalized(name):
        result = []
        for row in rows(name):
            values = []
            for key, value in row.items():
                if key in ignored:
                    continue
                pieces = [
                    grease(item) if grease_values else item
                    for item in value.split(";")
                    if item
                ]
                if key in unordered:
                    pieces.sort()
                value = ";".join(pieces)
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
    "clienthello", unordered=(
        "tls.handshake.extension.type",
        "tls.handshake.ciphersuite",
        "tls.handshake.extensions_supported_group",
        "tls.handshake.sig_hash_alg",
        "tls.handshake.extensions_key_share_group",
    ), grease_values=True
)
serverhello_equal = config_equal("serverhello")
alpn_equal = config_equal("alpn")
transport_equal = config_equal("transport")
settings_equal = config_equal(
    "settings", ("quic.connection.number", "quic.stream.stream_id")
)
if reference_mode == "same-base":
    require(hello_equal, "same-base H3 semantic ClientHello differs")
    require(serverhello_equal, "same-base H3 server negotiation differs")
    require(alpn_equal, "same-base H3 selected ALPN differs")
    require(transport_equal, "same-base H3 transport parameters differ")
    require(settings_equal, "same-base H3/QPACK SETTINGS differ")
# The reference is an independently downloaded current Firefox, while
# NaiveFox is intentionally pinned to its validated Firefox snapshot.  A
# version update may legitimately change ClientHello, transport parameters, or
# SETTINGS.  Record those comparisons instead of treating expected drift as a
# failure; the strict gates below still require real H3, CONNECT streams,
# padding, no synthetic markers, and UDP-only transport.


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
        values = []
        for key, value in row.items():
            if key == "quic.connection.number":
                continue
            pieces = [grease(item) for item in value.split(";") if item]
            if key in {
                "tls.handshake.extension.type",
                "tls.handshake.ciphersuite",
                "tls.handshake.extensions_supported_group",
                "tls.handshake.sig_hash_alg",
                "tls.handshake.extensions_key_share_group",
            }:
                pieces.sort()
            values.append((key, ";".join(pieces)))
        result.append(tuple(values))
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

fingerprint_source = repr(
    unique_rows("decrypted-reference-clienthello.csv", ("quic.connection.number",))
).encode()
fingerprint = hashlib.sha256(fingerprint_source).hexdigest()

def lifecycle_summary(side):
    data = rows(f"decrypted-{side}-lifecycle.csv")
    return {
        "reset_stream": sum(bool(row["quic.rsts.stream_id"]) for row in data),
        "stop_sending": sum(bool(row["quic.ss.stream_id"]) for row in data),
        "stream_fin": sum(
            any(value in {"1", "true", "True"}
                for value in row["quic.stream.fin"].split(";"))
            for row in data
        ),
        "connection_close": sum(
            bool(row["quic.cc.error_code"] or row["quic.cc.error_code.app"])
            for row in data
        ),
    }

lifecycle = {side: lifecycle_summary(side) for side in ("reference", "naivefox")}

with open(destination, "w", encoding="utf-8") as output:
    output.write("capture_scope=local_strict_h3_firefox_vs_naivefox\n")
    output.write(f"reference_mode={reference_mode}\n")
    output.write("capture_interface=any_sll_transmit_copy\n")
    output.write("strict_udp_quic_only=yes\n")
    output.write("tcp_sessions_established=0\n")
    output.write("tcp_payload_bytes=0\n")
    output.write("decrypted_selected_protocol=h3\n")
    output.write("decrypted_reference_method=GET\n")
    output.write("decrypted_naivefox_method=CONNECT\n")
    output.write(f"quic_versions={packet_summaries[('decrypted', 'reference')]['versions']}\n")
    output.write(f"tls_clienthello_semantic_config_equal={'yes' if hello_equal else 'no'}\n")
    output.write(f"tls_server_negotiation_equal={'yes' if serverhello_equal else 'no'}\n")
    output.write(f"selected_alpn_equal={'yes' if alpn_equal else 'no'}\n")
    output.write("tls_extension_order_expected_randomized=yes\n")
    output.write(f"client_transport_parameters_equal={'yes' if transport_equal else 'no'}\n")
    output.write(f"h3_settings_equal={'yes' if settings_equal else 'no'}\n")
    output.write("qpack_settings_compared=max_table_capacity,blocked_streams\n")
    output.write("h3_lifecycle_frames_recorded=RESET_STREAM,STOP_SENDING,STREAM_FIN,CONNECTION_CLOSE\n")
    output.write(f"clienthello_canonical_sha256={fingerprint}\n")
    output.write("synthetic_marker_names=none\n")
    output.write("padding_request_header_name=present\n")
    output.write("padding_response_header_name=present\n")
    output.write(f"naivefox_quic_connections={len(connect_connections)}\n")
    output.write(f"naivefox_connect_streams={len(connect_streams)}\n")
    output.write("passive_tls_keylog=disabled\n")
    output.write(f"passive_clienthello_visible_fields_equal={'yes' if passive_hello_equal else 'no'}\n")
    output.write(f"passive_client_transport_parameters_equal={'yes' if passive_transport_equal else 'no'}\n")
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
    for side, values in lifecycle.items():
        for key, value in values.items():
            output.write(f"decrypted_{side}_lifecycle_{key}={value}\n")
    output.write("raw_capture_material=deleted_after_success\n")
PY
chmod 0600 "$safe_dir/summary.txt"

{
  printf 'capture_revision=%s\n' "$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
  printf 'capture_worktree_dirty=%s\n' "$(source_worktree_dirty)"
  printf 'capture_source_state_sha256=%s\n' "$(source_state_sha256)"
  printf 'tshark_version=%s\n' "$(tshark --version | head -n 1)"
  printf 'reference_binary=%s\n' \
    "$(readelf -n "$REFERENCE_BIN" | sed -n 's/^ *Build ID: //p' | head -n 1)"
  printf 'naivefox_binary=%s\n' \
    "$(readelf -n "$NAIVEFOX_BIN" | sed -n 's/^ *Build ID: //p' | head -n 1)"
  printf 'reference_libxul_sha256=%s\n' \
    "$(sha256sum "$REFERENCE_LIBDIR/libxul.so" | cut -d' ' -f1)"
  printf 'reference_libssl3_sha256=%s\n' \
    "$(sha256sum "$REFERENCE_LIBDIR/libssl3.so" | cut -d' ' -f1)"
  printf 'naivefox_libxul_sha256=%s\n' \
    "$(sha256sum "$NAIVEFOX_LIBDIR/libxul.so" | cut -d' ' -f1)"
  printf 'naivefox_libssl3_sha256=%s\n' \
    "$(sha256sum "$NAIVEFOX_LIBDIR/libssl3.so" | cut -d' ' -f1)"
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
