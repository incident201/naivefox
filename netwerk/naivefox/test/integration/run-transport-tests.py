#!/usr/bin/env python3
"""Exercise the sole NaiveFox transport with the matching Caddy module."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import transport_fixture as f


def run_protocol(args, protocol):
    run = args.output / protocol
    run.mkdir(mode=0o700)
    f.issue_certificates(run)
    user, password = f.fixture_credentials()
    target = f.TargetServer()
    processes = []
    capture = None
    capture_log = (run / "capture.log").open("wb")
    try:
        server, port = f.start_caddy(
            args, run, protocol, target.server_address[1], user, password
        )
        processes.append(server)
        capture = subprocess.Popen(
            [
                "tcpdump",
                "-i",
                "lo",
                "--immediate-mode",
                "-B",
                "4096",
                "-s",
                "96",
                "-U",
                "-w",
                str(run / "outer.pcap"),
                f"port {port}",
            ],
            stdout=capture_log,
            stderr=capture_log,
        )
        time.sleep(0.2)
        f.require(capture.poll() is None, "capture did not start")
        client, ports = f.start_client(
            args, run, "valid", protocol, port, user, password
        )
        processes.append(client)
        transition_ms = (
            f.verify_idle_h3_transition(run, ports, target.server_address[1], server)
            if protocol == "h3"
            else None
        )
        f.exercise(ports, target.server_address[1], protocol)
        for listener in ("socks", "http"):
            f.response_first_half_close(ports, listener, target)
        f.concurrent_open_streams(ports, target.server_address[1])
        f.cancel_stream(ports, target.server_address[1])
        # A cancellation must not damage another stream on the same carrier.
        f.download(ports, "http", target.server_address[1], 65536)
        client.stop()
        f.require(client.exited_cleanly(), "valid client did not stop cleanly")
        for name, trusted, credential in (
            ("invalid-auth", True, password + "wrong"),
            ("untrusted-ca", False, password),
        ):
            before = target.accepted_connections
            rejected, rejected_ports = f.start_client(
                args, run, name, protocol, port, user, credential, trusted=trusted
            )
            processes.append(rejected)
            for listener in ("socks", "http"):
                f.open_tunnel(
                    rejected_ports, listener, target.server_address[1], rejected=True
                )
            rejected.stop()
            f.require(
                rejected.exited_cleanly() and target.accepted_connections == before,
                "rejected session reached target or failed shutdown",
            )
        if protocol == "h3":
            rule = ["iptables", "-p", "udp", "--dport", str(port), "-j", "DROP"]
            subprocess.run([rule[0], "-I", "OUTPUT", *rule[1:]], check=True)
            try:
                blocked, blocked_ports = f.start_client(
                    args, run, "udp-blocked", protocol, port, user, password
                )
                processes.append(blocked)
                before = target.accepted_connections
                f.open_tunnel(
                    blocked_ports,
                    "socks",
                    target.server_address[1],
                    rejected=True,
                    timeout=40,
                )
                blocked.stop()
                f.require(
                    blocked.exited_cleanly() and target.accepted_connections == before,
                    "blocked UDP fell back or did not stop",
                )
            finally:
                subprocess.run([rule[0], "-D", "OUTPUT", *rule[1:]], check=True)
        time.sleep(0.3)
        capture.send_signal(signal.SIGINT)
        capture.wait(timeout=10)
        capture_log.flush()
        text = subprocess.check_output(
            ["tcpdump", "-nn", "-r", str(run / "outer.pcap")],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        tcp = sum("Flags [" in line for line in text.splitlines())
        udp = sum(" UDP," in line for line in text.splitlines())
        f.require(
            "0 packets dropped by kernel" in (run / "capture.log").read_text(),
            "capture dropped packets",
        )
        f.require(
            (udp > 0 and tcp == 0) if protocol == "h3" else (tcp > 0 and udp == 0),
            "outer protocol proof failed",
        )
        server.stop()
        f.require(server.exited_cleanly(), "Caddy did not stop cleanly")
        stats = json.loads((run / "server-stats.json").read_text())
        f.require(
            stats["opens"] >= 50 and not target.failures,
            "target integrity or stream count failed",
        )
        f.validate_carrier_stats(stats, protocol)
        result = {
            "protocol": protocol,
            "idle_h3_transition_ms": transition_ms,
            "passed": True,
            "opens": stats["opens"],
            "response_first_half_closes": target.response_first_completed,
            "outer_tcp_packets": tcp,
            "outer_udp_packets": udp,
        }
        f.private_json(run / "result.json", result)
        print(json.dumps(result), flush=True)
    finally:
        if capture and capture.poll() is None:
            capture.send_signal(signal.SIGINT)
            capture.wait(timeout=10)
        capture_log.close()
        for process in reversed(processes):
            process.stop()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "caddy", "objdir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    args = parser.parse_args()
    f.require(
        os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
        "use an isolated network namespace",
    )
    args.output = args.output.resolve()
    args.objdir = args.objdir.resolve(strict=True)
    args.runtime = args.runtime.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    f.require(
        args.output.is_relative_to(args.objdir) and not args.output.exists(),
        "new output directory must be below objdir",
    )
    args.output.mkdir(parents=True, mode=0o700)
    os.environ.update(TMPDIR=str(args.output), XDG_RUNTIME_DIR=str(args.output))
    tempfile.tempdir = str(args.output)
    for protocol in ("h2", "h3") if args.protocol == "both" else (args.protocol,):
        run_protocol(args, protocol)


if __name__ == "__main__":
    main()
