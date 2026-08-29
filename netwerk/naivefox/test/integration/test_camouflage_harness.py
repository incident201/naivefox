#!/usr/bin/env python3

import base64
import csv
import importlib.util
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote, urlsplit

HERE = os.path.dirname(__file__)
SOURCE_ROOT = os.path.abspath(os.path.join(HERE, "../../../.."))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE = load("camouflage_capture_health", "camouflage_capture_health.py")
CONFIG = load("camouflage_naivefox_config", "camouflage_naivefox_config.py")
CACHE = load("camouflage_cache_validation", "camouflage_cache_validation.py")
CONTROLLER = load("camouflage_browser_controller", "camouflage_browser_controller.py")
FEATURES = load("camouflage_features", "camouflage_features.py")
INNER_H2 = load("camouflage_inner_h2_validation", "camouflage_inner_h2_validation.py")
SAMPLE = load("camouflage_sample_validation", "camouflage_sample_validation.py")
SUPERBLOCKS = load("camouflage_superblocks", "camouflage_superblocks.py")
TARGET = load("target_server", "target_server.py")


def native_parser_process_lifecycle_lines():
    return [
        "Native activation process phase=child-running parent_pid=100 child_pid=200",
        "Native activation process phase=hello parent_pid=100 child_pid=200 "
        "cross_process=1 persistent=1",
        "Native activation process phase=background-child-ready pid=200",
        "Connection 7 preamble native-parser-process "
        "phase=physical-root-suspended channel=71 generation=5 parent_pid=100 "
        "protocol=h3",
        "Connection 7 preamble native-parser-process "
        "phase=root-registered request=51 generation=5 parent_pid=100 protocol=h3",
        "Native activation child phase=root-ready request=51 generation=5 pid=200",
        "Connection 7 preamble native-parser-process "
        "phase=root-ready-resume request=51 generation=5 parent_pid=100 protocol=h3",
        "Connection 7 preamble native-parser-process "
        "phase=root-data request=51 generation=5 sequence=1 bytes=100 "
        "parent_pid=100 protocol=h3",
        "Native activation child phase=root-data-accepted request=51 generation=5 "
        "sequence=1 bytes=100 pid=200 main_thread=1",
        "Native activation child phase=parser-feed request=51 generation=5 "
        "sequence=1 bytes=100 descriptors=1 status=0x00000000 pid=200 "
        "main_thread=0",
        "Native activation child phase=style-discovered root=51 generation=5 "
        "style=61 sequence=1 pid=200",
        "Connection 7 preamble native-parser-process "
        "phase=style-opened root=51 style=61 sequence=1 parent_pid=100 protocol=h3",
        "Connection 7 preamble native-parser-process "
        "phase=root-stop request=51 generation=5 sequence=2 status=0x00000000 "
        "parent_pid=100 protocol=h3",
        "Native activation child phase=root-stop-accepted request=51 generation=5 "
        "sequence=2 status=0x00000000 pid=200 main_thread=1",
        "Native activation child phase=parser-finish request=51 generation=5 "
        "sequence=2 bytes=100 descriptors=0 status=0x00000000 pid=200 "
        "main_thread=0",
        "Native activation child phase=parser-finished request=51 generation=5 "
        "sequence=2 bytes=100 styles=1 status=0x00000000 pid=200 main_thread=1",
        "Connection 7 preamble native-parser-process "
        "phase=parser-finished request=51 generation=5 sequence=2 bytes=100 "
        "styles=1 parent_pid=100 protocol=h3",
        "Native activation child phase=root-actor-destroyed request=51 generation=5 "
        "finished=1 reason=1 pid=200",
        "Connection 7 preamble native-parser-process "
        "phase=style-onstop-complete style=61 status=0x00000000 parent_pid=100 "
        "protocol=h3",
        "Native activation child phase=style-complete request=61 root=51 "
        "generation=5 pid=200 main_thread=1",
        "Native activation child phase=style-actor-destroyed request=61 root=51 "
        "generation=5 completed=1 reason=1 pid=200",
    ]


def native_parser_full_process_lifecycle_lines():
    lines = [
        line.replace("native-parser-process", "native-parser-full-process")
        for line in native_parser_process_lifecycle_lines()
    ]
    root_ready = lines.index(
        "Native activation child phase=root-ready request=51 generation=5 pid=200"
    )
    lines[root_ready + 1 : root_ready + 1] = [
        "Native activation process phase=full-root-primary-ready request=51 "
        "generation=5 parent_pid=100 child_pid=200",
        "Native activation process phase=full-root-background-ready request=51 "
        "generation=5 parent_pid=100 child_pid=200",
        "Native activation process phase=full-root-verification-queued request=51 "
        "generation=5 parent_pid=100 child_pid=200",
        "Native activation process phase=full-root-verification-run request=51 "
        "generation=5 parent_pid=100 child_pid=200",
        "Native activation process phase=full-root-onstart-forwarded request=51 "
        "generation=5 parent_pid=100 child_pid=200",
    ]
    style_discovered = lines.index(
        "Native activation child phase=style-discovered root=51 generation=5 "
        "style=61 sequence=1 pid=200"
    )
    lines[style_discovered + 1 : style_discovered + 1] = [
        "Native activation process phase=full-style-primary-ready request=51 "
        "generation=5 style=61 sequence=1 parent_pid=100 child_pid=200",
        "Native activation process phase=full-style-background-ready request=51 "
        "generation=5 style=61 sequence=1 parent_pid=100 child_pid=200",
        "Native activation process phase=full-style-join-released request=51 "
        "generation=5 style=61 sequence=1 parent_pid=100 child_pid=200",
    ]
    lines.append(
        "Native activation process phase=full-root-background-drained request=51 "
        "generation=5 canceled=0 parent_pid=100 child_pid=200"
    )
    return lines


