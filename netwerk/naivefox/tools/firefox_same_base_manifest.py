#!/usr/bin/env python3

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any

SCHEMA_VERSION = 1
MANIFEST_NAME = "FIREFOX-SAME-BASE-MANIFEST.json"
MOZCONFIG_NAME = "firefox-same-base.mozconfig"
OID_RE = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> None:
    raise ValueError(message)


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def resolved(path: str | pathlib.Path, *, strict: bool = False) -> pathlib.Path:
    return pathlib.Path(path).expanduser().resolve(strict=strict)


def is_within(parent: pathlib.Path, child: pathlib.Path) -> bool:
    return os.path.commonpath((str(parent), str(child))) == str(parent)


def validate_layout(
    repo: pathlib.Path, worktree: pathlib.Path, objdir: pathlib.Path
) -> None:
    if repo == worktree or is_within(repo, worktree):
        fail(f"reference worktree must be outside the source checkout: {worktree}")
    if repo == objdir or is_within(repo, objdir):
        fail(
            f"reference object directory must be outside the source checkout: {objdir}"
        )
    if worktree == objdir or is_within(worktree, objdir) or is_within(objdir, worktree):
        fail("reference worktree and object directory must not contain each other")
    for label, path in (("worktree", worktree), ("object directory", objdir)):
        if path == pathlib.Path(path.anchor):
            fail(f"reference {label} cannot be a filesystem root")


def validate_oid(value: str, label: str) -> str:
    if not OID_RE.fullmatch(value):
        fail(f"{label} must be a canonical 40-hex Git commit OID")
    return value


def git(repo: pathlib.Path, *args: str) -> str:
    try:
        result = run("git", "-C", str(repo), *args)
    except subprocess.CalledProcessError as error:
        detail = error.stdout.strip()
        raise ValueError(detail or f"git {' '.join(args)} failed") from error
    return result.stdout.strip()


def validate_source_inputs(expected: dict[str, Any]) -> None:
    repo = pathlib.Path(expected["source"]["repository"])
    source = expected["source"]["naivefox_revision"]
    reference = expected["source"]["firefox_ref_revision"]
    base = expected["source"]["firefox_base_revision"]
    if git(repo, "rev-parse", "--verify", f"{source}^{{commit}}") != source:
        fail("NaiveFox revision does not resolve to itself")
    if git(repo, "rev-parse", "--verify", f"{reference}^{{commit}}") != reference:
        fail("Firefox reference revision does not resolve to itself")
    if git(repo, "merge-base", source, reference) != base:
        fail("Firefox base is not the exact merge-base of the recorded revisions")


def validate_worktree(repo: pathlib.Path, worktree: pathlib.Path, base: str) -> None:
    validate_oid(base, "Firefox base revision")
    if not worktree.is_dir():
        fail(f"reference worktree does not exist: {worktree}")
    common_value = pathlib.Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common_value.is_absolute():
        common_value = repo / common_value
    common = resolved(common_value, strict=True)
    candidate_common = git(worktree, "rev-parse", "--git-common-dir")
    candidate_common_path = pathlib.Path(candidate_common)
    if not candidate_common_path.is_absolute():
        candidate_common_path = worktree / candidate_common_path
    if resolved(candidate_common_path, strict=True) != common:
        fail(f"path is not a worktree of the source repository: {worktree}")
    if resolved(git(worktree, "rev-parse", "--show-toplevel"), strict=True) != worktree:
        fail(f"registered worktree root does not match requested path: {worktree}")
    if git(worktree, "rev-parse", "HEAD") != base:
        fail(f"reference worktree is not at Firefox base {base}: {worktree}")
    branch = run("git", "-C", str(worktree), "symbolic-ref", "-q", "HEAD", check=False)
    if branch.returncode == 0:
        fail(f"reference worktree must be detached: {worktree}")
    if git(worktree, "status", "--porcelain=v1"):
        fail(f"reference worktree must be pristine: {worktree}")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: pathlib.Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def utc_now() -> str:
    return (
        dt.datetime
        .now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def expected_inputs(args: argparse.Namespace) -> dict[str, Any]:
    repo = resolved(args.repo, strict=True)
    worktree = resolved(args.worktree)
    objdir = resolved(args.objdir)
    validate_layout(repo, worktree, objdir)
    return {
        "source": {
            "repository": str(repo),
            "naivefox_revision": validate_oid(
                args.source_revision, "NaiveFox revision"
            ),
            "firefox_ref": args.firefox_ref,
            "firefox_ref_revision": validate_oid(
                args.firefox_ref_revision, "Firefox reference revision"
            ),
            "firefox_base_revision": validate_oid(
                args.base_revision, "Firefox base revision"
            ),
        },
        "paths": {
            "worktree": str(worktree),
            "objdir": str(objdir),
            "mozconfig": str(objdir / MOZCONFIG_NAME),
        },
        "build": {
            "jobs": args.jobs,
            "sccache": {
                "selection": args.sccache_selection,
                "enabled": bool(args.sccache_path),
                "path": args.sccache_path or None,
            },
        },
    }


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"unrecognized reference object directory; manifest is missing: {path}")
    except json.JSONDecodeError as error:
        fail(f"reference build manifest is invalid JSON: {error}")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        fail("unsupported Firefox same-base manifest schema")
    return data


