import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("carrier_runner", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CarrierAdmissionTests(unittest.TestCase):
    def test_coalesced_quic_ids_are_not_extra_connections(self):
        self.assertEqual(runner.outer_flow_count([{"flow": ""}, {"flow": "0"}, {"flow": "0;0"}]), 1)

    def test_continuous_budget_includes_active_leases_and_idle(self):
        stats = self.stats()
        stats.update({"requests": runner.profile_requests("continuous-v1"), "write_errors": 0,
                      "cell_capacities": {"8192": 6, "32768": 2, "65536": 12},
                      "idle_started": 1, "idle_completed": 0, "idle_cancelled": 1})
        stats["requests"]["GET /api/events/idle"] = 1
        runner.validate_http_graph(stats, "continuous-v1", "reference")
        stats["requests"]["GET /api/data/download"] = 4
        stats["requests"]["POST /api/sync"] += 4
        stats["cell_capacities"]["65536"] += 4
        stats["download_bytes"] += 4 * 65536
        stats["upload_bytes"] += 4 * 4096
        runner.validate_http_graph(stats, "continuous-v1", "replace")
        stats["download_bytes"] += 1
        with self.assertRaises(RuntimeError):
            runner.validate_http_graph(stats, "continuous-v1", "replace")

    def test_strict_h2_fixture_aliases(self):
        target_spec = importlib.util.spec_from_file_location("carrier_target", runner.INTEGRATION / "target_server.py")
        target = importlib.util.module_from_spec(target_spec)
        target_spec.loader.exec_module(target)
        handler = object.__new__(target.Handler)
        received = []
        handler.send_bytes = lambda *value: received.append(value)
        handler.send_error = lambda *value: self.fail("alias rejected")
        handler.path = "/camouflage/delay?ms=0"
        with mock.patch.object(target.time, "sleep"):
            handler.do_GET()
        self.assertEqual(received[-1][1], target.SMALL_BODY)
        payload = b"carrier alias upload"
        handler.path = "/camouflage/slow-upload?ms=0"
        handler.rfile = io.BytesIO(payload)
        handler.headers = {"Content-Length": str(len(payload))}
        handler.do_POST()
        self.assertEqual(json.loads(received[-1][1]), {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})

    def test_bulk_lease_has_equal_aggregate_body_budget(self):
        stats = self.stats()
        stats.update({"requests": runner.profile_requests("continuous-bulk"), "write_errors": 0,
                      "cell_capacities": {"8192": 6, "32768": 2, "65536": 12, "262144": 1},
                      "idle_started": 1, "idle_completed": 0, "idle_cancelled": 1})
        stats["requests"].update({"GET /api/events/idle": 1, "POST /api/sync/bulk": 1, "GET /api/data/bulk": 1})
        stats["download_bytes"] += 4 * 65536
        stats["upload_bytes"] += 4 * 4096
        runner.validate_http_graph(stats, "continuous-bulk", "replace")
        runner.validate_http_graph(stats, "continuous-bulk-ready", "replace")
        runner.validate_http_graph(stats, "continuous-bulk-frames", "replace")
        for change in ("budget", "post", "legacy"):
            invalid = copy.deepcopy(stats)
            if change == "budget": invalid["download_bytes"] += 1
            elif change == "post": invalid["requests"]["POST /api/sync/bulk"] += 1
            else: invalid["requests"]["GET /api/data/download"] = 4
            with self.assertRaises(RuntimeError):
                runner.validate_http_graph(invalid, "continuous-bulk", "replace")

    def test_combined_activity_uses_post_response_capacity(self):
        stats = self.stats()
        stats.update({"requests": runner.profile_requests("continuous-sync"), "write_errors": 0,
                      "cell_capacities": {"8192": 6, "32768": 2, "65536": 12},
                      "idle_started": 1, "idle_completed": 0, "idle_cancelled": 1})
        stats["requests"]["GET /api/events/idle"] = 1
        for state, up, down in (("interactive", 4096, 8192), ("download", 4096, 65536), ("upload", 131072, 8192), ("mixed", 131072, 65536)):
            stats["requests"]["POST /api/exchange/" + state] = 4
            stats["upload_bytes"] += 4 * up
            stats["download_bytes"] += 4 * down
            stats["cell_capacities"][str(down)] += 4
        runner.validate_http_graph(stats, "continuous-sync", "replace")
        short = copy.deepcopy(stats)
        short["requests"]["POST /api/exchange/download"] -= 2
        short["download_bytes"] -= 2 * 65536
        short["upload_bytes"] -= 2 * 4096
        short["cell_capacities"]["65536"] -= 2
        runner.validate_http_graph(short, "continuous-sync2", "replace")
        with self.assertRaises(RuntimeError):
            runner.validate_http_graph(short, "continuous-sync", "replace")
        for method in ("GET /api/data/download", "POST /api/exchange/unknown"):
            changed = copy.deepcopy(stats)
            changed["requests"][method] = 4
            with self.assertRaises(RuntimeError):
                runner.validate_http_graph(changed, "continuous-sync", "replace")
        stats["requests"]["POST /api/exchange/download"] -= 1
        with self.assertRaises(RuntimeError):
            runner.validate_http_graph(stats, "continuous-sync", "replace")

    def test_frozen_budgets(self):
        down = {"v1": 1671168, "duplex-v1": 1671168, "compact": 884736,
                "compact-sync": 884736, "compact-sync20": 1146880,
                "compact-fast20": 1146880, "staged": 770048,
                "staged-fast": 770048, "staged-fast20": 901120,
                "staged-stream20": 901120, "staged-commit20": 905216, "continuous-v1": 901120, "continuous-sync": 901120, "continuous-sync2": 901120}

        down["continuous-bulk"] = 901120
        down["continuous-bulk-ready"] = 901120
        down["continuous-bulk-frames"] = 901120
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
