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
CONTROLLER = load("camouflage_browser_controller", "camouflage_browser_controller.py")
FEATURES = load("camouflage_features", "camouflage_features.py")
SAMPLE = load("camouflage_sample_validation", "camouflage_sample_validation.py")
SUPERBLOCKS = load("camouflage_superblocks", "camouflage_superblocks.py")
TARGET = load("target_server", "target_server.py")


class CamouflageHarnessTests(unittest.TestCase):
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
            runtime.index("nsresult GeckoRuntime::WaitForNetworkStartup()") :
            runtime.index("nsresult GeckoRuntime::RunEventLoopSmoke()")
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
        self.assertIn("return WaitForNetworkStartup();", runtime)
        embedded = runner[
            runner.index("NaiveFoxRunEmbedded") : runner.index("NaiveFoxMain")
        ]
        self.assertLess(
            embedded.index("runtime.InitializeEmbedded("),
            embedded.index("RunLocalProxyServer(config.mListeners"),
        )
        standalone = runner[
            runner.index("const bool configMode") : runner.index("nsCString profile;")
        ]
        self.assertLess(
            standalone.index("runtime.Initialize(aArgc"),
            standalone.index("RunLocalProxyServer(config.mListeners"),
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

        observe = handler[handler.index('!strcmp(topic, NS_NETWORK_LINK_TOPIC)') :]
        self.assertIn("mConnMgr->VerifyTraffic()", observe)
        verify = entry[
            entry.index("void ConnectionEntry::VerifyTraffic()") :
            entry.index("void ConnectionEntry::InsertIntoIdleConnections_internal")
        ]
        self.assertIn(
            "network_http_move_to_pending_list_after_network_change", verify
        )
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

    def test_opt_in_superblock_arms_share_one_control_pair(self):
        arms = (
            "gate",
            "root",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-overlap",
        )
        rows = SUPERBLOCKS.schedule_rows(
            17, "h3", 2, ["browser_page"], arms=arms
        )
        SUPERBLOCKS.validate_superblocks(rows, expected_blocks=2, arms=arms)
        self.assertEqual(SUPERBLOCKS.infer_arms(rows), arms)
        for index in range(2):
            members = rows[index * 10 : (index + 1) * 10]
            self.assertEqual(
                {(row["label"], row["naivefox_arm"]) for row in members},
                {
                    ("firefox_a", "reference"),
                    ("firefox_b", "reference"),
                    *(("naivefox", arm) for arm in arms),
                },
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
        self.assertIn("packets_17_32", runner)
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
        self.assertIn('sslkeylog_unset=(-u SSLKEYLOGFILE)', runner)
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
        branch_start = runner.index("if [[ $diagnostic_naivefox_only == 1 ]]; then\n  diagnostic_protocols=")
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
        self.assertIn("ethtool -K lo gro off gso off tso off", helper)
        self.assertIn("tx-udp-segmentation off tx-gso-list off", helper)
        self.assertIn("offload_state=$(ethtool -k lo)", helper)
        self.assertIn("isolated loopback offload remained enabled", helper)
        self.assertIn("udp.length>1500", runner)
        self.assertIn(
            "capture_offload_policy=host_interface_offload_state_unmodified",
            runner,
        )
        self.assertIn("UDP offload superframe", runner)
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
        self.assertIn("invalid isolated camouflage network invocation", invalid_helper.stderr)

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
        ):
            config = CONFIG.build_config(
                arm, "h3", 1080, 4433, "fixture-user", "fixture-pass"
            )
            self.assertEqual(config["preamble"]["mode"], mode)
            self.assertEqual(config["preamble"]["max-assets"], 1)
            self.assertEqual(config["preamble"]["path"], CONFIG.PREAMBLE_PATH)
        alias = CONFIG.build_config(
            "document-complete", "h2", 1080, 4433, "user", "pass"
        )
        self.assertEqual(alias["preamble"]["mode"], "document-complete")

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

    def test_tree_fixture_assets_leave_streams_live_within_budget(self):
        page = TARGET.Handler.camouflage_page(
            object(), {"scenario": ["browser_page"]}
        )
        self.assertEqual(len(TARGET.CAMOUFLAGE_STYLE_CSS), 64 * 1024)
        self.assertEqual(len(TARGET.CAMOUFLAGE_APP_JS), 128 * 1024)
        self.assertTrue(TARGET.CAMOUFLAGE_STYLE_CSS.startswith(b":root{"))
        self.assertTrue(TARGET.CAMOUFLAGE_APP_JS.startswith(b"(()=>{"))
        aggregate = (
            len(page)
            + len(TARGET.CAMOUFLAGE_STYLE_CSS)
            + len(TARGET.CAMOUFLAGE_APP_JS)
        )
        self.assertLess(aggregate, CONFIG.TREE_PREAMBLE_MAX_BYTES)
        self.assertEqual(CONFIG.TREE_PREAMBLE_MAX_ASSETS, 2)

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

    def test_sample_validation_accepts_expected_arm_evidence(self):
        one_connection = {
            "protocol": "h3",
            "features": {"lifecycle_connection_count": 1.0},
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
            SAMPLE.validate_sample(
                "tree-root-overlap", "h3", result, one_connection
            )
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
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
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
            "Connection 1 established target=localhost:443 "
            "outer=h3 padding=yes\n"
        )
        drain = (
            "Connection 1 preamble root-overlap drain=complete "
            "completed_resources=2 protocol=h3\n"
        )
        timeout = "Connection 1 preamble background drain timed out\n"
        for arm, evidence in (
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
        body = suite.split("run_naivefox_sample() {", 1)[1].split(
            "scenario_csv=", 1
        )[0]
        reference_body = suite.split("run_reference_sample() {", 1)[1].split(
            "run_naivefox_sample() {", 1
        )[0]
        marker = "preamble root-overlap drain=complete completed_resources="
        self.assertIn(marker, body)
        self.assertNotIn(marker, reference_body)
        self.assertLess(body.index(marker), body.index("stop_capture"))

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
        self.assertNotIn("--socks-listen", sample_runner)
        self.assertNotIn("--profile", sample_runner)

    def test_direct_h3_browser_gets_forced_alt_svc_mapping(self):
        preferences = CONTROLLER.firefox_preferences("h3", 4433, 0)
        self.assertTrue(preferences["network.http.http3.enable"])
        self.assertEqual(
            preferences["network.http.http3.alt-svc-mapping-for-testing"],
            "localhost;h3=:4433",
        )

    def test_socks_browser_uses_fail_closed_pac_without_outer_h3_mapping(self):
        preferences = CONTROLLER.firefox_preferences("h3", 4433, 1080)
        self.assertFalse(preferences["network.http.http3.enable"])
        self.assertNotIn("network.http.http3.alt-svc-mapping-for-testing", preferences)
        self.assertEqual(preferences["network.proxy.type"], 2)
        self.assertFalse(preferences["network.proxy.failover_direct"])
        self.assertNotIn("network.proxy.socks", preferences)
        prefix = "data:application/x-ns-proxy-autoconfig;base64,"
        pac_url = preferences["network.proxy.autoconfig_url"]
        self.assertTrue(pac_url.startswith(prefix))
        pac = base64.b64decode(pac_url.removeprefix(prefix)).decode()
        self.assertEqual(pac, CONTROLLER.proxy_pac_script(1080))

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
        self.assertIn(
            'make_profile "$naivefox_profile" "$protocol" naivefox', runner
        )
        self.assertIn(
            'make_profile "$browser_profile" "$protocol" socks-browser '
            '"$socks_port"',
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
            script = "\n".join(
                (
                    "set -euo pipefail",
                    setup,
                    profile_functions,
                    f"make_profile {shlex.quote(reference)} h3 reference",
                    f"make_profile {shlex.quote(naivefox)} h3 naivefox",
                    f"make_profile {shlex.quote(socks)} h3 socks-browser 1080",
                )
            )
            subprocess.run(["bash"], input=script, text=True, check=True)

            with open(os.path.join(reference, "user.js"), encoding="utf-8") as stream:
                reference_prefs = stream.read()
            with open(os.path.join(naivefox, "user.js"), encoding="utf-8") as stream:
                naivefox_prefs = stream.read()
            with open(os.path.join(socks, "user.js"), encoding="utf-8") as stream:
                socks_prefs = stream.read()
            mapping_pref = "network.http.http3.alt-svc-mapping-for-testing"
            self.assertIn(mapping_pref, reference_prefs)
            self.assertIn('network.http.http3.enable", true', naivefox_prefs)
            self.assertNotIn(mapping_pref, naivefox_prefs)
            self.assertNotIn(mapping_pref, socks_prefs)
            self.assertIn('network.http.http3.enable", false', socks_prefs)

    def test_proxy_pac_sends_only_loopback_hosts_to_sample_socks(self):
        pac = CONTROLLER.proxy_pac_script(1080)
        for host in ("localhost", "127.0.0.1", "::1", "[::1]"):
            self.assertIn(f'host === "{host}"', pac)
        self.assertIn('return "SOCKS5 127.0.0.1:1080"', pac)
        self.assertIn(
            f'return "PROXY 127.0.0.1:{CONTROLLER.DEAD_LOCAL_PROXY_PORT}"',
            pac,
        )
        self.assertNotIn("DIRECT", pac)

    def test_proxy_pac_rejects_invalid_ports(self):
        for port in (0, 65536):
            with self.assertRaisesRegex(ValueError, "outside 1..65535"):
                CONTROLLER.proxy_pac_script(port)

    def test_commandline_profile_generator_uses_same_pac_preferences(self):
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(HERE, "camouflage_browser_controller.py"),
                "--generate-pac-user-js",
                "1080",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout, CONTROLLER.proxy_user_js(1080))
        self.assertIn('user_pref("network.proxy.type", 2);', result.stdout)
        self.assertIn('user_pref("network.proxy.autoconfig_url", "data:', result.stdout)
        self.assertIn(
            'user_pref("network.proxy.failover_direct", false);', result.stdout
        )
        self.assertNotIn("network.proxy.socks", result.stdout)

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
