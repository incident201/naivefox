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
    preamble_path=PREAMBLE_PATH,
    max_connections=0,
):
    supported_arms = (
        "off",
        "gate",
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-carrier-dispatch",
        "document-cold-winner-handoff",
        "document-native-cache-open",
        "document-native-channel-open",
        "document-handshake-confirmed",
        "document-overlap",
        "document-start-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-start-overlap-css",
        "tree-native-parser-document-start-navigation-stop-css",
        "tree-native-parser-document-start-response-stop-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    if arm not in supported_arms:
        raise ValueError(
            "config arm must be off, gate, root, root-pmtud-control, "
            "document-complete, document-carrier-dispatch, "
            "document-cold-winner-handoff, "
            "document-native-cache-open, "
            "document-native-channel-open, "
            "document-handshake-confirmed, "
            "document-overlap, document-start-overlap, "
            "tree-complete, tree-complete-css, tree-early-overlap, "
            "tree-root-overlap, tree-root-overlap-css, "
            "tree-resource-committed-overlap-css, tree-warm-css-304, or "
            "tree-resource-native-cache-committed-overlap, "
            "tree-native-parser-preload-overlap-css, or "
            "tree-native-parser-document-start-overlap-css, or "
            "tree-native-parser-document-start-navigation-stop-css, or "
            "tree-native-parser-document-start-response-stop-css, or "
            "tree-native-parser-document-handoff-overlap-css, or "
            "tree-native-parser-retarget-overlap-css, or "
            "tree-native-parser-ipc-rendezvous-overlap-css, or "
            "tree-native-parser-root-rendezvous-overlap-css, or "
            "tree-native-parser-process-overlap-css, or "
            "tree-native-parser-full-process-overlap-css, or "
            "tree-overlap"
        )
    if protocol not in ("h2", "h3"):
        raise ValueError("protocol must be h2 or h3")
    if arm == "root-pmtud-control" and protocol != "h3":
        raise ValueError("root-pmtud-control requires h3")
    if arm == "document-handshake-confirmed" and protocol != "h3":
        raise ValueError("document-handshake-confirmed requires h3")
    if arm == "document-carrier-dispatch" and protocol != "h3":
        raise ValueError("document-carrier-dispatch requires h3")
    if arm == "document-cold-winner-handoff" and protocol != "h3":
        raise ValueError("document-cold-winner-handoff requires h3")
    if arm == "document-native-cache-open" and protocol != "h3":
        raise ValueError("document-native-cache-open requires h3")
    if arm == "document-native-channel-open" and protocol != "h3":
        raise ValueError("document-native-channel-open requires h3")
    if arm == "tree-resource-committed-overlap-css" and protocol != "h3":
        raise ValueError("tree-resource-committed-overlap-css requires h3")
    if (
        arm == "tree-resource-native-cache-committed-overlap"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-resource-native-cache-committed-overlap requires h3"
        )
    if arm == "tree-native-parser-preload-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-preload-overlap-css requires h3")
    if (
        arm == "tree-native-parser-document-start-overlap-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-document-start-overlap-css requires h3"
        )
    if (
        arm == "tree-native-parser-document-start-navigation-stop-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-document-start-navigation-stop-css requires h3"
        )
    if (
        arm == "tree-native-parser-document-start-response-stop-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-document-start-response-stop-css requires h3"
        )
    if (
        arm == "tree-native-parser-document-handoff-overlap-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-document-handoff-overlap-css requires h3"
        )
    if arm == "tree-native-parser-retarget-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-retarget-overlap-css requires h3")
    if (
        arm == "tree-native-parser-ipc-rendezvous-overlap-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-ipc-rendezvous-overlap-css requires h3"
        )
    if (
        arm == "tree-native-parser-root-rendezvous-overlap-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-root-rendezvous-overlap-css requires h3"
        )
    if arm == "tree-native-parser-process-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-process-overlap-css requires h3")
    if arm == "tree-native-parser-full-process-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-full-process-overlap-css requires h3")
    for name, port in (("SOCKS", socks_port), ("proxy", proxy_port)):
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise ValueError(f"{name} port is outside 1..65535")
    if not proxy_user or not proxy_pass:
        raise ValueError("proxy credentials must be non-empty")
    if (
        not isinstance(max_connections, int)
        or isinstance(max_connections, bool)
        or not 0 <= max_connections <= 0xFFFFFFFF
    ):
        raise ValueError("max connections is outside 0..4294967295")
    if (
        not isinstance(preamble_path, str)
        or not (
            preamble_path == PREAMBLE_PATH
            or preamble_path.startswith(PREAMBLE_PATH + "?")
        )
        or len(preamble_path) > 2048
        or "#" in preamble_path
        or any(ord(character) < 0x20 for character in preamble_path)
    ):
        raise ValueError("preamble path must be a bounded camouflage document path")

    scheme = "https" if protocol == "h2" else "quic"
    user = quote(proxy_user, safe="")
    password = quote(proxy_pass, safe="")
    preamble = {"mode": "off"}
    if arm == "tree-resource-committed-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-resource-committed-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
        }
    elif arm == "tree-resource-native-cache-committed-overlap":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-resource-native-cache-committed-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-preload-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-preload-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-document-start-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-document-start-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-document-start-navigation-stop-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-document-start-navigation-stop",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-document-start-response-stop-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-document-start-response-stop",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-document-handoff-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-document-handoff-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-retarget-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-retarget-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-ipc-rendezvous-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-ipc-rendezvous-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-root-rendezvous-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-root-rendezvous-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-process-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-process-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm == "tree-native-parser-full-process-overlap-css":
        preamble = {
            "mode": "off",
            "h3-mode": "tree-native-parser-full-process-overlap",
            "path": preamble_path,
            "max-assets": 1,
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
            "cache-resources": True,
        }
    elif arm in (
        "document-handshake-confirmed",
        "document-carrier-dispatch",
        "document-cold-winner-handoff",
        "document-native-cache-open",
        "document-native-channel-open",
    ):
        preamble = {
            "mode": "off",
            "h3-mode": arm,
            "path": preamble_path,
            "max-bytes": PREAMBLE_MAX_BYTES,
        }
    elif arm in (
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
            "path": preamble_path,
            "max-bytes": PREAMBLE_MAX_BYTES,
        }
    elif arm in (
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    ):
        preamble = {
            "mode": {
                "tree-complete-css": "tree-complete",
                "tree-root-overlap-css": "tree-root-overlap",
                "tree-warm-css-304": "tree-root-overlap",
            }.get(arm, arm),
            "path": preamble_path,
            "max-assets": (
                1
                if arm
                in (
                    "tree-complete-css",
                    "tree-root-overlap-css",
                    "tree-warm-css-304",
                )
                else TREE_PREAMBLE_MAX_ASSETS
            ),
            "max-bytes": TREE_PREAMBLE_MAX_BYTES,
        }
        if arm == "tree-warm-css-304":
            preamble["cache-resources"] = True
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
    if max_connections:
        config["max-connections"] = max_connections
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
            "document-carrier-dispatch",
            "document-cold-winner-handoff",
            "document-native-cache-open",
            "document-native-channel-open",
            "document-handshake-confirmed",
            "document-overlap",
            "document-start-overlap",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-resource-committed-overlap-css",
            "tree-resource-native-cache-committed-overlap",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-start-overlap-css",
            "tree-native-parser-document-start-navigation-stop-css",
            "tree-native-parser-document-start-response-stop-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
            "tree-native-parser-process-overlap-css",
            "tree-native-parser-full-process-overlap-css",
            "tree-warm-css-304",
            "tree-overlap",
        ),
        required=True,
    )
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--socks-port", type=int, required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--preamble-path", default=PREAMBLE_PATH)
    parser.add_argument("--max-connections", type=int, default=0)
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
        diagnostic_first_socks_tunnel_urgent_start=(
            args.diagnostic_first_socks_tunnel_urgent_start
        ),
        preamble_path=args.preamble_path,
        max_connections=args.max_connections,
    )
    write_config(args.output, config)


if __name__ == "__main__":
    main()
