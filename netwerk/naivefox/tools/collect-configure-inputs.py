#!/usr/bin/env python3

"""Run and attest a clean configure file-access trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


FIREFOX_BASE = "8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6"
QUOTED_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
FORBIDDEN_PARTS = {
    ".git",
    ".hg",
    "__pycache__",
    "artifacts",
    "captures",
    "profiles",
}


def decode_strace_string(value: str) -> str:
    return bytes(value, "utf-8").decode("unicode_escape")


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--target-triple", required=True)
    parser.add_argument("--mozconfig", type=Path, required=True)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--trace-output", type=Path)
    args = parser.parse_args()

    repo = args.repo.resolve(strict=True)
    output = args.output.resolve()
    mozconfig = args.mozconfig.resolve(strict=True)
    objdir = args.objdir.resolve()
    if repo not in mozconfig.parents:
        raise SystemExit("mozconfig must be inside the source checkout")
    if objdir == repo or repo in objdir.parents:
        raise SystemExit("configure trace objdir must be outside the source checkout")
    if objdir.exists():
        raise SystemExit(f"configure trace requires a fresh objdir: {objdir}")
    if git(repo, "status", "--porcelain=v1"):
        raise SystemExit("source checkout must be clean before configure tracing")
    source_commit = git(repo, "rev-parse", "HEAD")
    tracked_raw = subprocess.check_output([
        "git",
        "-C",
        str(repo),
        "ls-files",
        "-z",
    ]).decode("utf-8", "surrogateescape")
    tracked = {value for value in tracked_raw.split("\0") if value}

    strace = shutil.which("strace")
    if not strace:
        raise SystemExit("strace is required for configure input collection")
    trace_output = args.trace_output.resolve() if args.trace_output else None
    temporary_trace = None
    if trace_output:
        if trace_output == repo or repo in trace_output.parents:
            raise SystemExit("raw configure trace must stay outside the checkout")
        if trace_output.exists():
            raise SystemExit(f"refusing to overwrite trace output: {trace_output}")
        trace_output.parent.mkdir(parents=True, exist_ok=True)
        trace = trace_output
    else:
        handle, name = tempfile.mkstemp(prefix="naivefox-configure-", suffix=".strace")
        os.close(handle)
        trace = Path(name)
        temporary_trace = trace

    command = [
        strace,
        "-f",
        "-qq",
        "-e",
        "trace=%file",
        "-o",
        str(trace),
        "./mach",
        "configure",
    ]
    environment = os.environ.copy()
    environment["MOZCONFIG"] = str(mozconfig)
    environment["NAIVEFOX_OBJDIR"] = str(objdir)
    try:
        completed = subprocess.run(command, cwd=repo, env=environment, check=False)
        if completed.returncode:
            raise SystemExit(f"configure under strace failed: {completed.returncode}")
        if git(repo, "rev-parse", "HEAD") != source_commit:
            raise SystemExit("source commit changed while configure was running")
        status = git(repo, "status", "--porcelain=v1")
        if status:
            raise SystemExit("configure changed the source checkout")
        config_status = objdir / "config.status"
        if not config_status.is_file():
            raise SystemExit("successful configure did not create config.status")
        target_match = re.search(
            r"^\s*'target':\s*'([^']+)',\s*$",
            config_status.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        if not target_match:
            raise SystemExit("could not attest configured target triple")
        configured_target = target_match.group(1)
        if configured_target != args.target_triple:
            raise SystemExit(
                "configured target mismatch: "
                f"{configured_target} != {args.target_triple}"
            )

        prefix = f"{repo}/"
        files: set[str] = set()
        directories: set[str] = set()
        untracked: set[str] = set()
        for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
            if "= -1 " in line:
                continue
            for encoded in QUOTED_STRING.findall(line):
                try:
                    value = decode_strace_string(encoded)
                except UnicodeDecodeError:
                    continue
                if not value.startswith(prefix):
                    continue
                path = Path(value).resolve()
                try:
                    relative = path.relative_to(repo)
                except ValueError:
                    continue
                if any(
                    part in FORBIDDEN_PARTS or part.startswith("obj-")
                    for part in relative.parts
                ):
                    continue
                if relative.suffix == ".pyc":
                    continue
                relative_text = relative.as_posix()
                if path.is_file():
                    if relative_text in tracked:
                        files.add(relative_text)
                    else:
                        untracked.add(relative_text)
                elif path.is_dir() and relative_text not in {"", "."}:
                    directories.add(relative_text)
        if untracked:
            details = "\n".join(sorted(untracked))
            raise SystemExit(f"configure read untracked source inputs:\n{details}")

        report = {
            "report_version": 2,
            "target": args.target,
            "target_triple": configured_target,
            "source_commit": source_commit,
            "source_worktree_clean": True,
            "firefox_base_commit": FIREFOX_BASE,
            "mozconfig": mozconfig.relative_to(repo).as_posix(),
            "mozconfig_sha256": sha256(mozconfig),
            "configure_command": [
                "strace",
                "-f",
                "-qq",
                "-e",
                "trace=%file",
                "-o",
                "<trace>",
                "./mach",
                "configure",
            ],
            "configure_environment": {
                "MOZCONFIG": mozconfig.relative_to(repo).as_posix(),
                "NAIVEFOX_OBJDIR": "<fresh-external-objdir>",
                "NAIVEFOX_DISABLE_SCCACHE": environment.get(
                    "NAIVEFOX_DISABLE_SCCACHE", "<unset>"
                ),
            },
            "configure_exit_status": completed.returncode,
            "strace_version": subprocess.check_output(
                [strace, "--version"], text=True
            ).splitlines()[0],
            "python_version": sys.version.split()[0],
            "collector_sha256": sha256(Path(__file__).resolve()),
            "trace_sha256": sha256(trace),
            "trace_size_bytes": trace.stat().st_size,
            "files": sorted(files),
            "observed_directories": sorted(directories),
            "counts": {"files": len(files), "directories": len(directories)},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(f"{output.suffix}.tmp")
        temporary.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
        print(
            f"wrote {output}: {len(files)} files, {len(directories)} directories, "
            f"trace {report['trace_sha256']}"
        )
        return 0
    finally:
        if temporary_trace:
            temporary_trace.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
