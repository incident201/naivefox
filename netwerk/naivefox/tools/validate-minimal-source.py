#!/usr/bin/env python3

"""Validate a generated NaiveFox minimal-source snapshot before building it."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import stat
import sys
import urllib.parse
from collections.abc import Iterable

from minimal_source_manifest import (
    COMMIT_RE,
    MODE_RE,
    SHA256_RE,
    canonical_json,
    manifest_hash,
    upstream_base_text,
)

FORBIDDEN_COMPONENTS = {
    ".git",
    "artifacts",
    "captures",
    "logs",
    "profiles",
}
FORBIDDEN_SUFFIXES = (
    ".keylog",
    ".log",
    ".o",
    ".obj",
    ".pcap",
    ".pcapng",
    ".pdb",
)
TRACKED_SOURCE_FIXTURES = {
    pathlib.PurePosixPath("memory/replace/logalloc/replay/expected_output_minimal.log"),
    pathlib.PurePosixPath("memory/replace/logalloc/replay/replay.log"),
}
FORBIDDEN_BASENAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI-HANDOFF.md",
    "MINIMAL-PATCHES.md",
    "MINIMAL.md",
    "MINIMISATION-TASK.MD",
    "UPSTREAM-PATCHES.md",
    "UPSTREAM.md",
    "cert9.db",
    "key4.db",
    "pkcs11.txt",
}
PRODUCT_DOCS = {
    pathlib.PurePosixPath("netwerk/naivefox/README.md"),
    pathlib.PurePosixPath("netwerk/naivefox/ARCHITECTURE.md"),
    pathlib.PurePosixPath("netwerk/naivefox/KNOWN-ISSUES.md"),
    pathlib.PurePosixPath("netwerk/naivefox/CAPTURE.md"),
    pathlib.PurePosixPath("netwerk/naivefox/SHIMS.md"),
    pathlib.PurePosixPath("netwerk/naivefox/test/integration/README.md"),
}
MANIFEST_KEYS = {
    "manifest_version",
    "firefox_base_commit",
    "naivefox_reference_commit",
    "minimal_export_commit",
    "evidence_source_commit",
    "evidence_commit",
    "counts",
    "files",
    "manifest_sha256",
}
PROVENANCE_KEYS = {
    "firefox_base_commit",
    "naivefox_reference_commit",
    "minimal_export_commit",
    "evidence_source_commit",
    "evidence_commit",
}
UPSTREAM_FIELDS = {
    "Firefox base SHA": "firefox_base_commit",
    "NaiveFox reference SHA": "naivefox_reference_commit",
    "Minimal export SHA": "minimal_export_commit",
    "Evidence source SHA": "evidence_source_commit",
    "Evidence commit SHA": "evidence_commit",
    "Export manifest version": "manifest_version",
    "Export manifest SHA-256": "manifest_sha256",
}
ABSOLUTE_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|mnt|workspaces)/[^\s\"']*/"
    r"(?:naivefox|obj-[^/\s\"']*)(?:/|\s|$)|"
    r"[A-Za-z]:\\Users\\[^\s\"']*/(?:naivefox|obj-[^\\\s\"']*)(?:\\|\s|$)"
)
FENCED_CODE = re.compile(
    r"^[ \t]*(```+|~~~+)[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL
)
INLINE_LINK = re.compile(
    r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)\n]+))", re.MULTILINE
)
REFERENCE_LINK = re.compile(
    r"^[ \t]*\[[^\]\n]+\]:[ \t]*(?:<([^>\n]+)>|([^\s]+))", re.MULTILINE
)


class ValidationError(ValueError):
    pass


def fail(message: str) -> None:
    raise ValidationError(f"minimal-source validation failed: {message}")


def safe_relative(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or not path.parts
        or path.as_posix() != value
        or ".." in path.parts
    ):
        fail(f"unsafe or non-canonical path: {value}")
    if any(part in FORBIDDEN_BASENAMES for part in path.parts):
        fail(f"agent/maintenance/private file leaked into export: {value}")
    if any(part in FORBIDDEN_COMPONENTS for part in path.parts):
        fail(f"forbidden path component: {value}")
    if any(part == "objdir" or part.startswith("obj-") for part in path.parts):
        fail(f"generated object directory leaked into export: {value}")
    if path.parts and path.parts[0] == "browser":
        if len(path.parts) >= 2 and path.parts[1] != "config":
            fail(f"Firefox browser product source leaked into export: {value}")
    return path


def markdown_destinations(text: str) -> Iterable[str]:
    text = FENCED_CODE.sub("", text)
    for pattern in (INLINE_LINK, REFERENCE_LINK):
        for match in pattern.finditer(text):
            yield match.group(1) or match.group(2)


def validate_markdown_links(
    root: pathlib.Path, markdown_paths: Iterable[pathlib.PurePosixPath]
) -> None:
    for relative in sorted(markdown_paths):
        source = root / relative
        text = source.read_text(encoding="utf-8", errors="replace")
        for raw_destination in markdown_destinations(text):
            destination = raw_destination.strip()
            if not destination or destination.startswith("#"):
                continue
            try:
                parsed = urllib.parse.urlsplit(destination)
            except ValueError:
                fail(f"invalid Markdown link in {relative}: {destination}")
            if parsed.scheme or parsed.netloc:
                continue
            decoded = urllib.parse.unquote(parsed.path)
            if not decoded:
                continue
            if decoded.startswith("/") or "\\" in decoded:
                fail(f"non-portable Markdown link in {relative}: {destination}")
            target = (source.parent / decoded).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                fail(f"Markdown link escapes export in {relative}: {destination}")
            if not target.exists():
                fail(f"broken Markdown link in {relative}: {destination}")


def load_manifest(path: pathlib.Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read manifest: {error}")
    if not isinstance(manifest, dict):
        fail("manifest root is not an object")
    version = manifest.get("manifest_version")
    if version == 1:
        fail("legacy manifest version 1 is read-only; regenerate with the v2 exporter")
    if version != 2:
        fail(f"unsupported manifest version: {version!r}")
    if set(manifest) != MANIFEST_KEYS:
        missing = sorted(MANIFEST_KEYS - set(manifest))
        unexpected = sorted(set(manifest) - MANIFEST_KEYS)
        fail(f"manifest fields mismatch; missing={missing} unexpected={unexpected}")
    return manifest


def validate_manifest(manifest: dict) -> set[pathlib.PurePosixPath]:
    for key in PROVENANCE_KEYS:
        value = manifest[key]
        if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
            fail(f"invalid provenance commit: {key}")
    if manifest["minimal_export_commit"] != manifest["evidence_commit"]:
        fail("minimal export and evidence commits differ")
    if manifest["evidence_source_commit"] == manifest["evidence_commit"]:
        fail("evidence source and evidence commits are identical")

    declared_hash = manifest["manifest_sha256"]
    if not isinstance(declared_hash, str) or not SHA256_RE.fullmatch(declared_hash):
        fail("invalid manifest SHA-256")
    if declared_hash != manifest_hash(manifest):
        fail("manifest SHA-256 does not match its canonical contents")

    files = manifest["files"]
    if not isinstance(files, list) or not files:
        fail("manifest has no files")
    expected = set()
    previous = None
    executable_files = 0
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "mode", "sha256"}:
            fail("malformed or non-public manifest file entry")
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            fail("manifest file path is not a string")
        path = safe_relative(path_value)
        mode = entry.get("mode")
        digest = entry.get("sha256")
        if previous is not None and path.as_posix() <= previous:
            fail("manifest files are not strictly sorted by path")
        previous = path.as_posix()
        if path in expected:
            fail(f"duplicate export path: {path}")
        expected.add(path)
        if not isinstance(mode, str) or not MODE_RE.fullmatch(mode):
            fail(f"invalid mode in manifest: {path}")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            fail(f"invalid content SHA-256 in manifest: {path}")
        executable_files += bool(int(mode, 8) & 0o111)

    counts = manifest["counts"]
    expected_counts = {"files": len(files), "executable_files": executable_files}
    if not isinstance(counts, dict) or counts != expected_counts:
        fail(f"manifest counts mismatch: expected {expected_counts}")
    return expected


def parse_upstream_base(path: pathlib.Path) -> dict[str, str]:
    fields = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        fail(f"cannot read UPSTREAM-BASE: {error}")
    for line in lines:
        if ": " not in line:
            fail(f"malformed UPSTREAM-BASE line: {line!r}")
        key, value = line.split(": ", 1)
        if key in fields:
            fail(f"duplicate UPSTREAM-BASE field: {key}")
        fields[key] = value
    return fields


def validate(root: pathlib.Path) -> int:
    root = root.resolve()
    manifest_path = root / "minimal-source.manifest.json"
    base_path = root / "UPSTREAM-BASE"
    if not root.is_dir() or not manifest_path.is_file() or not base_path.is_file():
        fail("manifest or UPSTREAM-BASE is missing")

    manifest = load_manifest(manifest_path)
    if manifest_path.read_bytes() != canonical_json(manifest):
        fail("manifest does not use canonical JSON serialization")
    expected = validate_manifest(manifest)
    generated = {
        pathlib.PurePosixPath("minimal-source.manifest.json"),
        pathlib.PurePosixPath("UPSTREAM-BASE"),
    }
    if expected & generated:
        fail("generated metadata is listed as an exported source file")

    entry_by_path = {item["path"]: item for item in manifest["files"]}
    for path in expected:
        exported = root / path
        if not exported.is_file() or exported.is_symlink():
            fail(f"manifest file is missing or not regular: {path}")
        entry = entry_by_path[path.as_posix()]
        if hashlib.sha256(exported.read_bytes()).hexdigest() != entry["sha256"]:
            fail(f"content hash mismatch: {path}")
        actual_mode = stat.S_IMODE(exported.stat().st_mode)
        expected_mode = int(entry["mode"], 8)
        if actual_mode != expected_mode:
            fail(f"mode mismatch for {path}: {actual_mode:04o} != {expected_mode:04o}")

    actual = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        safe = safe_relative(relative)
        if path.is_symlink():
            fail(f"symlink present: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            fail(f"unsupported filesystem node present: {relative}")
        actual.add(safe)
        if safe not in TRACKED_SOURCE_FIXTURES and path.name.lower().endswith(
            FORBIDDEN_SUFFIXES
        ):
            fail(f"build/capture/log artifact present: {relative}")
    if actual != expected | generated:
        missing = sorted(str(value) for value in (expected | generated) - actual)
        unexpected = sorted(str(value) for value in actual - (expected | generated))
        fail(f"file list mismatch; missing={missing[:5]} unexpected={unexpected[:5]}")
    for path in generated:
        mode = stat.S_IMODE((root / path).stat().st_mode)
        if mode != 0o644:
            fail(f"mode mismatch for {path}: {mode:04o} != 0644")

    project_docs = {
        path
        for path in actual
        if path.parts[:2] == ("netwerk", "naivefox") and path.suffix.lower() == ".md"
    }
    if project_docs != PRODUCT_DOCS:
        missing = sorted(str(path) for path in PRODUCT_DOCS - project_docs)
        unexpected = sorted(str(path) for path in project_docs - PRODUCT_DOCS)
        fail(
            f"product documentation mismatch; missing={missing} unexpected={unexpected}"
        )

    readme = (root / "README.md").read_text(encoding="utf-8", errors="replace")
    if "NaiveFox" not in readme or "config.json" not in readme:
        fail("root README is not the NaiveFox product/build README")
    if not (root / "config.example.json").is_file():
        fail("credential-free config.example.json is missing")

    base_fields = parse_upstream_base(base_path)
    expected_base = {
        key: str(manifest[manifest_key])
        for key, manifest_key in UPSTREAM_FIELDS.items()
    }
    if base_fields != expected_base:
        missing = sorted(set(expected_base) - set(base_fields))
        unexpected = sorted(set(base_fields) - set(expected_base))
        fail(
            f"UPSTREAM-BASE fields mismatch; missing={missing} unexpected={unexpected}"
        )
    for key, value in expected_base.items():
        if base_fields[key] != value:
            fail(f"UPSTREAM-BASE field mismatch: {key}")
    if base_path.read_text(encoding="utf-8") != upstream_base_text(manifest):
        fail("UPSTREAM-BASE is not in canonical generated form")

    if (
        not (root / "LICENSE").is_file()
        or not (root / "toolkit/content/license.html").is_file()
    ):
        fail("root or consolidated third-party license is missing")

    validate_markdown_links(root, PRODUCT_DOCS | {pathlib.PurePosixPath("README.md")})
    for path in actual:
        if path in generated:
            continue
        exported = root / path
        text = exported.read_text(encoding="utf-8", errors="replace")
        if ABSOLUTE_TEXT.search(text):
            fail(f"absolute host/build path present in {path}")
        if "github_" + "pat_" in text:
            fail(f"credential-bearing text present in {path}")
    return len(expected)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    try:
        count = validate(root)
    except ValidationError as error:
        raise SystemExit(str(error)) from error
    print(f"minimal-source validation passed: {count} manifest files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
