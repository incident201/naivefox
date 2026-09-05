import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


suite = load("transport_smoke_tested", "run-no-connect-tests.py")
windows = load("transport_smoke_windows_tested", "run-no-connect-windows-tests.py")


class TransportSmokeTests(unittest.TestCase):
    def request(self, method="POST", uri="/api/bootstrap", proto="HTTP/3.0"):
        return {"method": method, "uri": uri, "proto": proto, "headers": {}}

    def test_hybrid_checks_each_request_protocol_not_only_the_combined_set(self):
        for protocol, wire in (("h2", "HTTP/2.0"), ("h3", "HTTP/3.0")):
            startup = self.request(proto=wire)
            websocket = self.request("GET", "/api/realtime", "HTTP/1.1")
            suite.check_smoke_requests([startup, websocket], protocol, True)
            for requests in ([self.request(proto="HTTP/1.1"), startup, websocket],
                             [startup, {**websocket, "proto": wire}], [startup],
                             [startup, websocket, websocket]):
                with self.assertRaises(RuntimeError):
                    suite.check_smoke_requests(requests, protocol, True)

    def test_default_classic_requires_two_selected_protocol_connects_without_ws(self):
        for protocol, wire in (("h2", "HTTP/2.0"), ("h3", "HTTP/3.0")):
            requests = [self.request("CONNECT", "localhost:1234", wire) for _ in range(2)]
            suite.check_smoke_requests(requests, protocol, True, classic=True)
            for invalid in (requests[:1], requests + [requests[0]],
                            [{**requests[0], "proto": "HTTP/1.1"}, requests[1]]):
                with self.assertRaises(RuntimeError):
                    suite.check_smoke_requests(invalid, protocol, True, classic=True)

    def test_finite_carrier_rejects_websocket_connect_and_auth_headers(self):
        request = self.request()
        suite.check_smoke_requests([request], "h3", False)
        for invalid in (self.request("GET", "/api/realtime", "HTTP/1.1"),
                        self.request("CONNECT"),
                        {**request, "headers": {"Authorization": ["redacted"]}}):
            with self.assertRaises(RuntimeError):
                suite.check_smoke_requests([request, invalid], "h3", False)

    def test_windows_relays_match_no_connect_tcp_requirement(self):
        for transport in ("classic", "no-connect"):
            self.assertEqual(windows.relay_protocols("h2", transport), ("h2",))
        self.assertEqual(windows.relay_protocols("h3", "classic"), ("h3",))
        self.assertEqual(windows.relay_protocols("h3", "no-connect"), ("h3", "h2"))

    def test_windows_readiness_accepts_complete_marker_and_cleanup_is_idempotent(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(10)"],
                                   stdout=subprocess.PIPE)
        try:
            windows.wait_for_relay(process, timeout=2)
        finally:
            windows.stop_relay(process)
            windows.stop_relay(process)
            process.stdout.close()
        self.assertIsNotNone(process.poll())

    def test_windows_partial_or_missing_relay_readiness_has_a_deadline(self):
        for message in ("rea", "bad\n"):
            code = "import sys,time;sys.stdout.write(" + repr(message) + ");sys.stdout.flush();time.sleep(10)"
            process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
            try:
                with self.assertRaisesRegex(RuntimeError, "readiness"):
                    windows.wait_for_relay(process, timeout=0.2)
            finally:
                windows.stop_relay(process)
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
