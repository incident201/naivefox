#!/usr/bin/env python3

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import tempfile
import threading
import time


spec = importlib.util.spec_from_file_location(
    "no_connect_runtime", Path(__file__).with_name("run-no-connect-tests.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

CASES = (
    "missing-subprotocol", "wrong-subprotocol", "unsolicited-compression",
    "text", "oversize", "capacity", "sequence", "future-ack", "decreasing-ack",
    "ack-stream", "ack-body", "reserved",
)
HANDSHAKE_CASES = frozenset(CASES[:3])
ACK_CASES = frozenset(CASES[7:11])


def frame(opcode, body):
    length = len(body)
    head = bytes([0x80 | opcode])
    if length < 126:
        return head + bytes([length]) + body
    if length < 65536:
        return head + b"\x7e" + struct.pack("!H", length) + body
    return head + b"\x7f" + struct.pack("!Q", length) + body


def cell(sequence=20, capacity=512, ack=None, stream=0, body=b"", hint=0):
    frames = b"" if ack is None else struct.pack("!B3xIII", 8, stream, ack, len(body)) + body
    used = 16 + len(frames)
    fixture.require(used <= capacity, "invalid adversarial fixture capacity")
    return (b"NFC1" + struct.pack("!IIHBB", sequence, used,
                                  int(ack is not None), hint, 0) +
            frames + bytes(capacity - used))


def read_client_frame(sock):
    first, second = fixture.receive(sock, 2)
    fixture.require(first & 0x70 == 0 and second & 0x80, "client sent invalid WebSocket frame")
    size = second & 0x7f
    if size == 126:
        size = struct.unpack("!H", fixture.receive(sock, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", fixture.receive(sock, 8))[0]
    fixture.require(size <= 262144, "client message exceeded fixture bound")
    mask = fixture.receive(sock, 4)
    body = fixture.receive(sock, size)
    return first & 15, bool(first & 0x80), bytes(value ^ mask[index % 4] for index, value in enumerate(body))


def receive_client_cell(sock, transport):
    body = bytearray()
    started = False
    while True:
        opcode, final, part = read_client_frame(sock)
        fixture.require(opcode == (0 if started else 2), "expected client binary application message")
        started = True
        body.extend(part)
        fixture.require(len(body) <= 262144, "client fragmented message exceeded fixture bound")
        if final:
            break
    capacities = (512, 4096, 16384, 131072)
    fixture.require(len(body) in capacities and body[:4] == b"NFC1" and
                    body[14] == 0 and
                    body[15] == 0,
                    "client did not send a shaped NFC1 message")
    return struct.unpack_from("!I", body, 4)[0]


class MaliciousWebSocket(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        server.connections += 1
        try:
            self.request.settimeout(8)
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header.extend(fixture.receive(self.request, 1))
                fixture.require(len(header) <= 16384, "WebSocket handshake exceeded fixture bound")
            lines = header.decode("ascii").split("\r\n")
            fixture.require(lines[0] == "GET /api/realtime HTTP/1.1", "unexpected upstream WebSocket request")
            headers = {}
            for line in lines[1:]:
                if line:
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
            fixture.require(headers.get("upgrade", "").lower() == "websocket" and
                            headers.get("sec-websocket-protocol") == server.protocol and
                            headers.get("sec-websocket-version") == "13", "missing genuine WebSocket handshake")
            fixture.require("authorization" not in headers and "proxy-authorization" not in headers,
                            "proxy credentials leaked into WebSocket headers")
            fixture.require(headers.get("cookie", "").startswith("app_session="), "session cookie missing")
            key = headers.get("sec-websocket-key", "")
            fixture.require(len(base64.b64decode(key, validate=True)) == 16, "invalid WebSocket handshake key")
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
            reply = ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket", "Connection: Upgrade",
                     "Sec-WebSocket-Accept: " + accept]
            if server.case != "missing-subprotocol":
                reply.append("Sec-WebSocket-Protocol: " +
                             ("wrong.protocol" if server.case == "wrong-subprotocol" else server.protocol))
            if server.case == "unsolicited-compression":
                reply.append("Sec-WebSocket-Extensions: permessage-deflate")
            fixture.require(server.allow_response.wait(10), "local stream did not reach the attack gate")
            self.request.sendall(("\r\n".join(reply) + "\r\n\r\n").encode("ascii"))
            server.upgraded.set()
            if server.case not in HANDSHAKE_CASES:
                upstream = receive_client_cell(self.request, server.transport) if server.case in ACK_CASES else 20
                body = cell()
                opcode = 2
                if server.case == "text":
                    opcode, body = 1, b"not a binary carrier"
                elif server.case == "oversize":
                    body = cell(capacity=262145)
                elif server.case == "capacity":
                    body = cell(capacity=1024)
                elif server.case == "sequence":
                    body = cell(sequence=21)
                elif server.case == "future-ack":
                    body = cell(ack=upstream + 1)
                elif server.case == "decreasing-ack":
                    self.request.sendall(frame(2, cell(ack=upstream)))
                    server.binary_sent += 1
                    body = cell(sequence=21, ack=upstream - 1)
                elif server.case == "ack-stream":
                    body = cell(ack=upstream, stream=1)
                elif server.case == "ack-body":
                    body = cell(ack=upstream, body=b"x")
                elif server.case == "reserved":
                    body = cell(hint=3)
                server.attack_at = time.monotonic()
                self.request.sendall(frame(opcode, body))
                server.binary_sent += int(opcode == 2)
            else:
                server.attack_at = time.monotonic()
            self.request.settimeout(5)
            while self.request.recv(4096):
                pass
            server.closed_by_client = True
        except (ConnectionResetError, BrokenPipeError):
            server.closed_by_client = server.attack_at is not None
        except (OSError, RuntimeError, ValueError) as error:
            server.failures.append(type(error).__name__)
        finally:
            server.finished.set()


class MaliciousServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, case, transport):
        super().__init__(("127.0.0.1", 0), MaliciousWebSocket)
        self.case = case
        self.transport = transport
        self.protocol = "nfc1.stream.v1"
        self.connections = 0
        self.binary_sent = 0
        self.attack_at = None
        self.closed_by_client = False
        self.failures = []
        self.upgraded = threading.Event()
        self.allow_response = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


def mutation(port):
    def apply(server):
        server["routes"].insert(0, {
            "match": [{"path": ["/api/realtime"]}],
            "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": f"127.0.0.1:{port}"}],
                        "transport": {"protocol": "http", "versions": ["1.1"]}}],
            "terminal": True,
        })
    return apply


