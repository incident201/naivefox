#!/usr/bin/env python3

import importlib.util
import os
import socket
import tempfile
import time
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("hybrid_matrix", HERE / "carrier_capture.py")
MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MATRIX)


class HybridMatrixTests(unittest.TestCase):
    def test_observer_merges_both_transport_families_and_distinguishes_ids(self):
        tcp = [{"time": .1, "frame": 2, "flow": "0", "wire_size": 60, "direction": 1},
               {"time": .4, "frame": 4, "flow": "0", "wire_size": 1500, "direction": -1}]
        quic = [{"time": .05, "frame": 1, "flow": "0", "wire_size": 1280, "direction": 1},
                {"time": .2, "frame": 3, "flow": "0", "wire_size": 1200, "direction": -1}]
        events = MATRIX.merge_outer_events(tcp, quic)
        self.assertEqual([item["frame"] for item in events], [1, 2, 3, 4])
        self.assertEqual(MATRIX.wire_summary(events), {"wire_bytes": 4040, "packets": 4,
                         "client_wire_bytes": 1340, "server_wire_bytes": 2700, "outer_flows": 2})
        self.assertEqual(tcp[0]["flow"], "0")
        self.assertEqual(quic[0]["flow"], "0")

    def test_coalesced_quic_ids_do_not_create_a_phantom_connection(self):
        quic = [{"time": .1, "frame": 1, "flow": "0;0", "wire_size": 1280, "direction": 1},
                {"time": .2, "frame": 2, "flow": "0", "wire_size": 1200, "direction": -1}]
        self.assertEqual(MATRIX.wire_summary(MATRIX.merge_outer_events([], quic))["outer_flows"], 1)

    def test_passive_observer_preserves_scenario_and_late_websocket_traffic(self):
        def event(time, frame, flow, size, direction, syn=False, packet_types=()):
            return {"time": time, "frame": frame, "flow": flow, "wire_size": size,
                    "transport_size": size - 40, "direction": direction, "syn": syn, "ack": False,
                    "fin": False, "rst": False, "retransmission": False, "out_of_order": False,
                    "lost_segment": False, "packet_types": packet_types}
        tcp = [event(.3, 3, "0", 60, 1, True), event(.4, 4, "0", 1500, -1)]
        quic = [event(.1, 1, "0", 1280, 1, packet_types=("0",)), event(.2, 2, "0", 1200, -1)]
        merged = MATRIX.merge_outer_events(tcp, quic)
        row = {"scenario": "matched_application", "label": "naivefox",
               "naivefox_arm": "native-no-connect-socks", "experiment_block": "block-0"}
        with mock.patch.object(MATRIX, "outer_events", return_value=(merged, tcp, quic, [])), \
             mock.patch.object(MATRIX.features, "extract_handshake"), \
             mock.patch.object(MATRIX.features, "extract_transport_parameters"), \
             mock.patch.object(MATRIX.features, "add_h2_features"), \
             mock.patch.object(MATRIX.features, "add_h3_features"):
            document, wire = MATRIX.passive_document(Path("unused.pcapng"), 443, "h3", row, "sample")
        self.assertEqual(wire["wire_bytes"], 4040)
        self.assertEqual(document["features"]["whole_packet_count"], 4)
        self.assertEqual(document["features"]["whole_server_wire_bytes"], 2700)
        self.assertEqual(document["features"]["lifecycle_connection_count"], 2)
        self.assertEqual(document["scenario"], "matched_application")

    def test_late_websocket_cannot_change_early_views(self):
        analysis = MATRIX.module("hybrid_invariance_analysis", "analyze-camouflage-arms.py")
        syn_row = dict.fromkeys(("tcp.options.mss_val", "tcp.options.wscale.shift", "tcp.window_size_value",
                                "tcp.options", "tcp.options.timestamp.tsval", "tcp.options.tfo.request",
                                "tcp.options.tfo.cookie", "tcp.flags.ece", "tcp.flags.cwr"), "")
        def event(index, flow, syn=False):
            return {"time": index / 1000, "frame": index, "flow": flow, "wire_size": 1000,
                    "transport_size": 960, "direction": 1 if syn else -1, "syn": syn, "ack": False,
                    "fin": False, "rst": False, "retransmission": False, "out_of_order": False,
                    "lost_segment": False, "packet_types": ["0"] if index == 1 else [],
                    "versions": ["0x00000001"], "dcil": ["8"], "scil": ["8"], "row": syn_row}
        for protocol in ("h2", "h3"):
            documents = []
            for variation in (1, 2):
                startup = [event(index, "0", index == 1) for index in range(1, 34)]
                late = [event(40, "1", True), event(41, "1")]
                if variation == 2:
                    late += [event(42, "2", True), event(43, "2")]
                tcp, quic = (startup + late, []) if protocol == "h2" else (late, startup)
                records = [{"time": .004, "flow": "0", "frame": 4, "direction": -1, "length": 900}] if protocol == "h2" else []
                records += [{"time": .041, "flow": "1", "frame": 41, "direction": -1, "length": 100 * variation}] * variation
                def handshake(pcap, family, port, output, flow_filter=None):
                    start_filter = ("tcp.stream" if protocol == "h2" else "quic.connection.number") + "==0"
                    stable = family == protocol and flow_filter == start_filter
                    output.update({"tls_client_hello_count": 1 if stable else variation,
                                   "tls_cipher_stable" if stable else f"tls_cipher_late_{variation}": 1})
                row = {"scenario": "browser_page", "label": "naivefox",
                       "naivefox_arm": "native-no-connect-socks", "experiment_block": "block"}
                with mock.patch.object(MATRIX, "outer_events", return_value=(MATRIX.merge_outer_events(tcp, quic), tcp, quic, records)),                      mock.patch.object(MATRIX.features, "extract_handshake", side_effect=handshake),                      mock.patch.object(MATRIX.features, "extract_transport_parameters"):
                    document, _ = MATRIX.passive_document(Path("unused"), 443, protocol, row, "sample")
                documents.append(document["features"])
            names = sorted(set(documents[0]) | set(documents[1]))
            self.assertTrue(all(name.startswith(analysis.ANALYSIS.FEATURE_PREFIXES) for name in names))
            for view in ("initial_packets_16", "packets_17_32"):
                selected = MATRIX.matrix_view_feature_names(names, view)
                self.assertTrue(selected)
                self.assertEqual({name: documents[0].get(name, 0) for name in selected},
                                 {name: documents[1].get(name, 0) for name in selected}, (protocol, view))
            self.assertNotEqual(documents[0], documents[1])

    def test_strict_windows_ignore_future_startup_records_and_quic_phases(self):
        first = {f"packet_{index:03d}_wire_size_signed": index for index in range(1, 49)}
        first.update({"initial_16_packet_count": 16, "initial_32_packet_count": 32,
                      "initial_250ms_packet_count": 32, "tls_record_001_signed_length": 100,
                      "tls_record_017_signed_length": 200, "tls_client_hello_count": 1,
                      "quic_phase_position_17_c_handshake": 1, "quic_phase_position_24_s_short": 1})
        second = dict(first)
        second.update({"tls_record_001_signed_length": 1500, "tls_record_017_signed_length": 1200,
                       "quic_phase_position_17_c_handshake": 0, "quic_phase_position_24_s_short": 0,
                       "tls_client_hello_count": 2, "packet_040_wire_size_signed": 500})
        for view in MATRIX.VIEWS[:-1]:
            names = MATRIX.matrix_view_feature_names(first, view)
            self.assertTrue(names)
            self.assertEqual({name: first[name] for name in names}, {name: second[name] for name in names})
        self.assertNotEqual(first, second)

    @unittest.skipUnless(os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED") == "1"
                         and os.environ.get("NAIVEFOX_CAPTURE_PACKET_TEST_ROOT"), "requires isolated capture fixture")
    def test_actual_capture_canary_never_enters_origin_features(self):
        root = Path(os.environ["NAIVEFOX_CAPTURE_PACKET_TEST_ROOT"])
        with tempfile.TemporaryDirectory(prefix="canary-", dir=root) as name, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            port = receiver.getsockname()[1]
            capture = MATRIX.Capture(Path(name), "test", port)
            try:
                before = time.time()
                receiver.sendto(b"x" * 400, receiver.getsockname())
                receiver.recv(1000)
                after = time.time()
            finally:
                capture.stop()
            markers = MATRIX.features.tshark_rows(str(capture.pcap), [], f"udp.port=={capture.marker_port}", ["frame.number"])
            events, _, _, _ = MATRIX.outer_events(capture.pcap, port)
            self.assertGreaterEqual(len(markers), 2)
            self.assertEqual(len(events), 1)
            self.assertEqual(MATRIX.wire_summary(events)["wire_bytes"], 428)
            sliced = Path(name) / "window.pcapng"
            MATRIX.subprocess.run(["editcap", "-A", f"{before:.9f}", "-B", f"{after:.9f}", str(capture.pcap), str(sliced)], check=True, capture_output=True)
            self.assertEqual(len(MATRIX.outer_events(sliced, port)[0]), 1)
            self.assertEqual(MATRIX.features.tshark_rows(str(sliced), [], f"udp.port=={capture.marker_port}", ["frame.number"]), [])

    def test_speed_loss_is_not_time_growth(self):
        values = MATRIX.penalties(10, 20, 100, 125)
        self.assertEqual(values["effective_rate_loss_percent"], 50)
        self.assertEqual(values["completion_time_increase_percent"], 100)
        self.assertEqual(values["extra_outer_traffic_percent"], 25)
        self.assertLess(MATRIX.penalties(20, 10, 125, 100)["effective_rate_loss_percent"], 0)

    def test_existing_analyzer_accepts_complete_native_superblocks(self):
        analysis = MATRIX.module("hybrid_analysis_test", "analyze-camouflage-arms.py")
        analysis.SUPERBLOCKS.SUPPORTED_ARMS += MATRIX.ARMS
        analysis.ANALYSIS.view_feature_names = MATRIX.matrix_view_feature_names
        schedule = analysis.SUPERBLOCKS.schedule_rows(7, "h2", 2, ["browser_page"], MATRIX.ARMS)
        names = ["packet_001_wire_size_signed", "packet_017_wire_size_signed", "whole_server_wire_bytes"]
        rows = []
        for index, row in enumerate(schedule):
            value = 1 if row["label"] == "firefox_a" else 2 if row["label"] == "firefox_b" else 3
            rows.append({**row, "session_id": f"sample-{index}", "features": dict.fromkeys(names, value)})
        report = analysis.build_report(SimpleNamespace(mode="gate", seed=7, bootstrap=100,
                        permutations=99, min_blocks=30, views=("initial_packets_16", "packets_17_32", "whole")), rows, names)
        self.assertEqual(set(report["arms"]), set(MATRIX.ARMS))
        self.assertTrue(report["screening_only"])
        self.assertEqual(set(report["protocols"]["h2"]["views"]["whole"]["arms"]), set(MATRIX.ARMS))

    def test_invalid_denominators_fail_closed(self):
        for values in ((0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 0, 1)):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                MATRIX.penalties(*values)


if __name__ == "__main__":
    unittest.main()
