import copy
import importlib.util
import json
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit


def load_script(name, module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).with_name(name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load_script("transport_fixture.py", "transport_fixture_tested")
adversarial = load_script(
    "run-transport-adversarial-tests.py", "transport_adversarial_tested"
)


class RuntimeFixtureTests(unittest.TestCase):
    def test_secondary_carrier_can_finish_during_startup(self):
        stats = {
            "ws_opened": 1,
            "h3_opened": 0,
            "peers": [
                {"opened": 46, "peak_streams": 32},
                {"opened": 8, "peak_streams": 8},
            ],
        }
        fixture.validate_carrier_stats(stats, "h2")
        with self.assertRaises(RuntimeError):
            fixture.validate_carrier_stats(stats, "h3")
        invalid = copy.deepcopy(stats)
        invalid["peers"] = invalid["peers"][:1]
        with self.assertRaises(RuntimeError):
            fixture.validate_carrier_stats(invalid, "h2")
        invalid = copy.deepcopy(stats)
        invalid["peers"][0]["peak_streams"] = 33
        with self.assertRaises(RuntimeError):
            fixture.validate_carrier_stats(invalid, "h2")

    def test_current_config_has_one_transport_and_encoded_credentials(self):
        for protocol in ("h2", "h3"):
            config = fixture.client_config(
                protocol,
                18443,
                "user@example",
                "p:/a% ss",
                {"socks": 18080, "http": 18081},
                2,
            )
            self.assertNotIn("transport", config)
            self.assertNotIn("preamble", config)
            proxy = urlsplit(config["proxy"])
            self.assertEqual(unquote(proxy.username), "user@example")
            self.assertEqual(unquote(proxy.password), "p:/a% ss")
            self.assertEqual(proxy.scheme, "quic" if protocol == "h3" else "https")

    def test_caddy_owns_authentication_and_policy(self):
        text = fixture.caddyfile_text()
        self.assertNotIn("forward_proxy", text)
        self.assertNotIn("profile ", text)
        self.assertEqual(text.count("naivefox_transport {"), 1)
        self.assertIn("basic_auth", text)
        self.assertIn("allow 127.0.0.1/32", text)
        self.assertIn("deny all", text)

    def test_partial_fixture_credentials_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "partial fixture credentials"):
            fixture.client_config(
                "h2", 18443, "user", None, {"socks": 18080, "http": 18081}, 2
            )

    def test_optional_port_policy_is_explicit_and_bounded(self):
        text = fixture.caddyfile_text([18080, 18081])
        self.assertIn("ports 18080 18081", text)
        for invalid in ([0], [65536], [True], ["18080"]):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                fixture.caddyfile_text(invalid)

    def test_confirmation_faults_preserve_public_site_and_fixed_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixture.prepare_application(Path(directory))
            for case in (
                "hello-missing",
                "hello-contract",
                "hello-site",
                "hello-duplicate",
            ):
                server = {
                    "routes": [
                        {
                            "handle": [
                                {
                                    "handler": "naivefox_transport",
                                    "application_root": str(root),
                                }
                            ]
                        }
                    ]
                }
                adversarial.mutation(case)(server)
                route = server["routes"][0]
                self.assertEqual(route["match"][0]["path"], ["/api/events/brief"])
                if case == "hello-duplicate":
                    response = route["handle"][-1]
                    self.assertEqual(response["handler"], "file_server")
                    self.assertEqual(
                        (Path(response["root"]) / "cell.bin").stat().st_size, 8192
                    )
                    continue
                response = route["handle"][0]
                self.assertEqual(response["status_code"], 200)
                self.assertEqual(len(response["body"]), 8192)
                self.assertFalse(
                    any(name.startswith("X-App-") for name in response["headers"])
                )

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
        with mock.patch.object(
            fixture, "open_tunnel", side_effect=RuntimeError("refused")
        ):
            with self.assertRaisesRegex(
                RuntimeError, "concurrent logical stream gate failed"
            ):
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

    def policy_reply(self, *, accepted, tail=b"", timed_out=False, strict=False):
        class Stream:
            def __init__(self):
                self.buffer = (
                    b"\x05\x00\x05"
                    + (b"\x00" if accepted else b"\x02")
                    + b"\x00\x01"
                    + b"\x00" * 6
                )
                self.timeouts = []
                self.closed = False

            def sendall(self, _):
                pass

            def settimeout(self, timeout):
                self.timeouts.append(timeout)

            def recv(self, count):
                if self.buffer:
                    result, self.buffer = self.buffer[:count], self.buffer[count:]
                    return result
                if timed_out:
                    raise socket.timeout("policy fixture timeout")
                return tail

            def close(self):
                self.closed = True

        stream = Stream()
        with mock.patch.object(
            fixture.socket, "create_connection", return_value=stream
        ):
            if strict:
                fixture.open_tunnel({"socks": 18080}, "socks", 18081, rejected=True)
            else:
                fixture.reject_policy({"socks": 18080}, "socks", 18081)
        self.assertTrue(stream.closed)
        return stream

    def test_policy_refusal_accepts_explicit_reject(self):
        self.policy_reply(accepted=False)

    def test_authentication_refusal_remains_strict_despite_fast_open_eof(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected local CONNECT success"):
            self.policy_reply(accepted=True, strict=True)


if __name__ == "__main__":
    unittest.main()
