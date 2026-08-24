#!/usr/bin/env python3

import base64
import csv
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

HERE = os.path.dirname(__file__)


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
            rows.append(
                {
                    "schema_version": "1",
                    "protocol": "h2",
                    "scenario": member["scenario"],
                    "label": member["label"],
                    "naivefox_arm": member["naivefox_arm"],
                    "session_id": f"s{index}",
                    "experiment_block": member["experiment_block"],
                    "whole_packet_count": str(index),
                }
            )
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
            SUPERBLOCKS.validate_superblocks(rows, expected_blocks=1)

    def test_runner_preserves_single_arm_and_adds_same_base_superblocks(self):
        path = os.path.join(HERE, "run-camouflage-suite.sh")
        with open(path, encoding="utf-8") as stream:
            runner = stream.read()
        self.assertIn("--multi-arm-superblocks", runner)
        self.assertIn("--naivefox-arm off|gate|root", runner)
        self.assertIn("requires NAIVEFOX_CAPTURE_MODE=same-base", runner)
        self.assertIn("features-superblocks.csv", runner)
        self.assertIn('for arm in off gate root; do', runner)

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
        self.assertEqual(config["preamble"]["path"], CONFIG.PREAMBLE_PATH)
        self.assertLessEqual(config["preamble"]["max-bytes"], 64 * 1024)
        self.assertTrue(config["outer-session-gate"])

    def test_config_arm_validation_rejects_unknown_arm_and_invalid_ports(self):
        with self.assertRaisesRegex(ValueError, "off, gate, or root"):
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
        self.assertLess(
            caddyfile.index(f"respond {path}"), caddyfile.index("forward_proxy")
        )
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
                ("naivefox", "off"),
                ("naivefox", "gate"),
                ("naivefox", "root"),
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
                )
            )
            with open(output, newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 5)
            self.assertEqual(
                {row["naivefox_arm"] for row in rows},
                {"reference", "off", "gate", "root"},
            )


if __name__ == "__main__":
    unittest.main()
