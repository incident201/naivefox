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
  NAIVEFOX_ANDROID_BOOT_TIMEOUT (seconds, default: 180)
EOF
}

avd=${NAIVEFOX_ANDROID_AVD:-naivefox-arm64-api27-raw}
serial=${NAIVEFOX_ANDROID_SERIAL:-emulator-5554}
emulator=${NAIVEFOX_ANDROID_EMULATOR:-}
boot_timeout=${NAIVEFOX_ANDROID_BOOT_TIMEOUT:-180}

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

windows_user=${NAIVEFOX_WINDOWS_USER:-${USERNAME:-}}
if [[ -z $windows_user && -x /mnt/c/WINDOWS/system32/cmd.exe ]]; then
  windows_user=$(cd / && /mnt/c/WINDOWS/system32/cmd.exe /c echo %USERNAME% 2>/dev/null |
    tr -d '\r\n')
fi

if "$adb" -s "$serial" get-state >/dev/null 2>&1 &&
   [[ $("$adb" -s "$serial" shell getprop ro.product.cpu.abi 2>/dev/null |
       tr -d '\r') == arm64-v8a ]]; then
  printf 'Android ARM64 emulator already online: %s\n' "$serial"
  exit 0
fi

if [[ -z $emulator ]]; then
  sdk_root=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}
  if [[ -n $sdk_root && -x $sdk_root/emulator/emulator ]]; then
    emulator=$sdk_root/emulator/emulator
  elif [[ -n $sdk_root && -x $sdk_root/emulator/emulator.exe ]]; then
    emulator=$sdk_root/emulator/emulator.exe
  else
    emulator=$(command -v emulator 2>/dev/null || true)
  fi
fi

# WSL commonly has adb in Linux but the emulator installed on the Windows
# host. Resolve the conventional per-user SDK location without requiring an
# Android application toolchain.
if [[ -z $emulator && -n $windows_user &&
      -x /mnt/c/Users/$windows_user/AppData/Local/Android/Sdk/emulator/emulator.exe ]]; then
  emulator=/mnt/c/Users/$windows_user/AppData/Local/Android/Sdk/emulator/emulator.exe
fi
[[ -n $emulator && -x $emulator ]] || {
  printf 'Android emulator binary is unavailable; set NAIVEFOX_ANDROID_EMULATOR\n' >&2
  exit 1
}

if [[ -n ${ANDROID_SDK_ROOT:-} ]]; then
  avd_home=${ANDROID_AVD_HOME:-$ANDROID_SDK_ROOT/.android/avd}
elif [[ -n ${ANDROID_HOME:-} ]]; then
  avd_home=${ANDROID_AVD_HOME:-$ANDROID_HOME/.android/avd}
elif [[ -d ${HOME:-}/.android/avd ]]; then
  avd_home=${HOME}/.android/avd
elif [[ -n $windows_user && -d /mnt/c/Users/$windows_user/.android/avd ]]; then
  avd_home=/mnt/c/Users/$windows_user/.android/avd
else
  avd_home=
fi
if [[ -n $avd_home && ! -f $avd_home/$avd.avd/config.ini ]]; then
  printf 'AVD is not installed: %s (looked below %s)\n' "$avd" "$avd_home" >&2
  exit 1
fi

"$adb" start-server >/dev/null
log_file=${NAIVEFOX_ANDROID_EMULATOR_LOG:-${TMPDIR:-/tmp}/naivefox-android-emulator.log}
printf 'Starting Android ARM64 AVD %s with QEMU virt machine; log: %s\n' \
  "$avd" "$log_file"

# Keep these options in one place. In particular, -qemu -machine virt avoids
# the host QEMU audio device selecting a PCI bus that is not present on ARM64
# software-emulation launches.
nohup "$emulator" \
  -avd "$avd" \
  -no-window \
  -no-audio \
  -gpu swiftshader_indirect \
  -accel off \
  -no-snapshot \
  -no-boot-anim \
  -qemu -machine virt \
  >"$log_file" 2>&1 &

for ((second = 0; second < boot_timeout; second++)); do
  boot_completed=
  boot_animation=
  legacy_boot_completed=
  if "$adb" -s "$serial" get-state >/dev/null 2>&1; then
    boot_completed=$("$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null |
      tr -d '\r')
    boot_animation=$("$adb" -s "$serial" shell getprop init.svc.bootanim 2>/dev/null |
      tr -d '\r')
    legacy_boot_completed=$("$adb" -s "$serial" shell getprop dev.bootcomplete 2>/dev/null |
      tr -d '\r')
  fi
  if "$adb" -s "$serial" get-state >/dev/null 2>&1 &&
     [[ $("$adb" -s "$serial" shell getprop ro.product.cpu.abi 2>/dev/null |
       tr -d '\r') == arm64-v8a ]] &&
     [[ $boot_completed == 1 || $legacy_boot_completed == 1 ||
        $boot_animation == stopped ]]; then
    printf 'Android ARM64 emulator ready: %s\n' "$serial"
    exit 0
  fi
  sleep 1
done

printf 'Android emulator did not boot within %ss: %s\n' \
  "$boot_timeout" "$log_file" >&2
tail -40 "$log_file" >&2 || true
exit 1
