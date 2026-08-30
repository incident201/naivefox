#!/usr/bin/env python3

import importlib.util
import pathlib
import socket
import threading
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).parents[2] / "tools" / "verify-staged-windows-smoke.py"
SPEC = importlib.util.spec_from_file_location("verify_staged_windows_smoke", SCRIPT)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class AlwaysAliveProcess:
    def poll(self):
        return None


class LifecycleChurnHelpersTest(unittest.TestCase):
    def test_live_transfers_require_matching_payloads(self):
        with mock.patch.object(SMOKE, "fetch_digest", return_value="expected") as fetch:
            SMOKE.verify_live_transfers("http://target/", "http://localhost:1234", "expected")
            self.assertEqual(fetch.call_count, 8)
        with mock.patch.object(SMOKE, "fetch_digest", return_value="different"):
            with self.assertRaisesRegex(AssertionError, "body mismatch"):
                SMOKE.verify_live_transfers("http://target/", "socks5h://localhost:1234", "expected")

    def test_failed_live_request_cannot_hash_as_success(self):
        result = mock.Mock(returncode=7, stdout=b"error body")
        with mock.patch.object(SMOKE.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(AssertionError, "curl exit 7"):
                SMOKE.fetch_digest("http://target/")

    def test_live_timeout_does_not_echo_private_target(self):
        private_target = "http://private-target.invalid/secret"
        error = SMOKE.subprocess.TimeoutExpired(["curl.exe", private_target], 70)
        with mock.patch.object(SMOKE.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(AssertionError, "^live transfer timed out$"):
                SMOKE.fetch_digest(private_target)

    def test_socks_request_is_a_valid_domain_connect(self):
        request = SMOKE.make_socks_connect_request("race.test", 8443)
        self.assertEqual(request[:4], b"\x05\x01\x00\x03")
        self.assertEqual(request[4], len(b"race.test"))
        self.assertEqual(request[5:-2], b"race.test")
        self.assertEqual(int.from_bytes(request[-2:], "big"), 8443)

    def test_socks_request_rejects_non_ascii_and_invalid_lengths(self):
        with self.assertRaises(UnicodeEncodeError):
            SMOKE.make_socks_connect_request("not-ascii-\u2603")
        with self.assertRaises(ValueError):
            SMOKE.make_socks_connect_request("")
        with self.assertRaises(ValueError):
            SMOKE.make_socks_connect_request("x" * 256)

    def test_http_request_is_a_complete_valid_connect(self):
        request = SMOKE.make_http_connect_request("race.test:8443")
        self.assertEqual(
            request,
            b"CONNECT race.test:8443 HTTP/1.1\r\nHost: race.test:8443\r\n\r\n",
        )

    def test_disconnect_waves_send_every_request(self):
        waves = 3
        width = 6
        expected = waves * width
        received = []
        done = threading.Event()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(expected)
            listener.settimeout(3.0)
            port = listener.getsockname()[1]

            def accept_all():
                try:
                    while len(received) < expected:
                        connection, _ = listener.accept()
                        with connection:
                            chunks = []
                            while True:
                                chunk = connection.recv(256)
                                if not chunk:
                                    break
                                chunks.append(chunk)
                            received.append(b"".join(chunks))
                finally:
                    done.set()

            server = threading.Thread(target=accept_all, daemon=True)
            server.start()
            payload = SMOKE.make_http_connect_request()
            SMOKE.run_disconnect_waves(
                AlwaysAliveProcess(),
                port,
                payload,
                "http",
                "TEST",
                waves=waves,
                width=width,
            )
            self.assertTrue(done.wait(3.0), "test listener did not drain requests")
            self.assertEqual(received, [payload] * expected)
            server.join(timeout=1.0)

    def test_socks_drop_negotiates_before_connect(self):
        received = []
        ready = threading.Event()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]

            def serve_one():
                ready.set()
                connection, _ = listener.accept()
                with connection:
                    received.append(SMOKE.recv_exact(connection, 3))
                    connection.sendall(b"\x05\x00")
                    chunks = []
                    while True:
                        chunk = connection.recv(256)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    received.append(b"".join(chunks))

            server = threading.Thread(target=serve_one, daemon=True)
            server.start()
            self.assertTrue(ready.wait(1.0))
            request = SMOKE.make_socks_connect_request("race.test", 443)
            SMOKE.connect_and_drop(port, request, "socks")
            server.join(timeout=2.0)
            self.assertFalse(server.is_alive())
            self.assertEqual(received, [SMOKE.SOCKS_NO_AUTH_GREETING, request])

    def test_proxy_credentials_are_redacted_and_detected(self):
        url = "https://user%40name:p%40ss@proxy.test:443/path"
        self.assertEqual(SMOKE.redact_proxy_url(url), "https://proxy.test:443/path")
        self.assertEqual(
            set(SMOKE.proxy_secret_tokens(url)),
            {"user%40name", "user@name", "p%40ss", "p@ss"},
        )
        with self.assertRaisesRegex(AssertionError, "credential leaked"):
            SMOKE.assert_no_proxy_secrets(
                "unexpected p@ss value", SMOKE.proxy_secret_tokens(url), "test"
            )


if __name__ == "__main__":
    unittest.main()
