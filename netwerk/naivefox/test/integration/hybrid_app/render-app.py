#!/usr/bin/env python3
"""Render the shared benchmark application as an exact 24-KiB asset."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAPACITY = 24576


def render():
    raw = (ROOT / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    canonical = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if raw != canonical:
        raise ValueError("manifest.json must be canonical compact sorted JSON with one final LF")
    source = (ROOT / "app.template.js").read_text()
    for marker in ("__MANIFEST_JSON__", "__MANIFEST_SHA256__"):
        if source.count(marker) != 1:
            raise ValueError("application template marker count is invalid")
    source = source.replace("__MANIFEST_JSON__", raw.decode().strip()).replace(
        "__MANIFEST_SHA256__", hashlib.sha256(raw).hexdigest())
    source = "\n".join(line.removeprefix("  ") for line in source.splitlines()) + "\n"
    body = source.encode()
    overhead = len(b"\n/*\n*/\n")
    if len(body) + overhead > CAPACITY:
        raise ValueError("application source exceeds its declared asset capacity")
    return body + b"\n/*" + b"." * (CAPACITY - len(body) - overhead) + b"\n*/\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    body = render()
    destination = ROOT / "app.js"
    if args.check:
        if not destination.is_file() or destination.read_bytes() != body:
            raise SystemExit("app.js is stale; run render-app.py")
    else:
        destination.write_bytes(body)
    print("app.js: 24576 bytes; manifest " + hashlib.sha256((ROOT / "manifest.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
