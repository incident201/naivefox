#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
from pathlib import Path


FORMAT_VERSION = 1
MANIFEST_NAME = "runtime-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict:
    files = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda entry: entry.as_posix()):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        files.append({
            "mode": f"{path.stat().st_mode & 0o777:04o}",
            "path": relative,
            "sha256": sha256(path),
            "size": size,
        })
    return {
        "files": files,
        "format_version": FORMAT_VERSION,
        "total_bytes": total_bytes,
    }


def write_manifest(root: Path, output: Path) -> None:
    output.write_text(
        json.dumps(build_manifest(root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o644)


def verify_manifest(root: Path, manifest_path: Path) -> None:
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = build_manifest(root)
    if expected != actual:
        raise SystemExit("runtime manifest does not match package contents")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    manifest_path = root / MANIFEST_NAME
    if args.action == "create":
        write_manifest(root, manifest_path)
    else:
        if not manifest_path.is_file():
            raise SystemExit("runtime manifest is missing")
        verify_manifest(root, manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