def run_case(args, base, protocol, case, listener):
    run = base / f"{protocol}-{listener}-{case}"
    run.mkdir(mode=0o700)
    fixture.issue_certificates(run)
    target = fixture.TargetServer()
    malicious = MaliciousServer(case, args.transport)
    processes = []
    try:
        user, password = fixture.fixture_credentials()
        args.server_mutator = mutation(malicious.server_address[1])
        caddy, port = fixture.start_caddy(args, run, protocol, target.server_address[1], user, password)
        processes.append(caddy)
        client, ports = fixture.start_client(args, run, "client", protocol, port,
                                            args.transport, user, password, 1)
        processes.append(client)
        with fixture.open_tunnel(ports, listener, target.server_address[1]) as local:
            local.sendall(b"E")
            malicious.allow_response.set()
            fixture.require(malicious.upgraded.wait(10), "client never reached the WebSocket handshake")
            if case in ACK_CASES:
                local.sendall(b"trigger uplink cell")
            local.settimeout(8)
            try:
                fixture.require(not local.recv(1), "malformed WebSocket delivered proxy payload")
            except ConnectionResetError:
                pass
            closed_at = time.monotonic()
        fixture.require(malicious.finished.wait(6), "malicious responder did not observe shutdown")
        fixture.require(not malicious.failures and malicious.closed_by_client,
                        "client did not reject the malformed WebSocket before fixture timeout")
        fixture.require(malicious.attack_at is not None and closed_at - malicious.attack_at < 5,
                        "malformed WebSocket did not fail promptly")
        fixture.require(malicious.connections == 1, "client reconnected or replayed WebSocket establishment")
        if case in HANDSHAKE_CASES:
            fixture.require(malicious.binary_sent == 0, "handshake rejection depended on binary data")
        client.exited_cleanly()
        caddy.stop()
        stats = json.loads((run / "server-stats.json").read_text())
        requests = fixture.access_requests(run)
        fixture.require(stats["connect"] == 0 and stats["opens"] == 1 and target.accepted_connections == 1,
                        "malformed WebSocket caused fallback or duplicate target opening")
        fixture.require(stats.get("startup_completed") == 1, "WebSocket opened without completed startup")
        fixture.require(stats["requests"].get("POST /api/sync") == 20 and
                        sum(value for name, value in stats["requests"].items() if name.startswith("GET /api/") or name.startswith("GET /media/")) == 20,
                        "client resumed HTTP carriers after WebSocket failure")
        fixture.require(sum(request.get("uri") == "/api/realtime" for request in requests) == 1 and
                        sum(request.get("uri") == "/" for request in requests) == 1 and
                        not any(request.get("method") == "CONNECT" for request in requests),
                        "client replayed startup or used CONNECT after failure")
        result = {"protocol": protocol, "listener": listener, "case": case, "status": "PASS",
                  "startup_pairs": 20, "websocket_attempts": 1, "target_connections": 1,
                  "handshake_only_rejection": case in HANDSHAKE_CASES, "outer_connects": 0}
        fixture.private_json(run / "result.json", result)
        print(f"PASS {protocol}/{listener}: reject {case}", flush=True)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        malicious.close()
        target.close()


def main():
    parser = argparse.ArgumentParser(description="Fail-closed native hybrid WebSocket checks behind trusted Caddy TLS.")
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--listener", choices=("socks", "http", "both"), default="socks")
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--transport", choices=("no-connect",),
                        default="no-connect")
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    root = (args.work_dir or args.objdir / "naivefox-fixture").resolve()
    fixture.require(root.is_relative_to(args.objdir), "work directory must stay below objdir")
    root.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    run = Path(tempfile.mkdtemp(prefix="hybrid-adversarial-", dir=root))
    try:
        results = [run_case(args, run, protocol, case, listener)
                   for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,))
                   for listener in (("socks", "http") if args.listener == "both" else (args.listener,))
                   for case in (args.case or CASES)]
        fixture.private_json(run / "result.json", {"status": "PASS", "cases": results})
        print(f"Private fixture and sanitized result: {run}")
    except (OSError, RuntimeError) as error:
        print(f"FAIL: {error}. Private diagnostics: {run}", flush=True)
        return 1
    finally:
        os.umask(previous_umask)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
