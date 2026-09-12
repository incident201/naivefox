import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).with_name("audit-matched-app-results.py")
spec = importlib.util.spec_from_file_location("matched_app_audit", path)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class MatchedAppAuditTests(unittest.TestCase):
    def write_fixture(self, root, arms):
        rows = []
        for block in range(2):
            for label, arm, value in [
                ("firefox_a", "reference", 100),
                ("firefox_b", "reference", 120),
                *(
                    ("naivefox", arm, 110 if index % 2 == 0 else 140)
                    for index, arm in enumerate(arms)
                ),
            ]:
                rows.append({
                    "protocol": "h2",
                    "experiment_block": str(block),
                    "label": label,
                    "naivefox_arm": arm,
                    "packet_1_ip_length": value,
                })
        with (root / "features.csv").open("w") as output:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        values = {
            arm: {
                "mean_distance": 0.0 if index % 2 == 0 else 0.5,
                "median_block_distance": 0.0 if index % 2 == 0 else 0.5,
            }
            for index, arm in enumerate(arms)
        }
        (root / "analysis.json").write_text(
            json.dumps({
                "protocols": {
                    "h2": {
                        "views": {
                            "whole": {"features": 1, "arms": values},
                            "initial_packets_16": {"features": 1, "arms": values},
                        }
                    }
                }
            })
        )
        return rows

    def test_recomputes_current_and_paired_comparisons(self):
        for arms in [
            ("native-socks", "native-http"),
            ("before-socks", "before-http", "after-socks", "after-http"),
        ]:
            with self.subTest(arms=arms), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.write_fixture(root, arms)
                result = audit.audit_distances(root, "h2")
                self.assertEqual(result["whole"]["all_arms_recomputed"], len(arms))

    def test_missing_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_fixture(root, ("native-socks", "native-http"))
            lines = (root / "features.csv").read_text().splitlines()
            (root / "features.csv").write_text(
                "\n".join(line for line in lines if "firefox_b" not in line) + "\n"
            )
            with self.assertRaisesRegex(RuntimeError, "incomplete paired blocks"):
                audit.audit_distances(root, "h2")


if __name__ == "__main__":
    unittest.main()
