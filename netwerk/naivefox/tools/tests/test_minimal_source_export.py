#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import posixpath
import sys
import tempfile
import unittest
import urllib.parse

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from minimal_source_manifest import (  # noqa: E402
    PRODUCT_DOC_SOURCES,
    canonical_json,
    create_public_manifest,
    manifest_hash,
    render_root_readme,
    upstream_base_text,
)

spec = importlib.util.spec_from_file_location(
    "validate_minimal_source", TOOLS / "validate-minimal-source.py"
)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class MinimalSourceExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        nested_readme = (
            "# NaiveFox\n\nUse `config.json`. See [architecture](ARCHITECTURE.md).\n"
        )
        self.contents = {
            "README.md": render_root_readme(nested_readme),
            "config.example.json": "{}\n",
            "LICENSE": "test license\n",
            "toolkit/content/license.html": "<html>licenses</html>\n",
            "netwerk/naivefox/README.md": nested_readme,
            "netwerk/naivefox/ARCHITECTURE.md": "# Architecture\n",
            "netwerk/naivefox/KNOWN-ISSUES.md": "# Known issues\n",
            "netwerk/naivefox/NO-CONNECT.md": "# No-connect\n",
            "netwerk/naivefox/FRONTING-PAGE.md": "# Fronting page\n",
            "netwerk/naivefox/CAPTURE.md": (
                "# Capture\n[benchmark](test/integration/hybrid_app/BENCHMARK.md)\n"
            ),
            "netwerk/naivefox/SHIMS.md": "# Shims\n",
            "netwerk/naivefox/test/integration/README.md": "# Integration\n",
            "netwerk/naivefox/test/integration/hybrid_app/BENCHMARK.md": "# Benchmark\n",
        }
        self.plan = self.make_plan()
        self.write_export()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_plan(self) -> dict:
        entries = []
        for path, content in self.contents.items():
            source = PRODUCT_DOC_SOURCES.get(path, path)
            entries.append({
                "path": path,
                "source": source,
                "categories": ["internal-test-category"],
                "mode": "0644",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            })
        return {
            "plan_version": 99,
            "firefox_base_commit": "1" * 40,
            "naivefox_reference_commit": "2" * 40,
            "minimal_export_commit": "4" * 40,
            "evidence_source_commit": "3" * 40,
            "evidence_commit": "4" * 40,
            "entries": entries,
            "generated_contents": {},
            "cargo_license_inventory": [{"internal": True}],
            "evidence": [{"raw": "internal"}],
        }

    def write_export(self) -> None:
        for relative, content in self.contents.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o644)
        self.manifest = create_public_manifest(self.plan)
        (self.root / "minimal-source.manifest.json").write_bytes(
            canonical_json(self.manifest)
        )
        (self.root / "UPSTREAM-BASE").write_text(
            upstream_base_text(self.manifest), encoding="utf-8"
        )

    def replace_file_and_resign(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.write_text(content, encoding="utf-8")
        for entry in self.manifest["files"]:
            if entry["path"] == relative:
                entry["sha256"] = hashlib.sha256(content.encode()).hexdigest()
                break
        else:
            self.fail(f"missing test manifest path: {relative}")
        self.manifest["manifest_sha256"] = manifest_hash(self.manifest)
        (self.root / "minimal-source.manifest.json").write_bytes(
            canonical_json(self.manifest)
        )
        (self.root / "UPSTREAM-BASE").write_text(
            upstream_base_text(self.manifest), encoding="utf-8"
        )

    def assert_invalid(self, pattern: str) -> None:
        with self.assertRaisesRegex(validator.ValidationError, pattern):
            validator.validate(self.root)

    def test_public_manifest_is_compact_sorted_v2(self) -> None:
        self.assertEqual(validator.validate(self.root), len(self.contents))
        self.assertEqual(self.manifest["manifest_version"], 2)
        self.assertEqual(
            [entry["path"] for entry in self.manifest["files"]],
            sorted(self.contents),
        )
        serialized = canonical_json(self.manifest).decode()
        for internal in ("categories", "source", "cargo_license_inventory", "evidence"):
            self.assertNotIn(f'"{internal}"', serialized)

    def test_root_readme_rewrites_only_product_link_destinations(self) -> None:
        source = (
            "ARCHITECTURE.md prose\n"
            "[architecture](ARCHITECTURE.md#threading)\n"
            "[transport](NO-CONNECT.md#configuration)\n"
            "[fronting](FRONTING-PAGE.md)\n"
            "[external](https://example.invalid/ARCHITECTURE.md)\n"
        )
        rendered = render_root_readme(source)
        self.assertIn("ARCHITECTURE.md prose", rendered)
        self.assertIn("](netwerk/naivefox/ARCHITECTURE.md#threading)", rendered)
        self.assertIn("](netwerk/naivefox/NO-CONNECT.md#configuration)", rendered)
        self.assertIn("](netwerk/naivefox/FRONTING-PAGE.md)", rendered)
        self.assertIn("https://example.invalid/ARCHITECTURE.md", rendered)

    def test_operator_documents_are_required(self) -> None:
        for path in ("netwerk/naivefox/NO-CONNECT.md",
                     "netwerk/naivefox/FRONTING-PAGE.md",
                     "netwerk/naivefox/test/integration/hybrid_app/BENCHMARK.md"):
            plan = dict(self.plan)
            plan["entries"] = [
                entry for entry in self.plan["entries"] if entry["path"] != path
            ]
            with self.assertRaisesRegex(ValueError, "product document mapping"):
                create_public_manifest(plan)

    def test_actual_product_markdown_links_stay_within_exported_documents(self) -> None:
        repo = TOOLS.parents[2]
        for destination, source in PRODUCT_DOC_SOURCES.items():
            content = (repo / source).read_text(encoding="utf-8")
            if destination == "README.md":
                content = render_root_readme(content)
            for link in validator.markdown_destinations(content):
                parsed = urllib.parse.urlsplit(link)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = urllib.parse.unquote(parsed.path)
                if not path.lower().endswith(".md"):
                    continue
                target = posixpath.normpath(posixpath.join(
                    posixpath.dirname(destination), path
                ))
                with self.subTest(document=destination, link=link):
                    self.assertIn(target, PRODUCT_DOC_SOURCES)

    def test_missing_file_is_rejected(self) -> None:
        (self.root / "config.example.json").unlink()
        self.assert_invalid("missing or not regular")

    def test_extra_file_is_rejected(self) -> None:
        (self.root / "unexpected.txt").write_text("extra\n", encoding="utf-8")
        self.assert_invalid("file list mismatch")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO nodes are POSIX-only")
    def test_fifo_is_rejected(self) -> None:
        os.mkfifo(self.root / "unexpected.fifo")
        self.assert_invalid("unsupported filesystem node")

    def test_tampered_content_is_rejected(self) -> None:
        (self.root / "config.example.json").write_text("tampered\n", encoding="utf-8")
        self.assert_invalid("content hash mismatch")

    def test_mode_change_is_rejected(self) -> None:
        os.chmod(self.root / "config.example.json", 0o755)
        self.assert_invalid("mode mismatch")

    def test_manifest_hash_change_is_rejected(self) -> None:
        self.manifest["counts"]["files"] += 1
        (self.root / "minimal-source.manifest.json").write_bytes(
            canonical_json(self.manifest)
        )
        self.assert_invalid("manifest SHA-256")

    def test_non_public_manifest_metadata_is_rejected(self) -> None:
        self.manifest["cargo_license_inventory"] = []
        self.manifest["manifest_sha256"] = manifest_hash(self.manifest)
        (self.root / "minimal-source.manifest.json").write_bytes(
            canonical_json(self.manifest)
        )
        self.assert_invalid("manifest fields mismatch")

    def test_provenance_mismatch_is_rejected(self) -> None:
        self.manifest["evidence_commit"] = "5" * 40
        self.manifest["manifest_sha256"] = manifest_hash(self.manifest)
        (self.root / "minimal-source.manifest.json").write_bytes(
            canonical_json(self.manifest)
        )
        self.assert_invalid("minimal export and evidence commits differ")

    def test_broken_relative_markdown_link_is_rejected(self) -> None:
        self.replace_file_and_resign(
            "netwerk/naivefox/ARCHITECTURE.md",
            "# Architecture\n\n[missing](does-not-exist.md)\n",
        )
        self.assert_invalid("broken Markdown link")

    def test_v1_manifest_gets_migration_diagnostic(self) -> None:
        (self.root / "minimal-source.manifest.json").write_text(
            json.dumps({"manifest_version": 1}), encoding="utf-8"
        )
        self.assert_invalid("legacy manifest version 1")


if __name__ == "__main__":
    unittest.main()
