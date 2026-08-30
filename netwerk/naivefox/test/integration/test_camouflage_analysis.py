#!/usr/bin/env python3

import csv
import importlib.util
import os
import random
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

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
            rows.append({
                "protocol": "h2",
                "scenario": "initial" if index % 2 else "browser_page",
                "label": label,
                "session_id": f"{label}-{index}",
                "features": {
                    "whole_signal": value,
                    "whole_noise": rng.uniform(-1.0, 1.0),
                },
            })
    return rows


class CamouflageAnalysisTests(unittest.TestCase):
    def test_research_inference_requires_240_samples_per_cohort(self):
        insufficient = ANALYZE.absolute_inference_status(
            synthetic_rows(239), "research"
        )
        self.assertFalse(insufficient["supports_absolute_verdict"])
        self.assertFalse(insufficient["research_samples_sufficient"])
        self.assertEqual(len(insufficient["research_sample_shortfalls"]), 3)
        self.assertTrue(
            all(
                item["minimum"] == ANALYZE.MIN_RESEARCH_SAMPLES_PER_COHORT
                for item in insufficient["research_sample_shortfalls"]
            )
        )

        sufficient = ANALYZE.absolute_inference_status(synthetic_rows(240), "research")
        self.assertTrue(sufficient["supports_absolute_verdict"])
        self.assertTrue(sufficient["research_samples_sufficient"])

    def test_multi_arm_screening_disables_absolute_research_verdict(self):
        status = ANALYZE.absolute_inference_status(
            synthetic_rows(240), "research", screening_only=True
        )
        self.assertTrue(status["research_samples_sufficient"])
        self.assertFalse(status["supports_absolute_verdict"])
        self.assertEqual(status["status"], "INCONCLUSIVE")
        self.assertTrue(any("screening" in reason for reason in status["reasons"]))

    def test_screening_report_forces_all_classifications_inconclusive(self):
        baseline = {
            "available": True,
            "auc": 0.5,
            "auc_ci95": [0.45, 0.55],
            "distinguishability": 0.5,
            "refit_bootstrap": {"auc_ci95": [0.45, 0.55]},
        }
        target = {
            "available": True,
            "auc": 0.8,
            "auc_ci95": [0.7, 0.9],
            "distinguishability": 0.8,
            "refit_bootstrap": {"auc_ci95": [0.7, 0.9]},
            "permutation_test": {"p_value": 0.001},
        }
        original_analyze = ANALYZE.analyze_comparison
        original_cross = ANALYZE.cross_workload
        original_handshake = ANALYZE.passive_handshake_differences

        def fixed_comparison(selected_rows, _labels, *_args, **_kwargs):
            has_naivefox = any(row["label"] == "naivefox" for row in selected_rows)
            return dict(target if has_naivefox else baseline)

        ANALYZE.analyze_comparison = fixed_comparison
        ANALYZE.cross_workload = lambda *_args, **_kwargs: {
            "macro_auc": None,
            "holdouts": {},
        }
        ANALYZE.passive_handshake_differences = lambda *_args, **_kwargs: {
            "different_feature_count": 0,
            "top_differences": [],
        }
        try:
            report = ANALYZE.build_report(
                SimpleNamespace(
                    mode="research",
                    screening_only=True,
                    seed=91,
                    max_features=2,
                    l2=0.05,
                    iterations=20,
                    bootstrap=100,
                    permutations=99,
                    refit_bootstrap=20,
                    folds=5,
                ),
                synthetic_rows(240),
                ["whole_signal", "whole_noise"],
            )
        finally:
            ANALYZE.analyze_comparison = original_analyze
            ANALYZE.cross_workload = original_cross
            ANALYZE.passive_handshake_differences = original_handshake
        self.assertFalse(report["inference"]["supports_absolute_verdict"])
        self.assertEqual(
            report["conclusion"]["naivefox_distinguishable_by_selected_classifiers"],
            "INCONCLUSIVE",
        )
        for protocol in report["protocols"].values():
            for view in protocol["views"].values():
                self.assertEqual(view["classification"], "inconclusive")

    def test_research_cli_rejects_undersized_dataset_before_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            features = os.path.join(directory, "features.csv")
            output_json = os.path.join(directory, "metrics.json")
            output_summary = os.path.join(directory, "summary.txt")
            with open(features, "w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=(
                        "schema_version",
                        "protocol",
                        "scenario",
                        "label",
                        "session_id",
                        "whole_packet_count",
                    ),
                )
                writer.writeheader()
                for label in ANALYZE.COHORT_LABELS:
                    writer.writerow({
                        "schema_version": 1,
                        "protocol": "h2",
                        "scenario": "initial",
                        "label": label,
                        "session_id": label,
                        "whole_packet_count": 1,
                    })
            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(HERE, "analyze-camouflage.py"),
                    "--features",
                    features,
                    "--output-json",
                    output_json,
                    "--output-summary",
                    output_summary,
                    "--mode",
                    "research",
                    "--seed",
                    "1",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("at least 240 samples", result.stderr)
            self.assertFalse(os.path.exists(output_json))
            self.assertFalse(os.path.exists(output_summary))

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

    def test_experiment_blocks_are_kept_in_one_fold(self):
        rows = synthetic_rows(10)
        for row in rows:
            row["experiment_block"] = "block-" + row["session_id"].rsplit("-", 1)[1]
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")
        split = ANALYZE.grouped_folds(selected, labels, 5, 42)
        for train, test in split:
            train_groups = {ANALYZE.analysis_group(selected[index]) for index in train}
            test_groups = {ANALYZE.analysis_group(selected[index]) for index in test}
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
        self.assertGreater(result["auc"], 0.95)
        self.assertEqual(result["top_features"][0]["feature"], "whole_signal")

    def test_null_signal_stays_at_chance(self):
        rows = synthetic_rows(12)
        for row in rows:
            row["features"] = {"whole_constant": 1.0}
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")
        result = ANALYZE.analyze_comparison(
            selected,
            labels,
            ["whole_constant"],
            5,
            73,
            1,
            0.05,
            40,
            100,
            9,
        )
        self.assertEqual(result["auc"], 0.5)
        self.assertEqual(result["auc_ci95"], [0.5, 0.5])
        self.assertEqual(result["permutation_test"]["p_value"], 1.0)

    def test_permutation_refits_the_full_pipeline(self):
        rows = synthetic_rows(8)
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")
        original = ANALYZE.fit_cross_validated
        calls = []

        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        ANALYZE.fit_cross_validated = counted
        try:
            result = ANALYZE.analyze_comparison(
                selected,
                labels,
                ["whole_signal", "whole_noise"],
                4,
                79,
                2,
                0.05,
                40,
                100,
                7,
            )
        finally:
            ANALYZE.fit_cross_validated = original
        self.assertEqual(len(calls), 8)
        self.assertIn("pipeline refit", result["permutation_test"]["method"])

    def test_unblocked_permutation_changes_session_labels(self):
        rows = synthetic_rows(4)
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")

        class ReverseRandom:
            @staticmethod
            def shuffle(values):
                values.reverse()

        permuted = ANALYZE.permuted_session_labels(selected, labels, ReverseRandom())
        self.assertNotEqual(permuted, labels)

    def test_auc_is_compared_only_within_outer_folds(self):
        labels = [0, 1, 0, 1]
        scores = [0.1, 0.2, 0.8, 0.9]
        result = ANALYZE.fold_auc_metrics(labels, scores, [0, 0, 1, 1])
        self.assertEqual(result["auc"], 1.0)
        self.assertEqual(ANALYZE.auc(labels, scores), 0.75)

    def test_verdict_does_not_invert_held_out_auc(self):
        target = {
            "available": True,
            "auc": 0.2,
            "auc_ci95": [0.15, 0.25],
            "distinguishability": 0.8,
            "distinguishability_ci95": [0.75, 0.85],
            "refit_bootstrap": {"auc_ci95": [0.15, 0.25]},
        }
        baseline = {
            "available": True,
            "auc": 0.5,
            "distinguishability": 0.5,
            "refit_bootstrap": {"auc_ci95": [0.45, 0.55]},
        }
        self.assertEqual(ANALYZE.classify(target, baseline, "research"), "yellow")

    def test_red_verdict_requires_permutation_support(self):
        target = {
            "available": True,
            "auc": 0.8,
            "auc_ci95": [0.7, 0.9],
            "distinguishability": 0.8,
            "permutation_test": None,
            "refit_bootstrap": {"auc_ci95": [0.7, 0.9]},
        }
        baseline = {
            "available": True,
            "auc": 0.5,
            "distinguishability": 0.5,
            "refit_bootstrap": {"auc_ci95": [0.45, 0.55]},
        }
        self.assertEqual(ANALYZE.classify(target, baseline, "research"), "yellow")
        target["permutation_test"] = {"p_value": 0.01}
        self.assertEqual(ANALYZE.classify(target, baseline, "research"), "red")

    def test_unhealthy_firefox_baseline_cannot_produce_green(self):
        target = {
            "available": True,
            "auc": 0.54,
            "refit_bootstrap": {"auc_ci95": [0.48, 0.59]},
        }
        baseline = {
            "available": True,
            "auc": 0.9,
            "refit_bootstrap": {"auc_ci95": [0.82, 0.96]},
        }
        self.assertEqual(ANALYZE.classify(target, baseline, "research"), "yellow")
        baseline["auc"] = 0.5
        baseline["refit_bootstrap"] = {"auc_ci95": [0.0, 1.0]}
        self.assertEqual(ANALYZE.classify(target, baseline, "research"), "yellow")
        baseline["refit_bootstrap"] = {"auc_ci95": [0.45, 0.55]}
        self.assertEqual(ANALYZE.classify(target, baseline, "research"), "green")

    def test_refit_bootstrap_refits_grouped_cv_pipeline(self):
        rows = synthetic_rows(8)
        selected, labels = ANALYZE.comparison_rows(rows, "firefox_vs_naivefox")
        original = ANALYZE.fit_cross_validated
        duplicate_seen = []

        def checked(sampled_rows, *args, **kwargs):
            groups_by_session = {}
            for row in sampled_rows:
                groups_by_session.setdefault(row["session_id"], set()).add(
                    ANALYZE.analysis_group(row)
                )
            duplicated = [
                session
                for session, groups in groups_by_session.items()
                if sum(row["session_id"] == session for row in sampled_rows) > 1
            ]
            if duplicated:
                duplicate_seen.append(True)
                self.assertTrue(
                    all(len(groups_by_session[session]) == 1 for session in duplicated)
                )
            return original(sampled_rows, *args, **kwargs)

        ANALYZE.fit_cross_validated = checked
        try:
            result = ANALYZE.refit_clustered_bootstrap(
                selected,
                labels,
                ["whole_signal", "whole_noise"],
                4,
                83,
                2,
                0.05,
                40,
                20,
            )
        finally:
            ANALYZE.fit_cross_validated = original
        self.assertEqual(result["iterations"], 20)
        self.assertIn("pipeline refit", result["method"])
        self.assertGreater(result["auc_ci95"][0], 0.9)
        self.assertTrue(duplicate_seen)

    def test_permutation_refits_are_limited_to_primary_target_views(self):
        self.assertEqual(
            ANALYZE.permutation_plan("initial_packets_32", "firefox_vs_naivefox", 19),
            (19, None),
        )
        count, reason = ANALYZE.permutation_plan("whole", "firefox_vs_naivefox", 19)
        self.assertEqual(count, 0)
        self.assertIn("primary views", reason)
        count, reason = ANALYZE.permutation_plan(
            "initial_packets_32", "firefox_baseline", 19
        )
        self.assertEqual(count, 0)
        self.assertIn("baseline", reason)

    def test_feature_views_do_not_accept_decrypted_boundaries(self):
        names = [
            "packet_001_direction",
            "packet_016_wire_size_signed",
            "packet_017_wire_size_signed",
            "packet_032_transport_size_signed",
            "packet_033_direction",
            "tls_record_016_signed_length",
            "tls_record_017_signed_length",
            "tls_record_032_signed_length",
            "tls_record_033_signed_length",
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
        second_window = ANALYZE.view_feature_names(names, "packets_17_32")
        self.assertEqual(
            second_window,
            [
                "packet_017_wire_size_signed",
                "packet_032_transport_size_signed",
                "tls_record_017_signed_length",
                "tls_record_032_signed_length",
            ],
        )
        self.assertNotIn("quic_tcp_probe_client_syn_count", second_window)
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
        for leakage in (
            "source_port",
            "whole_source_port",
            "whole_label",
            "whole_session_id",
            "whole_naivefox_arm",
            "quic_decrypted_method",
            "whole_plaintext_header",
            "whole_stream_id",
            "whole_status",
        ):
            with self.subTest(
                leakage=leakage
            ), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "features.csv")
                fieldnames = [
                    "schema_version",
                    "protocol",
                    "scenario",
                    "label",
                    "session_id",
                    leakage,
                ]
                with open(path, "w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerow({
                        "schema_version": 1,
                        "protocol": "h2",
                        "scenario": "initial",
                        "label": "firefox_a",
                        "session_id": "s1",
                        leakage: 49152,
                    })
                with self.assertRaises(SystemExit):
                    ANALYZE.load_dataset(path)

    def test_dataset_accepts_optional_blocks_and_adds_phase_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "features.csv")
            fieldnames = [
                "schema_version",
                "protocol",
                "scenario",
                "label",
                "session_id",
                "experiment_block",
                "naivefox_arm",
                "steady_after_32_packet_count",
            ]
            with open(path, "w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                for label, count in (
                    ("firefox_a", ""),
                    ("firefox_b", ""),
                    ("naivefox", "5"),
                ):
                    writer.writerow({
                        "schema_version": 1,
                        "protocol": "h3",
                        "scenario": "initial",
                        "label": label,
                        "session_id": label,
                        "experiment_block": "block-1",
                        "naivefox_arm": (
                            "document-handshake-confirmed"
                            if label == "naivefox"
                            else "reference"
                        ),
                        "steady_after_32_packet_count": count,
                    })
            rows, names = ANALYZE.load_dataset(path)
        self.assertIn("steady_after_32_present", names)
        self.assertEqual(
            [row["features"]["steady_after_32_present"] for row in rows],
            [0.0, 0.0, 1.0],
        )
        self.assertEqual(
            {ANALYZE.analysis_group(row) for row in rows}, {"block:block-1"}
        )
        self.assertEqual(
            [row["naivefox_arm"] for row in rows],
            ["reference", "reference", "document-handshake-confirmed"],
        )

    def test_dataset_accepts_h2_native_firefox_proxy_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "features.csv")
            fieldnames = [
                "schema_version",
                "protocol",
                "scenario",
                "label",
                "session_id",
                "naivefox_arm",
                "whole_packet_count",
            ]
            with open(path, "w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                for label, arm in (
                    ("firefox_a", "reference"),
                    ("firefox_b", "reference"),
                    ("naivefox", "firefox-proxied"),
                ):
                    writer.writerow({
                        "schema_version": 1,
                        "protocol": "h2",
                        "scenario": "browser_page",
                        "label": label,
                        "session_id": label,
                        "naivefox_arm": arm,
                        "whole_packet_count": 1,
                    })
            rows, _ = ANALYZE.load_dataset(path)
        self.assertEqual(rows[-1]["naivefox_arm"], "firefox-proxied")

    def test_finite_exchange_metadata_is_h2_only(self):
        for arm in (
            "h2-finite-socks",
            "h2-finite-http-connect",
            "h2-finite-read-through-socks",
            "h2-finite-read-through-http-connect",
            "h2-finite-both-read-through-socks",
            "h2-finite-both-read-through-http-connect",
            "h2-finite-both-read-through-budgeted-socks",
            "h2-finite-both-read-through-budgeted-http-connect",
            "h2-finite-both-read-through-budgeted-data-window-socks",
            "h2-finite-both-read-through-budgeted-data-window-http-connect",
        ):
            for protocol in ("h2", "h3"):
                with self.subTest(
                    arm=arm, protocol=protocol
                ), tempfile.TemporaryDirectory() as directory:
                    path = os.path.join(directory, "features.csv")
                    fields = [
                        "schema_version",
                        "protocol",
                        "scenario",
                        "label",
                        "session_id",
                        "naivefox_arm",
                        "whole_packet_count",
                    ]
                    with open(path, "w", newline="", encoding="utf-8") as stream:
                        writer = csv.DictWriter(stream, fieldnames=fields)
                        writer.writeheader()
                        for label, selected in (
                            ("firefox_a", "reference"),
                            ("firefox_b", "reference"),
                            ("naivefox", arm),
                        ):
                            writer.writerow({
                                "schema_version": 1,
                                "protocol": protocol,
                                "scenario": "browser_page",
                                "label": label,
                                "session_id": label,
                                "naivefox_arm": selected,
                                "whole_packet_count": 1,
                            })
                    if protocol == "h2":
                        rows, _ = ANALYZE.load_dataset(path)
                        self.assertEqual(rows[-1]["naivefox_arm"], arm)
                    else:
                        with self.assertRaisesRegex(SystemExit, "requires h2"):
                            ANALYZE.load_dataset(path)


if __name__ == "__main__":
    unittest.main()
