#!/usr/bin/env python3

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("matched_app_matrix", HERE / "run-matched-app-matrix.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def successful_records():
    manifest = json.loads((M.APP / "manifest.json").read_text())
    assets = M.expected_assets(M.APP)
    up, down = M.useful_totals(manifest)
    browser = {"manifest_sha256": M.MANIFEST_SHA, "uploaded_bytes": up, "downloaded_bytes": down,
               "assets": list(assets.values()), "app_sha256": assets["/assets/app.js"]["sha256"],
               "websocket": {"opened": 1, "closed": 1, "close_code": 1000, "clean": True,
                             "open_ms": 0, "close_ms": 5000, **M.CONTROL_COUNTS}, "stages": []}
    browser["consumer"] = {"navigation": {"decoded_body_size": 4096, "response_status": 200, "next_hop_protocol": "h2"},
        "resources": [{"path": path, "decoded_body_size": item["bytes"], "response_status": 200, "next_hop_protocol": "h2"} for path, item in assets.items()],
        "images": [{"path": f"/assets/image-{index}.svg", "complete": True, "decoded": True, "natural_width": 8, "natural_height": 8} for index in range(1, 5)],
        "stylesheet_loaded": True, "collected_ms": 1}
    specifications = {item["id"]: item for item in manifest["jobs"]}
    clock = 2001
    for spec in manifest["stages"]:
        if spec["name"] == "wake":
            clock += 2000
        start = clock
        jobs = []
        for identity in spec["job_ids"]:
            job = dict(specifications[identity])
            job.update(io_start_ms=clock, io_end_ms=clock + 1, verified_ms=clock + 2)
            jobs.append(job)
            clock += 3
        sent = sum(item["bytes"] for item in jobs if item["kind"] in ("upload", "echo"))
        received = sum(item["bytes"] for item in jobs if item["kind"] in ("download", "echo"))
        browser["stages"].append({"name": spec["name"], "jobs": jobs, "io_start_ms": start,
            "io_end_ms": max(item["io_end_ms"] for item in jobs), "verified_ms": max(item["verified_ms"] for item in jobs),
            "sent_bytes": sent, "received_bytes": received, "useful_bytes": sum(item["bytes"] for item in jobs)})
    backend_jobs = []
    for job in manifest["jobs"]:
        value = dict(job)
        value.update(verified=True, validated=job["bytes"] if job["kind"] in ("upload", "echo") else 0,
                     received=job["bytes"] if job["kind"] in ("upload", "echo") else 0,
                     sent=job["bytes"] if job["kind"] in ("download", "echo") else 0)
        backend_jobs.append(value)
    connection = {"normal_close": True, "close_code": 1000, "bootstrap_pairs": 20,
                  "parallel_batches": 1, "parallel_job_count": 4, "peak_jobs": 4,
                  "data_bytes_in": up, "expected_data_bytes_in": up, "data_bytes_out": down, "expected_data_bytes_out": down,
                  "data_messages_in": 21, "data_messages_out": 165, "control_messages_in": 190, "control_messages_out": 43,
                  "open_order": list(range(1, 12)), "asset_cookie_hash": "app", "jobs": backend_jobs}
    backend = {"manifest_sha256": M.MANIFEST_SHA, "ws_opened": 1, "ws_closed": 1, "normal_closes": 1,
               "bootstrap_completed": 1, "api_posts": 20, "api_gets": 20, "catalog_records": 1280,
               "rejected": 0, "asset_failures": 0, "connections": [connection],
               "asset_groups": {"app": {"responses": {path: {**value, "requests": 1, "completed": 1, "written_bytes": value["bytes"]}
                                                      for path, value in assets.items()}}}, "api": []}
    for index in range(40):
        backend["api"].append({"method": "POST" if index % 2 == 0 else "GET",
            "path": f"/app/api/bootstrap/{index // 2}", "asset_cookie_hash": "app",
            "request_bytes": 100 if index % 2 == 0 else 0, "request_sha256": "a" * 64,
            "response_bytes": 500, "response_sha256": "b" * 64})
    return browser, backend, manifest, assets


class MatchedApplicationTests(unittest.TestCase):
    def test_complete_active_application_is_required(self):
        browser, backend, manifest, assets = successful_records()
        result = M.validate_application(browser, backend, manifest, assets)
        self.assertEqual(result["jobs_verified"], 11)
        self.assertEqual(result["uploaded_bytes"], 1069056)
        self.assertEqual(result["downloaded_bytes"], 10506240)
        browser["uploaded_bytes"] = browser["downloaded_bytes"] = 0
        browser["stages"] = []
        backend["connections"][0]["jobs"] = []
        with self.assertRaisesRegex(RuntimeError, "complete active workload"):
            M.validate_application(browser, backend, manifest, assets)

    def test_wrong_bytes_hashes_parallelism_and_message_graph_fail(self):
        mutations = (
            lambda b, s: b.update(downloaded_bytes=b["downloaded_bytes"] - 1),
            lambda b, s: b.update(app_sha256="0" * 64),
            lambda b, s: s["connections"][0].update(peak_jobs=1),
            lambda b, s: s["connections"][0].update(data_messages_out=164),
            lambda b, s: s["connections"][0]["jobs"][0].update(sha256="0" * 64),
            lambda b, s: b["stages"][0]["jobs"][0].update(verified_ms=0),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                browser, backend, manifest, assets = successful_records()
                mutation(browser, backend)
                with self.assertRaises(RuntimeError):
                    M.validate_application(browser, backend, manifest, assets)

    def test_carrier_assets_cannot_substitute_for_application_assets(self):
        browser, backend, manifest, assets = successful_records()
        backend["asset_groups"]["carrier"] = backend["asset_groups"].pop("app")
        with self.assertRaisesRegex(RuntimeError, "carrier assets"):
            M.validate_application(browser, backend, manifest, assets)

    def test_idle_gap_and_normal_close_are_not_optional(self):
        browser, backend, manifest, assets = successful_records()
        browser["websocket"]["close_code"] = 1006
        with self.assertRaises(RuntimeError):
            M.validate_application(browser, backend, manifest, assets)
        browser, backend, manifest, assets = successful_records()
        browser["stages"][-1]["io_start_ms"] -= 1999
        with self.assertRaisesRegex(RuntimeError, "idle period"):
            M.validate_application(browser, backend, manifest, assets)

    def test_icmp_counts_outer_packet_only_and_reverses_quoted_direction(self):
        base = {"frame.number": "2", "frame.time_epoch": "10.2", "ip.len": "112;84", "ip.hdr_len": "20;20",
                "udp.srcport": "443", "udp.dstport": "50000", "tcp.srcport": "", "tcp.dstport": ""}
        feedback = M.icmp_feedback_event(base, 443, 10.0)
        self.assertEqual((feedback["wire_size"], feedback["direction"], feedback["flow"]), (112, 1, ""))
        original = {"wire_size": 84, "direction": -1, "flow": "quic:0"}
        summary = M.origin_wire_summary([original, feedback])
        self.assertEqual((summary["wire_bytes"], summary["packets"], summary["outer_flows"]), (196, 2, 1))
        reverse = dict(base, **{"udp.srcport": "", "udp.dstport": "", "tcp.srcport": "50000", "tcp.dstport": "443"})
        self.assertEqual(M.icmp_feedback_event(reverse, 443, 10.0)["direction"], -1)
        unrelated = dict(base, **{"udp.srcport": "444"})
        with self.assertRaises(RuntimeError):
            M.icmp_feedback_event(unrelated, 443, 10.0)

    def test_active_websocket_cannot_bypass_selected_native_listener(self):
        def owners(port):
            return [{100}] if port == 444 else [{200}]
        with mock.patch.object(M, "established_owners", side_effect=owners):
            proof = M.validate_routing_owners(False, 443, 444, 445, 100, {200, 201})
            self.assertTrue(proof["verified"])
        def bypass(port):
            return [{100}, {200}] if port == 444 else [{200}]
        with mock.patch.object(M, "established_owners", side_effect=bypass), self.assertRaisesRegex(RuntimeError, "bypassed"):
            M.validate_routing_owners(False, 443, 444, 445, 100, {200, 201})
        with mock.patch.object(M, "established_owners", return_value=[{200}]):
            self.assertTrue(M.validate_routing_owners(True, 443, 444, 0, 100, {200})["verified"])

    def test_tcp_ack_wraparound(self):
        self.assertTrue(M.ack_covers(3, 0xffffffff))
        self.assertFalse(M.ack_covers(0xfffffffe, 3))

    def test_complete_tcp_fin_payload_wrap_and_retransmission(self):
        def packet(frame, incoming=False, syn=False, fin=False, seq=0, ack=0, length=0):
            return {"tcp.stream": "0", "tcp.dstport": "42000" if incoming else "443",
                    "tcp.srcport": "443" if incoming else "42000", "tcp.flags.syn": str(int(syn)),
                    "tcp.flags.ack": str(int(not syn)), "tcp.flags.fin": str(int(fin)), "tcp.flags.reset": "0",
                    "tcp.seq_raw": str(seq), "tcp.ack_raw": str(ack), "tcp.len": str(length), "frame.number": str(frame)}
        rows = [packet(1, syn=True), packet(2, fin=True, seq=0xfffffffe, length=2),
                packet(3, incoming=True, fin=True, seq=100, ack=0),
                packet(4, fin=True, seq=0xfffffffe, length=2, ack=101),
                packet(5, incoming=True, seq=101, ack=1)]
        with mock.patch.object(M.features, "tshark_rows", return_value=rows) as tshark:
            self.assertEqual(M.validate_tcp_termination(Path("capture.pcapng"), 443),
                             {"tcp_flows": 1, "tcp_fin_completed": 1, "tcp_reset_completed": 0})
            self.assertIn("sll.pkttype==0", tshark.call_args.args[2])
            self.assertIn("!icmp && !icmpv6", tshark.call_args.args[2])
        rows[-1]["tcp.ack_raw"] = "0"
        with mock.patch.object(M.features, "tshark_rows", return_value=rows):
            with self.assertRaisesRegex(RuntimeError, "TCP FIN is not acknowledged"):
                M.validate_tcp_termination(Path("capture.pcapng"), 443)

    def test_nonce_cannot_hide_packet_arriving_after_producer_exit(self):
        class Clock:
            value = 0.0
            def time(self): return 1000 + self.value
            def monotonic(self): return self.value
            def sleep(self, seconds): self.value += seconds
        clock = Clock()
        last = [990.0]
        capture = mock.Mock(pcap=Path("unused"))
        calls = []
        def nonce():
            calls.append(clock.value)
            if len(calls) == 1:
                last[0] = clock.time()
        capture.observe_nonce.side_effect = nonce
        link = mock.Mock(profile="rtt40-20mbps")
        link.validate.return_value = {"profile": "rtt40-20mbps", "qdiscs": [
            {"kind": "netem", "backlog": 0, "qlen": 0}, {"kind": "netem", "backlog": 0, "qlen": 0}]}
        with mock.patch.object(M, "time", clock), mock.patch.object(M, "live_owned", return_value=[]), \
             mock.patch.object(M, "latest_outer_packet", side_effect=lambda *args: last[0]), \
             mock.patch.object(M, "validate_tcp_termination"), mock.patch.object(M, "write_json"):
            result = M.drain_complete(capture, link, 443, {}, Path("unused"))
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreaterEqual(result["drain_ms"] + 1e-6, 120)
        self.assertGreaterEqual(result["observed_quiet_ms"] + 1e-6, 120)

    def test_environment_marker_cannot_authorize_host_qdisc_changes(self):
        with mock.patch.dict(M.os.environ, {"NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED": "1"}),              mock.patch.object(M.os, "readlink", return_value="net:[same]"),              mock.patch.object(M, "run_quiet") as commands:
            with self.assertRaisesRegex(RuntimeError, "host network"):
                M.OuterLink(443, "rtt40-20mbps").install()
            commands.assert_not_called()
        link = M.OuterLink(443, "rtt40-20mbps")
        link.installed = True
        link.namespace = "net:[owned]"
        with mock.patch.object(M, "assert_isolated_namespace", return_value="net:[different]"),              mock.patch.object(M, "run_quiet") as commands:
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                link.close()
            commands.assert_not_called()

    def test_pending_shaper_queue_cannot_be_called_drained(self):
        snapshot = {"profile": "rtt40-20mbps", "qdiscs": [
            {"kind": "netem", "backlog": 512, "qlen": 1}, {"kind": "netem", "backlog": 0, "qlen": 0}]}
        self.assertFalse(M.queues_empty(snapshot))
        snapshot["qdiscs"][0]["backlog"] = snapshot["qdiscs"][0]["qlen"] = 0
        self.assertTrue(M.queues_empty(snapshot))


if __name__ == "__main__":
    unittest.main()
