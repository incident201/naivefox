import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "no_connect_android_fixture_tested", Path(__file__).with_name("run-no-connect-android.py"))
android = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android)


class AndroidFixtureTests(unittest.TestCase):
    def fixture(self, work):
        fixture = object.__new__(android.AndroidFixture)
        fixture.work = work
        fixture.args = SimpleNamespace(host_alias="10.0.2.2")
        fixture.adb = ["adb", "-s", "owned-emulator"]
        fixture.remote = "/data/local/tmp/naivefox-no-connect-owned"
        fixture.runtime = fixture.remote + "/runtime"
        fixture.ports = set()
        fixture.shared_configs = {}
        fixture.processes = []
        return fixture

    def test_new_listeners_replace_host_ports_with_one_guest_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = self.fixture(work)
            ports = {"socks": 18080, "http": 18081}
            completed = subprocess.CompletedProcess([], 0, b"41000 41001\n")
            with mock.patch.object(fixture, "capture_command", return_value=completed) as capture, \
                 mock.patch.object(android.suite, "free_port", side_effect=AssertionError("host allocation forbidden")):
                fixture.choose_listener_ports(SimpleNamespace(), work, ports)
            self.assertEqual(ports, {"socks": 41000, "http": 41001})
            self.assertEqual(fixture.ports, {41000, 41001})
            self.assertEqual(capture.call_count, 1)
            self.assertEqual(capture.call_args.args[1:],
                             ("shell", fixture.remote + "/probe", "--allocate-listeners"))

    def test_shared_config_reuses_actual_guest_ports_without_reallocation(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            ports = {"socks": 41000, "http": 41001}
            with mock.patch.object(fixture, "capture_command", side_effect=AssertionError("shared ports changed")):
                for mode in ("classic", "no-connect", "classic", "no-connect"):
                    actual = dict(ports)
                    fixture.choose_listener_ports(SimpleNamespace(listener_ports=ports), Path(directory), actual)
                    self.assertEqual(actual, ports)
                    fixture.ports.difference_update(actual.values())

    def test_owned_guest_ports_are_not_retried_or_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            fixture.ports.add(41000)
            completed = subprocess.CompletedProcess([], 0, b"41000 41001\n")
            with mock.patch.object(fixture, "capture_command", return_value=completed) as capture:
                with self.assertRaisesRegex(RuntimeError, "still owned"):
                    fixture.choose_listener_ports(SimpleNamespace(), Path(directory), {"socks": 1, "http": 2})
            self.assertEqual(capture.call_count, 1)

    def test_invalid_or_failed_allocator_never_changes_listeners(self):
        for status, output in ((1, b""), (0, b"0 41001\n"), (0, b"65536 41001\n"),
                               (0, b"41000 41000\n"), (0, b"41000 41001 extra\n")):
            with self.subTest(status=status, output=output), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(Path(directory))
                ports = {"socks": 1, "http": 2}
                completed = subprocess.CompletedProcess([], status, output)
                with mock.patch.object(fixture, "capture_command", return_value=completed):
                    with self.assertRaises(RuntimeError):
                        fixture.choose_listener_ports(SimpleNamespace(), Path(directory), ports)
                self.assertEqual(ports, {"socks": 1, "http": 2})
                self.assertFalse(fixture.ports)

    def test_shared_port_drift_and_duplicate_pair_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            for fixed, proposed in (({"socks": 41000, "http": 41001}, {"socks": 41002, "http": 41001}),
                                    ({"socks": 41000, "http": 41000}, {"socks": 41000, "http": 41000})):
                with self.subTest(fixed=fixed), self.assertRaises(RuntimeError):
                    fixture.choose_listener_ports(SimpleNamespace(listener_ports=fixed), Path(directory), proposed)

    def test_failure_capture_retains_result_and_only_owned_pid_logcat(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = self.fixture(work)
            commands = []

            def run(command, **kwargs):
                commands.append(command)
                if command[-1].endswith("/pid"):
                    output = b"4321\n"
                elif command[-1].endswith("/result"):
                    output = b"version=test\nstatus=3\nstop_requested=0\n"
                else:
                    output = b"owned process diagnostic\n"
                return subprocess.CompletedProcess(command, 0, output)

            with mock.patch.object(android.subprocess, "run", side_effect=run):
                fixture.capture_failure(work, fixture.remote + "/client-owned", "startup", 1, RuntimeError())
            diagnostics = work / "diagnostics-startup"
            summary = json.loads((diagnostics / "summary.json").read_text())
            self.assertEqual(summary["adb_shell_exit_code"], 1)
            self.assertEqual(summary["owned_pid"], 4321)
            self.assertTrue(summary["harness_result_available"])
            self.assertIn("status=3", (diagnostics / "result.txt").read_text())
            logcat = next(command for command in commands if "logcat" in command)
            self.assertIn("--pid=4321", logcat)
            self.assertIn("-d", logcat)
            self.assertNotIn("-c", logcat)
            self.assertIn("undetermined", summary["cause"])

    def test_failure_without_pid_does_not_dump_other_process_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = self.fixture(work)
            with mock.patch.object(android.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"missing")) as run:
                fixture.capture_failure(work, fixture.remote + "/client-owned", "startup", 137)
            self.assertFalse(any("logcat" in call.args[0] for call in run.call_args_list))
            details = json.loads((work / "diagnostics-startup/summary.json").read_text())
            self.assertEqual(details["adb_shell_exit_code"], 137)
            self.assertIsNone(details["owned_pid"])
            self.assertFalse(details["harness_result_available"])

    def test_startup_failure_is_captured_before_remote_cleanup_without_retry(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = self.fixture(work)

            class FailedProcess:
                def __init__(self, argv, directory, name, env):
                    events.append("launch")
                    self.process = SimpleNamespace(poll=lambda: 1)
                    self.log_path = directory / "client.log"
                    self.log_path.write_text("SOCKS5 listening only\n")

                def stop(self):
                    events.append("stop")

            def call(*arguments, **kwargs):
                if "rm" in arguments:
                    events.append("remote-cleanup")

            allocated = subprocess.CompletedProcess([], 0, b"41000 41001\n")
            with mock.patch.object(android.suite, "Process", FailedProcess), \
                 mock.patch.object(android.suite, "wait_until", side_effect=RuntimeError("startup failed")), \
                 mock.patch.object(fixture, "capture_command", return_value=allocated), \
                 mock.patch.object(fixture, "call", side_effect=call), \
                 mock.patch.object(fixture, "capture_failure", side_effect=lambda *args: events.append("capture-" + args[2])):
                with self.assertRaisesRegex(RuntimeError, "startup failed"):
                    fixture.start(SimpleNamespace(), work, {"listen": []}, {}, {"socks": 1, "http": 2})
                fixture.close()
            self.assertEqual(events.count("launch"), 1)
            self.assertLess(events.index("capture-startup"), events.index("stop"))
            self.assertLess(events.index("capture-startup"), events.index("remote-cleanup"))
            self.assertFalse(fixture.ports)

    def test_embedded_selector_preserves_null_empty_and_frozen_device_config(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = self.fixture(work)
            launches = []

            class ReadyProcess:
                def __init__(self, argv, directory, name, env):
                    launches.append(shlex.split(argv[-1].split("exec ", 1)[1]))
                    self.process = SimpleNamespace(poll=lambda: 0)
                    self.log_path = directory / "client.log"
                    self.log_path.write_text("")

                def stop(self):
                    pass

            ports = {"socks": 41000, "http": 41001}
            config_path = work / "shared.json"
            with mock.patch.object(android.suite, "Process", ReadyProcess), \
                 mock.patch.object(android.suite, "wait_until"), \
                 mock.patch.object(fixture, "call") as call:
                original = None
                for override in (None, "", "classic", "no-connect"):
                    args = SimpleNamespace(listener_ports=ports, client_config_path=config_path,
                                           preserve_client_config=True, transport_override=override,
                                           rejected_transports=("", "unknown"))
                    process, _ = fixture.start(args, work, {"listen": []}, {}, dict(ports))
                    process.stop()
                    if original is None:
                        original = config_path.read_bytes()
                    self.assertEqual(config_path.read_bytes(), original)
                    argv = launches[-1]
                    if override is None:
                        self.assertNotIn("--transport", argv)
                    else:
                        self.assertEqual(argv[argv.index("--transport") + 1], override)
                    self.assertEqual(argv[-4:], ["--reject-first", "", "--reject-first", "unknown"])
                pushes = [item.args for item in call.call_args_list if item.args[0] == "push"]
                self.assertEqual(len(pushes), 1)
                self.assertEqual(pushes[0][1], str(config_path))

    def test_frozen_config_drift_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fixture = self.fixture(work)
            path = work / "shared.json"
            original = b'{"listen": [], "transport": "no-connect"}\n'
            path.write_bytes(original)
            args = SimpleNamespace(client_config_path=path, preserve_client_config=True,
                                   listener_ports={"socks": 41000, "http": 41001})
            with mock.patch.object(fixture, "call"), \
                 mock.patch.object(android.suite, "Process") as process:
                with self.assertRaisesRegex(RuntimeError, "shared configuration"):
                    fixture.start(args, work, {"listen": [], "transport": "classic"}, {},
                                  dict(args.listener_ports))
            process.assert_not_called()
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
