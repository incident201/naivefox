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
MAKE_SOURCE_PATH = re.compile(
    r"\$\((topsrcdir|TOPSRCDIR|MOZILLA_DIR|srcdir)\)(/[^\s\\'\"():;,]+)"
)
GYP_SOURCE_PATH = re.compile(r"[\"']([^\"']+\.gypi?)[\"']")
PY_ACTION = re.compile(r"call\s+py_action,\s*([A-Za-z0-9_+-]+)")


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

    def source_relative(path: Path) -> str | None:
        try:
            resolved = path.resolve()
            if resolved == objdir or objdir in resolved.parents:
                return None
            relative = resolved.relative_to(source_tree)
        except (OSError, ValueError):
            return None
        if any(part == "objdir" or part.startswith("obj-") for part in relative.parts):
            return None
        return relative.as_posix()

    def add_path(path: Path, category: str) -> None:
        relative = source_relative(path)
        if relative is None:
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
                relative = source_relative(path)
                if relative is not None:
                    directory_contracts.add(relative)
            else:
                add_path(path, category)

    # The product build virtualenv is a semantic build input in its own right.
    # It is not visible in compiler depfiles, and configure succeeds when some
    # vendored import roots are absent because they are only imported by later
    # generated actions.  Retain the repository-owned Python packages declared
    # by the build site, but deliberately exclude the WPT-only import roots:
    # NaiveFox does not build or run the WPT harness as part of its product and
    # focused acceptance gates.
    build_site_manifest = source_tree / "python" / "sites" / "build.txt"
    if not build_site_manifest.is_file():
        raise SystemExit(f"build virtualenv manifest is missing: {build_site_manifest}")
    add_path(build_site_manifest, "python:build-site-manifest")
    build_site_roots = set()
    for raw_line in build_site_manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("pth:"):
            value = line.removeprefix("pth:").rstrip("/")
        elif line.startswith("vendored:third_party/python/"):
            value = line.removeprefix("vendored:").rstrip("/")
        else:
            continue
        root = (source_tree / value).resolve()
        try:
            relative_root = root.relative_to(source_tree).as_posix()
        except ValueError as error:
            raise SystemExit(
                f"build virtualenv path escapes the source tree: {value}"
            ) from error
        if not root.is_dir():
            raise SystemExit(f"build virtualenv path is missing: {value}")
        build_site_roots.add(relative_root)
    build_site_prefixes = tuple(f"{value}/" for value in build_site_roots)
    for value in tracked:
        if value in build_site_roots or value.startswith(build_site_prefixes):
            categories["python:build-site-package"].add(value)
        if value.startswith("third_party/rust/") and value.endswith(
            "/.cargo-checksum.json"
        ):
            # Cargo validates the vendored replacement source while resolving
            # the unfiltered --all-features graph used by RunCbindgen.  This
            # metadata index is broader than the platform-filtered package set,
            # but it contains no crate implementation sources.
            categories["cargo:vendor-checksum-index"].add(value)

    for value in tracked:
        if (value.startswith("config/") and value.endswith(".mk")) or value == (
            "toolkit/mozapps/installer/upload-files.mk"
        ):
            add_path(source_tree / value, "make:core-build-infrastructure")

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
    make_fragments = []
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
            if name == "Makefile" or name.endswith(".mk"):
                make_fragments.append(path)
            if name == "backend.mk":
                backends.append(path)
            if name == "manifest-lists.json":
                manifest_lists.append(path)
    depfiles.sort()
    makefiles.sort()
    manifest_lists.sort()
    backends.sort()
    make_fragments.sort()

    # Installed headers and resources are generated from EXPORTS and related
    # moz.build declarations.  They do not necessarily occur in linker object
    # depfiles, so the target-specific install manifests are an independent
    # source-input class required by a clean standalone build.
    install_manifest_dir = objdir / "_build_manifests" / "install"
    if not install_manifest_dir.is_dir():
        raise SystemExit(
            f"objdir install manifests are missing: {install_manifest_dir}"
        )
    source_install_manifest_names = {
        # The mozbuild frontend validates test manifests and
        # TEST_HARNESS_FILES while generating the product backend even when
        # tests are disabled.  Keep the exact target manifests rather than
        # walking test directories broadly.
        "_test_files",
        "_tests",
        "dist_bin",
        "dist_include",
        "dist_private",
        "dist_public",
        "xpidl",
    }
    install_manifests = sorted(
        path
        for path in install_manifest_dir.iterdir()
        if path.is_file() and path.name in source_install_manifest_names
    )
    manifest_names = {path.name for path in install_manifests}
    if not {"dist_bin", "dist_include"}.issubset(manifest_names):
        raise SystemExit(
            f"objdir product install manifests are incomplete: {install_manifest_dir}"
        )
    install_manifest_digest = hashlib.sha256()
    install_manifest_records = 0
    for manifest in install_manifests:
        content = manifest.read_bytes()
        install_manifest_digest.update(manifest.name.encode())
        install_manifest_digest.update(b"\0")
        install_manifest_digest.update(content)
        install_manifest_digest.update(b"\0")
        lines = content.decode("utf-8", "surrogateescape").splitlines()
        if not lines or lines[0] not in {"1", "2", "3", "4", "5"}:
            raise SystemExit(f"unknown install manifest format: {manifest}")
        for line in lines[1:]:
            fields = line.split("\x1f")
            if not fields:
                continue
            install_manifest_records += 1
            record_type = fields[0]
            if record_type in {"1", "2"}:
                if len(fields) != 3:
                    raise SystemExit(f"malformed install manifest record: {manifest}")
                add_path(Path(fields[2]), "objdir:install-manifest-source")
            elif record_type in {"5", "6"}:
                if len(fields) != 5:
                    raise SystemExit(f"malformed install pattern record: {manifest}")
                base, pattern = fields[2:4]
                for match in glob.glob(str(Path(base) / pattern), recursive=True):
                    add_path(Path(match), "objdir:install-manifest-pattern")
            elif record_type == "7":
                if len(fields) != 8:
                    raise SystemExit(f"malformed install preprocess record: {manifest}")
                add_path(Path(fields[2]), "objdir:install-manifest-source")
                add_path(Path(fields[3]), "objdir:install-manifest-dependency")
            elif record_type not in {"3", "4", "8"}:
                raise SystemExit(
                    f"unsupported install manifest record {record_type}: {manifest}"
                )

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

    for fragment in make_fragments:
        text = fragment.read_text(encoding="utf-8", errors="replace")
        source_dir = source_tree / fragment.parent.relative_to(objdir)
        for match in MAKE_SOURCE_PATH.finditer(text):
            root = source_tree if match.group(1) != "srcdir" else source_dir
            add_make_path(
                str(root) + match.group(2),
                "objdir:generated-make-fragment-source",
            )
        for match in absolute_source.finditer(text):
            add_make_path(match.group(0), "objdir:generated-make-fragment-source")

    active_source_makefiles = sorted(
        value for value in set().union(*categories.values()) if value.endswith(".mk")
    )
    for value in active_source_makefiles:
        path = source_tree / value
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in MAKE_SOURCE_PATH.finditer(text):
            root = source_tree if match.group(1) != "srcdir" else path.parent
            add_make_path(
                str(root) + match.group(2),
                "make:active-source-fragment-input",
            )

    for backend in backends:
        text = backend.read_text(encoding="utf-8", errors="replace")
        for match in INCLUDE_PATH.finditer(text):
            value = next(item for item in match.groups() if item is not None)
            path = Path(value)
            relative = source_relative(path)
            if path.is_dir():
                if relative is not None:
                    directory_contracts.add(relative)

    active_python_actions = set()
    for fragment in make_fragments:
        text = fragment.read_text(encoding="utf-8", errors="replace")
        for action in PY_ACTION.findall(text):
            path = source_tree / "python" / "mozbuild" / "mozbuild" / "action"
            path /= f"{action}.py"
            if path.is_file():
                add_path(path, "make:active-python-action")
                active_python_actions.add(action)

    # backend.RecursiveMakeBackend.in records recursively loaded GYP files,
    # but not necessarily the root named by an active GYP_DIRS declaration.
    # Recover those target entry points from the already target-filtered
    # moz.build inputs instead of walking every GYP file in the checkout.
    active_gyp_roots = set()
    active_mozbuilds = sorted(
        value
        for value in set().union(*categories.values())
        if Path(value).name == "moz.build"
    )
    for value in active_mozbuilds:
        path = source_tree / value
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in GYP_SOURCE_PATH.finditer(text):
            raw = match.group(1)
            candidate = (
                source_tree / raw.lstrip("/")
                if raw.startswith("/")
                else path.parent / raw
            )
            add_path(candidate, "mozbuild:active-gyp-entry")
            relative = source_relative(candidate)
            if relative is not None:
                active_gyp_roots.add(Path(relative).parent.as_posix())

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
    cbindgen_metadata = json.loads(
        subprocess.check_output(
            [
                "cargo",
                "metadata",
                "--manifest-path",
                str(source_tree / "Cargo.toml"),
                "--format-version",
                "1",
                "--all-features",
                "--frozen",
            ],
            cwd=objdir,
            text=True,
        )
    )
    packages_by_id = {package["id"]: package for package in metadata["packages"]}
    nodes_by_id = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    manifest_parse_packages = []
    for package in metadata["packages"]:
        manifest = Path(package["manifest_path"]).resolve()
        relative = source_relative(manifest)
        if relative is None:
            raise SystemExit(
                "Cargo metadata package manifest is outside the source tree: "
                f"{package['name']} {manifest}"
            )
        add_path(manifest, "cargo:target-manifest-parse-closure")
        target_entry_points = []
        for target in package.get("targets", []):
            if not {"lib", "proc-macro", "custom-build", "bin"}.intersection(
                target.get("kind", [])
            ):
                continue
            entry = Path(target["src_path"]).resolve()
            relative_entry = source_relative(entry)
            if relative_entry is None:
                raise SystemExit(
                    "Cargo metadata target entry is outside the source tree: "
                    f"{package['name']} {entry}"
                )
            add_path(entry, "cargo:target-manifest-parse-entry")
            target_entry_points.append(relative_entry)
        manifest_parse_packages.append({
            "name": package["name"],
            "version": package["version"],
            "manifest_path": relative,
            "source": package.get("source"),
            "target_entry_points": sorted(target_entry_points),
        })
    cbindgen_manifest_parse_packages = []
    for package in cbindgen_metadata["packages"]:
        manifest = Path(package["manifest_path"]).resolve()
        relative = source_relative(manifest)
        if relative is None:
            raise SystemExit(
                "cbindgen Cargo metadata package manifest is outside the source "
                f"tree: {package['name']} {manifest}"
            )
        add_path(manifest, "cargo:cbindgen-manifest-parse-closure")
        target_entry_points = []
        for target in package.get("targets", []):
            if not {"lib", "proc-macro", "custom-build", "bin"}.intersection(
                target.get("kind", [])
            ):
                continue
            entry = Path(target["src_path"]).resolve()
            relative_entry = source_relative(entry)
            if relative_entry is None:
                raise SystemExit(
                    "cbindgen Cargo metadata target entry is outside the source "
                    f"tree: {package['name']} {entry}"
                )
            add_path(entry, "cargo:cbindgen-manifest-parse-entry")
            target_entry_points.append(relative_entry)
        cbindgen_manifest_parse_packages.append({
            "name": package["name"],
            "version": package["version"],
            "manifest_path": relative,
            "source": package.get("source"),
            "target_entry_points": sorted(target_entry_points),
        })
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
    vendored_packages: dict[str, list[Path]] = defaultdict(list)
    for manifest in (source_tree / "third_party" / "rust").glob("*/Cargo.toml"):
        try:
            package_name = (
                tomllib
                .loads(manifest.read_text(encoding="utf-8"))
                .get("package", {})
                .get("name")
            )
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if package_name:
            vendored_packages[package_name].append(manifest)
    git_patch_packages = set()
    for patches in root_manifest.get("patch", {}).values():
        for alias, specification in patches.items():
            if not isinstance(specification, dict):
                continue
            if "path" in specification:
                directory = (source_tree / specification["path"]).resolve()
                for name in (
                    "Cargo.toml",
                    "build.rs",
                    "src/lib.rs",
                    "src/main.rs",
                ):
                    add_path(directory / name, "cargo:local-patch-metadata")
                continue
            if "git" not in specification:
                continue
            package_name = specification.get("package", alias)
            manifests = vendored_packages.get(package_name, [])
            if not manifests:
                raise SystemExit(
                    f"vendored Cargo git patch package is missing: {package_name}"
                )
            git_patch_packages.add(package_name)
            for manifest in manifests:
                add_path(manifest, "cargo:git-patch-manifest")
                data = tomllib.loads(manifest.read_text(encoding="utf-8"))
                package = data.get("package", {})
                library = data.get("lib")
                if isinstance(library, dict):
                    add_path(
                        manifest.parent / library.get("path", "src/lib.rs"),
                        "cargo:git-patch-entry",
                    )
                elif package.get("autolib", True):
                    add_path(
                        manifest.parent / "src" / "lib.rs",
                        "cargo:git-patch-entry",
                    )
                build_script = package.get("build")
                if isinstance(build_script, str):
                    add_path(
                        manifest.parent / build_script,
                        "cargo:git-patch-entry",
                    )
                elif build_script is not False:
                    add_path(
                        manifest.parent / "build.rs",
                        "cargo:git-patch-entry",
                    )

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
        "install_manifest_count": len(install_manifests),
        "install_manifest_names": sorted(manifest_names),
        "install_manifest_record_count": install_manifest_records,
        "install_manifests_sha256": install_manifest_digest.hexdigest(),
        "depfile_count": len(depfiles),
        "cargo_package_count": len(reachable_package_ids),
        "cargo_manifest_parse_package_count": len(manifest_parse_packages),
        "cargo_manifest_parse_packages": sorted(
            manifest_parse_packages,
            key=lambda package: (
                package["name"],
                package["version"],
                package["source"] or "",
            ),
        ),
        "cbindgen_manifest_parse_package_count": len(cbindgen_manifest_parse_packages),
        "cbindgen_manifest_parse_packages": sorted(
            cbindgen_manifest_parse_packages,
            key=lambda package: (
                package["name"],
                package["version"],
                package["source"] or "",
            ),
        ),
        "cargo_build_roots": sorted(
            packages_by_id[value]["name"] for value in root_ids
        ),
        "cargo_workspace_member_count": len(metadata["workspace_members"]),
        "active_gyp_roots": sorted(active_gyp_roots),
        "active_python_actions": sorted(active_python_actions),
        "build_python_site_manifest": source_relative(build_site_manifest),
        "build_python_site_sha256": sha256(build_site_manifest),
        "build_python_site_roots": sorted(build_site_roots),
        "cargo_git_patch_packages": sorted(git_patch_packages),
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
