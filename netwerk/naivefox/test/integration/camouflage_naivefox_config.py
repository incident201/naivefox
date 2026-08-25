#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

PREAMBLE_PATH = "/camouflage/index.html"
PREAMBLE_MAX_BYTES = 64 * 1024
TREE_PREAMBLE_MAX_BYTES = 256 * 1024
TREE_PREAMBLE_MAX_ASSETS = 2


def build_config(
    arm,
    protocol,
    socks_port,
    proxy_port,
    proxy_user,
    proxy_pass,
    diagnostic_first_socks_tunnel_urgent_start=False,
):
    supported_arms = (
        "off",
        "gate",
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-overlap",
        "document-start-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-overlap",
    )
    if arm not in supported_arms:
        raise ValueError(
            "config arm must be off, gate, root, root-pmtud-control, "
            "document-complete, document-overlap, document-start-overlap, "
            "tree-complete, tree-complete-css, tree-early-overlap, "
            "tree-root-overlap, tree-root-overlap-css, or "
            "tree-overlap"
        )
    if protocol not in ("h2", "h3"):
        raise ValueError("protocol must be h2 or h3")
    if arm == "root-pmtud-control" and protocol != "h3":
        raise ValueError("root-pmtud-control requires h3")
    for name, port in (("SOCKS", socks_port), ("proxy", proxy_port)):
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise ValueError(f"{name} port is outside 1..65535")
    if not proxy_user or not proxy_pass:
        raise ValueError("proxy credentials must be non-empty")

    scheme = "https" if protocol == "h2" else "quic"
    user = quote(proxy_user, safe="")
    password = quote(proxy_pass, safe="")
    preamble = {"mode": "off"}
    if arm in (
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-overlap",
        "document-start-overlap",
    ):
        preamble = {
            "mode": (
                arm
                if arm in ("document-overlap", "document-start-overlap")
                else "document-complete"
            ),
            "path": PREAMBLE_PATH,
            "max-bytes": PREAMBLE_MAX_BYTES,
        }
    elif arm in (
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-overlap",
    ):
        preamble = {
            "mode": {
                "tree-complete-css": "tree-complete",
                "tree-root-overlap-css": "tree-root-overlap",
            }.get(arm, arm),
            "path": PREAMBLE_PATH,
            "max-assets": (
                1
                if arm in ("tree-complete-css", "tree-root-overlap-css")
                else TREE_PREAMBLE_MAX_ASSETS
            ),
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
        }
    config = {
        "listen": f"socks://127.0.0.1:{socks_port}",
        "proxy": f"{scheme}://{user}:{password}@localhost:{proxy_port}",
        "host-resolver-rules": "MAP localhost 127.0.0.1",
        "outer-session-gate": arm != "off",
        "preamble": preamble,
        "log": "",
    }
    if diagnostic_first_socks_tunnel_urgent_start:
        config["diagnostic-first-socks-tunnel-urgent-start"] = True
    return config


def write_config(path, config):
    destination = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(destination, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(config, stream, separators=(",", ":"), sort_keys=True)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--arm",
        choices=(
            "off",
            "gate",
            "root",
            "root-pmtud-control",
            "document-complete",
            "document-overlap",
            "document-start-overlap",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-overlap",
        ),
        required=True,
    )
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--socks-port", type=int, required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument(
        "--diagnostic-first-socks-tunnel-urgent-start",
        action="store_true",
    )
    args = parser.parse_args()
    user = os.environ.get("NAIVEFOX_FIXTURE_USER", "")
    password = os.environ.get("NAIVEFOX_FIXTURE_PASS", "")
    config = build_config(
        args.arm,
        args.protocol,
        args.socks_port,
        args.proxy_port,
        user,
        password,
        args.diagnostic_first_socks_tunnel_urgent_start,
    )
    write_config(args.output, config)


if __name__ == "__main__":
    main()
