#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
"$INTEGRATION_DIR/setup.sh"
find_certutil
"$INTEGRATION_DIR/stop.sh" --quiet

umask 077
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 6)"
RUN_DIR="$STATE_ROOT/runs/$run_id"
mkdir -p "$RUN_DIR" "$RUN_DIR/xdg-data" "$RUN_DIR/xdg-config" \
  "$RUN_DIR/profiles/trusted" "$RUN_DIR/profiles/untrusted" "$RUN_DIR/pki"
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
  --ready-file "$ready_file" >"$RUN_DIR/target.log" 2>&1 &
target_pid=$!
printf '%s\n' "$target_pid" >"$RUN_DIR/target.pid"
wait_for_file "$ready_file" "$target_pid" 'target server'

read -r http_port https_port < <(
  python3 -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(d["http_port"], d["https_port"])' \
    "$ready_file"
)
proxy_port=$(python3 -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
fixture_user="nf-$(openssl rand -hex 8)"
fixture_pass=$(openssl rand -hex 24)

export NAIVEFOX_FIXTURE_PROXY_PORT="$proxy_port"
export NAIVEFOX_FIXTURE_PROXY_CERT="$RUN_DIR/pki/proxy.crt"
export NAIVEFOX_FIXTURE_PROXY_KEY="$RUN_DIR/pki/proxy.key"
export NAIVEFOX_FIXTURE_USER="$fixture_user"
export NAIVEFOX_FIXTURE_PASS="$fixture_pass"
export NAIVEFOX_FIXTURE_HTTP_PORT="$http_port"
export NAIVEFOX_FIXTURE_HTTPS_PORT="$https_port"

"$CADDY_BIN" adapt --config "$INTEGRATION_DIR/Caddyfile" --adapter caddyfile \
  --pretty >"$RUN_DIR/adapted.json"
if ! "$CADDY_BIN" validate --config "$RUN_DIR/adapted.json" \
  >"$RUN_DIR/config-validation.log" 2>&1; then
  cat "$RUN_DIR/config-validation.log" >&2
  exit 1
fi

python3 - "$RUN_DIR/adapted.json" "$proxy_port" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
port = sys.argv[2]
servers = config["apps"]["http"]["servers"]
matches = []
found_listener = False
for server in servers.values():
    if f"127.0.0.1:{port}" in server.get("listen", []):
        found_listener = True
        if server.get("protocols") != ["h1", "h2"]:
            raise SystemExit("proxy listener protocols are not exactly h1,h2")
    for route in server.get("routes", []):
        matches.extend(route.get("match", []))
if not found_listener:
    raise SystemExit("adapted config lacks the loopback proxy listener")
if any("host" in match for match in matches):
    raise SystemExit("adapted proxy config contains a request Host matcher")
PY

env XDG_DATA_HOME="$RUN_DIR/xdg-data" XDG_CONFIG_HOME="$RUN_DIR/xdg-config" \
  "$CADDY_BIN" run --config "$RUN_DIR/adapted.json" \
  >"$RUN_DIR/caddy.log" 2>&1 &
caddy_pid=$!
printf '%s\n' "$caddy_pid" >"$RUN_DIR/caddy.pid"
wait_for_proxy "$caddy_pid" "$proxy_port" "$CA_CERT"

cat >"$RUN_DIR/fixture.env" <<EOF
NAIVEFOX_FIXTURE_RUN_DIR=$RUN_DIR
NAIVEFOX_FIXTURE_PROXY_PORT=$proxy_port
NAIVEFOX_FIXTURE_HTTP_PORT=$http_port
NAIVEFOX_FIXTURE_HTTPS_PORT=$https_port
NAIVEFOX_FIXTURE_USER=$fixture_user
NAIVEFOX_FIXTURE_PASS=$fixture_pass
NAIVEFOX_FIXTURE_CA=$CA_CERT
NAIVEFOX_FIXTURE_TRUSTED_PROFILE=$RUN_DIR/profiles/trusted
NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE=$RUN_DIR/profiles/untrusted
NAIVEFOX_FIXTURE_CADDY_PID=$caddy_pid
NAIVEFOX_FIXTURE_TARGET_PID=$target_pid
EOF
chmod 0600 "$RUN_DIR/fixture.env"

{
  cat "$STATE_ROOT/setup-diagnostics.txt"
  printf 'run_id=%s\n' "$run_id"
  printf 'proxy_listener=127.0.0.1:%s\n' "$proxy_port"
  printf 'http_target=127.0.0.1:%s\n' "$http_port"
  printf 'https_target=127.0.0.1:%s\n' "$https_port"
  printf 'caddy_pid=%s\n' "$caddy_pid"
  printf 'target_pid=%s\n' "$target_pid"
} >"$RUN_DIR/diagnostics.txt"

started=1
trap - EXIT INT TERM
printf 'fixture started; environment: %s\n' "$RUN_DIR/fixture.env"
