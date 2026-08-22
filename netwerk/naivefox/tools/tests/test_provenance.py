#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import provenance


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


collector = load_script(
    "collect_minimal_source_evidence",
    TOOLS_DIR / "collect-minimal-source-evidence.py",
)
analyzer = load_script(
    "analyze_full_closure",
    TOOLS_DIR / "analyze-full-closure.py",
)
closure_assertions = load_script(
    "assert_closure",
    TOOLS_DIR / "assert-closure.py",
)


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, message: str) -> str:
    run(repo, "add", "-A")
    run(repo, "commit", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.objdirs_temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        run(self.repo, "init", "-b", "firefox-upstream")
        run(self.repo, "config", "user.name", "NaiveFox Test")
        run(self.repo, "config", "user.email", "naivefox@example.invalid")
        (self.repo / "root.txt").write_text("old\n", encoding="utf-8")
        self.old_base = commit(self.repo, "old base")
        (self.repo / "root.txt").write_text("current\n", encoding="utf-8")
        self.firefox_base = commit(self.repo, "Firefox base")
        run(self.repo, "switch", "-c", "naivefox-full-source")
        (self.repo / "naivefox.txt").write_text("reference\n", encoding="utf-8")
        self.naivefox_reference = commit(self.repo, "NaiveFox reference")

        tools = self.repo / "netwerk/naivefox/tools"
        reports = self.repo / provenance.REPORT_DIRECTORY
        tools.mkdir(parents=True)
        reports.mkdir()
        for name in (
            "collect-build-inputs.py",
            "collect-configure-inputs.py",
            "analyze-full-closure.py",
            "provenance.py",
            "collect-minimal-source-evidence.py",
        ):
            (tools / name).write_text(f"{name}\n", encoding="utf-8")
        (self.repo / "netwerk/naivefox/mozconfig-minimal").write_text(
            "linux product\n", encoding="utf-8"
        )
        (self.repo / "netwerk/naivefox/mozconfig-windows-x86_64").write_text(
            "windows product\n", encoding="utf-8"
        )
        (self.repo / "netwerk/naivefox/mozconfig-android-aarch64").write_text(
            "android product\n", encoding="utf-8"
        )
        for path in provenance.CANONICAL_REPORT_PATHS:
            destination = self.repo / path
            destination.write_text("{}\n", encoding="utf-8")
        self.source = commit(self.repo, "source S")
        self.write_reports()
        self.evidence = commit(self.repo, "evidence E")

    def tearDown(self) -> None:
        self.objdirs_temporary.cleanup()
        self.temporary.cleanup()

    def make_objdir(
        self,
        target_name: str,
        *,
        overrides: dict | None = None,
        omit: str | None = None,
    ) -> Path:
        target = next(
            value for value in collector.TARGETS if value["name"] == target_name
        )
        objdir = Path(
            tempfile.mkdtemp(prefix=f"{target_name}-", dir=self.objdirs_temporary.name)
        ).resolve()
        mozinfo = {
            "topsrcdir": str(self.repo.resolve()),
            "topobjdir": str(objdir),
            "mozconfig": str((self.repo / target["mozconfig"]).resolve()),
            "buildapp": "netwerk/naivefox",
            "appname": "naivefox",
            "tests_enabled": False,
            **target["mozinfo"],
        }
        mozinfo.update(overrides or {})
        (objdir / "mozinfo.json").write_text(json.dumps(mozinfo), encoding="utf-8")
        required_outputs = (
            "config.status",
            target["link_response"],
            target["libxul"],
        )
        if target["executable"]:
            required_outputs += (target["executable"],)
        for relative in required_outputs:
            if relative == omit:
                continue
            output = objdir / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("evidence output\n", encoding="utf-8")
        return objdir

    def write_reports(
        self,
        source: str | None = None,
        firefox_base: str | None = None,
    ) -> None:
        source = source or self.source
        firefox_base = firefox_base or self.firefox_base
        tools = self.repo / "netwerk/naivefox/tools"
        common = {
            "provenance_version": 2,
            "source_commit": source,
            "source_worktree_clean": True,
            "firefox_base_commit": firefox_base,
            "naivefox_reference_commit": self.naivefox_reference,
            "provenance_sha256": digest(tools / "provenance.py"),
            "evidence_collector_sha256": digest(
                tools / "collect-minimal-source-evidence.py"
            ),
        }
        for target_spec in provenance.TARGET_SPECS:
            target = target_spec["name"]
            mozconfig = target_spec["mozconfig"]
            build = {
                **common,
                "target": target,
                "collector_sha256": digest(tools / "collect-build-inputs.py"),
                "mozconfig": mozconfig,
                "mozconfig_sha256": digest(self.repo / mozconfig),
            }
            configure = {
                **common,
                "target": target,
                "target_triple": target_spec["configure_target"],
                "collector_sha256": digest(tools / "collect-configure-inputs.py"),
                "mozconfig": mozconfig,
                "mozconfig_sha256": digest(self.repo / mozconfig),
                "configure_environment": {"NAIVEFOX_ENABLE_TESTS": "0"},
            }
            triple = target_spec["cargo_target"]
            closure = {
                "report_provenance": {
                    "provenance_version": 2,
                    "source_commit_sha": source,
                    "source_worktree_clean": True,
                    "firefox_base_sha": firefox_base,
                    "naivefox_reference_sha": self.naivefox_reference,
                    "analyzer_sha256": digest(tools / "analyze-full-closure.py"),
                    "provenance_sha256": digest(tools / "provenance.py"),
                    "evidence_collector_sha256": digest(
                        tools / "collect-minimal-source-evidence.py"
                    ),
                    "target": target,
                    "platform": target_spec["platform"],
                    "target_triple": triple,
                    "mozconfig_path": mozconfig,
                    "mozconfig_sha256": digest(self.repo / mozconfig),
                }
            }
            values = {
                f"build-inputs-{target}.json": build,
                f"configure-inputs-{target}.json": configure,
                f"closure-report-{target}.json": closure,
            }
            for name, value in values.items():
                (self.repo / provenance.REPORT_DIRECTORY / name).write_text(
                    json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
                )

    def load_bundle(self) -> dict[Path, dict]:
        return provenance.load_and_validate_report_bundle(
            self.repo,
            [self.repo / path for path in provenance.CANONICAL_REPORT_PATHS],
            self.source,
            self.firefox_base,
            self.naivefox_reference,
        )

    def test_valid_direct_report_only_evidence_is_accepted(self) -> None:
        release = provenance.validate_evidence_head(self.repo)
        self.assertEqual(release.source_commit, self.source)
        self.assertEqual(release.evidence_commit, self.evidence)
        self.assertEqual(len(self.load_bundle()), 9)

    def test_existing_stale_firefox_ancestor_is_rejected(self) -> None:
        self.write_reports(firefox_base=self.old_base)
        with self.assertRaisesRegex(ValueError, "Firefox base"):
            self.load_bundle()

    def test_noncanonical_41_character_oid_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical 40-hex"):
            provenance.canonical_oid(
                self.repo, f"{self.source}0", "deliberately malformed OID"
            )

    def test_mismatched_report_source_is_rejected(self) -> None:
        self.write_reports(source=self.naivefox_reference)
        with self.assertRaisesRegex(ValueError, "source commit S"):
            self.load_bundle()

    def test_report_filename_rejects_swapped_target_config_and_triple(self) -> None:
        reports = self.repo / provenance.REPORT_DIRECTORY
        mutations = (
            (
                reports / "build-inputs-linux-x86_64.json",
                "target",
                "windows-x86_64",
                "target does not match",
            ),
            (
                reports / "configure-inputs-linux-x86_64.json",
                "mozconfig",
                "netwerk/naivefox/mozconfig-windows-x86_64",
                "mozconfig does not match",
            ),
            (
                reports / "configure-inputs-linux-x86_64.json",
                "target_triple",
                "x86_64-pc-mingw32",
                "target triple does not match",
            ),
        )
        for path, field, value, message in mutations:
            with self.subTest(field=field):
                self.write_reports()
                report = json.loads(path.read_text(encoding="utf-8"))
                report[field] = value
                path.write_text(json.dumps(report) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    self.load_bundle()

        self.write_reports()
        closure_path = reports / "closure-report-linux-x86_64.json"
        closure = json.loads(closure_path.read_text(encoding="utf-8"))
        closure["report_provenance"]["target_triple"] = "x86_64-pc-windows-msvc"
        closure_path.write_text(json.dumps(closure) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "target triple does not match"):
            self.load_bundle()

        self.write_reports()
        android_path = reports / "closure-report-android-aarch64.json"
        android = json.loads(android_path.read_text(encoding="utf-8"))
        android["report_provenance"]["platform"] = "desktop-linux"
        android_path.write_text(json.dumps(android) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "platform does not match"):
            self.load_bundle()

    def test_changed_tool_and_config_invalidate_reports(self) -> None:
        tool = self.repo / "netwerk/naivefox/tools/collect-build-inputs.py"
        tool.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "generator hash"):
            self.load_bundle()
        run(self.repo, "restore", str(tool.relative_to(self.repo)))
        (self.repo / "netwerk/naivefox/mozconfig-minimal").write_text(
            "changed\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "mozconfig hash"):
            self.load_bundle()

    def test_extra_evidence_path_and_post_e_commit_are_rejected(self) -> None:
        run(self.repo, "reset", "--hard", self.source)
        self.write_reports()
        (self.repo / "extra.txt").write_text("extra\n", encoding="utf-8")
        commit(self.repo, "invalid evidence with extra path")
        with self.assertRaisesRegex(ValueError, "extra"):
            provenance.validate_evidence_head(self.repo)

        run(self.repo, "reset", "--hard", self.evidence)
        (self.repo / "post.txt").write_text("post\n", encoding="utf-8")
        commit(self.repo, "post evidence")
        with self.assertRaisesRegex(ValueError, "canonical reports"):
            provenance.validate_evidence_head(self.repo)

    def test_plan_serialization_is_byte_deterministic(self) -> None:
        module = load_script(
            "minimal_source_plan", TOOLS_DIR / "minimal-source-plan.py"
        )
        plan = {
            "entries": [{"sha256": "a", "mode": "0644", "path": "z"}],
            "evidence_commit": self.evidence,
            "evidence_source_commit": self.source,
        }
        first = self.repo / "first.json"
        second = self.repo / "second.json"
        module.write_plan(plan, first)
        module.write_plan(plan, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_evidence_objdirs_must_be_distinct(self) -> None:
        linux = self.make_objdir("linux-x86_64")
        android = self.make_objdir("android-aarch64")
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            collector.validate_objdirs(
                self.repo,
                {
                    "linux-x86_64": linux,
                    "windows-x86_64": linux,
                    "android-aarch64": android,
                },
            )

    def test_android_objdir_does_not_require_product_executable(self) -> None:
        android = self.make_objdir("android-aarch64")
        target = provenance.TARGET_SPECS_BY_NAME["android-aarch64"]
        self.assertFalse(target["executable"])
        collector.validate_objdir(self.repo, android, target)

    def test_android_platform_policy_allows_android_but_not_windows_crates(
        self,
    ) -> None:
        android_tokens = closure_assertions._forbidden_platform_crate_tokens("android")
        self.assertNotIn("android", android_tokens)
        self.assertIn("windows", android_tokens)
        linux_tokens = closure_assertions._forbidden_platform_crate_tokens(
            "desktop-linux"
        )
        self.assertIn("android", linux_tokens)
        self.assertIn("windows", linux_tokens)

    def test_analyzer_prefers_current_package_directory(self) -> None:
        objdir = Path(self.objdirs_temporary.name) / "package-layout"
        target = provenance.TARGET_SPECS_BY_NAME["android-aarch64"]
        legacy = objdir / "naivefox-package" / target["staged_package"]
        current = objdir / "package" / target["staged_package"]
        legacy.mkdir(parents=True)
        current.mkdir(parents=True)
        self.assertEqual(analyzer.staged_package_dir(objdir, target), current)

    def test_stale_objdir_is_rejected(self) -> None:
        target = collector.TARGETS[0]
        linux = self.make_objdir("linux-x86_64", omit=target["link_response"])
        with self.assertRaisesRegex(ValueError, "stale or incomplete"):
            collector.validate_objdir(self.repo, linux, target)

    def test_wrong_target_objdir_is_rejected(self) -> None:
        linux = self.make_objdir("linux-x86_64", overrides={"os": "win"})
        with self.assertRaisesRegex(ValueError, "os mismatch"):
            collector.validate_objdir(self.repo, linux, collector.TARGETS[0])

    def test_wrong_source_objdir_is_rejected(self) -> None:
        other_source = Path(self.objdirs_temporary.name) / "other-source"
        other_source.mkdir()
        linux = self.make_objdir(
            "linux-x86_64", overrides={"topsrcdir": str(other_source)}
        )
        with self.assertRaisesRegex(ValueError, "different source tree"):
            collector.validate_objdir(self.repo, linux, collector.TARGETS[0])

    def test_build_invocation_is_full_target_bound_and_test_disabled(self) -> None:
        linux = self.make_objdir("linux-x86_64")
        source = provenance.derive_source_provenance(self.repo)
        calls = []
        original_run = collector.run

        def fake_run(command, *, cwd=None, env=None):
            calls.append((command, cwd, env))

        collector.run = fake_run
        try:
            collector.build_target(
                self.repo,
                linux,
                collector.TARGETS[0],
                source,
                "firefox-upstream",
                "naivefox-full-source",
                {**os.environ, "NAIVEFOX_ENABLE_TESTS": "1"},
            )
        finally:
            collector.run = original_run
        self.assertEqual(calls[0][0], [str(self.repo / "mach"), "build", "-j4"])
        self.assertEqual(calls[0][1], self.repo)
        self.assertEqual(calls[0][2]["NAIVEFOX_ENABLE_TESTS"], "0")
        self.assertEqual(calls[0][2]["NAIVEFOX_OBJDIR"], str(linux))
        self.assertEqual(
            calls[0][2]["MOZCONFIG"],
            str((self.repo / collector.TARGETS[0]["mozconfig"]).resolve()),
        )

    def test_evidence_environment_forces_tests_off(self) -> None:
        previous = os.environ.get("NAIVEFOX_ENABLE_TESTS")
        os.environ["NAIVEFOX_ENABLE_TESTS"] = "1"
        try:
            self.assertEqual(
                collector.evidence_environment()["NAIVEFOX_ENABLE_TESTS"], "0"
            )
        finally:
            if previous is None:
                del os.environ["NAIVEFOX_ENABLE_TESTS"]
            else:
                os.environ["NAIVEFOX_ENABLE_TESTS"] = previous


if __name__ == "__main__":
    unittest.main()
