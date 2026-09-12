#!/usr/bin/env python3
"""Verified native socket performance screen, without traffic camouflage claims."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("optimization_native", HERE / "run-no-connect-tests.py")
native = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = native
spec.loader.exec_module(native)
require = native.require


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout


class Link:
    def __init__(self, port, rtt, rate):
        self.port, self.rtt, self.rate = port, rtt, rate
        self.installed = False

    def install(self):
        require(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
                "refusing host network namespace mutation")
        require(os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED") == "1",
                "use the isolated network runner")
        if not self.rtt and not self.rate:
            return
        command("tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "prio",
                "bands", "3", "priomap", *(["0"] * 16))
        self.installed = True
        for parent, handle in (("1:2", "20:"), ("1:3", "30:")):
            args = ["tc", "qdisc", "add", "dev", "lo", "parent", parent, "handle", handle,
                    "netem", "limit", "10000"]
            if self.rtt:
                args += ["delay", f"{self.rtt / 2:g}ms"]
            if self.rate:
                args += ["rate", f"{self.rate:g}mbit"]
            command(*args)
        priority = 10
        for protocol in ("6", "17"):
            for direction, flow in (("dport", "1:2"), ("sport", "1:3")):
                command("tc", "filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:",
                        "prio", str(priority), "u32", "match", "ip", "protocol", protocol,
                        "0xff", "match", "ip", direction, str(self.port), "0xffff", "flowid", flow)
                priority += 1

    def snapshot(self):
        value = json.loads(command("tc", "-j", "-s", "qdisc", "show", "dev", "lo"))
        require(all(item.get("drops", 0) == 0 for item in value), "shaping queue dropped packets")
        return value

    def close(self):
        if self.installed:
            command("tc", "qdisc", "del", "dev", "lo", "root")


class Target(native.Target):
    def handle(self):
        try:
            self.request.settimeout(120)
            kind = native.receive(self.request, 1)
            if kind == b"E":
                while chunk := self.request.recv(65536):
                    self.request.sendall(chunk)
                self.request.shutdown(socket.SHUT_WR)
                return
            length = struct.unpack("!I", native.receive(self.request, 4))[0]
            require(0 < length <= 256 * 1024 * 1024, "target byte bound")
            if kind == b"D":
                native.send_payload(self.request, length)
            else:
                require(kind == b"U", "unknown transfer")
                digest = hashlib.sha256()
                remaining = length
                while remaining:
                    data = self.request.recv(min(65536, remaining))
                    require(data, "truncated upload")
                    digest.update(data)
                    remaining -= len(data)
                require(not self.request.recv(1), "upload FIN missing")
                self.request.sendall(struct.pack("!Q", length) + digest.digest())
            self.request.shutdown(socket.SHUT_WR)
        except (OSError, RuntimeError):
            self.server.failures.append("target transfer failed")


def transfer(ports, listener, target, length, upload=False, started=None):
    with native.open_tunnel(ports, listener, target, timeout=120) as sock:
        begin = time.perf_counter()
        sock.sendall((b"U" if upload else b"D") + struct.pack("!I", length))
        if started:
            started.set()
        if upload:
            native.send_payload(sock, length)
            sock.shutdown(socket.SHUT_WR)
            result = native.receive(sock, 40)
            require(not sock.recv(1), "upload EOF missing")
            ended = time.perf_counter()
            require(result == struct.pack("!Q", length) + native.payload_digest(length),
                    "upload digest differs")
        else:
            sock.shutdown(socket.SHUT_WR)
            digest = hashlib.sha256()
            count = 0
            while data := sock.recv(65536):
                count += len(data)
                require(count <= length, "excess download")
                digest.update(data)
            ended = time.perf_counter()
            require(count == length and digest.digest() == native.payload_digest(length),
                    "download digest differs")
    elapsed = ended - begin
    return {"bytes": length, "ms": elapsed * 1000, "mbps": length * 8 / elapsed / 1e6,
            "verified": True}


def echo(sock, initial=False):
    payload = native.BLOCK[:64]
    started = time.perf_counter()
    sock.sendall((b"E" if initial else b"") + payload)
    result = native.receive(sock, len(payload))
    ended = time.perf_counter()
    require(result == payload, "echo differs")
    return (ended - started) * 1000


def distribution(values):
    ordered = sorted(values)
    return {"median": statistics.median(values),
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * .95))],
            "samples": values}


def sample(args, index):
    directory = args.work_dir / f"sample-{index:03d}"
    directory.mkdir(mode=0o700)
    native.issue_certificates(directory)
    target = native.TargetServer()
    target.RequestHandlerClass = Target
    user, password = native.fixture_credentials()
    client = caddy = link = None
    result = {"block": index, "admitted": False}
    try:
        caddy, port = native.start_caddy(args, directory, args.protocol,
                                       target.server_address[1], user, password)
        link = Link(port, args.rtt_ms, args.mbps)
        link.install()
        tcp_rtt = []
        for _ in range(3):
            begin = time.perf_counter()
            with socket.create_connection(("127.0.0.1", port), timeout=10):
                tcp_rtt.append((time.perf_counter() - begin) * 1000)
        result["tcp_connect_ms"] = distribution(tcp_rtt)
        client, ports = native.start_client(args, directory, "client", args.protocol, port,
                                            "no-connect", user, password, 0)
        target_port = target.server_address[1]
        begin_wall = time.time()
        begin = time.perf_counter()
        with native.open_tunnel(ports, args.listener, target_port, timeout=120) as sock:
            echo(sock, True)
            result["cold_echo_ms"] = (time.perf_counter() - begin) * 1000
            sock.shutdown(socket.SHUT_WR)
            require(not sock.recv(1), "cold echo EOF missing")
        native.wait_until(lambda: "No-connect websocket ready startup=" in
                          client.log_path.read_text(errors="replace"),
                          "WebSocket startup incomplete", client, timeout=120)
        result["carrier_ready_ms"] = (time.perf_counter() - begin) * 1000
        ready = re.search(r"\[(\d{4})/(\d{6})\.(\d{6}):[^\]]+\] No-connect websocket ready startup=",
                          client.log_path.read_text(errors="replace"))
        require(ready is not None, "WebSocket event timestamp missing")
        stamp = time.strptime(str(time.localtime(begin_wall).tm_year) + ready[1] + ready[2],
                              "%Y%m%d%H%M%S")
        ready_wall = time.mktime(stamp) + int(ready[3]) / 1e6
        result["websocket_ready_ms"] = (ready_wall - begin_wall) * 1000
        require(0 <= result["websocket_ready_ms"] <= result["carrier_ready_ms"] + 10,
                "WebSocket event clock differs")
        for upload, name, mib in ((False, "download", args.bulk_mib),
                                  (True, "upload", args.upload_mib)):
            result[name] = transfer(ports, args.listener, target_port, mib * 1024 * 1024, upload)
        with native.open_tunnel(ports, args.listener, target_port, timeout=120) as sock:
            echo(sock, True)
            result["warm_echo_ms"] = distribution([echo(sock) for _ in range(20)])
            if args.mixed_mib:
                started = threading.Event()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    job = pool.submit(transfer, ports, args.listener, target_port,
                                      args.mixed_mib * 1024 * 1024, False, started)
                    require(started.wait(30), "mixed bulk did not start")
                    time.sleep(.1)
                    mixed, opens = [], []
                    for _ in range(10):
                        if job.done():
                            break
                        mixed.append(echo(sock))
                        if job.done():
                            break
                        begin_open = time.perf_counter()
                        with native.open_tunnel(ports, args.listener, target_port, timeout=120) as short:
                            echo(short, True)
                            opens.append((time.perf_counter() - begin_open) * 1000)
                            short.shutdown(socket.SHUT_WR)
                            require(not short.recv(1), "short echo EOF missing")
                    result["mixed_download"] = job.result()
                    require(mixed and opens, "bulk ended before latency probes")
                    result["mixed_echo_ms"] = distribution(mixed)
                    result["mixed_open_echo_ms"] = distribution(opens)
            sock.shutdown(socket.SHUT_WR)
            require(not sock.recv(1), "echo EOF missing")
        time.sleep(.2)
        client.stop()
        require(client.process.returncode == 0, "client shutdown failed")
        caddy.stop()
        require(caddy.process.returncode == 0, "server shutdown failed")
        result["qdisc"] = link.snapshot()
        stats = json.loads((directory / "server-stats.json").read_text())
        require(stats["connect"] == 0 and stats["startup_completed"] >= 1 and stats["ws_opened"] >= 1,
                "transport lifecycle differs")
        require(not target.failures, "target failure")
        result["peer_flight_bounds"] = [
            {key: peer.get(key) for key in ("receive_window", "flight_min", "flight_max")}
            for peer in stats.get("peers", [])]
        result["carrier"] = {key: value for key, value in stats.items()
                             if key.startswith("ws_") or key in ("opens", "connect", "startup_completed")}
        result["admitted"] = True
        native.private_json(directory / "result.json", result)
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("qdisc", "carrier")}), flush=True)
        return result
    except Exception as error:
        result["error"] = str(error)
        native.private_json(directory / "result.json", result)
        raise
    finally:
        for process in (client, caddy):
            if process:
                process.stop()
        if link:
            link.close()
        target.close()


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("objdir", "work-dir", "runtime", "caddy"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--carrier-profile", default="native-stream-v2")
    parser.add_argument("--protocol", choices=("h2", "h3"), default="h2")
    parser.add_argument("--listener", choices=("socks", "http"), default="socks")
    parser.add_argument("--rtt-ms", type=float, default=100)
    parser.add_argument("--mbps", type=float, default=300)
    parser.add_argument("--bulk-mib", type=int, default=32)
    parser.add_argument("--upload-mib", type=int, default=16)
    parser.add_argument("--mixed-mib", type=int, default=8)
    parser.add_argument("--blocks", type=int, default=3)
    args = parser.parse_args()
    args.transport = "no-connect"
    args.work_dir = args.work_dir.resolve()
    require(args.work_dir.is_relative_to(args.objdir.resolve()) and not args.work_dir.exists(),
            "output must be new and below objdir")
    require(1 <= args.blocks <= 10 and 0 <= args.rtt_ms <= 500 and 0 <= args.mbps <= 10000
            and 1 <= args.bulk_mib <= 256 and 1 <= args.upload_mib <= 256
            and 0 <= args.mixed_mib <= 256, "invalid screen bounds")
    os.umask(0o077)
    args.work_dir.mkdir(parents=True)
    native.private_json(args.work_dir / "plan.json", {
        "schema": "native-optimization-screen-v1", "label": args.label,
        "rtt_ms": args.rtt_ms, "mbps": args.mbps, "blocks": args.blocks,
        "protocol": args.protocol, "listener": args.listener, "carrier_profile": args.carrier_profile,
        "bulk_mib": args.bulk_mib, "upload_mib": args.upload_mib, "mixed_mib": args.mixed_mib,
        "runtime_sha256": digest(args.runtime), "libxul_sha256": digest(args.runtime.parent / "libxul.so"),
        "caddy_sha256": digest(args.caddy), "harness_sha256": digest(Path(__file__)),
        "scope": "socket benchmark; hashing included in receive time; no browser or wire-cost claim"})
    results = []
    for index in range(args.blocks):
        results.append(sample(args, index))
        native.private_json(args.work_dir / "results.json", results)


if __name__ == "__main__":
    main()
