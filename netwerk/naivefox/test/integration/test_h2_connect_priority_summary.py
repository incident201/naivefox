#!/usr/bin/env python3

import csv
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUMMARY = load_module("h2_connect_priority_summary", "h2_connect_priority_summary.py")
CONFIG = load_module("camouflage_naivefox_config", "camouflage_naivefox_config.py")


class ConnectPrioritySummaryTests(unittest.TestCase):
    def records(self):
        return {
            "firefox-proxied": {
                "signature": (("True",), ("13",), ("16",)),
                "priority_header": False,
            },
            "naivefox-default": {
                "signature": (("False",), (), ()),
                "priority_header": False,
            },
            "naivefox-urgent": {
                "signature": (("True",), ("13",), ("16",)),
                "priority_header": False,
            },
        }

    def test_native_match(self):
        records = self.records()
        self.assertEqual(SUMMARY.classify_mechanism(records), "native-match")

    def test_wire_null_is_a_valid_outcome(self):
        records = self.records()
        records["naivefox-default"]["signature"] = records["firefox-proxied"][
            "signature"
        ]
        self.assertEqual(SUMMARY.classify_mechanism(records), "wire-null")

    def test_native_mismatch_is_a_valid_outcome(self):
        records = self.records()
        records["naivefox-urgent"]["signature"] = (("True",), ("3",), ("16",))
        records["naivefox-urgent"]["priority_header"] = True
        self.assertEqual(SUMMARY.classify_mechanism(records), "native-mismatch")

    def test_header_only_mismatch_is_not_native_match(self):
        records = self.records()
        records["naivefox-urgent"]["priority_header"] = True
        self.assertEqual(SUMMARY.classify_mechanism(records), "native-mismatch")

    def test_equal_scheduling_with_different_headers_is_not_wire_null(self):
        records = self.records()
        records["naivefox-default"]["signature"] = records["firefox-proxied"][
            "signature"
        ]
        records["naivefox-default"]["priority_header"] = True
        self.assertEqual(SUMMARY.classify_mechanism(records), "native-mismatch")

    def test_empty_scheduling_evidence_is_rejected(self):
        records = self.records()
        records["naivefox-urgent"]["signature"] = ((), (), ())
        with self.assertRaisesRegex(ValueError, "no CONNECT scheduling evidence"):
            SUMMARY.classify_mechanism(records)

    def test_diagnostic_config_is_explicit_and_off_by_default(self):
        plain = CONFIG.build_config("off", "h2", 1080, 4433, "user", "pass")
        urgent = CONFIG.build_config("off", "h2", 1080, 4433, "user", "pass", True)
        self.assertNotIn("diagnostic-first-socks-tunnel-urgent-start", plain)
        self.assertIs(urgent["diagnostic-first-socks-tunnel-urgent-start"], True)

    def write_priority_extract(self, root, row):
        fieldnames = (
            "frame.number",
            "tcp.srcport",
            "tcp.dstport",
            "tcp.stream",
            "http2.type",
            "http2.streamid",
            "http2.headers.method",
            "http2.flags.priority",
            "http2.stream_dependency",
            "http2.headers.weight_real",
        )
        path = Path(root) / "naivefox-urgent-connect-priority.csv"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)

    def priority_row(self):
        return {
            "frame.number": "10",
            "tcp.srcport": "50000",
            "tcp.dstport": "4433",
            "tcp.stream": "0",
            "http2.type": "1",
            "http2.streamid": "1",
            "http2.headers.method": "CONNECT",
            "http2.flags.priority": "True",
            "http2.stream_dependency": "0",
            "http2.headers.weight_real": "16",
        }

    def test_priority_signature_accepts_one_exact_headers_occurrence(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_priority_extract(root, self.priority_row())
            signature = SUMMARY.read_priority_signature(
                Path(root),
                "naivefox-urgent",
                "4433",
                {"frame": 10, "stream": "1"},
            )
        self.assertEqual(signature, (("True",), ("0",), ("16",)))

    def test_priority_signature_rejects_coalesced_extra_headers(self):
        row = self.priority_row()
        separator = "\x1f"
        row["http2.type"] = separator.join(("1", "1"))
        row["http2.streamid"] = separator.join(("1", "3"))
        row["http2.headers.method"] = separator.join(("CONNECT", "GET"))
        with tempfile.TemporaryDirectory() as root:
            self.write_priority_extract(root, row)
            with self.assertRaisesRegex(ValueError, "method mapping is ambiguous"):
                SUMMARY.read_priority_signature(
                    Path(root),
                    "naivefox-urgent",
                    "4433",
                    {"frame": 10, "stream": "1"},
                )

    def test_priority_signature_rejects_coalesced_priority_frame(self):
        row = self.priority_row()
        separator = "\x1f"
        row["http2.type"] = separator.join(("1", "2"))
        row["http2.streamid"] = separator.join(("1", "1"))
        with tempfile.TemporaryDirectory() as root:
            self.write_priority_extract(root, row)
            with self.assertRaisesRegex(ValueError, "coalesced PRIORITY"):
                SUMMARY.read_priority_signature(
                    Path(root),
                    "naivefox-urgent",
                    "4433",
                    {"frame": 10, "stream": "1"},
                )

    def write_product_logs(self, root, urgent_lines):
        Path(root, "naivefox-default-naivefox.log").write_text(
            "Outer protocol: h2\n", encoding="utf-8"
        )
        Path(root, "naivefox-urgent-naivefox.log").write_text(
            "\n".join(urgent_lines) + "\n", encoding="utf-8"
        )

    def urgent_marker(self, connection="1", incremental="1", protocol="h2"):
        return (
            "[0824/140242.185605:INFO:naivefox] "
            f"Connection {connection} "
            "diagnostic-first-socks-tunnel-urgent-start "
            f"applied=1 incremental={incremental} protocol={protocol}"
        )

    def test_product_marker_accepts_first_fresh_h2_tunnel(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_product_logs(
                root,
                [
                    self.urgent_marker(),
                    "Outer protocol: h2",
                ],
            )
            self.assertEqual(
                SUMMARY.validate_product_markers(Path(root)),
                {"naivefox-default": True, "naivefox-urgent": True},
            )

    def test_product_marker_rejects_missing_urgent_marker(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_product_logs(root, ["Outer protocol: h2"])
            with self.assertRaisesRegex(ValueError, "exactly one"):
                SUMMARY.validate_product_markers(Path(root))

    def test_product_marker_rejects_wrong_connection(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_product_logs(
                root,
                [
                    self.urgent_marker(connection="2"),
                    "Outer protocol: h2",
                ],
            )
            with self.assertRaisesRegex(ValueError, "Connection 1"):
                SUMMARY.validate_product_markers(Path(root))

    def test_product_marker_rejects_marker_after_established(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_product_logs(
                root,
                [
                    "Outer protocol: h2",
                    self.urgent_marker(incremental="0"),
                ],
            )
            with self.assertRaisesRegex(ValueError, "follows"):
                SUMMARY.validate_product_markers(Path(root))

    def test_product_marker_rejects_noncanonical_log_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_product_logs(
                root,
                [
                    "arbitrary-prefix "
                    "Connection 1 diagnostic-first-socks-tunnel-urgent-start "
                    "applied=1 incremental=1 protocol=h2",
                    "Outer protocol: h2",
                ],
            )
            with self.assertRaisesRegex(ValueError, "malformed"):
                SUMMARY.validate_product_markers(Path(root))


class ConnectPriorityHarnessTests(unittest.TestCase):
    def test_controller_uses_exact_target_privileged_proxy_info(self):
        with open(
            os.path.join(HERE, "proxied_firefox_controller.py"), encoding="utf-8"
        ) as source:
            controller = source.read()
        self.assertIn("nsIProtocolProxyChannelFilter", controller)
        self.assertIn("pps.newProxyInfo(", controller)
        self.assertIn('"https", proxyHost, proxyPort, authorization', controller)
        self.assertIn("uri.asciiHost === targetHost", controller)
        self.assertIn("uri.port === targetPort", controller)
        self.assertIn("--allow-system-access", controller)
        self.assertNotIn(
            'options.add_argument("-remote-allow-system-access")', controller
        )
        self.assertNotIn('parser.add_argument("--proxy-user"', controller)
        self.assertNotIn('parser.add_argument("--proxy-pass"', controller)

    def test_runner_isolated_and_fail_closed(self):
        with open(
            os.path.join(HERE, "run-h2-connect-priority-comparison.sh"),
            encoding="utf-8",
        ) as source:
            runner = source.read()
        self.assertIn("run-camouflage-isolated-network.sh", runner)
        self.assertIn("monitor-network-mutations.py", runner)
        self.assertIn("camouflage_capture_health.py", runner)
        self.assertIn("network route/address/link mutation invalidated", runner)
        self.assertIn("firefox-proxied naivefox-default naivefox-urgent", runner)
        self.assertIn("--diagnostic-first-socks-tunnel-urgent-start", runner)
        self.assertIn("h2_connect_priority_summary.py", runner)
        self.assertIn("assert_urgent_marker_absent", runner)
        self.assertIn("validate_urgent_marker_after_workload", runner)
        self.assertIn(":INFO:naivefox\\] Connection 1 diagnostic-first", runner)
        self.assertIn("capture_source_state_sha256", runner)
        self.assertIn("private material reached safe", runner)
        self.assertNotIn("Caddyfile", runner)


if __name__ == "__main__":
    unittest.main()
