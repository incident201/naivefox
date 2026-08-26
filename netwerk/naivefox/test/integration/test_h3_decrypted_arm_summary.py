#!/usr/bin/env python3

import csv
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(__file__)
SPEC = importlib.util.spec_from_file_location(
    "h3_decrypted_arm_summary",
    os.path.join(HERE, "h3_decrypted_arm_summary.py"),
)
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)
PRIVATE_SEMANTIC_MARKER = "private-tree-authority.invalid"


class H3DecryptedArmSummaryTests(unittest.TestCase):
    def test_cold_winner_lifecycle_requires_exact_single_proxy_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (
                "decrypted-document-cold-winner-handoff-private-lifecycle.moz_log"
            )
            path.write_text(
                "h3.cold_winner_handoff action=document-configured document=aa ci=11 caps=1\n"
                "h3.cold_winner_handoff action=attempt-selected document=aa entry=22 ci=11 speculative=0 pending=1 transport=proxy-h3 attempts=1 route-match=1\n"
                "h3.cold_winner_handoff action=init-post document=aa attempt=33 pending-owned=1\n"
                "h3.cold_winner_handoff action=init-run document=aa attempt=33 registered=1\n"
                "h3.cold_winner_handoff action=dns-start document=aa attempt=33 physical-proxy=1 candidates-max=1\n"
                "h3.cold_winner_handoff action=dns-complete document=aa attempt=33 rv=00000000\n"
                "h3.cold_winner_handoff action=udp-attempt-start document=aa attempt=33 candidate=1 candidates-total=1 protocol=h3 proxy-aware=1\n"
                "h3.cold_winner_handoff action=connection-racing carrier=44 conn=55 racing=1 before-activate=1\n"
                "h3.cold_winner_handoff action=activate-callback-post carrier=44 rv=00000000\n"
                "h3.cold_winner_handoff action=activate-callback-run carrier=44 rv=00000000\n"
                "h3.cold_winner_handoff action=winner-ready document=aa carrier=44 conn=55 racing=1\n"
                "h3.cold_winner_handoff action=exact-dispatch-complete document=aa carrier=44 conn=55 pending-removed=1 dispatched=1 racing=1\n"
                "h3.cold_winner_handoff action=winner-publish document=aa conn=55 racing=0 exact-dispatch=1\n",
                encoding="utf-8",
            )
            identities = summary.validate_cold_winner_handoff_lifecycle(
                Path(directory), "document-cold-winner-handoff"
            )
            self.assertEqual(identities["document_id"], "aa")
            self.assertEqual(identities["carrier_id"], "44")
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "action=winner-publish", "action=terminal-failure"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "terminal failure"):
                summary.validate_cold_winner_handoff_lifecycle(
                    Path(directory), "document-cold-winner-handoff"
                )

    def test_native_cache_open_lifecycle_requires_async_readonly_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (
                "decrypted-document-native-cache-open-private-lifecycle.moz_log"
            )
            path.write_text(
                "h3.native_cache_open action=open-begin channel=abc "
                "inhibit_caching=1 expected_mode=readonly\n"
                "h3.native_cache_open action=callback-pending channel=abc\n"
                "h3.native_cache_open action=callback channel=abc entry=0 "
                "new=0 status=804b003d\n"
                "h3.native_cache_open action=trigger-network channel=abc "
                "cold_readonly_miss=1\n",
                encoding="utf-8",
            )
            summary.validate_native_cache_open_lifecycle(
                Path(directory), "document-native-cache-open"
            )
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "entry=0 new=0 status=804b003d",
                    "entry=abc new=0 status=00000000",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "cold read-only miss"):
                summary.validate_native_cache_open_lifecycle(
                    Path(directory), "document-native-cache-open"
                )

    def test_native_channel_open_lifecycle_requires_real_classifier_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (
                "decrypted-document-native-channel-open-private-lifecycle.moz_log"
            )
            path.write_text(
                "h3.native_channel_open action=open-begin channel=abc "
                "inhibit_caching=0 expected_mode=normal\n"
                "h3.native_channel_open action=callback-pending channel=abc\n"
                "h3.native_channel_open action=classifier-predicate channel=abc "
                "result=1 type=6 triggering_system=1 bypass=0 be_conservative=0\n"
                "h3.native_channel_open action=classifier-db-service channel=abc "
                "exists=1\n"
                "h3.native_channel_open action=classifier-uri-principal channel=abc "
                "triggering_system=1 uri_system=0 uri_content=1 uri_match=1 "
                "attrs_match=1 uri=https://localhost:4433/camouflage/index.html?"
                "scenario=browser_page&size=262144&count=4&idle_ms=5000&"
                "completion=0123456789abcdef0123456789abcdef origin_attributes=\n"
                "h3.native_channel_open action=classifier-mode channel=abc "
                "local_db=1 real_time_mode=0\n"
                "h3.native_channel_open action=classifier-classify channel=abc "
                "status=00000000 expect_callback=1\n"
                "h3.native_channel_open action=classifier-suspended channel=abc "
                "suspend_count=1\n"
                "h3.native_channel_open action=callback channel=abc entry=def "
                "new=1 status=00000000\n"
                "h3.native_channel_open action=trigger-network channel=abc "
                "new_writable_entry=1 classifier_started=1\n"
                "h3.native_channel_open action=classifier-complete channel=abc "
                "status=00000000 resume_status=00000000 asynchronous=1\n",
                encoding="utf-8",
            )
            summary.validate_native_channel_open_lifecycle(
                Path(directory), "document-native-channel-open"
            )
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "expect_callback=1", "expect_callback=0"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "genuine asynchronous"):
                summary.validate_native_channel_open_lifecycle(
                    Path(directory), "document-native-channel-open"
                )

    def test_carrier_dispatch_requires_document_complete_control(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "document-carrier-dispatch decrypted validation requires",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-carrier-dispatch",),
                )

    def test_carrier_dispatch_admission_and_safe_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.015, "server", 0, status="200"),
                self.event(11, 0.017, "client", 4, method="CONNECT"),
                self.event(14, 0.022, "server", 4, status="200"),
            ]
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(directory, "document-complete", common)
            self.make_cohort(directory, "document-carrier-dispatch", common)
            self.make_carrier_dispatch_evidence(directory)
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("document-complete", "document-carrier-dispatch"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            for key in (
                "single_connection",
                "single_client_hello",
                "no_carrier_request",
                "real_document_deferred",
                "normal_cm_dispatch",
                "same_session",
                "request_semantics_match",
                "response_size_match",
                "root_fin_before_connect",
                "lifecycle_exact",
            ):
                self.assertIn(f"document_carrier_dispatch_{key}=yes", safe_summary)
            self.assertNotIn("0x111", safe_summary)
            self.assertNotIn("0x222", safe_summary)

    def test_carrier_dispatch_rejects_lifecycle_reordering(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_carrier_dispatch_evidence(directory)
            path = Path(directory) / (
                "decrypted-document-carrier-dispatch-private-lifecycle.moz_log"
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[8], lines[9] = lines[9], lines[8]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "marker order is invalid"):
                summary.validate_carrier_dispatch_lifecycle(
                    Path(directory), "document-carrier-dispatch"
                )

    def test_carrier_dispatch_rejects_unknown_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_carrier_dispatch_evidence(directory)
            path = Path(directory) / (
                "decrypted-document-carrier-dispatch-private-lifecycle.moz_log"
            )
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "h3.carrier_dispatch action=unexpected session=0x111 "
                    "carrier=0x222 document=0x333\n"
                )
            with self.assertRaisesRegex(ValueError, "unknown or ambiguous"):
                summary.validate_carrier_dispatch_lifecycle(
                    Path(directory), "document-carrier-dispatch"
                )

    def test_carrier_dispatch_rejects_any_extra_http_request(self):
        with tempfile.TemporaryDirectory() as directory:
            control = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.015, "server", 0, status="200"),
                self.event(11, 0.017, "client", 4, method="CONNECT"),
                self.event(14, 0.022, "server", 4, status="200"),
            ]
            carrier = [
                self.event(6, 0.008, "client", 8, method="HEAD"),
                *control,
            ]
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(directory, "document-complete", control)
            self.make_cohort(directory, "document-carrier-dispatch", carrier)
            self.make_carrier_dispatch_evidence(directory)
            with self.assertRaisesRegex(ValueError, "unexpected outer HTTP request"):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-complete", "document-carrier-dispatch"),
                )

    def test_carrier_dispatch_rejects_unexplained_bidi_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.015, "server", 0, status="200"),
                self.event(11, 0.017, "client", 4, method="CONNECT"),
                self.event(14, 0.022, "server", 4, status="200"),
            ]
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(directory, "document-complete", common)
            self.make_cohort(directory, "document-carrier-dispatch", common)
            self.make_carrier_dispatch_evidence(directory)
            rows = summary.read_rows(
                Path(directory), "document-carrier-dispatch", "lifecycle"
            )
            extra = dict(rows[0])
            extra["frame.number"] = "6"
            extra["quic.stream.stream_id"] = "8"
            self.write_csv(
                directory,
                "document-carrier-dispatch",
                "lifecycle",
                tuple(rows[0]),
                [*rows, extra],
            )
            with self.assertRaisesRegex(ValueError, "unexplained client bidirectional"):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-complete", "document-carrier-dispatch"),
                )

    def test_carrier_dispatch_rejects_identity_and_stream_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_carrier_dispatch_evidence(directory)
            path = Path(directory) / (
                "decrypted-document-carrier-dispatch-private-lifecycle.moz_log"
            )
            text = path.read_text(encoding="utf-8").replace(
                "carrier=0x222", "carrier=0x333"
            )
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "transaction ids must differ"):
                summary.validate_carrier_dispatch_lifecycle(
                    Path(directory), "document-carrier-dispatch"
                )

        with tempfile.TemporaryDirectory() as directory:
            self.make_carrier_dispatch_evidence(directory)
            path = Path(directory) / (
                "decrypted-document-carrier-dispatch-private-lifecycle.moz_log"
            )
            text = path.read_text(encoding="utf-8").replace(
                "stream_id=0 carrier_complete=1",
                "stream_id=8 carrier_complete=1",
            )
            path.write_text(text, encoding="utf-8")
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.015, "server", 0, status="200"),
                self.event(11, 0.017, "client", 4, method="CONNECT"),
                self.event(14, 0.022, "server", 4, status="200"),
            ]
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(directory, "document-complete", common)
            self.make_cohort(directory, "document-carrier-dispatch", common)
            with self.assertRaisesRegex(ValueError, "does not match the wire GET"):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-complete", "document-carrier-dispatch"),
                )

    def test_carrier_dispatch_rejects_confirmed_gate_contamination(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_carrier_dispatch_evidence(directory)
            path = Path(directory) / (
                "decrypted-document-carrier-dispatch-private-lifecycle.moz_log"
            )
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "h3.preamble_confirm_gate action=wait session=0x111 "
                    "ci=0x444 transport_confirmed=0\n"
                )
            with self.assertRaisesRegex(ValueError, "handshake-confirmed gate"):
                summary.validate_carrier_dispatch_lifecycle(
                    Path(directory), "document-carrier-dispatch"
                )

    def test_handshake_confirmed_requires_document_complete_control(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "document-handshake-confirmed decrypted validation requires",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-handshake-confirmed",),
                )

    def test_handshake_confirmed_admission_and_safe_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.015, "server", 0, status="200"),
                self.event(11, 0.017, "client", 4, method="CONNECT"),
                self.event(14, 0.022, "server", 4, status="200"),
            ]
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(directory, "document-complete", common)
            self.make_cohort(directory, "document-handshake-confirmed", common)
            self.make_confirmed_evidence(directory, handshake_frame=6)
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("document-complete", "document-handshake-confirmed"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            for key in (
                "single_connection",
                "single_client_hello",
                "server_handshake_done_before_get",
                "h3_initialization_before_handshake_done",
                "settings_match",
                "no_client_zero_rtt",
                "request_semantics_match",
                "response_size_match",
                "root_fin_before_connect",
                "lifecycle_exact",
                "lifecycle_order_valid",
                "lifecycle_ids_match",
            ):
                self.assertIn(
                    f"document_handshake_confirmed_{key}=yes", safe_summary
                )
            self.assertNotIn("0x111", safe_summary)
            self.assertNotIn("0x222", safe_summary)

    def test_handshake_confirmed_rejects_get_before_handshake_done(self):
        with tempfile.TemporaryDirectory() as directory:
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.015, "server", 0, status="200"),
                self.event(11, 0.017, "client", 4, method="CONNECT"),
                self.event(14, 0.022, "server", 4, status="200"),
            ]
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(directory, "document-complete", common)
            self.make_cohort(directory, "document-handshake-confirmed", common)
            self.make_confirmed_evidence(directory, handshake_frame=8)
            with self.assertRaisesRegex(
                ValueError, "initialization < HANDSHAKE_DONE < GET"
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-complete", "document-handshake-confirmed"),
                )

    def test_tree_root_overlap_requires_complete_control(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "document-overlap decrypted validation requires document-complete",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("document-overlap",),
                )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "root-pmtud-control decrypted validation requires root",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("root-pmtud-control",),
                )

        for arms in (
            ("document-start-overlap",),
            ("document-complete", "document-start-overlap"),
        ):
            with self.subTest(arms=arms), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(
                    ValueError,
                    "document-start-overlap decrypted validation requires",
                ):
                    summary.write_outputs(
                        Path(directory),
                        Path(directory) / "events.csv",
                        Path(directory) / "summary.txt",
                        "4433",
                        arms,
                    )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "tree-root-overlap decrypted validation requires tree-complete",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-root-overlap",),
                )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "tree-overlap decrypted validation requires tree-complete",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-overlap",),
                )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "tree-early-overlap decrypted validation requires tree-complete",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-early-overlap",),
                )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError,
                "tree-root-overlap-css decrypted validation requires tree-complete-css",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-root-overlap-css",),
                )

    def test_runner_supports_selectable_document_and_tree_arms(self):
        path = os.path.join(HERE, "run-h3-capture-comparison.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("--compare-arm", runner)
        self.assertIn(
            "comparison_arms=(off gate root tree-complete tree-overlap)", runner
        )
        self.assertIn('capture_sides=(reference "${comparison_arms[@]}")', runner)
        self.assertIn("/camouflage/index.html?scenario=browser_page", runner)
        self.assertIn('--arms "$(IFS=,;', runner)
        self.assertIn("get-header-values.csv", runner)
        self.assertIn('http3.headers.method==\\"GET\\"', runner)
        self.assertIn(
            '!(http3.header.header.name contains \\"authorization\\")',
            runner,
        )
        self.assertIn("http3.headers.header.value", runner)
        self.assertIn("response-header-values.csv", runner)
        self.assertIn("http3.headers.status==200", runner)
        self.assertIn("if [[ -n $(tshark", runner)
        self.assertIn('mv -f -- "$capture_stage_raw" "$capture_pcap"', runner)
        self.assertIn("exec unshare --net --mount-proc", runner)
        self.assertIn("run-camouflage-isolated-network.sh", runner)
        self.assertIn("monitor-network-mutations.py", runner)
        self.assertIn("network route/address/link mutation invalidated", runner)
        self.assertIn('camouflage_capture_health.py" "$capture_log"', runner)
        self.assertIn(
            "dumpcap stopped before the H3 workload capture was complete", runner
        )
        self.assertIn("capture_worktree_dirty", runner)
        self.assertIn("capture_source_state_sha256", runner)
        self.assertIn('git -C "$SOURCE_ROOT" diff --binary', runner)
        self.assertIn("fixture_user_encoded", runner)
        self.assertIn("fixture_pass_encoded", runner)
        self.assertIn("traffic_secret", runner)
        self.assertIn("root-pmtud-control comparison requires root", runner)
        self.assertIn(
            "document-handshake-confirmed comparison requires document-complete",
            runner,
        )
        self.assertIn('"MOZ_LOG=NaiveFoxLifecycle:5"', runner)
        self.assertIn('"MOZ_LOG_FILE=$lifecycle_log_base"', runner)
        self.assertIn('quic.frame_type==0x1e', runner)
        self.assertIn('>"$prefix-handshake-done.csv"', runner)
        self.assertIn("tree-overlap comparison requires tree-complete", runner)
        self.assertIn(
            "document-native-channel-open comparison requires document-complete",
            runner,
        )
        self.assertIn(
            'reference_profile="$capture_dir/reference-profile-template"', runner
        )
        self.assertIn(
            'local run_profile="$capture_dir/$pass-reference-profile"', runner
        )
        self.assertIn('cp -aL -- "$reference_profile/." "$run_profile/"', runner)
        self.assertIn(
            'validate_native_channel_fresh_cache "$run_profile" reference', runner
        )
        self.assertIn(
            'validate_native_channel_fresh_cache "$naivefox_profile" naivefox',
            runner,
        )
        for pref in (
            "browser.safebrowsing.realTime.enabled",
            "browser.safebrowsing.globalCache.enabled",
            "browser.safebrowsing.provider.google5.enabled",
        ):
            self.assertEqual(runner.count(f'user_pref("{pref}", false);'), 3)
        self.assertIn("tree-early-overlap comparison requires tree-complete", runner)
        self.assertIn('user_pref("network.http.http3.pmtud", true);', runner)
        reference_url = (
            "https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/"
            "camouflage/index.html?scenario=browser_page"
        )
        self.assertEqual(runner.count(reference_url), 1)
        self.assertIn(
            'local preamble_path="/camouflage/index.html?scenario=browser_page',
            runner,
        )
        self.assertIn('--preamble-path "$preamble_path"', runner)
        self.assertEqual(
            runner.count(
                '"https://localhost:$NAIVEFOX_FIXTURE_HTTPS_PORT$preamble_path"'
            ),
            1,
        )
        self.assertNotIn("&arm=$arm", runner)
        self.assertIn("camouflage_browser_controller.py", runner)
        self.assertIn(
            "browser_backend=${NAIVEFOX_CAMOUFLAGE_BROWSER_BACKEND:-selenium}",
            runner,
        )
        self.assertIn('--backend "$browser_backend"', runner)
        self.assertIn(
            "H3 arm comparison requires a pre-launched Selenium browser", runner
        )
        self.assertIn(
            "WebDriver quit timed out; using controlled Firefox process-group SIGTERM",
            runner,
        )
        self.assertIn('kill -TERM -- "-$browser_controller_pid"', runner)
        self.assertIn('kill -KILL -- "-$browser_controller_pid"', runner)
        self.assertIn("--completion-file", runner)
        self.assertIn(
            "browser_env=(-u SSLKEYLOGFILE -u MOZ_LOG -u MOZ_LOG_FILE)", runner
        )
        self.assertIn('browser_env+=("SSLKEYLOGFILE=$keylog")', runner)
        self.assertIn('setsid env "${browser_env[@]}"', runner)
        self.assertIn('run_browser_workload "$pass-reference"', runner)
        self.assertIn('run_browser_workload "decrypted-$arm"', runner)

        reference_body = runner.split("run_reference()", 1)[1].split(
            "run_naivefox()", 1
        )[0]
        arm_body = runner.split("run_naivefox_arm()", 1)[1].split(
            "if [[ $comparison_design == arms ]]", 1
        )[0]
        self.assertLess(
            reference_body.index("start_network_mutation_monitor"),
            reference_body.index("start_capture"),
        )
        self.assertLess(
            reference_body.index("start_capture"),
            reference_body.index('run_browser_workload "$pass-reference"'),
        )
        self.assertLess(
            arm_body.index("start_network_mutation_monitor"),
            arm_body.index('"$NAIVEFOX_BIN" "$config"'),
        )
        self.assertLess(
            arm_body.index("start_capture"),
            arm_body.index('run_browser_workload "decrypted-$arm"'),
        )

    def test_failed_h3_comparison_removes_only_its_exact_safe_directory(self):
        path = os.path.join(HERE, "run-h3-capture-comparison.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        cleanup = runner.split("cleanup() {", 1)[1].split("trap cleanup EXIT", 1)[0]
        self.assertIn("safe_dir=", runner.split("stop_pid()", 1)[0])
        self.assertIn(
            "[[ -n $safe_dir && ($status -ne 0 || $success -ne 1) ]]",
            cleanup,
        )
        self.assertIn('safe_root="$STATE_ROOT/h3-capture-safe"', cleanup)
        self.assertIn('$(dirname -- "$safe_dir") == "$safe_root"', cleanup)
        self.assertIn('rm -rf -- "$safe_dir"', cleanup)
        self.assertIn("refusing to remove unexpected safe H3 path", cleanup)

    def test_test_alt_svc_mapping_is_reference_only(self):
        path = os.path.join(HERE, "run-h3-capture-comparison.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()

        mapping_pref = "network.http.http3.alt-svc-mapping-for-testing"
        force_pref = "network.http.http3.force-use-alt-svc-mapping-for-testing"
        self.assertEqual(runner.count(mapping_pref), 1)
        self.assertEqual(runner.count(force_pref), 1)

        reference_setup = runner.split('reference_profile="$capture_dir/', 1)[1]
        reference_setup = reference_setup.split("run_reference()", 1)[0]
        self.assertIn(mapping_pref, reference_setup)
        self.assertIn(force_pref, reference_setup)

        naivefox_setup = runner.split("run_naivefox_arm()", 1)[1]
        naivefox_setup = naivefox_setup.split("NAIVEFOX_FIXTURE_USER=", 1)[0]
        self.assertNotIn(mapping_pref, naivefox_setup)
        self.assertNotIn(force_pref, naivefox_setup)

    def test_rejects_a_second_physical_outer_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "root",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(10, 0.013, "server", 0, status="200"),
                    self.event(12, 0.015, "client", 4, method="CONNECT"),
                    self.event(14, 0.018, "server", 4, status="200"),
                ],
            )
            packets = Path(directory) / "decrypted-root-packets.csv"
            with packets.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            extra = dict(rows[0])
            extra["frame.number"] = "2"
            extra["frame.time_relative"] = "0.001"
            extra["quic.connection.number"] = "1"
            with packets.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows([*rows, extra])

            with self.assertRaisesRegex(
                ValueError,
                "root must use exactly one physical outer QUIC connection",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("root",),
                )

    def test_rejects_a_second_unique_outer_clienthello(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "gate",
                [
                    self.event(7, 0.009, "client", 0, method="CONNECT"),
                    self.event(10, 0.013, "server", 0, status="200"),
                ],
            )
            hellos = Path(directory) / "decrypted-gate-clienthello.csv"
            with hellos.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["quic.connection.number"])
                writer.writeheader()
                writer.writerows([
                    {"quic.connection.number": "0"},
                    {"quic.connection.number": "1"},
                ])

            with self.assertRaisesRegex(
                ValueError,
                "gate outer ClientHello has an unknown connection identity",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("gate",),
                )

    def test_rejects_missing_outer_connection_identity(self):
        for suffix, message in (
            ("packets", "QUIC packet connection identity is ambiguous"),
            ("clienthello", "outer ClientHello connection identity is ambiguous"),
        ):
            with self.subTest(
                suffix=suffix
            ), tempfile.TemporaryDirectory() as directory:
                self.make_cohort(
                    directory,
                    "reference",
                    [
                        self.event(8, 0.010, "client", 0, method="GET"),
                        self.event(12, 0.020, "server", 0, status="200"),
                    ],
                )
                self.make_cohort(
                    directory,
                    "gate",
                    [
                        self.event(7, 0.009, "client", 0, method="CONNECT"),
                        self.event(10, 0.013, "server", 0, status="200"),
                    ],
                )
                path = Path(directory) / f"decrypted-gate-{suffix}.csv"
                with path.open(newline="", encoding="utf-8") as stream:
                    reader = csv.DictReader(stream)
                    rows = list(reader)
                    fieldnames = reader.fieldnames
                rows[0]["quic.connection.number"] = ""
                with path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                with self.assertRaisesRegex(ValueError, message):
                    summary.write_outputs(
                        Path(directory),
                        Path(directory) / "events.csv",
                        Path(directory) / "summary.txt",
                        "4433",
                        ("gate",),
                    )

    def test_rejects_fixture_alt_used_on_tree_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.013, "server", 0, status="200"),
                    self.event(12, 0.015, "server", 4, status="200"),
                    self.event(14, 0.017, "server", 8, status="200"),
                    self.event(15, 0.018, "client", 12, method="CONNECT"),
                    self.event(18, 0.022, "server", 12, status="200"),
                ],
            )
            headers = Path(directory) / "decrypted-tree-complete-header-names.csv"
            with headers.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                if row["frame.number"] == "8":
                    row["http3.header.header.name"] += ";alt-used"
            with headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            with self.assertRaisesRegex(
                ValueError,
                "tree-complete preamble GET unexpectedly used fixture Alt-Svc mapping",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete",),
                )

    def test_tree_complete_rejects_asset_fin_after_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
                fin_frames={"8": 15},
            )
            with self.assertRaisesRegex(
                ValueError,
                "tree-complete asset stream FIN did not precede CONNECT",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete",),
                )

    def test_tree_complete_does_not_treat_asset_fin_as_application_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
            )
            lifecycle = Path(directory) / "decrypted-tree-complete-lifecycle.csv"
            with lifecycle.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
                fields = rows[0].keys()
            rows = [row for row in rows if row["quic.stream.stream_id"] != "8"]
            with lifecycle.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("tree-complete",),
            )
            self.assertIn(
                "tree_expected_request_semantics=yes",
                destination.read_text(encoding="utf-8"),
            )

    def test_tree_overlap_requires_headers_and_late_fin_on_same_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_tree_complete_control(directory)
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 8, status="200"),
                    self.event(15, 0.017, "server", 12, status="200"),
                ],
                fin_frames={"4": 12, "8": 16},
            )
            with self.assertRaisesRegex(
                ValueError,
                "resource HEADERS < CONNECT < resource FIN",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete", "tree-overlap"),
                )

    def test_tree_overlap_accepts_one_asset_stream_spanning_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_tree_complete_control(directory)
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
                fin_frames={"4": 15, "8": 12},
            )
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("tree-complete", "tree-overlap"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn(
                "tree-overlap_overlap_evidence="
                "server-fin-after-connect,resource-stream-spans-connect",
                safe_summary,
            )
            self.assertIn("tree_request_semantics_match=yes", safe_summary)
            self.assertIn("tree_expected_request_semantics=yes", safe_summary)

    def test_tree_early_overlap_requires_completed_root_and_matching_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-early-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
                fin_frames={"4": 15},
            )
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("tree-complete", "tree-early-overlap"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn("tree-early-overlap_outer_get_count=3", safe_summary)
            self.assertIn(
                "tree-early-overlap_overlap_evidence="
                "server-fin-after-connect,resource-stream-spans-connect",
                safe_summary,
            )
            self.assertIn(
                "tree_early_overlap_request_semantics_match=yes", safe_summary
            )
            self.assertIn("tree_early_overlap_asset_sizes_match=yes", safe_summary)

            lifecycle = Path(directory) / "decrypted-tree-early-overlap-lifecycle.csv"
            with lifecycle.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                if row["quic.stream.stream_id"] == "0":
                    row["frame.number"] = "16"
            with lifecycle.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "root FIN did not precede CONNECT"):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    destination,
                    "4433",
                    ("tree-complete", "tree-early-overlap"),
                )

            # Restore the causal ordering, then prove that the private-only
            # response-size comparison rejects a workload mismatch.
            for row in rows:
                if row["quic.stream.stream_id"] == "0":
                    row["frame.number"] = "10"
            with lifecycle.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            response_headers = (
                Path(directory)
                / "decrypted-tree-early-overlap-response-header-values.csv"
            )
            with response_headers.open(newline="", encoding="utf-8") as stream:
                response_rows = list(csv.DictReader(stream))
            for row in response_rows:
                if row["quic.stream.stream_id"] == "4":
                    values = row["http3.headers.header.value"].split(
                        summary.PRIVATE_VALUE_SEPARATOR
                    )
                    values[-1] = "32768"
                    row["http3.headers.header.value"] = (
                        summary.PRIVATE_VALUE_SEPARATOR.join(values)
                    )
            with response_headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=response_rows[0].keys())
                writer.writeheader()
                writer.writerows(response_rows)
            with self.assertRaisesRegex(
                ValueError,
                "tree asset content-lengths differ between complete and early-overlap",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    destination,
                    "4433",
                    ("tree-complete", "tree-early-overlap"),
                )

    def test_tree_root_overlap_keeps_wire_overlap_report_only(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(8, 0.010, "client", 4, method="GET"),
                self.event(9, 0.011, "client", 8, method="GET"),
                self.event(10, 0.012, "server", 0, status="200"),
                self.event(11, 0.013, "server", 4, status="200"),
                self.event(12, 0.014, "server", 8, status="200"),
                self.event(13, 0.015, "client", 12, method="CONNECT"),
                self.event(14, 0.016, "server", 12, status="200"),
            ]
            self.make_cohort(directory, "tree-complete", common)
            # Every resource FIN precedes CONNECT. The new arm remains valid:
            # wire overlap is an outcome, not a selection predicate.
            self.make_cohort(directory, "tree-root-overlap", common)
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("tree-complete", "tree-root-overlap"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn("tree-root-overlap_overlap_observed=no", safe_summary)
            self.assertIn(
                "tree-complete_asset_content_lengths=16384,8192", safe_summary
            )
            self.assertIn(
                "tree-root-overlap_asset_content_lengths=16384,8192",
                safe_summary,
            )
            self.assertIn("tree_root_overlap_request_semantics_match=yes", safe_summary)
            self.assertIn("tree_root_overlap_asset_sizes_match=yes", safe_summary)
            self.assertIn(
                "tree_root_overlap_wire_overlap_is_admission=no", safe_summary
            )
            lifecycle = Path(directory) / "decrypted-tree-root-overlap-lifecycle.csv"
            with lifecycle.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                lifecycle_rows = list(reader)
                lifecycle_fields = reader.fieldnames
            lifecycle_rows = [
                row for row in lifecycle_rows if row["quic.stream.stream_id"] != "8"
            ]
            with lifecycle.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=lifecycle_fields)
                writer.writeheader()
                writer.writerows(lifecycle_rows)
            missing_fin_destination = Path(directory) / "summary-missing-fin.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events-missing-fin.csv",
                missing_fin_destination,
                "4433",
                ("tree-complete", "tree-root-overlap"),
            )
            self.assertIn(
                "tree_root_overlap_wire_overlap_is_admission=no",
                missing_fin_destination.read_text(encoding="utf-8"),
            )

    def test_tree_root_overlap_css_pairs_one_asset_and_keeps_overlap_report_only(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(8, 0.010, "client", 4, method="GET"),
                self.event(10, 0.012, "server", 0, status="200"),
                self.event(11, 0.013, "server", 4, status="200"),
                self.event(13, 0.015, "client", 8, method="CONNECT"),
                self.event(14, 0.016, "server", 8, status="200"),
            ]
            self.make_cohort(directory, "tree-complete-css", common)
            self.make_cohort(directory, "tree-root-overlap-css", common)
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("tree-complete-css", "tree-root-overlap-css"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn("tree-root-overlap-css_outer_get_count=2", safe_summary)
            self.assertIn("tree-root-overlap-css_overlap_observed=no", safe_summary)
            self.assertIn(
                "tree_root_overlap_css_request_semantics_match=yes",
                safe_summary,
            )
            self.assertIn("tree_root_overlap_css_asset_sizes_match=yes", safe_summary)
            self.assertIn(
                "tree_root_overlap_css_wire_overlap_is_admission=no",
                safe_summary,
            )

    def test_root_pmtud_control_pairs_identical_complete_root_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.012, "server", 0, status="200"),
                self.event(13, 0.015, "client", 4, method="CONNECT"),
                self.event(14, 0.016, "server", 4, status="200"),
            ]
            self.make_cohort(directory, "root", common)
            self.make_cohort(directory, "root-pmtud-control", common)
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                ("root", "root-pmtud-control"),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn(
                "root_pmtud_control_request_semantics_match=yes", safe_summary
            )
            self.assertIn("root_pmtud_control_response_size_match=yes", safe_summary)
            self.assertIn("root_pmtud_control_wire_pmtud_claim=no", safe_summary)

    def test_document_overlap_pairs_identical_root_and_keeps_fin_report_only(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            common = [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(10, 0.012, "server", 0, status="200"),
                self.event(13, 0.015, "client", 4, method="CONNECT"),
                self.event(14, 0.016, "server", 4, status="200"),
            ]
            self.make_cohort(directory, "document-complete", common)
            self.make_cohort(
                directory,
                "document-overlap",
                common,
                fin_frames={"0": 16},
            )
            self.make_cohort(
                directory,
                "document-start-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="CONNECT"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(14, 0.016, "server", 4, status="200"),
                ],
                fin_frames={"0": 16},
            )
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                Path(directory) / "events.csv",
                destination,
                "4433",
                (
                    "document-complete",
                    "document-overlap",
                    "document-start-overlap",
                ),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn("document_overlap_request_semantics_match=yes", safe_summary)
            self.assertIn("document_overlap_response_size_match=yes", safe_summary)
            self.assertIn("document_overlap_wire_overlap_is_admission=no", safe_summary)
            self.assertIn("document-overlap_overlap_observed=yes", safe_summary)
            self.assertIn(
                "document_start_overlap_request_semantics_match=yes", safe_summary
            )
            self.assertIn(
                "document_start_overlap_response_size_match=yes", safe_summary
            )
            self.assertIn(
                "document_start_overlap_get_before_connect=yes", safe_summary
            )

    def test_tree_arms_reject_different_selected_header_values(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
                fin_frames={"4": 15},
            )
            headers = Path(directory) / "decrypted-tree-overlap-get-header-values.csv"
            with headers.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                if row["frame.number"] == "8":
                    names = row["http3.header.header.name"].split(
                        summary.PRIVATE_VALUE_SEPARATOR
                    )
                    values = row["http3.headers.header.value"].split(
                        summary.PRIVATE_VALUE_SEPARATOR
                    )
                    values[names.index(":path")] = "/camouflage/other.css"
                    row["http3.headers.header.value"] = (
                        summary.PRIVATE_VALUE_SEPARATOR.join(values)
                    )
            with headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(
                ValueError,
                "tree-overlap stylesheet expected request semantics differ",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete", "tree-overlap"),
                )
            for row in rows:
                if row["frame.number"] == "8":
                    names = row["http3.header.header.name"].split(
                        summary.PRIVATE_VALUE_SEPARATOR
                    )
                    values = row["http3.headers.header.value"].split(
                        summary.PRIVATE_VALUE_SEPARATOR
                    )
                    values[names.index(":path")] = "/camouflage/style.css"
                    values[names.index("priority")] = "u=7"
                    row["http3.headers.header.value"] = (
                        summary.PRIVATE_VALUE_SEPARATOR.join(values)
                    )
            with headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(
                ValueError,
                "stylesheet GET selected header values/order differ",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete", "tree-overlap"),
                )

    def test_tree_arm_rejects_unexpected_fetch_site_and_referer(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_tree_complete_control(directory)
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
                fin_frames={"4": 15},
            )
            headers = Path(directory) / "decrypted-tree-overlap-get-header-values.csv"
            with headers.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                if row["frame.number"] != "7":
                    continue
                names = row["http3.header.header.name"].split(
                    summary.PRIVATE_VALUE_SEPARATOR
                )
                values = row["http3.headers.header.value"].split(
                    summary.PRIVATE_VALUE_SEPARATOR
                )
                values[names.index("sec-fetch-site")] = "same-origin"
                row["http3.headers.header.value"] = (
                    summary.PRIVATE_VALUE_SEPARATOR.join(values)
                )
            with headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(
                ValueError,
                "tree-overlap root expected request semantics differ",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete", "tree-overlap"),
                )
            for row in rows:
                names = row["http3.header.header.name"].split(
                    summary.PRIVATE_VALUE_SEPARATOR
                )
                values = row["http3.headers.header.value"].split(
                    summary.PRIVATE_VALUE_SEPARATOR
                )
                if row["frame.number"] == "7":
                    values[names.index("sec-fetch-site")] = "none"
                elif row["frame.number"] == "8":
                    values[names.index("referer")] = (
                        f"https://{PRIVATE_SEMANTIC_MARKER}/wrong-root"
                    )
                row["http3.headers.header.value"] = (
                    summary.PRIVATE_VALUE_SEPARATOR.join(values)
                )
            with headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(
                ValueError,
                "stylesheet GET referer does not equal the computed root URL",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete", "tree-overlap"),
                )

    def test_tree_overlap_requires_control_root_and_asset_size_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_tree_complete_control(directory)
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
                fin_frames={"4": 15},
            )
            response_headers = (
                Path(directory) / "decrypted-tree-overlap-response-header-values.csv"
            )
            with response_headers.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                if row["quic.stream.stream_id"] == "4":
                    values = row["http3.headers.header.value"].split(
                        summary.PRIVATE_VALUE_SEPARATOR
                    )
                    values[-1] = "32768"
                    row["http3.headers.header.value"] = (
                        summary.PRIVATE_VALUE_SEPARATOR.join(values)
                    )
            with response_headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(
                ValueError,
                "tree asset content-lengths differ between complete and overlap",
            ):
                summary.write_outputs(
                    Path(directory),
                    Path(directory) / "events.csv",
                    Path(directory) / "summary.txt",
                    "4433",
                    ("tree-complete", "tree-overlap"),
                )

    def test_private_semantics_align_actual_multiplexed_header_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            separator = summary.PRIVATE_VALUE_SEPARATOR
            request_fields = [
                "frame.number",
                "udp.srcport",
                "udp.dstport",
                "quic.connection.number",
                "quic.stream.stream_id",
                "http3.headers.method",
                "http3.header.header.name",
                "http3.headers.header.value",
            ]
            root_url = f"https://{PRIVATE_SEMANTIC_MARKER}/camouflage/index.html"

            def request_block(path, site, mode, destination, referer=""):
                names = [":method", ":scheme", ":authority", ":path"]
                values = ["GET", "https", PRIVATE_SEMANTIC_MARKER, path]
                if referer:
                    names.append("referer")
                    values.append(referer)
                names.extend(("sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest"))
                values.extend((site, mode, destination))
                return names, values

            root_names, root_values = request_block(
                "/camouflage/index.html", "none", "navigate", "document"
            )
            script_names, script_values = request_block(
                "/camouflage/app.js",
                "same-origin",
                "no-cors",
                "script",
                root_url,
            )
            style_names, style_values = request_block(
                "/camouflage/style.css",
                "same-origin",
                "no-cors",
                "style",
                root_url,
            )
            self.write_csv(
                directory,
                "tree-overlap",
                "get-header-values",
                request_fields,
                [
                    {
                        "frame.number": "7",
                        "udp.srcport": "55000",
                        "udp.dstport": "4433",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": "0",
                        "http3.headers.method": "GET",
                        "http3.header.header.name": separator.join(root_names),
                        "http3.headers.header.value": separator.join(root_values),
                    },
                    {
                        "frame.number": "8",
                        "udp.srcport": "55000",
                        "udp.dstport": "4433",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": separator.join(("8", "4")),
                        "http3.headers.method": separator.join(("GET", "GET")),
                        "http3.header.header.name": separator.join((
                            *script_names,
                            *style_names,
                        )),
                        "http3.headers.header.value": separator.join((
                            *script_values,
                            *style_values,
                        )),
                    },
                ],
            )
            semantics = summary.read_get_request_semantics(
                Path(directory), "tree-overlap", "4433"
            )
            summary.validate_expected_get_request_semantics("tree-overlap", semantics)
            self.assertEqual(
                dict(semantics["stylesheet"])[":path"],
                "/camouflage/style.css",
            )
            self.assertEqual(dict(semantics["script"])[":path"], "/camouflage/app.js")

            response_fields = [
                "frame.number",
                "udp.srcport",
                "udp.dstport",
                "quic.connection.number",
                "quic.stream.stream_id",
                "http3.headers.status",
                "http3.header.header.name",
                "http3.headers.header.value",
            ]
            self.write_csv(
                directory,
                "tree-overlap",
                "response-header-values",
                response_fields,
                [
                    {
                        "frame.number": "12",
                        "udp.srcport": "4433",
                        "udp.dstport": "55000",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": separator.join(("8", "4")),
                        "http3.headers.status": separator.join(("200", "200")),
                        "http3.header.header.name": separator.join((
                            ":status",
                            "content-length",
                            ":status",
                            "content-length",
                        )),
                        "http3.headers.header.value": separator.join((
                            "200",
                            "33",
                            "200",
                            "22",
                        )),
                    }
                ],
            )
            self.assertEqual(
                summary.read_response_content_lengths(
                    Path(directory), "tree-overlap", "4433"
                ),
                {"8": 33, "4": 22},
            )

    def test_private_response_fails_closed_when_data_stream_makes_mapping_ambiguous(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            separator = summary.PRIVATE_VALUE_SEPARATOR
            self.write_csv(
                directory,
                "tree-overlap",
                "response-header-values",
                [
                    "frame.number",
                    "udp.srcport",
                    "udp.dstport",
                    "quic.connection.number",
                    "quic.stream.stream_id",
                    "http3.headers.status",
                    "http3.header.header.name",
                    "http3.headers.header.value",
                ],
                [
                    {
                        "frame.number": "24",
                        "udp.srcport": "4433",
                        "udp.dstport": "55000",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": separator.join(("4", "8")),
                        "http3.headers.status": "200",
                        "http3.header.header.name": separator.join((
                            ":status",
                            "content-length",
                        )),
                        "http3.headers.header.value": separator.join(("200", "22")),
                    }
                ],
            )
            with self.assertRaisesRegex(
                ValueError, "response status/stream cardinality is ambiguous"
            ):
                summary.read_response_content_lengths(
                    Path(directory), "tree-overlap", "4433"
                )

    def test_private_response_maps_new_headers_beside_known_data_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            separator = summary.PRIVATE_VALUE_SEPARATOR
            fields = [
                "frame.number",
                "udp.srcport",
                "udp.dstport",
                "quic.connection.number",
                "quic.stream.stream_id",
                "http3.headers.status",
                "http3.header.header.name",
                "http3.headers.header.value",
            ]
            self.write_csv(
                directory,
                "tree-overlap",
                "response-header-values",
                fields,
                [
                    {
                        "frame.number": "20",
                        "udp.srcport": "4433",
                        "udp.dstport": "55000",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": "4",
                        "http3.headers.status": "200",
                        "http3.header.header.name": separator.join((
                            ":status",
                            "content-length",
                        )),
                        "http3.headers.header.value": separator.join(("200", "11")),
                    },
                    {
                        "frame.number": "40",
                        "udp.srcport": "4433",
                        "udp.dstport": "55000",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": separator.join(("8", "4")),
                        "http3.headers.status": "200",
                        "http3.header.header.name": separator.join((
                            ":status",
                            "content-length",
                        )),
                        "http3.headers.header.value": separator.join(("200", "22")),
                    },
                ],
            )
            self.assertEqual(
                summary.read_response_content_lengths(
                    Path(directory), "tree-overlap", "4433"
                ),
                {"4": 11, "8": 22},
            )

    def test_private_response_recovers_tshark_quoted_etag_csv_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            separator = summary.PRIVATE_VALUE_SEPARATOR
            self.write_csv(
                directory,
                "tree-complete-css",
                "response-header-values",
                [
                    "frame.number",
                    "udp.srcport",
                    "udp.dstport",
                    "quic.connection.number",
                    "quic.stream.stream_id",
                    "http3.headers.status",
                    "http3.header.header.name",
                    "http3.headers.header.value",
                ],
                [
                    {
                        "frame.number": "21",
                        "udp.srcport": "4433",
                        "udp.dstport": "55000",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": "4",
                        "http3.headers.status": "200",
                        "http3.header.header.name": separator.join((
                            ":status",
                            "etag",
                            "content-length",
                        )),
                        # This is the exact value shape csv.DictReader returns
                        # after tshark fails to double the ETag's RFC quotes.
                        "http3.headers.header.value": separator.join((
                            "200",
                            'naivefox-style-deadbeef"',
                            '65536"',
                        )),
                    }
                ],
            )
            self.assertEqual(
                summary.read_response_content_lengths(
                    Path(directory), "tree-complete-css", "4433"
                ),
                {"4": 65536},
            )

    def test_private_response_ignores_ambiguous_non_sized_connect_block(self):
        with tempfile.TemporaryDirectory() as directory:
            separator = summary.PRIVATE_VALUE_SEPARATOR
            self.write_csv(
                directory,
                "tree-overlap",
                "response-header-values",
                [
                    "frame.number",
                    "udp.srcport",
                    "udp.dstport",
                    "quic.connection.number",
                    "quic.stream.stream_id",
                    "http3.headers.status",
                    "http3.header.header.name",
                    "http3.headers.header.value",
                ],
                [
                    {
                        "frame.number": "24",
                        "udp.srcport": "4433",
                        "udp.dstport": "55000",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": separator.join(("20", "16")),
                        "http3.headers.status": "200",
                        "http3.header.header.name": separator.join((
                            ":status",
                            "padding",
                        )),
                        "http3.headers.header.value": separator.join(("200", "x")),
                    }
                ],
            )
            self.assertEqual(
                summary.read_response_content_lengths(
                    Path(directory), "tree-overlap", "4433"
                ),
                {},
            )

    def test_stream_fin_mapping_is_per_occurrence_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.012, "server", 0, status="200"),
                    self.event(11, 0.013, "server", 4, status="200"),
                    self.event(12, 0.014, "server", 8, status="200"),
                    self.event(13, 0.015, "client", 12, method="CONNECT"),
                    self.event(14, 0.016, "server", 12, status="200"),
                ],
            )
            lifecycle = Path(directory) / "decrypted-tree-overlap-lifecycle.csv"
            with lifecycle.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
                fields = rows[0].keys()
            multiplexed = dict(rows[0])
            multiplexed.update({
                "frame.number": "20",
                "udp.srcport": "4433",
                "udp.dstport": "55000",
                "quic.connection.number": "0",
                "quic.stream.stream_id": "4;8",
                "quic.stream.fin": "0;1",
            })
            with lifecycle.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(multiplexed)

            events, _connections, _hellos = summary.summarize_cohort(
                Path(directory), "tree-overlap", "4433"
            )
            responses = {
                row["stream_id"]: row
                for row in events
                if row["direction"] == "server" and row["status"] == "200"
            }
            self.assertEqual(responses["4"]["stream_fin_packet_position"], "")
            self.assertEqual(responses["8"]["stream_fin_packet_position"], 20)

            multiplexed["quic.stream.fin"] = "1"
            with lifecycle.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(multiplexed)
            with self.assertRaisesRegex(
                ValueError, "stream FIN/stream cardinality is ambiguous"
            ):
                summary.summarize_cohort(Path(directory), "tree-overlap", "4433")

    def test_private_get_semantics_fail_closed_on_multiplexed_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            fields = [
                "frame.number",
                "udp.srcport",
                "udp.dstport",
                "quic.connection.number",
                "quic.stream.stream_id",
                "http3.headers.method",
                "http3.header.header.name",
                "http3.headers.header.value",
            ]
            separator = summary.PRIVATE_VALUE_SEPARATOR
            self.write_csv(
                directory,
                "tree-overlap",
                "get-header-values",
                fields,
                [
                    {
                        "frame.number": "8",
                        "udp.srcport": "55000",
                        "udp.dstport": "4433",
                        "quic.connection.number": "0",
                        "quic.stream.stream_id": f"4{separator}8",
                        "http3.headers.method": "GET",
                        "http3.header.header.name": separator.join((
                            ":method",
                            ":scheme",
                            ":authority",
                            ":path",
                        )),
                        "http3.headers.header.value": separator.join((
                            "GET",
                            "https",
                            PRIVATE_SEMANTIC_MARKER,
                            "/asset",
                        )),
                    }
                ],
            )
            with self.assertRaisesRegex(
                ValueError, "method/stream cardinality is ambiguous"
            ):
                summary.read_get_request_semantics(
                    Path(directory), "tree-overlap", "4433"
                )

    def write_csv(self, directory, cohort, suffix, fieldnames, rows):
        path = os.path.join(directory, f"decrypted-{cohort}-{suffix}.csv")
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def make_tree_complete_control(self, directory):
        self.make_cohort(
            directory,
            "tree-complete",
            [
                self.event(7, 0.009, "client", 0, method="GET"),
                self.event(8, 0.010, "client", 4, method="GET"),
                self.event(9, 0.011, "client", 8, method="GET"),
                self.event(10, 0.012, "server", 0, status="200"),
                self.event(11, 0.013, "server", 4, status="200"),
                self.event(12, 0.014, "server", 8, status="200"),
                self.event(17, 0.019, "client", 12, method="CONNECT"),
                self.event(18, 0.020, "server", 12, status="200"),
            ],
        )

    def make_confirmed_evidence(self, directory, handshake_frame):
        self.write_csv(
            directory,
            "document-handshake-confirmed",
            "handshake-done",
            [
                "frame.number",
                "frame.time_relative",
                "udp.srcport",
                "udp.dstport",
                "quic.connection.number",
                "quic.frame_type",
            ],
            [
                {
                    "frame.number": str(handshake_frame),
                    "frame.time_relative": "0.008",
                    "udp.srcport": "4433",
                    "udp.dstport": "55000",
                    "quic.connection.number": "0",
                    "quic.frame_type": "0x1e",
                }
            ],
        )
        lifecycle = Path(directory) / (
            "decrypted-document-handshake-confirmed-private-lifecycle.moz_log"
        )
        lifecycle.write_text(
            "h3.preamble_confirm_gate action=wait session=0x111 ci=0x222 "
            "transport_confirmed=0\n"
            "h3.state session=0x111 ci=0x333 from=0 to=2 cause=connected\n"
            "h3.transport_confirmation action=observed session=0x111 ci=0x222 "
            "transport_confirmed=1\n"
            "h3.preamble_confirm_gate action=release session=0x111 ci=0x222 "
            "transport_confirmed=1\n",
            encoding="utf-8",
        )

    def make_carrier_dispatch_evidence(self, directory):
        lifecycle = Path(directory) / (
            "decrypted-document-carrier-dispatch-private-lifecycle.moz_log"
        )
        lifecycle.write_text(
            "h3.carrier_dispatch action=carrier-created gate=0x555 "
            "carrier=0x222 ci=0x444 use_he=0 fetch_https_rr=0 parallel_limit=1\n"
            "h3.carrier_dispatch action=document-configured gate=0x555 "
            "carrier=0x222 document=0x333 ci=0x444 caps=00000001\n"
            "h3.carrier_dispatch action=carrier-establishment-start gate=0x555 "
            "carrier=0x222 ci=0x444\n"
            "h3.carrier_dispatch action=document-waiting gate=0x555 "
            "carrier=0x222 document=0x333 carrier_complete=0\n"
            "h3.carrier_dispatch action=carrier-activated connection=0x666 "
            "session=0x111 carrier=0x222 connected=0\n"
            "h3.state session=0x111 ci=0x777 from=0 to=2 cause=connected\n"
            "h3.carrier_dispatch action=connection-connected connection=0x666 "
            "session=0x111\n"
            "h3.carrier_dispatch action=document-waiting gate=0x555 "
            "carrier=0x222 document=0x333 carrier_complete=0 path=spdy-pending\n"
            "h3.carrier_dispatch action=carrier-read-complete gate=0x555 "
            "carrier=0x222 request_bytes=0 result=base-stream-closed rv=80470002\n"
            "h3.carrier_dispatch action=carrier-complete gate=0x555 "
            "carrier=0x222 result=00000000 carrier_read_complete=1\n"
            "h3.carrier_dispatch action=document-normal-dispatch gate=0x555 "
            "carrier=0x222 document=0x333 carrier_complete=1 path=spdy-pending\n"
            "h3.carrier_dispatch action=document-activated gate=0x555 "
            "carrier=0x222 connection=0x666 session=0x111 document=0x333 "
            "connected=1 wildcard=1\n"
            "h3.carrier_dispatch action=document-attached gate=0x555 "
            "carrier=0x222 session=0x111 document=0x333 via=add-stream "
            "carrier_complete=1\n"
            "h3.carrier_dispatch action=document-headers-emitted gate=0x555 "
            "carrier=0x222 session=0x111 document=0x333 stream_id=0 "
            "carrier_complete=1\n",
            encoding="utf-8",
        )

    def make_cohort(self, directory, cohort, requests, fin_frames=None):
        fin_frames = fin_frames or {}
        request_fields = [
            "frame.number",
            "frame.time_relative",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.stream.stream_id",
            "http3.headers.method",
            "http3.headers.status",
        ]
        header_fields = [
            "frame.number",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.stream.stream_id",
            "http3.header.header.name",
        ]
        packet_fields = [
            "frame.number",
            "frame.time_relative",
            "udp.srcport",
            "udp.dstport",
            "udp.length",
            "quic.connection.number",
            "quic.version",
            "quic.long.packet_type",
            "quic.dcil",
            "quic.scil",
            "quic.packet_number",
            "quic.packet_length",
        ]
        lifecycle_fields = [
            "frame.number",
            "frame.time_relative",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.frame_type",
            "quic.rsts.stream_id",
            "quic.rsts.application_error_code",
            "quic.rsts.final_size",
            "quic.ss.stream_id",
            "quic.ss.application_error_code",
            "quic.stream.stream_id",
            "quic.stream.fin",
            "quic.cc.error_code",
            "quic.cc.error_code.app",
        ]
        self.write_csv(directory, cohort, "requests", request_fields, requests)
        headers = []
        for row in requests:
            if row["http3.headers.method"] == "CONNECT":
                names = ":method;:authority;proxy-authorization;padding"
            elif row["http3.headers.method"]:
                block = ":method;:scheme;:authority;:path;user-agent"
                if cohort == "reference":
                    block += ";alt-used"
                names = ";".join(block for _ in row["http3.headers.method"].split(";"))
            else:
                names = ";".join(
                    ":status;content-length;padding"
                    for _ in row["http3.headers.status"].split(";")
                    if _
                )
            headers.append({
                "frame.number": row["frame.number"],
                "udp.srcport": row["udp.srcport"],
                "udp.dstport": row["udp.dstport"],
                "quic.connection.number": row["quic.connection.number"],
                "quic.stream.stream_id": row["quic.stream.stream_id"],
                "http3.header.header.name": names,
            })
        self.write_csv(directory, cohort, "header-names", header_fields, headers)
        private_header_fields = [
            "frame.number",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.stream.stream_id",
            "http3.headers.method",
            "http3.header.header.name",
            "http3.headers.header.value",
        ]
        get_rows = [
            row
            for row in requests
            if row["http3.headers.method"] == "GET" and row["udp.dstport"] == "4433"
        ]
        semantic_rows = []
        paths = (
            "/camouflage/index.html",
            "/camouflage/style.css",
            "/camouflage/app.js",
        )
        destinations = ("document", "style", "script")
        modes = ("navigate", "no-cors", "no-cors")
        sites = ("none", "same-origin", "same-origin")
        for index, row in enumerate(get_rows):
            names = [":method", ":scheme", ":authority", ":path"]
            values = ["GET", "https", PRIVATE_SEMANTIC_MARKER, paths[index]]
            if index:
                names.append("referer")
                values.append(
                    f"https://{PRIVATE_SEMANTIC_MARKER}/camouflage/index.html"
                )
            names.extend([
                "sec-fetch-dest",
                "sec-fetch-mode",
                "sec-fetch-site",
                "priority",
            ])
            values.extend([
                destinations[index],
                modes[index],
                sites[index],
                f"u={index}",
            ])
            semantic_rows.append({
                "frame.number": row["frame.number"],
                "udp.srcport": row["udp.srcport"],
                "udp.dstport": row["udp.dstport"],
                "quic.connection.number": row["quic.connection.number"],
                "quic.stream.stream_id": row["quic.stream.stream_id"],
                "http3.headers.method": "GET",
                "http3.header.header.name": summary.PRIVATE_VALUE_SEPARATOR.join(names),
                "http3.headers.header.value": summary.PRIVATE_VALUE_SEPARATOR.join(
                    values
                ),
            })
        self.write_csv(
            directory,
            cohort,
            "get-header-values",
            private_header_fields,
            semantic_rows,
        )
        response_header_fields = [
            "frame.number",
            "udp.srcport",
            "udp.dstport",
            "quic.connection.number",
            "quic.stream.stream_id",
            "http3.headers.status",
            "http3.header.header.name",
            "http3.headers.header.value",
        ]
        response_sizes = {
            row["quic.stream.stream_id"]: size
            for row, size in zip(get_rows, ("4096", "16384", "8192"))
        }
        response_semantics = []
        for row in requests:
            if row["http3.headers.status"] != "200":
                continue
            names = [":status"]
            values = ["200"]
            size = response_sizes.get(row["quic.stream.stream_id"])
            if size:
                names.append("content-length")
                values.append(size)
            response_semantics.append({
                "frame.number": row["frame.number"],
                "udp.srcport": row["udp.srcport"],
                "udp.dstport": row["udp.dstport"],
                "quic.connection.number": row["quic.connection.number"],
                "quic.stream.stream_id": row["quic.stream.stream_id"],
                "http3.headers.status": "200",
                "http3.header.header.name": summary.PRIVATE_VALUE_SEPARATOR.join(names),
                "http3.headers.header.value": summary.PRIVATE_VALUE_SEPARATOR.join(
                    values
                ),
            })
        self.write_csv(
            directory,
            cohort,
            "response-header-values",
            response_header_fields,
            response_semantics,
        )
        self.write_csv(
            directory,
            cohort,
            "packets",
            packet_fields,
            [
                {
                    "frame.number": "1",
                    "frame.time_relative": "0.000",
                    "udp.srcport": "55000",
                    "udp.dstport": "4433",
                    "udp.length": "1200",
                    "quic.connection.number": "0",
                    "quic.version": "0x00000001",
                    "quic.long.packet_type": "0",
                    "quic.dcil": "8",
                    "quic.scil": "8",
                    "quic.packet_number": "0",
                    "quic.packet_length": "1192",
                }
            ],
        )
        lifecycle = []
        for row in requests:
            if not row["http3.headers.status"]:
                continue
            fin_frame = fin_frames.get(
                str(row["quic.stream.stream_id"]), row["frame.number"]
            )
            lifecycle.append({
                "frame.number": str(fin_frame),
                "frame.time_relative": row["frame.time_relative"],
                "udp.srcport": row["udp.srcport"],
                "udp.dstport": row["udp.dstport"],
                "quic.connection.number": row["quic.connection.number"],
                "quic.frame_type": "0x08",
                "quic.rsts.stream_id": "",
                "quic.rsts.application_error_code": "",
                "quic.rsts.final_size": "",
                "quic.ss.stream_id": "",
                "quic.ss.application_error_code": "",
                "quic.stream.stream_id": row["quic.stream.stream_id"],
                "quic.stream.fin": ";".join(
                    "1" for _ in str(row["quic.stream.stream_id"]).split(";")
                ),
                "quic.cc.error_code": "",
                "quic.cc.error_code.app": "",
            })
        self.write_csv(directory, cohort, "lifecycle", lifecycle_fields, lifecycle)
        self.write_csv(
            directory,
            cohort,
            "clienthello",
            ["quic.connection.number"],
            [{"quic.connection.number": "0"}],
        )
        self.write_csv(
            directory,
            cohort,
            "unidirectional-streams",
            [
                "frame.number",
                "udp.srcport",
                "udp.dstport",
                "quic.connection.number",
                "quic.stream.stream_id",
                "http3.stream_uni_type",
            ],
            [
                {
                    "frame.number": "3",
                    "udp.srcport": "55000",
                    "udp.dstport": "4433",
                    "quic.connection.number": "0",
                    "quic.stream.stream_id": "2;6;10",
                    "http3.stream_uni_type": "0;2;3",
                }
            ],
        )
        self.write_csv(
            directory,
            cohort,
            "settings",
            [
                "quic.connection.number",
                "quic.stream.stream_id",
                "http3.settings.id",
                "http3.settings.qpack.max_table_capacity",
                "http3.settings.qpack.blocked_streams",
                "http3.settings.max_field_section_size",
                "http3.settings.extended_connect",
                "http3.settings.h3_datagram",
                "http3.settings.webtransport",
            ],
            [
                {
                    "quic.connection.number": "0",
                    "quic.stream.stream_id": "2",
                    "http3.settings.id": "1;7",
                    "http3.settings.qpack.max_table_capacity": "0",
                    "http3.settings.qpack.blocked_streams": "",
                    "http3.settings.max_field_section_size": "65536",
                    "http3.settings.extended_connect": "",
                    "http3.settings.h3_datagram": "",
                    "http3.settings.webtransport": "",
                }
            ],
        )

    @staticmethod
    def event(frame, time, direction, stream, method="", status=""):
        client = direction == "client"
        return {
            "frame.number": str(frame),
            "frame.time_relative": str(time),
            "udp.srcport": "55000" if client else "4433",
            "udp.dstport": "4433" if client else "55000",
            "quic.connection.number": "0",
            "quic.stream.stream_id": str(stream),
            "http3.headers.method": method,
            "http3.headers.status": status,
        }

    def test_writes_ordered_sanitized_arm_events(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            for arm in ("off", "gate"):
                self.make_cohort(
                    directory,
                    arm,
                    [
                        self.event(9, 0.012, "client", 0, method="CONNECT"),
                        self.event(13, 0.021, "server", 0, status="200"),
                    ],
                )
            self.make_cohort(
                directory,
                "root",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(10, 0.015, "server", 0, status="200"),
                    self.event(11, 0.017, "client", 4, method="CONNECT"),
                    self.event(14, 0.022, "server", 4, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.013, "server", 0, status="200"),
                    self.event(12, 0.015, "server", 4, status="200"),
                    self.event(14, 0.017, "server", 8, status="200"),
                    self.event(15, 0.018, "client", 12, method="CONNECT"),
                    self.event(18, 0.022, "server", 12, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "tree-overlap",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(8, 0.010, "client", 4, method="GET"),
                    self.event(9, 0.011, "client", 8, method="GET"),
                    self.event(10, 0.013, "server", 0, status="200"),
                    self.event(11, 0.014, "server", 4, status="200"),
                    self.event(12, 0.015, "server", 8, status="200"),
                    self.event(13, 0.016, "client", 12, method="CONNECT"),
                    self.event(14, 0.018, "server", 12, status="200"),
                ],
                fin_frames={"4": 15, "8": 16},
            )
            events = Path(directory) / "events.csv"
            destination = Path(directory) / "summary.txt"
            arms = ("off", "gate", "root", "tree-complete", "tree-overlap")
            summary.write_outputs(Path(directory), events, destination, "4433", arms)
            with events.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            root_requests = [
                row for row in rows if row["cohort"] == "root" and row["method"]
            ]
            self.assertEqual(
                [row["method"] for row in root_requests], ["GET", "CONNECT"]
            )
            self.assertEqual(
                root_requests[1]["header_name_order"],
                ":method;:authority;auth-or-cookie-redacted;padding",
            )
            serialized = events.read_text(encoding="utf-8")
            self.assertNotIn("proxy-authorization", serialized.lower())
            self.assertNotIn("fixture-pass", serialized)
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertNotIn(PRIVATE_SEMANTIC_MARKER, serialized)
            self.assertNotIn(PRIVATE_SEMANTIC_MARKER, safe_summary)
            self.assertIn("root_preamble_mode=document-complete", safe_summary)
            self.assertIn("reference_outer_client_hellos=1", safe_summary)
            self.assertIn("root_outer_client_hellos=1", safe_summary)
            self.assertIn("tree-complete_outer_get_count=3", safe_summary)
            self.assertIn("tree-complete_outer_client_hellos=1", safe_summary)
            self.assertIn(
                "tree-complete_connect_after_all_observed_server_fins=yes",
                safe_summary,
            )
            self.assertIn("tree-overlap_overlap_observed=yes", safe_summary)
            self.assertIn("tree_request_semantics_match=yes", safe_summary)
            self.assertIn("tree_expected_request_semantics=yes", safe_summary)
            self.assertIn(
                "tree-overlap_overlap_evidence="
                "server-fin-after-connect,resource-stream-spans-connect",
                safe_summary,
            )

    def test_accepts_document_complete_name_without_forcing_other_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "reference",
                [
                    self.event(8, 0.010, "client", 0, method="GET"),
                    self.event(12, 0.020, "server", 0, status="200"),
                ],
            )
            self.make_cohort(
                directory,
                "document-complete",
                [
                    self.event(7, 0.009, "client", 0, method="GET"),
                    self.event(10, 0.015, "server", 0, status="200"),
                    self.event(11, 0.017, "client", 4, method="CONNECT"),
                    self.event(14, 0.022, "server", 4, status="200"),
                ],
            )
            events = Path(directory) / "events.csv"
            destination = Path(directory) / "summary.txt"
            summary.write_outputs(
                Path(directory),
                events,
                destination,
                "4433",
                ("document-complete",),
            )
            safe_summary = destination.read_text(encoding="utf-8")
            self.assertIn("cohorts=reference,document-complete", safe_summary)
            self.assertNotIn("tree-complete", safe_summary)

    def test_splits_multiplexed_response_blocks_by_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "off",
                [
                    self.event(9, 0.012, "client", 12, method="CONNECT"),
                    self.event(10, 0.013, "client", 16, method="CONNECT"),
                    self.event(13, 0.021, "server", "16;12", status="200;200"),
                ],
            )
            rows, _, _ = summary.summarize_cohort(Path(directory), "off", "4433")
            responses = [row for row in rows if row["status"] == "200"]
            self.assertEqual(
                {(row["stream_id"], row["status"]) for row in responses},
                {("12", "200"), ("16", "200")},
            )

    def test_multiplexed_header_names_are_mapped_per_headers_block(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "off",
                [
                    self.event(9, 0.012, "client", "4;8", method="GET;CONNECT"),
                    self.event(13, 0.021, "server", "4;8", status="200;200"),
                ],
            )
            headers = Path(directory) / "decrypted-off-header-names.csv"
            with headers.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
                fields = rows[0].keys()
            rows[0]["http3.header.header.name"] = (
                ":method;:scheme;:authority;:path;user-agent;"
                ":method;:authority;proxy-authorization;padding"
            )
            with headers.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            events, _, _ = summary.summarize_cohort(Path(directory), "off", "4433")
            requests = {
                row["method"]: row
                for row in events
                if row["direction"] == "client" and row["method"]
            }
            self.assertNotIn("padding", requests["GET"]["header_name_set"].split(";"))
            self.assertIn("padding", requests["CONNECT"]["header_name_set"].split(";"))

    def test_unseen_data_stream_beside_headers_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "off",
                [
                    self.event(9, 0.012, "client", "4;8", method="CONNECT"),
                    self.event(13, 0.021, "server", 8, status="200"),
                ],
            )
            with self.assertRaisesRegex(
                ValueError, "HEADERS/stream mapping is ambiguous"
            ):
                summary.summarize_cohort(Path(directory), "off", "4433")

    def test_future_headers_stream_is_not_assigned_to_current_block(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "off",
                [
                    self.event(9, 0.012, "client", "4;8", method="GET"),
                    self.event(10, 0.013, "client", 8, method="CONNECT"),
                    self.event(13, 0.021, "server", 4, status="200"),
                    self.event(14, 0.022, "server", 8, status="200"),
                ],
            )
            rows, _, _ = summary.summarize_cohort(
                Path(directory), "off", "4433"
            )
            requests = {
                row["method"]: row
                for row in rows
                if row["direction"] == "client" and row["method"]
            }
            self.assertEqual(requests["GET"]["stream_id"], "4")
            self.assertEqual(requests["CONNECT"]["stream_id"], "8")

    def test_ambiguous_future_headers_row_does_not_force_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_cohort(
                directory,
                "off",
                [
                    self.event(9, 0.012, "client", "4;8", method="GET"),
                    self.event(10, 0.013, "client", "8;12", method="CONNECT"),
                    self.event(13, 0.021, "server", 4, status="200"),
                ],
            )
            with self.assertRaisesRegex(
                ValueError, "HEADERS/stream mapping is ambiguous"
            ):
                summary.summarize_cohort(Path(directory), "off", "4433")


if __name__ == "__main__":
    unittest.main()
