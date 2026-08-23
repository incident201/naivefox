#!/usr/bin/env python3

"""Safely replace a generated minimal-source worktree from a validated export."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable


class ReplacementError(RuntimeError):
    """Raised when a replacement guard fails."""


def _git(path: pathlib.Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ReplacementError(f"git command failed in {path}: {detail.strip()}") from error
    return result.stdout.strip()


def _load_validator() -> object:
    tools = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(tools))
    spec = importlib.util.spec_from_file_location(
        "naivefox_validate_minimal_source", tools / "validate-minimal-source.py"
    )
    if spec is None or spec.loader is None:
        raise ReplacementError("cannot load minimal-source validator")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    return validator


def validate_export(source: pathlib.Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ReplacementError(f"export directory is not a regular directory: {source}")
    try:
        _load_validator().validate(source)
    except Exception as error:  # validator has its own actionable diagnostics.
        raise ReplacementError(f"invalid minimal-source export: {error}") from error


def verify_target(target: pathlib.Path) -> None:
    if not target.is_dir() or target.is_symlink():
        raise ReplacementError(f"target is not a regular directory: {target}")
    try:
        top = pathlib.Path(_git(target, "rev-parse", "--show-toplevel")).resolve()
    except ReplacementError:
        raise
    if top != target:
        raise ReplacementError(f"target is not a Git worktree root: {target}")
    branch = _git(target, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != "naivefox-minimal-source":
        raise ReplacementError(
            "target must be on branch naivefox-minimal-source, "
            f"not {branch or '<detached HEAD>'}"
        )
    status = _git(target, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ReplacementError("target worktree must be clean before replacement")
    metadata = target / ".git"
    if metadata.is_symlink() or not metadata.is_file():
        raise ReplacementError(
            "target must be a linked worktree with a regular .git metadata file"
        )
    try:
        first_line = metadata.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeError, IndexError) as error:
        raise ReplacementError("target .git metadata file is unreadable") from error
    if not first_line.startswith("gitdir: "):
        raise ReplacementError("target .git metadata file is malformed")


def _walk(root: pathlib.Path) -> Iterable[tuple[pathlib.PurePosixPath, str, int, str]]:
    for path in sorted(root.rglob("*")):
        relative = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if path.is_symlink():
            raise ReplacementError(f"symlink present in replacement tree: {relative}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            yield relative, "directory", mode, ""
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            yield relative, "file", mode, digest.hexdigest()
        else:
            raise ReplacementError(f"unsupported filesystem node: {relative}")


def _snapshot(root: pathlib.Path, *, skip_git: bool = False) -> set[tuple[str, str, int, str]]:
    values = set()
    for relative, kind, mode, digest in _walk(root):
        if skip_git and relative.parts and relative.parts[0] == ".git":
            continue
        values.add((relative.as_posix(), kind, mode, digest))
    return values


def _remove(path: pathlib.Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise ReplacementError(f"cannot remove unsupported target node: {path}")


def _replace_contents(source: pathlib.Path, target: pathlib.Path) -> None:
    parent = target.parent
    stage = pathlib.Path(tempfile.mkdtemp(prefix=".naivefox-replacement.", dir=parent))
    backup = pathlib.Path(tempfile.mkdtemp(prefix=".naivefox-replacement-backup.", dir=parent))
    try:
        shutil.copytree(
            source,
            stage,
            symlinks=False,
            copy_function=shutil.copy2,
            dirs_exist_ok=True,
        )
        expected = _snapshot(stage)
        for child in list(target.iterdir()):
            if child.name != ".git":
                shutil.move(str(child), str(backup / child.name))
        for child in list(stage.iterdir()):
            shutil.move(str(child), str(target / child.name))
        actual = _snapshot(target, skip_git=True)
        if actual != expected:
            raise ReplacementError("target contents do not match validated export")
        shutil.rmtree(backup)
    except Exception:
        for child in list(target.iterdir()):
            if child.name != ".git":
                _remove(child)
        for child in list(backup.iterdir()):
            shutil.move(str(child), str(target / child.name))
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup.exists():
            shutil.rmtree(backup)


def replace_worktree(source: pathlib.Path, target: pathlib.Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ReplacementError("source export and target worktree must be different")
    try:
        source.relative_to(target)
        target_inside_source = True
    except ValueError:
        target_inside_source = False
    try:
        target.relative_to(source)
        source_inside_target = True
    except ValueError:
        source_inside_target = False
    if target_inside_source or source_inside_target:
        raise ReplacementError("source export and target worktree must not contain one another")
    validate_export(source)
    verify_target(target)
    _replace_contents(source, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace a clean naivefox-minimal-source worktree from a validated export."
    )
    parser.add_argument("source_export", type=pathlib.Path)
    parser.add_argument("target_worktree", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        replace_worktree(args.source_export, args.target_worktree)
    except ReplacementError as error:
        print(f"replacement failed: {error}", file=sys.stderr)
        return 2
    print(f"replaced {args.target_worktree} from {args.source_export}")
    print("review and commit the generated worktree; this tool never stages or commits files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
