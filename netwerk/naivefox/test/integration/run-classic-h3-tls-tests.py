#!/usr/bin/env python3
"""Require prompt classic H3 TLS rejection before any target dial or TCP fallback."""

import argparse
import importlib.util
import os
from pathlib import Path
import socketserver
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("no_connect_tests", HERE / "run-no-connect-tests.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class TCPCanary(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port):
        self.accepted = 0
        self.closed = False
        self.lock = threading.Lock()
        class Sink(socketserver.BaseRequestHandler):
            def handle(inner):
                pass
        super().__init__(("127.0.0.1", port), Sink)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def get_request(self):
        connection, address = super().get_request()
        with self.lock:
            self.accepted += 1
        return connection, address

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.shutdown()
        self.thread.join(timeout=5)
        # The client has exited, so drain any final queued TCP handshake before
        # checking the counter. A scheduling delay must not conceal fallback.
        self.socket.setblocking(False)
        while True:
            try:
                connection, _ = self.get_request()
            except BlockingIOError:
                break
            connection.close()
        self.server_close()


def run_case(args, root, preamble):
    directory = root / preamble
    directory.mkdir(mode=0o700)
    fixture.issue_certificates(directory)
    args.classic_preamble = preamble
    target = fixture.TargetServer()
    processes = []
    canary = None
    try:
        user, password = fixture.fixture_credentials()
        caddy, port = fixture.start_caddy(args, directory, "h3", target.server_address[1], user, password)
        processes.append(caddy)
        # The real Caddy listens only on UDP. A separate TCP sink on that same
        # port detects forbidden fallback even when the local request fails.
        canary = TCPCanary(port)
        client, ports = fixture.start_client(args, directory, "untrusted", "h3", port,
                                             "classic", user, password, 2, trusted=False)
        processes.append(client)
        durations = {}
        for listener in ("socks", "http"):
            started = time.monotonic()
            fixture.open_tunnel(ports, listener, target.server_address[1],
                                rejected=True, timeout=5)
            durations[listener] = round(time.monotonic() - started, 3)
        client.exited_cleanly()
        canary.close()
        fixture.require(target.accepted_connections == 0, "untrusted TLS reached the target")
        fixture.require(canary.accepted == 0, "strict H3 attempted TCP fallback")
        result = {"status": "PASS", "preamble": preamble, "rejection_seconds": durations,
                  "target_connections": 0, "tcp_fallback_connections": 0,
                  "explicit_local_rejection": True, "request_timeout_seconds": 5}
        fixture.private_json(directory / "result.json", result)
        print(f"PASS classic H3 untrusted TLS ({preamble}): both listeners, no TCP fallback or target dial", flush=True)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        if canary is not None:
            canary.close()
        target.shutdown()
        target.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--preamble", choices=("default", "off", "both"), default="both")
    args = parser.parse_args()
    for name in ("objdir", "runtime", "caddy"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    previous_umask = os.umask(0o077)
    root = args.objdir / "naivefox-fixture"
    root.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="classic-h3-tls-", dir=root))
    try:
        modes = ("default", "off") if args.preamble == "both" else (args.preamble,)
        results = [run_case(args, run, mode) for mode in modes]
        fixture.private_json(run / "result.json", {"status": "PASS", "cases": results})
        print(f"Private fixture and sanitized result: {run}", flush=True)
        return 0
    except (OSError, RuntimeError) as error:
        fixture.private_json(run / "result.json", {"status": "FAIL", "error_type": type(error).__name__})
        print(f"FAIL: {type(error).__name__}; private diagnostics: {run}", flush=True)
        return 1
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
