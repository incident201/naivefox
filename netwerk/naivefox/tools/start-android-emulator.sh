#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: start-android-emulator.sh [OPTIONS]

Start and wait for an emulator that can execute the staged ARM64 NaiveFox
runtime. The default Google APIs x86_64 image provides arm64-v8a through
Android's native bridge.

Options:
  --avd NAME       AVD name (default: naivefox-googleapis-api30-arm64)
  --serial SERIAL  adb serial (default: emulator-5554)
  --help           show this help

Environment:
  NAIVEFOX_ANDROID_AVD
  NAIVEFOX_ANDROID_SERIAL
  NAIVEFOX_ANDROID_EMULATOR
  ANDROID_SDK_ROOT or ANDROID_HOME
  ANDROID_AVD_HOME
  NAIVEFOX_ANDROID_BOOT_TIMEOUT (seconds, default: 900)

The SDK may be installed under $HOME/Android/Sdk or the managed
${XDG_DATA_HOME:-$HOME/.local/share}/naivefox/android-sdk directory.
See MINIMAL.md for AVD provisioning and the runtime test command.
EOF
}

avd=${NAIVEFOX_ANDROID_AVD:-naivefox-googleapis-api30-arm64}
serial=${NAIVEFOX_ANDROID_SERIAL:-emulator-5554}
emulator=${NAIVEFOX_ANDROID_EMULATOR:-}
boot_timeout=${NAIVEFOX_ANDROID_BOOT_TIMEOUT:-900}

while (( $# )); do
  case "$1" in
    --avd)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      avd=$2
      shift 2
      ;;
    --serial)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      serial=$2
      shift 2
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

[[ $avd =~ ^[A-Za-z0-9._-]+$ ]] || {
  printf 'unsafe AVD name: %s\n' "$avd" >&2
  exit 2
}
[[ $serial =~ ^[A-Za-z0-9._:-]+$ ]] || {
  printf 'unsafe adb serial: %s\n' "$serial" >&2
  exit 2
}
[[ $boot_timeout =~ ^[1-9][0-9]*$ ]] || {
  printf 'boot timeout must be a positive integer: %s\n' "$boot_timeout" >&2
  exit 2
}

managed_root=${XDG_DATA_HOME:-$HOME/.local/share}/naivefox
sdk_root=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}
if [[ -z $sdk_root && $(uname -s) == Linux &&
      -x $managed_root/android-sdk/emulator/emulator ]]; then
  sdk_root=$managed_root/android-sdk
fi
if [[ -z $sdk_root && -x ${HOME:-}/Android/Sdk/emulator/emulator ]]; then
  sdk_root=$HOME/Android/Sdk
fi
if [[ -n $sdk_root ]]; then
  export ANDROID_SDK_ROOT=$sdk_root
fi

adb=${NAIVEFOX_ADB:-$(command -v adb || true)}
if [[ -z $adb && -x $sdk_root/platform-tools/adb ]]; then
  adb=$sdk_root/platform-tools/adb
fi
[[ -n $adb && -x $adb ]] || {
  printf 'adb is unavailable; install platform-tools or set NAIVEFOX_ADB\n' >&2
  exit 1
}

wait_for_android_boot() {
  local owned_pid=${1:-}
  local deadline=$((SECONDS + boot_timeout))
  local consecutive_clocks=0
  while (( SECONDS < deadline )); do
    if [[ -n $owned_pid ]] && ! kill -0 "$owned_pid" 2>/dev/null; then
      printf 'Android emulator exited before boot completion\n' >&2
      return 1
    fi
    local boot_completed= legacy_boot_completed= abi_list=
    if timeout 5 "$adb" -s "$serial" get-state >/dev/null 2>&1; then
      boot_completed=$(timeout 5 "$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null |
        tr -d '\r') || boot_completed=
      legacy_boot_completed=$(timeout 5 "$adb" -s "$serial" shell getprop dev.bootcomplete 2>/dev/null |
        tr -d '\r') || legacy_boot_completed=
      abi_list=$(timeout 5 "$adb" -s "$serial" shell getprop ro.product.cpu.abilist 2>/dev/null |
        tr -d '\r') || abi_list=
    fi
    if [[ ,$abi_list, == *,arm64-v8a,* &&
          ( $boot_completed == 1 || $legacy_boot_completed == 1 ) ]]; then
      local host_before guest_time host_after
      host_before=$(date +%s)
      guest_time=$(timeout 5 "$adb" -s "$serial" shell date +%s 2>/dev/null |
        tr -d '\r') || guest_time=
      host_after=$(date +%s)
      if [[ $guest_time =~ ^[1-9][0-9]{0,11}$ ]] &&
         (( host_after >= host_before && guest_time >= host_before - 1 &&
            guest_time <= host_after + 1 )); then
        consecutive_clocks=$((consecutive_clocks + 1))
        if (( consecutive_clocks >= 2 )); then
          return 0
        fi
      else
        consecutive_clocks=0
      fi
    else
      consecutive_clocks=0
    fi
    sleep 1
  done
  printf 'Android boot and wall-clock readiness did not complete within %ss\n' \
    "$boot_timeout" >&2
  return 1
}

