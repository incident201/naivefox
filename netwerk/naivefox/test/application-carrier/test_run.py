import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("carrier_runner", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CarrierAdmissionTests(unittest.TestCase):
    def test_frozen_budgets(self):
        down = {"v1": 1671168, "duplex-v1": 1671168, "compact": 884736,
                "compact-sync": 884736, "compact-sync20": 1146880,
                "compact-fast20": 1146880, "staged": 770048,
                "staged-fast": 770048, "staged-fast20": 901120,
                "staged-stream20": 901120, "staged-commit20": 905216}
        self.assertEqual(set(down), set(runner.PROFILES))
        for name, capacity in down.items():
            self.assertEqual(runner.profile_budget(name)[1], capacity)
            self.assertEqual(sum(runner.profile_requests(name).values()), runner.profile_budget(name)[3])
        self.assertEqual(runner.profile_budget("staged-fast20"), (20, 901120, 81920, 47))
        self.assertEqual(runner.profile_budget("duplex-v1"), (16, 1671168, 65536, 23))
        self.assertEqual(runner.profile_budget("staged-commit20"), (21, 905216, 86016, 48))

    def stats(self):
        return {"connect": 0, "rejected": 0, "requests": runner.profile_requests("staged-fast20"),
                "download_bytes": 901120, "upload_bytes": 81920,
                "opens": 0, "download_useful": 0, "upload_useful": 0,
                "download_filler": 900800, "upload_filler": 81600}

    def test_exact_capacity_and_empty_reference(self):
        runner.validate_http_graph(self.stats(), "staged-fast20", "reference")
        for key in ("connect", "rejected", "download_bytes", "upload_bytes", "opens", "download_useful", "upload_useful"):
            stats = self.stats()
            stats[key] += 1
            with self.assertRaises(RuntimeError):
                runner.validate_http_graph(stats, "staged-fast20", "reference")
        stats = self.stats()
        stats["requests"]["GET /"] += 1
        with self.assertRaises(RuntimeError):
            runner.validate_http_graph(stats, "staged-fast20", "replace")
        stats = self.stats()
        stats["requests"]["GET /wrong"] = stats["requests"].pop("GET /api/events/state")
        with self.assertRaises(RuntimeError):
            runner.validate_http_graph(stats, "staged-fast20", "replace")

    def test_append_must_retain_fixed_filler(self):
        stats = self.stats()
        stats["download_bytes"] += 8192
        stats["upload_bytes"] += 1024
        runner.validate_http_graph(stats, "staged-fast20", "append")
        for key in ("download_filler", "upload_filler"):
            changed = copy.deepcopy(stats)
            changed[key] -= 1
            with self.assertRaises(RuntimeError):
                runner.validate_http_graph(changed, "staged-fast20", "append")

    def test_shaping_refuses_missing_namespace(self):
        with mock.patch.dict(runner.os.environ, {}, clear=True):
            with mock.patch.object(runner.subprocess, "run") as command:
                with self.assertRaises(RuntimeError):
                    runner.Campaign(Path("/unused"), "h2").shape_outer(10)
                command.assert_not_called()

    def test_shaping_filters_only_outer_port(self):
        for protocol, number in (("h2", "6"), ("h3", "17")):
            with tempfile.TemporaryDirectory() as temporary:
                campaign = runner.Campaign(Path(temporary), protocol)
                campaign.port = 12345
                with mock.patch.dict(runner.os.environ, {"NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED": "1"}):
                    with mock.patch.object(runner.subprocess, "run") as command, mock.patch.object(runner.subprocess, "check_output", return_value="netem test"):
                        campaign.shape_outer(10)
                calls = [call.args[0] for call in command.call_args_list]
                self.assertEqual(len(calls), 4)
                for direction, call in zip(("sport", "dport"), calls[2:]):
                    self.assertIn(direction, call)
                    self.assertIn("12345", call)
                    self.assertIn(number, call)
                    self.assertEqual(call[-1], "1:3")


if __name__ == "__main__":
    unittest.main()
