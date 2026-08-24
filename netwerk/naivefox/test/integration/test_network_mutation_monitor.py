#!/usr/bin/env python3

import importlib.util
import io
import os
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest


HERE = os.path.dirname(__file__)
SPEC = importlib.util.spec_from_file_location(
    "network_mutation_monitor", os.path.join(HERE, "monitor-network-mutations.py")
)
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


def netlink_message(message_type, payload=b"", flags=0, sequence=0, process=0):
    length = MONITOR.NLMSG_HEADER_SIZE + len(payload)
    message = struct.pack("=IHHII", length, message_type, flags, sequence, process)
    message += payload
    return message + b"\0" * ((-length) % MONITOR.NLMSG_ALIGNMENT)


class FakeSocket:
    def __init__(self, results):
        self.results = list(results)
        self.blocking = None

    def recvmsg(self, _size):
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def setblocking(self, value):
        self.blocking = value


class NetworkMutationMonitorTests(unittest.TestCase):
    def test_parses_aligned_messages_and_records_only_invariant_mutations(self):
        data = b"".join(
            [
                netlink_message(16, payload=b"x", sequence=7, process=8),
                netlink_message(28, sequence=9, process=10),  # RTM_NEWNEIGH
                netlink_message(25, flags=3, sequence=11, process=12),
            ]
        )
        events = io.StringIO()
        MONITOR.record_datagram(data, events, MONITOR.time.monotonic_ns())
        lines = events.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("type=new-link sequence=7 process=8 flags=0", lines[0])
        self.assertIn("type=del-route sequence=11 process=12 flags=3", lines[1])
        self.assertNotIn("neighbor", events.getvalue())

    def test_rejects_short_header(self):
        with self.assertRaisesRegex(MONITOR.MonitorInvalidated, "aligned|header"):
            MONITOR.parse_netlink_messages(b"\0" * 12)

    def test_rejects_length_shorter_than_header(self):
        data = struct.pack("=IHHII", 15, 16, 0, 0, 0)
        with self.assertRaisesRegex(MONITOR.MonitorInvalidated, "length"):
            MONITOR.parse_netlink_messages(data)

    def test_rejects_payload_past_datagram(self):
        data = struct.pack("=IHHII", 20, 16, 0, 0, 0)
        with self.assertRaisesRegex(MONITOR.MonitorInvalidated, "payload"):
            MONITOR.parse_netlink_messages(data)

    def test_rejects_missing_alignment_padding(self):
        data = struct.pack("=IHHII", 17, 16, 0, 0, 0) + b"x"
        with self.assertRaisesRegex(MONITOR.MonitorInvalidated, "aligned"):
            MONITOR.parse_netlink_messages(data)

    def test_rejects_nlmsg_overrun_and_error(self):
        for message_type, text in (
            (MONITOR.NLMSG_OVERRUN, "overrun"),
            (MONITOR.NLMSG_ERROR, "NLMSG_ERROR"),
        ):
            with self.subTest(message_type=message_type):
                with self.assertRaisesRegex(MONITOR.MonitorInvalidated, text):
                    MONITOR.parse_netlink_messages(netlink_message(message_type))

    def test_recvmsg_truncation_invalidates_sample(self):
        fake = FakeSocket([(b"x", [], socket.MSG_TRUNC, (0, 0))])
        with self.assertRaisesRegex(MONITOR.MonitorInvalidated, "truncated"):
            MONITOR.receive_datagram(fake)

    def test_drain_processes_every_queued_datagram(self):
        fake = FakeSocket(
            [
                (netlink_message(20, sequence=1), [], 0, (0, 0)),
                (netlink_message(24, sequence=2), [], 0, (0, 0)),
                BlockingIOError(),
            ]
        )
        events = io.StringIO()
        MONITOR.drain_pending(fake, events, MONITOR.time.monotonic_ns())
        self.assertFalse(fake.blocking)
        self.assertEqual(len(events.getvalue().splitlines()), 2)

    def test_done_marker_is_atomic_private_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "done")
            MONITOR.write_done_marker(path)
            with open(path, encoding="utf-8") as marker:
                self.assertEqual(marker.read(), "done\n")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MONITOR.write_done_marker(path)
            with open(path, encoding="utf-8") as marker:
                self.assertEqual(marker.read(), "done\n")

    def test_sigterm_drains_and_publishes_done_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = os.path.join(directory, "ready")
            events = os.path.join(directory, "events")
            done = os.path.join(directory, "done")
            process = subprocess.Popen(
                [
                    sys.executable,
                    os.path.join(HERE, "monitor-network-mutations.py"),
                    "--ready",
                    ready,
                    "--events",
                    events,
                    "--done",
                    done,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not os.path.exists(ready) and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.fail(f"monitor exited before ready: {stdout}{stderr}")
                self.assertTrue(os.path.exists(ready))
                os.kill(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                with open(done, encoding="utf-8") as marker:
                    self.assertEqual(marker.read(), "done\n")
                self.assertTrue(os.path.exists(events))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
