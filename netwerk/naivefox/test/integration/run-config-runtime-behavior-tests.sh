#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

runtime=${NAIVEFOX_RUNTIME:-$OBJDIR/dist/bin/naivefox}
external_runtime=false
if [[ -n ${NAIVEFOX_RUNTIME:-} ]]; then
  [[ $runtime == /* && -x $runtime ]] || {
    printf 'NAIVEFOX_RUNTIME must be an absolute executable path\n' >&2
    exit 2
  }
  external_runtime=true
fi
runtime_environment=(env -u LD_PRELOAD)
if $external_runtime; then
  runtime_environment+=(-u LD_LIBRARY_PATH)
else
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
run_dir=
client_pid=
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ $status -ne 0 ]]; then
    for output in "${quiet_output:-}" "${no_home_output:-}" \
      "${address_output:-}" "${file_output:-}"; do
      [[ -n $output && -f $output ]] && cat "$output"
    done | sanitize_stream "${NAIVEFOX_FIXTURE_USER:-}" \
      "${NAIVEFOX_FIXTURE_PASS:-}" \
      >"$SOURCE_ROOT/artifacts/config-runtime-behavior-failure.log"
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT

"$INTEGRATION_DIR/start.sh" --mode h2
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"

free_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

write_config() {
  local path=$1 port=$2 log_mode=$3 log_path=${4:-} host=${5:-127.0.0.1}
  CONFIG_PATH=$path PROXY_PORT=$NAIVEFOX_FIXTURE_PROXY_PORT \
    PROXY_USER=$NAIVEFOX_FIXTURE_USER PROXY_PASS=$NAIVEFOX_FIXTURE_PASS \
    LISTEN_PORT=$port LISTEN_HOST=$host LOG_MODE=$log_mode LOG_PATH=$log_path \
    python3 - <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import quote

config = {
    "listen": f"socks://{os.environ['LISTEN_HOST']}:{os.environ['LISTEN_PORT']}",
    "proxy": (
        "https://"
        + quote(os.environ["PROXY_USER"], safe="")
        + ":"
        + quote(os.environ["PROXY_PASS"], safe="")
        + "@localhost:"
        + os.environ["PROXY_PORT"]
    ),
}
if os.environ["LOG_MODE"] == "file":
    config["log"] = os.environ["LOG_PATH"]
Path(os.environ["CONFIG_PATH"]).write_text(json.dumps(config), encoding="utf-8")
Path(os.environ["CONFIG_PATH"]).chmod(0o600)
PY
}

quiet_port=$(free_port)
quiet_config="$run_dir/quiet-config.json"
quiet_output="$run_dir/quiet-output.log"
state_root="$run_dir/state"
write_config "$quiet_config" "$quiet_port" absent
"${runtime_environment[@]}" -u NAIVEFOX_PROFILE -u NAIVEFOX_PROXY_USER \
  -u NAIVEFOX_PROXY_PASS \
  -u SSLKEYLOGFILE XDG_STATE_HOME="$state_root" \
  MOZ_CRASHREPORTER_DISABLE=1 \
  "$runtime" "$quiet_config" >"$quiet_output" 2>&1 &
client_pid=$!
for ((i = 0; i < 100; i++)); do
  [[ -n $(ss -Hltn "sport = :$quiet_port") ]] && break
  kill -0 "$client_pid"
  sleep 0.1
done
[[ -n $(ss -Hltn "sport = :$quiet_port") ]]
[[ ! -s $quiet_output ]]
[[ -d $state_root/naivefox/profile ]]
[[ $(stat -c '%a' "$state_root/naivefox/profile") == 700 ]]
kill "$client_pid"
wait "$client_pid" || [[ $? -eq 143 ]]
client_pid=

no_home_port=$(free_port)
no_home_config="$run_dir/no-home-config.json"
no_home_output="$run_dir/no-home-output.log"
no_home_temp="$run_dir/no-home-temp"
mkdir -m 700 "$no_home_temp"
write_config "$no_home_config" "$no_home_port" absent
"${runtime_environment[@]}" -u NAIVEFOX_PROFILE -u NAIVEFOX_PROXY_USER \
  -u NAIVEFOX_PROXY_PASS \
  -u SSLKEYLOGFILE -u HOME -u XDG_STATE_HOME -u XDG_RUNTIME_DIR \
  TMPDIR="$no_home_temp" MOZ_CRASHREPORTER_DISABLE=1 \
  "$runtime" "$no_home_config" >"$no_home_output" 2>&1 &
client_pid=$!
for ((i = 0; i < 100; i++)); do
  [[ -n $(ss -Hltn "sport = :$no_home_port") ]] && break
  kill -0 "$client_pid"
  sleep 0.1
done
[[ -n $(ss -Hltn "sport = :$no_home_port") ]]
[[ ! -s $no_home_output ]]
mapfile -t temporary_profiles < <(
  find "$no_home_temp" -mindepth 1 -maxdepth 1 -type d \
    -name 'naivefox-profile-*' -print
)
[[ ${#temporary_profiles[@]} -eq 1 ]]
[[ $(stat -c '%a' "${temporary_profiles[0]}") == 700 ]]
kill "$client_pid"
wait "$client_pid" || [[ $? -eq 143 ]]
client_pid=

bind_address=$(ip -4 -o addr show scope global | awk '
  { split($4, fields, "/"); print fields[1]; exit }
')
[[ -n $bind_address ]]
address_port=$(free_port)
address_config="$run_dir/address-config.json"
address_output="$run_dir/address-output.log"
write_config "$address_config" "$address_port" absent '' "$bind_address"
"${runtime_environment[@]}" -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
  -u SSLKEYLOGFILE \
  NAIVEFOX_PROFILE="$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  MOZ_CRASHREPORTER_DISABLE=1 \
  "$runtime" "$address_config" >"$address_output" 2>&1 &
client_pid=$!
for ((i = 0; i < 100; i++)); do
  [[ -n $(ss -Hltn "sport = :$address_port") ]] && break
  kill -0 "$client_pid"
  sleep 0.1
done
ss -Hltn "sport = :$address_port" | rg -Fq "$bind_address:"
[[ $(curl --silent --show-error --fail --noproxy '' \
  --socks5-hostname "$bind_address:$address_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small") == \
  naivefox-fixture-small ]]
[[ ! -s $address_output ]]
kill "$client_pid"
wait "$client_pid" || [[ $? -eq 143 ]]
client_pid=

file_port=$(free_port)
file_config="$run_dir/file-config.json"
runtime_log="$run_dir/runtime.log"
file_output="$run_dir/file-output.log"
write_config "$file_config" "$file_port" file "$runtime_log"
"${runtime_environment[@]}" -u NAIVEFOX_PROXY_USER -u NAIVEFOX_PROXY_PASS \
  -u SSLKEYLOGFILE \
  NAIVEFOX_PROFILE="$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  MOZ_CRASHREPORTER_DISABLE=1 \
  "$runtime" "$file_config" >"$file_output" 2>&1 &
client_pid=$!
for ((i = 0; i < 100; i++)); do
  [[ -f $runtime_log ]] && \
    rg -q "^SOCKS5 listening on 127.0.0.1:$file_port$" "$runtime_log" && break
  kill -0 "$client_pid"
  sleep 0.1
done
[[ ! -s $file_output ]]
[[ $(stat -c '%a' "$runtime_log") == 600 ]]
[[ $(curl --silent --show-error --fail --noproxy '' \
  --socks5-hostname "127.0.0.1:$file_port" \
  "http://localhost:$NAIVEFOX_FIXTURE_HTTP_PORT/small") == \
  naivefox-fixture-small ]]
rg -q '^Outer protocol: h2$' "$runtime_log"
rg -q '^Padding negotiated: yes$' "$runtime_log"
! rg -Fq "$NAIVEFOX_FIXTURE_PASS" "$runtime_log"
kill "$client_pid"
wait "$client_pid" || [[ $? -eq 143 ]]
client_pid=

printf '%s\n' \
  'NaiveFox config logging and persistent/temporary profile tests passed'
