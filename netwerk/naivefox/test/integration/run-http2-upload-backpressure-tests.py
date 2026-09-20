#!/usr/bin/env python3
"""Exercise finite HTTPS uploads with a deliberately small TCP send buffer."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import transport_fixture as f


def command(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def shape(port):
    command("tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:",
            "prio", "bands", "3", "priomap", *(["0"] * 16))
    for parent, handle in (("1:2", "20:"), ("1:3", "30:")):
        command("tc", "qdisc", "add", "dev", "lo", "parent", parent,
                "handle", handle, "netem", "delay", "20ms",
                "rate", "20mbit", "limit", "10000")
    for priority, (direction, flow) in enumerate(
        (("dport", "1:2"), ("sport", "1:3")), start=10
    ):
        command("tc", "filter", "add", "dev", "lo", "protocol", "ip",
                "parent", "1:", "prio", str(priority), "u32",
                "match", "ip", "protocol", "6", "0xff",
                "match", "ip", direction, str(port), "0xffff", "flowid", flow)


def diagnostic_client(args, directory, config, env, ports):
    env.update(
        MOZ_LOG="nsHttp:4,timestamp",
        MOZ_LOG_FILE=str(directory / "http.log"),
        SSLKEYLOGFILE=str(directory / "tls.keys"),
    )
    client = f.Process(
        [str(args.runtime), str(directory / "config.json")],
        directory, "client", env,
    )
    try:
        f.wait_until(
            lambda: "HTTP CONNECT listening" in client.log_path.read_text(),
            "client listeners did not start", client,
        )
    except Exception:
        client.stop()
        raise
    return client, ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "caddy", "objdir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=8, choices=range(1, 33))
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    f.require(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
              "use an isolated network namespace")
    for name in ("runtime", "caddy", "objdir"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output = args.output.resolve()
    f.require(args.output.is_relative_to(args.objdir) and not args.output.exists(),
              "new output must be below objdir")
    args.output.mkdir(mode=0o700, parents=True)
    run = args.output
    os.environ.update(TMPDIR=str(run), XDG_RUNTIME_DIR=str(run))
    tempfile.tempdir = str(run)
    if args.diagnostics:
        os.environ["GODEBUG"] = "http2debug=2"
        args.client_factory = diagnostic_client
    f.issue_certificates(run)
    target = f.TargetServer()
    processes = []
    previous_wmem = subprocess.check_output(
        ["sysctl", "-n", "net.ipv4.tcp_wmem"], text=True
    ).strip()
    durations = []
    try:
        command("sysctl", "-qw", "net.ipv4.tcp_wmem=4096 16384 16384")
        user, password = f.fixture_credentials()
        server, port = f.start_caddy(
            args, run, "packet", target.server_address[1], user, password
        )
        processes.append(server)
        shape(port)
        client, ports = f.start_client(
            args, run, "native", "packet", port, user, password
        )
        processes.append(client)
        for index in range(args.rounds):
            frontend = "socks" if index % 2 == 0 else "http"
            f.download(ports, frontend, target.server_address[1], length=8*1024*1024)
            started = time.monotonic()
            f.upload(ports, frontend, target.server_address[1], length=1024*1024)
            elapsed = time.monotonic() - started
            durations.append(elapsed)
            f.private_json(run / "progress.json", {"upload_seconds": durations})
            print(json.dumps({"round": index, "frontend": frontend,
                              "upload_seconds": elapsed}), flush=True)
            f.require(elapsed < 8, "HTTPS upload stalled under bounded backpressure")
        client.stop()
        client.exited_cleanly()
        server.stop()
        server.exited_cleanly()
        slow_uploads = 0
        for line in (run / "access.jsonl").read_text().splitlines():
            request = json.loads(line)
            if "/upload/" in request.get("request", {}).get("uri", ""):
                slow_uploads += request.get("duration", 0) >= 8
        f.require(slow_uploads == 0 and not target.failures,
                  "stalled HTTP upload or target integrity failure")
        f.private_json(run / "result.json", {
            "passed": True, "rounds": args.rounds, "upload_seconds": durations,
            "slow_upload_requests": slow_uploads,
            "scope": "local HTTP/2 socket-backpressure regression",
        })
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()
        subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        command("sysctl", "-qw", "net.ipv4.tcp_wmem=" + previous_wmem)


if __name__ == "__main__":
    main()
