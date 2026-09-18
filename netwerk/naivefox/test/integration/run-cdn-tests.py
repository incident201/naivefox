#!/usr/bin/env python3
"""Verify H2/WSS through a TLS reverse proxy before public CDN staging."""

import argparse
import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import transport_fixture as f


HEALTHY = {"normal", "replay"}
FAULTS = ("mime", "encoding", "truncated", "extra", "snapshot",
          "challenge", "redirect", "rate", "server-error", "cookie-overflow")


def run_case(args, protocol, mode):
    run = args.output / f"{protocol}-{mode}"
    run.mkdir(mode=0o700)
    f.issue_certificates(run)
    user, password = f.fixture_credentials()
    target = f.TargetServer()
    processes = []
    inputs = copy.copy(args)
    inputs.cdn_proxy = None

    def trust(server):
        server["trusted_proxies"] = {
            "source": "static", "ranges": ["127.0.0.2/32", "127.0.0.3/32"]}
        server["trusted_proxies_strict"] = 1
        server["client_ip_headers"] = ["CF-Connecting-IP"]
    inputs.server_mutator = trust

    def start_edge(port, name):
        proxy = f.Process([
            str(args.cdn_proxy), "--listen", f"127.0.0.1:{port}",
            "--origin", f"https://127.0.0.1:{origin_port}",
            "--origin-protocol", protocol, "--mode", mode,
            "--cert", str(run / "server.crt"), "--key", str(run / "server.key"),
            "--ca", str(run / "ca.crt"), "--stats", str(run / f"{name}-stats.json")
        ], run, name, os.environ.copy())
        processes.append(proxy)
        f.wait_until(lambda: f.socket_listeners(port), "edge did not start", proxy)
        return proxy

    try:
        server, origin_port = f.start_caddy(
            inputs, run, "h2", target.server_address[1], user, password)
        processes.append(server)
        edge_port = f.free_port()
        edge = start_edge(edge_port, "edge")
        client, ports = f.start_client(args, run, "client", "h2", edge_port, user, password)
        processes.append(client)
        if mode in HEALTHY:
            f.exercise(ports, target.server_address[1], f"CDN/{protocol}/{mode}")
            f.wait_until(
                lambda: "NaiveFox websocket ready startup=20" in client.log_path.read_text(errors="replace"),
                "carrier did not reach WebSocket", client, timeout=30)
            for listener in ("socks", "http"):
                f.response_first_half_close(ports, listener, target)
            f.concurrent_open_streams(ports, target.server_address[1])
            f.cancel_stream(ports, target.server_address[1])
            f.download(ports, "http", target.server_address[1], 65536)
            if mode == "normal" and protocol == "h2":
                f.echo_wake(ports, "socks", target.server_address[1], idle_seconds=30)
                tunnel = f.open_tunnel(ports, "http", target.server_address[1])
                tunnel.sendall(b"R")
                f.require(f.receive(tunnel, 5) == b"ready", "pre-restart echo")
                edge.stop()
                tunnel.settimeout(5)
                try:
                    ended = not tunnel.recv(1)
                except (ConnectionResetError, BrokenPipeError):
                    ended = True
                tunnel.close()
                f.require(ended, "edge restart did not close affected stream")
                edge = start_edge(edge_port, "edge-restarted")
                f.download(ports, "http", target.server_address[1], 65536)
                f.wait_until(
                    lambda: client.log_path.read_text(errors="replace").count(
                        "NaiveFox websocket ready startup=20") >= 3,
                    "replacement carrier did not complete startup", client, timeout=30)
                f.require(edge.process.poll() is None, "replacement edge exited")
        else:
            for listener in ("socks", "http"):
                f.open_tunnel(ports, listener, target.server_address[1], rejected=True)
            f.require(target.accepted_connections == 0, "invalid startup opened a target")
        client.stop()
        f.require(client.exited_cleanly(), "client shutdown failed")
        edge.stop()
        server.stop()
        f.require(server.exited_cleanly(), "origin shutdown failed")
        aggregate = {}
        for path in run.glob("edge*-stats.json"):
            for name, count in json.loads(path.read_text()).items():
                aggregate[name] = aggregate.get(name, 0) + count
        f.require(aggregate.get("edge_HTTP/2.0", 0) > 0, "client did not use edge H2")
        f.require(not aggregate.get("cookie_errors") and
                  not aggregate.get("cookie_path_errors"), "cookie propagation failed")
        if mode in HEALTHY:
            f.require(aggregate.get("origin_HTTP/2.0" if protocol == "h2" else "origin_HTTP/1.1", 0) > 0,
                      "origin protocol not exercised")
            f.require(aggregate.get("websockets", 0) >= 2 and
                      aggregate.get("length_removed", 0) >= 20 and
                      aggregate.get("cookie_updates", 0) >= 20, "fixture mechanism missing")
            f.require(not aggregate.get("proxy_errors") and not target.failures,
                      "proxy or target failed")
            if mode == "replay":
                f.require(aggregate.get("replays", 0) >= 80 and
                          not aggregate.get("replay_mismatch"), "replay not verified")
            stats = json.loads((run / "server-stats.json").read_text())
            f.validate_carrier_stats(stats, "h2")
        result = {"origin_protocol": protocol, "scenario": mode, "status": "PASS",
                  "edge": aggregate, "target_connections": target.accepted_connections,
                  "response_first_half_closes": target.response_first_completed}
        f.private_json(run / "result.json", result)
        print(json.dumps(result), flush=True)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "caddy", "cdn-proxy", "objdir", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--origin-protocol", choices=("h1", "h2", "both"), default="both")
    parser.add_argument("--scenario", choices=("all", *HEALTHY, *FAULTS), default="all")
    args = parser.parse_args()
    f.require(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
              "use an isolated network namespace")
    for name in ("runtime", "caddy", "cdn_proxy", "objdir"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output = args.output.resolve()
    f.require(args.output.is_relative_to(args.objdir) and not args.output.exists(),
              "new output must be below objdir")
    args.output.mkdir(mode=0o700, parents=True)
    os.environ.update(TMPDIR=str(args.output), XDG_RUNTIME_DIR=str(args.output))
    tempfile.tempdir = str(args.output)
    protocols = ("h1", "h2") if args.origin_protocol == "both" else (args.origin_protocol,)
    modes = ("normal", "replay", *FAULTS) if args.scenario == "all" else (args.scenario,)
    results = [run_case(args, protocol, mode) for protocol in protocols for mode in modes]
    f.private_json(args.output / "results.json", {"status": "PASS", "cases": results})


if __name__ == "__main__":
    main()
