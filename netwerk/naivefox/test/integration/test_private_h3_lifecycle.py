#!/usr/bin/env python3

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("analyze-private-h3-lifecycle.py")
SPEC = importlib.util.spec_from_file_location("private_h3_lifecycle", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(frame, connection, *, src="40000", dst="45500", **fields):
    result = {field: "" for field in MODULE.FIELDS}
    result.update(
        {
            "frame.number": str(frame),
            "frame.time_relative": f"{frame / 1000:.6f}",
            "udp.srcport": src,
            "udp.dstport": dst,
            "quic.connection.number": str(connection),
        }
    )
    result.update(fields)
    return result


class PrivateH3LifecycleTests(unittest.TestCase):
    def test_no_shutdown_signal_before_second_clienthello(self):
        rows = [
            row(1, 7, **{"tls.handshake.type": "1"}),
            row(9, 7, **{"http3.frame_type": "0x1"}),
            row(20, 8, **{"tls.handshake.type": "1"}),
        ]
        summary = MODULE.summarize_rows(rows, 45500)
        self.assertFalse(summary["outer_shutdown_signal_before_second_clienthello"])
        self.assertEqual(summary["event_counts"]["H3_GOAWAY"], 0)
        self.assertEqual(summary["second_clienthello_frame"], 20)

    def test_reports_goaway_close_and_stream_abort_before_boundary(self):
        rows = [
            row(1, 7, **{"tls.handshake.type": "1"}),
            row(8, 7, **{"http3.frame_type": "0x4"}),
            row(
                10,
                7,
                src="45500",
                dst="40000",
                **{"http3.frame_type": "0x7"},
            ),
            row(
                11,
                7,
                **{
                    "quic.rsts.stream_id": "12",
                    "quic.rsts.application_error_code": "268",
                    "quic.ss.stream_id": "16",
                    "quic.ss.application_error_code": "268",
                },
            ),
            row(12, 7, **{"quic.cc.error_code.app": "256"}),
            row(20, 8, **{"tls.handshake.type": "1"}),
        ]
        summary = MODULE.summarize_rows(rows, 45500)
        self.assertTrue(summary["outer_shutdown_signal_before_second_clienthello"])
        self.assertTrue(summary["stream_abort_signal_before_second_clienthello"])
        self.assertEqual(summary["event_counts"]["H3_GOAWAY"], 1)
        self.assertEqual(summary["event_counts"]["CONNECTION_CLOSE"], 1)
        self.assertEqual(summary["events"][0]["direction"], "proxy_to_client")

    def test_retransmitted_clienthello_is_not_a_second_connection(self):
        rows = [
            row(1, 7, **{"tls.handshake.type": "1"}),
            row(2, 7, **{"tls.handshake.type": "1"}),
            row(8, 7, **{"http3.frame_type": "0x1"}),
        ]
        with self.assertRaisesRegex(ValueError, "fewer than two"):
            MODULE.summarize_rows(rows, 45500)

    def test_refuses_to_claim_absence_without_decrypted_h3(self):
        rows = [
            row(1, 7, **{"tls.handshake.type": "1"}),
            row(20, 8, **{"tls.handshake.type": "1"}),
        ]
        with self.assertRaisesRegex(ValueError, "absence of GOAWAY"):
            MODULE.summarize_rows(rows, 45500)


if __name__ == "__main__":
    unittest.main()
