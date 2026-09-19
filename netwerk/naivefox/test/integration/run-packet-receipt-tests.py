#!/usr/bin/env python3
"""A held upload gap must not make the client resend receipted later blocks."""

import argparse
import json
import os
from pathlib import Path
import tempfile

import transport_fixture as f


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "caddy", "cdn-proxy", "objdir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cdn-origin-protocol", choices=("h1", "h2"), default="h2")
    args = parser.parse_args()
    f.require(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
              "use an isolated network namespace")
    for name in ("runtime", "caddy", "cdn_proxy", "objdir"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output = args.output.resolve()
    f.require(args.output.is_relative_to(args.objdir) and not args.output.exists(),
              "new output must be below objdir")
    args.cdn_mode = "packet-gap"
    run = args.output
    run.mkdir(parents=True, mode=0o700)
    os.environ.update(TMPDIR=str(run), XDG_RUNTIME_DIR=str(run))
    tempfile.tempdir = str(run)
    f.issue_certificates(run)
    user, password = f.fixture_credentials()
    target = f.TargetServer()
    processes = []
    try:
        server, port = f.start_caddy(
            args, run, "packet", target.server_address[1], user, password)
        processes.append(server)
        for frontend in ("socks", "http"):
            client, ports = f.start_client(
                args, run, frontend, "packet", port, user, password)
            processes.append(client)
            f.upload(ports, frontend, target.server_address[1], length=2 * 1024 * 1024)
            f.download(ports, frontend, target.server_address[1], length=65536)
            client.stop()
            f.require(client.exited_cleanly(), "receipt client shutdown failed")
        server.stop()
        f.require(server.exited_cleanly() and not target.failures,
                  "receipt fixture failed")
        edge = json.loads((run / "cdn-stats.json").read_text())
        f.require(edge.get("packet_gap_held") == 2 and
                  edge.get("packet_gap_receipts", 0) >= 4 and
                  edge.get("packet_gap_lost_head") == 2 and
                  edge.get("packet_gap_lost_tail") == 2 and
                  edge.get("packet_gap_lost_tail_retries", 0) >= 2,
                  "gap and lost-receipt faults did not execute")
        f.require(3 <= edge.get("packet_gap_furthest", 0) <= 7 and
                  edge.get("packet_gap_outside_window", 0) == 0,
                  "receipt released the cumulative upload window")
        f.require(edge.get("packet_gap_redundant_posts", 0) == 0,
                  "client resent a block after receiving its storage receipt")
        f.require(edge.get("packet_gap_head_retries", 0) >= 2,
                  "lost head response did not recover")
        result = {"passed": True, "frontends": ["socks", "http"],
                  "held_gaps": edge["packet_gap_held"],
                  "later_receipts": edge["packet_gap_receipts"],
                  "redundant_posts": 0, "window_slots": 8}
        f.private_json(run / "result.json", result)
        print(json.dumps(result), flush=True)
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


if __name__ == "__main__":
    main()
