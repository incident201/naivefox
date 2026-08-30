#!/usr/bin/env python3

"""Create the compact public metadata for a minimal-source export."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MODE_RE = re.compile(r"0[0-7]{3}")
PROVENANCE_FIELDS = (
    "firefox_base_commit",
    "naivefox_reference_commit",
    "minimal_export_commit",
    "evidence_source_commit",
    "evidence_commit",
)
PRODUCT_DOC_SOURCES = {
    "README.md": "netwerk/naivefox/README.md",
    "netwerk/naivefox/README.md": "netwerk/naivefox/README.md",
    "netwerk/naivefox/ARCHITECTURE.md": "netwerk/naivefox/ARCHITECTURE.md",
    "netwerk/naivefox/KNOWN-ISSUES.md": "netwerk/naivefox/KNOWN-ISSUES.md",
    "netwerk/naivefox/NO-CONNECT.md": "netwerk/naivefox/NO-CONNECT.md",
    "netwerk/naivefox/FRONTING-PAGE.md": "netwerk/naivefox/FRONTING-PAGE.md",
    "netwerk/naivefox/CAPTURE.md": "netwerk/naivefox/CAPTURE.md",
    "netwerk/naivefox/SHIMS.md": "netwerk/naivefox/SHIMS.md",
    "netwerk/naivefox/test/integration/README.md": (
        "netwerk/naivefox/test/integration/README.md"
    ),
}
ROOT_README_LINKS = {
    "ARCHITECTURE.md": "netwerk/naivefox/ARCHITECTURE.md",
    "KNOWN-ISSUES.md": "netwerk/naivefox/KNOWN-ISSUES.md",
    "NO-CONNECT.md": "netwerk/naivefox/NO-CONNECT.md",
    "FRONTING-PAGE.md": "netwerk/naivefox/FRONTING-PAGE.md",
    "CAPTURE.md": "netwerk/naivefox/CAPTURE.md",
    "test/integration/README.md": "netwerk/naivefox/test/integration/README.md",
}
ROOT_README_LINK_RE = re.compile(
    r"(?P<prefix>!?\[[^\]\n]*\]\(\s*<?)(?P<path>[^\s)>#?]+)"
)


def canonical_json(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def manifest_hash(manifest: dict[str, Any]) -> str:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    return hashlib.sha256(canonical_json(canonical)).hexdigest()


def validate_product_doc_mapping(entries: list[dict[str, Any]]) -> None:
    actual = {
        entry.get("path"): entry.get("source")
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and entry["path"].startswith("netwerk/naivefox/")
        and entry["path"].lower().endswith(".md")
    }
    root_readme = next(
        (
            entry.get("source")
            for entry in entries
            if isinstance(entry, dict) and entry.get("path") == "README.md"
        ),
        None,
    )
    actual["README.md"] = root_readme
    if actual != PRODUCT_DOC_SOURCES:
        missing = sorted(set(PRODUCT_DOC_SOURCES) - set(actual))
        unexpected = sorted(set(actual) - set(PRODUCT_DOC_SOURCES))
        mismatched = sorted(
            path
            for path in set(actual) & set(PRODUCT_DOC_SOURCES)
            if actual[path] != PRODUCT_DOC_SOURCES[path]
        )
        raise ValueError(
            "product document mapping mismatch; "
            f"missing={missing} unexpected={unexpected} mismatched={mismatched}"
        )


def render_root_readme(source: str) -> str:
    def rewrite(match: re.Match[str]) -> str:
        destination = ROOT_README_LINKS.get(match.group("path"))
        if destination is None:
            return match.group(0)
        return f"{match.group('prefix')}{destination}"

    return ROOT_README_LINK_RE.sub(rewrite, source)


def create_public_manifest(plan: dict[str, Any]) -> dict[str, Any]:
    provenance = {}
    for field in PROVENANCE_FIELDS:
        value = plan.get(field)
        if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
            raise ValueError(f"invalid or missing plan field: {field}")
        provenance[field] = value
    if provenance["minimal_export_commit"] != provenance["evidence_commit"]:
        raise ValueError("minimal export and evidence commits differ")
    if provenance["evidence_source_commit"] == provenance["evidence_commit"]:
        raise ValueError("evidence commit must be a child of its source commit")

    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("plan has no entries")
    validate_product_doc_mapping(entries)
    files = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("malformed plan entry")
        path = entry.get("path")
        mode = entry.get("mode")
        digest = entry.get("sha256")
        if not isinstance(path, str) or not path:
            raise ValueError("plan entry has no path")
        if not isinstance(mode, str) or not MODE_RE.fullmatch(mode):
            raise ValueError(f"invalid plan mode: {path}")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid plan SHA-256: {path}")
        files.append({"path": path, "mode": mode, "sha256": digest})
    files.sort(key=lambda entry: entry["path"])
    paths = [entry["path"] for entry in files]
    if len(paths) != len(set(paths)):
        raise ValueError("plan contains duplicate export paths")

    manifest = {
        "manifest_version": 2,
        **provenance,
        "counts": {
            "files": len(files),
            "executable_files": sum(
                bool(int(entry["mode"], 8) & 0o111) for entry in files
            ),
        },
        "files": files,
    }
    manifest["manifest_sha256"] = manifest_hash(manifest)
    return manifest


def upstream_base_text(manifest: dict[str, Any]) -> str:
    return (
        f"Firefox base SHA: {manifest['firefox_base_commit']}\n"
        f"NaiveFox reference SHA: {manifest['naivefox_reference_commit']}\n"
        f"Minimal export SHA: {manifest['minimal_export_commit']}\n"
        f"Evidence source SHA: {manifest['evidence_source_commit']}\n"
        f"Evidence commit SHA: {manifest['evidence_commit']}\n"
        f"Export manifest version: {manifest['manifest_version']}\n"
        f"Export manifest SHA-256: {manifest['manifest_sha256']}\n"
    )
