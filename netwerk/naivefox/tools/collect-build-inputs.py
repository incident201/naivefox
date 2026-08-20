#!/usr/bin/env python3

"""Collect target-specific standalone build inputs from a validated objdir."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path


SOURCE_PATH = re.compile(r"(?:^|[\s(])(/[^\s)]+)")
INCLUDE_PATH = re.compile(r"-I(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))")
MAKE_SOURCE_PATH = re.compile(r"\$\((topsrcdir|TOPSRCDIR|srcdir)\)(/[^\s\\'\"():;,]+)")


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-tree", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cargo-target", required=True)
    parser.add_argument("--mozconfig", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    repo = args.repo.resolve(strict=True)
    source_tree = (args.source_tree or repo).resolve(strict=True)
    objdir = args.objdir.resolve(strict=True)
    mozconfig = args.mozconfig.resolve(strict=True)
    output = args.output.resolve()
    if source_tree not in mozconfig.parents:
        raise SystemExit("mozconfig must be inside the analyzed source tree")
    if source_tree != repo and not args.source_commit:
        raise SystemExit("diagnostic source trees require --source-commit")

    status = git(repo, "status", "--porcelain=v1")
    if status and not args.allow_dirty:
        raise SystemExit("source checkout must be clean when collecting build inputs")

    tracked_raw = subprocess.check_output([
        "git",
        "-C",
        str(repo),
        "ls-files",
        "-z",
    ]).decode("utf-8", "surrogateescape")
    tracked = {value for value in tracked_raw.split("\0") if value}
    source_commit = args.source_commit or git(repo, "rev-parse", "HEAD")
    if subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{source_commit}^{{commit}}"],
        check=False,
    ).returncode:
        raise SystemExit(f"source commit does not exist: {source_commit}")
    categories: dict[str, set[str]] = defaultdict(set)
    directory_contracts = set()

    def add_path(path: Path, category: str) -> None:
        try:
            relative = path.resolve().relative_to(source_tree).as_posix()
        except (OSError, ValueError):
            return
        if relative in tracked and (source_tree / relative).is_file():
            categories[category].add(relative)

    def add_make_path(value: str, category: str) -> None:
        matches = (
            glob.glob(value, recursive=False) if glob.has_magic(value) else [value]
        )
        for match in matches:
            path = Path(match)
            if path.is_dir():
                try:
                    relative = path.resolve().relative_to(source_tree).as_posix()
                except (OSError, ValueError):
                    continue
                directory_contracts.add(relative)
            else:
                add_path(path, category)

    evidence_names = ("backend.RecursiveMakeBackend.in", "config_status_deps.in")
    evidence = {}
    for name in evidence_names:
        path = objdir / name
        if not path.is_file():
            raise SystemExit(f"objdir evidence is missing: {path}")
        evidence[name] = sha256(path)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"{source_tree}/"):
                add_path(Path(line), f"objdir:{name}")

    depfiles = []
    makefiles = []
    manifest_lists = []
    backends = []
    for root, directories, names in os.walk(objdir):
        directories[:] = [
            directory for directory in directories if directory != "naivefox-fixture"
        ]
        directory = Path(root)
        for name in names:
            path = directory / name
            if name.endswith((".d", ".pp")):
                depfiles.append(path)
            if name in {"Makefile", "backend.mk"}:
                makefiles.append(path)
            if name == "backend.mk":
                backends.append(path)
            if name == "manifest-lists.json":
                manifest_lists.append(path)
    depfiles.sort()
    makefiles.sort()
    manifest_lists.sort()
    backends.sort()
    for depfile in depfiles:
        text = depfile.read_text(encoding="utf-8", errors="replace").replace(
            "\\\n", " "
        )
        for match in SOURCE_PATH.finditer(text):
            add_path(
                Path(match.group(1).rstrip(":")), "objdir:generated-action-depfile"
            )
        dependency_base = (
            depfile.parent.parent if depfile.parent.name == ".deps" else depfile.parent
        )
        for token in text.split():
            value = token.rstrip(":")
            if (
                not value
                or value.startswith(("/", "$", "-"))
                or value.endswith((".o", ".obj"))
            ):
                continue
            add_path(
                dependency_base / value,
                "objdir:generated-action-relative-depfile",
            )

    for manifest_list in manifest_lists:
        try:
            data = json.loads(manifest_list.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for value in data.get("manifests", []):
            add_path(Path(value), "objdir:component-manifest")

    makefile_digest = hashlib.sha256()
    absolute_source = re.compile(re.escape(str(source_tree)) + r"/[^\s\\'\"():;,]+")
    for makefile in makefiles:
        text = makefile.read_text(encoding="utf-8", errors="replace")
        makefile_digest.update(makefile.relative_to(objdir).as_posix().encode())
        makefile_digest.update(b"\0")
        makefile_digest.update(text.encode())
        makefile_digest.update(b"\0")
        source_dir = source_tree / makefile.parent.relative_to(objdir)
        for match in MAKE_SOURCE_PATH.finditer(text):
            root = source_tree if match.group(1) != "srcdir" else source_dir
            add_make_path(
                str(root) + match.group(2), "objdir:generated-make-prerequisite"
            )
        for match in absolute_source.finditer(text):
            add_make_path(match.group(0), "objdir:generated-make-prerequisite")

    for backend in backends:
        text = backend.read_text(encoding="utf-8", errors="replace")
        for match in INCLUDE_PATH.finditer(text):
            value = next(item for item in match.groups() if item is not None)
            try:
                path = Path(value).resolve()
                relative = path.relative_to(source_tree).as_posix()
            except (OSError, ValueError):
                continue
            if path.is_dir():
                directory_contracts.add(relative)

    metadata = json.loads(
        subprocess.check_output(
            [
                "cargo",
                "metadata",
                "--manifest-path",
                str(source_tree / "Cargo.toml"),
                "--format-version",
                "1",
                "--filter-platform",
                args.cargo_target,
                "--no-default-features",
                "--features",
                "gkrust/naivefox",
                "--frozen",
            ],
            cwd=objdir,
            text=True,
        )
    )
    packages_by_id = {package["id"]: package for package in metadata["packages"]}
    nodes_by_id = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    root_ids = {
        package["id"]
        for package in metadata["packages"]
        if package["name"] in {"gkrust", "oxilangtag-ffi"} and package["source"] is None
    }
    if {packages_by_id[value]["name"] for value in root_ids} != {
        "gkrust",
        "oxilangtag-ffi",
    }:
        raise SystemExit("Cargo metadata is missing a required NaiveFox build root")
    reachable_package_ids = set()
    pending_package_ids = list(root_ids)
    while pending_package_ids:
        package_id = pending_package_ids.pop()
        if package_id in reachable_package_ids:
            continue
        reachable_package_ids.add(package_id)
        for dependency in nodes_by_id[package_id]["deps"]:
            if any(
                kind.get("kind") != "dev" for kind in dependency.get("dep_kinds", [])
            ):
                pending_package_ids.append(dependency["pkg"])

    package_dirs = set()
    cargo_packages = []
    for package_id in sorted(reachable_package_ids):
        package = packages_by_id[package_id]
        manifest = Path(package["manifest_path"]).resolve()
        relative_manifest = None
        license_file = None
        license_candidates = []
        try:
            relative_manifest = manifest.relative_to(source_tree).as_posix()
            package_directory = manifest.parent.relative_to(source_tree).as_posix()
            package_dirs.add(package_directory)
        except ValueError:
            package_directory = None
        if package_directory:
            declared_license_file = package.get("license_file")
            if declared_license_file:
                candidate = Path(declared_license_file).resolve()
                try:
                    value = candidate.relative_to(source_tree).as_posix()
                except ValueError:
                    pass
                else:
                    if value in tracked:
                        license_file = value
            prefix = f"{package_directory.rstrip('/')}/"
            for value in tracked:
                if not value.startswith(prefix):
                    continue
                name = Path(value).name.lower()
                if name.startswith(("license", "copying", "notice")):
                    license_candidates.append(value)
        if license_file:
            license_coverage = [license_file]
        elif license_candidates:
            license_coverage = sorted(license_candidates)
        elif package["source"] is None and not (package_directory or "").startswith(
            "third_party/"
        ):
            license_coverage = ["LICENSE"]
        elif package.get("license"):
            license_coverage = ["toolkit/content/license.html"]
        else:
            license_coverage = []
        cargo_packages.append({
            "name": package["name"],
            "version": package["version"],
            "source": package.get("source"),
            "manifest_path": relative_manifest,
            "license": package.get("license"),
            "license_file": license_file,
            "license_candidates": sorted(license_candidates),
            "license_coverage": license_coverage,
        })
    prefixes = tuple(f"{value.rstrip('/')}" + "/" for value in package_dirs)
    for value in tracked:
        if value.startswith(prefixes):
            categories["cargo:target-build-closure"].add(value)

    root_manifest = tomllib.loads(
        (source_tree / "Cargo.toml").read_text(encoding="utf-8")
    )
    for patches in root_manifest.get("patch", {}).values():
        for specification in patches.values():
            if not isinstance(specification, dict) or "path" not in specification:
                continue
            directory = (source_tree / specification["path"]).resolve()
            for name in ("Cargo.toml", "build.rs", "src/lib.rs", "src/main.rs"):
                add_path(directory / name, "cargo:local-patch-metadata")

    files = sorted(set().union(*categories.values()))
    missing_contracts = sorted(
        directory
        for directory in directory_contracts
        if not any(
            value == directory or value.startswith(f"{directory}/") for value in files
        )
    )
    report = {
        "report_version": 1,
        "target": args.target,
        "cargo_target": args.cargo_target,
        "source_tree_kind": "repository" if source_tree == repo else "diagnostic",
        "source_commit": source_commit,
        "source_worktree_clean": source_tree == repo and not bool(status),
        "source_worktree_status": status.splitlines(),
        "firefox_base_commit": "8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6",
        "collector_sha256": sha256(Path(__file__).resolve()),
        "mozconfig": mozconfig.relative_to(source_tree).as_posix(),
        "mozconfig_sha256": sha256(mozconfig),
        "objdir_evidence_sha256": evidence,
        "generated_makefile_count": len(makefiles),
        "generated_makefiles_sha256": makefile_digest.hexdigest(),
        "depfile_count": len(depfiles),
        "cargo_package_count": len(reachable_package_ids),
        "cargo_build_roots": sorted(
            packages_by_id[value]["name"] for value in root_ids
        ),
        "cargo_workspace_member_count": len(metadata["workspace_members"]),
        "cargo_packages": sorted(
            cargo_packages,
            key=lambda package: (
                package["name"],
                package["version"],
                package["source"] or "",
            ),
        ),
        "files": files,
        "directory_contracts": missing_contracts,
        "category_counts": {
            name: len(values) for name, values in sorted(categories.items())
        },
        "counts": {
            "files": len(files),
            "directory_contracts": len(missing_contracts),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    print(
        f"wrote {output}: {len(files)} files, {len(missing_contracts)} "
        f"directory contracts, {len(depfiles)} depfiles"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
