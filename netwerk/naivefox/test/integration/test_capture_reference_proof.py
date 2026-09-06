import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("capture_reference", Path(__file__).with_name("verify-capture-reference.py"))
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class OfficialReferenceProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = "a" * 40
        names = ["firefox", "firefox-bin", "libxul.so", "libnss3.so", "libssl3.so"]
        for name in names:
            (self.root / name).write_bytes(name.encode())
        (self.root / "application.ini").write_text("[App]\nSourceStamp=hg-stamp\nBuildID=123\n")
        self.proof = {"schema_version": 1, "git_base": self.base, "hg_revision": "hg-stamp", "build_id": "123",
                      "runtime_files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}}
        self.path = self.root / "proof.json"

    def check(self):
        self.path.write_text(json.dumps(self.proof))
        return reference.verify_runtime_proof(self.path, self.root / "firefox", self.base)

    def test_verified_files(self):
        self.assertEqual(self.check(), 6)

    def test_changed_binary(self):
        (self.root / "libssl3.so").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.check()

    def test_different_source(self):
        self.proof["git_base"] = "b" * 40
        with self.assertRaises(ValueError):
            self.check()

    def test_missing_essential_file(self):
        del self.proof["runtime_files_sha256"]["libssl3.so"]
        with self.assertRaises(ValueError):
            self.check()

    def test_path_escape(self):
        self.proof["runtime_files_sha256"]["../outside"] = "0" * 64
        with self.assertRaises(ValueError):
            self.check()

    def test_application_identity(self):
        self.proof["hg_revision"] = "wrong"
        with self.assertRaises(ValueError):
            self.check()
