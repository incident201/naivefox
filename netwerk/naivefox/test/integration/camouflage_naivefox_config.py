#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

PREAMBLE_PATH = "/assets/runtime.js"
PREAMBLE_MAX_BYTES = 64 * 1024


def build_config(arm, protocol, socks_port, proxy_port, proxy_user, proxy_pass):
    if arm not in ("off", "gate", "root"):
        raise ValueError("config arm must be off, gate, or root")
    if protocol not in ("h2", "h3"):
        raise ValueError("protocol must be h2 or h3")
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
    if arm == "root":
        preamble = {
            "mode": "root",
            "path": PREAMBLE_PATH,
            "max-bytes": PREAMBLE_MAX_BYTES,
        }
    return {
        "listen": f"socks://127.0.0.1:{socks_port}",
        "proxy": f"{scheme}://{user}:{password}@localhost:{proxy_port}",
        "host-resolver-rules": "MAP localhost 127.0.0.1",
        "outer-session-gate": arm != "off",
        "preamble": preamble,
        "log": "",
    }


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
    parser.add_argument("--arm", choices=("off", "gate", "root"), required=True)
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--socks-port", type=int, required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
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
    )
    write_config(args.output, config)


if __name__ == "__main__":
    main()
