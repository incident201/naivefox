#!/usr/bin/env bash

set -euo pipefail
umask 077

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
original_args=("$@")

if [[ $# -ne 0 ]]; then
  printf 'usage: %s\n' "$0" >&2
  exit 2
fi
if [[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0} != 1 ]]; then
  for tool in unshare ip ethtool; do
    command -v "$tool" >/dev/null 2>&1 || {
      printf 'repeat-navigation isolation requires %s\n' "$tool" >&2
      exit 1
    }
  done
  export NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1
  exec unshare --net --mount-proc \
    "$INTEGRATION_DIR/run-camouflage-isolated-network.sh" \
    "$0" "${original_args[@]}"
fi
if [[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0} != 1 ]]; then
  printf 'isolated repeat-navigation marker is inconsistent\n' >&2
  exit 2
fi

for tool in dumpcap tshark getcap openssl python3 rg sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'required repeat-navigation tool not found: %s\n' "$tool" >&2
    exit 1
  }
done
if [[ ${NAIVEFOX_CAPTURE_MODE:-} != same-base ||
      -z ${NAIVEFOX_CAPTURE_REFERENCE_BIN:-} ||
      -z ${NAIVEFOX_CAPTURE_REFERENCE_OBJDIR:-} ]]; then
  printf 'repeat navigation requires same-base reference binary and objdir\n' >&2
  exit 2
fi

REFERENCE_BIN=$NAIVEFOX_CAPTURE_REFERENCE_BIN
REFERENCE_LIBDIR=${NAIVEFOX_CAPTURE_REFERENCE_LIBDIR:-$(dirname "$REFERENCE_BIN")}
REFERENCE_OBJDIR=$NAIVEFOX_CAPTURE_REFERENCE_OBJDIR
for required in "$REFERENCE_BIN" "$REFERENCE_LIBDIR/libssl3.so" \
  "$REFERENCE_LIBDIR/libxul.so"; do
  [[ -f $required ]] || {
    printf 'required same-base artifact is missing: %s\n' "$required" >&2
    exit 1
  }
done
if ! rg -q -- '-DNSS_ALLOW_SSLKEYLOGFILE' \
    "$REFERENCE_OBJDIR/security/nss/lib/ssl/ssl_ssl/backend.mk"; then
  printf 'same-base NSS build does not enable SSLKEYLOGFILE\n' >&2
  exit 1
fi

browser_python=${NAIVEFOX_CAMOUFLAGE_PYTHON:-}
if [[ -z $browser_python && -x "$OBJDIR/camouflage-venv/bin/python" ]]; then
  browser_python="$OBJDIR/camouflage-venv/bin/python"
fi
browser_python=${browser_python:-$(command -v python3)}
"$browser_python" -c 'import selenium' || {
  printf 'repeat navigation requires Selenium\n' >&2
  exit 1
}

capture_pid=
controller_pid=
monitor_pid=
capture_stage_dir=
capture_dir=
safe_dir=
success=0

stop_process_group() {
  local pid=${1:-}
  [[ -n $pid ]] || return 0
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [[ $pgid == "$pid" ]]; then
    kill -TERM -- "-$pid" 2>/dev/null || true
    timeout 5 tail --pid="$pid" -f /dev/null 2>/dev/null || true
    kill -KILL -- "-$pid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
}

stop_capture() {
  [[ -n $capture_pid ]] || return 0
  local was_running=0
  if kill -0 "$capture_pid" 2>/dev/null; then
    was_running=1
    kill -INT "$capture_pid" 2>/dev/null || true
  fi
  wait "$capture_pid" 2>/dev/null || true
  capture_pid=
  [[ $was_running -eq 1 ]] || {
    printf 'dumpcap stopped before repeat navigation completed\n' >&2
    return 1
  }
  python3 "$INTEGRATION_DIR/camouflage_capture_health.py" "$capture_dir/dumpcap.log"
  if [[ -n $(tshark -r "$capture_stage_dir/repeat.raw.pcapng" \
      -Y 'sll.pkttype==4' -T fields -e frame.number 2>/dev/null | sed -n '1p') ]]; then
    tshark -r "$capture_stage_dir/repeat.raw.pcapng" -Y 'sll.pkttype==4' \
      -w "$capture_dir/repeat.pcapng" >/dev/null 2>&1
  else
    mv -f -- "$capture_stage_dir/repeat.raw.pcapng" \
      "$capture_dir/repeat.pcapng"
  fi
}

