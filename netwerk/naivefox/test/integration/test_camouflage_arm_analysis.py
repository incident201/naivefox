#!/usr/bin/env python3

import csv
import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(__file__)
SPEC = importlib.util.spec_from_file_location(
    "analyze_camouflage_arms", os.path.join(HERE, "analyze-camouflage-arms.py")
)
ARMS_ANALYSIS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARMS_ANALYSIS)


def synthetic_blocks(count=40, unique_scenarios=False):
    blocks = []
    for index in range(count):
        midpoint = float(index % 4)
        scenario = f"scenario-{index}" if unique_scenarios else f"scenario-{index % 4}"
        references = {
            "firefox_a": {
                "whole_signal": midpoint - 0.2,
                "initial_32_signal": midpoint - 0.1,
            },
            "firefox_b": {
                "whole_signal": midpoint + 0.2,
                "initial_32_signal": midpoint + 0.1,
            },
        }
        blocks.append({
            "protocol": "h2",
            "experiment_block": f"h2_sb{index:06d}",
            "scenario": scenario,
            "references": references,
            "arms": {
                "off": {
                    "whole_signal": midpoint + 2.0,
                    "initial_32_signal": midpoint + 1.0,
                },
                "gate": {
                    "whole_signal": midpoint + 0.8,
                    "initial_32_signal": midpoint + 0.4,
                },
                "root": {
                    "whole_signal": midpoint,
                    "initial_32_signal": midpoint,
                },
            },
        })
    return blocks


def write_dataset(path, blocks):
    fieldnames = [
        "schema_version",
        "protocol",
        "scenario",
        "label",
        "naivefox_arm",
        "session_id",
        "experiment_block",
        "whole_signal",
    ]
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        session = 0
        for block in blocks:
            members = [
                ("firefox_a", "reference", block["references"]["firefox_a"]),
                ("firefox_b", "reference", block["references"]["firefox_b"]),
                *(
                    ("naivefox", arm, block["arms"][arm])
                    for arm in block["arms"]
                ),
            ]
            for label, arm, features in members:
                session += 1
                writer.writerow({
                    "schema_version": 1,
                    "protocol": block["protocol"],
                    "scenario": block["scenario"],
                    "label": label,
                    "naivefox_arm": arm,
                    "session_id": f"s{session}",
                    "experiment_block": block["experiment_block"],
                    "whole_signal": features["whole_signal"],
                })


