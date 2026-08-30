import copy
import json
from pathlib import Path
import tempfile
import unittest

from session_costs import summarize


class SessionCostTests(unittest.TestCase):
    def rows(self, variant=None):
        baseline = {"mode": "replace" if variant else "default", "app_profile": "continuous-v1",
                    "admitted": True, "session_wire": {"bytes": 1000},
                    "session_exercise": {"checks": [{"stage": "download", "useful_bytes": 100,
                                                       "completion_ms": 20, "curl_completion_ms": 15}]}}
        candidate = copy.deepcopy(baseline)
        candidate.update({"mode": "replace", "app_profile": variant or "continuous-v1"})
        candidate["session_wire"]["bytes"] = 1100
        candidate["session_exercise"]["checks"][0].update({"completion_ms": 15, "curl_completion_ms": 10})
        return [baseline, candidate]

    def report(self, rows):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, row in enumerate(rows):
                sample = root / f"session-{index:03d}"
                sample.mkdir()
                (sample / "result.json").write_text(json.dumps(row))
            return summarize(root)

    def test_native_and_profile_pairs_keep_distinct_labels(self):
        for variant in (None, "continuous-sync", "continuous-sync2"):
            result = self.report(self.rows(variant))
            self.assertEqual(result["control"], "continuous-v1" if variant else "default")
            self.assertEqual(result["candidate"], variant or "replace")
            self.assertAlmostEqual(result["wire"]["growth_percent"], 10)
            self.assertAlmostEqual(result["curl_stages"]["download"]["completion_reduction_percent"], 100 / 3)

    def test_incomplete_or_unequal_work_never_scores(self):
        rows = self.rows("continuous-sync2")
        for changed in (rows[:1], rows[1:]):
            with self.assertRaises(ValueError):
                self.report(changed)
        for field in ("admission", "stage", "size", "timing"):
            changed = copy.deepcopy(rows)
            if field == "admission": changed[1]["admitted"] = False
            elif field == "stage": changed[1]["session_exercise"]["checks"] = []
            elif field == "size": changed[1]["session_exercise"]["checks"][0]["useful_bytes"] += 1
            else: del changed[1]["session_exercise"]["checks"][0]["curl_completion_ms"]
            with self.assertRaises(ValueError):
                self.report(changed)

    def test_historical_poll_timing_is_not_promoted_to_precise_timing(self):
        rows = self.rows()
        for row in rows:
            del row["session_exercise"]["checks"][0]["curl_completion_ms"]
        self.assertEqual(self.report(rows)["curl_stages"], {})
