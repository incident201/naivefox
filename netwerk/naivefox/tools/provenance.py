#!/usr/bin/env python3

"""Shared Git provenance and evidence validation for NaiveFox releases."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

OID_PATTERN = re.compile(r"[0-9a-f]{40}")
REPORT_DIRECTORY = Path("netwerk/naivefox/reports")
REPORT_SPECS = {
    REPORT_DIRECTORY / "build-inputs-linux-x86_64.json": {
        "kind": "build",
        "target": "linux-x86_64",
        "mozconfig": "netwerk/naivefox/mozconfig-minimal",
    },
    REPORT_DIRECTORY / "build-inputs-windows-x86_64.json": {
        "kind": "build",
        "target": "windows-x86_64",
        "mozconfig": "netwerk/naivefox/mozconfig-windows-x86_64",
    },
    REPORT_DIRECTORY / "closure-report-linux-x86_64.json": {
        "kind": "closure",
        "target_triple": "x86_64-unknown-linux-gnu",
        "mozconfig": "netwerk/naivefox/mozconfig-minimal",
    },
    REPORT_DIRECTORY / "closure-report-windows-x86_64.json": {
        "kind": "closure",
        "target_triple": "x86_64-pc-windows-msvc",
        "mozconfig": "netwerk/naivefox/mozconfig-windows-x86_64",
    },
    REPORT_DIRECTORY / "configure-inputs-linux-x86_64.json": {
        "kind": "configure",
        "target": "linux-x86_64",
        "target_triple": "x86_64-pc-linux-gnu",
        "mozconfig": "netwerk/naivefox/mozconfig-minimal",
    },
    REPORT_DIRECTORY / "configure-inputs-windows-x86_64.json": {
        "kind": "configure",
        "target": "windows-x86_64",
        "target_triple": "x86_64-pc-mingw32",
        "mozconfig": "netwerk/naivefox/mozconfig-windows-x86_64",
    },
}
CANONICAL_REPORT_PATHS = tuple(REPORT_SPECS)


@dataclass(frozen=True)
class SourceProvenance:
    source_commit: str
    firefox_base_commit: str
    naivefox_reference_commit: str


@dataclass(frozen=True)
class EvidenceProvenance:
    source_commit: str
    evidence_commit: str
    firefox_base_commit: str
    naivefox_reference_commit: str


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as error:
        detail = error.output.strip()
        raise ValueError(detail or f"git {' '.join(args)} failed") from error


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_oid(repo: Path, value: str, label: str) -> str:
    if not isinstance(value, str) or not OID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be one canonical 40-hex commit OID")
    resolved = resolve_commit(repo, value, label)
    if resolved != value:
        raise ValueError(f"{label} does not resolve to itself: {value}")
    return value


def resolve_commit(repo: Path, ref: str, label: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise ValueError(f"{label} reference is empty")
    resolved = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not OID_PATTERN.fullmatch(resolved):
        raise ValueError(f"{label} did not resolve to a canonical commit OID")
    return resolved


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def derive_source_provenance(
    repo: Path,
    source_ref: str = "HEAD",
    firefox_ref: str = "firefox-upstream",
    naivefox_ref: str = "naivefox-full-source",
) -> SourceProvenance:
    repo = repo.resolve(strict=True)
    source = resolve_commit(repo, source_ref, "source")
    firefox = resolve_commit(repo, firefox_ref, "Firefox reference")
    naivefox = resolve_commit(repo, naivefox_ref, "NaiveFox reference")
    firefox_base = git(repo, "merge-base", source, firefox)
    naivefox_base = git(repo, "merge-base", source, naivefox)
    canonical_oid(repo, firefox_base, "derived Firefox base")
    canonical_oid(repo, naivefox_base, "derived NaiveFox reference")
    if not is_ancestor(repo, firefox_base, source) or not is_ancestor(
        repo, firefox_base, firefox
    ):
        raise ValueError("derived Firefox base does not belong to both histories")
    if not is_ancestor(repo, naivefox_base, source) or not is_ancestor(
        repo, naivefox_base, naivefox
    ):
        raise ValueError("derived NaiveFox reference does not belong to both histories")
    return SourceProvenance(source, firefox_base, naivefox_base)


def require_clean_tree(repo: Path, message: str = "checkout must be clean") -> None:
    if git(repo, "status", "--porcelain=v1"):
        raise ValueError(message)


def _changed_paths(repo: Path, older: str, newer: str) -> set[Path]:
    output = git(repo, "diff", "--name-only", f"{older}..{newer}")
    return {Path(value) for value in output.splitlines() if value}


def validate_evidence_head(
    repo: Path,
    head_ref: str = "HEAD",
    firefox_ref: str = "firefox-upstream",
    naivefox_ref: str = "naivefox-full-source",
    require_clean: bool = True,
) -> EvidenceProvenance:
    repo = repo.resolve(strict=True)
    if require_clean:
        require_clean_tree(repo, "minimal checkout must be clean at evidence commit E")
    evidence = resolve_commit(repo, head_ref, "evidence")
    parents = git(repo, "show", "-s", "--format=%P", evidence).split()
    if len(parents) != 1:
        raise ValueError("evidence commit E must have exactly one parent S")
    source = canonical_oid(repo, parents[0], "evidence source S")
    changed = _changed_paths(repo, source, evidence)
    expected = set(CANONICAL_REPORT_PATHS)
    if changed != expected:
        missing = sorted(path.as_posix() for path in expected - changed)
        extra = sorted(path.as_posix() for path in changed - expected)
        raise ValueError(
            "evidence commit E must change exactly the six canonical reports; "
            f"missing={missing}, extra={extra}"
        )
    derived = derive_source_provenance(
        repo, source, firefox_ref=firefox_ref, naivefox_ref=naivefox_ref
    )
    return EvidenceProvenance(
        source,
        evidence,
        derived.firefox_base_commit,
        derived.naivefox_reference_commit,
    )


def _report_source(report: dict) -> str | None:
    provenance = report.get("report_provenance")
    if isinstance(provenance, dict):
        return provenance.get("source_commit_sha")
    return report.get("source_commit")


def _report_firefox_base(report: dict) -> str | None:
    provenance = report.get("report_provenance")
    if isinstance(provenance, dict):
        return provenance.get("firefox_base_sha")
    return report.get("firefox_base_commit")


def _report_naivefox_reference(report: dict) -> str | None:
    provenance = report.get("report_provenance")
    if isinstance(provenance, dict):
        return provenance.get("naivefox_reference_sha")
    return report.get("naivefox_reference_commit")


def _report_provenance_field(report: dict, field: str) -> object:
    provenance = report.get("report_provenance")
    if isinstance(provenance, dict):
        return provenance.get(field)
    return report.get(field)


def load_and_validate_report_bundle(
    repo: Path,
    report_paths: Iterable[Path],
    source_commit: str,
    firefox_base_commit: str,
    naivefox_reference_commit: str,
) -> dict[Path, dict]:
    repo = repo.resolve(strict=True)
    source_commit = canonical_oid(repo, source_commit, "report source S")
    firefox_base_commit = canonical_oid(repo, firefox_base_commit, "Firefox base")
    naivefox_reference_commit = canonical_oid(
        repo, naivefox_reference_commit, "NaiveFox reference"
    )
    expected_tools = {
        "collector_sha256": {
            "build": sha256(repo / "netwerk/naivefox/tools/collect-build-inputs.py"),
            "configure": sha256(
                repo / "netwerk/naivefox/tools/collect-configure-inputs.py"
            ),
        },
        "analyzer_sha256": sha256(
            repo / "netwerk/naivefox/tools/analyze-full-closure.py"
        ),
        "provenance_sha256": sha256(repo / "netwerk/naivefox/tools/provenance.py"),
        "evidence_collector_sha256": sha256(
            repo / "netwerk/naivefox/tools/collect-minimal-source-evidence.py"
        ),
    }
    loaded: dict[Path, dict] = {}
    for report_path in report_paths:
        resolved_path = report_path.resolve(strict=True)
        report = json.loads(resolved_path.read_text(encoding="utf-8"))
        name = resolved_path.name
        relative = REPORT_DIRECTORY / name
        spec = REPORT_SPECS.get(relative)
        if spec is None:
            raise ValueError(f"non-canonical evidence report: {name}")
        kind = spec["kind"]
        if "target" in spec and report.get("target") != spec["target"]:
            raise ValueError(f"{name} target does not match its canonical filename")
        if kind == "closure":
            report_target_triple = report.get("report_provenance", {}).get(
                "target_triple"
            )
        else:
            report_target_triple = report.get("target_triple")
        if "target_triple" in spec and report_target_triple != spec["target_triple"]:
            raise ValueError(
                f"{name} target triple does not match its canonical filename"
            )
        canonical_oid(repo, _report_source(report), f"{name} source commit")
        canonical_oid(repo, _report_firefox_base(report), f"{name} Firefox base")
        canonical_oid(
            repo,
            _report_naivefox_reference(report),
            f"{name} NaiveFox reference",
        )
        if _report_source(report) != source_commit:
            raise ValueError(f"{name} does not attest source commit S")
        if _report_provenance_field(report, "source_worktree_clean") is not True:
            raise ValueError(f"{name} was not collected from a clean source S")
        if _report_firefox_base(report) != firefox_base_commit:
            raise ValueError(f"{name} Firefox base does not match source S")
        if _report_naivefox_reference(report) != naivefox_reference_commit:
            raise ValueError(f"{name} NaiveFox reference does not match source S")
        if _report_provenance_field(report, "provenance_version") != 2:
            raise ValueError(f"{name} does not use provenance schema 2")
        if (
            _report_provenance_field(report, "provenance_sha256")
            != expected_tools["provenance_sha256"]
        ):
            raise ValueError(f"{name} provenance helper hash mismatch")
        if (
            _report_provenance_field(report, "evidence_collector_sha256")
            != expected_tools["evidence_collector_sha256"]
        ):
            raise ValueError(f"{name} evidence collector hash mismatch")
        tool_field = "analyzer_sha256" if kind == "closure" else "collector_sha256"
        expected = (
            expected_tools["analyzer_sha256"]
            if kind == "closure"
            else expected_tools["collector_sha256"][kind]
        )
        if _report_provenance_field(report, tool_field) != expected:
            raise ValueError(f"{name} generator hash mismatch")
        if (
            kind == "configure"
            and report.get("configure_environment", {}).get("NAIVEFOX_ENABLE_TESTS")
            != "0"
        ):
            raise ValueError(f"{name} does not attest tests-disabled configure")
        mozconfig = _report_provenance_field(report, "mozconfig_path")
        if mozconfig is None:
            mozconfig = report.get("mozconfig")
        mozconfig_hash = _report_provenance_field(report, "mozconfig_sha256")
        if not isinstance(mozconfig, str) or not isinstance(mozconfig_hash, str):
            raise ValueError(f"{name} has incomplete mozconfig provenance")
        if mozconfig != spec["mozconfig"]:
            raise ValueError(f"{name} mozconfig does not match its canonical filename")
        mozconfig_path = (repo / mozconfig).resolve(strict=True)
        if (
            repo not in mozconfig_path.parents
            or sha256(mozconfig_path) != mozconfig_hash
        ):
            raise ValueError(f"{name} mozconfig hash mismatch")
        loaded[resolved_path] = report
    return loaded
