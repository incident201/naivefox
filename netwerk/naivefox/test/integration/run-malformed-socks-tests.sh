#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
umask 077

runtime=${NAIVEFOX_RUNTIME:-$OBJDIR/dist/bin/naivefox}
[[ -x $runtime ]] || {
  printf 'NaiveFox runtime not found: %s\n' "$runtime" >&2
  exit 2
}
external_runtime=false
if [[ -n ${NAIVEFOX_RUNTIME:-} ]]; then
  external_runtime=true
fi
runtime_environment=(env -u LD_PRELOAD)
if $external_runtime; then
  runtime_environment+=(-u LD_LIBRARY_PATH)
else
  export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

run_dir=$(mktemp -d "$OBJDIR/naivefox-malformed-socks.XXXXXX")
client_pid=
cleanup() {
  local status=$?
  if [[ -n $client_pid ]] && kill -0 "$client_pid" 2>/dev/null; then
    kill "$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ $status -eq 0 ]]; then
    rm -rf -- "$run_dir"
  else
    printf 'malformed SOCKS test failed; private state: %s\n' "$run_dir" >&2
  fi
  return "$status"
}
trap cleanup EXIT

free_port() {
  python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

socks_port=$(free_port)
http_port=$(free_port)
config="$run_dir/config.json"
client_log="$run_dir/naivefox.log"
CONFIG_PATH=$config SOCKS_PORT=$socks_port HTTP_PORT=$http_port \
  python3 - <<'PY'
import json
import os
from pathlib import Path

config = {
    "listen": [
        f"socks://127.0.0.1:{os.environ['SOCKS_PORT']}",
        f"http://127.0.0.1:{os.environ['HTTP_PORT']}",
    ],
    "proxy": "https://test:test@127.0.0.1:28443",
    "log": "",
}
path = Path(os.environ["CONFIG_PATH"])
path.write_text(json.dumps(config), encoding="utf-8")
path.chmod(0o600)
PY

XDG_STATE_HOME="$run_dir/state" \
  "${runtime_environment[@]}" \
  "$runtime" "$config" >"$client_log" 2>&1 &
client_pid=$!
for ((i = 0; i < 150; i++)); do
  if [[ -n $(ss -Hltn "sport = :$socks_port") &&
        -n $(ss -Hltn "sport = :$http_port") ]]; then
    break
  fi
  kill -0 "$client_pid" 2>/dev/null || {
    cat "$client_log" >&2
    exit 1
  }
  sleep 0.1
done
[[ -n $(ss -Hltn "sport = :$socks_port") ]]
[[ -n $(ss -Hltn "sport = :$http_port") ]]

rss_kib() {
  awk '/^VmRSS:/ { print $2 }' "/proc/$client_pid/status"
}

rss_before=$(rss_kib)
SOCKS_PORT=$socks_port HTTP_PORT=$http_port python3 - <<'PY'
import os
import socket
import time

socks_port = int(os.environ["SOCKS_PORT"])
http_port = int(os.environ["HTTP_PORT"])

def socks_probe(payload: bytes, read_reply: bool = True) -> None:
    with socket.create_connection(("127.0.0.1", socks_port), timeout=2) as sock:
        try:
            sock.sendall(payload)
        except (BrokenPipeError, ConnectionResetError):
            # The server is allowed to close as soon as its bounded reject has
            # been flushed.  A peer that writes a large tail can observe that
            # close before sendall() has consumed the tail.
            return
        if read_reply:
            sock.settimeout(2)
            try:
                reply = sock.recv(64)
            except ConnectionResetError:
                # CloseWithStatus(NS_OK) can surface as ECONNRESET on a local
                # socket after the small reply was flushed.  This is terminal
                # and bounded; the parser gtest checks the exact reply event.
                return
            if reply and len(reply) > 12:
                raise SystemExit(f"unexpected bounded SOCKS reply: {reply!r}")
        else:
            # Keep a non-reading peer alive long enough to exercise the fixed
            # reply buffer and verify that input is not re-armed.
            time.sleep(0.5)

greeting = b"\x05\x01\x00"
udp = greeting + b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00"
bind = greeting + b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00"
bad_atyp = greeting + b"\x05\x01\x00\x09" + b"\x00" * 8
bad_version = b"\x04" + b"\x00" * 64
bad_reserved = greeting + b"\x05\x01\x01\x01" + b"\x00" * 6

for payload in (udp, bind, bad_atyp, bad_version, bad_reserved):
    socks_probe(payload)

large_tail = udp + (b"x" * (2 * 1024 * 1024))
socks_probe(large_tail)
socks_probe(udp, read_reply=False)
time.sleep(0.5)

for _ in range(200):
    socks_probe(udp)

with socket.create_connection(("127.0.0.1", socks_port), timeout=2) as sock:
    sock.sendall(greeting)
    sock.settimeout(2)
    if sock.recv(2) != b"\x05\x00":
        raise SystemExit("normal SOCKS connection did not survive malformed peers")

def http_probe(request: bytes, expected: bytes) -> None:
    with socket.create_connection(("127.0.0.1", http_port), timeout=2) as sock:
        sock.sendall(request)
        sock.settimeout(2)
        response = sock.recv(256)
        if not response.startswith(expected):
            raise SystemExit(f"unexpected HTTP response: {response!r}")

http_probe(b"GET / HTTP/1.1\r\nHost: example.test\r\n\r\n", b"HTTP/1.1 405")
http_probe(
    b"CONNECT example.test:443 HTTP/1.1\r\n" + b"X-Fill: " + b"x" * 70000 + b"\r\n\r\n",
    b"HTTP/1.1 431",
)
for _ in range(100):
    http_probe(b"GET / HTTP/1.1\r\nHost: example.test\r\n\r\n", b"HTTP/1.1 405")

print("malformed SOCKS and HTTP terminal-state probes passed")
PY

kill -0 "$client_pid"
rss_after=$(rss_kib)
(( rss_after - rss_before < 65536 )) || {
  printf 'RSS grew beyond malformed-input bound: before=%s KiB after=%s KiB\n' \
    "$rss_before" "$rss_after" >&2
  exit 1
}

printf 'Malformed SOCKS/HTTP terminal-state and bounded-memory tests passed\n'
