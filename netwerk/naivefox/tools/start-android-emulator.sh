#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: start-android-emulator.sh [OPTIONS]

Start and wait for the ARM64 Android emulator used by the NaiveFox embedded
harness. The helper supplies the QEMU machine override needed by ARM64 system
images whose default audio device selects an unavailable PCI bus.

Options:
  --avd NAME       AVD name (default: naivefox-arm64-api27-raw)
  --serial SERIAL  adb serial (default: emulator-5554)
  --help           show this help

Environment:
  NAIVEFOX_ANDROID_AVD
  NAIVEFOX_ANDROID_SERIAL
  NAIVEFOX_ANDROID_EMULATOR
  ANDROID_SDK_ROOT or ANDROID_HOME
  ANDROID_AVD_HOME
  NAIVEFOX_ANDROID_BOOT_TIMEOUT (seconds, default: 900)

On Linux/WSL the managed default lives under
${XDG_DATA_HOME:-$HOME/.local/share}/naivefox/{android-sdk,android-avd}.
No Windows emulator is selected automatically from WSL.
EOF
}

avd=${NAIVEFOX_ANDROID_AVD:-naivefox-arm64-api27-raw}
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

adb=${NAIVEFOX_ADB:-$(command -v adb || true)}
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
    local boot_completed= legacy_boot_completed= abi=
    if timeout 5 "$adb" -s "$serial" get-state >/dev/null 2>&1; then
      boot_completed=$(timeout 5 "$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null |
        tr -d '\r') || boot_completed=
      legacy_boot_completed=$(timeout 5 "$adb" -s "$serial" shell getprop dev.bootcomplete 2>/dev/null |
        tr -d '\r') || legacy_boot_completed=
      abi=$(timeout 5 "$adb" -s "$serial" shell getprop ro.product.cpu.abi 2>/dev/null |
        tr -d '\r') || abi=
    fi
    if [[ $abi == arm64-v8a && ( $boot_completed == 1 || $legacy_boot_completed == 1 ) ]]; then
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

managed_root=${XDG_DATA_HOME:-$HOME/.local/share}/naivefox
sdk_root=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}
if [[ -z $sdk_root && $(uname -s) == Linux &&
      -x $managed_root/android-sdk/emulator/emulator ]]; then
  sdk_root=$managed_root/android-sdk
fi
if [[ -n $sdk_root ]]; then
  export ANDROID_SDK_ROOT=$sdk_root
fi
if [[ -z ${ANDROID_AVD_HOME:-} && -d $managed_root/android-avd ]]; then
  export ANDROID_AVD_HOME=$managed_root/android-avd
fi

if timeout 5 "$adb" -s "$serial" get-state >/dev/null 2>&1 &&
   [[ $(timeout 5 "$adb" -s "$serial" shell getprop ro.product.cpu.abi 2>/dev/null |
       tr -d '\r') == arm64-v8a ]]; then
  if wait_for_android_boot; then
    printf 'Android ARM64 emulator already online with a ready clock: %s\n' "$serial"
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

if [[ -n ${ANDROID_AVD_HOME:-} ]]; then
  avd_home=$ANDROID_AVD_HOME
elif [[ -n ${ANDROID_HOME:-} ]]; then
  avd_home=${ANDROID_AVD_HOME:-$ANDROID_HOME/.android/avd}
elif [[ -d ${HOME:-}/.android/avd ]]; then
  avd_home=${HOME}/.android/avd
else
  avd_home=
fi
if [[ -n $avd_home && ! -f $avd_home/$avd.avd/config.ini ]]; then
  printf 'AVD is not installed: %s (looked below %s)\n' "$avd" "$avd_home" >&2
  exit 1
fi

timeout 15 "$adb" start-server >/dev/null
log_file=${NAIVEFOX_ANDROID_EMULATOR_LOG:-${TMPDIR:-/tmp}/naivefox-android-emulator.log}
printf 'Starting Android ARM64 AVD %s with QEMU virt machine; log: %s\n' \
  "$avd" "$log_file"

# Keep these options in one place. In particular, -qemu -machine virt avoids
# the host QEMU audio device selecting a PCI bus that is not present on ARM64
# software-emulation launches.
emulator_environment=()
emulator_extra_args=()
if [[ $(uname -s) == Linux && $emulator != *.exe ]]; then
  # A headless network gate must not load Windows GPU drivers via WSLg.
  emulator_environment=(env -u DISPLAY -u WAYLAND_DISPLAY
    QT_QPA_PLATFORM=offscreen LIBGL_ALWAYS_SOFTWARE=1)
  emulator_extra_args=(-feature -Vulkan)
fi
nohup "${emulator_environment[@]}" "$emulator" \
  -avd "$avd" \
  -no-window \
  -no-audio \
  -gpu swiftshader_indirect \
  -accel off \
  -no-snapshot \
  -no-boot-anim \
  "${emulator_extra_args[@]}" \
  -qemu -machine virt \
  >"$log_file" 2>&1 &
emulator_pid=$!

if wait_for_android_boot "$emulator_pid"; then
  printf 'Android ARM64 emulator boot and clock ready: %s\n' "$serial"
  exit 0
fi

kill -TERM "$emulator_pid" 2>/dev/null || true
tail -40 "$log_file" >&2 || true
exit 1
