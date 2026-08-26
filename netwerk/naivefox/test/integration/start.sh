#!/usr/bin/env bash

set -euo pipefail

# shellcheck source=netwerk/naivefox/test/integration/common.sh
source "$(cd "$(dirname "$0")" && pwd)/common.sh"

fixture_mode=h2
inner_h2_enabled=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --mode)
      fixture_mode=${2:-}
      shift 2
      ;;
    --inner-h2)
      inner_h2_enabled=1
      shift
      ;;
    *)
      printf 'usage: %s [--mode h2|h3] [--inner-h2]\n' "$0" >&2
      exit 2
      ;;
  esac
done
case $fixture_mode in
  h2) fixture_protocols='h1 h2' ;;
  h3) fixture_protocols=h3 ;;
  *)
    printf 'unsupported fixture mode: %s\n' "$fixture_mode" >&2
    exit 2
    ;;
esac

camouflage_style_size=${NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE:-65536}
camouflage_script_size=${NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE:-131072}
for asset_size in "$camouflage_style_size" "$camouflage_script_size"; do
  if [[ ! $asset_size =~ ^[0-9]+$ ]] ||
     (( asset_size < 1024 || asset_size > 4194304 )); then
    printf 'camouflage asset sizes must be integers in 1024..4194304\n' >&2
    exit 2
  fi
done
export NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE=$camouflage_style_size
export NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE=$camouflage_script_size

init_paths
"$INTEGRATION_DIR/setup.sh"
find_certutil
"$INTEGRATION_DIR/stop.sh" --quiet

umask 077
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 6)"
RUN_DIR="$STATE_ROOT/runs/$run_id"
mkdir -p "$RUN_DIR" "$RUN_DIR/xdg-data" "$RUN_DIR/xdg-config" \
  "$RUN_DIR/inner-h2-xdg-data" "$RUN_DIR/inner-h2-xdg-config" \
  "$RUN_DIR/profiles/trusted" "$RUN_DIR/profiles/untrusted" "$RUN_DIR/pki" \
  "$RUN_DIR/completions"
printf '%s\n' "$RUN_DIR" >"$ACTIVE_RUN_FILE"

started=0
cleanup_failed_start() {
  if [[ $started -eq 0 ]]; then
    "$INTEGRATION_DIR/stop.sh" --quiet || true
  fi
}
trap cleanup_failed_start EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CA_KEY="$RUN_DIR/pki/root.key"
CA_CERT="$RUN_DIR/pki/root.crt"
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 7 \
  -keyout "$CA_KEY" -out "$CA_CERT" \
  -subj "/CN=NaiveFox Fixture Root $run_id" \
  -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1

issue_leaf() {
  local name=$1
  local subject_alt_name='DNS:localhost'
  if [[ $name == target ]]; then
    subject_alt_name+=',IP:127.0.0.1'
  elif [[ -n ${NAIVEFOX_FIXTURE_PROXY_IP_SAN:-} ]]; then
    if ! python3 -c 'import ipaddress,sys; ipaddress.ip_address(sys.argv[1])' \
      "$NAIVEFOX_FIXTURE_PROXY_IP_SAN"; then
      printf 'invalid proxy certificate IP SAN: %s\n' \
        "$NAIVEFOX_FIXTURE_PROXY_IP_SAN" >&2
      exit 2
    fi
    subject_alt_name+=",IP:$NAIVEFOX_FIXTURE_PROXY_IP_SAN"
  fi
  local key="$RUN_DIR/pki/$name.key"
  local csr="$RUN_DIR/pki/$name.csr"
  local cert="$RUN_DIR/pki/$name.crt"
  openssl req -new -newkey rsa:2048 -sha256 -nodes -keyout "$key" -out "$csr" \
    -subj "/CN=localhost" >/dev/null 2>&1
  openssl x509 -req -sha256 -days 2 -in "$csr" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$cert" \
    -extfile <(printf '%s\n' \
      'basicConstraints=critical,CA:FALSE' \
      'keyUsage=critical,digitalSignature,keyEncipherment' \
      'extendedKeyUsage=serverAuth' \
      "subjectAltName=$subject_alt_name") >/dev/null 2>&1
  rm -f -- "$csr"
}

issue_leaf proxy
issue_leaf target

run_certutil -N -d "sql:$RUN_DIR/profiles/trusted" --empty-password
run_certutil -A -d "sql:$RUN_DIR/profiles/trusted" \
  -n 'NaiveFox Fixture Root' -t 'CT,,' -i "$CA_CERT"
run_certutil -N -d "sql:$RUN_DIR/profiles/untrusted" --empty-password

if [[ $fixture_mode == h3 ]]; then
  printf '%s\n' \
    'user_pref("network.http.http3.disable_when_third_party_roots_found", false);' \
    >"$RUN_DIR/profiles/trusted/user.js"
fi

if ! run_certutil -L -d "sql:$RUN_DIR/profiles/trusted" \
  -n 'NaiveFox Fixture Root' >/dev/null; then
  printf 'fixture root is absent from the trusted NSS profile\n' >&2
  exit 1
