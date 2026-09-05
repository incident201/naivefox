import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("current_publication", Path(__file__).with_name("publish-matched-app-results.py"))
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublicationTopologyTests(unittest.TestCase):
    def test_two_transports_define_primary_cardinality(self):
        self.assertEqual(publisher.MODES, ("classic", "no-connect"))
        self.assertEqual(len(publisher.ARMS), 4)
        self.assertEqual(len(publisher.GROUPS), 6)
        self.assertEqual(publisher.SAMPLES_PER_PROTOCOL, 60)
        self.assertEqual(publisher.TOTAL_SAMPLES, 120)

    def test_markdown_uses_only_retained_baselines(self):
        comparison = {"stages": {
            "download": {"effective_rate_loss_percent": 1},
            "upload": {"effective_rate_loss_percent": 2},
            "small": {"time_increase_percent": 3}},
            "extra_complete_session_traffic_percent": 4}
        row = {"startup_protocol": "h2", "listener": "socks",
               "residual": {name: {"mean_distance": .1} for name in
                            ("initial_packets_16", "packets_17_32", "whole")},
               "comparisons": {"classic": comparison, "firefox": comparison}}
        text = publisher.section({"no_connect_rows": [row]})
        self.assertIn("no-connect with classic", text)
        self.assertNotIn("hybrid", text)


if __name__ == "__main__":
    unittest.main()
