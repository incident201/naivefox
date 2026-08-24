#!/usr/bin/env python3

import csv
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(__file__)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE = load("camouflage_capture_health", "camouflage_capture_health.py")
CONTROLLER = load(
    "camouflage_browser_controller", "camouflage_browser_controller.py"
)
FEATURES = load("camouflage_features", "camouflage_features.py")
TARGET = load("target_server", "target_server.py")


class CamouflageHarnessTests(unittest.TestCase):
    def test_direct_h3_browser_gets_forced_alt_svc_mapping(self):
        preferences = CONTROLLER.firefox_preferences("h3", 4433, 0)
        self.assertTrue(preferences["network.http.http3.enable"])
        self.assertEqual(
            preferences["network.http.http3.alt-svc-mapping-for-testing"],
            "localhost;h3=:4433",
        )

    def test_socks_browser_does_not_get_outer_h3_mapping(self):
        preferences = CONTROLLER.firefox_preferences("h3", 4433, 1080)
        self.assertFalse(preferences["network.http.http3.enable"])
        self.assertNotIn(
            "network.http.http3.alt-svc-mapping-for-testing", preferences
        )
        self.assertEqual(preferences["network.proxy.socks_port"], 1080)

    def test_dumpcap_clean_shutdown_is_accepted(self):
        CAPTURE.validate_dumpcap_log(
            """Capturing on 'any'
File: /tmp/capture.pcapng
Packets captured: 42
Packets received/dropped on interface 'any': 84/0 (pcap:0/dumpcap:0/flushed:0/ps_ifdrop:0) (0.0%)
"""
        )

    def test_dumpcap_drop_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "dropped packets"):
            CAPTURE.validate_dumpcap_log(
                """Packets captured: 42
Packets received/dropped on interface 'any': 84/1 (pcap:1/dumpcap:0/flushed:0/ps_ifdrop:0) (1.2%)
"""
            )

    def test_dumpcap_without_final_statistics_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "final interface statistics"):
            CAPTURE.validate_dumpcap_log(
                "Capturing on 'any'\nFile: /tmp/capture.pcapng\n"
            )

    def test_controlled_page_reports_completion_after_load(self):
        token = "a" * 32
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["initial"], "completion": [token]}
        ).decode()
        self.assertIn("window.addEventListener('load'", page)
        self.assertIn(f"/camouflage/complete?token={token}", page)

    def test_controlled_page_rejects_invalid_completion_token(self):
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["initial"], "completion": ["../bad"]}
        )
        self.assertIsNone(page)

    def test_completion_marker_is_private_and_complete(self):
        token = "b" * 32
        with tempfile.TemporaryDirectory() as directory:
            TARGET.write_completion(directory, token)
            path = os.path.join(directory, token)
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "complete\n")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_feature_merge_preserves_complete_blocks_and_old_fragments(self):
        with tempfile.TemporaryDirectory() as directory:
            documents = (
                {
                    "schema_version": 1,
                    "protocol": "h2",
                    "scenario": "initial",
                    "label": "firefox_a",
                    "session_id": "s1",
                    "experiment_block": "h2_b000001",
                    "features": {"whole_packet_count": 1.0},
                },
                {
                    "schema_version": 1,
                    "protocol": "h2",
                    "scenario": "initial",
                    "label": "naivefox",
                    "session_id": "s2",
                    "features": {"whole_packet_count": 2.0},
                },
            )
            for index, document in enumerate(documents):
                with open(
                    os.path.join(directory, f"{index}.json"),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    json.dump(document, stream)
            output = os.path.join(directory, "features.csv")
            FEATURES.merge(
                SimpleNamespace(
                    input_dir=directory,
                    output=output,
                    expected_per_cohort=None,
                )
            )
            with open(output, newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["experiment_block"], "h2_b000001")
            self.assertEqual(rows[1]["experiment_block"], "")

    def test_feature_merge_rejects_globally_balanced_but_broken_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = ("firefox_a", "firefox_b", "naivefox") * 2
            blocks = ("b1", "b2", "b1", "b1", "b2", "b2")
            scenarios = ("initial", "page", "initial", "initial", "page", "page")
            for index, (label, block, scenario) in enumerate(
                zip(labels, blocks, scenarios, strict=True)
            ):
                document = {
                    "schema_version": 1,
                    "protocol": "h2",
                    "scenario": scenario,
                    "label": label,
                    "session_id": f"s{index}",
                    "experiment_block": block,
                    "features": {"whole_packet_count": float(index)},
                }
                with open(
                    os.path.join(directory, f"{index}.json"),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    json.dump(document, stream)
            with self.assertRaisesRegex(SystemExit, "incomplete experiment block"):
                FEATURES.merge(
                    SimpleNamespace(
                        input_dir=directory,
                        output=os.path.join(directory, "features.csv"),
                        expected_per_cohort=2,
                    )
                )


if __name__ == "__main__":
    unittest.main()
