#!/usr/bin/env python3

import copy
import importlib.util
import json
import math
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "h2_request_lifecycle_summary", HERE / "h2_request_lifecycle_summary.py"
)
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)
TOKEN = "0123456789abcdef0123456789abcdef"


def record(uri, start, duration, *, port=45500, method="GET", status=200, size=494):
    return {
        "logger": "http.log.access.log0",
        "ts": start + duration,
        "duration": duration,
        "size": size,
        "status": status,
        "request": {
            "host": f"localhost:{port}",
            "method": method,
            "proto": "HTTP/2.0",
            "uri": uri,
            "headers": {"Authorization": ["secret-sentinel"]},
        },
        "user_id": "private-user-sentinel",
    }


def page(start=1000, port=45500):
    entries = [
        record(
            f"/camouflage/index.html?scenario=browser_page&completion={TOKEN}",
            start,
            0.002,
            port=port,
        ),
        record("/camouflage/style.css", start + 0.032, 0.004, port=port, size=65536),
        record("/camouflage/app.js", start + 0.033, 0.010, port=port, size=131072),
        record(
            "/camouflage/resource?size=65536",
            start + 0.034,
            0.005,
            port=port,
            size=65536,
        ),
        record(
            "/camouflage/resource?size=131072",
            start + 0.035,
            0.005,
            port=port,
            size=131072,
        ),
        record(
            "/camouflage/resource?size=262144",
            start + 0.036,
            0.006,
            port=port,
            size=262144,
        ),
        record("/camouflage/api", start + 0.037, 0.004, port=port, size=34),
        record(
            f"/camouflage/complete?token={TOKEN}",
            start + 0.060,
            0.001,
            port=port,
            method="POST",
            status=204,
            size=0,
        ),
    ]
    return sorted(entries, key=lambda entry: entry["ts"])


