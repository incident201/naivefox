#!/usr/bin/env python3

"""Generate, validate, and install the complete NaiveFox evidence bundle."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from provenance import (
    CANONICAL_REPORT_PATHS,
    TARGET_SPECS,
    derive_source_provenance,
    load_and_validate_report_bundle,
    require_clean_tree,
)

TARGETS = TARGET_SPECS
STAGERS = {
    "linux-x86_64": "stage-runtime.sh",
    "windows-x86_64": "stage-runtime-windows-x86_64.sh",
    "android-aarch64": "stage-runtime-android-aarch64.sh",
}


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def evidence_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["NAIVEFOX_ENABLE_TESTS"] = "0"
    # Evidence must not silently depend on a long-lived compiler-cache daemon.
    # Keep this aligned with build-product.sh so provenance refreshes cannot
    # reintroduce the recurring stale/unavailable sccache failure mode.
    environment["NAIVEFOX_DISABLE_SCCACHE"] = "1"
    environment["SCCACHE_DISABLE"] = "1"
    environment.pop("USE_SCCACHE", None)
    return environment


def _resolved_metadata_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"mozinfo {label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"mozinfo {label} must be an absolute path")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"mozinfo {label} does not exist: {value}") from error


def validate_objdir(repo: Path, objdir: Path, target: dict[str, object]) -> dict:
    mozinfo_path = objdir / "mozinfo.json"
    if not mozinfo_path.is_file():
        raise ValueError(f"missing regular {target['name']} mozinfo.json")
    try:
        mozinfo = json.loads(mozinfo_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {target['name']} mozinfo.json: {error}") from error
    if not isinstance(mozinfo, dict):
        raise ValueError(f"{target['name']} mozinfo.json must contain an object")
    if _resolved_metadata_path(mozinfo.get("topsrcdir"), "topsrcdir") != repo:
        raise ValueError(f"{target['name']} objdir belongs to a different source tree")
    if _resolved_metadata_path(mozinfo.get("topobjdir"), "topobjdir") != objdir:
        raise ValueError(f"{target['name']} mozinfo topobjdir does not match argument")
    expected_mozconfig = (repo / target["mozconfig"]).resolve(strict=True)
    if (
        _resolved_metadata_path(mozinfo.get("mozconfig"), "mozconfig")
        != expected_mozconfig
    ):
        raise ValueError(f"{target['name']} objdir uses the wrong mozconfig")
    expected_values = {
        "buildapp": "netwerk/naivefox",
        "appname": "naivefox",
        **target["mozinfo"],
    }
    for field, expected in expected_values.items():
        if mozinfo.get(field) != expected:
            raise ValueError(
                f"{target['name']} mozinfo {field} mismatch: "
                f"{mozinfo.get(field)!r} != {expected!r}"
            )
    if mozinfo.get("tests_enabled") is not False:
        raise ValueError(f"{target['name']} evidence objdir must have tests disabled")
    required_outputs = [
        "config.status",
        target["link_response"],
        target["libxul"],
    ]
    if target["executable"]:
        required_outputs.append(target["executable"])
    for relative in required_outputs:
        output = objdir / relative
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError(
                f"{target['name']} objdir is stale or incomplete: missing {relative}"
            )
    return mozinfo


def validate_objdirs(repo: Path, objdirs: dict[str, Path]) -> dict[str, dict]:
    expected_names = {target["name"] for target in TARGETS}
    if set(objdirs) != expected_names:
        raise ValueError(f"evidence objdirs must be exactly {sorted(expected_names)}")
    if len(set(objdirs.values())) != len(objdirs):
        raise ValueError("all evidence objdirs must be distinct")
    validated = {}
    for target in TARGETS:
        objdir = objdirs[target["name"]]
        if objdir == repo or repo in objdir.parents:
            raise ValueError(f"{target['name']} objdir must be outside the checkout")
        validated[target["name"]] = validate_objdir(repo, objdir, target)
    return validated


def build_target(
    repo: Path,
    objdir: Path,
    target: dict[str, object],
    source,
    firefox_ref: str,
    naivefox_ref: str,
    environment: dict[str, str],
) -> None:
    build_environment = environment.copy()
    build_environment["MOZCONFIG"] = str((repo / target["mozconfig"]).resolve())
    build_environment["NAIVEFOX_OBJDIR"] = str(objdir)
    build_environment["NAIVEFOX_ENABLE_TESTS"] = "0"
    run(
        [str(repo / "mach"), "build", "-j4"],
        cwd=repo,
        env=build_environment,
    )
    current = derive_source_provenance(
        repo, firefox_ref=firefox_ref, naivefox_ref=naivefox_ref
    )
    if current != source:
        raise ValueError(f"source or references changed during {target['name']} build")
    require_clean_tree(repo, f"{target['name']} build changed source commit S")
    validate_objdir(repo, objdir, target)


def changed_paths(repo: Path) -> set[Path]:
    output = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain=v1"], text=True
    )
    return {Path(line[3:]) for line in output.splitlines() if line}


def stage_target(
    repo: Path,
    objdir: Path,
    target: dict[str, object],
    package: Path,
    environment: dict[str, str],
) -> None:
    run(
        [
            "bash",
            str(repo / "netwerk/naivefox/tools" / STAGERS[target["name"]]),
            str(package),
        ],
        cwd=repo,
        env={**environment, "NAIVEFOX_OBJDIR": str(objdir)},
    )


def validate_targets(reports: dict[Path, dict]) -> None:
    build_targets = {
        report.get("target")
        for path, report in reports.items()
        if path.name.startswith("build-inputs-")
    }
    configure_targets = {
        report.get("target")
        for path, report in reports.items()
        if path.name.startswith("configure-inputs-")
    }
    closure_targets = {
        report.get("report_provenance", {}).get("target_triple")
        for path, report in reports.items()
        if path.name.startswith("closure-report-")
    }
    if build_targets != {target["name"] for target in TARGETS}:
        raise ValueError("build evidence does not contain exactly all targets")
    if configure_targets != {target["name"] for target in TARGETS}:
        raise ValueError("configure evidence does not contain exactly all targets")
    if closure_targets != {target["cargo_target"] for target in TARGETS}:
        raise ValueError("closure evidence does not contain exactly all targets")


def install_bundle(repo: Path, generated: dict[Path, Path]) -> None:
    destinations = {relative: repo / relative for relative in CANONICAL_REPORT_PATHS}
    backups = {
        relative: destination.read_bytes() if destination.exists() else None
        for relative, destination in destinations.items()
    }
    staged: dict[Path, Path] = {}
    try:
        for relative, destination in destinations.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f".{destination.name}.evidence-{os.getpid()}.tmp"
            )
            shutil.copy2(generated[relative], temporary)
            staged[relative] = temporary
        for relative, destination in destinations.items():
            os.replace(staged[relative], destination)
        changed = changed_paths(repo)
        if changed != set(CANONICAL_REPORT_PATHS):
            raise ValueError(
                "evidence installation must leave exactly the canonical reports "
                f"modified, got {sorted(path.as_posix() for path in changed)}"
            )
    except Exception:
        for relative, destination in destinations.items():
            previous = backups[relative]
            if previous is None:
                destination.unlink(missing_ok=True)
                continue
            rollback = destination.with_name(
                f".{destination.name}.evidence-{os.getpid()}.rollback"
            )
            rollback.write_bytes(previous)
            os.replace(rollback, destination)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[3]
    )
    parser.add_argument("--linux-objdir", type=Path, required=True)
    parser.add_argument("--windows-objdir", type=Path, required=True)
    parser.add_argument("--android-objdir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--firefox-ref", default="firefox-upstream")
    parser.add_argument("--naivefox-ref", default="naivefox-full-source")
    args = parser.parse_args()

    repo = args.repo.resolve(strict=True)
    objdirs = {
        "linux-x86_64": args.linux_objdir.resolve(strict=True),
        "windows-x86_64": args.windows_objdir.resolve(strict=True),
        "android-aarch64": args.android_objdir.resolve(strict=True),
    }
    try:
        require_clean_tree(
            repo, "source commit S must be clean before evidence collection"
        )
        source = derive_source_provenance(
            repo,
            firefox_ref=args.firefox_ref,
            naivefox_ref=args.naivefox_ref,
        )
        validate_objdirs(repo, objdirs)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    automatic_work = args.work_dir is None
    if automatic_work:
        work_dir = Path(tempfile.mkdtemp(prefix="naivefox-evidence-"))
    else:
        work_dir = args.work_dir.resolve()
        if work_dir.exists() and any(work_dir.iterdir()):
            raise SystemExit(f"evidence work directory must be empty: {work_dir}")
        work_dir.mkdir(parents=True, exist_ok=True)
    if work_dir == repo or repo in work_dir.parents:
        raise SystemExit("evidence work directory must be outside the checkout")

    tools = repo / "netwerk/naivefox/tools"
    output_dir = work_dir / "reports"
    output_dir.mkdir()
    ref_args = [
        "--firefox-ref",
        args.firefox_ref,
        "--naivefox-ref",
        args.naivefox_ref,
    ]
    environment = evidence_environment()
    stages = ExitStack()
    try:
        package_args = []
        for target in TARGETS:
            objdir = objdirs[target["name"]]
            build_target(
                repo,
                objdir,
                target,
                source,
                args.firefox_ref,
                args.naivefox_ref,
                environment,
            )
            stage_root = Path(
                stages.enter_context(
                    tempfile.TemporaryDirectory(prefix=".naivefox-evidence-", dir=objdir)
                )
            )
            package = stage_root / "package"
            stage_target(repo, objdir, target, package, environment)
            platform = target["name"].split("-", 1)[0]
            package_args.extend([f"--{platform}-package-dir", str(package)])
        validate_objdirs(repo, objdirs)
        run(
            [
                sys.executable,
                str(tools / "analyze-full-closure.py"),
                "--repo",
                str(repo),
                "--output-dir",
                str(output_dir),
                "--linux-objdir",
                str(objdirs["linux-x86_64"]),
                "--windows-objdir",
                str(objdirs["windows-x86_64"]),
                "--android-objdir",
                str(objdirs["android-aarch64"]),
                *package_args,
                *ref_args,
            ],
            env=environment,
        )
        for target in TARGETS:
            name = target["name"]
            mozconfig = repo / target["mozconfig"]
            run(
                [
                    sys.executable,
                    str(tools / "collect-build-inputs.py"),
                    str(output_dir / f"build-inputs-{name}.json"),
                    "--repo",
                    str(repo),
                    "--objdir",
                    str(objdirs[name]),
                    "--target",
                    name,
                    "--cargo-target",
                    target["cargo_target"],
                    "--mozconfig",
                    str(mozconfig),
                    *ref_args,
                ],
                env=environment,
            )
            run(
                [
                    sys.executable,
                    str(tools / "collect-configure-inputs.py"),
                    str(output_dir / f"configure-inputs-{name}.json"),
                    "--repo",
                    str(repo),
                    "--target",
                    name,
                    "--target-triple",
                    target["configure_target"],
                    "--mozconfig",
                    str(mozconfig),
                    "--objdir",
                    str(work_dir / f"configure-{name}"),
                    *ref_args,
                ],
                env=environment,
            )
        current = derive_source_provenance(
            repo,
            firefox_ref=args.firefox_ref,
            naivefox_ref=args.naivefox_ref,
        )
        if current.source_commit != source.source_commit:
            raise ValueError("source commit changed during evidence collection")
        if current != source:
            raise ValueError("source references changed during evidence collection")
        require_clean_tree(repo, "evidence collection changed source commit S")
        generated = {
            relative: output_dir / relative.name for relative in CANONICAL_REPORT_PATHS
        }
        reports = load_and_validate_report_bundle(
            repo,
            generated.values(),
            source.source_commit,
            source.firefox_base_commit,
            source.naivefox_reference_commit,
        )
        validate_targets(reports)
        install_bundle(repo, generated)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise SystemExit(str(error)) from error
    finally:
        stages.close()
        if automatic_work:
            shutil.rmtree(work_dir, ignore_errors=True)

    print(
        f"installed {len(CANONICAL_REPORT_PATHS)} validated reports for source S "
        f"{source.source_commit}; commit them together as evidence E"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
