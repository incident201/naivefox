#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

usage() {
  printf 'usage: %s [--package DIR] [--protocol h2|h3|all] [--check-only] [--allow-skip-device] [--start-emulator]\n' "$0"
}

package_arg=${NAIVEFOX_ANDROID_PACKAGE:-}
protocol=all
check_only=0
allow_skip_device=0
start_emulator=0
while (( $# )); do
  case $1 in
    --package)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      package_arg=$2
      shift 2
      ;;
    --protocol)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      protocol=$2
      shift 2
      ;;
    --check-only)
      check_only=1
      shift
      ;;
    --allow-skip-device)
      allow_skip_device=1
      shift
      ;;
    --start-emulator)
      start_emulator=1
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done
case $protocol in
  h2 | h3 | all) ;;
  *) printf 'unsupported protocol: %s\n' "$protocol" >&2; exit 2 ;;
esac

init_paths
if [[ -z $package_arg ]]; then
  package_arg="$OBJDIR/package/naivefox-android-aarch64"
fi
package_dir=$(realpath -m -- "$package_arg")

find_ndk() {
  local candidates=()
  [[ -z ${NAIVEFOX_ANDROID_NDK:-} ]] || candidates+=("$NAIVEFOX_ANDROID_NDK")
  [[ -z ${ANDROID_NDK_ROOT:-} ]] || candidates+=("$ANDROID_NDK_ROOT")
  candidates+=(
    "$HOME/.mozbuild/android-ndk-r29"
    "/root/.mozbuild/android-ndk-r29"
    "/home/zubastik/.mozbuild/android-ndk-r29"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++" ]]; then
      NDK_ROOT=$(realpath -e -- "$candidate")
      return
    fi
  done
  printf 'Android NDK r29 not found; set NAIVEFOX_ANDROID_NDK\n' >&2
  exit 1
}

find_ndk
if [[ ! -f $NDK_ROOT/source.properties ]] ||
  ! rg -q '^Pkg\.Revision = 29\.' "$NDK_ROOT/source.properties"; then
  printf 'NaiveFox Android tests require NDK r29: %s\n' "$NDK_ROOT" >&2
  exit 1
fi

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/naivefox-android-test.XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT
harness="$work_dir/naivefox-android-embedded-harness"
header_dir="$SOURCE_ROOT/netwerk/naivefox"
[[ ! -f $package_dir/include/NaiveFoxAPI.h ]] || header_dir="$package_dir/include"
compiler="$NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++"
"$compiler" -std=c++17 -O2 -fPIE -pie -static-libstdc++ -pthread \
  -I"$header_dir" "$INTEGRATION_DIR/android_embedded_harness.cpp" \
  -ldl -o "$harness"
readelf="$NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
"$readelf" --file-header "$harness" |
  rg '^  Machine:[[:space:]]+AArch64$' >/dev/null

if (( check_only )); then
  printf 'Android embedded harness static checks passed with NDK r29\n'
  exit 0
fi

[[ -d $package_dir ]] || {
  printf 'staged Android package not found: %s\n' "$package_dir" >&2
  exit 1
}
"$SOURCE_ROOT/netwerk/naivefox/tools/verify-staged-android-runtime.sh" \
  "$package_dir"

adb_bin=${NAIVEFOX_ADB:-}
if [[ -z $adb_bin ]]; then
  adb_bin=$(command -v adb || true)
fi
skip_device() {
  local reason=$1
  if (( allow_skip_device )); then
    printf 'SKIP: %s\n' "$reason"
    exit 0
  fi
  printf '%s; pass --allow-skip-device only for a non-acceptance static run\n' \
    "$reason" >&2
  exit 1
}
[[ -n $adb_bin && -x $adb_bin ]] || skip_device 'adb is unavailable'
emulator_started=0
emulator_stop_adb=$adb_bin
emulator_stop_serial=${NAIVEFOX_ANDROID_SERIAL:-emulator-5554}
if [[ -n ${NAIVEFOX_ANDROID_STOP_ADB:-} ]]; then
  emulator_stop_adb=$NAIVEFOX_ANDROID_STOP_ADB
elif [[ -n ${NAIVEFOX_WINDOWS_USER:-} &&
        -x /mnt/c/Users/$NAIVEFOX_WINDOWS_USER/AppData/Local/Android/Sdk/platform-tools/adb.exe ]]; then
  emulator_stop_adb=/mnt/c/Users/$NAIVEFOX_WINDOWS_USER/AppData/Local/Android/Sdk/platform-tools/adb.exe
else
  for candidate in /mnt/c/Users/*/AppData/Local/Android/Sdk/platform-tools/adb.exe; do
    if [[ -x $candidate ]]; then
      emulator_stop_adb=$candidate
      break
    fi
  done
fi
ADB=("$adb_bin")
[[ -z ${NAIVEFOX_ANDROID_SERIAL:-} ]] || ADB+=(-s "$NAIVEFOX_ANDROID_SERIAL")
if ! "${ADB[@]}" get-state >/dev/null 2>&1; then
  if (( start_emulator )); then
    emulator_tool="$SOURCE_ROOT/netwerk/naivefox/tools/start-android-emulator.sh"
    NAIVEFOX_ADB="$adb_bin" "$emulator_tool"
    emulator_started=1
  else
    skip_device 'no selected adb device is online'
  fi
fi
device_abi=$("${ADB[@]}" shell getprop ro.product.cpu.abi | tr -d '\r')
[[ $device_abi == arm64-v8a ]] || skip_device "selected device ABI is $device_abi, not arm64-v8a"
device_api=$("${ADB[@]}" shell getprop ro.build.version.sdk | tr -d '\r')
[[ $device_api =~ ^[0-9]+$ && $device_api -ge 26 ]] || \
  skip_device "selected device API is ${device_api:-unknown}, below 26"

current_remote_root=
current_forward_socks=
current_forward_http=
current_reverse=
current_harness_pid=
current_delay_pid=
current_probe_pid=
current_client_log=
current_fixture_user=
current_fixture_pass=
cleanup_current() {
  local status=${1:-0}
  if [[ -n $current_delay_pid ]] && kill -0 "$current_delay_pid" 2>/dev/null; then
    kill "$current_delay_pid" 2>/dev/null || true
    wait "$current_delay_pid" 2>/dev/null || true
  fi
  if [[ -n $current_remote_root ]]; then
    "${ADB[@]}" shell "touch $current_remote_root/stop" >/dev/null 2>&1 || true
  fi
  if [[ -n $current_harness_pid ]] && kill -0 "$current_harness_pid" 2>/dev/null; then
    for ((i = 0; i < 100; i++)); do
      kill -0 "$current_harness_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$current_harness_pid" 2>/dev/null; then
      remote_pid=$("${ADB[@]}" shell "cat $current_remote_root/harness.pid 2>/dev/null" | tr -d '\r' || true)
      if [[ $remote_pid =~ ^[0-9]+$ ]]; then
        "${ADB[@]}" shell "kill $remote_pid" >/dev/null 2>&1 || true
      fi
    fi
    wait "$current_harness_pid" 2>/dev/null || true
  fi
  if [[ -n $current_probe_pid ]] && kill -0 "$current_probe_pid" 2>/dev/null; then
    kill "$current_probe_pid" 2>/dev/null || true
    wait "$current_probe_pid" 2>/dev/null || true
  fi
  [[ -z $current_forward_socks ]] || \
    "${ADB[@]}" forward --remove "tcp:$current_forward_socks" >/dev/null 2>&1 || true
  [[ -z $current_forward_http ]] || \
    "${ADB[@]}" forward --remove "tcp:$current_forward_http" >/dev/null 2>&1 || true
  [[ -z $current_reverse ]] || \
    "${ADB[@]}" reverse --remove "tcp:$current_reverse" >/dev/null 2>&1 || true
  if [[ -n $current_remote_root && $current_remote_root == /data/local/tmp/naivefox-android-embedded-* ]]; then
    "${ADB[@]}" shell "rm -rf -- $current_remote_root" >/dev/null 2>&1 || true
  fi
  if (( status != 0 )) && [[ -n $current_client_log && -f $current_client_log ]]; then
    mkdir -p "$SOURCE_ROOT/artifacts"
    sanitize_stream "$current_fixture_user" "$current_fixture_pass" \
      <"$current_client_log" \
      >"$SOURCE_ROOT/artifacts/android-embedded-failure.log"
    printf 'sanitized Android log: %s\n' \
      "$SOURCE_ROOT/artifacts/android-embedded-failure.log" >&2
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  current_remote_root=
  current_forward_socks=
  current_forward_http=
  current_reverse=
  current_harness_pid=
  current_delay_pid=
  current_probe_pid=
  current_client_log=
  current_fixture_user=
  current_fixture_pass=
}
cleanup() {
  local status=$?
  cleanup_current "$status"
  if (( emulator_started )); then
    "$emulator_stop_adb" -s "$emulator_stop_serial" emu kill >/dev/null 2>&1 || true
    emulator_started=0
  fi
  rm -rf -- "$work_dir"
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_remote_file() {
  local path=$1
  local description=$2
  for ((i = 0; i < 200; i++)); do
    if "${ADB[@]}" shell "test -s $path" >/dev/null 2>&1; then
      return
    fi
    if [[ -n $current_harness_pid ]] && ! kill -0 "$current_harness_pid" 2>/dev/null; then
      printf 'Android harness exited before %s\n' "$description" >&2
      return 1
    fi
    sleep 0.1
  done
  printf 'timed out waiting for %s\n' "$description" >&2
  return 1
}

wait_for_host_process() {
  local pid=$1
  local description=$2
  for ((i = 0; i < 300; i++)); do
    # A cleanly exited child is the success condition.  Preserve an explicit
    # zero status here so callers running with `set -e` do not misclassify the
    # expected shutdown as a harness failure.
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  printf 'timed out waiting for %s\n' "$description" >&2
  return 1
}

free_device_port() {
  local base=$1
  local candidate
  local hex_port
  for ((offset = 0; offset < 200; offset++)); do
    candidate=$((base + offset))
    printf -v hex_port '%04X' "$candidate"
    if ! "${ADB[@]}" shell "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null" |
      tr -d '\r' | rg -qi ":${hex_port}[[:space:]]"; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  printf 'cannot find an unused device TCP port\n' >&2
  return 1
}

run_protocol() {
  local current_protocol=$1
  local host_alias=${NAIVEFOX_ANDROID_HOST_ALIAS:-10.0.2.2}
  local proxy_scheme=https
  local fixture_environment=()
  if [[ $current_protocol == h3 ]]; then
    proxy_scheme=quic
    fixture_environment=(env "NAIVEFOX_FIXTURE_PROXY_IP_SAN=$host_alias")
  fi
  "${fixture_environment[@]}" "$INTEGRATION_DIR/start.sh" --mode "$current_protocol"
  run_dir=$(<"$ACTIVE_RUN_FILE")
  source "$run_dir/fixture.env"
  current_fixture_user=$NAIVEFOX_FIXTURE_USER
  current_fixture_pass=$NAIVEFOX_FIXTURE_PASS

  device_socks_port=$(free_device_port 38080)
  device_http_port=$(free_device_port $((device_socks_port + 1)))
  current_forward_socks=$("${ADB[@]}" forward tcp:0 "tcp:$device_socks_port" | tr -d '\r')
  current_forward_http=$("${ADB[@]}" forward tcp:0 "tcp:$device_http_port" | tr -d '\r')
  [[ $current_forward_socks =~ ^[0-9]+$ && $current_forward_http =~ ^[0-9]+$ ]] || {
    printf 'adb did not allocate host forwarding ports\n' >&2
    return 1
  }

  proxy_host=localhost
  proxy_port=$NAIVEFOX_FIXTURE_PROXY_PORT
  if [[ $current_protocol == h2 ]]; then
    current_reverse=$(free_device_port 38443)
    "${ADB[@]}" reverse "tcp:$current_reverse" \
      "tcp:$NAIVEFOX_FIXTURE_PROXY_PORT" >/dev/null
    proxy_port=$current_reverse
  else
    proxy_host=$host_alias
  fi

  current_remote_root="/data/local/tmp/naivefox-android-embedded-$current_protocol-$$-$RANDOM"
  [[ $current_remote_root == /data/local/tmp/naivefox-android-embedded-* ]]
  remote_package="$current_remote_root/package-relocated"
  remote_profile="$current_remote_root/profile"
  remote_config="$current_remote_root/config.json"
  remote_harness="$current_remote_root/harness"
  remote_runtime="$remote_package/lib/arm64-v8a"
  remote_stop="$current_remote_root/stop"
  remote_ready="$current_remote_root/ready"
  remote_result="$current_remote_root/result"
  "${ADB[@]}" shell "mkdir -p $remote_package $remote_profile" >/dev/null
  "${ADB[@]}" push "$package_dir/." "$remote_package/" >/dev/null
  "${ADB[@]}" push "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE/." \
    "$remote_profile/" >/dev/null
  "${ADB[@]}" push "$harness" "$remote_harness" >/dev/null
  "${ADB[@]}" shell "chmod 700 $remote_harness $remote_profile && chmod 600 $remote_profile/*" >/dev/null

  config_file="$run_dir/android-$current_protocol-config.json"
  CONFIG_PATH=$config_file PROXY_SCHEME=$proxy_scheme PROXY_HOST=$proxy_host \
    PROXY_PORT=$proxy_port PROXY_USER=$NAIVEFOX_FIXTURE_USER \
    PROXY_PASS=$NAIVEFOX_FIXTURE_PASS SOCKS_PORT=$device_socks_port \
    HTTP_PORT=$device_http_port python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import quote

credentials = (
    f"{quote(os.environ['PROXY_USER'], safe='')}:"
    f"{quote(os.environ['PROXY_PASS'], safe='')}@"
)
proxy = (
    f"{os.environ['PROXY_SCHEME']}://{credentials}"
    f"{os.environ['PROXY_HOST']}:{os.environ['PROXY_PORT']}"
)
config = {
    "listen": [
        f"socks://127.0.0.1:{os.environ['SOCKS_PORT']}",
        f"http://127.0.0.1:{os.environ['HTTP_PORT']}",
    ],
    "proxy": [proxy, proxy],
    "log": "",
}
path = Path(os.environ["CONFIG_PATH"])
path.write_text(json.dumps(config), encoding="utf-8")
path.chmod(0o600)
PY
  "${ADB[@]}" push "$config_file" "$remote_config" >/dev/null
  "${ADB[@]}" shell "chmod 600 $remote_config" >/dev/null

  if [[ $current_protocol == h3 ]]; then
    probe_ready="$run_dir/android-udp-probe.json"
    python3 "$INTEGRATION_DIR/android_udp_echo.py" --ready-file "$probe_ready" \
      >"$run_dir/android-udp-probe.log" 2>&1 &
    current_probe_pid=$!
    wait_for_file "$probe_ready" "$current_probe_pid" 'Android UDP preflight server'
    probe_port=$(python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["port"])' "$probe_ready")
    if ! "${ADB[@]}" shell "$remote_harness --udp-probe $host_alias $probe_port"; then
      printf 'Android emulator cannot exchange UDP with host alias %s\n' \
        "$host_alias" >&2
      return 1
    fi
    wait_for_host_process "$current_probe_pid" 'Android UDP preflight server'
    wait "$current_probe_pid"
    current_probe_pid=
  fi

  current_client_log="$run_dir/android-$current_protocol-harness.log"
  remote_command="cd $current_remote_root && echo \$\$ > harness.pid && exec env LD_LIBRARY_PATH=$remote_runtime $remote_harness $remote_runtime/libxul.so $remote_config $remote_profile $remote_runtime $remote_stop $remote_ready $remote_result"
  "${ADB[@]}" shell "$remote_command" >"$current_client_log" 2>&1 &
  current_harness_pid=$!
  wait_for_remote_file "$remote_ready" 'embedded harness readiness marker'

  curl_socks=(
    --silent --show-error --fail --noproxy '' --connect-timeout 2 --max-time 20
    --socks5-hostname "127.0.0.1:$current_forward_socks"
  )
  curl_http=(
    --silent --show-error --fail --noproxy '' --connect-timeout 2 --max-time 20
    --proxy "http://127.0.0.1:$current_forward_http"
  )
  ready=0
  for ((i = 0; i < 100; i++)); do
    if curl "${curl_socks[@]}" \
      "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/health" >/dev/null 2>&1 &&
      curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
      "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    kill -0 "$current_harness_pid" 2>/dev/null || break
    sleep 0.2
  done
  (( ready )) || {
    printf 'Android SOCKS and HTTP CONNECT listeners did not become ready\n' >&2
    return 1
  }

  expected_hash=$(python3 -c \
    'import hashlib; n=1048576; p=bytes(range(251)); print(hashlib.sha256((p*((n+250)//251))[:n]).hexdigest())')
  for frontend in socks http; do
    download="$run_dir/android-$current_protocol-$frontend-download.bin"
    if [[ $frontend == socks ]]; then
      curl "${curl_socks[@]}" --output "$download" \
        "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/large?size=1048576"
    else
      curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
        --output "$download" \
        "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/large?size=1048576"
    fi
    [[ $(sha256sum "$download" | cut -d' ' -f1) == "$expected_hash" ]]
  done

  upload="$run_dir/android-$current_protocol-upload.bin"
  dd if=/dev/zero of="$upload" bs=65536 count=8 status=none
  expected_upload_hash=$(sha256sum "$upload" | cut -d' ' -f1)
  upload_socks=$(curl "${curl_socks[@]}" --data-binary "@$upload" \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/upload")
  upload_http=$(curl "${curl_http[@]}" --cacert "$NAIVEFOX_FIXTURE_CA" \
    --data-binary "@$upload" \
    "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/upload")
  python3 - "$upload_socks" "$upload_http" "$expected_upload_hash" <<'PY'
import json
import sys

expected = {"bytes": 524288, "sha256": sys.argv[3]}
for response in sys.argv[1:3]:
    if json.loads(response) != expected:
        raise SystemExit("Android upload integrity check failed")
PY

  protocol_pattern="^Outer protocol: $current_protocol\r?$"
  protocol_count_before=$(rg -c "$protocol_pattern" "$current_client_log" || true)
  curl "${curl_socks[@]}" --max-time 20 --output /dev/null \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/delay?ms=10000" &
  current_delay_pid=$!
  active=0
  for ((i = 0; i < 50; i++)); do
    protocol_count=$(rg -c "$protocol_pattern" "$current_client_log" || true)
    if (( protocol_count > protocol_count_before )); then
      active=1
      break
    fi
    kill -0 "$current_delay_pid" 2>/dev/null || break
    sleep 0.1
  done
  (( active )) || {
    printf 'Android delayed request did not establish before stop\n' >&2
    return 1
  }
  "${ADB[@]}" shell "touch $remote_stop" >/dev/null
  wait_for_host_process "$current_harness_pid" 'embedded runner shutdown'
  set +e
  wait "$current_harness_pid"
  harness_status=$?
  set -e
  current_harness_pid=
  [[ $harness_status -eq 0 ]] || {
    printf 'Android embedded harness returned %s\n' "$harness_status" >&2
    return 1
  }
  set +e
  wait "$current_delay_pid" 2>/dev/null
  delay_status=$?
  set -e
  current_delay_pid=
  [[ $delay_status -ne 0 ]] || {
    printf 'active Android request unexpectedly completed during stop\n' >&2
    return 1
  }

  result_file="$run_dir/android-$current_protocol-result.txt"
  "${ADB[@]}" pull "$remote_result" "$result_file" >/dev/null
  rg -q '^version=.+$' "$result_file"
  rg -q '^status=0$' "$result_file"
  rg -q '^stop_requested=1$' "$result_file"
  if rg -Fq "$NAIVEFOX_FIXTURE_PASS" "$current_client_log"; then
    printf 'proxy password appeared in Android harness output\n' >&2
    return 1
  fi
  rg -q "$protocol_pattern" "$current_client_log"
  if curl "${curl_socks[@]}" --connect-timeout 1 --max-time 2 \
    "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/health" >/dev/null 2>&1; then
    printf 'Android SOCKS listener remained open after embedded shutdown\n' >&2
    return 1
  fi
  if curl "${curl_http[@]}" --connect-timeout 1 --max-time 2 \
    --cacert "$NAIVEFOX_FIXTURE_CA" \
    "https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT/health" >/dev/null 2>&1; then
    printf 'Android HTTP CONNECT listener remained open after embedded shutdown\n' >&2
    return 1
  fi

  printf 'NaiveFox Android embedded SOCKS + HTTP CONNECT tests passed over %s\n' \
    "$current_protocol"
  cleanup_current 0
}

if [[ $protocol == h2 || $protocol == all ]]; then
  run_protocol h2
fi
if [[ $protocol == h3 || $protocol == all ]]; then
  run_protocol h3
fi
