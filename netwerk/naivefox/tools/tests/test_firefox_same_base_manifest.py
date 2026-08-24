#!/usr/bin/env python3

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
HELPER = TOOLS_DIR / "firefox_same_base_manifest.py"
BUILDER = TOOLS_DIR / "build-firefox-same-base.sh"


def run(
    *command: str, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


class FirefoxSameBaseManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repo = root / "source"
        self.worktree = root / "firefox-worktree"
        self.objdir = root / "firefox-objdir"
        self.repo.mkdir()
        run("git", "init", "-b", "firefox-upstream", cwd=self.repo)
        run("git", "config", "user.name", "NaiveFox Test", cwd=self.repo)
        run("git", "config", "user.email", "naivefox@example.invalid", cwd=self.repo)
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        run("git", "add", "base.txt", cwd=self.repo)
        run("git", "commit", "-m", "Firefox base", cwd=self.repo)
        self.base = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        run("git", "switch", "-c", "naivefox-full-source", cwd=self.repo)
        tools = self.repo / "netwerk/naivefox/tools"
        tools.mkdir(parents=True)
        shutil.copy2(HELPER, tools / HELPER.name)
        shutil.copy2(BUILDER, tools / BUILDER.name)
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-m", "Add same-base builder", cwd=self.repo)
        self.source = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        run(
            "git",
            "worktree",
            "add",
            "--detach",
            str(self.worktree),
            self.base,
            cwd=self.repo,
        )
        self.helper = tools / HELPER.name
        self.mozconfig = (
            f"mk_add_options MOZ_OBJDIR={self.objdir}\n"
            "ac_add_options --enable-project=browser\n"
            "ac_add_options --enable-optimize\n"
            "ac_add_options --disable-debug\n"
            "ac_add_options --disable-tests\n"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def helper_args(self) -> list[str]:
        return [
            "--repo",
            str(self.repo),
            "--source-revision",
            self.source,
            "--firefox-ref",
            "firefox-upstream",
            "--firefox-ref-revision",
            self.base,
            "--base-revision",
            self.base,
            "--worktree",
            str(self.worktree),
            "--objdir",
            str(self.objdir),
            "--jobs",
            "4",
            "--sccache-selection",
            "off",
            "--sccache-path",
            "",
        ]

    def prepare(self) -> None:
        result = run(
            "python3",
            str(self.helper),
            "prepare",
            *self.helper_args(),
            "--mozconfig-content",
            self.mozconfig,
        )
        self.assertEqual(result.stdout.strip(), "prepared")

    def create_outputs(self) -> None:
        runtime = self.objdir / "dist/bin"
        runtime.mkdir(parents=True)
        for name, content in (
            ("firefox", b"firefox binary\n"),
            ("libxul.so", b"libxul\n"),
            ("libssl3.so", b"libssl3\n"),
            ("libnss3.so", b"libnss3\n"),
        ):
            (runtime / name).write_bytes(content)
        (runtime / "application.ini").write_text(
            "[App]\nName=Firefox\nVersion=156.0a1\nBuildID=20260824010101\n",
            encoding="utf-8",
        )
        (self.objdir / "dist/firefox-156.0a1.en-US.linux-x86_64.tar.xz").write_bytes(
            b"package\n"
        )
        backend = self.objdir / "security/nss/lib/ssl/ssl_ssl/backend.mk"
        backend.parent.mkdir(parents=True)
        backend.write_text("DEFINES += -DNSS_ALLOW_SSLKEYLOGFILE\n", encoding="utf-8")
        mozinfo = {
            "topsrcdir": str(self.worktree),
            "topobjdir": str(self.objdir),
            "mozconfig": str(self.objdir / "firefox-same-base.mozconfig"),
            "buildapp": "browser",
            "appname": "firefox",
            "tests_enabled": False,
            "debug": False,
            "opt": True,
            "pgo": False,
        }
        (self.objdir / "mozinfo.json").write_text(json.dumps(mozinfo), encoding="utf-8")
        configured = {
            "mozconfig": {
                "path": str(self.objdir / "firefox-same-base.mozconfig"),
                "topobjdir": str(self.objdir),
                "configure_args": [
                    "--enable-project=browser",
                    "--enable-optimize",
                    "--disable-debug",
                    "--disable-tests",
                ],
            }
        }
        (self.objdir / ".mozconfig.json").write_text(
            json.dumps(configured), encoding="utf-8"
        )
        status = {
            "substs": {
                "CC": ["/bin/true"],
                "CXX": ["/bin/true"],
                "RUSTC": "/bin/true",
                "CARGO": "/bin/true",
            }
        }
        (self.objdir / "config.status.json").write_text(
            json.dumps(status), encoding="utf-8"
        )

    def test_complete_verify_show_and_tamper_detection(self) -> None:
        self.prepare()
        self.create_outputs()
        complete = run("python3", str(self.helper), "complete", *self.helper_args())
        self.assertEqual(complete.stdout.strip(), "complete")
        verified = run("python3", str(self.helper), "verify", *self.helper_args())
        self.assertIn("verified Firefox same-base reference", verified.stdout)
        shown = run("python3", str(self.helper), "show", *self.helper_args())
        self.assertIn("NAIVEFOX_CAPTURE_MODE=same-base", shown.stdout)
        self.assertIn(f"NAIVEFOX_CAPTURE_REFERENCE_OBJDIR={self.objdir}", shown.stdout)

        (self.objdir / "dist/bin/libxul.so").write_bytes(b"changed\n")
        failed = run(
            "python3", str(self.helper), "verify", *self.helper_args(), check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("hash or size mismatch: libxul", failed.stdout)

    def test_verify_rejects_mutation_of_unlisted_runtime_library(self) -> None:
        self.prepare()
        self.create_outputs()
        run("python3", str(self.helper), "complete", *self.helper_args())
        (self.objdir / "dist/bin/libnss3.so").write_bytes(b"changed\n")
        failed = run(
            "python3", str(self.helper), "verify", *self.helper_args(), check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("runtime tree does not match", failed.stdout)

    def test_complete_rejects_runtime_directory_symlink_escape(self) -> None:
        self.prepare()
        self.create_outputs()
        external = Path(self.temporary.name) / "external-runtime-data"
        external.mkdir()
        (external / "mutable.txt").write_text("mutable\n", encoding="utf-8")
        (self.objdir / "dist/bin/external").symlink_to(
            external, target_is_directory=True
        )
        failed = run(
            "python3", str(self.helper), "complete", *self.helper_args(), check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("directory symlink escapes", failed.stdout)

    def test_prepared_manifest_is_resumable_but_not_reusable(self) -> None:
        self.prepare()
        prepared = run(
            "python3",
            str(self.helper),
            "prepare",
            *self.helper_args(),
            "--mozconfig-content",
            self.mozconfig,
        )
        self.assertEqual(prepared.stdout.strip(), "prepared")
        failed = run(
            "python3", str(self.helper), "show", *self.helper_args(), check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("reference build is not complete", failed.stdout)

    def test_prepared_manifest_rejects_builder_mozconfig_drift(self) -> None:
        self.prepare()
        failed = run(
            "python3",
            str(self.helper),
            "prepare",
            *self.helper_args(),
            "--mozconfig-content",
            self.mozconfig + "ac_add_options --without-wasm-sandboxed-libraries\n",
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("does not match the current builder", failed.stdout)

    def test_complete_reference_survives_later_same_base_source_commit(self) -> None:
        self.prepare()
        self.create_outputs()
        run("python3", str(self.helper), "complete", *self.helper_args())
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        run("git", "add", "later.txt", cwd=self.repo)
        run("git", "commit", "-m", "Later NaiveFox experiment", cwd=self.repo)
        self.source = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        verified = run("python3", str(self.helper), "verify", *self.helper_args())
        self.assertIn("verified Firefox same-base reference", verified.stdout)

    def test_unrecognized_objdir_is_refused(self) -> None:
        self.objdir.mkdir()
        failed = run(
            "python3",
            str(self.helper),
            "prepare",
            *self.helper_args(),
            "--mozconfig-content",
            self.mozconfig,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("manifest is missing", failed.stdout)

    def test_dirty_or_attached_worktree_is_refused(self) -> None:
        (self.worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        failed = run(
            "python3",
            str(self.helper),
            "check-worktree",
            *self.helper_args(),
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("must be pristine", failed.stdout)

    def test_false_base_is_refused(self) -> None:
        false_base = "0" * 40
        arguments = self.helper_args()
        arguments[arguments.index("--base-revision") + 1] = false_base
        failed = run(
            "python3",
            str(self.helper),
            "check-worktree",
            *arguments,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("not the exact merge-base", failed.stdout)

    def test_builder_dry_run_does_not_create_paths(self) -> None:
        root = Path(self.temporary.name)
        dry_worktree = root / "dry-worktree"
        dry_objdir = root / "dry-objdir"
        result = run(
            "bash",
            str(self.repo / "netwerk/naivefox/tools" / BUILDER.name),
            "--dry-run",
            "--sccache",
            "off",
            "--worktree",
            str(dry_worktree),
            "--objdir",
            str(dry_objdir),
            cwd=self.repo,
        )
        self.assertIn(f"Firefox base:      {self.base}", result.stdout)
        self.assertIn("--enable-project=browser", result.stdout)
        self.assertIn("build -j4", result.stdout)
        self.assertFalse(dry_worktree.exists())
        self.assertFalse(dry_objdir.exists())

    def test_builder_rejects_objdir_inside_checkout(self) -> None:
        failed = run(
            "bash",
            str(self.repo / "netwerk/naivefox/tools" / BUILDER.name),
            "--dry-run",
            "--sccache",
            "off",
            "--worktree",
            str(Path(self.temporary.name) / "dry-worktree"),
            "--objdir",
            str(self.repo / "obj-firefox"),
            cwd=self.repo,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("object directory must be outside", failed.stdout)

    def test_builder_rejects_overlapping_paths_before_creating_them(self) -> None:
        root = Path(self.temporary.name)
        worktree = root / "overlap"
        objdir = worktree / "objdir"
        failed = run(
            "bash",
            str(self.repo / "netwerk/naivefox/tools" / BUILDER.name),
            "--sccache",
            "off",
            "--worktree",
            str(worktree),
            "--objdir",
            str(objdir),
            cwd=self.repo,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("must not contain each other", failed.stdout)
        self.assertFalse(worktree.exists())
        self.assertFalse(objdir.exists())


if __name__ == "__main__":
    unittest.main()
