#!/usr/bin/env python3
"""Native packet-carrier correctness; this is not real-provider acceptance."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import transport_fixture as f


def public_pin(certificate):
    public = subprocess.check_output(
        ["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"],
        stderr=subprocess.DEVNULL,
    )
    der = subprocess.check_output(
        ["openssl", "pkey", "-pubin", "-outform", "DER"],
        input=public, stderr=subprocess.DEVNULL,
    )
    return hashlib.sha256(der).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "caddy", "objdir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cdn-proxy", type=Path)
    parser.add_argument("--cdn-origin-protocol", choices=("h1", "h2"), default="h2")
    args = parser.parse_args()
    args.cdn_mode = "packet-loss"
    f.require(
        os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
        "use an isolated network namespace",
    )
    args.objdir = args.objdir.resolve(strict=True)
    args.output = args.output.resolve()
    args.runtime = args.runtime.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    f.require(args.output.is_relative_to(args.objdir) and not args.output.exists(),
              "new output must be below objdir")
    run = args.output
    run.mkdir(mode=0o700, parents=True)
    os.environ.update(TMPDIR=str(run), XDG_RUNTIME_DIR=str(run))
    tempfile.tempdir = str(run)
    f.issue_certificates(run)
    inner = run / "inner"
    inner.mkdir(mode=0o700)
    f.issue_certificates(inner)
    pin = public_pin(inner / "server.crt")
    f.require(pin != public_pin(run / "server.crt"),
              "inner origin identity must differ from outer edge identity")
    user, password = f.fixture_credentials()
    target = f.TargetServer()
    processes = []

    def configure(server):
        pending = [item for route in server["routes"]
                   for item in route.get("handle", [])]
        while pending:
            item = pending.pop()
            if item.get("handler") == "naivefox_transport":
                item["packet_certificate"] = str(inner / "server.crt")
                item["packet_key"] = str(inner / "server.key")
            for route in item.get("routes", []):
                pending.extend(route.get("handle", []))

    args.server_mutator = configure
    try:
        server, port = f.start_caddy(
            args, run, "h2", target.server_address[1], user, password)
        processes.append(server)
        client, ports = f.start_client(
            args, run, "valid", "cdn", port, user + "~" + pin, password)
        processes.append(client)
        f.exercise(ports, target.server_address[1], "cdn")
        for frontend in ("socks", "http"):
            f.response_first_half_close(ports, frontend, target)
        f.concurrent_open_streams(ports, target.server_address[1])
        f.cancel_stream(ports, target.server_address[1])
        f.download(ports, "http", target.server_address[1], 65536)
        f.echo_wake(ports, "http", target.server_address[1], idle_seconds=12)
        client.stop()
        f.require(client.exited_cleanly(), "packet client shutdown failed")
        for name, credential, supplied_pin, trusted in (
            ("wrong-pin", password, ("1" if pin[0] == "0" else "0") + pin[1:], True),
            ("wrong-auth", password + "wrong", pin, True),
            ("untrusted-edge", password, pin, False),
        ):
            before = target.accepted_connections
            rejected, rejected_ports = f.start_client(
                args, run, name, "cdn", port, user + "~" + supplied_pin,
                credential, trusted=trusted)
            processes.append(rejected)
            for frontend in ("socks", "http"):
                f.open_tunnel(rejected_ports, frontend,
                              target.server_address[1], rejected=True)
            rejected.stop()
            f.require(rejected.exited_cleanly() and
                      target.accepted_connections == before,
                      "rejected packet client reached target")
        server.stop()
        f.require(server.exited_cleanly(), "packet Caddy shutdown failed")
        stats = json.loads((run / "server-stats.json").read_text())
        f.require(stats["packet_opened"] >= 2 and stats["packet_uploads"] > 0 and
                  stats["packet_downloads"] > 0 and stats["ws_opened"] == 0 and
                  stats["h3_opened"] == 0 and not target.failures,
                  "packet selection/integrity proof failed")
        if args.cdn_proxy:
            edge = json.loads((run / "cdn-stats.json").read_text())
            f.require(edge.get("packet_lost_responses", 0) >= 3 and
                      edge.get("packet_cut_downloads", 0) >= 1 and
                      edge.get("packet_reset_connections", 0) >= 1 and
                      edge.get("packet_reframed_uploads", 0) >= 1 and
                      edge.get("packet_reordered", 0) >= 1 and
                      edge.get("websockets", 0) == 0 and
                      edge.get("cookie_errors", 0) == 0,
                      "CDN fault injection did not execute cleanly")
        result = {"passed": True, "packet_opened": stats["packet_opened"],
                  "opens": stats["opens"], "frontends": ["http", "socks"],
                  "wrong_pin_rejected": True, "outer_tls_verified": True,
                  "provider_acceptance": False}
        f.private_json(run / "result.json", result)
        print(json.dumps(result), flush=True)
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


if __name__ == "__main__":
    main()
