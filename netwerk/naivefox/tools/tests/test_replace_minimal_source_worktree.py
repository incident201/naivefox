#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "replace_minimal_source_worktree",
    TOOLS / "replace-minimal-source-worktree.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def git(path: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), *arguments], text=True
    ).strip()


class ReplaceMinimalSourceWorktreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "seed")
        git(self.repo, "config", "user.name", "NaiveFox Test")
        git(self.repo, "config", "user.email", "naivefox@example.invalid")
        (self.repo / "old.txt").write_text("old\n", encoding="utf-8")
        git(self.repo, "add", "old.txt")
        git(self.repo, "commit", "-m", "initial")
        self.target = self.root / "minimal-source"
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "worktree",
                "add",
                "-b",
                "naivefox-minimal-source",
                str(self.target),
                "HEAD",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.source = self.root / "export"
        (self.source / "nested").mkdir(parents=True)
        (self.source / "README.md").write_text("new\n", encoding="utf-8")
        (self.source / "nested/data.txt").write_text("data\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_replaces_contents_and_preserves_worktree_metadata(self) -> None:
        metadata = (self.target / ".git").read_text(encoding="utf-8")
        with mock.patch.object(MODULE, "validate_export"):
            MODULE.replace_worktree(self.source, self.target)
        self.assertEqual((self.target / ".git").read_text(encoding="utf-8"), metadata)
        self.assertFalse((self.target / "old.txt").exists())
        self.assertEqual(
            (self.target / "README.md").read_text(encoding="utf-8"), "new\n"
        )
        self.assertEqual(
            (self.target / "nested/data.txt").read_text(encoding="utf-8"), "data\n"
        )
        self.assertTrue(git(self.target, "status", "--porcelain=v1"))

    def test_dirty_target_is_rejected_before_replacement(self) -> None:
        (self.target / "dirty.txt").write_text("do not remove\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ReplacementError, "must be clean"):
            MODULE.verify_target(self.target)
        self.assertTrue((self.target / "old.txt").exists())

    def test_non_worktree_git_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            MODULE.ReplacementError, "naivefox-minimal-source"
        ):
            MODULE.verify_target(self.repo)


if __name__ == "__main__":
    unittest.main()
