import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit


def load_script(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load_script("run-no-connect-tests.py", "no_connect_fixture_tested")
adversarial = load_script("run-no-connect-adversarial-tests.py", "no_connect_adversarial_tested")


class NoConnectFixtureTests(unittest.TestCase):
    def test_modes_share_encoded_credentials_and_all_other_config_fields(self):
        user, password = "fixture user@example", "p:/a% ss"
        ports = {"socks": 18080, "http": 18081}
        for protocol in ("h2", "h3"):
            for preamble in ("off", "default"):
                with self.subTest(protocol=protocol, preamble=preamble):
                    classic = fixture.client_config(protocol, 18443, "classic", user, password, ports, 2, preamble)
                    native = fixture.client_config(protocol, 18443, "no-connect", user, password, ports, 2, preamble)
                    self.assertEqual(classic.pop("transport"), "classic")
                    self.assertEqual(native.pop("transport"), "no-connect")
                    self.assertEqual(classic, native)
                    self.assertNotIn("no-connect-key", native)
                    proxy = urlsplit(native["proxy"])
                    self.assertEqual(unquote(proxy.username), user)
                    self.assertEqual(unquote(proxy.password), password)
                    self.assertIn("%40", native["proxy"])
                    self.assertIn("%25", native["proxy"])

    def test_absent_credentials_have_no_userinfo_for_either_mode(self):
        for transport in ("classic", "no-connect"):
            config = fixture.client_config("h2", 18443, transport, None, None,
                                           {"socks": 18080, "http": 18081}, 2)
            self.assertIsNone(urlsplit(config["proxy"]).username)
            self.assertNotIn("@", config["proxy"])

    def test_partial_fixture_credentials_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "partial fixture credentials"):
            fixture.client_config("h2", 18443, "classic", "user", None,
                                  {"socks": 18080, "http": 18081}, 2)

    def test_caddy_has_one_nested_real_forward_proxy_without_target_list(self):
        text = fixture.caddyfile_text()
        self.assertEqual(text.count("forward_proxy {"), 1)
        self.assertIn('basic_auth "{$NF_PROXY_USER}" "{$NF_PROXY_PASSWORD}"', text)
        self.assertIn("            forward_proxy {", text)
        self.assertNotIn("allowed_targets", text)
        self.assertNotIn("TRANSPORT_KEY", text)
        self.assertNotRegex(text, r"(?m)^\s*(?:key|hosts|ports)\s")
        self.assertIn("allow 127.0.0.1/32", text)
        self.assertIn("deny all", text)

    def test_optional_port_policy_is_explicit_and_bounded(self):
        text = fixture.caddyfile_text([18080, 18081])
        self.assertIn("ports 18080 18081", text)
        for invalid in ([0], [65536], [True], ["18080"]):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                fixture.caddyfile_text(invalid)

    def test_auth_mode_faults_keep_valid_root_profile_and_capacity(self):
        for case in ("auth-mode-missing", "auth-mode-legacy"):
            server = {"routes": []}
            adversarial.mutation(case)(server)
            response = server["routes"][0]["handle"][0]
            self.assertEqual(response["status_code"], 200)
            self.assertEqual(len(response["body"]), 4096)
            self.assertEqual(response["headers"]["X-App-Profile"], ["continuous-bulk-pipeline"])
            self.assertNotEqual(response["headers"].get("X-App-Auth"), ["basic"])
            self.assertIn("Set-Cookie", response["headers"])

    def test_concurrent_gate_holds_all_forty_streams_before_any_data(self):
        lock = threading.Lock()
        counts = {"opened": 0, "closed": 0, "writes": 0}

        class Stream:
            def __init__(self):
                self.buffer = b""
                with lock:
                    counts["opened"] += 1

            def __enter__(self):
                return self

            def __exit__(self, *_):
                with lock:
                    counts["closed"] += 1

            def sendall(self, value):
                with lock:
                    if counts["opened"] != 40:
                        raise AssertionError("payload sent before all streams opened")
                    counts["writes"] += 1
                self.buffer += value[1:]

            def recv(self, count):
                result, self.buffer = self.buffer[:count], self.buffer[count:]
                return result

            def shutdown(self, _):
                pass

        with mock.patch.object(fixture, "open_tunnel", side_effect=lambda *_: Stream()):
            fixture.concurrent_open_streams({"socks": 18080}, 18081)
        self.assertEqual(counts, {"opened": 40, "closed": 40, "writes": 40})

    def test_one_carrier_limit_cannot_satisfy_concurrent_gate(self):
        with self.assertRaisesRegex(RuntimeError, "exceed one carrier"):
            fixture.concurrent_open_streams({"socks": 18080}, 18081, 32)

    def test_failed_open_aborts_other_barrier_waiters(self):
        with mock.patch.object(fixture, "open_tunnel", side_effect=RuntimeError("refused")):
            with self.assertRaisesRegex(RuntimeError, "concurrent logical stream gate failed"):
                fixture.concurrent_open_streams({"socks": 18080}, 18081)

    def test_auth_partition_keeps_authenticated_stream_alive_during_rejection(self):
        calls = []
        live = []

        class Stream:
            def __init__(self):
                self.buffer = b""
                self.initial = True

            def __enter__(self):
                live.append(self)
                return self

            def __exit__(self, *_):
                live.remove(self)

            def sendall(self, value):
                if self.initial:
                    self.initial = False
                    if not value.startswith(b"E"):
                        raise AssertionError("missing echo handshake")
                    value = value[1:]
                self.buffer += value

            def recv(self, count):
                result, self.buffer = self.buffer[:count], self.buffer[count:]
                return result

            def shutdown(self, _):
                pass

        def open_stream(ports, listener, target, rejected=False):
            calls.append((listener, rejected))
            if len(calls) > 1:
                self.assertEqual(len(live), 1)
            if rejected:
                self.assertEqual(listener, "http")
                return None
            self.assertEqual(listener, "socks")
            return Stream()

        with mock.patch.object(fixture, "open_tunnel", side_effect=open_stream):
            fixture.auth_partition_streams({"socks": 18080, "http": 18081}, 18082)
        self.assertEqual(calls, [("socks", False), ("http", True), ("socks", False)])
        self.assertEqual(live, [])

    def shared_config_run(self, drift=False):
        ports = {"socks": 28080, "http": 28081}
        requests = []
        modes = []
        paths = []
        owner = self

        class Process:
            def __init__(self, mode=None):
                self.mode = mode

            def stop(self):
                pass

            def exited_cleanly(self):
                if self.mode == "classic":
                    requests.extend([{"method": "CONNECT"}] * 2)
                else:
                    requests.append({"method": "POST", "headers": {}})

        def client(inputs, directory, name, protocol, proxy_port, transport, user, password, count):
            modes.append(transport)
            paths.append(inputs.client_config_path)
            if hasattr(inputs, "base_client_config"):
                config = copy.deepcopy(inputs.base_client_config)
                config["transport"] = transport
                owner.assertEqual(inputs.listener_ports, ports)
            else:
                config = fixture.client_config(protocol, proxy_port, transport, user, password, ports, count)
            if drift and len(modes) == 2:
                config["listen"][0] = "socks://127.0.0.1:29999"
            fixture.private_json(inputs.client_config_path, config)
            return Process(transport), dict(ports)

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(fixture, "issue_certificates"), \
                 mock.patch.object(fixture, "start_caddy", return_value=(Process(), 28443)), \
                 mock.patch.object(fixture, "start_client", side_effect=client), \
                 mock.patch.object(fixture, "download"), \
                 mock.patch.object(fixture, "access_requests", side_effect=lambda _: list(requests)):
                result = fixture.check_shared_config(SimpleNamespace(), Path(directory), "h2", 28090,
                                                     "user", "password")
        self.assertEqual(modes, ["classic", "no-connect", "classic", "no-connect"])
        self.assertEqual(len(set(paths)), 1)
        return result

    def test_shared_config_switch_changes_only_transport_and_reuses_ports(self):
        result = self.shared_config_run()
        self.assertTrue(result["only_transport_changed"])
        self.assertTrue(result["listener_ports_reused"])
        self.assertEqual(result["successful_transfers"], 8)

    def test_shared_config_switch_rejects_platform_port_drift(self):
        with self.assertRaisesRegex(RuntimeError, "fields other than transport"):
            self.shared_config_run(drift=True)


if __name__ == "__main__":
    unittest.main()
