#!/usr/bin/env python3

"""Validate a generated NaiveFox minimal-source snapshot before building it."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import stat
import sys


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
}
FORBIDDEN_BASENAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI-HANDOFF.md",
    "MINIMISATION-TASK.MD",
    "cert9.db",
    "key4.db",
    "pkcs11.txt",
}
ABSOLUTE_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|mnt|workspaces)/[^\s\"']*/"
    r"(?:naivefox|obj-[^/\s\"']*)(?:/|\s|$)|"
    r"[A-Za-z]:\\Users\\[^\s\"']*/(?:naivefox|obj-[^\\\s\"']*)(?:\\|\s|$)"
)


def fail(message: str) -> None:
    raise SystemExit(f"minimal-source validation failed: {message}")


def safe_relative(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        fail(f"unsafe absolute/parent path: {value}")
    if any(part in FORBIDDEN_BASENAMES for part in path.parts):
        fail(f"agent/task/private file leaked into export: {value}")
    if any(part in FORBIDDEN_COMPONENTS for part in path.parts):
        fail(f"forbidden path component: {value}")
    if any(part == "objdir" or part.startswith("obj-") for part in path.parts):
        fail(f"generated object directory leaked into export: {value}")
    if path.parts and path.parts[0] == "browser":
        if len(path.parts) < 2 or path.parts[1] != "config":
            fail(f"Firefox browser product source leaked into export: {value}")
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
        if not isinstance(entry, dict) or not entry.get("path"):
            fail("malformed manifest entry")
        path = safe_relative(entry["path"])
        source_value = entry.get("source")
        if source_value is not None:
            safe_relative(source_value)
        if path in expected:
            fail(f"duplicate export path: {path}")
        expected.add(path)
        exported = root / path
        if not exported.is_file():
            fail(f"manifest file is missing: {path}")
        actual_content_hash = hashlib.sha256(exported.read_bytes()).hexdigest()
        if actual_content_hash != entry.get("sha256"):
            fail(f"content hash mismatch: {path}")
        try:
            expected_mode = int(entry.get("mode", ""), 8)
        except (TypeError, ValueError):
            fail(f"invalid mode in manifest: {path}")
        actual_mode = stat.S_IMODE(exported.stat().st_mode)
        if actual_mode != expected_mode:
            fail(f"mode mismatch for {path}: {actual_mode:04o} != {expected_mode:04o}")

    actual = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        safe_relative(relative)
        if path.is_symlink():
            fail(f"symlink present: {relative}")
        if path.is_file():
            actual.add(path.relative_to(root))
            lower = path.name.lower()
            if pathlib.PurePosixPath(
                relative
            ) not in TRACKED_SOURCE_FIXTURES and lower.endswith(FORBIDDEN_SUFFIXES):
                fail(f"build/capture/log artifact present: {relative}")

    generated = {
        pathlib.PurePosixPath("minimal-source.manifest.json"),
        pathlib.PurePosixPath("UPSTREAM-BASE"),
    }
    if actual != expected | generated:
        missing = sorted(str(value) for value in (expected | generated) - actual)
        unexpected = sorted(str(value) for value in actual - (expected | generated))
        fail(f"file list mismatch; missing={missing[:5]} unexpected={unexpected[:5]}")

    readme = (root / "README.md").read_text(encoding="utf-8", errors="replace")
    if "NaiveFox" not in readme or "config.json" not in readme:
        fail("root README is not the NaiveFox product/build README")
    if not (root / "config.example.json").is_file():
        fail("credential-free config.example.json is missing")

    base_fields = {}
    for line in base_path.read_text(encoding="utf-8").splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            base_fields[key] = value
    expected_base = {
        "Firefox base SHA": manifest["firefox_base_commit"],
        "NaiveFox reference SHA": manifest["naivefox_reference_commit"],
        "Minimal export SHA": manifest["minimal_export_commit"],
        "Build report source SHA": manifest["build_report_source_commit"],
        "Closure report source SHA(s)": ",".join(
            manifest["closure_report_source_commits"]
        ),
        "Export manifest SHA-256": declared_hash,
    }
    for key, value in expected_base.items():
        if base_fields.get(key) != value:
            fail(f"UPSTREAM-BASE field mismatch: {key}")

    if (
        not (root / "LICENSE").is_file()
        or not (root / "toolkit/content/license.html").is_file()
    ):
        fail("root or consolidated third-party license is missing")
    for package in manifest.get("cargo_license_inventory", []):
        manifest_relative = safe_relative(package.get("manifest_path", ""))
        if not (root / manifest_relative).is_file():
            fail(f"Cargo package manifest missing: {manifest_relative}")
        coverage = package.get("license_coverage", [])
        if not coverage:
            fail(
                "Cargo package has no mapped license coverage: "
                f"{package.get('name')} {package.get('version')}"
            )
        for value in coverage:
            if not (root / safe_relative(value)).is_file():
                fail(f"Cargo license coverage missing: {value}")
        for candidate in package.get("license_candidates", []):
            if not (root / safe_relative(candidate)).is_file():
                fail(f"Cargo license candidate missing: {candidate}")

    for path in root.rglob("*"):
        if not path.is_file() or path.name in {
            "minimal-source.manifest.json",
            "UPSTREAM-BASE",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ABSOLUTE_TEXT.search(text):
            fail(f"absolute host/build path present in {path.relative_to(root)}")
        if "github_" + "pat_" in text:
            fail(f"credential-bearing text present in {path.relative_to(root)}")
    print(f"minimal-source validation passed: {len(expected)} manifest files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