def validate_expected(
    manifest: dict[str, Any], expected: dict[str, Any], *, complete: bool = False
) -> None:
    manifest_source = manifest.get("source")
    if not isinstance(manifest_source, dict):
        fail("reference build manifest is missing source inputs")
    source_fields = (
        ("repository", "firefox_base_revision") if complete else expected["source"]
    )
    for key in source_fields:
        if manifest_source.get(key) != expected["source"][key]:
            fail(
                f"reference build manifest source.{key} does not match this build request"
            )
    manifest_paths = manifest.get("paths")
    if not isinstance(manifest_paths, dict):
        fail("reference build manifest is missing paths")
    for key, value in expected["paths"].items():
        if manifest_paths.get(key) != value:
            fail(
                f"reference build manifest paths.{key} does not match this build request"
            )
    manifest_build = manifest.get("build")
    if not isinstance(manifest_build, dict):
        fail("reference build manifest is missing build inputs")
    if complete:
        return
    for key in ("jobs", "sccache"):
        actual = manifest_build.get(key)
        if key == "sccache" and isinstance(actual, dict):
            actual = {field: actual.get(field) for field in expected["build"][key]}
        if actual != expected["build"][key]:
            fail(
                f"reference build manifest build.{key} does not match this build request"
            )


def validate_mozconfig(manifest: dict[str, Any]) -> pathlib.Path:
    mozconfig = resolved(manifest["paths"]["mozconfig"], strict=True)
    expected_hash = manifest.get("build", {}).get("mozconfig_sha256")
    expected_content = manifest.get("build", {}).get("mozconfig_content")
    if sha256(mozconfig) != expected_hash:
        fail(f"reference mozconfig hash mismatch: {mozconfig}")
    if mozconfig.read_text(encoding="utf-8") != expected_content:
        fail(f"reference mozconfig content mismatch: {mozconfig}")
    return mozconfig


def prepare(args: argparse.Namespace) -> None:
    expected = expected_inputs(args)
    validate_source_inputs(expected)
    worktree = pathlib.Path(expected["paths"]["worktree"])
    objdir = pathlib.Path(expected["paths"]["objdir"])
    validate_worktree(
        pathlib.Path(expected["source"]["repository"]),
        worktree,
        expected["source"]["firefox_base_revision"],
    )
    manifest_path = objdir / MANIFEST_NAME
    if objdir.exists():
        manifest = load_manifest(manifest_path)
        status = manifest.get("status")
        validate_expected(manifest, expected, complete=status == "complete")
        validate_mozconfig(manifest)
        if status not in ("prepared", "complete"):
            fail(f"unsupported reference build status: {status!r}")
        if (
            status == "prepared"
            and manifest.get("build", {}).get("mozconfig_content")
            != args.mozconfig_content
        ):
            fail("prepared reference mozconfig does not match the current builder")
        print(status)
        return

    objdir.mkdir(parents=True, mode=0o700)
    mozconfig = objdir / MOZCONFIG_NAME
    atomic_write(mozconfig, args.mozconfig_content)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared",
        "created_utc": utc_now(),
        **expected,
    }
    manifest["build"]["mozconfig_sha256"] = sha256(mozconfig)
    manifest["build"]["mozconfig_content"] = args.mozconfig_content
    atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("prepared")


def read_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        fail(f"cannot read {label}: {path}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} must contain a JSON object: {path}")
    return value


def version_of(command: list[str]) -> str | None:
    if not command:
        return None
    try:
        result = run(command[0], "--version", check=False)
    except OSError:
        return None
    output = result.stdout.strip()
    return output.splitlines()[0] if output else None