fi
if run_certutil -L -d "sql:$RUN_DIR/profiles/untrusted" \
  -n 'NaiveFox Fixture Root' >/dev/null 2>&1; then
  printf 'fixture root unexpectedly appears in the untrusted NSS profile\n' >&2
  exit 1
fi

ready_file="$RUN_DIR/target-ready.json"
python3 "$INTEGRATION_DIR/target_server.py" \
  --cert "$RUN_DIR/pki/target.crt" --key "$RUN_DIR/pki/target.key" \
  --completion-dir "$RUN_DIR/completions" \
  --request-journal "$RUN_DIR/cache-requests.jsonl" \
  --ready-file "$ready_file" >"$RUN_DIR/target.log" 2>&1 &
target_pid=$!
printf '%s\n' "$target_pid" >"$RUN_DIR/target.pid"
wait_for_file "$ready_file" "$target_pid" 'target server'

read -r http_port https_port < <(
  python3 -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(d["http_port"], d["https_port"])' \
    "$ready_file"
)
if [[ $fixture_mode == h3 ]]; then
  proxy_port=$(python3 -c \
    'import socket; s=socket.socket(type=socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
else
  proxy_port=$(python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
fi
inner_h2_port=
if [[ $inner_h2_enabled == 1 ]]; then
  inner_h2_port=$(python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  while [[ $inner_h2_port == "$proxy_port" ||
           $inner_h2_port == "$http_port" ||
           $inner_h2_port == "$https_port" ]]; do
    inner_h2_port=$(python3 -c \
      'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  done
fi
fixture_user="nf-$(openssl rand -hex 8)"
fixture_pass=$(openssl rand -hex 24)
fixture_allowed_ports="$http_port $https_port"
if [[ $inner_h2_enabled == 1 ]]; then
  fixture_allowed_ports+=" $inner_h2_port"
fi

export NAIVEFOX_FIXTURE_PROTOCOLS="$fixture_protocols"
export NAIVEFOX_FIXTURE_PROXY_PORT="$proxy_port"
export NAIVEFOX_FIXTURE_PROXY_CERT="$RUN_DIR/pki/proxy.crt"
export NAIVEFOX_FIXTURE_PROXY_KEY="$RUN_DIR/pki/proxy.key"
export NAIVEFOX_FIXTURE_ALLOWED_PORTS="$fixture_allowed_ports"
export NAIVEFOX_FIXTURE_USER="$fixture_user"
export NAIVEFOX_FIXTURE_PASS="$fixture_pass"
export NAIVEFOX_FIXTURE_HTTP_PORT="$http_port"
export NAIVEFOX_FIXTURE_HTTPS_PORT="$https_port"
export NAIVEFOX_FIXTURE_INNER_H2_PORT="$inner_h2_port"

"$CADDY_BIN" adapt --config "$INTEGRATION_DIR/Caddyfile" --adapter caddyfile \
  --pretty >"$RUN_DIR/adapted.json"
if ! "$CADDY_BIN" validate --config "$RUN_DIR/adapted.json" \
  >"$RUN_DIR/config-validation.log" 2>&1; then
  cat "$RUN_DIR/config-validation.log" >&2
  exit 1
fi

python3 - "$RUN_DIR/adapted.json" "$proxy_port" "$fixture_mode" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
proxy_port = sys.argv[2]
mode = sys.argv[3]
expected_protocols = ["h1", "h2"] if mode == "h2" else ["h3"]
servers = config["apps"]["http"]["servers"]
matches = []
found_proxy_listener = False
for server in servers.values():
    listen = server.get("listen", [])
    if f"127.0.0.1:{proxy_port}" in listen:
        found_proxy_listener = True
        if server.get("protocols") != expected_protocols:
            raise SystemExit(
                f"proxy listener protocols are not exactly {expected_protocols}"
            )
    for route in server.get("routes", []):
        matches.extend(route.get("match", []))
if not found_proxy_listener:
    raise SystemExit("adapted config lacks the loopback proxy listener")
if any("host" in match for match in matches):
    raise SystemExit("adapted proxy config contains a request Host matcher")
PY

env XDG_DATA_HOME="$RUN_DIR/xdg-data" XDG_CONFIG_HOME="$RUN_DIR/xdg-config" \
  "$CADDY_BIN" run --config "$RUN_DIR/adapted.json" \
  >"$RUN_DIR/caddy.log" 2>&1 &
caddy_pid=$!
printf '%s\n' "$caddy_pid" >"$RUN_DIR/caddy.pid"
if [[ $fixture_mode == h3 ]]; then
  wait_for_h3_proxy "$caddy_pid" "$proxy_port" "$RUN_DIR/caddy.log"
else
  wait_for_proxy "$caddy_pid" "$proxy_port" "$CA_CERT"
fi

inner_h2_pid=
if [[ $inner_h2_enabled == 1 ]]; then
  export NAIVEFOX_FIXTURE_TARGET_CERT="$RUN_DIR/pki/target.crt"
  export NAIVEFOX_FIXTURE_TARGET_KEY="$RUN_DIR/pki/target.key"
  export NAIVEFOX_FIXTURE_INNER_H2_ACCESS_LOG="$RUN_DIR/inner-h2-access.jsonl"
  : >"$NAIVEFOX_FIXTURE_INNER_H2_ACCESS_LOG"
  "$CADDY_BIN" adapt --config "$INTEGRATION_DIR/Caddyfile-inner-h2" \
    --adapter caddyfile --pretty >"$RUN_DIR/inner-h2-adapted.json"
  if ! "$CADDY_BIN" validate --config "$RUN_DIR/inner-h2-adapted.json" \
    >"$RUN_DIR/inner-h2-config-validation.log" 2>&1; then
    cat "$RUN_DIR/inner-h2-config-validation.log" >&2
    exit 1
  fi
  python3 - "$RUN_DIR/inner-h2-adapted.json" "$inner_h2_port" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
expected_listener = f"127.0.0.1:{sys.argv[2]}"
servers = config["apps"]["http"]["servers"]
matching = [
    server for server in servers.values()
    if expected_listener in server.get("listen", [])
]
if len(matching) != 1:
    raise SystemExit("inner config does not contain exactly one loopback listener")
if matching[0].get("protocols") != ["h2"]:
    raise SystemExit("inner listener protocols are not exactly ['h2']")
PY
  env XDG_DATA_HOME="$RUN_DIR/inner-h2-xdg-data" \
    XDG_CONFIG_HOME="$RUN_DIR/inner-h2-xdg-config" \
    "$CADDY_BIN" run --config "$RUN_DIR/inner-h2-adapted.json" \
    >"$RUN_DIR/inner-h2.log" 2>&1 &
  inner_h2_pid=$!
  printf '%s\n' "$inner_h2_pid" >"$RUN_DIR/inner-h2.pid"
  wait_for_h2_origin "$inner_h2_pid" "$inner_h2_port" "$CA_CERT"
fi

cat >"$RUN_DIR/fixture.env" <<EOF
NAIVEFOX_FIXTURE_RUN_DIR=$RUN_DIR
NAIVEFOX_FIXTURE_MODE=$fixture_mode
NAIVEFOX_FIXTURE_PROTOCOLS='$fixture_protocols'
NAIVEFOX_FIXTURE_PROXY_PORT=$proxy_port
NAIVEFOX_FIXTURE_HTTP_PORT=$http_port
NAIVEFOX_FIXTURE_HTTPS_PORT=$https_port
NAIVEFOX_FIXTURE_INNER_H2_PORT=$inner_h2_port
NAIVEFOX_FIXTURE_INNER_H2_ENABLED=$inner_h2_enabled
NAIVEFOX_FIXTURE_INNER_H2_PID=$inner_h2_pid
NAIVEFOX_FIXTURE_INNER_H2_ACCESS_LOG=$RUN_DIR/inner-h2-access.jsonl
NAIVEFOX_FIXTURE_USER=$fixture_user
NAIVEFOX_FIXTURE_PASS=$fixture_pass
NAIVEFOX_FIXTURE_CA=$CA_CERT
NAIVEFOX_FIXTURE_TRUSTED_PROFILE=$RUN_DIR/profiles/trusted
NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE=$RUN_DIR/profiles/untrusted
NAIVEFOX_FIXTURE_CADDY_PID=$caddy_pid
NAIVEFOX_FIXTURE_TARGET_PID=$target_pid
NAIVEFOX_FIXTURE_CACHE_REQUEST_JOURNAL=$RUN_DIR/cache-requests.jsonl
NAIVEFOX_FIXTURE_PROXY_IP_SAN=${NAIVEFOX_FIXTURE_PROXY_IP_SAN:-}
NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE=$camouflage_style_size
NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE=$camouflage_script_size
EOF
chmod 0600 "$RUN_DIR/fixture.env"

{
  cat "$STATE_ROOT/setup-diagnostics.txt"
  printf 'run_id=%s\n' "$run_id"
  printf 'fixture_mode=%s\n' "$fixture_mode"
  printf 'proxy_protocols=%s\n' "$fixture_protocols"
  printf 'proxy_listener=127.0.0.1:%s\n' "$proxy_port"
  printf 'proxy_ip_san=%s\n' "${NAIVEFOX_FIXTURE_PROXY_IP_SAN:-}"
  printf 'http_target=127.0.0.1:%s\n' "$http_port"
  printf 'https_target=127.0.0.1:%s\n' "$https_port"
  printf 'inner_h2_enabled=%s\n' "$inner_h2_enabled"
  if [[ $inner_h2_enabled == 1 ]]; then
    printf 'inner_h2_target=127.0.0.1:%s\n' "$inner_h2_port"
    printf 'inner_h2_pid=%s\n' "$inner_h2_pid"
  fi
  printf 'caddy_pid=%s\n' "$caddy_pid"
  printf 'target_pid=%s\n' "$target_pid"
  printf 'camouflage_style_size=%s\n' "$camouflage_style_size"
  printf 'camouflage_script_size=%s\n' "$camouflage_script_size"
} >"$RUN_DIR/diagnostics.txt"

started=1
trap - EXIT INT TERM
printf 'fixture started; environment: %s\n' "$RUN_DIR/fixture.env"
