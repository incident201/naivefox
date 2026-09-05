#!/usr/bin/env python3
"""Native transport cost screen; no browser-equivalence or residual claims."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import socket
import struct
import sys
import threading
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("performance_matched", HERE / "run-matched-app-matrix.py")
matched = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = matched
spec.loader.exec_module(matched)
native = matched.native
require = native.require


class Target(native.Target):
    def handle(self):
        try:
            self.request.settimeout(45)
            kind = native.receive(self.request, 1)
            if kind == b"E":
                while chunk := self.request.recv(65536):
                    self.request.sendall(chunk)
                self.request.shutdown(socket.SHUT_WR)
                return
            length = struct.unpack("!I", native.receive(self.request, 4))[0]
            require(length <= 8 * 1024 * 1024, "target length exceeds bound")
            if kind == b"D":
                native.send_payload(self.request, length)
                self.request.shutdown(socket.SHUT_WR)
                return
            require(kind == b"U", "unknown job")
            digest = hashlib.sha256()
            remaining = length
            while remaining:
                chunk = self.request.recv(min(65536, remaining))
                require(bool(chunk), "truncated upload")
                digest.update(chunk)
                remaining -= len(chunk)
            require(not self.request.recv(1), "upload missed half-close")
            self.request.sendall(struct.pack("!Q", length) + digest.digest())
            self.request.shutdown(socket.SHUT_WR)
        except (OSError, RuntimeError):
            self.server.failures.append("target transfer failed")


def transfer(ports, listener, target, length, upload=False, barrier=None):
    with native.open_tunnel(ports, listener, target, timeout=90) as sock:
        if barrier:
            barrier.wait(timeout=30)
        started = time.perf_counter()
        sock.sendall((b"U" if upload else b"D") + struct.pack("!I", length))
        if upload:
            native.send_payload(sock, length)
            sock.shutdown(socket.SHUT_WR)
            result = native.receive(sock, 40)
            require(not sock.recv(1), "upload reply missed EOF")
            ended = time.perf_counter()
            require(result == struct.pack("!Q", length) + native.payload_digest(length),
                    "upload length or digest differs")
        else:
            sock.shutdown(socket.SHUT_WR)
            digest = hashlib.sha256()
            received = 0
            while chunk := sock.recv(65536):
                received += len(chunk)
                require(received <= length, "excess download bytes")
                digest.update(chunk)
            ended = time.perf_counter()
            require(received == length and digest.digest() == native.payload_digest(length),
                    "download length or digest differs")
    return {"io_ms": (ended - started) * 1000, "bytes": length, "verified": True}


def echo(sock, initial=False):
    payload = native.BLOCK[:4096]
    started = time.perf_counter()
    sock.sendall((b"E" if initial else b"") + payload)
    received = native.receive(sock, len(payload))
    ended = time.perf_counter()
    require(received == payload, "echo differs")
    return (ended - started) * 1000


def wire(directory, port):
    source = directory / "session-raw.pcapng"
    receive = directory / "session-observer.pcapng"
    relevant = f"sll.pkttype==0 && (tcp.port=={port} || udp.port=={port})"
    matched.run_quiet(["tshark", "-n", "-r", source, "-Y", relevant, "-w", receive])
    rows = matched.features.tshark_rows(str(receive), [], "", [
        "ip.len", "ip.hdr_len", "ip.src", "ip.dst", "ipv6.src",
        "tcp.srcport", "tcp.dstport", "udp.srcport", "udp.dstport", "icmp.type"])
    require(rows, "empty receive-side capture")
    up = down = 0
    for row in rows:
        require(not row["ipv6.src"], "unexpected IPv6")
        size = int(row["ip.len"].split(",")[0])
        require(20 <= size <= 1500 and row["ip.src"].split(",")[0] == "127.0.0.1"
                and row["ip.dst"].split(",")[0] == "127.0.0.1", "outer routing or MTU differs")
        from_server = (row["tcp.srcport"] or row["udp.srcport"]) == str(port)
        if row["icmp.type"]:
            from_server = not from_server
        if from_server:
            down += size
        else:
            up += size
    return {"ip_bytes": up + down, "up_ip_bytes": up, "down_ip_bytes": down,
            "packets": len(rows), "copy": "receive_after_qdisc",
            **matched.validate_tcp_termination(receive, port)}


def sample(args, root, row):
    directory = root / f"sample-{row['index']:03d}"
    directory.mkdir(mode=0o700)
    native.issue_certificates(directory)
    target = native.TargetServer()
    target.RequestHandlerClass = Target
    user, password = native.fixture_credentials()
    options = SimpleNamespace(runtime=args.runtime, caddy=args.caddy,
                              transport=row["transport"])
    caddy = client = capture = link = None
    known = {}
    result = {**row, "admitted": False}
    try:
        caddy, port = native.start_caddy(options, directory, row["protocol"],
                                       target.server_address[1], user, password)
        matched.remember_tree(caddy.process.pid, known)
        link = matched.OuterLink(port, args.link)
        link.install()
        capture = matched.CompleteCapture(directory, port)
        client, ports = native.start_client(options, directory, "client", row["protocol"],
                                            port, row["transport"], user, password, 0)
        matched.remember_tree(client.process.pid, known)
        cold_start = time.perf_counter()
        with native.open_tunnel(ports, row["listener"], target.server_address[1]) as sock:
            echo(sock, True)
            result["cold_echo_ms"] = 1000 * (time.perf_counter() - cold_start)
            sock.shutdown(socket.SHUT_WR)
            require(not sock.recv(1), "cold echo missed EOF")
        if "hybrid" in row["transport"]:
            native.wait_until(lambda: "No-connect hybrid websocket ready startup=20" in
                              client.log_path.read_text(errors="replace"),
                              "hybrid startup incomplete", client, timeout=60)
        # All arms finish the fixed startup before warm measurements.
        else:
            native.wait_until(lambda: len(native.access_requests(directory)) >= 47,
                              "finite startup incomplete", client, timeout=60)
        result["carrier_ready_ms"] = 1000 * (time.perf_counter() - cold_start)
        target_port = target.server_address[1]
        result["download"] = transfer(ports, row["listener"], target_port, 8 * 1024 * 1024)
        result["upload"] = transfer(ports, row["listener"], target_port, 1024 * 1024, True)
        barrier = threading.Barrier(4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(transfer, ports, row["listener"], target_port, 512 * 1024,
                                False, barrier) for _ in range(4)]
            values = [job.result() for job in jobs]
        result["parallel"] = {"io_ms": max(item["io_ms"] for item in values),
                              "bytes": 2 * 1024 * 1024, "verified": True, "jobs": values}
        with native.open_tunnel(ports, row["listener"], target_port) as sock:
            echo(sock, True)
            result["small_ms"] = [echo(sock) for _ in range(4)]
            time.sleep(2)
            result["wake_ms"] = echo(sock)
            sock.shutdown(socket.SHUT_WR)
            require(not sock.recv(1), "warm echo missed EOF")
        time.sleep(0.15)
        client.stop()
        require(client.process.returncode == 0, "native shutdown was not graceful")
        caddy.stop()
        require(caddy.process.returncode == 0, "Caddy shutdown was not graceful")
        result["drain"] = matched.drain_complete(capture, link, port, known, directory)
        capture.stop()
        result["wire"] = wire(directory, port)
        result["link"] = link.validate()
        stats = json.loads((directory / "server-stats.json").read_text())
        result["carrier"] = {key: value for key, value in stats.items()
                             if key.startswith("ws_") or key in (
                                 "upload_bytes", "download_bytes", "upload_filler", "download_filler",
                                 "upload_useful", "download_useful", "startup_completed", "opens", "connect")}
        require(stats["connect"] == 0 and stats["opens"] == 8, "wrong carrier routing/stream count")
        require(not target.failures and target.accepted_connections == 8, "target integrity failure")
        require(stats["startup_completed"] == 1, "fixed startup not completed")
        require(stats["ws_opened"] == (1 if "hybrid" in row["transport"] else 0),
                "wrong realtime transition")
        result["admitted"] = True
        native.private_json(directory / "result.json", result)
        print(json.dumps({key: result[key] for key in ("index", "protocol", "listener", "transport",
                         "cold_echo_ms", "download", "upload", "parallel", "small_ms", "wake_ms", "wire")}), flush=True)
        return result
    except Exception as error:
        result["error"] = str(error)
        native.private_json(directory / "result.json", result)
        raise
    finally:
        for process in (client, caddy):
            if process:
                process.stop()
        if capture and not capture.output.closed:
            capture.stop()
        if link:
            link.close()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("objdir", "work-dir", "runtime", "caddy"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--listener", choices=("socks", "http", "both"), default="both")
    parser.add_argument("--transport", nargs="+", default=["no-connect", "no-connect-hybrid",
                                                          "no-connect-hybrid-asymmetric"])
    parser.add_argument("--link", choices=("loopback", "rtt40-20mbps"), required=True)
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    matched.assert_isolated_namespace()
    root = args.work_dir.resolve()
    require(root.is_relative_to(args.objdir.resolve()) and not root.exists(), "new output must be below objdir")
    require(1 <= args.blocks <= 10, "bounded screening blocks required")
    os.umask(0o077)
    root.mkdir(parents=True)
    args.runtime = args.runtime.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    randomizer = random.Random(args.seed)
    schedule = []
    for block in range(args.blocks):
        rows = [{"block": block, "protocol": protocol, "listener": listener, "transport": transport}
                for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,))
                for listener in (("socks", "http") if args.listener == "both" else (args.listener,))
                for transport in args.transport]
        randomizer.shuffle(rows)
        schedule.extend(rows)
    for index, row in enumerate(schedule):
        row["index"] = index
    native.private_json(root / "plan.json", {"schema": "native-transport-cost-screen-v1",
        "seed": args.seed, "link": args.link, "schedule": schedule,
        "harness_sha256": matched.digest(Path(__file__)),
        "runtime_sha256": matched.digest(args.runtime),
        "libxul_sha256": matched.digest(args.runtime.parent / "libxul.so"),
        "caddy_sha256": matched.digest(args.caddy), "browser_equivalence_claim": False})
    results = []
    for row in schedule:
        results.append(sample(args, root, row))
        native.private_json(root / "results.json", results)


if __name__ == "__main__":
    main()