def command_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def elf_build_id(path: pathlib.Path) -> str | None:
    result = run("readelf", "-n", str(path), check=False)
    match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else None


def artifact_record(objdir: pathlib.Path, path: pathlib.Path) -> dict[str, Any]:
    path = resolved(path, strict=True)
    if not is_within(objdir, path):
        fail(f"reference artifact escapes object directory: {path}")
    if not path.is_file():
        fail(f"reference artifact is not a regular file: {path}")
    return {
        "path": path.relative_to(objdir).as_posix(),
        "sha256": sha256(path),
        "size": path.stat().st_size,
        "elf_build_id": elf_build_id(path),
    }


def runtime_tree_inventory(runtime: pathlib.Path) -> list[dict[str, Any]]:
    records = []
    for directory, dirnames, filenames in os.walk(runtime, followlinks=False):
        dirnames.sort()
        filenames.sort()
        parent = pathlib.Path(directory)
        for name in sorted((*dirnames, *filenames)):
            path = parent / name
            metadata = path.lstat()
            record: dict[str, Any] = {
                "path": path.relative_to(runtime).as_posix(),
                "mode": stat.S_IMODE(metadata.st_mode),
            }
            if stat.S_ISLNK(metadata.st_mode):
                record.update({"type": "symlink", "target": os.readlink(path)})
                target = path.resolve(strict=True)
                if target.is_file():
                    record.update({
                        "target_type": "file",
                        "target_size": target.stat().st_size,
                        "target_sha256": sha256(target),
                    })
                elif target.is_dir():
                    if not is_within(runtime, target):
                        fail(
                            "runtime directory symlink escapes the inventoried tree: "
                            f"{path}"
                        )
                    record["target_type"] = "directory"
                else:
                    fail(f"runtime symlink has an unsupported target: {path}")
            elif stat.S_ISDIR(metadata.st_mode):
                record["type"] = "directory"
            elif stat.S_ISREG(metadata.st_mode):
                record.update({
                    "type": "file",
                    "size": metadata.st_size,
                    "sha256": sha256(path),
                })
            else:
                fail(f"runtime contains an unsupported filesystem entry: {path}")
            records.append(record)
    return records


def select_runtime(objdir: pathlib.Path) -> pathlib.Path:
    candidates = (objdir / "dist/firefox", objdir / "dist/bin")
    for candidate in candidates:
        if all(
            (candidate / name).is_file()
            for name in ("firefox", "libxul.so", "libssl3.so")
        ):
            return resolved(candidate, strict=True)
    fail("packaged Firefox runtime is missing firefox, libxul.so, or libssl3.so")


def select_package(objdir: pathlib.Path) -> pathlib.Path:
    candidates = sorted(
        path for path in (objdir / "dist").glob("firefox-*.tar.*") if path.is_file()
    )
    if len(candidates) != 1:
        fail(f"expected exactly one packaged Firefox archive, found {len(candidates)}")
    return resolved(candidates[0], strict=True)


def application_metadata(runtime: pathlib.Path) -> dict[str, str]:
    ini_path = runtime / "application.ini"
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(ini_path, encoding="utf-8"):
        fail(f"Firefox application.ini is missing: {ini_path}")
    try:
        return {
            "name": parser["App"]["Name"],
            "version": parser["App"]["Version"],
            "build_id": parser["App"]["BuildID"],
        }
    except KeyError as error:
        fail(f"Firefox application.ini is incomplete: missing {error}")