stop_monitor() {
  [[ -n $monitor_pid ]] || return 0
  kill -0 "$monitor_pid" 2>/dev/null || {
    wait "$monitor_pid" 2>/dev/null || true
    monitor_pid=
    printf 'network mutation monitor stopped before repeat navigation\n' >&2
    return 1
  }
  kill -TERM "$monitor_pid" 2>/dev/null || true
  wait "$monitor_pid"
  monitor_pid=
  [[ -f $capture_dir/network-monitor-done ]] || {
    printf 'network mutation monitor did not drain\n' >&2
    return 1
  }
  [[ ! -s $capture_dir/network-mutations.log ]] || {
    printf 'network mutation invalidated repeat navigation\n' >&2
    return 1
  }
}

cleanup() {
  local status=$?
  stop_capture || status=1
  stop_monitor || status=1
  stop_process_group "$controller_pid"
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  [[ -z $capture_stage_dir ]] || rm -rf -- "$capture_stage_dir"
  if [[ -n $safe_dir && ($status -ne 0 || $success -ne 1) ]]; then
    case $safe_dir in
      "$STATE_ROOT"/h3-capture-safe/*) rm -rf -- "$safe_dir" ;;
      *) printf 'refusing to remove unexpected safe path: %s\n' "$safe_dir" >&2 ;;
    esac
  fi
  if [[ -n $capture_dir ]]; then
    case $capture_dir in
      "$STATE_ROOT"/h3-captures/*)
        if [[ $status -eq 0 && $success -eq 1 ]]; then
          printf 'private repeat-navigation evidence retained at %s\n' \
            "$capture_dir" >&2
        else
          printf 'repeat-navigation diagnostics preserved at %s\n' \
            "$capture_dir" >&2
        fi
        ;;
    esac
  fi
  return "$status"
}
trap cleanup EXIT
trap 'status=$?; printf "repeat navigation failed at line %s (status=%s): %s\n" \
  "$LINENO" "$status" "$BASH_COMMAND" >&2; exit "$status"' ERR
trap 'exit 130' INT
trap 'exit 143' TERM

"$INTEGRATION_DIR/start.sh" --mode h3
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
[[ $NAIVEFOX_FIXTURE_MODE == h3 ]]

capture_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
capture_dir="$STATE_ROOT/h3-captures/$capture_id"
safe_dir="$STATE_ROOT/h3-capture-safe/$capture_id"
mkdir -p "$capture_dir" "$safe_dir"
chmod 0700 "$capture_dir" "$safe_dir"
capture_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-repeat.XXXXXX")
chmod 0700 "$capture_stage_dir"

profile="$capture_dir/reference-profile"
mkdir -m 0700 "$profile"
cp -aL -- "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." "$profile/"
cat >"$profile/user.js" <<EOF
user_pref("app.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.safebrowsing.realTime.enabled", false);
user_pref("browser.safebrowsing.globalCache.enabled", false);
user_pref("browser.safebrowsing.provider.google5.enabled", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("network.dns.disableIPv6", true);
user_pref("network.prefetch-next", false);
user_pref("network.http.speculative-parallel-limit", 0);
user_pref("network.http.http3.enable", true);
user_pref("network.http.http3.disable_when_third_party_roots_found", false);
user_pref("network.http.http3.alt-svc-mapping-for-testing", "localhost;h3=:$NAIVEFOX_FIXTURE_PROXY_PORT");
user_pref("network.http.http3.force-use-alt-svc-mapping-for-testing", true);
EOF
chmod 0600 "$profile/user.js"
[[ ! -e $profile/cache2 ]] || {
  printf 'repeat-navigation profile is not initially cache-cold\n' >&2
  exit 1
}

warm_completion=$(openssl rand -hex 16)
completion1=$(openssl rand -hex 16)
completion2=$(openssl rand -hex 16)
nav1=$(openssl rand -hex 16)
nav2=$(openssl rand -hex 16)
[[ $nav1 != "$nav2" && $completion1 != "$completion2" ]]
for token in "$warm_completion" "$completion1" "$completion2"; do
  rm -f -- "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$token"
done

ready_file="$capture_dir/browser-ready.json"
navigate_file="$capture_dir/browser-navigate"
done_file="$capture_dir/browser-done"
stop_file="$capture_dir/browser-stop"
identity_file="$capture_dir/navigation-identity.json"
keylog="$capture_dir/repeat.keys"
: >"$keylog"
chmod 0600 "$keylog"

runtime_env=()
if [[ $EUID -eq 0 ]]; then
  runtime_dir="$capture_stage_dir/firefox-runtime"
  mkdir -m 0700 "$runtime_dir"
  runtime_env=("XDG_RUNTIME_DIR=$runtime_dir")
fi
url1="https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/camouflage/index.html?scenario=browser_page&size=262144&count=4&idle_ms=5000&completion=$completion1&nav=$nav1"
url2="https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/camouflage/index.html?scenario=browser_page&size=262144&count=4&idle_ms=5000&completion=$completion2&nav=$nav2"
warm_url="https://127.0.0.1:$NAIVEFOX_FIXTURE_HTTPS_PORT/camouflage/index.html?scenario=initial&completion=$warm_completion"

setsid env "SSLKEYLOGFILE=$keylog" \
  "MOZ_LOG=timestamp,nsHttp:5,nsCSSLoader:5" \
  "MOZ_LOG_FILE=$capture_dir/repeat-lifecycle" "${runtime_env[@]}" \
  "LD_LIBRARY_PATH=$REFERENCE_LIBDIR" MOZ_HEADLESS=1 \
  "$browser_python" "$INTEGRATION_DIR/camouflage_browser_controller.py" \
  --binary "$REFERENCE_BIN" --profile "$profile" --backend selenium \
  --protocol h3 --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --url "$url1" \
  --completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion1" \
  --second-url "$url2" \
  --second-completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion2" \
  --navigation-evidence-file "$identity_file" \
  --warmup-url "$warm_url" \
  --warmup-completion-file "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$warm_completion" \
  --ready-file "$ready_file" --navigate-file "$navigate_file" \
  --done-file "$done_file" --stop-file "$stop_file" \
  --browser-log "$capture_dir/firefox.log" \
  --webdriver-log "$capture_dir/webdriver.log" --timeout 35 \
  >"$capture_dir/controller.log" 2>&1 &
controller_pid=$!
wait_for_file "$ready_file" "$controller_pid" "pre-launched Firefox" 300

python3 "$INTEGRATION_DIR/monitor-network-mutations.py" \
  --ready "$capture_dir/network-monitor-ready" \
  --events "$capture_dir/network-mutations.log" \
  --done "$capture_dir/network-monitor-done" &
monitor_pid=$!
wait_for_file "$capture_dir/network-monitor-ready" "$monitor_pid" \
  "network mutation monitor"

: >"$capture_dir/dumpcap.log"
chmod 0600 "$capture_dir/dumpcap.log"
dumpcap -q -i any \
  -f "udp port $NAIVEFOX_FIXTURE_PROXY_PORT" \
  -a duration:60 -a filesize:65536 \
  -w "$capture_stage_dir/repeat.raw.pcapng" \
  >"$capture_dir/dumpcap.log" 2>&1 &
capture_pid=$!
for ((i = 0; i < 100; i++)); do
  kill -0 "$capture_pid" 2>/dev/null || {
    printf 'dumpcap exited before repeat capture readiness\n' >&2
    exit 1
  }
  [[ -s $capture_stage_dir/repeat.raw.pcapng ]] && break
  sleep 0.1
done
[[ -s $capture_stage_dir/repeat.raw.pcapng ]] || {
  printf 'timed out waiting for repeat capture file\n' >&2
  exit 1
}

: >"$navigate_file"
wait_for_file "$done_file" "$controller_pid" "two Firefox navigations" 500
sleep 0.25
stop_capture
stop_monitor
: >"$stop_file"
controlled_shutdown=0
if ! timeout 20 tail --pid="$controller_pid" -f /dev/null; then
  controlled_shutdown=1
  kill -TERM -- "-$controller_pid" 2>/dev/null || true
  timeout 5 tail --pid="$controller_pid" -f /dev/null || {
    printf 'controlled Firefox required SIGKILL\n' >&2
    exit 1
  }
fi
if [[ $controlled_shutdown -eq 1 ]]; then
  wait "$controller_pid" 2>/dev/null || true
elif ! wait "$controller_pid"; then
  printf 'repeat-navigation browser controller failed\n' >&2
  exit 1
fi
controller_pid=

decode=(-d "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT,quic" \
  -o "tls.keylog_file:$keylog")
fields=(-T fields -E header=y -E "separator=," -E quote=d \
  -E occurrence=a '-E' 'aggregator=;')
tshark -r "$capture_dir/repeat.pcapng" "${decode[@]}" \
  -Y "http3.headers.method || http3.headers.status" \
  "${fields[@]}" -e frame.number -e frame.time_relative \
  -e frame.time_epoch \
  -e udp.srcport -e udp.dstport -e quic.connection.number \
  -e quic.stream.stream_id -e http3.headers.method \
  -e http3.headers.status >"$capture_dir/repeat-requests.csv"
tshark -r "$capture_dir/repeat.pcapng" "${decode[@]}" \
  -Y "http3.header.header.name" \
  "${fields[@]}" -e frame.number -e udp.srcport -e udp.dstport \
  -e quic.connection.number -e quic.stream.stream_id \
  -e http3.header.header.name >"$capture_dir/repeat-header-names.csv"
semantic_separator='~'
tshark -r "$capture_dir/repeat.pcapng" "${decode[@]}" \
  -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && http3.headers.method==\"GET\" && http3.header.header.name && !(http3.header.header.name contains \"authorization\") && !(http3.header.header.name contains \"cookie\")" \
  -T fields -E header=y -E separator=/t -E quote=n -E occurrence=a \
  -E "aggregator=$semantic_separator" \
  -e frame.number -e udp.srcport -e udp.dstport \
  -e quic.connection.number -e quic.stream.stream_id \
  -e http3.headers.method -e http3.header.header.name \
  -e http3.headers.header.value >"$capture_dir/repeat-get-header-values.csv"
tshark -r "$capture_dir/repeat.pcapng" "${decode[@]}" \
  -Y "udp.srcport==$NAIVEFOX_FIXTURE_PROXY_PORT && http3.headers.status && http3.header.header.name" \
  -T fields -E header=y -E separator=/t -E quote=n -E occurrence=a \
  -E "aggregator=$semantic_separator" \
  -e frame.number -e udp.srcport -e udp.dstport \
  -e quic.connection.number -e quic.stream.stream_id \
  -e http3.headers.status -e http3.header.header.name \
  -e http3.headers.header.value \
  >"$capture_dir/repeat-response-header-values.csv"
tshark -r "$capture_dir/repeat.pcapng" "${decode[@]}" \
  -Y "udp.dstport==$NAIVEFOX_FIXTURE_PROXY_PORT && tls.handshake.type==1" \
  "${fields[@]}" -e quic.connection.number \
  -e tls.handshake.length >"$capture_dir/repeat-clienthello.csv"

python3 "$INTEGRATION_DIR/firefox_repeat_navigation_summary.py" \
  --root "$capture_dir" --proxy-port "$NAIVEFOX_FIXTURE_PROXY_PORT" \
  --navigation-evidence "$identity_file" \
  --nav1 "$nav1" --nav2 "$nav2" \
  --completion1 "$completion1" --completion2 "$completion2" \
  --output "$safe_dir/summary.txt"

oversized=$(tshark -r "$capture_dir/repeat.pcapng" \
  -Y "udp.port==$NAIVEFOX_FIXTURE_PROXY_PORT && frame.len>1500" \
  -T fields -e frame.number | sed -n '1p')
[[ -z $oversized ]] || {
  printf 'repeat navigation contains oversized outer UDP frame\n' >&2
  exit 1
}
cat >"$safe_dir/metadata.txt" <<EOF
capture_mode=same-base
protocol=h3
design=reference_repeat_navigation
same_firefox_process=required
same_content_process=required
resource_cache_contract=unique_navigation_token_and_unconditional_200
source_commit=$(git -C "$SOURCE_ROOT" rev-parse HEAD)
reference_binary_sha256=$(sha256sum "$REFERENCE_BIN" | cut -d' ' -f1)
EOF

success=1
printf 'repeat-navigation diagnostic passed: %s\n' "$safe_dir"
cat "$safe_dir/summary.txt"
