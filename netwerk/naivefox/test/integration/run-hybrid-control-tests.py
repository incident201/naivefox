#!/usr/bin/env python3
"""Verify native control PING/PONG does not consume the NFC1 writer budget."""
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
import threading

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("control_fixture", HERE / "run-hybrid-adversarial-tests.py")
adversarial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adversarial)
fixture = adversarial.fixture


def encode(sequence, frames):
    payload = b"".join(struct.pack("!B3xIII", kind, stream, offset, len(body)) + body
                       for kind, stream, offset, body in frames)
    used = 16 + len(payload)
    fixture.require(used <= 512, "control fixture exceeds cell bound")
    return b"NFC1" + struct.pack("!IIHBB", sequence, used, len(frames), 0, 0) + payload + bytes(512-used)


class ControlPeer(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        try:
            self.request.settimeout(8)
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header.extend(fixture.receive(self.request, 1))
                fixture.require(len(header) <= 16384, "handshake exceeds bound")
            fields = dict(line.split(":", 1) for line in header.decode().split("\r\n")[1:] if line)
            key = next(value.strip() for name, value in fields.items() if name.lower() == "sec-websocket-key")
            accept = base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            protocol = "nfc1.hybrid.a1" if server.transport.endswith("asymmetric") else "nfc1.hybrid.v1"
            self.request.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: {protocol}\r\n\r\n").encode())
            down = 20
            up = 20
            received_fin = False
            fragment = bytearray()
            output_offset = 0
            def consume():
                nonlocal up, down, output_offset, received_fin, fragment
                opcode, final, body = adversarial.read_client_frame(self.request)
                if opcode == 9:
                    self.request.sendall(adversarial.frame(10, body))
                    return None
                if opcode == 10:
                    return body
                fixture.require(opcode == (0 if fragment else 2), "unexpected client close or message")
                fragment.extend(body)
                fixture.require(len(fragment) <= 262144, "fragment exceeds bound")
                if not final:
                    return None
                body = bytes(fragment)
                fragment.clear()
                sequence, used, count = struct.unpack_from("!IIH", body, 4)
                fixture.require(body[:4] == b"NFC1" and sequence == up and 16 <= used <= len(body),
                                "client cell order or length differs")
                up += 1
                frames = []
                offset = 16
                for _ in range(count):
                    kind, stream, position, size = struct.unpack_from("!B3xIII", body, offset)
                    value = body[offset+16:offset+16+size]
                    fixture.require(len(value) == size and stream == 1, "invalid logical frame")
                    offset += 16+size
                    if kind == 2:
                        fixture.require(position == 1 and value == b"after-controls", "post-control data differs")
                        server.data_verified = True
                        frames.append((2, 1, output_offset, value))
                        output_offset += len(value)
                    elif kind == 3:
                        fixture.require(position == 1+len(b"after-controls") and not value, "FIN differs")
                        frames.append((3, 1, output_offset, b""))
                        received_fin = True
                    else:
                        fixture.require(kind == 5, "unexpected stream control")
                fixture.require(offset == used, "cell used length differs")
                if frames:
                    frames.insert(0, (8, 0, sequence, b""))
                    self.request.sendall(adversarial.frame(2, encode(down, frames)))
                    down += 1
                return None

            for length in (4, 125):
                ping = bytes(range(length))
                self.request.sendall(adversarial.frame(9, ping))
                while (pong := consume()) is None:
                    pass
                fixture.require(pong == ping, "PONG payload differs")
                server.pongs += 1
                self.request.sendall(adversarial.frame(2, encode(down, [(2, 1, output_offset, b"alive")])))
                down += 1
                output_offset += 5
            while not received_fin:
                consume()
            try:
                opcode, final, body = adversarial.read_client_frame(self.request)
                while opcode in (0, 2, 9, 10):
                    if opcode == 9:
                        self.request.sendall(adversarial.frame(10, body))
                    opcode, final, body = adversarial.read_client_frame(self.request)
                fixture.require(opcode == 8, "unexpected terminal frame")
                self.request.sendall(adversarial.frame(8, body))
            except (ConnectionResetError, BrokenPipeError):
                pass
            except RuntimeError as error:
                if str(error) != "stream ended before its declared length":
                    raise
        except (OSError, RuntimeError, ValueError) as error:
            server.failures.append(type(error).__name__ + ": " + str(error))
        finally:
            server.finished.set()


def run(args, protocol, listener, transport):
    root = args.work_dir / (protocol+"-"+listener+"-"+transport)
    root.mkdir(parents=True, mode=0o700)
    fixture.issue_certificates(root)
    target = fixture.TargetServer()
    peer = socketserver.ThreadingTCPServer(("127.0.0.1", 0), ControlPeer)
    peer.daemon_threads = True
    peer.transport = transport
    peer.pongs = 0
    peer.data_verified = False
    peer.failures = []
    peer.finished = threading.Event()
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    processes = []
    try:
        args.transport = transport
        args.server_mutator = adversarial.mutation(peer.server_address[1])
        user, password = fixture.fixture_credentials()
        caddy, port = fixture.start_caddy(args, root, protocol, target.server_address[1], user, password)
        processes.append(caddy)
        client, ports = fixture.start_client(args, root, "client", protocol, port, transport, user, password, 1)
        processes.append(client)
        with fixture.open_tunnel(ports, listener, target.server_address[1]) as local:
            local.sendall(b"E")
            fixture.require(fixture.receive(local, 10) == b"alivealive", "control PING aborted the carrier")
            local.sendall(b"after-controls")
            fixture.require(fixture.receive(local, len(b"after-controls")) == b"after-controls", "echo failed after controls")
            local.shutdown(socket.SHUT_WR)
            fixture.require(not local.recv(1), "half-close failed after controls")
        client.exited_cleanly()
        fixture.require(peer.finished.wait(8) and not peer.failures, "control peer failed: "+str(peer.failures))
        fixture.require(peer.pongs == 2 and peer.data_verified, "missing legal controls or post-control data")
        result = {"protocol": protocol, "listener": listener, "transport": transport,
                  "status": "PASS", "pong_payload_bytes": [4, 125], "data_verified": True, "half_close": True}
        fixture.private_json(root / "result.json", result)
        print(json.dumps(result), flush=True)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=5)
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("objdir", "caddy", "work-dir"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--listener", choices=("socks", "http", "both"), default="both")
    parser.add_argument("--transport", choices=("no-connect-hybrid", "no-connect-hybrid-asymmetric", "both"), default="both")
    args = parser.parse_args()
    args.runtime = args.runtime or args.objdir / "dist/bin/naivefox"
    fixture.require(args.work_dir.resolve().is_relative_to(args.objdir.resolve()) and not args.work_dir.exists(),
                    "new work directory must be under objdir")
    transport = args.transport
    os.umask(0o077)
    args.work_dir.mkdir(parents=True)
    results = []
    for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,)):
        for listener in (("socks", "http") if args.listener == "both" else (args.listener,)):
            for mode in (("no-connect-hybrid", "no-connect-hybrid-asymmetric") if transport == "both" else (transport,)):
                results.append(run(args, protocol, listener, mode))
    fixture.private_json(args.work_dir / "results.json", results)


if __name__ == "__main__":
    main()