class CamouflageHarnessTests(unittest.TestCase):
    def test_commandline_controller_records_group_sigterm_shutdown_race(self):
        controller = CONTROLLER.Controller(SimpleNamespace())
        controller.process = mock.Mock()
        controller.process.poll.return_value = -15

        controller.close()

        self.assertEqual(controller.shutdown_method, "controlled_sigterm")
        self.assertFalse(controller.forced_kill)
        controller.process.terminate.assert_not_called()

    def test_fixture_accepted_connections_require_tcp_nodelay(self):
        connection = mock.Mock()
        connection.getsockopt.return_value = 1
        TARGET.require_tcp_nodelay(connection)
        connection.setsockopt.assert_called_once_with(
            TARGET.socket.IPPROTO_TCP, TARGET.socket.TCP_NODELAY, 1
        )
        connection.getsockopt.assert_called_once_with(
            TARGET.socket.IPPROTO_TCP, TARGET.socket.TCP_NODELAY
        )

    def test_fixture_tcp_nodelay_verification_fails_closed(self):
        connection = mock.Mock()
        connection.getsockopt.return_value = 0
        with self.assertRaisesRegex(OSError, "did not enable TCP_NODELAY"):
            TARGET.require_tcp_nodelay(connection)

    def test_native_parser_process_validator_accepts_exact_lifecycle(self):
        SAMPLE.validate_native_parser_process(
            native_parser_process_lifecycle_lines(), expected_connection=7
        )

    def test_native_parser_process_validator_distinguishes_full_process(self):
        full_lines = native_parser_full_process_lifecycle_lines()
        SAMPLE.validate_native_parser_process(
            full_lines, expected_connection=7, expected_mode="full-process"
        )
        with self.assertRaisesRegex(ValueError, "mode marker differs"):
            SAMPLE.validate_native_parser_process(
                native_parser_process_lifecycle_lines(),
                expected_connection=7,
                expected_mode="full-process",
            )

        missing_background = [
            line
            for line in full_lines
            if "phase=full-style-background-ready" not in line
        ]
        with self.assertRaisesRegex(ValueError, "one marker per join phase"):
            SAMPLE.validate_native_parser_process(
                missing_background,
                expected_connection=7,
                expected_mode="full-process",
            )

    def test_native_parser_process_validator_rejects_unknown_or_partial_markers(self):
        lines = native_parser_process_lifecycle_lines()
        lines.insert(
            5,
            "Native activation child phase=unexpected request=51 generation=5 pid=200",
        )
        with self.assertRaisesRegex(ValueError, "child marker schema"):
            SAMPLE.validate_native_parser_process(lines, expected_connection=7)

        lines = native_parser_process_lifecycle_lines()
        lines[6] = lines[6].replace(" parent_pid=100", "")
        with self.assertRaisesRegex(ValueError, "parent marker schema"):
            SAMPLE.validate_native_parser_process(lines, expected_connection=7)

    def test_native_parser_process_validator_rejects_wrong_identity(self):
        with self.assertRaisesRegex(ValueError, "connection identity"):
            SAMPLE.validate_native_parser_process(
                native_parser_process_lifecycle_lines(), expected_connection=8
            )
        lines = native_parser_process_lifecycle_lines()
        lines[5] = lines[5].replace("pid=200", "pid=201")
        with self.assertRaisesRegex(ValueError, "child PID changed"):
            SAMPLE.validate_native_parser_process(lines, expected_connection=7)

    def test_native_parser_process_validator_rejects_false_descriptor_evidence(self):
        lines = native_parser_process_lifecycle_lines()
        lines[9] = lines[9].replace("descriptors=1", "descriptors=0")
        with self.assertRaisesRegex(ValueError, "descriptor provenance"):
            SAMPLE.validate_native_parser_process(lines, expected_connection=7)

    def test_native_parser_process_validator_rejects_early_actor_teardown(self):
        lines = native_parser_process_lifecycle_lines()
        root_destroyed = lines.pop(17)
        lines.insert(15, root_destroyed)
        with self.assertRaisesRegex(ValueError, "root actor teardown ordering"):
            SAMPLE.validate_native_parser_process(lines, expected_connection=7)

        lines = native_parser_process_lifecycle_lines()
        lines[-1] = lines[-1].replace("reason=1", "reason=4")
        with self.assertRaisesRegex(ValueError, "style actor died"):
            SAMPLE.validate_native_parser_process(lines, expected_connection=7)

    def test_full_process_product_callback_failure_is_route_local(self):
        path = os.path.join(
            SOURCE_ROOT,
            "netwerk/naivefox/NativeStylePreloadActivation.cpp",
        )
        with open(path, encoding="utf-8") as stream:
            source = stream.read()
        root = source[
            source.index(
                "nsresult ProcessServiceState::BackgroundRootOnStartForwarded("
            ) : source.index("nsresult ProcessServiceState::StyleDiscovered(")
        ]
        style = source[
            source.index(
                "nsresult ProcessServiceState::MaybeReleaseFullStyle("
            ) : source.index("void ProcessServiceState::RouteFailed(")
        ]
        for body in (root, style):
            self.assertIn("RouteFailed(", body)
            self.assertIn("return NS_OK;", body)
        self.assertNotIn("TransportFailed(", root)
        self.assertNotIn("TransportFailed(", style)

    def test_process_actor_completion_rejects_abnormal_destroy(self):
        with open(
            os.path.join(
                SOURCE_ROOT,
                "netwerk/naivefox/NativeStylePreloadProcessBackground.cpp",
            ),
            encoding="utf-8",
        ) as stream:
            background = stream.read()
        with open(
            os.path.join(
                SOURCE_ROOT,
                "netwerk/naivefox/NativeStylePreloadProcessBridge.cpp",
            ),
            encoding="utf-8",
        ) as stream:
            primary = stream.read()
        self.assertEqual(background.count("(mCompleted && cleanDelete)"), 2)
        self.assertEqual(
            primary.count("(aWhy != Deletion && aWhy != NormalShutdown)"),
            4,
        )

    def test_cold_listener_follows_network_and_socket_barriers(self):
        with open(
            os.path.join(SOURCE_ROOT, "netwerk/naivefox/GeckoRuntime.cpp"),
            encoding="utf-8",
        ) as stream:
            runtime = stream.read()
        with open(
            os.path.join(SOURCE_ROOT, "netwerk/naivefox/NaiveFoxRunner.cpp"),
            encoding="utf-8",
        ) as stream:
            runner = stream.read()

        wait_body = runtime[
            runtime.index(
                "nsresult GeckoRuntime::WaitForNetworkStartup()"
            ) : runtime.index("nsresult GeckoRuntime::RunEventLoopSmoke()")
        ]
        self.assertLess(
            wait_body.index('"NaiveFox::InitialNetworkState"'),
            wait_body.index('"NaiveFox::NetworkMainThreadBarrier"'),
        )
        self.assertLess(
            wait_body.index('"NaiveFox::NetworkMainThreadBarrier"'),
            wait_body.index('"NaiveFox::NetworkSocketThreadBarrier"'),
        )
        self.assertIn("WaitForStartupCondition", wait_body)
        self.assertIn("NS_NewTimerWithCallback", runtime)
        self.assertIn("InitialNetworkStateAllowsStartup", wait_body)
        self.assertIn(
            "terminalState, kAllowUnavailableNetworkMonitor", wait_body
        )
        self.assertIn(
            "#  ifdef ANDROID\n"
            "constexpr bool kAllowUnavailableNetworkMonitor = true;\n"
            "#  else\n"
            "constexpr bool kAllowUnavailableNetworkMonitor = false;",
            runtime,
        )
        self.assertIn("barrier.initial-monitor-unavailable", wait_body)
        self.assertIn("MOZ_TRY(WaitForNetworkStartup());", runtime)
        embedded = runner[
            runner.index("NaiveFoxRunEmbedded") : runner.index("NaiveFoxMain")
        ]
        self.assertLess(
            embedded.index("runtime.InitializeEmbedded("),
            embedded.index("RunLocalProxyServer("),
        )
        standalone_start = runner.index("const bool configMode")
        standalone = runner[
            standalone_start : runner.index("nsCString profile;", standalone_start)
        ]
        self.assertLess(
            standalone.index("runtime.Initialize("),
            standalone.index("RunLocalProxyServer("),
        )

    def test_post_start_network_change_still_invalidates_h3(self):
        with open(
            os.path.join(SOURCE_ROOT, "netwerk/protocol/http/nsHttpHandler.cpp"),
            encoding="utf-8",
        ) as stream:
            handler = stream.read()
        with open(
            os.path.join(SOURCE_ROOT, "netwerk/protocol/http/ConnectionEntry.cpp"),
            encoding="utf-8",
        ) as stream:
            entry = stream.read()
        with open(
            os.path.join(SOURCE_ROOT, "netwerk/test/unit/test_http3_network_change.js"),
            encoding="utf-8",
        ) as stream:
            regression = stream.read()

        observe = handler[handler.index("!strcmp(topic, NS_NETWORK_LINK_TOPIC)") :]
        self.assertIn("mConnMgr->VerifyTraffic()", observe)
        verify = entry[
            entry.index("void ConnectionEntry::VerifyTraffic()") : entry.index(
                "void ConnectionEntry::InsertIntoIdleConnections_internal"
            )
        ]
        self.assertIn("network_http_move_to_pending_list_after_network_change", verify)
        self.assertIn("MakeConnectionPendingAndDontReuse(connUDP)", verify)
        self.assertIn(
            'notifyObservers(null, "network:link-status-changed", "changed")',
            regression,
        )
        self.assertIn(
            '"network.http.move_to_pending_list_after_network_change",\n    true',
            regression,
        )

    def test_multi_arm_schedule_is_seeded_randomized_and_complete(self):
        first = SUPERBLOCKS.schedule_rows(1234, "h3", 4, ["initial", "page"])
        self.assertEqual(
            first,
            SUPERBLOCKS.schedule_rows(1234, "h3", 4, ["initial", "page"]),
        )
        self.assertNotEqual(
            first,
            SUPERBLOCKS.schedule_rows(4321, "h3", 4, ["initial", "page"]),
        )
        SUPERBLOCKS.validate_superblocks(first, expected_blocks=4)
        for index in range(4):
            members = first[index * 5 : (index + 1) * 5]
            self.assertEqual(len({row["experiment_block"] for row in members}), 1)
            self.assertEqual(len({row["scenario"] for row in members}), 1)

    def test_superblock_materialization_reuses_common_firefox_controls(self):
        fieldnames = [
            "schema_version",
            "protocol",
            "scenario",
            "label",
            "naivefox_arm",
            "session_id",
            "experiment_block",
            "whole_packet_count",
        ]
        rows = []
        for index, member in enumerate(
            SUPERBLOCKS.schedule_rows(9, "h2", 2, ["initial"])
        ):
            rows.append({
                "schema_version": "1",
                "protocol": "h2",
                "scenario": member["scenario"],
                "label": member["label"],
                "naivefox_arm": member["naivefox_arm"],
                "session_id": f"s{index}",
                "experiment_block": member["experiment_block"],
                "whole_packet_count": str(index),
            })
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "features-superblocks.csv")
            with open(source, "w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            outputs = SUPERBLOCKS.materialize_arms(
                source, os.path.join(directory, "arms"), expected_blocks=2
            )
            control_ids = None
            for arm, output in outputs.items():
                with open(output, newline="", encoding="utf-8") as stream:
                    selected = list(csv.DictReader(stream))
                self.assertEqual(len(selected), 6)
                self.assertEqual(
                    {row["experiment_block"] for row in selected},
                    {"h2_sb000000", "h2_sb000001"},
                )
                self.assertEqual(
                    {row["naivefox_arm"] for row in selected},
                    {"reference", arm},
                )
                current_controls = {
                    row["session_id"]
                    for row in selected
                    if row["naivefox_arm"] == "reference"
                }
                if control_ids is None:
                    control_ids = current_controls
                self.assertEqual(current_controls, control_ids)
                self.assertEqual(stat.S_IMODE(os.stat(output).st_mode), 0o600)

    def test_superblock_validation_rejects_missing_arm(self):
        rows = SUPERBLOCKS.schedule_rows(1, "h2", 1, ["initial"])
        rows = [row for row in rows if row["naivefox_arm"] != "root"]
        with self.assertRaisesRegex(ValueError, "incomplete superblock"):
            SUPERBLOCKS.validate_superblocks(
                rows, expected_blocks=1, arms=SUPERBLOCKS.DEFAULT_ARMS
            )

    def test_h2_proxy_floor_schedule_has_two_controls_and_two_candidates(self):
        arms = ("firefox-proxied", "off")
        rows = SUPERBLOCKS.schedule_rows(17, "h2", 3, ["browser_page"], arms=arms)
        SUPERBLOCKS.validate_superblocks(rows, expected_blocks=3, arms=arms)
        for index in range(3):
            members = rows[index * 4 : (index + 1) * 4]
            self.assertEqual(
                {(member["label"], member["naivefox_arm"]) for member in members},
                {
                    ("firefox_a", "reference"),
                    ("firefox_b", "reference"),
                    ("naivefox", "firefox-proxied"),
                    ("naivefox", "off"),
                },
            )
            self.assertEqual(
                {member["scenario"] for member in members}, {"browser_page"}
            )

    def test_navigation_stop_superblock_requires_document_start_control(self):
        treatment = "tree-native-parser-document-start-navigation-stop-css"
        control = "tree-native-parser-document-start-overlap-css"
        with self.assertRaisesRegex(ValueError, "requires the .* control"):
            SUPERBLOCKS.validate_arm_sequence(("root", treatment))
        self.assertEqual(
            SUPERBLOCKS.validate_arm_sequence((control, treatment)),
            (control, treatment),
        )

    def test_response_stop_superblock_requires_navigation_stop_control(self):
        document_start = "tree-native-parser-document-start-overlap-css"
        navigation_stop = "tree-native-parser-document-start-navigation-stop-css"
        response_stop = "tree-native-parser-document-start-response-stop-css"
        with self.assertRaisesRegex(ValueError, "requires the .* control"):
            SUPERBLOCKS.validate_arm_sequence((document_start, response_stop))
        self.assertEqual(
            SUPERBLOCKS.validate_arm_sequence((
                document_start,
                navigation_stop,
                response_stop,
            )),
            (document_start, navigation_stop, response_stop),
        )

    def test_resource_tree_is_browser_page_controlled_on_h2_and_h3(self):
        treatment = "tree-native-parser-document-start-resource-tree"
        control = "document-start-overlap"
        with self.assertRaisesRegex(ValueError, "requires the .* control"):
            SUPERBLOCKS.validate_arm_sequence(("root", treatment))
        with self.assertRaisesRegex(ValueError, "browser_page"):
            SUPERBLOCKS.schedule_rows(
                1, "h2", 1, ["initial"], arms=(control, treatment)
            )
        for protocol in ("h2", "h3"):
            rows = SUPERBLOCKS.schedule_rows(
                1, protocol, 1, ["browser_page"], arms=(control, treatment)
            )
            SUPERBLOCKS.validate_superblocks(
                rows, expected_blocks=1, arms=(control, treatment)
            )

    def test_http_connect_resource_tree_requires_matching_control(self):
        treatment = "tree-native-parser-resource-committed-page-http-connect"
        control = "document-start-http-connect"
        with self.assertRaisesRegex(ValueError, "requires the .* control"):
            SUPERBLOCKS.validate_arm_sequence(("root", treatment))
        with self.assertRaisesRegex(ValueError, "browser_page"):
            SUPERBLOCKS.schedule_rows(
                1, "h3", 1, ["initial"], arms=(control, treatment)
            )
        rows = SUPERBLOCKS.schedule_rows(
            1, "h3", 1, ["browser_page"], arms=(control, treatment)
        )
        SUPERBLOCKS.validate_superblocks(
            rows, expected_blocks=1, arms=(control, treatment)
        )

    def test_pipeline_parsers_accept_every_superblock_arm(self):
        parsers = (
            ("camouflage_features.py", ("extract", "--help"), set()),
            (
                "camouflage_sample_validation.py",
                ("--help",),
                {"firefox-proxied"},
            ),
        )
        for filename, arguments, excluded in parsers:
            with self.subTest(filename=filename):
                result = subprocess.run(
                    [sys.executable, os.path.join(HERE, filename), *arguments],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                for arm in set(SUPERBLOCKS.SUPPORTED_ARMS) - excluded:
                    self.assertIn(arm, result.stdout)

    def test_resource_tree_config_and_lifecycle_are_fail_closed(self):
        arm = "tree-native-parser-document-start-resource-tree"
        config = CONFIG.build_config(
            arm,
            "h2",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
            preamble_path="/camouflage/index.html?scenario=fronting_page",
        )
        self.assertEqual(
            config["preamble"],
            {
                "mode": "off",
                "h2-mode": arm,
                "path": "/camouflage/index.html?scenario=fronting_page",
                "max-assets": 3,
                "max-bytes": 128 * 1024,
                "cache-resources": True,
            },
        )
        h3_config = CONFIG.build_config(
            arm, "h3", 1080, 4433, "fixture-user", "fixture-pass"
        )
        self.assertEqual(h3_config["preamble"]["mode"], "off")
        self.assertEqual(h3_config["preamble"]["h3-mode"], arm)
        self.assertEqual(h3_config["preamble"]["max-assets"], 3)
        lines = [
            "Connection 7 preamble native-parser-resource-tree "
            "admission=request-committed request_committed=1 root_done=0 "
            "protocol=h2",
            "Connection 7 established target=localhost:443 outer=h2 padding=yes",
            "Preamble native-parser-preload lifecycle=chunk-flushed sequence=1 "
            "descriptors=4 status=0x00000000 generation=1 protocol=h2",
            "Preamble native-parser-resource-tree lifecycle=resource-opened "
            "stream=1 kind=style referrer=inherited protocol=h2",
            "Preamble native-parser-resource-tree lifecycle=resource-opened "
            "stream=2 kind=script referrer=inherited protocol=h2",
            "Preamble native-parser-resource-tree lifecycle=resource-opened "
            "stream=3 kind=image referrer=inherited protocol=h2",
            "Preamble native-parser-resource-tree lifecycle=resource-committed "
            "stream=1 status=waiting-for protocol=h2",
            "Preamble native-parser-resource-tree lifecycle=resource-committed "
            "stream=2 status=waiting-for protocol=h2",
            "Preamble native-parser-resource-tree lifecycle=resource-committed "
            "stream=3 status=waiting-for protocol=h2",
            "Connection 7 preamble result=success status=0x00000000 "
            "http=200 bytes=99 protocol=h2",
            "Connection 7 preamble native-parser-resource-tree drain=complete "
            "completed_resources=3 http=200 protocol=h2",
        ]
        features = {
            "protocol": "h2",
            "features": {
                "lifecycle_connection_count": 1.0,
                "tls_client_hello_count": 1.0,
            },
        }
        SAMPLE.validate_sample(arm, "h2", "\n".join(lines), features)
        mutated = [line.replace("kind=image", "kind=script") for line in lines]
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(arm, "h2", "\n".join(mutated), features)

    def test_resource_committed_page_requires_deferred_image_opens(self):
        arm = "tree-native-parser-resource-committed-page"
        http_arm = "tree-native-parser-resource-committed-page-http-connect"
        protocol = "h3"
        lines = [
            "Preamble native-parser-preload lifecycle=chunk-flushed sequence=1 "
            "descriptors=7 status=0x00000000 generation=1 protocol=h3",
            "Preamble native-parser-resource-tree lifecycle=resource-opened "
            "stream=1 kind=style referrer=inherited protocol=h3",
            "Preamble native-parser-resource-tree lifecycle=resource-opened "
            "stream=2 kind=script referrer=inherited protocol=h3",
        ]
        for stream in range(3, 7):
            lines.append(
                "Preamble native-parser-resource-tree "
                f"lifecycle=resource-prepared stream={stream} kind=image "
                "referrer=inherited protocol=h3"
            )
        for stream in range(3, 7):
            lines.append(
                "Preamble native-parser-resource-tree "
                f"lifecycle=deferred-resource-opened stream={stream} kind=image "
                "cause=next-main-turn protocol=h3"
            )
        for stream in range(1, 7):
            lines.append(
                "Preamble native-parser-resource-tree "
                f"lifecycle=resource-committed stream={stream} "
                "status=waiting-for protocol=h3"
            )
        lines.extend([
            "Preamble native-parser-resource-tree "
            "lifecycle=first-resource-body-buffer-consumed stream=1 protocol=h3",
            "Preamble native-parser-resource-tree "
            "barrier=first-resource-body-buffer assets=6 committed=6 protocol=h3",
            "Connection 7 preamble native-parser-resource-tree "
            "admission=resources-committed request_committed=1 root_done=1 "
            "protocol=h3",
            "Connection 7 preamble result=success status=0x00000000 "
            "http=200 bytes=99 protocol=h3",
            "Connection 7 established target=localhost:443 outer=h3 padding=yes",
            "Connection 7 preamble native-parser-resource-tree drain=complete "
            "completed_resources=6 http=200 protocol=h3",
        ])
        features = {
            "protocol": protocol,
            "features": {
                "lifecycle_connection_count": 1.0,
                "tls_client_hello_count": 1.0,
            },
        }
        SAMPLE.validate_sample(arm, protocol, "\n".join(lines), features)
        SAMPLE.validate_sample(http_arm, protocol, "\n".join(lines), features)
        prefixed_lines = [
            f"[0829/001452.366705:INFO:naivefox] {line}" for line in lines
        ]
        SAMPLE.validate_sample(
            arm, protocol, "\n".join(prefixed_lines), features
        )

        body_before_later_commits = list(lines)
        first_body = body_before_later_commits.pop(17)
        body_before_later_commits.insert(13, first_body)
        SAMPLE.validate_sample(
            arm, protocol, "\n".join(body_before_later_commits), features
        )

        body_before_own_commit = list(lines)
        first_body = body_before_own_commit.pop(17)
        body_before_own_commit.insert(11, first_body)
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                arm, protocol, "\n".join(body_before_own_commit), features
            )

        missing_first_body = [
            line
            for line in lines
            if "lifecycle=first-resource-body-buffer-consumed" not in line
        ]
        with self.assertRaisesRegex(ValueError, "configured resource opens"):
            SAMPLE.validate_sample(
                arm, protocol, "\n".join(missing_first_body), features
            )

        missing_open = [
            line
            for line in lines
            if "deferred-resource-opened stream=6" not in line
        ]
        with self.assertRaisesRegex(ValueError, "configured resource opens"):
            SAMPLE.validate_sample(
                arm, protocol, "\n".join(missing_open), features
            )

        wrong_lifecycle = [
            line.replace(
                "lifecycle=resource-prepared stream=3",
                "lifecycle=resource-opened stream=3",
            )
            for line in lines
        ]
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                arm, protocol, "\n".join(wrong_lifecycle), features
            )

    def test_opt_in_superblock_arms_share_one_control_pair(self):
        arms = (
            "gate",
            "root",
            "root-pmtud-control",
            "document-carrier-dispatch",
            "document-cold-winner-handoff",
            "document-handshake-confirmed",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
            "tree-native-parser-process-overlap-css",
            "tree-native-parser-full-process-overlap-css",
            "tree-overlap",
        )
        rows = SUPERBLOCKS.schedule_rows(17, "h3", 2, ["browser_page"], arms=arms)
        SUPERBLOCKS.validate_superblocks(rows, expected_blocks=2, arms=arms)
        self.assertEqual(SUPERBLOCKS.infer_arms(rows), arms)
        for index in range(2):
            block_size = 2 + len(arms)
            members = rows[index * block_size : (index + 1) * block_size]
            self.assertEqual(
                {(row["label"], row["naivefox_arm"]) for row in members},
                {
                    ("firefox_a", "reference"),
                    ("firefox_b", "reference"),
                    *(("naivefox", arm) for arm in arms),
                },
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h2",
                1,
                ["browser_page"],
                arms=("root", "root-pmtud-control"),
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h2",
                1,
                ["browser_page"],
                arms=("root", "document-handshake-confirmed"),
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h2",
                1,
                ["browser_page"],
                arms=("root", "document-carrier-dispatch"),
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h2",
                1,
                ["browser_page"],
                arms=("root", "document-cold-winner-handoff"),
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h2",
                1,
                ["browser_page"],
                arms=("root", "document-native-cache-open"),
            )
        with self.assertRaisesRegex(ValueError, "invalid multi-arm labels"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h3",
                1,
                ["browser_page"],
                arms=("root", "document-native-channel-open"),
            )
        with self.assertRaisesRegex(ValueError, "native parser arms require h3"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h2",
                1,
                ["browser_page"],
                arms=(
                    "root",
                    "tree-native-parser-root-rendezvous-overlap-css",
                    "tree-native-parser-process-overlap-css",
                    "tree-native-parser-full-process-overlap-css",
                ),
            )
        with self.assertRaisesRegex(ValueError, "root-rendezvous-overlap-css control"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h3",
                1,
                ["browser_page"],
                arms=("root", "tree-native-parser-process-overlap-css"),
            )
        with self.assertRaisesRegex(ValueError, "process-overlap-css control"):
            SUPERBLOCKS.schedule_rows(
                17,
                "h3",
                1,
                ["browser_page"],
                arms=("root", "tree-native-parser-full-process-overlap-css"),
            )

    def test_multi_arm_parser_rejects_alias_duplication(self):
        with self.assertRaisesRegex(ValueError, "aliases"):
            SUPERBLOCKS.parse_arms("gate,root,document-complete")

    def test_runner_preserves_single_arm_and_adds_same_base_superblocks(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("--multi-arm-superblocks", runner)
        self.assertIn("--multi-arm-arms", runner)
        self.assertIn("--multi-arm-views", runner)
        self.assertIn("multi_arm_arms_csv=off,gate,root", runner)
        self.assertIn("requires NAIVEFOX_CAPTURE_MODE=same-base", runner)
        self.assertIn("features-superblocks.csv", runner)
        self.assertIn("analyze-camouflage-arms.py", runner)
        self.assertIn("arm-comparison.json", runner)
        self.assertIn("arm-comparison.txt", runner)
        self.assertIn('for arm in "${multi_arm_arms[@]}"; do', runner)
        self.assertIn("analyzer_args+=(--screening-only)", runner)
        self.assertIn("metadata_arm_specific_analysis=screening_only", runner)
        self.assertIn('--views "$multi_arm_views_csv"', runner)
        self.assertIn(
            "H3 multi-arm screening requires a pre-launched Selenium browser",
            runner,
        )
        self.assertIn("effective_backend=selenium", runner)
        self.assertIn("--warmup-url", runner)
        self.assertIn('"https://127.0.0.1:$NAIVEFOX_FIXTURE_HTTPS_PORT/', runner)
        self.assertIn("bounded post-capture Firefox process-group SIGTERM", runner)
        self.assertNotIn('timeout 10 tail --pid="$controller_pid"', runner)
        self.assertIn('timeout 5 tail --pid="$controller_pid"', runner)
        self.assertIn('kill -TERM -- "-$controller_pid"', runner)
        self.assertIn("Firefox browser controller required SIGKILL", runner)
        self.assertIn("Firefox browser controller left process-group members", runner)
        self.assertIn(
            "document-handshake-confirmed multi-arm screening requires --protocol h3",
            runner,
        )
        self.assertIn(
            "document-carrier-dispatch multi-arm screening requires --protocol h3",
            runner,
        )
        self.assertIn(
            "document-cold-winner-handoff multi-arm screening requires --protocol h3",
            runner,
        )
        self.assertIn(
            "document-native-cache-open multi-arm screening requires --protocol h3",
            runner,
        )
        self.assertIn("packets_17_32", runner)
        self.assertNotIn(
            "document-native-channel-open multi-arm screening requires", runner
        )
        self.assertIn(
            "if [[ $participant == reference || $participant == naivefox ]]",
            runner,
        )
        for pref in (
            "browser.safebrowsing.realTime.enabled",
            "browser.safebrowsing.globalCache.enabled",
            "browser.safebrowsing.provider.google5.enabled",
        ):
            self.assertIn(f'user_pref("{pref}", false);', runner)
        self.assertIn(
            'validate_native_channel_fresh_cache "$profile" reference', runner
        )
        self.assertIn(
            'validate_native_channel_fresh_cache "$naivefox_profile" naivefox',
            runner,
        )
        self.assertIn("--scenario", runner)
        self.assertIn('scenarios=("$scenario_override")', runner)

    def test_runner_rejects_unknown_scenario_before_capture(self):
        result = subprocess.run(
            [
                "bash",
                os.path.join(HERE, "run-camouflage-suite.sh"),
                "--scenario",
                "not-a-workload",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported camouflage scenario", result.stderr)

    def test_private_h3_keylog_is_explicit_and_diagnostic_only(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("NAIVEFOX_CAPTURE_PRIVATE_H3_KEYLOG", runner)
        self.assertIn('local keylog="$sample_dir/naivefox.keys"', runner)
        self.assertIn("sslkeylog_unset=(-u SSLKEYLOGFILE)", runner)
        self.assertIn("restricted to gate/smoke diagnostics", runner)

        environment = os.environ.copy()
        environment["NAIVEFOX_CAPTURE_PRIVATE_H3_KEYLOG"] = "invalid"
        result = subprocess.run(
            ["bash", path],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be 0 or 1", result.stderr)

    def test_naivefox_only_lifecycle_mode_is_private_and_non_statistical(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY", runner)
        self.assertIn('print("naivefox", arm, scenario', runner)
        branch_start = runner.index(
            "if [[ $diagnostic_naivefox_only == 1 ]]; then\n  diagnostic_protocols="
        )
        analyzer_start = runner.index("\nanalyze_dataset()", branch_start)
        diagnostic_branch = runner[branch_start:analyzer_start]
        self.assertIn("diagnostic-summary.txt", diagnostic_branch)
        self.assertIn("sample_count=$session_counter", diagnostic_branch)
        self.assertIn("exit 0", diagnostic_branch)
        self.assertNotIn("analyze-camouflage", diagnostic_branch)
        self.assertNotIn("features.csv", diagnostic_branch)

    def test_naivefox_only_lifecycle_mode_rejects_unsafe_shapes(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        environment = os.environ.copy()
        environment["NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY"] = "1"

        missing_scenario = subprocess.run(
            ["bash", path, "--mode", "gate", "--naivefox-arm", "gate"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(missing_scenario.returncode, 2)
        self.assertIn("require exactly one --scenario", missing_scenario.stderr)

        missing_arm = subprocess.run(
            ["bash", path, "--mode", "gate", "--scenario", "sequential"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(missing_arm.returncode, 2)
        self.assertIn("require exactly one --naivefox-arm", missing_arm.stderr)

        repeated_scenario = subprocess.run(
            [
                "bash",
                path,
                "--mode",
                "gate",
                "--scenario",
                "initial",
                "--scenario",
                "sequential",
                "--naivefox-arm",
                "gate",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(repeated_scenario.returncode, 2)
        self.assertIn("require exactly one --scenario", repeated_scenario.stderr)

        repeated_arm = subprocess.run(
            [
                "bash",
                path,
                "--mode",
                "gate",
                "--scenario",
                "sequential",
                "--naivefox-arm",
                "gate",
                "--naivefox-arm",
                "tree-complete",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(repeated_arm.returncode, 2)
        self.assertIn("require exactly one --naivefox-arm", repeated_arm.stderr)

        standard = subprocess.run(
            [
                "bash",
                path,
                "--mode",
                "standard",
                "--scenario",
                "sequential",
                "--naivefox-arm",
                "gate",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(standard.returncode, 2)
        self.assertIn("restricted to gate/smoke", standard.stderr)

        invalid_environment = environment.copy()
        invalid_environment["NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY"] = "yes"
        invalid = subprocess.run(
            ["bash", path],
            check=False,
            capture_output=True,
            text=True,
            env=invalid_environment,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("must be 0 or 1", invalid.stderr)

    def test_isolated_network_mode_is_explicit_and_fail_closed(self):
        runner_path = os.path.join(HERE, "run-camouflage-suite.sh")
        helper_path = os.path.join(HERE, "run-camouflage-isolated-network.sh")
        monitor_path = os.path.join(HERE, "monitor-network-mutations.py")
        with open(runner_path, encoding="utf-8") as stream:
            runner = stream.read()
        with open(helper_path, encoding="utf-8") as stream:
            helper = stream.read()
        with open(monitor_path, encoding="utf-8") as stream:
            monitor = stream.read()

        self.assertIn("NAIVEFOX_CAPTURE_ISOLATED_NETWORK", runner)
        self.assertIn("exec unshare --net --mount-proc", runner)
        self.assertIn("isolated-network capture requires same-base mode", runner)
        self.assertIn("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED=1", helper)
        self.assertIn("ip link set lo mtu 1500", helper)
        self.assertIn("loopback_mtu=$(ip -o link show dev lo", helper)
        self.assertIn("isolated loopback MTU is not 1500", helper)
        self.assertIn("ethtool -K lo gro off gso off tso off", helper)
        self.assertIn("tx-udp-segmentation off tx-gso-list off", helper)
        self.assertIn("offload_state=$(ethtool -k lo)", helper)
        self.assertIn("isolated loopback offload remained enabled", helper)
        self.assertIn("udp.length>1500", runner)
        self.assertIn("tcp.options.mss_val", runner)
        self.assertIn("tcp.len>$max_tcp_payload", runner)
        self.assertIn(
            "capture_offload_policy=host_interface_offload_state_unmodified",
            runner,
        )
        self.assertIn("UDP offload superframe", runner)
        self.assertIn("oversized TCP segment", runner)
        self.assertIn("ip link add naivefox0 type dummy", helper)
        self.assertIn("192.0.2.1/32", helper)
        self.assertNotIn("/etc/wsl.conf", helper)
        self.assertNotIn(".wslconfig", helper)
        self.assertTrue(os.access(helper_path, os.X_OK))
        self.assertIn("network route/address/link mutation invalidated", runner)
        self.assertIn("network mutation monitor stopped before the sample", runner)
        self.assertIn("network mutation monitor did not confirm a drained stop", runner)
        self.assertIn("network_mutation_monitor=netlink_route_v1_fail_closed", runner)
        self.assertIn("RTMGRP_LINK", monitor)
        self.assertIn('20: "new-address"', monitor)
        self.assertIn('25: "del-route"', monitor)
        self.assertNotIn("IFA_ADDRESS", monitor)
        self.assertNotIn("RTA_GATEWAY", monitor)

        naivefox_sample = runner[
            runner.index("run_naivefox_sample()") : runner.index("scenario_csv=")
        ]
        self.assertLess(
            naivefox_sample.index('start_network_mutation_monitor "$sample_dir"'),
            naivefox_sample.index('"$NAIVEFOX_BIN" "$naivefox_config"'),
        )

        environment = os.environ.copy()
        environment["NAIVEFOX_CAPTURE_ISOLATED_NETWORK"] = "invalid"
        result = subprocess.run(
            ["bash", runner_path],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be 0 or 1", result.stderr)

        invalid_helper = subprocess.run(
            ["bash", helper_path, "/bin/true"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(invalid_helper.returncode, 2)
        self.assertIn(
            "invalid isolated camouflage network invocation", invalid_helper.stderr
        )

    def test_runner_rejects_undersized_research_capture(self):
        result = subprocess.run(
            [
                "bash",
                os.path.join(HERE, "run-camouflage-suite.sh"),
                "--mode",
                "research",
                "--samples-per-cohort",
                "239",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("at least 240 samples per cohort", result.stderr)

    def test_off_arm_uses_same_config_shape_without_gate_or_preamble(self):
        config = CONFIG.build_config(
            "off", "h2", 1080, 4433, "fixture-user", "fixture-pass"
        )
        self.assertFalse(config["outer-session-gate"])
        self.assertEqual(config["preamble"], {"mode": "off"})
        self.assertEqual(config["host-resolver-rules"], "MAP localhost 127.0.0.1")
        self.assertEqual(config["log"], "")

    def test_gate_arm_config_uses_h2_and_outer_session_gate(self):
        config = CONFIG.build_config(
            "gate", "h2", 1080, 4433, "fixture-user", "fixture-pass"
        )
        self.assertEqual(config["listen"], "socks://127.0.0.1:1080")
        self.assertEqual(urlsplit(config["proxy"]).scheme, "https")
        self.assertTrue(config["outer-session-gate"])
        self.assertEqual(config["preamble"], {"mode": "off"})

    def test_http_connect_ingress_uses_document_start_for_h2_and_h3(self):
        h2_config = CONFIG.build_config(
            "document-start-http-connect",
            "h2",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(h2_config["listen"], "http://127.0.0.1:1080")
        self.assertEqual(h2_config["preamble"]["mode"], "document-start-overlap")
        self.assertEqual(urlsplit(h2_config["proxy"]).scheme, "https")
        self.assertTrue(h2_config["outer-session-gate"])

        h3_config = CONFIG.build_config(
            "document-start-http-connect",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(h3_config["listen"], "http://127.0.0.1:1080")
        self.assertEqual(h3_config["preamble"]["mode"], "document-start-overlap")
        self.assertEqual(urlsplit(h3_config["proxy"]).scheme, "quic")
        self.assertTrue(h3_config["outer-session-gate"])

    def test_http_connect_ingress_combines_with_h3_resource_page(self):
        config = CONFIG.build_config(
            "tree-native-parser-resource-committed-page-http-connect",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(config["listen"], "http://127.0.0.1:1080")
        self.assertEqual(urlsplit(config["proxy"]).scheme, "quic")
        self.assertEqual(
            config["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-resource-committed-overlap",
                "path": "/camouflage/index.html",
                "max-assets": 6,
                "max-bytes": 384 * 1024,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-resource-committed-page-http-connect",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )

    def test_http_connect_ingress_combines_with_response_header_admission(self):
        for protocol in ("h2", "h3"):
            config = CONFIG.build_config(
                "document-overlap-http-connect",
                protocol,
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
            self.assertEqual(config["listen"], "http://127.0.0.1:1080")
            self.assertEqual(config["preamble"]["mode"], "document-overlap")
            self.assertTrue(config["outer-session-gate"])

    def test_http_connect_ingress_combines_with_first_buffer_admission(self):
        for protocol in ("h2", "h3"):
            config = CONFIG.build_config(
                "document-first-buffer-http-connect",
                protocol,
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
            self.assertEqual(config["listen"], "http://127.0.0.1:1080")
            self.assertEqual(
                config["preamble"]["mode"], "document-first-buffer-overlap"
            )
            self.assertTrue(config["outer-session-gate"])

    def test_http_connect_task_barriers_map_to_product_modes(self):
        cases = {
            "document-start-task-http-connect": "document-start-task-overlap",
            "document-headers-task-http-connect": "document-headers-task-overlap",
            "document-first-buffer-task-http-connect": (
                "document-first-buffer-task-overlap"
            ),
        }
        for arm, mode in cases.items():
            with self.subTest(arm=arm):
                config = CONFIG.build_config(
                    arm,
                    "h3",
                    1080,
                    4433,
                    "fixture-user",
                    "fixture-pass",
                )
                self.assertEqual(config["listen"], "http://127.0.0.1:1080")
                self.assertEqual(config["preamble"]["mode"], mode)
                self.assertTrue(config["proxy"].startswith("quic://"))

    def test_http_connect_task_aliases_reuse_causal_validation(self):
        features = {
            "protocol": "h3",
            "features": {"lifecycle_connection_count": 1.0},
        }
        samples = {
            "document-start-task-http-connect": (
                "Connection 1 preamble document-start-overlap "
                "admission=request-committed-task request_committed=1 "
                "root_done=0 protocol=h3\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=512 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble document-start-overlap drain=complete "
                "root_done=1 completed_resources=0 protocol=h3\n"
            ),
            "document-headers-task-http-connect": (
                "Connection 1 preamble document-overlap "
                "admission=response-headers-task response_accepted=1 "
                "root_done=0 protocol=h3\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=0 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble document-overlap drain=complete "
                "root_done=1 completed_resources=0 protocol=h3\n"
            ),
            "document-first-buffer-task-http-connect": (
                "Connection 1 preamble document-overlap "
                "admission=first-data-buffer-task response_accepted=1 "
                "root_done=0 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=512 protocol=h3\n"
                "Connection 1 preamble document-overlap drain=complete "
                "root_done=1 completed_resources=0 protocol=h3\n"
            ),
        }
        for arm, log_text in samples.items():
            with self.subTest(arm=arm):
                SAMPLE.validate_sample(arm, "h3", log_text, features)

    def test_socks_ingress_combines_with_first_buffer_admission(self):
        config = CONFIG.build_config(
            "document-first-buffer-overlap",
            "h2",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(config["listen"], "socks://127.0.0.1:1080")
        self.assertEqual(
            config["preamble"]["mode"], "document-first-buffer-overlap"
        )
        self.assertTrue(config["outer-session-gate"])
        h3_config = CONFIG.build_config(
            "document-first-buffer-overlap",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            h3_config["preamble"]["mode"], "document-first-buffer-overlap"
        )
        self.assertTrue(h3_config["proxy"].startswith("quic://"))

    def test_socks_first_buffer_task_arm_uses_explicit_task_mode(self):
        config = CONFIG.build_config(
            "document-first-buffer-task-overlap",
            "h2",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(config["listen"], "socks://127.0.0.1:1080")
        self.assertEqual(
            config["preamble"]["mode"], "document-first-buffer-task-overlap"
        )
        h3_config = CONFIG.build_config(
            "document-first-buffer-task-overlap",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            h3_config["preamble"]["mode"],
            "document-first-buffer-task-overlap",
        )
        self.assertTrue(h3_config["proxy"].startswith("quic://"))

    def test_root_arm_config_uses_h3_and_bounded_preamble(self):
        user = "fixture user:@"
        password = "p@ss/word?#"
        config = CONFIG.build_config("root", "h3", 1080, 4433, user, password)
        proxy = urlsplit(config["proxy"])
        self.assertEqual(proxy.scheme, "quic")
        self.assertEqual(unquote(proxy.username), user)
        self.assertEqual(unquote(proxy.password), password)
        self.assertNotIn(user, config["proxy"])
        self.assertNotIn(password, config["proxy"])
        self.assertEqual(config["preamble"]["mode"], "document-complete")
        self.assertEqual(config["preamble"]["path"], CONFIG.PREAMBLE_PATH)
        self.assertLessEqual(config["preamble"]["max-bytes"], 64 * 1024)
        self.assertTrue(config["outer-session-gate"])
        control = CONFIG.build_config(
            "root-pmtud-control", "h3", 1081, 4433, user, password
        )
        self.assertEqual(control["preamble"], config["preamble"])
        self.assertEqual(control["outer-session-gate"], config["outer-session-gate"])
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config("root-pmtud-control", "h2", 1081, 4433, user, password)

    def test_handshake_confirmed_arm_is_explicitly_h3_only(self):
        config = CONFIG.build_config(
            "document-handshake-confirmed",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            config["preamble"],
            {
                "mode": "off",
                "h3-mode": "document-handshake-confirmed",
                "path": CONFIG.PREAMBLE_PATH,
                "max-bytes": CONFIG.PREAMBLE_MAX_BYTES,
            },
        )
        self.assertTrue(config["outer-session-gate"])
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "document-handshake-confirmed",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )

    def test_carrier_dispatch_arm_is_explicitly_h3_only(self):
        config = CONFIG.build_config(
            "document-carrier-dispatch",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            config["preamble"],
            {
                "mode": "off",
                "h3-mode": "document-carrier-dispatch",
                "path": CONFIG.PREAMBLE_PATH,
                "max-bytes": CONFIG.PREAMBLE_MAX_BYTES,
            },
        )
        self.assertTrue(config["outer-session-gate"])
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "document-carrier-dispatch",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )

    def test_native_cache_open_arm_is_explicitly_h3_only(self):
        config = CONFIG.build_config(
            "document-native-cache-open",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            config["preamble"],
            {
                "mode": "off",
                "h3-mode": "document-native-cache-open",
                "path": CONFIG.PREAMBLE_PATH,
                "max-bytes": CONFIG.PREAMBLE_MAX_BYTES,
            },
        )
        self.assertTrue(config["outer-session-gate"])
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "document-native-cache-open",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )

    def test_native_channel_open_arm_is_explicitly_retired(self):
        for protocol in ("h2", "h3"):
            with self.assertRaisesRegex(ValueError, "config arm must be"):
                CONFIG.build_config(
                    "document-native-channel-open",
                    protocol,
                    1080,
                    4433,
                    "fixture-user",
                    "fixture-pass",
                )
        with open(
            os.path.join(SOURCE_ROOT, "netwerk", "naivefox", "Config.cpp"),
            encoding="utf-8",
        ) as stream:
            source = stream.read()
        self.assertIn(
            'mode.EqualsLiteral("document-native-channel-open")', source
        )
        self.assertIn("was retired because the falsified", source)
        self.assertIn("diagnostic pulled the full Safe Browsing", source)
        self.assertNotIn("DocumentNativeChannelOpen", source)

    def test_native_resource_uri_principal_is_lean_private(self):
        with open(
            os.path.join(SOURCE_ROOT, "caps", "nsScriptSecurityManagerNaiveFox.cpp"),
            encoding="utf-8",
        ) as stream:
            security_manager = stream.read()
        with open(
            os.path.join(SOURCE_ROOT, "caps", "NaiveFoxURIPrincipal.h"),
            encoding="utf-8",
        ) as stream:
            uri_principal_header = stream.read()
        with open(
            os.path.join(SOURCE_ROOT, "caps", "NaiveFoxURIPrincipal.cpp"),
            encoding="utf-8",
        ) as stream:
            uri_principal_source = stream.read()
        with open(
            os.path.join(
                SOURCE_ROOT,
                "netwerk",
                "naivefox",
                "NativeStylePreloadChannel.cpp",
            ),
            encoding="utf-8",
        ) as stream:
            native_style_channel = stream.read()

        channel_principal = security_manager.split(
            "nsScriptSecurityManager::GetChannelURIPrincipal", 1
        )[1].split("nsScriptSecurityManager::ActivateDomainPolicy", 1)[0]
        self.assertIn("return GetSystemPrincipal(aResult);", channel_principal)
        self.assertNotIn("NaiveFoxClassifierURIPrincipal", security_manager)
        self.assertIn("class NaiveFoxURIPrincipal", uri_principal_header)
        self.assertIn("NaiveFoxURIPrincipal::Create", uri_principal_source)
        self.assertGreaterEqual(
            native_style_channel.count("NaiveFoxURIPrincipal::Create"), 2
        )
        self.assertIn("documentPrincipal->IsSystemPrincipal()", native_style_channel)

    def test_cold_winner_handoff_arm_is_explicitly_h3_only(self):
        config = CONFIG.build_config(
            "document-cold-winner-handoff",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            config["preamble"],
            {
                "mode": "off",
                "h3-mode": "document-cold-winner-handoff",
                "path": CONFIG.PREAMBLE_PATH,
                "max-bytes": CONFIG.PREAMBLE_MAX_BYTES,
            },
        )
        self.assertTrue(config["outer-session-gate"])
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "document-cold-winner-handoff",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )

    def test_tree_arm_configs_use_browser_page_and_bounded_assets(self):
        for arm in (
            "tree-complete",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-overlap",
        ):
            config = CONFIG.build_config(
                arm, "h3", 1080, 4433, "fixture-user", "fixture-pass"
            )
            self.assertEqual(config["preamble"]["mode"], arm)
            self.assertEqual(config["preamble"]["path"], "/camouflage/index.html")
            self.assertEqual(
                config["preamble"]["max-assets"],
                CONFIG.TREE_PREAMBLE_MAX_ASSETS,
            )
            self.assertLessEqual(config["preamble"]["max-bytes"], 256 * 1024)
            self.assertTrue(config["outer-session-gate"])
        for arm, mode in (
            ("tree-complete-css", "tree-complete"),
            ("tree-root-overlap-css", "tree-root-overlap"),
            ("tree-warm-css-304", "tree-root-overlap"),
        ):
            config = CONFIG.build_config(
                arm, "h3", 1080, 4433, "fixture-user", "fixture-pass"
            )
            self.assertEqual(config["preamble"]["mode"], mode)
            self.assertEqual(config["preamble"]["max-assets"], 1)
            self.assertEqual(config["preamble"]["path"], CONFIG.PREAMBLE_PATH)
            if arm == "tree-warm-css-304":
                self.assertTrue(config["preamble"]["cache-resources"])
            else:
                self.assertNotIn("cache-resources", config["preamble"])
        committed = CONFIG.build_config(
            "tree-resource-committed-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            committed["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-resource-committed-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
            },
        )
        committed_tree = CONFIG.build_config(
            "tree-resource-committed-overlap-tree",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            committed_tree["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-resource-committed-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": CONFIG.RESOURCE_TREE_PREAMBLE_MAX_ASSETS,
                "max-bytes": CONFIG.RESOURCE_TREE_PREAMBLE_MAX_BYTES,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-resource-committed-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-resource-committed-overlap-tree",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        native_cache_committed = CONFIG.build_config(
            "tree-resource-native-cache-committed-overlap",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            native_cache_committed["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-resource-native-cache-committed-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-resource-native-cache-committed-overlap",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        native_parser = CONFIG.build_config(
            "tree-native-parser-preload-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            native_parser["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-preload-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-preload-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        native_parser_document_start = CONFIG.build_config(
            "tree-native-parser-document-start-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            native_parser_document_start["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-document-start-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        native_parser_document_start_h2 = CONFIG.build_config(
            "tree-native-parser-document-start-overlap-css",
            "h2",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            native_parser_document_start_h2["preamble"],
            {
                "mode": "off",
                "h2-mode": "tree-native-parser-document-start-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        navigation_stop = CONFIG.build_config(
            "tree-native-parser-document-start-navigation-stop-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            navigation_stop["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-document-start-navigation-stop",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        navigation_stop_h2 = CONFIG.build_config(
            "tree-native-parser-document-start-navigation-stop-css",
            "h2",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            navigation_stop_h2["preamble"],
            {
                "mode": "off",
                "h2-mode": "tree-native-parser-document-start-navigation-stop",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        response_stop = CONFIG.build_config(
            "tree-native-parser-document-start-response-stop-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            response_stop["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-document-start-response-stop",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-document-start-response-stop-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        document_handoff = CONFIG.build_config(
            "tree-native-parser-document-handoff-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            document_handoff["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-document-handoff-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-document-handoff-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        retarget = CONFIG.build_config(
            "tree-native-parser-retarget-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            retarget["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-retarget-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-retarget-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        ipc_rendezvous = CONFIG.build_config(
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            ipc_rendezvous["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-ipc-rendezvous-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-ipc-rendezvous-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        root_rendezvous = CONFIG.build_config(
            "tree-native-parser-root-rendezvous-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            root_rendezvous["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-root-rendezvous-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-root-rendezvous-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        process = CONFIG.build_config(
            "tree-native-parser-process-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            process["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-process-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-process-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        full_process = CONFIG.build_config(
            "tree-native-parser-full-process-overlap-css",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
        )
        self.assertEqual(
            full_process["preamble"],
            {
                "mode": "off",
                "h3-mode": "tree-native-parser-full-process-overlap",
                "path": CONFIG.PREAMBLE_PATH,
                "max-assets": 1,
                "max-bytes": CONFIG.TREE_PREAMBLE_MAX_BYTES,
                "cache-resources": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            CONFIG.build_config(
                "tree-native-parser-full-process-overlap-css",
                "h2",
                1080,
                4433,
                "fixture-user",
                "fixture-pass",
            )
        alias = CONFIG.build_config(
            "document-complete", "h2", 1080, 4433, "user", "pass"
        )
        self.assertEqual(alias["preamble"]["mode"], "document-complete")
        bounded = CONFIG.build_config(
            "tree-warm-css-304",
            "h3",
            1080,
            4433,
            "user",
            "pass",
            max_connections=1,
        )
        self.assertEqual(bounded["max-connections"], 1)
        for arm in ("document-overlap", "document-start-overlap"):
            config = CONFIG.build_config(
                arm, "h3", 1080, 4433, "fixture-user", "fixture-pass"
            )
            self.assertEqual(config["preamble"]["mode"], arm)
            self.assertNotIn("max-assets", config["preamble"])
            self.assertEqual(config["preamble"]["path"], CONFIG.PREAMBLE_PATH)
        measured_path = (
            "/camouflage/index.html?scenario=browser_page&size=0&count=0&"
            "idle_ms=0&completion=0123456789abcdef0123456789abcdef"
        )
        matched = CONFIG.build_config(
            "document-complete",
            "h3",
            1080,
            4433,
            "fixture-user",
            "fixture-pass",
            preamble_path=measured_path,
        )
        self.assertEqual(matched["preamble"]["path"], measured_path)
        for invalid_path in ("/other", CONFIG.PREAMBLE_PATH + "#fragment", "\n"):
            with self.assertRaisesRegex(ValueError, "camouflage document path"):
                CONFIG.build_config(
                    "document-complete",
                    "h3",
                    1080,
                    4433,
                    "fixture-user",
                    "fixture-pass",
                    preamble_path=invalid_path,
                )

    def test_config_arm_validation_rejects_unknown_arm_and_invalid_ports(self):
        with self.assertRaisesRegex(ValueError, "document-complete"):
            CONFIG.build_config("unknown", "h2", 1080, 4433, "user", "pass")
        for port in (0, 65536, True):
            with self.assertRaisesRegex(ValueError, "outside 1..65535"):
                CONFIG.build_config("gate", "h2", port, 4433, "user", "pass")

    def test_private_arm_config_is_mode_0600_and_never_overwritten(self):
        config = CONFIG.build_config("gate", "h2", 1080, 4433, "user", "pass")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "naivefox-config.json")
            CONFIG.write_config(path, config)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                CONFIG.write_config(path, config)

    def test_config_generator_keeps_credentials_out_of_output(self):
        user = "fixture user:@"
        password = "p@ss/word?#"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "private-config.json")
            environment = os.environ.copy()
            environment["NAIVEFOX_FIXTURE_USER"] = user
            environment["NAIVEFOX_FIXTURE_PASS"] = password
            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(HERE, "camouflage_naivefox_config.py"),
                    "--output",
                    path,
                    "--arm",
                    "root",
                    "--protocol",
                    "h3",
                    "--socks-port",
                    "1080",
                    "--proxy-port",
                    "4433",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            with open(path, encoding="utf-8") as stream:
                serialized = stream.read()
            self.assertNotIn(user, serialized)
            self.assertNotIn(password, serialized)

    def test_preamble_fixture_precedes_authenticated_forward_proxy(self):
        with open(os.path.join(HERE, "Caddyfile"), encoding="utf-8") as stream:
            caddyfile = stream.read()
        path = CONFIG.PREAMBLE_PATH
        self.assertEqual(path, "/camouflage/index.html")
        self.assertIn("reverse_proxy /camouflage*", caddyfile)
        preamble_route = caddyfile[
            caddyfile.index("@preambleOriginAuthLeak") : caddyfile.index(
                "forward_proxy"
            )
        ]
        self.assertNotIn("NaiveFox", preamble_route)
        self.assertNotIn("X-", preamble_route)
        self.assertIn("@preambleOriginAuthLeak", preamble_route)
        self.assertIn("@preambleProxyAuthLeak", preamble_route)
        self.assertIn("preamble must not carry Authorization", preamble_route)
        self.assertIn("preamble must not carry Proxy-Authorization", preamble_route)
        self.assertNotRegex(caddyfile, r"(?m)^\s*encode(?:\s|$)")

    def test_tree_preamble_uses_browser_page_root_and_resources(self):
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["browser_page"]}
        ).decode()
        self.assertIn("/camouflage/style.css", page)
        self.assertIn("/camouflage/app.js", page)
        self.assertIn("/camouflage/resource?size=65536", page)
        self.assertIn("/camouflage/api", page)
        self.assertLess(page.index("/camouflage/style.css"), page.index("<img"))
        self.assertLess(page.index("/camouflage/app.js"), page.index("<img"))

    def test_browser_page_navigation_token_reaches_every_asset(self):
        token = "d" * 32
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["browser_page"], "nav": [token]}
        ).decode()
        self.assertEqual(page.count(f"nav={token}"), 6)
        self.assertIn(f"/camouflage/style.css?nav={token}", page)
        self.assertIn(f"/camouflage/app.js?nav={token}", page)
        self.assertIn(f"/camouflage/resource?size=65536&nav={token}", page)
        self.assertIn(f"/camouflage/resource?size=131072&nav={token}", page)
        self.assertIn(f"/camouflage/resource?size=262144&nav={token}", page)
        self.assertIn(f"/camouflage/api?nav={token}", page)

    def test_browser_page_base_size_scales_every_asset(self):
        token = "f" * 32
        page = TARGET.Handler.camouflage_page(
            object(),
            {
                "scenario": ["browser_page"],
                "asset_base": ["65536"],
                "nav": [token],
            },
        ).decode()
        self.assertIn(f"/camouflage/style.css?size=16384&nav={token}", page)
        self.assertIn(f"/camouflage/app.js?size=32768&nav={token}", page)
        self.assertIn(f"/camouflage/resource?size=16384&nav={token}", page)
        self.assertIn(f"/camouflage/resource?size=32768&nav={token}", page)
        self.assertIn(f"/camouflage/resource?size=65536&nav={token}", page)
        self.assertIn(f"/camouflage/api?size=1024&nav={token}", page)
        self.assertEqual(page.count(f"nav={token}"), 6)

    def test_browser_page_early_hints_match_document_urls(self):
        for mode, expected_count in (("css", 1), ("blocking", 2), ("all", 6)):
            query = {"scenario": ["browser_page"], "early_hints": [mode]}
            page = TARGET.Handler.camouflage_page(object(), query).decode()
            links = TARGET.browser_page_early_hint_links(query)
            self.assertEqual(len(links), expected_count)
            for link in links:
                url = link.split(">", 1)[0][1:]
                self.assertIn(url, page)

    def test_browser_page_rejects_unknown_early_hints_mode(self):
        self.assertIsNone(
            TARGET.browser_page_early_hint_links(
                {"scenario": ["browser_page"], "early_hints": ["invalid"]}
            )
        )
        self.assertIsNone(
            TARGET.browser_page_early_hint_links(
                {"scenario": ["initial"], "early_hints": ["css"]}
            )
        )

    def test_outer_early_hints_cli_is_bounded_and_h2_browser_only(self):
        runner = os.path.join(HERE, "run-camouflage-suite.sh")
        for arguments, message in (
            (
                [
                    "--protocol",
                    "h2",
                    "--scenario",
                    "browser_page",
                    "--outer-early-hints",
                    "invalid",
                ],
                "must be none, css, blocking, or all",
            ),
            (
                [
                    "--protocol",
                    "h3",
                    "--scenario",
                    "browser_page",
                    "--outer-early-hints",
                    "css",
                ],
                "requires --protocol h2 --scenario browser_page",
            ),
            (
                [
                    "--protocol",
                    "h2",
                    "--scenario",
                    "initial",
                    "--outer-early-hints",
                    "css",
                ],
                "requires --protocol h2 --scenario browser_page",
            ),
        ):
            result = subprocess.run(
                ["bash", runner, *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)

    def test_browser_page_emits_early_hints_before_final_response(self):
        handler = object.__new__(TARGET.Handler)
        handler.path = (
            "/camouflage/index.html?scenario=browser_page&early_hints=blocking"
        )
        handler.headers = {}
        handler.send_response_only = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = mock.Mock()
        handler.send_bytes = mock.Mock()

        TARGET.Handler.do_GET(handler)

        handler.send_response_only.assert_called_once_with(103)
        self.assertEqual(handler.send_header.call_count, 2)
        handler.end_headers.assert_called_once_with()
        handler.wfile.flush.assert_called_once_with()
        handler.send_bytes.assert_called_once()

    def test_browser_page_rejects_invalid_navigation_token(self):
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["browser_page"], "nav": ["../bad"]}
        )
        self.assertIsNone(page)

    def test_navigation_token_api_asset_is_a_valid_fixed_svg(self):
        handler = object.__new__(TARGET.Handler)
        handler.path = "/camouflage/api?nav=" + "e" * 32
        handler.send_svg = mock.Mock()
        handler.send_bytes = mock.Mock()
        TARGET.Handler.do_GET(handler)
        handler.send_svg.assert_called_once_with(TARGET.CAMOUFLAGE_API_IMAGE_SIZE)
        handler.send_bytes.assert_not_called()

    def test_scaled_browser_assets_serve_the_requested_lengths(self):
        handler = object.__new__(TARGET.Handler)
        handler.headers = {}
        handler.send_camouflage_style = mock.Mock()
        handler.send_bytes = mock.Mock()
        handler.send_svg = mock.Mock()

        handler.path = "/camouflage/style.css?size=16384"
        TARGET.Handler.do_GET(handler)
        style = handler.send_camouflage_style.call_args.args[0]
        self.assertEqual(len(style), 16384)
        self.assertTrue(style.startswith(TARGET.CAMOUFLAGE_STYLE_PREFIX))

        handler.path = "/camouflage/app.js?size=32768"
        TARGET.Handler.do_GET(handler)
        script = handler.send_bytes.call_args.args[1]
        self.assertEqual(len(script), 32768)
        self.assertTrue(script.startswith(TARGET.CAMOUFLAGE_SCRIPT_PREFIX))

        handler.path = "/camouflage/api?size=1024"
        TARGET.Handler.do_GET(handler)
        handler.send_svg.assert_called_once_with(1024)

    def test_browser_page_base_size_cli_is_bounded_and_explicit(self):
        runner = os.path.join(HERE, "run-camouflage-suite.sh")
        for arguments, message in (
            (["--browser-page-base-size", "65536"], "requires --scenario"),
            (
                [
                    "--scenario",
                    "browser_page",
                    "--browser-page-base-size",
                    "65535",
                ],
                "between 65536 and 4194304",
            ),
        ):
            result = subprocess.run(
                ["bash", runner, *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)

    def test_outer_resource_profiles_are_coherent_and_bounded(self):
        self.assertEqual(
            TARGET.fronting_resource_profile(None),
            (12 * 1024, 24 * 1024, 8 * 1024, 34, False),
        )
        for unit in (1024, 4096, 16384, 22000):
            style, script, image, fourth, fourth_is_svg = (
                TARGET.fronting_resource_profile(unit)
            )
            self.assertEqual(
                (style, script, image, fourth),
                (3 * unit, 6 * unit, 2 * unit, 2 * unit),
            )
            self.assertTrue(fourth_is_svg)
            self.assertLess(17 * unit + 1024, 384 * 1024)
        for invalid in (1023, 22001):
            with self.assertRaises(ValueError):
                TARGET.fronting_resource_profile(invalid)

    def test_scaled_outer_fourth_image_is_a_valid_svg(self):
        handler = object.__new__(TARGET.Handler)
        handler.path = "/camouflage/api?item=4"
        handler.send_svg = mock.Mock()
        handler.send_bytes = mock.Mock()
        with mock.patch.object(
            TARGET, "FRONTING_FOURTH_IMAGE_IS_SVG", True
        ), mock.patch.object(TARGET, "FRONTING_FOURTH_IMAGE_SIZE", 8192):
            TARGET.Handler.do_GET(handler)
        handler.send_svg.assert_called_once_with(8192)
        handler.send_bytes.assert_not_called()

    def test_exact_outer_fourth_response_remains_the_measured_json(self):
        handler = object.__new__(TARGET.Handler)
        handler.path = "/camouflage/api?item=4"
        handler.send_svg = mock.Mock()
        handler.send_bytes = mock.Mock()
        with mock.patch.object(TARGET, "FRONTING_FOURTH_IMAGE_IS_SVG", False):
            TARGET.Handler.do_GET(handler)
        handler.send_svg.assert_not_called()
        self.assertEqual(handler.send_bytes.call_args.args[0], 200)
        self.assertEqual(len(handler.send_bytes.call_args.args[1]), 34)
        self.assertEqual(handler.send_bytes.call_args.args[2], "application/json")

    def test_outer_resource_unit_cli_is_bounded_and_dense_only(self):
        runner = os.path.join(HERE, "run-camouflage-suite.sh")
        for arguments, message in (
            (
                ["--outer-resource-unit-size", "1023"],
                "between 1024 and 22000",
            ),
            (
                [
                    "--scenario",
                    "browser_page",
                    "--outer-resource-unit-size",
                    "4096",
                ],
                "requires a dense H3 fronting-page arm",
            ),
        ):
            result = subprocess.run(
                ["bash", runner, *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)

    def test_network_profile_is_isolated_recorded_and_uses_receive_copy(self):
        runner_path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(runner_path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("--network-one-way-delay-ms", runner)
        self.assertIn("--network-rate-mbit", runner)
        self.assertIn("refusing network shaping outside", runner)
        self.assertIn("qdisc replace dev lo root handle 1: netem", runner)
        self.assertGreaterEqual(runner.count("network_profile_applied_protocols="), 3)
        self.assertGreaterEqual(runner.count("capture_copy_policy="), 2)
        self.assertIn("copy_filter='sll.pkttype==0'", runner)
        self.assertLess(
            runner.index('source "$run_dir/fixture.env"'),
            runner.index("apply_network_profile", runner.index("for protocol in")),
        )

        result = subprocess.run(
            ["bash", runner_path, "--network-one-way-delay-ms", "20"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1", result.stderr)

    def test_tree_fixture_assets_leave_streams_live_within_budget(self):
        page = TARGET.Handler.camouflage_page(object(), {"scenario": ["browser_page"]})
        self.assertEqual(len(TARGET.CAMOUFLAGE_STYLE_CSS), 64 * 1024)
        self.assertEqual(len(TARGET.CAMOUFLAGE_APP_JS), 128 * 1024)
        self.assertTrue(TARGET.CAMOUFLAGE_STYLE_CSS.startswith(b":root{"))
        self.assertTrue(TARGET.CAMOUFLAGE_APP_JS.startswith(b"(()=>{"))
        aggregate = (
            len(page) + len(TARGET.CAMOUFLAGE_STYLE_CSS) + len(TARGET.CAMOUFLAGE_APP_JS)
        )
        self.assertLess(aggregate, CONFIG.TREE_PREAMBLE_MAX_BYTES)
        self.assertEqual(CONFIG.TREE_PREAMBLE_MAX_ASSETS, 2)

    def test_tree_fixture_asset_profile_is_bounded_and_explicit(self):
        with mock.patch.dict(
            os.environ,
            {
                "NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE": "16384",
                "NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE": "32768",
            },
        ):
            self.assertEqual(
                TARGET.configured_camouflage_asset_size(
                    "NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE", 64 * 1024
                ),
                16 * 1024,
            )
            self.assertEqual(
                TARGET.configured_camouflage_asset_size(
                    "NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE", 128 * 1024
                ),
                32 * 1024,
            )
        with mock.patch.dict(
            os.environ,
            {"NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE": "512"},
        ):
            with self.assertRaises(ValueError):
                TARGET.configured_camouflage_asset_size(
                    "NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE", 64 * 1024
                )

        with open(os.path.join(HERE, "start.sh"), encoding="utf-8") as stream:
            fixture_start = stream.read()
        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            suite = stream.read()
        self.assertIn("asset_size < 1024 || asset_size > 4194304", fixture_start)
        self.assertIn(
            "camouflage_style_size=$NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE",
            suite,
        )
        self.assertIn(
            "camouflage_script_size=$NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE",
            suite,
        )
        self.assertIn("outer_resource_profile=$outer_resource_profile", suite)
        self.assertIn(
            "outer_resource_body_bytes_excluding_root=",
            suite,
        )
        self.assertIn("validate_outer_resource_fixture", suite)
        self.assertIn("outer_resource_profile_preflight=", suite)
        self.assertLess(
            suite.index("validate_outer_resource_fixture", suite.index("for protocol in")),
            suite.index("apply_network_profile", suite.index("for protocol in")),
        )

        class ResponseRecorder:
            def __init__(self):
                self.headers = {}
                self.wfile = io.BytesIO()

            def send_response(self, _status):
                pass

            def send_header(self, name, value):
                self.headers[name] = value

            def end_headers(self):
                pass

        for body, content_type in (
            (TARGET.CAMOUFLAGE_STYLE_CSS, "text/css"),
            (TARGET.CAMOUFLAGE_APP_JS, "application/javascript"),
        ):
            response = ResponseRecorder()
            TARGET.Handler.send_bytes(response, 200, body, content_type)
            self.assertEqual(response.headers["Content-Length"], str(len(body)))
            self.assertNotIn("Content-Encoding", response.headers)

    def test_inner_h2_access_log_requires_exact_http2_completion(self):
        token = "0123456789abcdef0123456789abcdef"

        def record(uri, *, proto="HTTP/2.0", method="GET", status=200):
            return json.dumps({
                "request": {
                    "host": "localhost:45501",
                    "method": method,
                    "proto": proto,
                    "uri": uri,
                },
                "status": status,
            })

        valid = [
            record(f"/camouflage/index.html?completion={token}"),
            record("/camouflage/style.css"),
            record(
                f"/camouflage/complete?token={token}",
                method="POST",
                status=204,
            ),
        ]
        INNER_H2.validate_records(valid, completion=token, port=45501)

        for invalid, pattern in (
            (
                [
                    record(f"/camouflage/index.html?completion={token}"),
                    record(
                        f"/camouflage/complete?token={token}",
                        proto="HTTP/1.1",
                        method="POST",
                        status=204,
                    ),
                ],
                "unexpected protocol",
            ),
            (
                [
                    record(
                        f"/camouflage/complete?token={token}",
                        method="POST",
                        status=200,
                    )
                ],
                "status is not 204",
            ),
            (valid + [valid[-1]], "exactly one"),
        ):
            with self.assertRaisesRegex(ValueError, pattern):
                INNER_H2.validate_records(invalid, completion=token, port=45501)

    def test_inner_h2_fixture_is_opt_in_and_persistent(self):
        with open(os.path.join(HERE, "Caddyfile"), encoding="utf-8") as stream:
            proxy_caddyfile = stream.read()
        with open(os.path.join(HERE, "Caddyfile-inner-h2"), encoding="utf-8") as stream:
            inner_caddyfile = stream.read()
        with open(os.path.join(HERE, "start.sh"), encoding="utf-8") as stream:
            fixture_start = stream.read()
        with open(os.path.join(HERE, "stop.sh"), encoding="utf-8") as stream:
            fixture_stop = stream.read()
        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            suite = stream.read()

        self.assertIn("{$NAIVEFOX_FIXTURE_ALLOWED_PORTS}", proxy_caddyfile)
        self.assertNotIn("INNER_H2_PORT}", proxy_caddyfile)
        self.assertIn("protocols h2", inner_caddyfile)
        self.assertNotIn("protocols h1", inner_caddyfile)
        self.assertIn("--inner-h2", fixture_start)
        self.assertIn("--outer-h2-only", fixture_start)
        self.assertIn("expected_protocols = sys.argv[3].split()", fixture_start)
        self.assertIn("inner-h2.pid", fixture_start)
        self.assertIn("wait_for_h2_origin", fixture_start)
        self.assertIn("caddy inner-h2 target", fixture_stop)
        self.assertIn("fixture_start_args+=(--inner-h2)", suite)
        self.assertIn("fixture_start_args+=(--outer-h2-only)", suite)
        self.assertIn(
            "H2 camouflage fixture is not constrained to the h2-only listener",
            suite,
        )
        self.assertIn("camouflage_inner_h2_validation.py", suite)
        self.assertLess(
            suite.index("stop_capture", suite.index("run_naivefox_sample()")),
            suite.index(
                'validate_inner_h2_request "$completion"',
                suite.index("run_naivefox_sample()"),
            ),
        )

    def test_sample_validation_accepts_expected_arm_evidence(self):
        one_connection = {
            "protocol": "h3",
            "features": {
                "lifecycle_connection_count": 1.0,
                "tls_client_hello_count": 1.0,
            },
        }
        SAMPLE.validate_sample("off", "h3", "", one_connection)
        SAMPLE.validate_sample("gate", "h3", "", one_connection)
        SAMPLE.validate_sample(
            "root",
            "h3",
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "root-pmtud-control",
            "h3",
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n",
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SAMPLE.validate_sample(
                "root-pmtud-control",
                "h2",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=91 protocol=h2\n",
                {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
            )
        SAMPLE.validate_sample(
            "document-handshake-confirmed",
            "h3",
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n",
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SAMPLE.validate_sample(
                "document-handshake-confirmed",
                "h2",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=91 protocol=h2\n",
                {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
            )
        SAMPLE.validate_sample(
            "document-carrier-dispatch",
            "h3",
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "document-cold-winner-handoff",
            "h3",
            "Connection 1 preamble cold-winner-handoff "
            "establishment=requestless-single-proxy dispatch=exact-winner "
            "protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n"
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n",
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "exact winner marker"):
            SAMPLE.validate_sample(
                "document-cold-winner-handoff",
                "h3",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=91 protocol=h3\n",
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SAMPLE.validate_sample(
                "document-carrier-dispatch",
                "h2",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=91 protocol=h2\n",
                {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
            )
        SAMPLE.validate_sample(
            "document-native-cache-open",
            "h3",
            "Connection 1 preamble native-cache-open cache=readonly-miss "
            "protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n"
            "Connection 1 established target=example.test:443 outer=h3 "
            "padding=yes\n",
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "read-only miss marker"):
            SAMPLE.validate_sample(
                "document-native-cache-open",
                "h3",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=91 protocol=h3\n",
                one_connection,
            )
        SAMPLE.validate_sample(
            "document-native-channel-open",
            "h3",
            "Connection 1 preamble native-channel-open "
            "cache=new-writable-entry classifier=async-suspend-resume "
            "protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h3\n"
            "Connection 1 established target=example.test:443 outer=h3 "
            "padding=yes\n",
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "strict success marker"):
            SAMPLE.validate_sample(
                "document-native-channel-open",
                "h3",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=91 protocol=h3\n",
                one_connection,
            )
        for arm in (
            "document-complete",
            "tree-complete",
            "tree-early-overlap",
            "tree-overlap",
        ):
            SAMPLE.validate_sample(
                arm,
                "h3",
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=12000 protocol=h3\n",
                one_connection,
            )
        SAMPLE.validate_sample(
            "document-overlap",
            "h3",
            "Connection 1 preamble document-overlap "
            "admission=response-headers response_accepted=1 "
            "root_done=0 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=0 protocol=h3\n"
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Connection 1 preamble document-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "document-overlap-http-connect",
            "h2",
            "Connection 1 preamble document-overlap "
            "admission=response-headers response_accepted=1 "
            "root_done=0 protocol=h2\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=0 protocol=h2\n"
            "Connection 1 established target=localhost:443 "
            "outer=h2 padding=yes\n"
            "Connection 1 preamble document-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h2\n",
            {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
        )
        SAMPLE.validate_sample(
            "document-first-buffer-http-connect",
            "h2",
            "Connection 1 preamble document-overlap "
            "admission=first-data-buffer response_accepted=1 "
            "root_done=0 protocol=h2\n"
            "Connection 1 established target=localhost:443 "
            "outer=h2 padding=yes\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=512 protocol=h2\n"
            "Connection 1 preamble document-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h2\n",
            {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
        )
        SAMPLE.validate_sample(
            "document-first-buffer-overlap",
            "h2",
            "Connection 1 preamble document-overlap "
            "admission=first-data-buffer response_accepted=1 "
            "root_done=0 protocol=h2\n"
            "Connection 1 established target=localhost:443 "
            "outer=h2 padding=yes\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=512 protocol=h2\n"
            "Connection 1 preamble document-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h2\n",
            {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
        )
        SAMPLE.validate_sample(
            "document-first-buffer-task-overlap",
            "h2",
            "Connection 1 preamble document-overlap "
            "admission=first-data-buffer-task response_accepted=1 "
            "root_done=0 protocol=h2\n"
            "Connection 1 established target=localhost:443 "
            "outer=h2 padding=yes\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=512 protocol=h2\n"
            "Connection 1 preamble document-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h2\n",
            {"protocol": "h2", "features": {"lifecycle_connection_count": 1.0}},
        )
        SAMPLE.validate_sample(
            "document-start-overlap",
            "h3",
            "Connection 1 preamble document-start-overlap "
            "admission=request-committed request_committed=1 "
            "root_done=0 protocol=h3\n"
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 1 preamble document-start-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-root-overlap",
            "h3",
            "Connection 1 preamble root-overlap admission=started-resources "
            "root_done=1 started_resources=2 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=2 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-root-overlap-css",
            "h3",
            "Connection 1 preamble root-overlap admission=started-resources "
            "root_done=1 started_resources=1 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=1 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-resource-committed-overlap-css",
            "h3",
            "Connection 1 preamble resource-committed-overlap "
            "admission=request-committed root_done=1 started_resources=1 "
            "committed_resources=1 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Connection 1 preamble resource-committed-overlap "
            "drain=complete completed_resources=1 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-resource-committed-overlap-tree",
            "h3",
            "Connection 1 preamble resource-committed-overlap "
            "admission=request-committed root_done=1 started_resources=3 "
            "committed_resources=3 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Connection 1 preamble resource-committed-overlap "
            "drain=complete completed_resources=3 protocol=h3\n",
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-resource-native-cache-committed-overlap",
            "h3",
            "Connection 1 preamble resource-native-cache-committed-overlap "
            "admission=request-committed root_done=1 started_resources=1 "
            "committed_resources=1 cache_new=1 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Connection 1 preamble resource-native-cache-committed-overlap "
            "drain=complete completed_resources=1 cache_new=1 protocol=h3\n",
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                "tree-resource-native-cache-committed-overlap",
                "h3",
                "Connection 1 preamble resource-native-cache-committed-overlap "
                "admission=request-committed root_done=1 started_resources=1 "
                "committed_resources=1 cache_new=0 protocol=h3\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=12000 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble resource-native-cache-committed-overlap "
                "drain=complete completed_resources=1 cache_new=0 protocol=h3\n",
                one_connection,
            )
        native_parser_log = (
            "Preamble native-parser-preload lifecycle=chunk-flushed "
            "sequence=1 descriptors=1 status=0x00000000 generation=1 "
            "protocol=h3\n"
            "Connection 7 preamble native-parser-preload "
            "parser=html5-speculative-scanner parsers=1 descriptors=1 "
            "provenance=FromParser internal_type=40 protocol=h3\n"
            "Connection 7 preamble native-parser-preload "
            "channel=async-open channels=1 protocol=h3\n"
            "Connection 7 preamble native-parser-preload "
            "admission=request-committed root_done=1 started_resources=1 "
            "committed_resources=1 protocol=h3\n"
            "Connection 7 preamble native-parser-preload "
            "barrier=released protocol=h3\n"
            "Connection 7 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 7 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Connection 7 preamble native-parser-preload "
            "drain=complete completed_resources=1 http=200 protocol=h3\n"
        )
        SAMPLE.validate_sample(
            "tree-native-parser-preload-overlap-css",
            "h3",
            native_parser_log,
            one_connection,
        )
        native_parser_document_start_log = (
            "Connection 7 preamble native-parser-document-start "
            "admission=request-committed request_committed=1 root_done=0 "
            "protocol=h3\n"
            "Connection 7 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Preamble native-parser-preload lifecycle=chunk-flushed "
            "sequence=1 descriptors=1 status=0x00000000 generation=1 "
            "protocol=h3\n"
            "Preamble native-parser-preload lifecycle=stylesheet-opened "
            "stream=1 kind=from-parser referrer=inherited protocol=h3\n"
            "Connection 7 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 7 preamble native-parser-preload "
            "drain=complete completed_resources=1 http=200 protocol=h3\n"
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-overlap-css",
            "h3",
            native_parser_document_start_log,
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-overlap-css",
            "h2",
            native_parser_document_start_log.replace(
                "protocol=h3", "protocol=h2"
            ).replace("outer=h3", "outer=h2"),
            {
                "protocol": "h2",
                "features": {
                    "lifecycle_connection_count": 1.0,
                    "tls_client_hello_count": 1.0,
                },
            },
        )
        native_parser_document_start_h2_wrong_protocol = (
            native_parser_document_start_log
            .replace("protocol=h3", "protocol=h2")
            .replace("outer=h3", "outer=h2")
            .replace(
                "lifecycle=chunk-flushed sequence=1 descriptors=1 "
                "status=0x00000000 generation=1 protocol=h2",
                "lifecycle=chunk-flushed sequence=1 descriptors=1 "
                "status=0x00000000 generation=1 protocol=h3",
            )
        )
        with self.assertRaisesRegex(ValueError, "wrong outer protocol"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-overlap-css",
                "h2",
                native_parser_document_start_h2_wrong_protocol,
                {
                    "protocol": "h2",
                    "features": {
                        "lifecycle_connection_count": 1.0,
                        "tls_client_hello_count": 1.0,
                    },
                },
            )
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-overlap-css",
                "h3",
                native_parser_document_start_log.replace(
                    "request_committed=1", "request_committed=0"
                ),
                one_connection,
            )
        native_parser_navigation_stop_log = (
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "admission=request-committed request_committed=1 root_done=0 "
            "protocol=h3\n"
            "Connection 7 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Preamble native-parser-preload lifecycle=chunk-flushed "
            "sequence=1 descriptors=1 status=0x00000000 generation=1 "
            "protocol=h3\n"
            "Preamble native-parser-preload lifecycle=stylesheet-opened "
            "stream=1 kind=from-parser referrer=inherited protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=stylesheet-committed stream=1 status=waiting-for "
            "protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=tunnel-application-active direction=client-to-target "
            "bytes_positive=1 protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=stylesheet-response-started http=200 protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=navigation-stop-issued reason=NS_BINDING_ABORTED "
            "load_group=scoped protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=stylesheet-onstop status=NS_BINDING_ABORTED expected=1 "
            "protocol=h3\n"
            "Connection 7 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop drain=complete "
            "root_done=1 css_committed=1 css_aborted=1 http=200 protocol=h3\n"
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-navigation-stop-css",
            "h3",
            native_parser_navigation_stop_log,
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-navigation-stop-css",
            "h2",
            native_parser_navigation_stop_log.replace(
                "protocol=h3", "protocol=h2"
            ).replace("outer=h3", "outer=h2"),
            {
                "protocol": "h2",
                "features": {
                    "lifecycle_connection_count": 1.0,
                    "tls_client_hello_count": 1.0,
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace("expected=1", "expected=0"),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace(
                    "bytes_positive=1", "bytes_positive=0"
                ),
                one_connection,
            )
        tunnel_active_marker = (
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=tunnel-application-active direction=client-to-target "
            "bytes_positive=1 protocol=h3\n"
        )
        response_started_marker = (
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=stylesheet-response-started http=200 protocol=h3\n"
        )
        stop_issued_marker = (
            "Connection 7 preamble "
            "native-parser-document-start-navigation-stop "
            "phase=navigation-stop-issued reason=NS_BINDING_ABORTED "
            "load_group=scoped protocol=h3\n"
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-navigation-stop-css",
            "h3",
            native_parser_navigation_stop_log.replace(tunnel_active_marker, "").replace(
                "Preamble native-parser-preload lifecycle=chunk-flushed",
                tunnel_active_marker
                + "Preamble native-parser-preload lifecycle=chunk-flushed",
            ),
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-navigation-stop-css",
            "h3",
            native_parser_navigation_stop_log.replace(
                tunnel_active_marker + response_started_marker,
                response_started_marker + tunnel_active_marker,
            ),
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace(response_started_marker, ""),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace(
                    response_started_marker,
                    response_started_marker.replace("http=200", "http=404"),
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace(
                    response_started_marker + stop_issued_marker,
                    stop_issued_marker + response_started_marker,
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace(
                    tunnel_active_marker + response_started_marker + stop_issued_marker,
                    stop_issued_marker + tunnel_active_marker + response_started_marker,
                ),
                one_connection,
            )
        native_parser_response_stop_log = (
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "admission=request-committed request_committed=1 root_done=0 "
            "protocol=h3\n"
            "Connection 7 established target=localhost:443 "
            "outer=h3 padding=yes\n"
            "Preamble native-parser-preload lifecycle=chunk-flushed "
            "sequence=1 descriptors=1 status=0x00000000 generation=1 "
            "protocol=h3\n"
            "Preamble native-parser-preload lifecycle=stylesheet-opened "
            "stream=1 kind=from-parser referrer=inherited protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=stylesheet-committed stream=1 status=waiting-for "
            "protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=stylesheet-response-started http=200 protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=tunnel-application-active direction=target-to-client "
            "bytes_positive=1 payload=decoded protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=navigation-stop-issued reason=NS_BINDING_ABORTED "
            "load_group=scoped protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=stylesheet-onstop status=NS_BINDING_ABORTED expected=1 "
            "protocol=h3\n"
            "Connection 7 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 7 preamble "
            "native-parser-document-start-response-stop drain=complete "
            "root_done=1 css_committed=1 css_aborted=1 css_completed=0 "
            "http=200 protocol=h3\n"
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-response-stop-css",
            "h3",
            native_parser_response_stop_log,
            one_connection,
        )
        response_stop_tunnel_active_marker = (
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=tunnel-application-active direction=target-to-client "
            "bytes_positive=1 payload=decoded protocol=h3\n"
        )
        response_stop_response_started_marker = (
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=stylesheet-response-started http=200 protocol=h3\n"
        )
        response_stop_issued_marker = (
            "Connection 7 preamble "
            "native-parser-document-start-response-stop "
            "phase=navigation-stop-issued reason=NS_BINDING_ABORTED "
            "load_group=scoped protocol=h3\n"
        )
        natural_response_stop_log = (
            native_parser_response_stop_log
            .replace(response_stop_tunnel_active_marker, "")
            .replace(response_stop_issued_marker, "")
            .replace(
                "Connection 7 preamble "
                "native-parser-document-start-response-stop "
                "phase=stylesheet-onstop status=NS_BINDING_ABORTED expected=1 "
                "protocol=h3\n",
                "",
            )
            .replace(
                "css_aborted=1 css_completed=0",
                "css_aborted=0 css_completed=1",
            )
            .replace(
                "Connection 7 preamble "
                "native-parser-document-start-response-stop drain=complete ",
                "Connection 7 preamble native-parser-preload drain=complete "
                "completed_resources=1 http=200 protocol=h3\n"
                "Connection 7 preamble "
                "native-parser-document-start-response-stop drain=complete ",
            )
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-response-stop-css",
            "h3",
            natural_response_stop_log,
            one_connection,
        )
        SAMPLE.validate_sample(
            "tree-native-parser-document-start-response-stop-css",
            "h3",
            native_parser_response_stop_log.replace(
                response_stop_response_started_marker
                + response_stop_tunnel_active_marker,
                response_stop_tunnel_active_marker
                + response_stop_response_started_marker,
            ),
            one_connection,
        )
        for old, new in (
            ("direction=target-to-client", "direction=client-to-target"),
            ("payload=decoded", "payload=encoded"),
            ("css_completed=0", "css_completed=1"),
        ):
            with self.subTest(response_stop_invalid_state=old):
                with self.assertRaisesRegex(ValueError, "causal state"):
                    SAMPLE.validate_sample(
                        "tree-native-parser-document-start-response-stop-css",
                        "h3",
                        native_parser_response_stop_log.replace(old, new),
                        one_connection,
                    )
        with self.assertRaisesRegex(ValueError, "either a complete"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-response-stop-css",
                "h3",
                native_parser_response_stop_log.replace(
                    response_stop_tunnel_active_marker, ""
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-response-stop-css",
                "h3",
                native_parser_response_stop_log.replace(
                    response_stop_tunnel_active_marker + response_stop_issued_marker,
                    response_stop_issued_marker + response_stop_tunnel_active_marker,
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-response-stop-css",
                "h2",
                native_parser_response_stop_log,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "late parser barrier"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-navigation-stop-css",
                "h3",
                native_parser_navigation_stop_log.replace(
                    "Connection 7 preamble result=success",
                    "Connection 7 preamble native-parser-preload "
                    "barrier=released protocol=h3\n"
                    "Connection 7 preamble result=success",
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "late parser barrier"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-start-overlap-css",
                "h3",
                native_parser_document_start_log.replace(
                    "Connection 7 preamble result=success",
                    "Connection 7 preamble native-parser-preload "
                    "barrier=released protocol=h3\n"
                    "Connection 7 preamble result=success",
                ),
                one_connection,
            )
        handoff_phases = "".join(
            "Connection 7 preamble native-parser-document-handoff "
            f"phase={phase}"
            f"{' delivery=main-copy-dispatch' if phase == 'first-parser-feed' else ''} "
            "protocol=h3\n"
            for phase in SAMPLE.NATIVE_PARSER_DOCUMENT_HANDOFF_PHASES
        )
        document_handoff_log = handoff_phases + native_parser_log
        SAMPLE.validate_sample(
            "tree-native-parser-document-handoff-overlap-css",
            "h3",
            document_handoff_log,
            one_connection,
        )
        with self.assertRaisesRegex(ValueError, "delivery contract"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-handoff-overlap-css",
                "h3",
                document_handoff_log.replace(
                    "delivery=main-copy-dispatch", "delivery=retargeted"
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "missing, duplicated"):
            SAMPLE.validate_sample(
                "tree-native-parser-document-handoff-overlap-css",
                "h3",
                document_handoff_log.replace(
                    "phase=handoff-resume", "phase=consumer-constructed-main"
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            first_feed = (
                "Connection 7 preamble native-parser-document-handoff "
                "phase=first-parser-feed delivery=main-copy-dispatch protocol=h3\n"
            )
            channel_marker = (
                "Connection 7 preamble native-parser-preload "
                "channel=async-open channels=1 protocol=h3\n"
            )
            SAMPLE.validate_sample(
                "tree-native-parser-document-handoff-overlap-css",
                "h3",
                document_handoff_log.replace(first_feed, "").replace(
                    channel_marker, first_feed + channel_marker, 1
                ),
                one_connection,
            )
        retarget_lines = "".join(
            "Connection 7 preamble native-parser-retarget "
            f"phase={phase}"
            f"{' target=html5-parser verified=1' if phase == 'delivery-retargeted' else ''}"
            f"{' delivery=retargeted-direct' if phase == 'first-parser-feed' else ''} "
            "protocol=h3\n"
            for phase in SAMPLE.NATIVE_PARSER_RETARGET_PHASES
        )
        retarget_log = retarget_lines + native_parser_log
        SAMPLE.validate_sample(
            "tree-native-parser-retarget-overlap-css",
            "h3",
            retarget_log,
            one_connection,
        )
        activation_lines = (
            "Native style activation phase=descriptor-frozen request=41\n"
            "Native style activation phase=request-primary-actor-created "
            "request=41\n"
            "Native style activation phase=request-primary-actor-bound "
            "request=41\n"
            "Native style activation phase=child-open-sent request=41\n"
            "Native style activation phase=background-dispatched request=41\n"
            "Native style activation phase=request-background-actor-created "
            "request=41\n"
            "Native style activation phase=request-background-actor-bound "
            "request=41\n"
            "Native style activation phase=bg-ready-sent request=41\n"
            "Native style activation phase=background-ready-received request=41\n"
            "Preamble native-parser-preload "
            "lifecycle=stylesheet-channel-created stream=1 "
            "activation=ipc-rendezvous protocol=h3\n"
            "Native style activation phase=parent-channel-created request=41\n"
            "Native style activation phase=activation-released request=41 "
            "status=0x00000000\n"
            "Preamble native-parser-preload lifecycle=stylesheet-opened "
            "stream=1 kind=from-parser referrer=inherited "
            "activation=ipc-rendezvous protocol=h3\n"
            "Native style activation phase=async-open request=41\n"
        )
        activation_completion_lines = (
            "Native style activation phase=on-stop request=41 "
            "status=0x00000000\n"
            "Native style activation phase=request-primary-actor-delete-sent "
            "request=41\n"
            "Native style activation phase=request-background-actor-delete-sent "
            "request=41\n"
            "Native style activation phase=request-primary-actor-destroyed "
            "request=41\n"
            "Native style activation phase=request-background-actor-destroyed "
            "request=41\n"
        )
        descriptor_marker = (
            "Preamble native-parser-preload lifecycle=chunk-flushed "
            "sequence=1 descriptors=1 status=0x00000000 generation=1 "
            "protocol=h3\n"
        )
        ipc_rendezvous_log = (
            retarget_lines
            + native_parser_log.replace(
                descriptor_marker,
                descriptor_marker + activation_lines,
                1,
            )
            + activation_completion_lines
        )
        SAMPLE.validate_sample(
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "h3",
            ipc_rendezvous_log,
            one_connection,
        )
        root_activation_before_resume = (
            "Native root replacement activation phase=descriptor-frozen request=51\n"
            "Native root replacement activation phase=request-primary-actor-created "
            "request=51\n"
            "Native root replacement activation phase=begin-sent request=51\n"
            "Native root replacement activation phase=request-primary-actor-bound "
            "request=51\n"
            "Native root replacement activation phase=begin-received request=51\n"
            "Native root replacement activation phase=connect-parent-sent request=51\n"
            "Native root replacement activation phase=background-dispatched "
            "request=51\n"
            "Native root replacement activation phase=request-background-actor-created "
            "request=51\n"
            "Native root replacement activation phase=request-background-actor-bound "
            "request=51\n"
            "Native root replacement activation phase=background-ready request=51\n"
            "Native root replacement activation phase=bg-linked request=51\n"
            "Native root replacement activation phase=redirect-verification-started "
            "request=51\n"
            "Native root replacement activation phase=connect-parent-linked request=51 "
            "same_channel=1\n"
            "Native root replacement activation phase=redirect-verification-queued "
            "request=51\n"
            "Native root replacement activation phase=redirect-verification-run "
            "request=51 channel=71 generation=1\n"
            "Native root replacement activation phase=redirect-verification-callback "
            "request=51 channel=71 generation=1 status=0x00000000\n"
            "Native root replacement activation phase=redirect-verification-resolved "
            "request=51 channel=71 generation=1 status=0x00000000\n"
            "Native root replacement activation phase=continue-verification request=51\n"
            "Native root replacement activation phase=ready-to-verify request=51\n"
            "Native root replacement activation phase=setup-finished request=51\n"
        )
        root_activation_release = (
            "Native root replacement activation phase=forward-sent request=51 "
            "channel=71 generation=1\n"
            "Native root replacement activation phase=activation-released request=51 "
            "status=0x00000000\n"
        )
        root_activation_resume = (
            "Native root replacement activation phase=resume request=51\n"
        )
        root_activation_after_resume = (
            "Native root replacement activation phase=forward-received request=51 "
            "channel=71 generation=1\n"
            "Native root replacement activation phase=forward-start request=51\n"
        )
        root_activation_completion = (
            "Native root replacement activation phase=on-stop request=51 "
            "status=0x00000000 generation=1\n"
            "Native root replacement activation phase=request-primary-actor-delete-sent "
            "request=51\n"
            "Native root replacement activation phase=request-background-actor-delete-sent "
            "request=51\n"
            "Native root replacement activation phase=request-primary-actor-destroyed "
            "request=51\n"
            "Native root replacement activation phase=request-background-actor-destroyed "
            "request=51\n"
        )

        def root_product_phase(phase, request=51):
            return (
                "Connection 7 preamble native-parser-root-replacement "
                f"phase={phase} channel=71 request={request} "
                "generation=1 protocol=h3\n"
            )

        root_activation_before_resume = (
            (
                root_product_phase("root-response-validated", request=0)
                + root_product_phase("physical-root-suspended", request=0)
                + root_activation_before_resume
            )
            .replace(
                "Native root replacement activation phase=descriptor-frozen "
                "request=51\n",
                "Native root replacement activation phase=descriptor-frozen "
                "request=51\n" + root_product_phase("replacement-registered"),
                1,
            )
            .replace(
                "Native root replacement activation "
                "phase=connect-parent-linked request=51 same_channel=1\n",
                root_product_phase("connect-parent-same-root-linked")
                + root_product_phase("redirect-verifier-run-queued")
                + "Native root replacement activation "
                "phase=connect-parent-linked request=51 same_channel=1\n",
                1,
            )
            .replace(
                "Native root replacement activation "
                "phase=redirect-verification-callback request=51 channel=71 "
                "generation=1 status=0x00000000\n",
                root_product_phase("redirect-verifier-run")
                + root_product_phase("redirect-verifier-callback-queued")
                + "Native root replacement activation "
                "phase=redirect-verification-callback request=51 channel=71 "
                "generation=1 status=0x00000000\n",
                1,
            )
            .replace(
                "Native root replacement activation "
                "phase=continue-verification request=51\n",
                root_product_phase("redirect-verifier-callback-resolved")
                + "Native root replacement activation "
                "phase=continue-verification request=51\n",
                1,
            )
        )
        root_activation_release += (
            root_product_phase("replacement-listener-published")
            + root_product_phase("forward-on-start-sent")
            + root_product_phase("physical-root-resume")
        )
        root_activation_after_resume += root_product_phase(
            "forward-on-start-received"
        ) + root_product_phase("consumer-constructed-main")
        root_retarget_after_resume = (
            "Connection 7 preamble native-parser-retarget "
            "phase=delivery-retargeted target=html5-parser verified=1 "
            "protocol=h3\n"
            + root_product_phase("logical-request-retargeted")
            + "Native root replacement activation phase=forward-data-received "
            "request=51 bytes=4096\n"
            "Native root replacement activation phase=forward-data request=51 "
            "bytes=4096\n"
            "Native root replacement activation phase=forward-stop-received "
            "request=51 status=0x00000000\n"
            "Native root replacement activation phase=forward-stop request=51 "
            "status=0x00000000\n"
            "Connection 7 preamble native-parser-retarget "
            "phase=first-parser-feed delivery=logical-background protocol=h3\n"
            "Connection 7 preamble native-parser-retarget "
            "phase=parser-data-finished protocol=h3\n"
        )
        root_rendezvous_log = (
            root_activation_before_resume
            + root_activation_release
            + root_activation_resume
            + root_activation_after_resume
            + root_retarget_after_resume
            + native_parser_log.replace(
                descriptor_marker,
                descriptor_marker + activation_lines,
                1,
            )
            + root_activation_completion
            + activation_completion_lines
        )
        SAMPLE.validate_sample(
            "tree-native-parser-root-rendezvous-overlap-css",
            "h3",
            root_rendezvous_log,
            one_connection,
        )
        with self.assertRaisesRegex(
            ValueError, "unexpectedly logged native parser retarget lifecycle"
        ):
            SAMPLE.validate_sample(
                "tree-native-parser-process-overlap-css",
                "h3",
                root_rendezvous_log,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SAMPLE.validate_sample(
                "tree-native-parser-process-overlap-css",
                "h2",
                root_rendezvous_log,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "requires h3"):
            SAMPLE.validate_sample(
                "tree-native-parser-full-process-overlap-css",
                "h2",
                root_rendezvous_log,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "same root channel"):
            SAMPLE.validate_sample(
                "tree-native-parser-root-rendezvous-overlap-css",
                "h3",
                root_rendezvous_log.replace("same_channel=1", "same_channel=0"),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "registration identity"):
            SAMPLE.validate_sample(
                "tree-native-parser-root-rendezvous-overlap-css",
                "h3",
                root_rendezvous_log.replace(
                    "phase=replacement-registered channel=71 request=51",
                    "phase=replacement-registered channel=71 request=0",
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "exactly one marker"):
            SAMPLE.validate_sample(
                "tree-native-parser-ipc-rendezvous-overlap-css",
                "h3",
                ipc_rendezvous_log.replace(
                    "Native style activation phase=background-ready-received "
                    "request=41\n",
                    "",
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "causal ordering"):
            SAMPLE.validate_sample(
                "tree-native-parser-ipc-rendezvous-overlap-css",
                "h3",
                ipc_rendezvous_log.replace(
                    "Native style activation phase=background-dispatched request=41\n",
                    "",
                ).replace(
                    "Native style activation phase=background-ready-received "
                    "request=41\n",
                    "Native style activation phase=background-ready-received "
                    "request=41\n"
                    "Native style activation phase=background-dispatched "
                    "request=41\n",
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "unexpectedly logged"):
            SAMPLE.validate_sample(
                "tree-native-parser-retarget-overlap-css",
                "h3",
                ipc_rendezvous_log,
                one_connection,
            )
        for old, new, error in (
            ("verified=1", "verified=0", "verification failed"),
            (
                "delivery=retargeted-direct",
                "delivery=main-copy-dispatch",
                "delivery contract",
            ),
            ("target=html5-parser", "target=main", "target contract"),
        ):
            with self.subTest(invalid_retarget_field=old):
                with self.assertRaisesRegex(ValueError, error):
                    SAMPLE.validate_sample(
                        "tree-native-parser-retarget-overlap-css",
                        "h3",
                        retarget_log.replace(old, new),
                        one_connection,
                    )
        with self.assertRaisesRegex(ValueError, "missing, duplicated"):
            SAMPLE.validate_sample(
                "tree-native-parser-retarget-overlap-css",
                "h3",
                retarget_log.replace(
                    "phase=parser-data-finished", "phase=handoff-resume"
                ),
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "fallback, failure"):
            SAMPLE.validate_sample(
                "tree-native-parser-retarget-overlap-css",
                "h3",
                retarget_log + "Connection 7 preamble native-parser-retarget "
                "fallback=main-copy protocol=h3\n",
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "exactly one parser"):
            SAMPLE.validate_sample(
                "tree-native-parser-preload-overlap-css",
                "h3",
                native_parser_log.replace(
                    "Connection 7 preamble native-parser-preload channel=",
                    "Connection 7 preamble native-parser-preload "
                    "channel=async-open channels=1 protocol=h3\n"
                    "Connection 7 preamble native-parser-preload channel=",
                    1,
                ),
                one_connection,
            )
        for old, new in (
            ("parsers=1", "parsers=2"),
            ("descriptors=1", "descriptors=2"),
            ("channels=1", "channels=2"),
            ("provenance=FromParser", "provenance=None"),
            ("internal_type=40", "internal_type=4"),
        ):
            with self.subTest(invalid_native_parser_field=old):
                with self.assertRaisesRegex(ValueError, "causal state"):
                    SAMPLE.validate_sample(
                        "tree-native-parser-preload-overlap-css",
                        "h3",
                        native_parser_log.replace(old, new),
                        one_connection,
                    )
        with self.assertRaisesRegex(ValueError, "causal state"):
            SAMPLE.validate_sample(
                "tree-resource-committed-overlap-css",
                "h3",
                "Connection 1 preamble resource-committed-overlap "
                "admission=terminal-fallback root_done=1 started_resources=1 "
                "committed_resources=0 protocol=h3\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=12000 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble resource-committed-overlap "
                "drain=complete completed_resources=1 protocol=h3\n",
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "causal admission state"):
            SAMPLE.validate_sample(
                "tree-root-overlap-css",
                "h3",
                "Connection 1 preamble root-overlap admission=started-resources "
                "root_done=1 started_resources=2 protocol=h3\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=12000 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble root-overlap drain=complete "
                "completed_resources=2 protocol=h3\n",
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "completion count"):
            SAMPLE.validate_sample(
                "tree-root-overlap-css",
                "h3",
                "Connection 1 preamble root-overlap admission=started-resources "
                "root_done=1 started_resources=1 protocol=h3\n"
                "Connection 1 preamble result=success status=0x00000000 "
                "http=200 bytes=12000 protocol=h3\n"
                "Connection 1 established target=localhost:443 "
                "outer=h3 padding=yes\n"
                "Connection 1 preamble root-overlap drain=complete "
                "completed_resources=2 protocol=h3\n",
                one_connection,
            )
        SAMPLE.validate_sample(
            "tree-root-overlap",
            "h3",
            "Connection 1 preamble root-overlap admission=started-resources "
            "root_done=1 started_resources=2 protocol=h3\n"
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
            "Connection 2 established target=localhost:443 outer=h3 padding=yes\n"
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=2 protocol=h3\n",
            one_connection,
        )

    def test_root_overlap_sample_requires_causal_admission_marker(self):
        one_connection = {
            "protocol": "h3",
            "features": {"lifecycle_connection_count": 1.0},
        }
        result = (
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
        )
        with self.assertRaisesRegex(ValueError, "causal admission marker"):
            SAMPLE.validate_sample("tree-root-overlap", "h3", result, one_connection)
        fallback = (
            "Connection 1 preamble root-overlap admission=terminal-fallback "
            "root_done=1 started_resources=0 protocol=h3\n"
        )
        with self.assertRaisesRegex(ValueError, "causal admission state"):
            SAMPLE.validate_sample(
                "tree-root-overlap", "h3", fallback + result, one_connection
            )
        late_marker = (
            "Connection 1 preamble root-overlap admission=started-resources "
            "root_done=1 started_resources=2 protocol=h3\n"
        )
        drain = (
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=2 protocol=h3\n"
        )
        established = (
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
        )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                "tree-root-overlap",
                "h3",
                result + late_marker + established + drain,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "completed drain marker"):
            SAMPLE.validate_sample(
                "tree-root-overlap",
                "h3",
                late_marker + result + established,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "lifecycle marker identity"):
            SAMPLE.validate_sample(
                "tree-root-overlap",
                "h3",
                late_marker
                + result
                + established
                + "Connection 2 preamble root-overlap drain=complete "
                "completed_resources=2 protocol=h3\n",
                one_connection,
            )
        partial_admission = (
            "Connection 1 preamble root-overlap admission=started-resources "
            "root_done=1 started_resources=1 protocol=h3\n"
        )
        partial_drain = (
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=1 protocol=h3\n"
        )
        with self.assertRaisesRegex(ValueError, "causal admission state"):
            SAMPLE.validate_sample(
                "tree-root-overlap",
                "h3",
                partial_admission + result + established + partial_drain,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "completion count"):
            SAMPLE.validate_sample(
                "tree-root-overlap",
                "h3",
                late_marker + result + established + partial_drain,
                one_connection,
            )

    def test_document_overlap_rejects_drain_before_result(self):
        one_connection = {
            "protocol": "h3",
            "features": {"lifecycle_connection_count": 1.0},
        }
        admission = (
            "Connection 1 preamble document-overlap "
            "admission=response-headers response_accepted=1 "
            "root_done=0 protocol=h3\n"
        )
        drain = (
            "Connection 1 preamble document-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h3\n"
        )
        result = (
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=0 protocol=h3\n"
        )
        established = (
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
        )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                "document-overlap",
                "h3",
                admission + drain + result + established,
                one_connection,
            )

    def test_document_start_overlap_rejects_uncommitted_or_misordered_root(self):
        one_connection = {
            "protocol": "h3",
            "features": {"lifecycle_connection_count": 1.0},
        }
        admission = (
            "Connection 1 preamble document-start-overlap "
            "admission=request-committed request_committed=1 "
            "root_done=0 protocol=h3\n"
        )
        established = (
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
        )
        result = (
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
        )
        drain = (
            "Connection 1 preamble document-start-overlap drain=complete "
            "root_done=1 completed_resources=0 protocol=h3\n"
        )
        with self.assertRaisesRegex(ValueError, "causal admission state"):
            SAMPLE.validate_sample(
                "document-start-overlap",
                "h3",
                admission.replace("request_committed=1", "request_committed=0")
                + established
                + result
                + drain,
                one_connection,
            )
        with self.assertRaisesRegex(ValueError, "invalid ordering"):
            SAMPLE.validate_sample(
                "document-start-overlap",
                "h3",
                established + admission + result + drain,
                one_connection,
            )

    def test_overlapping_sample_rejects_background_drain_timeout(self):
        one_connection = {
            "protocol": "h3",
            "features": {"lifecycle_connection_count": 1.0},
        }
        result = (
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=12000 protocol=h3\n"
        )
        marker = (
            "Connection 1 preamble root-overlap admission=started-resources "
            "root_done=1 started_resources=2 protocol=h3\n"
        )
        established = (
            "Connection 1 established target=localhost:443 outer=h3 padding=yes\n"
        )
        drain = (
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=2 protocol=h3\n"
        )
        timeout = "Connection 1 preamble background drain timed out\n"
        for arm, evidence in (
            (
                "document-overlap",
                "Connection 1 preamble document-overlap "
                "admission=response-headers response_accepted=1 "
                "root_done=0 protocol=h3\n"
                + result
                + established
                + "Connection 1 preamble document-overlap drain=complete "
                "root_done=1 completed_resources=0 protocol=h3\n" + timeout,
            ),
            (
                "document-start-overlap",
                "Connection 1 preamble document-start-overlap "
                "admission=request-committed request_committed=1 "
                "root_done=0 protocol=h3\n"
                + established
                + result
                + "Connection 1 preamble document-start-overlap drain=complete "
                "root_done=1 completed_resources=0 protocol=h3\n" + timeout,
            ),
            ("tree-early-overlap", result + timeout),
            (
                "tree-root-overlap",
                marker + result + established + drain + timeout,
            ),
            (
                "tree-root-overlap-css",
                marker.replace("started_resources=2", "started_resources=1")
                + result
                + established
                + drain.replace("completed_resources=2", "completed_resources=1")
                + timeout,
            ),
            ("tree-overlap", result + timeout),
        ):
            with self.subTest(arm=arm), self.assertRaisesRegex(
                ValueError, "background drain timed out"
            ):
                SAMPLE.validate_sample(arm, "h3", evidence, one_connection)

    def test_root_overlap_runners_wait_for_normal_drain_before_capture_stop(self):
        for filename, function_end in (
            ("run-h2-capture-comparison.sh", "run_reference\nrun_candidate"),
            (
                "run-h3-capture-comparison.sh",
                "if [[ $comparison_design == arms ]]",
            ),
        ):
            with self.subTest(filename=filename):
                with open(os.path.join(HERE, filename), encoding="utf-8") as stream:
                    runner = stream.read()
                body = runner.split("run_candidate() {", 1)[-1]
                if filename.startswith("run-h3"):
                    body = runner.split("run_naivefox_arm() {", 1)[1]
                body = body.split(function_end, 1)[0]
                marker = "preamble root-overlap drain=complete completed_resources="
                self.assertIn(marker, body)
                self.assertLess(body.index(marker), body.index("stop_capture"))
                self.assertIn("admission_connection ==", body)
                self.assertIn("$result_line -lt $drain_line", body)

        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            suite = stream.read()
        body = suite.split("run_naivefox_sample() {", 1)[1].split("scenario_csv=", 1)[0]
        reference_body = suite.split("run_reference_sample() {", 1)[1].split(
            "run_naivefox_sample() {", 1
        )[0]
        marker = "preamble root-overlap drain=complete completed_resources="
        self.assertIn(marker, body)
        self.assertNotIn(marker, reference_body)
        self.assertLess(body.index(marker), body.index("stop_capture"))

    def test_h2_native_parser_document_start_has_decrypted_wire_admission(self):
        with open(
            os.path.join(HERE, "run-h2-capture-comparison.sh"),
            encoding="utf-8",
        ) as stream:
            runner = stream.read()
        with open(
            os.path.join(HERE, "h2_decrypted_parity_summary.py"),
            encoding="utf-8",
        ) as stream:
            summary = stream.read()
        arm = "tree-native-parser-document-start-overlap-css"
        self.assertIn(arm, runner)
        self.assertIn("camouflage_sample_validation.py", runner)
        self.assertIn(arm, summary)
        self.assertIn(
            'root_get["frame"]\n            < connect["frame"]\n'
            '            < min(event["frame"] for event in resource_gets)',
            summary,
        )
        self.assertIn("root or stylesheet lacks END_STREAM", summary)
        self.assertIn(
            "tree-native-parser-document-start-navigation-stop-css", runner
        )
        self.assertIn("http2.rst_stream.error", runner)
        self.assertIn("stylesheet completed instead of being canceled", summary)
        self.assertIn("lacks one causal H2 CANCEL", summary)

    def test_superblock_reuses_wire_completion_token_with_fresh_marker(self):
        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            runner = stream.read()
        self.assertIn("declare -A block_completion_tokens=()", runner)
        self.assertIn(
            "completion=${block_completion_tokens[$experiment_block]}", runner
        )
        self.assertIn(
            'rm -f -- "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion"',
            runner,
        )
        candidate = runner.split("run_naivefox_sample() {", 1)[1].split(
            "extract_sample() {", 1
        )[0]
        drain = "preamble document-start-overlap drain=complete"
        cutoff = candidate.index("sleep 0.25")
        drain_check = candidate.index('rg -q "$drain_pattern" "$log"')
        capture_stop = candidate.index("stop_capture")
        self.assertLess(candidate.index(drain), cutoff)
        self.assertLess(cutoff, drain_check)
        self.assertLess(drain_check, capture_stop)
        self.assertIn(
            "preamble resource-native-cache-committed-overlap "
            "drain=complete completed_resources=1 cache_new=1 protocol=$protocol$",
            candidate,
        )
        self.assertEqual(candidate[:capture_stop].count("wait_for_log"), 1)
        self.assertIn(
            "did not drain its preamble by the fixed capture cutoff", candidate
        )

    def test_h3_actor_teardown_wait_is_naivefox_only(self):
        with open(
            os.path.join(HERE, "run-h3-capture-comparison.sh"),
            encoding="utf-8",
        ) as stream:
            runner = stream.read()
        reference = runner.split("run_reference() {", 1)[1].split(
            "run_naivefox() {", 1
        )[0]
        candidate = runner.split("run_naivefox_arm() {", 1)[1].split(
            "run_reference", 1
        )[0]
        marker = "request-primary-actor-destroyed"
        self.assertNotIn(marker, reference)
        self.assertIn(marker, candidate)
        self.assertLess(candidate.index("stop_capture"), candidate.index(marker))

    def test_decrypted_process_arms_use_process_validator_not_legacy_actors(self):
        with open(
            os.path.join(HERE, "run-h3-capture-comparison.sh"),
            encoding="utf-8",
        ) as stream:
            runner = stream.read()
        self.assertIn(
            "if [[ $arm == tree-native-parser-ipc-rendezvous-overlap-css ||\n"
            "            $arm == tree-native-parser-root-rendezvous-overlap-css ]]; then",
            runner,
        )
        self.assertIn(
            "if [[ $arm == tree-native-parser-root-rendezvous-overlap-css ]]; then",
            runner,
        )
        self.assertIn(
            "elif [[ $arm == tree-native-parser-root-rendezvous-overlap-css ]]; then",
            runner,
        )
        self.assertIn('"$INTEGRATION_DIR/camouflage_sample_validation.py"', runner)
        self.assertIn("expected_mode=expected_mode", runner)

    def test_sample_validation_rejects_unexpected_preamble_or_connection(self):
        two_connections = {
            "protocol": "h2",
            "features": {"lifecycle_connection_count": 2.0},
        }
        success = (
            "Connection 1 preamble result=success status=0x00000000 "
            "http=200 bytes=91 protocol=h2\n"
        )
        with self.assertRaisesRegex(ValueError, "unexpectedly ran"):
            SAMPLE.validate_sample("off", "h2", success, two_connections)
        with self.assertRaisesRegex(ValueError, "one physical outer connection"):
            SAMPLE.validate_sample("gate", "h2", "", two_connections)

    def test_sample_validation_requires_explicit_padding_condition(self):
        one_connection = {
            "protocol": "h2",
            "features": {"lifecycle_connection_count": 1.0},
        }
        padding_yes = (
            "Connection 1 established target=localhost:443 "
            "outer=h2 padding=yes\n"
        )
        padding_no = (
            "Connection 1 established target=localhost:443 "
            "outer=h2 padding=no\n"
        )
        SAMPLE.validate_sample("gate", "h2", padding_yes, one_connection)
        with self.assertRaisesRegex(ValueError, "differs from expected"):
            SAMPLE.validate_sample("gate", "h2", padding_no, one_connection)
        with mock.patch.dict(
            os.environ, {"NAIVEFOX_CAPTURE_EXPECT_PADDING": "no"}, clear=False
        ):
            SAMPLE.validate_sample("gate", "h2", padding_no, one_connection)
            with self.assertRaisesRegex(ValueError, "differs from expected"):
                SAMPLE.validate_sample("gate", "h2", padding_yes, one_connection)
        with mock.patch.dict(
            os.environ, {"NAIVEFOX_CAPTURE_EXPECT_PADDING": "maybe"}, clear=False
        ):
            with self.assertRaisesRegex(ValueError, "unsupported expected padding"):
                SAMPLE.validate_sample("gate", "h2", padding_yes, one_connection)

    def test_root_sample_requires_exactly_one_successful_preamble(self):
        one_connection = {
            "protocol": "h2",
            "features": {"lifecycle_connection_count": 1.0},
        }
        failed = (
            "Connection 1 preamble result=http-error status=0x00000000 "
            "http=404 bytes=0 protocol=h2\n"
        )
        with self.assertRaisesRegex(ValueError, "did not succeed"):
            SAMPLE.validate_sample("root", "h2", failed, one_connection)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            SAMPLE.validate_sample("root", "h2", failed + failed, one_connection)
        success = (
            "[0824/140242.185605:INFO:naivefox] Connection 1 preamble "
            "result=success status=0x00000000 http=200 bytes=87 protocol=h2\n"
        )
        SAMPLE.validate_sample("root", "h2", success, one_connection)

    def test_h3_pool_race_gate_compares_distinct_off_and_gate_connections(self):
        path = os.path.join(HERE, "run-h3-pool-race-gate.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("measure_case off_simultaneous off 2", runner)
        self.assertIn("measure_case gate_simultaneous gate 1", runner)
        self.assertIn("-e quic.connection.number", runner)
        self.assertNotIn("tls.handshake.type==1", runner)
        self.assertIn("camouflage_naivefox_config.py", runner)
        self.assertIn('NAIVEFOX_PROFILE="$profile"', runner)

    def test_camouflage_arms_share_config_mode_and_private_credentials(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        start = runner.index("run_naivefox_sample()")
        end = runner.index("scenario_csv=", start)
        sample_runner = runner[start:end]
        self.assertIn('NAIVEFOX_FIXTURE_USER="$NAIVEFOX_FIXTURE_USER"', sample_runner)
        self.assertIn('NAIVEFOX_FIXTURE_PASS="$NAIVEFOX_FIXTURE_PASS"', sample_runner)
        self.assertIn('NAIVEFOX_PROFILE="$naivefox_profile"', sample_runner)
        self.assertIn('--preamble-path "$preamble_path"', sample_runner)
        self.assertNotIn("--socks-listen", sample_runner)
        self.assertNotIn("--profile", sample_runner)

    def test_camouflage_metadata_records_pairing_and_cutoff_provenance(self):
        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            runner = stream.read()
        for marker in (
            "completion_token_scope=experiment_block_wire_url",
            "completion_marker_reset=before_each_participant",
            "capture_cutoff=browser_done_plus_250ms",
            "preamble_drain_policy=reject_if_incomplete_at_capture_cutoff",
            "preamble_root_url_parity=reference_and_candidate_outer_exact_path",
        ):
            self.assertIn(marker, runner)

    def test_direct_h3_browser_gets_forced_alt_svc_mapping(self):
        preferences = CONTROLLER.firefox_preferences("h3", 4433, 0)
        self.assertTrue(preferences["network.http.http3.enable"])
        self.assertEqual(
            preferences["network.http.http3.alt-svc-mapping-for-testing"],
            "localhost;h3=:4433",
        )

    def test_socks_browser_uses_fail_closed_pac_without_outer_h3_mapping(self):
        preferences = CONTROLLER.firefox_preferences("h3", 4433, 1080, 0, 8443)
        self.assertFalse(preferences["network.http.http3.enable"])
        self.assertNotIn("network.http.http3.alt-svc-mapping-for-testing", preferences)
        self.assertEqual(preferences["network.proxy.type"], 2)
        self.assertFalse(preferences["network.proxy.failover_direct"])
        self.assertNotIn("network.proxy.socks", preferences)
        prefix = "data:application/x-ns-proxy-autoconfig;base64,"
        pac_url = preferences["network.proxy.autoconfig_url"]
        self.assertTrue(pac_url.startswith(prefix))
        pac = base64.b64decode(pac_url.removeprefix(prefix)).decode()
        self.assertEqual(pac, CONTROLLER.proxy_pac_script(1080, 8443))

    def test_http_proxy_browser_uses_native_connect_path_and_fail_closed_pac(self):
        preferences = CONTROLLER.firefox_preferences("h2", 4433, 0, 1080, 8443)
        self.assertFalse(preferences["network.http.http3.enable"])
        self.assertEqual(preferences["network.proxy.type"], 2)
        self.assertFalse(preferences["network.proxy.failover_direct"])
        prefix = "data:application/x-ns-proxy-autoconfig;base64,"
        pac_url = preferences["network.proxy.autoconfig_url"]
        pac = base64.b64decode(pac_url.removeprefix(prefix)).decode()
        self.assertEqual(pac, CONTROLLER.http_proxy_pac_script(1080, 8443))
        self.assertIn('return "PROXY 127.0.0.1:1080"', pac)
        self.assertNotIn("SOCKS", pac)
        self.assertIn('authority === "localhost:8443"', pac)
        self.assertIn('return "DIRECT"', pac)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            CONTROLLER.firefox_preferences("h2", 4433, 1080, 1081, 8443)

    def test_suite_profile_roles_keep_test_alt_svc_out_of_naivefox(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()

        mapping_pref = "network.http.http3.alt-svc-mapping-for-testing"
        force_pref = "network.http.http3.force-use-alt-svc-mapping-for-testing"
        self.assertEqual(runner.count(f'user_pref("{mapping_pref}"'), 1)
        self.assertEqual(runner.count(f'user_pref("{force_pref}"'), 1)
        self.assertIn("case $participant in", runner)
        self.assertIn('make_profile "$profile" "$protocol" reference', runner)
        self.assertIn('make_profile "$naivefox_profile" "$protocol" naivefox', runner)
        self.assertIn('local browser_participant=socks-browser', runner)
        self.assertIn('browser_participant=http-browser', runner)
        self.assertIn(
            'make_profile "$browser_profile" "$protocol" "$browser_participant"',
            runner,
        )
        self.assertIn("if [[ $direct_h3 == true ]]", runner)
        self.assertIn(
            'validate_profile_role "$destination" "$protocol" "$participant"',
            runner,
        )
        self.assertIn("unexpectedly contains AlternateServices.bin", runner)
        self.assertIn("is contaminated by a test Alt-Svc mapping", runner)

    def test_suite_generates_role_isolated_h3_profiles(self):
        runner_path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(runner_path, encoding="utf-8") as stream:
            runner = stream.read()
        start = runner.index("validate_profile_role()")
        end = runner.index("\nscenario_parameters()", start)
        profile_functions = runner[start:end]

        with tempfile.TemporaryDirectory() as temporary:
            trusted = os.path.join(temporary, "trusted")
            os.mkdir(trusted)
            with open(os.path.join(trusted, "prefs.js"), "w", encoding="utf-8"):
                pass
            reference = os.path.join(temporary, "reference")
            naivefox = os.path.join(temporary, "naivefox")
            pmtud_control = os.path.join(temporary, "pmtud-control")
            socks = os.path.join(temporary, "socks")
            variables = {
                "browser_python": sys.executable,
                "INTEGRATION_DIR": HERE,
                "NAIVEFOX_FIXTURE_TRUSTED_PROFILE": trusted,
                "NAIVEFOX_FIXTURE_PROXY_PORT": "4433",
            }
            setup = "\n".join(
                f"{name}={shlex.quote(value)}" for name, value in variables.items()
            )
            script = "\n".join((
                "set -euo pipefail",
                setup,
                profile_functions,
                f"make_profile {shlex.quote(reference)} h3 reference",
                f"make_profile {shlex.quote(naivefox)} h3 naivefox",
                f"make_profile {shlex.quote(pmtud_control)} h3 naivefox '' root-pmtud-control",
                f"make_profile {shlex.quote(socks)} h3 socks-browser 1080 '' 8443",
            ))
            subprocess.run(["bash"], input=script, text=True, check=True)

            with open(os.path.join(reference, "user.js"), encoding="utf-8") as stream:
                reference_prefs = stream.read()
            with open(os.path.join(naivefox, "user.js"), encoding="utf-8") as stream:
                naivefox_prefs = stream.read()
            with open(
                os.path.join(pmtud_control, "user.js"), encoding="utf-8"
            ) as stream:
                pmtud_control_prefs = stream.read()
            with open(os.path.join(socks, "user.js"), encoding="utf-8") as stream:
                socks_prefs = stream.read()
            mapping_pref = "network.http.http3.alt-svc-mapping-for-testing"
            self.assertIn(mapping_pref, reference_prefs)
            self.assertIn('network.http.http3.enable", true', naivefox_prefs)
            self.assertNotIn(mapping_pref, naivefox_prefs)
            self.assertNotIn(mapping_pref, socks_prefs)
            self.assertIn('network.http.http3.enable", false', socks_prefs)
            pmtud_pref = 'user_pref("network.http.http3.pmtud", true);'
            self.assertNotIn(pmtud_pref, reference_prefs)
            self.assertNotIn(pmtud_pref, naivefox_prefs)
            self.assertNotIn(pmtud_pref, socks_prefs)
            self.assertIn(pmtud_pref, pmtud_control_prefs)

    def test_proxy_pac_sends_only_target_loopback_port_to_sample_socks(self):
        pac = CONTROLLER.proxy_pac_script(1080, 8443)
        for host in ("localhost", "127.0.0.1", "::1", "[::1]"):
            self.assertIn(f'host === "{host}"', pac)
        self.assertIn('return "SOCKS5 127.0.0.1:1080"', pac)
        self.assertIn('authority === "localhost:8443"', pac)
        self.assertIn('return "DIRECT"', pac)
        self.assertIn(
            f'return "PROXY 127.0.0.1:{CONTROLLER.DEAD_LOCAL_PROXY_PORT}"',
            pac,
        )

    def test_proxy_pac_rejects_invalid_ports(self):
        for port in (0, 65536):
            with self.assertRaisesRegex(ValueError, "outside 1..65535"):
                CONTROLLER.proxy_pac_script(port, 8443)
            with self.assertRaisesRegex(ValueError, "outside 1..65535"):
                CONTROLLER.http_proxy_pac_script(port, 8443)
            with self.assertRaisesRegex(ValueError, "outside 1..65535"):
                CONTROLLER.proxy_pac_script(1080, port)
            with self.assertRaisesRegex(ValueError, "outside 1..65535"):
                CONTROLLER.http_proxy_pac_script(1080, port)

    def test_commandline_profile_generator_uses_same_pac_preferences(self):
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(HERE, "camouflage_browser_controller.py"),
                "--generate-pac-user-js",
                "1080",
                "8443",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout, CONTROLLER.proxy_user_js(1080, 8443))
        self.assertIn('user_pref("network.proxy.type", 2);', result.stdout)
        self.assertIn('user_pref("network.proxy.autoconfig_url", "data:', result.stdout)
        self.assertIn(
            'user_pref("network.proxy.failover_direct", false);', result.stdout
        )
        self.assertNotIn("network.proxy.socks", result.stdout)

    def test_http_commandline_profile_generator_uses_http_pac_preferences(self):
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(HERE, "camouflage_browser_controller.py"),
                "--generate-http-pac-user-js",
                "1080",
                "8443",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.stdout,
            CONTROLLER.http_proxy_user_js(1080, 8443),
        )
        self.assertIn('user_pref("network.proxy.type", 2);', result.stdout)
        self.assertIn('user_pref("network.proxy.autoconfig_url", "data:', result.stdout)
        self.assertNotIn("SOCKS5", result.stdout)

    def test_dumpcap_clean_shutdown_is_accepted(self):
        CAPTURE.validate_dumpcap_log(
            """Capturing on 'any'
File: /tmp/capture.pcapng
Packets captured: 42
Packets received/dropped on interface 'any': 84/0 (pcap:0/dumpcap:0/flushed:0/ps_ifdrop:0) (0.0%)
"""
        )

    def test_dumpcap_drop_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "dropped packets"):
            CAPTURE.validate_dumpcap_log(
                """Packets captured: 42
Packets received/dropped on interface 'any': 84/1 (pcap:1/dumpcap:0/flushed:0/ps_ifdrop:0) (1.2%)
"""
            )

    def test_dumpcap_without_final_statistics_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "final interface statistics"):
            CAPTURE.validate_dumpcap_log(
                "Capturing on 'any'\nFile: /tmp/capture.pcapng\n"
            )

    def test_controlled_page_reports_completion_after_load(self):
        token = "a" * 32
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["initial"], "completion": [token]}
        ).decode()
        self.assertIn("window.addEventListener('load'", page)
        self.assertIn(f"/camouflage/complete?token={token}", page)

    def test_controlled_page_rejects_invalid_completion_token(self):
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["initial"], "completion": ["../bad"]}
        )
        self.assertIsNone(page)

    def test_completion_marker_is_private_and_complete(self):
        token = "b" * 32
        with tempfile.TemporaryDirectory() as directory:
            TARGET.write_completion(directory, token)
            path = os.path.join(directory, token)
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "complete\n")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_warm_css_page_uses_the_measured_resource_url(self):
        token = "c" * 32
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["warm_css"], "completion": [token]}
        ).decode()
        self.assertIn('href="/camouflage/style.css"', page)
        self.assertNotIn("app.js", page)
        self.assertIn(f"/camouflage/complete?token={token}", page)
        self.assertEqual(
            TARGET.CAMOUFLAGE_STYLE_ETAG,
            '"naivefox-style-'
            + __import__("hashlib").sha256(TARGET.CAMOUFLAGE_STYLE_CSS).hexdigest()
            + '"',
        )

    def test_warm_cache_evidence_requires_natural_304_and_fresh_inner(self):
        warm = "d" * 32
        measured = "e" * 32
        etag = '"stable"'
        semantics = {
            "accept": "text/css,*/*;q=0.1",
            "host": "localhost:443",
            "listener": "http",
            "method": "GET",
            "path": "/camouflage/style.css",
            "priority": "u=2",
            "referer": f"https://localhost/camouflage/index.html?completion={measured}",
            "sec_fetch_dest": "style",
            "sec_fetch_mode": "no-cors",
            "sec_fetch_site": "same-origin",
        }
        reference = [
            dict(
                semantics,
                **{
                    "completion": warm,
                    "etag": etag,
                    "if_none_match": "",
                    "status": 200,
                },
            ),
            dict(
                semantics,
                **{
                    "completion": measured,
                    "etag": etag,
                    "if_none_match": etag,
                    "status": 304,
                },
            ),
        ]
        result = CACHE.validate_cache_sequence(reference, "reference", warm, measured)
        self.assertEqual(result["fresh_inner_200"], 0)
        self.assertRegex(result["semantics_sha256"], r"^[0-9a-f]{64}$")
        candidate = reference + [
            dict(
                semantics,
                **{
                    "completion": measured,
                    "etag": etag,
                    "if_none_match": "",
                    "listener": "https",
                    "status": 200,
                },
            )
        ]
        self.assertEqual(
            CACHE.validate_cache_sequence(candidate, "naivefox", warm, measured)[
                "fresh_inner_200"
            ],
            1,
        )
        candidate[1]["status"] = 200
        with self.assertRaisesRegex(ValueError, "did not receive 304"):
            CACHE.validate_cache_sequence(candidate, "naivefox", warm, measured)
        candidate[1]["status"] = 304
        with self.assertRaisesRegex(ValueError, "unexpected CSS requests"):
            CACHE.validate_cache_sequence(
                candidate + [dict(candidate[-1])], "naivefox", warm, measured
            )

    def test_warm_cache_transport_rejects_reconnect_or_zero_rtt(self):
        document = {
            "features": {
                "lifecycle_connection_count": 1.0,
                "tls_client_hello_count": 1.0,
                "quic_zero_rtt_packet_count": 0.0,
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "features.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(document, stream)
            CACHE.validate_transport(path)
            document["features"]["quic_zero_rtt_packet_count"] = 1.0
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(document, stream)
            with self.assertRaisesRegex(ValueError, "forbids measured H3 0-RTT"):
                CACHE.validate_transport(path)

    def test_quic_v2_zero_rtt_is_normalized_for_passive_features(self):
        row = {
            "frame.number": "1",
            "frame.time_relative": "0",
            "frame.len": "1200",
            "sll.pkttype": "0",
            "ip.len": "1200",
            "ipv6.plen": "",
            "udp.srcport": "50000",
            "udp.dstport": "443",
            "udp.length": "1180",
            "quic.connection.number": "0",
            "quic.version": "0x6b3343cf",
            "quic.long.packet_type": "",
            "quic.long.packet_type_v2": "2",
            "quic.dcil": "8",
            "quic.scil": "3",
            "quic.packet_length": "1160",
        }
        with mock.patch.object(FEATURES, "tshark_rows", return_value=[row]):
            events, _ = FEATURES.packet_events_h3("sample.pcapng", 443)
        features = {}
        FEATURES.add_h3_features(features, events)
        self.assertEqual(features["quic_zero_rtt_packet_count"], 1.0)

    def test_h2_proxy_floor_is_fixed_paired_and_fail_closed(self):
        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            runner = stream.read()
        self.assertIn("--h2-proxy-floor-superblocks", runner)
        self.assertIn("multi_arm_arms_csv=firefox-proxied,off", runner)
        self.assertIn("scenario_override=browser_page", runner)
        self.assertIn("protocol_selection=h2", runner)
        self.assertIn("inner_transport=https-h2", runner)
        self.assertIn("isolated_network=1", runner)
        self.assertIn("export NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1", runner)
        self.assertIn("start_proxied_browser_controller()", runner)
        self.assertIn("proxied_firefox_controller.py", runner)
        self.assertIn('--shutdown-file "$browser_shutdown_file"', runner)
        self.assertIn(
            'rm -f -- "$NAIVEFOX_FIXTURE_RUN_DIR/completions/$completion"',
            runner,
        )
        self.assertIn(
            'validate_inner_h2_request "$completion" "$inner_h2_log_start"',
            runner,
        )
        self.assertIn(
            "expected_inner_h2_validations=$((samples_per_cohort * 2))", runner
        )
        self.assertIn('strict_transport_check "$protocol" "$pcap"', runner)
        self.assertIn("capture_cutoff=browser_done_plus_250ms", runner)
        self.assertIn("proxy_floor_workload=", runner)
        self.assertIn("proxy_floor_candidate_slot_semantics=", runner)
        self.assertIn("initial_packets_16,packets_17_32,initial_packets_32", runner)

    def test_warm_cache_runner_is_ephemeral_and_fail_closed(self):
        with open(
            os.path.join(HERE, "run-camouflage-suite.sh"), encoding="utf-8"
        ) as stream:
            runner = stream.read()
        self.assertIn("tree-warm-css-304 requires a single-arm H3 run", runner)
        self.assertIn("tree-warm-css-304 requires --scenario browser_page", runner)
        self.assertIn(
            "tree-warm-css-304 cannot share cold superblock references", runner
        )
        self.assertLess(
            runner.index('warm_reference_cache "$profile"'),
            runner.index(
                'start_capture "$pcap"', runner.index("run_reference_sample()")
            ),
        )
        self.assertIn("camouflage_cache_validation.py", runner)
        self.assertIn("temporary_participant_sample_warm_measure_then_deleted", runner)
        self.assertIn("warm_traffic_excluded_measure_only", runner)
        self.assertIn("$session_id:reference_measure", runner)
        self.assertIn("$session_id:naivefox_measure", runner)
        self.assertIn("reference_cold_measure", runner)
        self.assertIn("naivefox_cold_measure", runner)
        self.assertIn("cold_proxy_reset_applies()", runner)
        self.assertIn("[[ $protocol == h3 ]] || return 1", runner)
        self.assertIn(",$multi_arm_arms_csv, == *,tree-root-overlap-css,*", runner)
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-resource-committed-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-preload-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-document-start-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-document-handoff-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-retarget-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-ipc-rendezvous-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-root-rendezvous-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-process-overlap-css,*",
            runner,
        )
        self.assertIn(
            ",$multi_arm_arms_csv, == *,tree-native-parser-full-process-overlap-css,*",
            runner,
        )
        self.assertIn(
            "tree-native-parser-preload-overlap-css multi-arm screening "
            "requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-document-handoff-overlap-css multi-arm "
            "screening requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-retarget-overlap-css multi-arm screening "
            "requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-ipc-rendezvous-overlap-css multi-arm "
            "screening requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-root-rendezvous-overlap-css multi-arm "
            "screening requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-process-overlap-css multi-arm screening "
            "requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-full-process-overlap-css multi-arm screening "
            "requires --protocol h3",
            runner,
        )
        self.assertIn(
            "tree-native-parser-process-overlap-css multi-arm screening "
            "requires the tree-native-parser-root-rendezvous-overlap-css control",
            runner,
        )
        self.assertIn(
            "tree-native-parser-full-process-overlap-css multi-arm screening "
            "requires the tree-native-parser-process-overlap-css control",
            runner,
        )
        self.assertIn("camouflage_sample_validation.py", runner)
        self.assertIn(
            "native-parser-preload drain=complete completed_resources=1 "
            "http=2[0-9][0-9]",
            runner,
        )
        self.assertIn("pid does not identify the fixture Caddy binary", runner)
        self.assertIn("found no exact target process identity", runner)
        self.assertIn("enabling HTTP/3 listener", runner)
        self.assertIn("fixture_proxy_restart_count=$proxy_restart_count", runner)
        strict_transport = runner[
            runner.index("strict_transport_check() {") : runner.index(
                "start_browser_controller() {"
            )
        ]
        self.assertIn("${#tcp_streams[@]} -ne 1", strict_transport)
        self.assertIn("client_syn_count -ne 1", strict_transport)
        self.assertIn("client_hello_count -ne 1", strict_transport)
        self.assertIn("tcp.options.mss_val", strict_transport)
        self.assertIn("tcp.len>$max_tcp_payload", strict_transport)
        self.assertIn("tcp.port==$NAIVEFOX_FIXTURE_PROXY_PORT", strict_transport)
        self.assertNotIn("NAIVEFOX_FIXTURE_INNER_H2_PORT", strict_transport)
        self.assertIn("outer_h2_alpn_policy=", runner)
        self.assertIn("proxy_restart_count -ne $expected_proxy_restart_count", runner)
        self.assertEqual(runner.count('normalize_h3_capture_origin "$pcap"'), 2)
        origin = runner[
            runner.index("normalize_h3_capture_origin() {") : runner.index(
                "strict_transport_check() {"
            )
        ]
        self.assertIn("client traffic before Initial", origin)
        self.assertIn("foreign UDP flow after Initial", origin)
        self.assertIn("udp.stream!=$measured_udp_stream", origin)
        self.assertIn("before-origin-trim.pcapng", origin)
        self.assertIn("frame.number>=$first_initial_frame", origin)
        self.assertIn("cache_validated_participants -ne $session_counter", runner)
        self.assertIn("network.ssl_tokens_cache_persistence", runner)
        self.assertIn("network.http.http3.enable_0rtt", runner)
        self.assertIn("--max-connections 1", runner)
        self.assertNotIn(
            'kill -TERM "$pid"',
            runner[
                runner.index("stop_pid_clean()") : runner.index("stop_process_group()")
            ],
        )
        self.assertIn("requires its condition-specific Firefox A/B controls", runner)
        self.assertIn("warm NaiveFox lacks successful completion evidence", runner)

    def test_feature_merge_preserves_complete_blocks_and_old_fragments(self):
        with tempfile.TemporaryDirectory() as directory:
            documents = (
                {
                    "schema_version": 1,
                    "protocol": "h2",
                    "scenario": "initial",
                    "label": "firefox_a",
                    "session_id": "s1",
                    "experiment_block": "h2_b000001",
                    "features": {"whole_packet_count": 1.0},
                },
                {
                    "schema_version": 1,
                    "protocol": "h2",
                    "scenario": "initial",
                    "label": "naivefox",
                    "session_id": "s2",
                    "features": {"whole_packet_count": 2.0},
                },
            )
            for index, document in enumerate(documents):
                with open(
                    os.path.join(directory, f"{index}.json"),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    json.dump(document, stream)
            output = os.path.join(directory, "features.csv")
            FEATURES.merge(
                SimpleNamespace(
                    input_dir=directory,
                    output=output,
                    expected_per_cohort=None,
                )
            )
            with open(output, newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["experiment_block"], "h2_b000001")
            self.assertEqual(rows[1]["experiment_block"], "")

    def test_feature_merge_rejects_globally_balanced_but_broken_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = ("firefox_a", "firefox_b", "naivefox") * 2
            blocks = ("b1", "b2", "b1", "b1", "b2", "b2")
            scenarios = ("initial", "page", "initial", "initial", "page", "page")
            for index, (label, block, scenario) in enumerate(
                zip(labels, blocks, scenarios, strict=True)
            ):
                document = {
                    "schema_version": 1,
                    "protocol": "h2",
                    "scenario": scenario,
                    "label": label,
                    "session_id": f"s{index}",
                    "experiment_block": block,
                    "features": {"whole_packet_count": float(index)},
                }
                with open(
                    os.path.join(directory, f"{index}.json"),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    json.dump(document, stream)
            with self.assertRaisesRegex(SystemExit, "incomplete experiment block"):
                FEATURES.merge(
                    SimpleNamespace(
                        input_dir=directory,
                        output=os.path.join(directory, "features.csv"),
                        expected_per_cohort=2,
                    )
                )

    def test_feature_merge_accepts_complete_multi_arm_superblock(self):
        with tempfile.TemporaryDirectory() as directory:
            members = (
                ("firefox_a", "reference"),
                ("firefox_b", "reference"),
                ("naivefox", "gate"),
                ("naivefox", "root"),
                ("naivefox", "tree-complete"),
                ("naivefox", "tree-early-overlap"),
                ("naivefox", "tree-root-overlap"),
                ("naivefox", "tree-overlap"),
            )
            for index, (label, arm) in enumerate(members):
                document = {
                    "schema_version": 1,
                    "protocol": "h3",
                    "scenario": "initial",
                    "label": label,
                    "naivefox_arm": arm,
                    "session_id": f"s{index}",
                    "experiment_block": "h3_sb000000",
                    "features": {"whole_packet_count": float(index)},
                }
                with open(
                    os.path.join(directory, f"{index}.json"),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    json.dump(document, stream)
            output = os.path.join(directory, "features-superblocks.csv")
            FEATURES.merge(
                SimpleNamespace(
                    input_dir=directory,
                    output=output,
                    expected_per_cohort=None,
                    expected_superblocks=1,
                    expected_superblock_arms=(
                        "gate,root,tree-complete,tree-early-overlap,"
                        "tree-root-overlap,tree-overlap"
                    ),
                )
            )
            with open(output, newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 8)
            self.assertEqual(
                {row["naivefox_arm"] for row in rows},
                {
                    "reference",
                    "gate",
                    "root",
                    "tree-complete",
                    "tree-early-overlap",
                    "tree-root-overlap",
                    "tree-overlap",
                },
            )


if __name__ == "__main__":
    unittest.main()