prepare_android_network() {
  local primary_abi native_bridge route attempt
  primary_abi=$(timeout 5 "$adb" -s "$serial" shell getprop ro.product.cpu.abi |
    tr -d '\r')
  if [[ $primary_abi == arm64-v8a ]]; then
    printf 'Native ARM64 Android boot and clock ready: %s\n' "$serial"
    return 0
  fi
  native_bridge=$(timeout 5 "$adb" -s "$serial" shell getprop ro.dalvik.vm.native.bridge |
    tr -d '\r')
  if [[ -z $native_bridge || $native_bridge == 0 ]]; then
    printf 'Android advertises arm64-v8a but has no active native bridge\n' >&2
    return 1
  fi
  # The Google APIs image may send 10.0.2.2 over wlan0, where the emulator's
  # host alias refuses connections. Disabling Wi-Fi selects the working eth0.
  timeout 10 "$adb" -s "$serial" shell svc wifi disable >/dev/null
  for ((attempt = 0; attempt < 30; attempt++)); do
    route=$(timeout 5 "$adb" -s "$serial" shell ip route get 10.0.2.2 2>/dev/null |
      tr -d '\r') || route=
    if [[ $route == *' dev eth0 '* || $route == *' dev eth0' ]]; then
      printf 'Android ARM64 native bridge boot and clock ready: %s (%s); 10.0.2.2 via eth0\n' \
        "$serial" "$native_bridge"
      return 0
    fi
    sleep 1
  done
  printf 'Android host alias 10.0.2.2 did not route through eth0\n' >&2
  return 1
}

if [[ -z ${ANDROID_AVD_HOME:-} ]]; then
  if [[ -f ${HOME:-}/.android/avd/$avd.ini ||
        -f ${HOME:-}/.android/avd/$avd.avd/config.ini ]]; then
    export ANDROID_AVD_HOME=$HOME/.android/avd
  elif [[ -d $managed_root/android-avd ]]; then
    export ANDROID_AVD_HOME=$managed_root/android-avd
  fi
fi

if timeout 5 "$adb" -s "$serial" get-state >/dev/null 2>&1; then
  if wait_for_android_boot; then
    prepare_android_network
    exit 0
  fi
  exit 1
fi

if [[ -z $emulator ]]; then
  if [[ -n $sdk_root && -x $sdk_root/emulator/emulator ]]; then
    emulator=$sdk_root/emulator/emulator
  elif [[ -n $sdk_root && -x $sdk_root/emulator/emulator.exe ]]; then
    emulator=$sdk_root/emulator/emulator.exe
  else
    emulator=$(command -v emulator 2>/dev/null || true)
  fi
fi

[[ -n $emulator && -x $emulator ]] || {
  printf 'Android emulator binary is unavailable; set NAIVEFOX_ANDROID_EMULATOR\n' >&2
  exit 1
}

avd_home=${ANDROID_AVD_HOME:-${HOME:-}/.android/avd}
avd_config=$avd_home/$avd.avd/config.ini
if [[ -f $avd_home/$avd.ini ]]; then
  avd_path=$(sed -n 's/^path=//p' "$avd_home/$avd.ini" | head -n 1)
  if [[ -n $avd_path ]]; then
    avd_config=$avd_path/config.ini
  fi
fi
if [[ ! -f $avd_config ]]; then
  printf 'AVD is not installed: %s (looked for %s)\n' "$avd" "$avd_config" >&2
  exit 1
fi
avd_abi=$(sed -n 's/^abi.type=//p' "$avd_config" | head -n 1)
case $avd_abi in
  x86_64)
    acceleration_args=(-accel auto)
    machine_args=()
    ;;
  arm64-v8a)
    acceleration_args=(-accel off)
    # Legacy ARM64 AVDs need the virt machine instead of a PCI audio device.
    machine_args=(-qemu -machine virt)
    ;;
  *)
    printf 'Unsupported AVD ABI in %s: %s\n' "$avd_config" "$avd_abi" >&2
    exit 1
    ;;
esac

timeout 15 "$adb" start-server >/dev/null
log_file=${NAIVEFOX_ANDROID_EMULATOR_LOG:-${TMPDIR:-/tmp}/naivefox-android-emulator.log}
printf 'Starting Android %s AVD %s; log: %s\n' "$avd_abi" "$avd" "$log_file"

# Keep the display settings in one place for headless Linux and WSL hosts.
emulator_environment=()
emulator_extra_args=()
emulator_detach=()
if [[ $(uname -s) == Linux && $emulator != *.exe ]]; then
  # A headless network gate must not load Windows GPU drivers via WSLg.
  emulator_environment=(env -u DISPLAY -u WAYLAND_DISPLAY
    QT_QPA_PLATFORM=offscreen LIBGL_ALWAYS_SOFTWARE=1)
  emulator_extra_args=(-feature -Vulkan)
  emulator_detach=(setsid)
fi
nohup "${emulator_detach[@]}" "${emulator_environment[@]}" "$emulator" \
  -avd "$avd" \
  -no-window \
  -no-audio \
  -gpu swiftshader_indirect \
  "${acceleration_args[@]}" \
  -no-snapshot \
  -no-boot-anim \
  "${emulator_extra_args[@]}" \
  "${machine_args[@]}" \
  >"$log_file" 2>&1 &
emulator_pid=$!

if wait_for_android_boot "$emulator_pid"; then
  if prepare_android_network; then
    exit 0
  fi
fi

kill -TERM "$emulator_pid" 2>/dev/null || true
tail -40 "$log_file" >&2 || true
exit 1
