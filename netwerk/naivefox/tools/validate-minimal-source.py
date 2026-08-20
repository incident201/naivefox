#!/usr/bin/env python3

"""Validate a generated NaiveFox minimal-source snapshot before building it."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys


FORBIDDEN_COMPONENTS = {
    ".git",
    "objdir",
    "artifacts",
    "captures",
}
FORBIDDEN_SUFFIXES = (".pcap", ".pcapng", ".keylog")
ABSOLUTE_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|mnt|workspaces)/[^\s\"']*/"
    r"(?:naivefox|obj-[^/\s\"']*)(?:/|\s|$)|"
    r"[A-Za-z]:\\\\Users\\\\[^\s\"']*/(?:naivefox|obj-[^\\\s\"']*)(?:\\\\|\s|$)"
)


def fail(message: str) -> None:
    raise SystemExit(f"minimal-source validation failed: {message}")


def safe_relative(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("/"):
        fail(f"unsafe absolute/parent path: {value}")
    if any(part in FORBIDDEN_COMPONENTS for part in path.parts if part != "objdir"):
        fail(f"forbidden path component: {value}")
    if path.parts and (path.parts[0] == "objdir" or path.parts[0].startswith("obj-")):
        fail(f"generated object directory at export root: {value}")
    return path


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    manifest_path = root / "minimal-source.manifest.json"
    base_path = root / "UPSTREAM-BASE"
    if not manifest_path.is_file() or not base_path.is_file():
        fail("manifest or UPSTREAM-BASE is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 1:
        fail("unsupported manifest version")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        fail("manifest has no entries")
    canonical = dict(manifest)
    declared_hash = canonical.pop("manifest_sha256", None)
    actual_hash = hashlib.sha256(
        (json.dumps(canonical, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    if declared_hash != actual_hash:
        fail("manifest SHA-256 does not match its contents")

    expected = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("source"):
            fail("malformed manifest entry")
        path = safe_relative(entry["path"])
        source = safe_relative(entry["source"])
        if path in expected:
            fail(f"duplicate export path: {path}")
        expected.add(path)
        if not (root / path).is_file():
            fail(f"manifest file is missing: {path}")
        if source.as_posix().startswith("objdir/"):
            fail(f"objdir source leaked into manifest: {source}")

    actual = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        safe_relative(relative)
        if path.is_symlink():
            fail(f"symlink present: {relative}")
        if path.is_file():
            actual.add(path.relative_to(root))
            if path.name.endswith(FORBIDDEN_SUFFIXES):
                fail(f"capture/key material present: {relative}")

    generated = {pathlib.PurePosixPath("minimal-source.manifest.json"), pathlib.PurePosixPath("UPSTREAM-BASE")}
    if actual != expected | generated:
        missing = sorted(str(x) for x in (expected | generated) - actual)
        unexpected = sorted(str(x) for x in actual - (expected | generated))
        fail(f"file list mismatch; missing={missing[:5]} unexpected={unexpected[:5]}")

    readme = (root / "README.md").read_text(encoding="utf-8", errors="replace")
    if "NaiveFox" not in readme or "Firefox" not in readme:
        fail("root README is not the NaiveFox product README")
    base = base_path.read_text(encoding="utf-8", errors="replace")
    if "Firefox base SHA:" not in base or "NaiveFox SHA:" not in base:
        fail("UPSTREAM-BASE lacks traceability fields")

    for path in root.rglob("*"):
        if not path.is_file() or path.name in {"minimal-source.manifest.json", "UPSTREAM-BASE"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ABSOLUTE_TEXT.search(text):
            fail(f"absolute host/build path present in {path.relative_to(root)}")
        if "github_pat_" in text:
            fail(f"credential-bearing text present in {path.relative_to(root)}")
    print(f"minimal-source validation passed: {len(expected)} manifest files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
