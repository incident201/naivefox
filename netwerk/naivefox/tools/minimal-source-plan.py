#!/usr/bin/env python3

"""Build and validate the deterministic allowlist for minimal-source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


FIREFOX_BASE = "8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6"
BUILD_TARGETS = {"linux-x86_64", "windows-x86_64"}
MAINTENANCE_TOOLS = {
    "analyze-full-closure.py",
    "analyze-link-closure.py",
    "analyze-runtime-trace.py",
    "assert-closure.py",
    "collect-build-inputs.py",
    "collect-configure-inputs.py",
    "export-minimal-source.sh",
    "minimal-source-plan.py",
    "validate-minimal-source.py",
    "verify-shims.py",
}
PRODUCT_DOCS = {
    "H3-DESIGN.md",
    "KNOWN-ISSUES.md",
    "MINIMISATION-REPORT.md",
    "PERFORMANCE-REPORT.md",
    "SHIMS.md",
    "TEST-REPORT.md",
    "UPSTREAM.md",
}
FORBIDDEN_BASENAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI-HANDOFF.md",
    "MINIMISATION-TASK.MD",
}
FORBIDDEN_COMPONENTS = {".git", "artifacts", "captures", "logs", "profiles"}
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
    "memory/replace/logalloc/replay/expected_output_minimal.log",
    "memory/replace/logalloc/replay/replay.log",
}
ABSOLUTE_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|mnt|workspaces)/[^\s\"']*/"
    r"(?:naivefox|obj-[^/\s\"']*)(?:/|\s|$)|"
    r"[A-Za-z]:\\Users\\[^\s\"']*/(?:naivefox|obj-[^\\\s\"']*)(?:\\|\s|$)"
)
REPORT_ONLY_PATHS = (
    re.compile(r"^netwerk/naivefox/reports/"),
    re.compile(r"^netwerk/naivefox/[^/]+\.md$", re.IGNORECASE),
    re.compile(r"^netwerk/naivefox/config\.example\.json$"),
    re.compile(
        r"^netwerk/naivefox/tools/(?:assert-closure\.py|"
        r"collect-(?:build|configure)-inputs\.py|"
        r"export-minimal-source\.sh|minimal-source-plan\.py|"
        r"validate-minimal-source\.py)$"
    ),
)


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"unsafe repository path: {value!r}")
    if any(part in FORBIDDEN_BASENAMES for part in path.parts):
        raise SystemExit(f"agent/task document in source plan: {value}")
    if any(part in FORBIDDEN_COMPONENTS for part in path.parts):
        raise SystemExit(f"generated/private path in source plan: {value}")
    if any(part == "objdir" or part.startswith("obj-") for part in path.parts):
        raise SystemExit(f"generated/private path in source plan: {value}")
    if path.as_posix() not in TRACKED_SOURCE_FIXTURES and path.name.lower().endswith(
        FORBIDDEN_SUFFIXES
    ):
        raise SystemExit(f"build/capture/log artifact in source plan: {value}")
    if path.parts and path.parts[0] == "browser":
        if len(path.parts) < 2 or path.parts[1] != "config":
            raise SystemExit(f"Firefox browser product source in plan: {value}")
    return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--configure-report", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, action="append", required=True)
    parser.add_argument("--closure-report", type=Path, action="append", required=True)
    args = parser.parse_args()

    repo = args.repo.resolve(strict=True)
    output = args.output.resolve()
    status = run(repo, "status", "--porcelain=v1")
    if status:
        raise SystemExit("minimal checkout must be clean when creating an export plan")
    source_commit = run(repo, "rev-parse", "HEAD")
    commit_epoch = int(run(repo, "show", "-s", "--format=%ct", source_commit))
    generated_at = (
        datetime
        .fromtimestamp(commit_epoch, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    tracked_raw = subprocess.check_output([
        "git",
        "-C",
        str(repo),
        "ls-files",
        "-z",
    ]).decode("utf-8", "surrogateescape")
    tracked = {value for value in tracked_raw.split("\0") if value}

    entry_sources: dict[str, str | None] = {}
    entry_categories: dict[str, set[str]] = defaultdict(set)
    generated_contents: dict[str, str] = {}
    directory_contracts = set()
    evidence = []

    def validate_commit(commit: str, label: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
            raise SystemExit(f"invalid {label} source commit: {commit!r}")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                commit,
                source_commit,
            ],
            check=False,
        )
        if result.returncode:
            raise SystemExit(f"{label} source {commit} is not an export ancestor")
        if commit == source_commit:
            return
        changed = run(
            repo, "diff", "--name-only", f"{commit}..{source_commit}"
        ).splitlines()
        invalid = [
            path
            for path in changed
            if not any(pattern.search(path) for pattern in REPORT_ONLY_PATHS)
        ]
        if invalid:
            details = "\n".join(invalid[:30])
            raise SystemExit(
                f"{label} is stale across build-affecting changes:\n{details}"
            )

    def add(source: str, category: str, destination: str | None = None) -> None:
        source = safe_path(source)
        destination = safe_path(destination or source)
        if source not in tracked or not (repo / source).is_file():
            raise SystemExit(f"missing tracked source input: {source} ({category})")
        if destination in entry_sources and entry_sources[destination] != source:
            raise SystemExit(
                f"two sources map to {destination}: "
                f"{entry_sources[destination]} and {source}"
            )
        entry_sources[destination] = source
        entry_categories[destination].add(category)

    def add_generated(destination: str, category: str, content: str) -> None:
        destination = safe_path(destination)
        if destination in entry_sources:
            raise SystemExit(f"generated path collides with entry: {destination}")
        entry_sources[destination] = None
        entry_categories[destination].add(category)
        generated_contents[destination] = content

    def load_report(path: Path, kind: str) -> dict:
        path = path.resolve(strict=True)
        if repo not in path.parents:
            raise SystemExit(f"{kind} report must be committed inside the repository")
        data = json.loads(path.read_text(encoding="utf-8"))
        evidence.append({
            "kind": kind,
            "path": path.relative_to(repo).as_posix(),
            "sha256": sha256(path),
        })
        return data

    build_reports = [load_report(path, "build-inputs") for path in args.build_report]
    targets = {report.get("target") for report in build_reports}
    if targets != BUILD_TARGETS or len(build_reports) != len(BUILD_TARGETS):
        raise SystemExit(f"build reports must be exactly {sorted(BUILD_TARGETS)}")
    build_source_commits = {report.get("source_commit") for report in build_reports}
    if len(build_source_commits) != 1:
        raise SystemExit(
            "Linux and Windows build reports have different source commits"
        )
    build_source_commit = next(iter(build_source_commits))
    validate_commit(build_source_commit, "build-input reports")
    cargo_license_inventory = {}
    build_collector_hash = sha256(
        repo / "netwerk/naivefox/tools/collect-build-inputs.py"
    )
    for report in build_reports:
        if report.get("report_version") != 1:
            raise SystemExit("unsupported build-input report version")
        if report.get("source_tree_kind") != "repository":
            raise SystemExit(
                "publication build-input report is not from the repository"
            )
        if not report.get("source_worktree_clean"):
            raise SystemExit("build-input report was collected from a dirty checkout")
        if report.get("firefox_base_commit") != FIREFOX_BASE:
            raise SystemExit("build-input report Firefox base mismatch")
        if report.get("collector_sha256") != build_collector_hash:
            raise SystemExit("build-input report collector hash mismatch")
        if set(report.get("cargo_build_roots", [])) != {
            "gkrust",
            "oxilangtag-ffi",
        }:
            raise SystemExit("build-input report Cargo roots mismatch")
        mozconfig = safe_path(report["mozconfig"])
        if sha256(repo / mozconfig) != report.get("mozconfig_sha256"):
            raise SystemExit(f"build-input report has stale mozconfig: {mozconfig}")
        for value in report.get("files", []):
            add(value, f"build:{report['target']}")
        directory_contracts.update(report.get("directory_contracts", []))
        for package in report.get("cargo_packages", []):
            manifest_path = package.get("manifest_path")
            if not manifest_path:
                raise SystemExit(
                    "Cargo package was resolved outside the source tree: "
                    f"{package.get('name')} {package.get('version')}"
                )
            safe_path(manifest_path)
            coverage = package.get("license_coverage", [])
            if not coverage:
                raise SystemExit(
                    "Cargo package has no mapped license coverage: "
                    f"{package.get('name')} {package.get('version')}"
                )
            for value in coverage:
                add(value, "license:cargo-package")
            key = (
                package["name"],
                package["version"],
                package.get("source") or "local",
            )
            previous = cargo_license_inventory.get(key)
            if previous is not None and previous != package:
                raise SystemExit(f"target Cargo license metadata differs for {key}")
            cargo_license_inventory[key] = package

    closure_reports = [
        load_report(path, "linked-closure") for path in args.closure_report
    ]
    closure_targets = set()
    closure_source_commits = set()
    for report in closure_reports:
        provenance = report.get("report_provenance", {})
        target = provenance.get("target_triple")
        closure_targets.add(target)
        commit = provenance.get("source_commit_sha")
        closure_source_commits.add(commit)
        validate_commit(commit, f"closure report {target}")
        if provenance.get("firefox_base_sha") != FIREFOX_BASE:
            raise SystemExit("closure report Firefox base mismatch")
        build = report.get("build_inputs", {})
        for key in (
            "cxx_translation_units",
            "headers",
            "cargo_manifests",
            "xpidl_inputs",
            "ipdl_inputs",
            "webidl_binding_inputs",
            "generators_and_python_scripts",
            "mozbuild_definition_inputs",
            "runtime_resource_sources",
            "licenses_and_notices",
        ):
            for value in build.get(key, []):
                add(value, f"closure:{target}:{key}")
        for key in ("cargo_root_manifest", "cargo_lockfile", "cargo_config_template"):
            value = build.get(key)
            if value:
                add(value, f"closure:{target}:{key}")
        glean = build.get("glean", {})
        for key in ("generator_scripts", "metrics_yaml_inputs", "pings_yaml_inputs"):
            for value in glean.get(key, []):
                add(value, f"closure:{target}:glean:{key}")
    expected_closure_targets = {"x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"}
    if closure_targets != expected_closure_targets or len(closure_reports) != 2:
        raise SystemExit("closure reports must be exactly Linux and Windows x86-64")
    if closure_source_commits != {build_source_commit}:
        raise SystemExit(
            "build-input and linked-closure reports must share one audited source"
        )

    configure_report = load_report(args.configure_report, "configure-trace")
    if configure_report.get("report_version") != 2:
        raise SystemExit("configure report must use attested schema version 2")
    if configure_report.get("target") != "linux-x86_64":
        raise SystemExit("configure report target mismatch")
    if configure_report.get("target_triple") != "x86_64-pc-linux-gnu":
        raise SystemExit("configure report configured target mismatch")
    if not configure_report.get("source_worktree_clean"):
        raise SystemExit("configure trace was collected from a dirty checkout")
    if configure_report.get("configure_exit_status") != 0:
        raise SystemExit("configure trace does not attest a successful configure")
    configure_source_commit = configure_report.get("source_commit")
    validate_commit(configure_source_commit, "configure trace")
    if configure_source_commit != build_source_commit:
        raise SystemExit(
            "configure, build-input, and closure reports must share one audited source"
        )
    if configure_report.get("firefox_base_commit") != FIREFOX_BASE:
        raise SystemExit("configure report Firefox base mismatch")
    configure_collector_hash = sha256(
        repo / "netwerk/naivefox/tools/collect-configure-inputs.py"
    )
    if configure_report.get("collector_sha256") != configure_collector_hash:
        raise SystemExit("configure report collector hash mismatch")
    configure_mozconfig = safe_path(configure_report["mozconfig"])
    if sha256(repo / configure_mozconfig) != configure_report.get("mozconfig_sha256"):
        raise SystemExit("configure report mozconfig hash mismatch")
    configure_bootstrap_prefixes = (
        "build/",
        "config/",
        "python/",
        "tools/",
        "third_party/python/",
        "other-licenses/ply/",
        "nsprpub/",
    )
    for value in configure_report.get("files", []):
        if value.startswith(configure_bootstrap_prefixes):
            add(value, "configure:bootstrap")

    project_raw = subprocess.check_output([
        "git",
        "-C",
        str(repo),
        "ls-files",
        "-z",
        "--",
        "netwerk/naivefox",
    ]).decode("utf-8", "surrogateescape")
    for path in (value for value in project_raw.split("\0") if value):
        relative = path.removeprefix("netwerk/naivefox/")
        if relative in FORBIDDEN_BASENAMES or relative == "README.md":
            continue
        if relative == "PRODUCT-README.md":
            add(path, "product:readme", "README.md")
        elif relative == "config.example.json":
            add(path, "product:config-example", "config.example.json")
        elif relative.endswith(".md"):
            if relative in PRODUCT_DOCS:
                add(path, "product:documentation", f"docs/{relative}")
        elif relative.startswith("reports/"):
            continue
        elif (
            relative.startswith("tools/")
            and PurePosixPath(relative).name in MAINTENANCE_TOOLS
        ):
            continue
        else:
            add(path, "product:source-and-tests")

    for value in (
        "mach",
        "client.mk",
        "configure",
        "configure.py",
        "moz.configure",
        "Cargo.toml",
        "Cargo.lock",
        ".cargo/config.toml.in",
        "CLOBBER",
        "LICENSE",
        "toolkit/content/license.html",
        "browser/config/version.txt",
        "browser/config/version_display.txt",
    ):
        add(value, "explicit:bootstrap-license")

    for directory in sorted(directory_contracts):
        directory = safe_path(directory).rstrip("/")
        if not (repo / directory).is_dir():
            raise SystemExit(f"directory contract is absent in source: {directory}")
        if not any(
            path == directory or path.startswith(f"{directory}/")
            for path in entry_sources
        ):
            add_generated(
                f"{directory}/.naivefox-directory",
                "generated:directory-contract",
                "NaiveFox standalone build directory contract.\n",
            )

    entries = []
    for destination in sorted(entry_sources):
        source = entry_sources[destination]
        if source is None:
            content = generated_contents[destination].encode("utf-8")
            mode = 0o644
        else:
            source_path = repo / source
            if source_path.is_symlink():
                resolved = source_path.resolve(strict=True)
                try:
                    resolved.relative_to(repo)
                except ValueError as error:
                    raise SystemExit(
                        f"source symlink escapes checkout: {source}"
                    ) from error
                content = resolved.read_bytes()
            else:
                content = source_path.read_bytes()
            mode = stat.S_IMODE(source_path.stat().st_mode)
        if ABSOLUTE_TEXT.search(content.decode("utf-8", errors="replace")):
            raise SystemExit(f"absolute host/build path in source plan: {destination}")
        if b"github_" + b"pat_" in content:
            raise SystemExit(f"credential-bearing text in source plan: {destination}")
        entries.append({
            "path": destination,
            "source": source,
            "categories": sorted(entry_categories[destination]),
            "sha256": hashlib.sha256(content).hexdigest(),
            "mode": f"{mode:04o}",
        })

    plan = {
        "plan_version": 1,
        "firefox_base_commit": FIREFOX_BASE,
        "naivefox_reference_commit": run(repo, "merge-base", "naivefox", source_commit),
        "minimal_export_commit": source_commit,
        "build_report_source_commit": build_source_commit,
        "closure_report_source_commits": sorted(closure_source_commits),
        "generated_at": generated_at,
        "commit_epoch": commit_epoch,
        "evidence": sorted(evidence, key=lambda item: (item["kind"], item["path"])),
        "entries": entries,
        "generated_contents": generated_contents,
        "cargo_license_inventory": [
            cargo_license_inventory[key] for key in sorted(cargo_license_inventory)
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)
    print(
        f"minimal-source plan passed: {len(entries)} files, "
        f"{len(directory_contracts)} directory contracts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
