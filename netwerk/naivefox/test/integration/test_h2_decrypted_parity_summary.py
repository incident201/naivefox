import csv
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "h2_decrypted_parity_summary", HERE / "h2_decrypted_parity_summary.py"
)
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


class H2DecryptedParitySummaryTests(unittest.TestCase):
    def write_csv(self, root, cohort, suffix, fields, rows):
        path = Path(root) / f"{cohort}-{suffix}.csv"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def transport_row(stream="0"):
        return {
            "frame.number": "1",
            "frame.time_relative": "0.001",
            "tcp.srcport": "55000",
            "tcp.dstport": "4433",
            "tcp.stream": stream,
        }

    @staticmethod
    def frame(frame, direction, stream_id, frame_type, flags, tcp_stream="0"):
        client = direction == "client"
        return {
            "frame.number": str(frame),
            "frame.time_relative": str(frame / 1000),
            "tcp.srcport": "55000" if client else "4433",
            "tcp.dstport": "4433" if client else "55000",
            "tcp.stream": tcp_stream,
            "http2.type": str(frame_type),
            "http2.streamid": str(stream_id),
            "http2.flags": str(flags),
        }

    @staticmethod
    def header(frame, direction, stream_id, method="", status="", names="", types="1"):
        client = direction == "client"
        return {
            "frame.number": str(frame),
            "frame.time_relative": str(frame / 1000),
            "tcp.srcport": "55000" if client else "4433",
            "tcp.dstport": "4433" if client else "55000",
            "tcp.stream": "0",
            "http2.type": types,
            "http2.streamid": str(stream_id),
            "http2.headers.method": method,
            "http2.headers.status": status,
            "http2.header.name": names,
        }

    @staticmethod
    def private_get(frame, stream_id, names, header_values, types="1", methods="GET"):
        row = H2DecryptedParitySummaryTests.header(
            frame, "client", stream_id, method=methods, names=names, types=types
        )
        row["http2.header.value"] = header_values
        return row

    def make_cohort(self, root, cohort, candidate=False):
        transport_fields = (
            "frame.number",
            "frame.time_relative",
            "tcp.srcport",
            "tcp.dstport",
            "tcp.stream",
        )
        hello_fields = (*transport_fields, "tls.handshake.version")
        client_hello = self.transport_row()
        client_hello["tls.handshake.version"] = "0x0303"
        self.write_csv(root, cohort, "clienthello", hello_fields, [client_hello])
        server_hello = self.transport_row()
        server_hello.update(
            {
                "tcp.srcport": "4433",
                "tcp.dstport": "55000",
                "tls.handshake.version": "0x0303",
            }
        )
        self.write_csv(root, cohort, "serverhello", hello_fields, [server_hello])
        self.write_csv(root, cohort, "syn", transport_fields, [self.transport_row()])
        alpn_fields = (*transport_fields, "tls.handshake.extensions_alpn_str")
        alpn = self.transport_row()
        alpn.update({"tcp.srcport": "4433", "tcp.dstport": "55000", "tls.handshake.extensions_alpn_str": "h2"})
        self.write_csv(root, cohort, "alpn", alpn_fields, [alpn])
        settings_fields = (*transport_fields, "http2.settings.id", "http2.settings.initial_window_size")
        settings = self.transport_row()
        settings.update({"http2.settings.id": "4", "http2.settings.initial_window_size": "131072"})
        self.write_csv(root, cohort, "settings", settings_fields, [settings])

        if candidate:
            frames = [
                self.frame(5, "client", 1, 1, 5),
                self.frame(6, "server", 1, 1, 4),
                self.frame(7, "server", 1, 0, 1),
                self.frame(8, "client", 3, 1, 4),
                self.frame(9, "server", 3, 1, 4),
            ]
            headers = [
                self.header(5, "client", 1, method="GET", names=":method\x1f:scheme\x1f:authority\x1f:path"),
                self.header(6, "server", 1, status="200", names=":status\x1fcontent-length"),
                self.header(8, "client", 3, method="CONNECT", names=":method\x1f:authority\x1fauth-or-cookie-redacted\x1fpadding"),
                self.header(9, "server", 3, status="200", names=":status\x1fpadding"),
            ]
        else:
            frames = [
                self.frame(5, "client", 1, 1, 5),
                self.frame(6, "server", 1, 1, 4),
                self.frame(7, "server", 1, 0, 1),
            ]
            headers = [
                self.header(5, "client", 1, method="GET", names=":method\x1f:scheme\x1f:authority\x1f:path"),
                self.header(6, "server", 1, status="200", names=":status\x1fcontent-length"),
            ]
        self.write_csv(root, cohort, "frames", frames[0].keys(), frames)
        self.write_csv(root, cohort, "headers", headers[0].keys(), headers)
        semantic_names = (
            ":method\x1f:scheme\x1f:authority\x1f:path\x1f"
            "sec-fetch-site\x1fsec-fetch-mode\x1fsec-fetch-dest\x1fpriority"
        )
        semantic_values = (
            "GET\x1fhttps\x1flocalhost:4433\x1f/camouflage/index.html\x1f"
            "none\x1fnavigate\x1fdocument\x1fu=0, i"
        )
        semantic_rows = (
            [self.private_get(5, "1", semantic_names, semantic_values)]
            if candidate
            else []
        )
        semantic_fields = (*headers[0].keys(), "http2.header.value")
        self.write_csv(
            root,
            cohort,
            "get-header-values",
            semantic_fields,
            semantic_rows,
        )

    def test_root_admission_writes_only_sanitized_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(directory, "reference")
            self.make_cohort(directory, "root", candidate=True)
            events = Path(directory) / "events.csv"
            report = Path(directory) / "summary.txt"
            SUMMARY.write_outputs(Path(directory), events, report, "4433", "root")
            text = report.read_text(encoding="utf-8")
            self.assertIn("client_settings_equal=yes", text)
            self.assertIn("semantic_clienthello_equal=yes", text)
            self.assertIn("server_negotiation_equal=yes", text)
            self.assertIn("root_preamble_get_count=1", text)
            self.assertIn("root_outer_connect_count=1", text)
            self.assertIn("root_sequence_validation=passed", text)
            self.assertIn("root_root_document_request_semantics=yes", text)
            self.assertIn("header_values_retained=no", text)
            with events.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            candidate = [row for row in rows if row["cohort"] == "root"]
            self.assertEqual([row["method"] for row in candidate if row["method"]], ["GET", "CONNECT"])
            self.assertNotIn("localhost", events.read_text(encoding="utf-8"))

    def test_document_start_overlap_requires_2xx_and_end_stream_at_any_position(self):
        for end_frame in (7, 10):
            with self.subTest(end_frame=end_frame), tempfile.TemporaryDirectory() as directory:
                self.make_cohort(directory, "reference")
                self.make_cohort(
                    directory, "document-start-overlap", candidate=True
                )
                frames_path = Path(directory) / "document-start-overlap-frames.csv"
                with frames_path.open(newline="", encoding="utf-8") as source:
                    frames = list(csv.DictReader(source))
                    frame_fields = frames[0].keys()
                frames[2]["frame.number"] = str(end_frame)
                frames[2]["frame.time_relative"] = str(end_frame / 1000)
                with frames_path.open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=frame_fields)
                    writer.writeheader()
                    writer.writerows(frames)
                headers_path = (
                    Path(directory) / "document-start-overlap-headers.csv"
                )
                with headers_path.open(newline="", encoding="utf-8") as source:
                    headers = list(csv.DictReader(source))
                    header_fields = headers[0].keys()
                headers[1]["http2.headers.status"] = "204"
                with headers_path.open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=header_fields)
                    writer.writeheader()
                    writer.writerows(headers)
                events = Path(directory) / "events.csv"
                report = Path(directory) / "summary.txt"
                SUMMARY.write_outputs(
                    Path(directory),
                    events,
                    report,
                    "4433",
                    "document-start-overlap",
                )
                text = report.read_text(encoding="utf-8")
                self.assertIn(
                    "document-start-overlap_preamble_before_first_connect=yes",
                    text,
                )
                self.assertIn(
                    "document-start-overlap_document_end_stream=yes", text
                )
                self.assertIn(
                    "document-start-overlap_end_stream_position_is_admission=no",
                    text,
                )

    def test_document_start_overlap_rejects_missing_end_stream_or_non_2xx(self):
        for mutation, message in (
            ("missing-end-stream", "document lacks END_STREAM"),
            ("non-2xx", "preamble GET lacks successful response"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                self.make_cohort(directory, "reference")
                self.make_cohort(
                    directory, "document-start-overlap", candidate=True
                )
                if mutation == "missing-end-stream":
                    path = Path(directory) / "document-start-overlap-frames.csv"
                    with path.open(newline="", encoding="utf-8") as source:
                        rows = list(csv.DictReader(source))
                        fields = rows[0].keys()
                    rows[2]["http2.flags"] = "0"
                else:
                    path = Path(directory) / "document-start-overlap-headers.csv"
                    with path.open(newline="", encoding="utf-8") as source:
                        rows = list(csv.DictReader(source))
                        fields = rows[0].keys()
                    rows[1]["http2.headers.status"] = "304"
                with path.open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                with self.assertRaisesRegex(ValueError, message):
                    SUMMARY.write_outputs(
                        Path(directory),
                        Path(directory) / "events.csv",
                        Path(directory) / "summary.txt",
                        "4433",
                        "document-start-overlap",
                    )

    def test_document_start_overlap_rejects_get_after_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(directory, "reference")
            self.make_cohort(
                directory, "document-start-overlap", candidate=True
            )
            for suffix in ("headers", "get-header-values"):
                path = Path(directory) / f"document-start-overlap-{suffix}.csv"
                with path.open(newline="", encoding="utf-8") as source:
                    rows = list(csv.DictReader(source))
                    fields = rows[0].keys()
                rows[0]["frame.number"] = "10"
                rows[0]["frame.time_relative"] = "0.010"
                with path.open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
            with self.assertRaisesRegex(
                ValueError, "preamble GET did not precede CONNECT"
            ):
                SUMMARY.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    "document-start-overlap",
                )

    def test_second_physical_tcp_connection_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(directory, "reference")
            self.make_cohort(directory, "root", candidate=True)
            frames = Path(directory) / "root-frames.csv"
            with frames.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
                fields = rows[0].keys()
            extra = dict(rows[-1])
            extra["frame.number"] = "10"
            extra["tcp.stream"] = "1"
            with frames.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writeheader()
                writer.writerows([*rows, extra])
            with self.assertRaisesRegex(ValueError, "one physical TCP/H2 connection"):
                SUMMARY.write_outputs(Path(directory), Path(directory) / "events.csv", Path(directory) / "summary.txt", "4433", "root")

    def test_data_and_headers_in_one_packet_map_to_headers_stream_only(self):
        separator = SUMMARY.SEPARATOR
        rows = [self.header(8, "client", f"1{separator}3", method="CONNECT", names=f":method{separator}:authority{separator}padding", types=f"0{separator}1")]
        events = SUMMARY.parse_headers(rows, "root", "4433")
        self.assertEqual(events[0]["stream"], "3")
        self.assertIn("padding", events[0]["header_set"])

    def test_ambiguous_two_headers_streams_fail_closed(self):
        separator = SUMMARY.SEPARATOR
        rows = [self.header(8, "client", f"1{separator}3", method="CONNECT", names=f":method{separator}:authority{separator}padding", types=f"1{separator}1")]
        with self.assertRaisesRegex(ValueError, "block/stream mapping is ambiguous"):
            SUMMARY.parse_headers(rows, "root", "4433")

    def test_tree_resource_semantics_require_computed_context_and_u2(self):
        separator = SUMMARY.SEPARATOR
        root_url = "https://localhost:4433/camouflage/index.html"
        base_names = (
            f":method{separator}:scheme{separator}:authority{separator}:path"
            f"{separator}sec-fetch-site{separator}sec-fetch-mode"
            f"{separator}sec-fetch-dest"
        )
        root_names = f"{base_names}{separator}priority"
        resource_names = (
            f"{base_names}{separator}referer{separator}priority"
        )
        rows = [
            self.private_get(
                5,
                "1",
                root_names,
                separator.join(
                    [
                        "GET",
                        "https",
                        "localhost:4433",
                        "/camouflage/index.html",
                        "none",
                        "navigate",
                        "document",
                        "u=0, i",
                    ]
                ),
            ),
            self.private_get(
                6,
                "3",
                resource_names,
                separator.join(
                    [
                        "GET",
                        "https",
                        "localhost:4433",
                        "/camouflage/style.css",
                        "same-origin",
                        "no-cors",
                        "style",
                        root_url,
                        "u=2",
                    ]
                ),
            ),
            self.private_get(
                7,
                "5",
                resource_names,
                separator.join(
                    [
                        "GET",
                        "https",
                        "localhost:4433",
                        "/camouflage/app.js",
                        "same-origin",
                        "no-cors",
                        "script",
                        root_url,
                        "u=2",
                    ]
                ),
            ),
        ]
        public = [
            {"frame": frame, "tcp_stream": "0", "stream": stream}
            for frame, stream in ((5, "1"), (6, "3"), (7, "5"))
        ]
        with tempfile.TemporaryDirectory() as directory:
            self.write_csv(
                directory,
                "tree-complete",
                "get-header-values",
                rows[0].keys(),
                rows,
            )
            semantics = SUMMARY.read_get_request_semantics(
                Path(directory), "tree-complete", "4433", public
            )
            root_identity = SUMMARY.validate_expected_get_request_semantics(
                "tree-complete", semantics, "tree-complete"
            )
            self.assertEqual(root_identity, (5, "0", "1"))
            rows[1]["http2.header.value"] = rows[1][
                "http2.header.value"
            ].replace("u=2", "u=7")
            self.write_csv(
                directory,
                "tree-complete",
                "get-header-values",
                rows[0].keys(),
                rows,
            )
            semantics = SUMMARY.read_get_request_semantics(
                Path(directory), "tree-complete", "4433", public
            )
            with self.assertRaisesRegex(
                ValueError, "style resource request semantics differ"
            ):
                SUMMARY.validate_expected_get_request_semantics(
                    "tree-complete", semantics, "tree-complete"
                )

    def test_root_document_semantics_require_https_and_native_priority(self):
        base = {
            "frame": 5,
            "tcp_stream": "0",
            "stream": "1",
            "selected": (
                (":method", "GET"),
                (":scheme", "https"),
                (":authority", "localhost:4433"),
                (":path", "/camouflage/index.html"),
                ("sec-fetch-site", "none"),
                ("sec-fetch-mode", "navigate"),
                ("sec-fetch-dest", "document"),
                ("priority", "u=0, i"),
            ),
        }
        self.assertEqual(
            SUMMARY.validate_expected_get_request_semantics(
                "root", [base], "root"
            ),
            (5, "0", "1"),
        )
        for name, value in ((":scheme", "http"), ("priority", "u=0")):
            changed = dict(base)
            changed["selected"] = tuple(
                (field, value if field == name else current)
                for field, current in base["selected"]
            )
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "root document request semantics differ"
            ):
                SUMMARY.validate_expected_get_request_semantics(
                    "root", [changed], "root"
                )

    def test_early_overlap_root_completion_uses_private_path_identity(self):
        def event(frame, direction, stream, method="", status="", end="", headers=()):
            return {
                "frame": frame,
                "time": frame / 1000,
                "direction": direction,
                "tcp_stream": "0",
                "stream": stream,
                "method": method,
                "status": status,
                "header_set": frozenset(headers),
                "header_order": tuple(headers),
                "end_stream_frame": end,
            }

        reference = [event(4, "client", "1", method="GET")]
        arm = [
            event(5, "client", "1", method="GET"),  # stylesheet first
            event(6, "client", "3", method="GET"),  # private :path says root
            event(7, "client", "5", method="GET"),
            event(8, "server", "3", status="200", end=9),
            event(10, "server", "1", status="200", end=20),
            event(11, "server", "5", status="200", end=21),
            event(12, "client", "7", method="CONNECT", headers=("padding",)),
            event(13, "server", "7", status="200", headers=("padding",)),
        ]
        SUMMARY.validate(
            reference,
            arm,
            "tree-early-overlap",
            "same-settings",
            "same-settings",
            "same-client-tls",
            "same-client-tls",
            "same-server-tls",
            "same-server-tls",
            (6, "0", "3"),
        )
        completed_before_connect = [
            {**candidate, "end_stream_frame": min(candidate["frame"] + 1, 11)}
            if candidate["direction"] == "server" and candidate["status"] == "200"
            else dict(candidate)
            for candidate in arm
        ]
        SUMMARY.validate(
            reference,
            completed_before_connect,
            "tree-root-overlap",
            "same-settings",
            "same-settings",
            "same-client-tls",
            "same-client-tls",
            "same-server-tls",
            "same-server-tls",
            (6, "0", "3"),
        )
        missing_asset_end = [
            {**candidate, "end_stream_frame": ""}
            if candidate["direction"] == "server"
            and candidate["status"] == "200"
            and candidate["stream"] == "5"
            else dict(candidate)
            for candidate in completed_before_connect
        ]
        with self.assertRaisesRegex(
            ValueError, "lacks END_STREAM for an expected resource"
        ):
            SUMMARY.validate(
                reference,
                missing_asset_end,
                "tree-root-overlap",
                "same-settings",
                "same-settings",
                "same-client-tls",
                "same-client-tls",
                "same-server-tls",
                "same-server-tls",
                (6, "0", "3"),
            )
        root_after_connect = [
            {**candidate, "end_stream_frame": 13}
            if candidate["direction"] == "server"
            and candidate["status"] == "200"
            and candidate["stream"] == "3"
            else dict(candidate)
            for candidate in completed_before_connect
        ]
        with self.assertRaisesRegex(
            ValueError, "tree-root-overlap root did not complete before CONNECT"
        ):
            SUMMARY.validate(
                reference,
                root_after_connect,
                "tree-root-overlap",
                "same-settings",
                "same-settings",
                "same-client-tls",
                "same-client-tls",
                "same-server-tls",
                "same-server-tls",
                (6, "0", "3"),
            )
        with self.assertRaisesRegex(
            ValueError, "root did not complete before CONNECT"
        ):
            SUMMARY.validate(
                reference,
                arm,
                "tree-early-overlap",
                "same-settings",
                "same-settings",
                "same-client-tls",
                "same-client-tls",
                "same-server-tls",
                "same-server-tls",
                (5, "0", "1"),
            )

    def test_private_get_multiplex_ambiguity_fails_closed(self):
        separator = SUMMARY.SEPARATOR
        row = self.private_get(
            5,
            f"1{separator}3",
            f":method{separator}:scheme{separator}:authority{separator}:path",
            f"GET{separator}https{separator}localhost:4433{separator}/camouflage/index.html",
            types=f"1{separator}1",
        )
        public = [{"frame": 5, "tcp_stream": "0", "stream": "1"}]
        with tempfile.TemporaryDirectory() as directory:
            self.write_csv(
                directory,
                "tree-complete",
                "get-header-values",
                row.keys(),
                [row],
            )
            with self.assertRaisesRegex(
                ValueError, "method/HEADERS-stream mapping is ambiguous"
            ):
                SUMMARY.read_get_request_semantics(
                    Path(directory), "tree-complete", "4433", public
                )

    def test_runner_is_same_base_isolated_and_fail_closed(self):
        runner = (HERE / "run-h2-capture-comparison.sh").read_text(encoding="utf-8")
        self.assertIn("H2 decrypted parity requires NAIVEFOX_CAPTURE_MODE=same-base", runner)
        self.assertIn("run-camouflage-isolated-network.sh", runner)
        self.assertIn("monitor-network-mutations.py", runner)
        self.assertIn("camouflage_capture_health.py", runner)
        self.assertIn("dumpcap stopped before the H2 workload completed", runner)
        self.assertIn('safe_root="$STATE_ROOT/h2-capture-safe"', runner)
        self.assertIn('$(dirname -- "$safe_dir") == "$safe_root"', runner)
        self.assertIn("traffic_secret", runner)
        self.assertIn("--browser-backend commandline|selenium", runner)
        self.assertIn("browser_backend=%s", runner)
        self.assertIn("camouflage_browser_controller.py", runner)
        self.assertIn("wait_for_marker", runner)
        self.assertIn("stop_controller", runner)
        self.assertIn("http2.header.value", runner)
        self.assertIn("safe summary exports boolean results only", runner)
        self.assertIn("capture_worktree_dirty", runner)
        self.assertIn("capture_source_state_sha256", runner)
        self.assertIn(
            "browser_start_state=cold_after_capture_start_both_cohorts", runner
        )
        self.assertIn(
            "browser_start_state=ready_before_capture_navigation_after_capture",
            runner,
        )
        self.assertIn("document-start-overlap", runner)
        self.assertIn("--mode h2 --outer-h2-only", runner)
        self.assertIn(
            "H2 decrypted fixture is not constrained to the h2-only listener",
            runner,
        )
        self.assertIn(
            "document-start-overlap admission=request-committed "
            "request_committed=1 root_done=0 protocol=h2$",
            runner,
        )
        self.assertIn(
            "preamble result=success .*http=2[0-9][0-9] .*protocol=h2$",
            runner,
        )
        self.assertIn(
            "document-start-overlap drain=complete root_done=1 "
            "completed_resources=0 protocol=h2$",
            runner,
        )
        self.assertIn(
            '$admission_connection == "$result_connection"', runner
        )
        self.assertIn(
            '$admission_connection == "$drain_connection"', runner
        )


if __name__ == "__main__":
    unittest.main()