def collect_complete(manifest: dict[str, Any]) -> dict[str, Any]:
    objdir = resolved(manifest["paths"]["objdir"], strict=True)
    worktree = resolved(manifest["paths"]["worktree"], strict=True)
    mozinfo_path = objdir / "mozinfo.json"
    mozconfig_json_path = objdir / ".mozconfig.json"
    config_status_path = objdir / "config.status.json"
    mozinfo = read_json(mozinfo_path, "mozinfo")
    mozconfig_json = read_json(mozconfig_json_path, "configured mozconfig")
    config_status = read_json(config_status_path, "configure status")
    required_mozinfo = {
        "topsrcdir": str(worktree),
        "topobjdir": str(objdir),
        "mozconfig": manifest["paths"]["mozconfig"],
        "buildapp": "browser",
        "appname": "firefox",
        "tests_enabled": False,
        "debug": False,
        "opt": True,
        "pgo": False,
    }
    for key, expected in required_mozinfo.items():
        if mozinfo.get(key) != expected:
            fail(
                f"mozinfo {key} mismatch: expected {expected!r}, got {mozinfo.get(key)!r}"
            )
    configured = mozconfig_json.get("mozconfig", {})
    if configured.get("path") != manifest["paths"]["mozconfig"]:
        fail(
            "configured mozconfig path does not match the prepared reference mozconfig"
        )
    if configured.get("topobjdir") != str(objdir):
        fail(
            "configured object directory does not match the reference object directory"
        )
    arguments = configured.get("configure_args")
    required_arguments = {
        "--enable-project=browser",
        "--enable-optimize",
        "--disable-debug",
        "--disable-tests",
    }
    if not isinstance(arguments, list) or not required_arguments.issubset(arguments):
        fail("configured Firefox build is not the expected optimized browser build")

    backend = objdir / "security/nss/lib/ssl/ssl_ssl/backend.mk"
    try:
        backend_text = backend.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"NSS build backend is missing: {backend}")
    if "-DNSS_ALLOW_SSLKEYLOGFILE" not in backend_text:
        fail("same-base Firefox NSS was built without NSS_ALLOW_SSLKEYLOGFILE")

    runtime = select_runtime(objdir)
    package = select_package(objdir)
    artifacts = {
        "firefox": artifact_record(objdir, runtime / "firefox"),
        "libxul": artifact_record(objdir, runtime / "libxul.so"),
        "libssl3": artifact_record(objdir, runtime / "libssl3.so"),
        "package": artifact_record(objdir, package),
        "application_ini": artifact_record(objdir, runtime / "application.ini"),
        "mozinfo": artifact_record(objdir, mozinfo_path),
        "config_status": artifact_record(objdir, config_status_path),
        "nss_backend": artifact_record(objdir, backend),
    }
    substs = config_status.get("substs")
    if not isinstance(substs, dict):
        fail("configure status is missing toolchain substitutions")
    toolchain: dict[str, Any] = {}
    for name in ("CC", "CXX", "RUSTC", "CARGO"):
        command = command_value(substs.get(name))
        toolchain[name.lower()] = {"command": command, "version": version_of(command)}
    sccache = manifest["build"]["sccache"]
    if sccache["enabled"]:
        sccache["version"] = version_of([sccache["path"]])
    else:
        sccache["version"] = None

    result = dict(manifest)
    result["status"] = "complete"
    result["completed_utc"] = utc_now()
    result["paths"] = {
        **manifest["paths"],
        "runtime_dir": str(runtime),
        "firefox_binary": str(runtime / "firefox"),
        "package_archive": str(package),
    }
    result["build"] = {
        **manifest["build"],
        "mozinfo": mozinfo,
        "toolchain": toolchain,
        "nss_allow_sslkeylogfile": True,
    }
    result["application"] = application_metadata(runtime)
    result["artifacts"] = artifacts
    result["runtime_tree"] = runtime_tree_inventory(runtime)
    return result


