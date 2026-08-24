#!/usr/bin/env python3

import csv
import importlib.util
import os
import random
import tempfile
import unittest


HERE = os.path.dirname(__file__)
SPEC = importlib.util.spec_from_file_location(
    "analyze_camouflage", os.path.join(HERE, "analyze-camouflage.py")
)
ANALYZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZE)

FEATURE_SPEC = importlib.util.spec_from_file_location(
    "camouflage_features", os.path.join(HERE, "camouflage_features.py")
)
FEATURES = importlib.util.module_from_spec(FEATURE_SPEC)
FEATURE_SPEC.loader.exec_module(FEATURES)


def synthetic_rows(count=12, signal=True):
    rng = random.Random(1789)
    rows = []
    for label in ("firefox_a", "firefox_b", "naivefox"):
        for index in range(count):
            positive = label == "naivefox"
            value = (4.0 if positive else -4.0) + rng.uniform(-0.25, 0.25)
            if not signal:
                value = rng.uniform(-1.0, 1.0)
            rows.append(
                {
                    "protocol": "h2",
                    "scenario": "initial" if index % 2 else "browser_page",
                    "label": label,
                    "session_id": f"{label}-{index}",
                    "features": {
                        "whole_signal": value,
                        "whole_noise": rng.uniform(-1.0, 1.0),
                    },
                }
            )
    return rows


class CamouflageAnalysisTests(unittest.TestCase):
    def test_auc_ties_and_inversion(self):
        self.assertEqual(ANALYZE.auc([0, 1, 0, 1], [0.5] * 4), 0.5)
        value = ANALYZE.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9])
        inverted = ANALYZE.auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1])
        self.assertEqual(max(value, 1 - value), max(inverted, 1 - inverted))

    def test_grouped_folds_are_disjoint_and_deterministic(self):
        rows = synthetic_rows(10)
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")
        first = ANALYZE.grouped_folds(selected, labels, 5, 42)
        second = ANALYZE.grouped_folds(selected, labels, 5, 42)
        self.assertEqual(first, second)
        for train, test in first:
            train_groups = {selected[index]["session_id"] for index in train}
            test_groups = {selected[index]["session_id"] for index in test}
            self.assertFalse(train_groups & test_groups)

    def test_strong_signal_is_found_and_explained(self):
        rows = synthetic_rows(12)
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")
        result = ANALYZE.analyze_comparison(
            selected,
            labels,
            ["whole_signal", "whole_noise"],
            5,
            71,
            2,
            0.05,
            100,
            200,
            0,
        )
        self.assertTrue(result["available"])
        self.assertGreater(result["distinguishability"], 0.95)
        self.assertEqual(result["top_features"][0]["feature"], "whole_signal")

    def test_feature_views_do_not_accept_decrypted_boundaries(self):
        names = [
            "packet_001_direction",
            "packet_033_direction",
            "initial_16_packet_count",
            "steady_after_32_packet_count",
            "lifecycle_connection_count",
            "tls_client_hello_length",
            "quic_tcp_probe_client_syn_count",
        ]
        initial = ANALYZE.view_feature_names(names, "initial_packets_16")
        self.assertIn("packet_001_direction", initial)
        self.assertIn("quic_tcp_probe_client_syn_count", initial)
        self.assertNotIn("packet_033_direction", initial)
        self.assertEqual(
            ANALYZE.view_feature_names(names, "steady_after_32"),
            ["steady_after_32_packet_count"],
        )

    def test_tcp_option_parser_keeps_only_kind_order(self):
        raw = "02:04:05:b4:04:02:08:0a:12:34:56:78:00:00:00:00:01:03:03:07"
        self.assertEqual(FEATURES.parse_tcp_option_order(raw), [2, 4, 8, 1, 3])

    def test_quic_transport_parameter_grease_is_normalized(self):
        self.assertEqual(FEATURES.quic_transport_parameter_token("0x1b"), "grease")
        self.assertEqual(FEATURES.quic_transport_parameter_token("0x3a"), "grease")
        self.assertEqual(FEATURES.quic_transport_parameter_token("0x04"), "0x0004")

    def test_dataset_rejects_unknown_leakage_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "features.csv")
            with open(path, "w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=[
                        "schema_version",
                        "protocol",
                        "scenario",
                        "label",
                        "session_id",
                        "source_port",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "schema_version": 1,
                        "protocol": "h2",
                        "scenario": "initial",
                        "label": "firefox_a",
                        "session_id": "s1",
                        "source_port": 49152,
                    }
                )
            with self.assertRaises(SystemExit):
                ANALYZE.load_dataset(path)


if __name__ == "__main__":
    unittest.main()
