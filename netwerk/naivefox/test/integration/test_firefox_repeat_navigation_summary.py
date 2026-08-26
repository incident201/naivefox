#!/usr/bin/env python3

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import firefox_repeat_navigation_summary as summary
import camouflage_browser_controller as browser_controller


class FirefoxRepeatNavigationSummaryTests(unittest.TestCase):
    @staticmethod
    def line(number, timestamp, message, process="Parent 1", filename="parent.log"):
        return {
            "file": Path(filename),
            "message": message,
            "number": number,
            "process": process,
            "time": timestamp,
        }

    def test_lifecycle_parser_maps_every_css_stage(self):
        root_uri = "https://localhost:443/camouflage/index.html?nav=" + "a" * 32
        css_uri = "https://localhost:443/camouflage/style.css?nav=" + "a" * 32
        parent = [
            self.line(
                1,
                100.01,
                f"nsHttpChannel::OnCacheEntryAvailable [this=aaa status=0] for {root_uri}",
            ),
            self.line(2, 100.10, "nsHttpChannel::Suspend [this=aaa]"),
            self.line(3, 100.20, "nsHttpChannel::ResumeInternal [this=aaa]"),
            self.line(
                4,
                100.60,
                f"HttpChannelParent RecvAsyncOpen [this=ccc uri={css_uri}, loadFlags=0]",
            ),
            self.line(
                14,
                100.56,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-alloc-enter necko=999 channelId=42 browserId=3",
            ),
            self.line(
                15,
                100.57,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-recv-enter actor=cd4 channelId=42 browserId=3",
            ),
            self.line(
                16,
                100.61,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-wait-start parent=ccc channelId=42",
            ),
            self.line(
                17,
                100.62,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-registrar-link-return parent=ccc channelId=42 ready=0",
            ),
            self.line(
                18,
                100.58,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=background-init-enter background=abc channelId=42",
            ),
            self.line(
                19,
                100.63,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=background-main-dispatch-run background=abc channelId=42",
            ),
            self.line(
                20,
                100.64,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-background-ready parent=ccc background=abc channelId=42",
            ),
            self.line(
                21,
                100.65,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=background-registrar-link-return background=abc channelId=42",
            ),
            self.line(
                22,
                100.66,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-wait-resolved parent=ccc channelId=42 status=success",
            ),
            self.line(
                23,
                100.67,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-try-invoke parent=ccc channelId=42 barrier=1 rv=0",
            ),
            self.line(5, 100.70, "HttpChannelParent::InvokeAsyncOpen [this=ccc rv=0]"),
            self.line(6, 100.70005, "nsHttpChannel::AsyncOpen [this=ddd]"),
            self.line(
                7,
                100.80,
                "nsHttpChannel::DispatchTransaction [this=ddd, aTransWithStickyConn=0]",
            ),
            self.line(8, 100.80005, "Creating nsHttpTransaction @eeee"),
            self.line(9, 100.80006, "nsHttpChannel ddd created nsHttpTransaction eefe"),
            self.line(
                10, 100.90, "nsHttpConnectionMgr::OnMsgNewTransaction [trans=eeee]"
            ),
            self.line(
                11,
                101.00,
                "nsHttpConnectionMgr::DispatchTransaction [ent=1 trans=eeee caps=0 isHttp3=1]",
            ),
            self.line(12, 101.10, "Http3Session::AddStream ffff atrans=eeee."),
            # Reuse after the CSS GET must not make the current mapping ambiguous.
            self.line(13, 101.30, "HttpChannelParent::InvokeAsyncOpen [this=ccc rv=0]"),
        ]
        child = [
            self.line(
                1,
                100.15,
                "Creating HttpChannelChild @bbb",
                "Web Content 2",
                "child.log",
            ),
            self.line(2, 100.16, f"uri={root_uri}", "Web Content 2", "child.log"),
            self.line(
                3,
                100.30,
                "HttpChannelChild::DoOnDataAvailable [this=bbb, request=abcd]",
                "Web Content 2: HTML5 Parser",
                "child.log",
            ),
            self.line(
                4,
                100.40,
                "css::Loader::LoadSheet(aURL, aObserver) api call",
                "Web Content 2",
                "child.log",
            ),
            self.line(
                5,
                100.400002,
                f"  Non-document sheet uri: '{css_uri}'",
                "Web Content 2",
                "child.log",
            ),
            self.line(
                6,
                100.50,
                f"HttpChannelChild::AsyncOpen [this=123 uri={css_uri}]",
                "Web Content 2",
                "child.log",
            ),
            self.line(
                7,
                100.51,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=child-openargs-ready child=123 channelId=42 browserId=3 contentWindowId=4",
                "Web Content 2",
                "child.log",
            ),
            self.line(
                8,
                100.52,
                "NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=child-send-return child=123 channelId=42 sent=1",
                "Web Content 2",
                "child.log",
            ),
        ]
        result = summary.parse_navigation_lifecycle(
            parent,
            child,
            root_uri,
            css_uri,
            {"time": 0.0},
            {"time": 1.2},
            100.0,
        )
        self.assertAlmostEqual(result["root_headers_to_css_get_ms"], 1200.0)
        self.assertAlmostEqual(result["root_suspend_to_resume_ms"], 100.0)
        self.assertAlmostEqual(result["parser_body_to_css_descriptor_ms"], 100.0)
        self.assertAlmostEqual(result["css_channel_async_open_to_dispatch_ms"], 99.95)
        self.assertAlmostEqual(
            result["css_child_async_open_to_openargs_ready_ms"], 10.0
        )
        self.assertAlmostEqual(result["css_openargs_ready_to_parent_alloc_ms"], 50.0)
        self.assertAlmostEqual(result["css_parent_wait_start_to_resolved_ms"], 50.0)
        self.assertAlmostEqual(result["css_h3_dispatch_to_add_stream_ms"], 100.0)
        self.assertAlmostEqual(result["css_add_stream_to_wire_get_ms"], 100.0)

    def test_response_mapper_reserves_already_mapped_companion_streams(self):
        separator = "\x1f"
        fieldnames = [
            "frame.number",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.stream.stream_id",
            "http3.headers.status",
            "http3.header.header.name",
            "http3.headers.header.value",
        ]
        rows = [
            [
                "1",
                "443",
                "50000",
                "7",
                "8",
                "200",
                f":status{separator}content-length",
                f"200{separator}20",
            ],
            [
                "2",
                "443",
                "50000",
                "7",
                f"4{separator}8",
                "200",
                f":status{separator}content-length",
                f"200{separator}10",
            ],
            [
                "3",
                "443",
                "50000",
                "7",
                f"12{separator}8",
                "200",
                f":status{separator}content-length",
                f"200{separator}30",
            ],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "responses.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(fieldnames)
                writer.writerows(rows)
            mapped = summary.response_header_blocks(path, 443)
        self.assertEqual([block["stream"] for block in mapped], ["8", "4", "12"])
        self.assertEqual(
            [dict(block["headers"])["content-length"] for block in mapped],
            ["20", "10", "30"],
        )

    def test_request_mapper_reserves_fragmented_stream_for_next_frame(self):
        separator = "~"
        fieldnames = [
            "frame.number",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.stream.stream_id",
            "http3.headers.method",
            "http3.header.header.name",
            "http3.headers.header.value",
        ]
        double_names = separator.join((":method", ":path", ":method", ":path"))
        rows = [
            [
                "700",
                "50000",
                "443",
                "7",
                separator.join(("48", "52", "56")),
                separator.join(("GET", "GET")),
                double_names,
                separator.join(("GET", "/first", "GET", "/second")),
            ],
            [
                "701",
                "50000",
                "443",
                "7",
                separator.join(("56", "60")),
                separator.join(("GET", "GET")),
                double_names,
                separator.join(("GET", "/third", "GET", "/fourth")),
            ],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.tsv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream, delimiter="\t", quoting=csv.QUOTE_NONE)
                writer.writerow(fieldnames)
                writer.writerows(rows)
            mapped = summary.private_header_blocks(
                path,
                ":method",
                "http3.headers.header.value",
                "http3.headers.method",
                443,
            )
        self.assertEqual(
            [block["stream"] for block in mapped], ["48", "52", "56", "60"]
        )
        self.assertEqual(
            [dict(block["headers"])[":path"] for block in mapped],
            ["/first", "/second", "/third", "/fourth"],
        )

    def test_content_length_only_accepts_the_known_quoted_etag_csv_shape(self):
        block = {
            "headers": (
                (":status", "200"),
                ("etag", 'fixture-etag"'),
                ("content-length", '65536"'),
            )
        }
        self.assertEqual(summary.response_content_length(block), 65536)
        block["headers"] = (
            (":status", "200"),
            ("content-length", '65536"'),
        )
        with self.assertRaisesRegex(ValueError, "invalid Content-Length"):
            summary.response_content_length(block)

    def test_identity_requires_same_process_session_and_tab_not_context_id(self):
        first = {
            "browser_pid": 10,
            "browsing_context_id": 13,
            "content_pid": 20,
            "current_window_handle": "tab-1",
            "webdriver_session_id": "session-1",
            "window_handles": ["tab-1"],
        }
        second = {**first, "browsing_context_id": 14}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            path.write_text(
                json.dumps({"navigation_1": first, "navigation_2": second}),
                encoding="utf-8",
            )
            result = summary.validate_browser_identity(path)
        self.assertTrue(result["browser_pid_stable"])
        self.assertTrue(result["content_process_stable"])
        self.assertTrue(result["single_tab_stable"])
        self.assertFalse(result["browsing_context_stable"])

    def test_identity_validates_an_arbitrary_navigation_series(self):
        identity = {
            "browser_pid": 10,
            "browsing_context_id": 13,
            "content_pid": 20,
            "current_window_handle": "tab-1",
            "webdriver_session_id": "session-1",
            "window_handles": ["tab-1"],
        }
        evidence = {
            f"navigation_{index}": {**identity, "browsing_context_id": index}
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            result = summary.validate_browser_identity(path, 8)
        self.assertTrue(result["content_process_stable"])
        self.assertFalse(result["browsing_context_stable"])

    def test_pearson_correlation_reports_direction_and_zero_variance(self):
        self.assertAlmostEqual(
            summary.pearson_correlation([1, 2, 3, 4], [2, 4, 6, 8]), 1.0
        )
        self.assertAlmostEqual(
            summary.pearson_correlation([1, 2, 3, 4], [8, 6, 4, 2]), -1.0
        )
        self.assertIsNone(summary.pearson_correlation([1, 1], [2, 3]))
        self.assertAlmostEqual(
            summary.population_covariance([1, 2, 3], [2, 4, 6]), 4 / 3
        )

    def test_browser_controller_runs_all_additional_navigations(self):
        class Driver:
            def __init__(self):
                self.urls = []

            def get(self, url):
                self.urls.append(url)

        class Controller(browser_controller.Controller):
            def __init__(self, args):
                super().__init__(args)
                self.driver = Driver()
                self.completions = []
                self.identity_index = 0

            def start(self):
                return "selenium"

            def navigate(self, backend):
                self.driver.get(self.args.url)

            def wait_for_completion(self, completion_file=None):
                self.completions.append(completion_file or self.args.completion_file)

            def wait_for_file(self, path):
                return

            def selenium_identity(self):
                self.identity_index += 1
                return {
                    "browser_pid": 1,
                    "browsing_context_id": self.identity_index,
                    "content_pid": 2,
                    "current_window_handle": "tab",
                    "webdriver_session_id": "session",
                    "window_handles": ["tab"],
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            additional = [
                (f"https://example.test/{index}", str(root / f"done-{index}"))
                for index in range(2, 9)
            ]
            args = SimpleNamespace(
                additional_navigation=additional,
                completion_file=str(root / "done-1"),
                done_file=str(root / "controller-done"),
                navigation_evidence_file=str(root / "identity.json"),
                navigate_file=str(root / "navigate"),
                ready_file=str(root / "ready.json"),
                second_completion_file=None,
                second_url=None,
                stop_file=str(root / "stop"),
                url="https://example.test/1",
                warmup_completion_file=None,
                warmup_url=None,
            )
            controller = Controller(args)
            controller.run()
            evidence = json.loads(
                Path(args.navigation_evidence_file).read_text(encoding="utf-8")
            )
        self.assertEqual(
            controller.driver.urls,
            ["https://example.test/1", *[item[0] for item in additional]],
        )
        self.assertEqual(len(evidence), 8)
        self.assertEqual(controller.completions[0], args.completion_file)


if __name__ == "__main__":
    unittest.main()