def verify_complete(manifest: dict[str, Any], expected: dict[str, Any]) -> None:
    validate_expected(manifest, expected, complete=True)
    if manifest.get("status") != "complete":
        fail(f"reference build is not complete: {manifest.get('status')!r}")
    repo = pathlib.Path(expected["source"]["repository"])
    worktree = pathlib.Path(expected["paths"]["worktree"])
    objdir = pathlib.Path(expected["paths"]["objdir"])
    validate_worktree(repo, worktree, expected["source"]["firefox_base_revision"])
    recorded_source = validate_oid(
        manifest["source"].get("naivefox_revision", ""), "recorded NaiveFox revision"
    )
    recorded_ref = validate_oid(
        manifest["source"].get("firefox_ref_revision", ""),
        "recorded Firefox reference revision",
    )
    if (
        git(repo, "rev-parse", "--verify", f"{recorded_source}^{{commit}}")
        != recorded_source
    ):
        fail("recorded NaiveFox revision is no longer available")
    if git(repo, "rev-parse", "--verify", f"{recorded_ref}^{{commit}}") != recorded_ref:
        fail("recorded Firefox reference revision is no longer available")
    if (
        git(repo, "merge-base", recorded_source, recorded_ref)
        != expected["source"]["firefox_base_revision"]
    ):
        fail("recorded source revisions do not derive the manifest Firefox base")
    validate_mozconfig(manifest)
    if not manifest.get("build", {}).get("nss_allow_sslkeylogfile"):
        fail("reference build manifest does not confirm NSS key logging support")
    artifact_names = {
        "firefox",
        "libxul",
        "libssl3",
        "package",
        "application_ini",
        "mozinfo",
        "config_status",
        "nss_backend",
    }
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != artifact_names:
        fail("reference build manifest has an incomplete artifact inventory")
    for name, record in artifacts.items():
        if not isinstance(record, dict):
            fail(f"invalid artifact record: {name}")
        path = resolved(objdir / record.get("path", ""), strict=True)
        if not is_within(objdir, path):
            fail(f"artifact path escapes reference object directory: {name}")
        if sha256(path) != record.get("sha256") or path.stat().st_size != record.get(
            "size"
        ):
            fail(f"reference artifact hash or size mismatch: {name}")
        if elf_build_id(path) != record.get("elf_build_id"):
            fail(f"reference artifact ELF build ID mismatch: {name}")
    rebuilt = collect_complete({**manifest, "status": "prepared"})
    for key in ("runtime_dir", "firefox_binary", "package_archive"):
        if rebuilt["paths"][key] != manifest["paths"].get(key):
            fail(f"reference runtime path mismatch: {key}")
    if rebuilt["application"] != manifest.get("application"):
        fail("Firefox application metadata does not match the manifest")
    if rebuilt["build"]["mozinfo"] != manifest["build"].get("mozinfo"):
        fail("Firefox mozinfo does not match the manifest")
    if rebuilt["artifacts"] != artifacts:
        fail("Firefox artifact inventory does not match the manifest")
    runtime_tree = manifest.get("runtime_tree")
    if not isinstance(runtime_tree, list) or rebuilt["runtime_tree"] != runtime_tree:
        fail("Firefox runtime tree does not match the manifest")


def common_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--firefox-ref", required=True)
    parser.add_argument("--firefox-ref-revision", required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--objdir", required=True)
    parser.add_argument("--jobs", required=True, type=int)
    parser.add_argument(
        "--sccache-selection", required=True, choices=("auto", "on", "off")
    )
    parser.add_argument("--sccache-path", default="")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "prepare",
        "complete",
        "verify",
        "show",
        "check-layout",
        "check-worktree",
    ):
        subparser = subparsers.add_parser(command)
        common_parser(subparser)
        if command == "prepare":
            subparser.add_argument("--mozconfig-content", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected = expected_inputs(args)
    manifest_path = pathlib.Path(expected["paths"]["objdir"]) / MANIFEST_NAME
    if args.command == "check-layout":
        validate_source_inputs(expected)
        print("valid")
        return 0
    if args.command == "check-worktree":
        validate_source_inputs(expected)
        validate_worktree(
            pathlib.Path(expected["source"]["repository"]),
            pathlib.Path(expected["paths"]["worktree"]),
            expected["source"]["firefox_base_revision"],
        )
        print("pristine")
        return 0
    if args.command == "prepare":
        prepare(args)
        return 0
    manifest = load_manifest(manifest_path)
    if args.command == "complete":
        if manifest.get("status") == "complete":
            verify_complete(manifest, expected)
            print("complete")
            return 0
        if manifest.get("status") != "prepared":
            fail(
                f"reference build is not in a completable state: {manifest.get('status')!r}"
            )
        validate_expected(manifest, expected)
        validate_worktree(
            pathlib.Path(expected["source"]["repository"]),
            pathlib.Path(expected["paths"]["worktree"]),
            expected["source"]["firefox_base_revision"],
        )
        validate_mozconfig(manifest)
        completed = collect_complete(manifest)
        atomic_write(
            manifest_path, json.dumps(completed, indent=2, sort_keys=True) + "\n"
        )
        verify_complete(completed, expected)
        print("complete")
        return 0
    verify_complete(manifest, expected)
    if args.command == "verify":
        print(f"verified Firefox same-base reference: {manifest_path}")
    else:
        paths = manifest["paths"]
        print("NAIVEFOX_CAPTURE_MODE=same-base")
        print(f"NAIVEFOX_CAPTURE_REFERENCE_BIN={paths['firefox_binary']}")
        print(f"NAIVEFOX_CAPTURE_REFERENCE_LIBDIR={paths['runtime_dir']}")
        print(f"NAIVEFOX_CAPTURE_REFERENCE_OBJDIR={paths['objdir']}")
        print(f"NAIVEFOX_CAPTURE_REFERENCE_PACKAGE={paths['package_archive']}")
        print(f"NAIVEFOX_CAPTURE_REFERENCE_MANIFEST={manifest_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
