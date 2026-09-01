import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "android_transport_selection_tested", Path(__file__).with_name("run-android-transport-tests.py"))
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)


class AndroidTransportSelectionTests(unittest.TestCase):
    def request(self, method="POST", uri="/api/carrier", proto="HTTP/2.0", headers=None):
        return {"method": method, "uri": uri, "proto": proto, "headers": headers or {}}

    def test_matrix_covers_default_json_and_explicit_overrides(self):
        cases = selection.selection_cases()
        self.assertEqual({case[2] for case in cases},
                         {None, "classic", "no-connect", "no-connect-hybrid",
                          "no-connect-hybrid-asymmetric"})
        self.assertIn(("default-classic", None, None, "classic"), cases)
        self.assertIn(("json-no-connect", "no-connect", None, "no-connect"), cases)
        self.assertIn(("override-json-classic", "no-connect", "classic", "classic"), cases)

    def test_classic_needs_all_six_real_connects(self):
        requests = [self.request("CONNECT", "localhost:1234") for _ in range(6)]
        selection.check_requests(requests, "classic", "h2")
        for invalid in (requests[:5], requests + [requests[0]],
                        requests + [self.request("GET", "/api/realtime", "HTTP/1.1")]):
            with self.assertRaises(RuntimeError):
                selection.check_requests(invalid, "classic", "h2")

    def test_finite_transport_rejects_fallback_websocket_and_classic_headers(self):
        selection.check_requests([self.request()], "no-connect", "h2")
        for request in (self.request("CONNECT"),
                        self.request("GET", "/api/realtime", "HTTP/1.1"),
                        self.request(headers={"X-Classic-Only": ["enabled"]}),
                        self.request(headers={"Proxy-Authorization": ["redacted"]})):
            with self.assertRaises(RuntimeError):
                selection.check_requests([self.request(), request], "no-connect", "h2")

    def test_hybrid_requires_actual_http1_websocket_after_selected_http_startup(self):
        for protocol, wire in (("h2", "HTTP/2.0"), ("h3", "HTTP/3.0")):
            startup = self.request(proto=wire)
            websocket = self.request("GET", "/api/realtime", "HTTP/1.1")
            for transport in ("no-connect-hybrid",
                              "no-connect-hybrid-asymmetric"):
                selection.check_requests([startup, websocket], transport, protocol)
                for invalid in ([startup], [websocket],
                                [startup, {**websocket, "proto": wire}]):
                    with self.assertRaises(RuntimeError):
                        selection.check_requests(invalid, transport, protocol)


if __name__ == "__main__":
    unittest.main()