class H2RequestLifecycleTests(unittest.TestCase):
    def test_identity_matches_actual_superblock_schedule(self):
        spec = importlib.util.spec_from_file_location(
            "camouflage_superblocks", HERE / "camouflage_superblocks.py"
        )
        planner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(planner)
        rows = planner.schedule_rows(
            2026083074,
            "h2",
            1,
            ["browser_page"],
            arms=(
                "document-first-buffer-task-overlap",
                "document-first-buffer-http-connect",
            ),
        )
        for index, row in enumerate(rows, 1):
            SUMMARY.validate_identity(f"h2_s{index:06d}", row["experiment_block"])
        with self.assertRaises(ValueError):
            SUMMARY.validate_identity("h2_s000001", "h2_b000000")

    def summarize(self, outer, inner=None, role="firefox_a"):
        return SUMMARY.summarize(
            outer,
            inner or [],
            role=role,
            completion=TOKEN,
            proxy_port=45500,
            inner_port=45501,
        )

    def candidate(self):
        outer = [
            page()[0],
            record(
                "localhost:45501",
                1000.003,
                4.997,
                port=45501,
                method="CONNECT",
                size=600000,
            ),
        ]
        return outer, page(start=1000.008, port=45501)

    def test_direct_reference_uses_handler_start_not_log_order(self):
        result = self.summarize(page())
        self.assertEqual(result["intervals_ms"]["workload_root_to_stylesheet_ms"], 32)
        self.assertEqual(
            result["intervals_ms"]["workload_root_end_to_stylesheet_ms"], 30
        )
        self.assertEqual(
            result["intervals_ms"]["outer_root_to_workload_completion_ms"], 60
        )
        names = [event["event"] for event in result["events"]]
        self.assertLess(names.index("outer_script"), names.index("outer_api"))
        self.assertFalse(result["passive_feature_input"])

    def test_both_listener_timelines_include_real_connect_start(self):
        for role in ("socks", "http"):
            with self.subTest(role=role):
                result = self.summarize(*self.candidate(), role=role)
                self.assertEqual(result["intervals_ms"]["outer_root_to_connect_ms"], 3)
                self.assertEqual(result["intervals_ms"]["connect_to_inner_root_ms"], 5)
                self.assertEqual(
                    result["intervals_ms"]["outer_root_to_inner_root_ms"], 8
                )
                self.assertEqual(
                    result["intervals_ms"]["workload_root_to_first_asset_ms"], 32
                )
                connect = next(
                    item
                    for item in result["events"]
                    if item["event"] == "outer_connect"
                )
                self.assertEqual(set(connect), {"event", "request_start_ms"})

    def test_safe_result_has_no_request_identity_or_absolute_time(self):
        result = json.dumps(self.summarize(*self.candidate(), role="socks"))
        for secret in (
            TOKEN,
            "localhost",
            "45501",
            "Authorization",
            "secret-sentinel",
            "private-user-sentinel",
            "/camouflage",
            "1000",
        ):
            self.assertNotIn(secret, result)

    def test_missing_duplicate_and_foreign_navigation_fail_closed(self):
        cases = []
        missing = page()
        missing.pop(1)
        cases.append(missing)
        duplicate = page()
        duplicate.append(copy.deepcopy(duplicate[0]))
        cases.append(duplicate)
        foreign = page()
        foreign[0]["request"]["uri"] = foreign[0]["request"]["uri"].replace(
            TOKEN, "f" * 32
        )
        cases.append(foreign)
        extra = page()
        extra.append(record("/unrecognized", 1000.070, 0.001))
        cases.append(extra)
        for entries in cases:
            with self.subTest(entries=len(entries)):
                with self.assertRaises(ValueError):
                    self.summarize(entries)

    def test_protocol_method_status_and_fixture_shape_are_strict(self):
        for field, value in (
            ("proto", "HTTP/1.1"),
            ("method", "HEAD"),
            ("uri", "/camouflage/style.css?other=1"),
        ):
            entries = page()
            entries[1]["request"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.summarize(entries)
        entries = page()
        entries[1]["status"] = 304
        with self.assertRaises(ValueError):
            self.summarize(entries)
        entries = page()
        entries[0]["request"]["uri"] += "&document_size=65536"
        with self.assertRaises(ValueError):
            self.summarize(entries)

    def test_connect_count_and_target_are_strict(self):
        for mutation in ("missing", "duplicate", "wrong_target", "wrong_status"):
            outer, inner = self.candidate()
            if mutation == "missing":
                outer.pop()
            elif mutation == "duplicate":
                outer.append(copy.deepcopy(outer[-1]))
            elif mutation == "wrong_target":
                outer[-1]["request"]["host"] = "localhost:45502"
            else:
                outer[-1]["status"] = 403
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.summarize(outer, inner, role="socks")

    def test_invalid_timing_and_size_are_rejected(self):
        for key, value in (
            ("ts", math.nan),
            ("ts", math.inf),
            ("ts", True),
            ("ts", -1),
            ("duration", -0.01),
            ("duration", 2000),
            ("duration", "0.1"),
            ("size", -1),
            ("size", True),
        ):
            entries = page()
            entries[0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.summarize(entries)

    def test_log_slice_ignores_non_access_records_and_checks_offsets(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "access.jsonl"
            prefix = b"unrelated private prefix\n"
            payload = (
                "\n".join(
                    json.dumps(item)
                    for item in [{"logger": "tls", "msg": "unrelated"}, *page()]
                )
                + "\n"
            )
            path.write_bytes(prefix + payload.encode())
            self.assertEqual(len(SUMMARY.read_slice(path, len(prefix))), 8)
            for offset in (-1, path.stat().st_size + 1):
                with self.assertRaises(ValueError):
                    SUMMARY.read_slice(path, offset)
            with self.assertRaises(ValueError):
                SUMMARY.read_slice(path, 0)
            path.write_bytes(b"\xff\n")
            with self.assertRaises(ValueError):
                SUMMARY.read_slice(path, 0)

    def test_runner_keeps_timeline_separate_and_after_shutdown(self):
        runner = (HERE / "run-camouflage-suite.sh").read_text()
        self.assertIn("H2 request timing requires isolated gate/smoke", runner)
        self.assertIn(
            "H2 request timing requires the canonical unshaped fixture", runner
        )
        self.assertIn('mkdir -m 0700 "$safe_dir/h2-request-lifecycle"', runner)
        self.assertIn(
            "h2_request_timing_validated_participants -ne $session_counter", runner
        )
        candidate = runner[
            runner.index("run_naivefox_sample() {") : runner.index("scenario_csv=")
        ]
        self.assertLess(
            candidate.rindex('stop_pid "$naivefox_pid"'),
            candidate.index('record_h2_request_timing "$h2_timing_role"'),
        )
        helper = runner[
            runner.index("record_h2_request_timing() {") : runner.index(
                "validate_profile_role() {"
            )
        ]
        self.assertNotIn("feature_fragments", helper)

    def test_runner_rejects_out_of_scope_timing_requests_before_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(
                os.environ,
                NAIVEFOX_OBJDIR=temporary,
                NAIVEFOX_CAPTURE_ISOLATED_NETWORK="1",
            )
            base = [
                "bash",
                str(HERE / "run-camouflage-suite.sh"),
                "--h2-request-timing",
                "--mode",
                "gate",
                "--protocol",
                "h2",
                "--inner-transport",
                "https-h2",
                "--scenario",
                "browser_page",
            ]
            arms = [
                "--multi-arm-arms",
                "document-first-buffer-task-overlap,document-first-buffer-http-connect",
            ]
            for arguments in (
                base,
                base + ["--multi-arm-arms", "off,gate"],
                base + arms + ["--document-body-size", "65536"],
            ):
                result = subprocess.run(
                    arguments,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("H2 request timing requires", result.stderr)


if __name__ == "__main__":
    unittest.main()
