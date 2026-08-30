#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_shims", TOOLS / "verify-shims.py")
assert spec and spec.loader
shims = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shims)


class ShimVerificationTest(unittest.TestCase):
    def test_missing_explicit_binary_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(shims.test_symbol_absence(temporary, temporary))

    def test_symbol_scan_demangles_cpp_names(self):
        with mock.patch.object(shims.os.path, "exists", return_value=True):
            with mock.patch.object(shims.subprocess, "check_output", return_value="absl::container") as scan:
                self.assertFalse(shims.test_symbol_absence("source", "obj"))
                self.assertIn("-C", scan.call_args.args[0])

    def test_current_cache_boundary(self):
        self.assertTrue(shims.test_cache_crypto_boundary(TOOLS.parents[2]))


if __name__ == "__main__":
    unittest.main()