class CamouflageArmAnalysisTests(unittest.TestCase):
    def test_proxy_floor_analysis_accepts_native_firefox_candidate_arm(self):
        blocks = synthetic_blocks(3)
        for block in blocks:
            block["arms"] = {
                "firefox-proxied": block["arms"]["gate"],
                "off": block["arms"]["off"],
            }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "proxy-floor.csv")
            write_dataset(path, blocks)
            rows, _ = ARMS_ANALYSIS.load_dataset(path)
            grouped = ARMS_ANALYSIS.group_superblocks(rows)
        self.assertEqual(len(grouped), 3)
        self.assertEqual(
            set(grouped[0]["arms"]), {"firefox-proxied", "off"}
        )

    def test_paired_distance_ranks_closer_arm_first(self):
        blocks = synthetic_blocks()
        result = ARMS_ANALYSIS.summarize_view(
            blocks,
            ["whole_signal", "initial_32_signal"],
            bootstrap_iterations=200,
            permutations=199,
            seed=41,
        )
        self.assertEqual(
            [item["arm"] for item in result["ranking"]],
            ["root", "gate", "off"],
        )
        self.assertEqual(result["arms"]["root"]["mean_distance"], 0.0)
        self.assertGreater(
            result["arms"]["off"]["mean_distance"],
            result["arms"]["gate"]["mean_distance"],
        )

    def test_opt_in_four_arm_screening_builds_all_pairs_and_early_views(self):
        blocks = synthetic_blocks()
        arms = ("gate", "root", "tree-complete", "tree-overlap")
        for block in blocks:
            midpoint = block["references"]["firefox_a"]["whole_signal"] + 0.2
            block["arms"] = {
                "gate": block["arms"]["gate"],
                "root": block["arms"]["root"],
                "tree-complete": {
                    "whole_signal": midpoint + 0.3,
                    "initial_32_signal": midpoint + 0.15,
                },
                "tree-overlap": {
                    "whole_signal": midpoint + 0.1,
                    "initial_32_signal": midpoint + 0.05,
                },
            }
        result = ARMS_ANALYSIS.summarize_view(
            blocks,
            ["whole_signal", "initial_32_signal"],
            bootstrap_iterations=100,
            permutations=99,
            seed=7,
            arms=arms,
        )
        self.assertEqual(set(result["arms"]), set(arms))
        self.assertEqual(len(result["paired_comparisons"]), 6)
        self.assertEqual(
            ARMS_ANALYSIS.parse_views(
                "initial_packets_16,packets_17_32,initial_packets_32"
            ),
            ("initial_packets_16", "packets_17_32", "initial_packets_32"),
        )

    def test_mechanism_diagnostics_keep_control_noise_and_signed_sequence(self):
        blocks = synthetic_blocks(2)
        feature = "packet_017_wire_size_signed"
        for block in blocks:
            block["references"]["firefox_a"][feature] = 100.0
            block["references"]["firefox_b"][feature] = 120.0
            block["arms"]["off"][feature] = 200.0
            block["arms"]["gate"][feature] = 140.0
            block["arms"]["root"][feature] = 112.0
        diagnostics = ARMS_ANALYSIS.mechanism_diagnostics(
            blocks, [feature], ("off", "gate", "root")
        )
        self.assertTrue(diagnostics["diagnostic_only"])
        self.assertFalse(diagnostics["used_for_arm_ranking_or_inference"])
        self.assertNotIn("decrypted", diagnostics["top_features_by_arm"])
        sequence = diagnostics["signed_packet_sequence"]
        self.assertEqual(len(sequence), 1)
        self.assertEqual(sequence[0]["packet_index"], 17)
        self.assertEqual(sequence[0]["firefox_midpoint_mean"], 110.0)
        self.assertEqual(
            sequence[0]["firefox_control_mean_abs_pair_difference"], 20.0
        )
        self.assertEqual(
            sequence[0]["arms"]["root"][
                "mean_signed_delta_from_firefox_midpoint"
            ],
            2.0,
        )
        self.assertEqual(
            diagnostics["top_features_by_arm"]["gate"][0]["feature"], feature
        )

    def test_mechanism_sequence_is_limited_to_first_32_passive_packets(self):
        blocks = synthetic_blocks(2)
        names = []
        for index in (16, 17, 32, 33):
            name = f"packet_{index:03d}_transport_size_signed"
            names.append(name)
            for block in blocks:
                for features in block["references"].values():
                    features[name] = float(index)
                for features in block["arms"].values():
                    features[name] = float(index)
        diagnostics = ARMS_ANALYSIS.mechanism_diagnostics(
            blocks, names, ("off", "gate", "root")
        )
        self.assertEqual(
            [item["packet_index"] for item in diagnostics["signed_packet_sequence"]],
            [16, 17, 32],
        )

    def test_selected_views_limit_report_and_holm_family(self):
        blocks = synthetic_blocks(30)
        rows = []
        for block in blocks:
            for label, features in block["references"].items():
                rows.append({
                    "protocol": block["protocol"],
                    "scenario": block["scenario"],
                    "label": label,
                    "naivefox_arm": "reference",
                    "session_id": f"{block['experiment_block']}-{label}",
                    "experiment_block": block["experiment_block"],
                    "features": features,
                })
            for arm, features in block["arms"].items():
                rows.append({
                    "protocol": block["protocol"],
                    "scenario": block["scenario"],
                    "label": "naivefox",
                    "naivefox_arm": arm,
                    "session_id": f"{block['experiment_block']}-{arm}",
                    "experiment_block": block["experiment_block"],
                    "features": features,
                })
        report = ARMS_ANALYSIS.build_report(
            SimpleNamespace(
                mode="standard",
                seed=3,
                bootstrap=100,
                permutations=99,
                min_blocks=30,
                views=("whole",),
            ),
            rows,
            ["whole_signal", "initial_32_signal"],
        )
        self.assertEqual(report["views_selected"], ["whole"])
        self.assertTrue(report["screening_only"])
        self.assertFalse(
            report["conclusion"]["supports_absolute_camouflage_verdict"]
        )
        self.assertEqual(list(report["protocols"]["h2"]["views"]), ["whole"])
        self.assertEqual(report["multiple_testing"]["tests"], 3)

    def test_pair_permutation_is_exact_for_smoke_sized_data(self):
        result = ARMS_ANALYSIS.paired_sign_flip([0.25] * 10, 999, 17)
        self.assertEqual(result["iterations"], 1024)
        self.assertIn("exact", result["method"])
        self.assertLess(result["p_value"], 0.01)

    def test_smoke_and_sparse_workloads_are_explicitly_insufficient(self):
        status = ARMS_ANALYSIS.inference_status(
            synthetic_blocks(10, unique_scenarios=True), "smoke", 30
        )
        self.assertFalse(status["supports_paired_inference"])
        self.assertIn("smoke mode is diagnostic only", status["reasons"])
        self.assertTrue(any("below minimum" in item for item in status["reasons"]))
        self.assertTrue(any("fewer than two" in item for item in status["reasons"]))

    def test_research_sized_balanced_blocks_are_eligible(self):
        status = ARMS_ANALYSIS.inference_status(synthetic_blocks(40), "research", 30)
        self.assertTrue(status["supports_paired_inference"])
        self.assertEqual(status["reasons"], [])

    def test_declared_minimum_blocks_cannot_be_lowered(self):
        status = ARMS_ANALYSIS.inference_status(synthetic_blocks(2), "standard", 2)
        self.assertFalse(status["supports_paired_inference"])
        self.assertEqual(status["minimum_blocks"], 30)
        self.assertTrue(any("below minimum 30" in item for item in status["reasons"]))

    def test_loader_requires_complete_shared_control_superblocks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "features-superblocks.csv")
            write_dataset(path, synthetic_blocks(2))
            rows, names = ARMS_ANALYSIS.load_dataset(path)
            grouped = ARMS_ANALYSIS.group_superblocks(rows)
        self.assertEqual(len(grouped), 2)
        self.assertEqual(names, ["whole_signal"])
        self.assertEqual(set(grouped[0]["arms"]), {"off", "gate", "root"})
        self.assertEqual(set(grouped[0]["references"]), {"firefox_a", "firefox_b"})

    def test_holm_correction_is_applied_only_to_eligible_protocols(self):
        report = {
            "protocols": {
                "h2": {
                    "inference": {"supports_paired_inference": True},
                    "views": {
                        "whole": {
                            "available": True,
                            "paired_comparisons": [
                                {
                                    "permutation_test": {
                                        "available": True,
                                        "p_value": 0.01,
                                    },
                                    "holm_adjusted_p_value": None,
                                },
                                {
                                    "permutation_test": {
                                        "available": True,
                                        "p_value": 0.04,
                                    },
                                    "holm_adjusted_p_value": None,
                                },
                            ],
                        }
                    },
                },
                "h3": {
                    "inference": {"supports_paired_inference": False},
                    "views": {
                        "whole": {
                            "available": True,
                            "paired_comparisons": [
                                {
                                    "permutation_test": {
                                        "available": True,
                                        "p_value": 0.001,
                                    },
                                    "holm_adjusted_p_value": None,
                                }
                            ],
                        }
                    },
                },
            }
        }
        self.assertEqual(ARMS_ANALYSIS.apply_holm_correction(report), 2)
        h2 = report["protocols"]["h2"]["views"]["whole"]
        self.assertEqual(h2["paired_comparisons"][0]["holm_adjusted_p_value"], 0.02)
        self.assertEqual(h2["paired_comparisons"][1]["holm_adjusted_p_value"], 0.04)
        h3 = report["protocols"]["h3"]["views"]["whole"]
        self.assertIsNone(h3["paired_comparisons"][0]["holm_adjusted_p_value"])


if __name__ == "__main__":
    unittest.main()
