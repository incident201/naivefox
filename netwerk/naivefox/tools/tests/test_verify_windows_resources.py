#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_windows_smoke", TOOLS / "verify-staged-windows-smoke.py"
)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class WindowsPackageResourcesTest(unittest.TestCase):
    def test_necko_localization_must_be_present_and_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            with self.assertRaisesRegex(AssertionError, "Necko localization"):
                verifier.validate_necko_localization(root)
            resource = root / "localization/en-US/netwerk/necko.ftl"
            resource.parent.mkdir(parents=True)
            resource.touch()
            with self.assertRaisesRegex(AssertionError, "Necko localization"):
                verifier.validate_necko_localization(root)
            resource.write_text("network-status = Connected\n", encoding="utf-8")
            verifier.validate_necko_localization(root)


if __name__ == "__main__":
    unittest.main()
