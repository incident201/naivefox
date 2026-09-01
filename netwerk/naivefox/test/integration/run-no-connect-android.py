#!/usr/bin/env python3

"""Run dual-transport and embedded-lifecycle gates on an ARM64 runtime."""

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import secrets
import shlex
import struct
import subprocess
import tempfile
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("no_connect_tests", HERE / "run-no-connect-tests.py")
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class AndroidFixture:
    def __init__(self, args, work):
        self.args = args
        self.work = work
        self.adb = [str(args.adb)] + (["-s", args.serial] if args.serial else [])
        self.remote = f"/data/local/tmp/naivefox-no-connect-{secrets.token_hex(8)}"
        self.ports = set()
        self.shared_configs = {}
        self.processes = []
        self.runtime = self.remote + "/runtime"
        self.call("shell", "mkdir", "-p", self.runtime)
        self.call("shell", "chmod", "700", self.remote)
        self.call("push", str(args.package / "lib/arm64-v8a") + "/.", self.runtime + "/")
        compiler = args.ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android26-clang++"
        harness = work / "harness"
        subprocess.run([str(compiler), "-std=c++17", "-O2", "-fPIE", "-pie", "-static-libstdc++",
                        "-pthread", "-I" + str(args.package / "include"),
                        str(HERE / "android_embedded_harness.cpp"), "-ldl", "-o", str(harness)], check=True)
        self.call("push", str(harness), self.remote + "/harness")
        self.call("shell", "chmod", "700", self.remote + "/harness")
        probe = work / "probe"
        subprocess.run([str(compiler), "-std=c++17", "-O2", "-fPIE", "-pie", "-static-libstdc++",
                        str(HERE / "android_transport_probe.cpp"), "-o", str(probe)], check=True)
        self.call("push", str(probe), self.remote + "/probe")
        self.call("shell", "chmod", "700", self.remote + "/probe")

    def probe(self, ports, listener, target_port, operation, length=0, slow=False, host="localhost"):
        ack = (struct.pack("!Q", length) + suite.payload_digest(length)).hex() if operation == "upload" else "-"
        if operation == "auth-partition":
            ack = str(ports["http"])
        command = [self.remote + "/probe", listener, str(ports[listener]), host,
                   str(target_port), operation, str(length), ack]
        if slow:
            command.append("slow")
        log_path = self.work / f"probe-{operation}-{secrets.token_hex(6)}.log"
        with log_path.open("wb") as log:
            result = subprocess.run(self.adb + ["shell", shlex.join(command)],
                                    stdout=log, stderr=log, timeout=90)
        suite.require(result.returncode == 0, f"Android {listener} {operation} failed ({result.returncode}); {log_path}")

    def install_probes(self):
        suite.download = lambda ports, listener, target_port, length=1024*1024, slow=False, host="localhost": self.probe(
            ports, listener, target_port, "download", length, slow, host)
        suite.upload = lambda ports, listener, target_port, length=1024*1024, host="localhost": self.probe(
            ports, listener, target_port, "upload", length, host=host)
        suite.echo_wake = lambda ports, listener, target_port, idle_seconds=2: self.probe(
            ports, listener, target_port, "idle", int(idle_seconds * 1000))
        suite.cancel_stream = lambda ports, target_port: self.probe(ports, "socks", target_port, "reset")
        suite.concurrent_open_streams = lambda ports, target_port, count=40: self.probe(
            ports, "socks", target_port, "concurrent", count)
        suite.auth_partition_streams = lambda ports, target_port: self.probe(
            ports, "socks", target_port, "auth-partition")
        suite.reject_policy = lambda ports, listener, target_port, host="localhost": self.probe(
            ports, listener, target_port, "policy-reject", host=host)
        def reject(ports, listener, target_port, host="localhost", rejected=False):
            suite.require(rejected, "positive Android workloads must run in the native probe")
            self.probe(ports, listener, target_port, "reject", host=host)
        suite.open_tunnel = reject

    def call(self, *args, check=True):
        with (self.work / "adb.log").open("ab") as log:
            return subprocess.run(self.adb + list(args), stdout=log, stderr=log, check=check)

    def capture_command(self, destination, *arguments):
        """Keep bounded, private diagnostic output without masking the failure."""
        try:
            result = subprocess.run(self.adb + list(arguments), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=10, check=False)
            destination.write_bytes(result.stdout)
            destination.chmod(0o600)
            return result
        except (OSError, subprocess.SubprocessError) as error:
            destination.write_text(type(error).__name__ + "\n")
            destination.chmod(0o600)
            return None

    def choose_listener_ports(self, args, directory, ports):
        fixed = getattr(args, "listener_ports", None)
        if fixed is None:
            output = directory / "guest-listener-ports.txt"
            result = self.capture_command(output, "shell", self.remote + "/probe",
                                          "--allocate-listeners")
            suite.require(result is not None and result.returncode == 0,
                          f"Android guest listener allocation failed; {output}")
            match = re.fullmatch(rb"([0-9]+) ([0-9]+)\r?\n", result.stdout)
            suite.require(match is not None, "invalid Android guest listener allocation")
            selected = dict(zip(("socks", "http"), (int(value) for value in match.groups())))
        else:
            # check_shared_config records the first device-selected ports and
            # requires exactly those ports for every later transport selection.
            suite.require(dict(ports) == dict(fixed), "shared Android listener ports drifted")
            selected = dict(fixed)
        suite.require(set(selected) == {"socks", "http"} and
                      all(type(port) is int and 1 <= port <= 65535
                          for port in selected.values()) and
                      selected["socks"] != selected["http"],
                      "invalid Android listener port pair")
        suite.require(not self.ports.intersection(selected.values()),
                      "Android listener is still owned by an active runtime")
        self.ports.update(selected.values())
        ports.clear()
        ports.update(selected)

    def capture_failure(self, directory, remote, phase, exit_code, error=None):
        diagnostics = directory / ("diagnostics-" + phase)
        diagnostics.mkdir(mode=0o700, exist_ok=True)
        details = {"phase": phase, "adb_shell_exit_code": exit_code,
                   "error_type": type(error).__name__ if error else None,
                   "cause": "undetermined; inspect retained result and owned-process diagnostics"}
        pid_result = self.capture_command(diagnostics / "pid.txt", "shell", "cat", remote + "/pid")
        result = self.capture_command(diagnostics / "result.txt", "shell", "cat", remote + "/result")
        details["harness_result_available"] = result is not None and result.returncode == 0
        pid = pid_result.stdout.strip() if pid_result is not None and pid_result.returncode == 0 else b""
        if pid.isdigit() and int(pid) > 0:
            owned_pid = pid.decode("ascii")
            details["owned_pid"] = int(owned_pid)
            self.capture_command(diagnostics / "logcat.txt", "logcat", "-b", "main", "-b", "system",
                                 "-b", "crash", "-d", "--pid=" + owned_pid,
                                 "-v", "threadtime", "-t", "1000")
            self.capture_command(diagnostics / "process-status.txt", "shell", "cat",
                                 "/proc/" + owned_pid + "/status")
        else:
            details["owned_pid"] = None
        suite.private_json(diagnostics / "summary.json", details)

    def start(self, args, directory, config, env, ports):
        remote = self.remote + "/client-" + secrets.token_hex(6)
        self.call("shell", "mkdir", "-p", remote + "/profile")
        self.call("shell", "chmod", "700", remote, remote + "/profile")
        config = dict(config)
        self.choose_listener_ports(args, directory, ports)
        config["listen"] = [f"{listener}://127.0.0.1:{port}" for listener, port in ports.items()]
        config["host-resolver-rules"] = f"MAP localhost {self.args.host_alias}"
        config_path = Path(getattr(args, "client_config_path", directory / "config.json"))
        shared = getattr(args, "client_config_path", None) is not None
        if shared:
            label = str(config_path)
            if label not in self.shared_configs:
                self.shared_configs[label] = self.remote + "/shared-config-" + secrets.token_hex(6) + ".json"
            remote_config = self.shared_configs[label]
        else:
            remote_config = remote + "/config.json"
        preserve_config = getattr(args, "preserve_client_config", False)
        if preserve_config and config_path.exists():
            suite.require(json.loads(config_path.read_bytes()) == config,
                          "embedded transport override changed the shared configuration")
        else:
            suite.private_json(config_path, config)
            self.call("push", str(config_path), remote_config)
        self.call("shell", "chmod", "600", remote_config)
        environment = [f"LD_LIBRARY_PATH={self.runtime}"]
        if env.get("SSL_CERT_FILE"):
            self.call("push", env["SSL_CERT_FILE"], remote + "/ca.crt")
            environment.append(f"SSL_CERT_FILE={remote}/ca.crt")
        argv = ["env", *environment, self.remote + "/harness", self.runtime + "/libxul.so",
                remote_config, remote + "/profile", self.runtime,
                remote + "/stop", remote + "/ready", remote + "/result"]
        transport = getattr(args, "transport_override", None)
        if transport is not None:
            argv.extend(("--transport", transport))
        for rejected in getattr(args, "rejected_transports", ()):
            argv.extend(("--reject-first", rejected))
        command = "cd " + shlex.quote(remote) + " && echo $$ > pid && exec " + shlex.join(argv)
        fixture = self

        class AndroidProcess(suite.Process):
            def __init__(self, *arguments):
                super().__init__(*arguments)
                self.released_ports = False
                self.failure_captures = set()

            def capture_failure(self, phase, error=None):
                if phase in self.failure_captures:
                    return
                self.failure_captures.add(phase)
                try:
                    fixture.capture_failure(directory, remote, phase, self.process.poll(), error)
                except (OSError, RuntimeError, subprocess.SubprocessError) as capture_error:
                    # The original failure is authoritative even if ADB or the
                    # local disk also fails while collecting diagnostics.
                    try:
                        with (directory / "diagnostic-capture-error.log").open("a") as log:
                            log.write(type(capture_error).__name__ + "\n")
                    except OSError:
                        print("Android failure diagnostics could not be written", flush=True)

            def release_ports(self):
                if not self.released_ports:
                    self.released_ports = True
                    for port in ports.values():
                        fixture.ports.discard(port)

            def executed_config(self):
                return json.loads(self.executed_config_bytes())

            def executed_config_bytes(self):
                result = subprocess.run(fixture.adb + ["shell", "cat", remote_config],
                                        capture_output=True, timeout=10, check=True)
                return result.stdout

            def result_values(self):
                result = subprocess.run(fixture.adb + ["shell", "cat", remote + "/result"],
                                        text=True, capture_output=True, timeout=10, check=True)
                return dict(line.split("=", 1) for line in result.stdout.splitlines())

            def stop(self):
                if self.process.poll() not in (None, 0):
                    self.capture_failure("before-stop")
                if self.process.poll() is None:
                    fixture.call("shell", "touch", remote + "/stop")
                    try:
                        self.process.wait(timeout=30)
                    except subprocess.TimeoutExpired as error:
                        self.capture_failure("stop-timeout", error)
                        result = subprocess.run(fixture.adb + ["shell", "cat", remote + "/pid"],
                                                text=True, capture_output=True, check=True)
                        pid = result.stdout.strip()
                        suite.require(pid.isdecimal(), "invalid owned Android harness PID")
                        fixture.call("shell", "kill", pid)
                super().stop()
                if self.process.poll() != 0:
                    self.capture_failure("after-stop")
                self.release_ports()

            def exited_cleanly(self):
                try:
                    status = self.process.wait(timeout=45)
                    suite.require(status == 0, "Android embedded runtime failed")
                    result = subprocess.run(fixture.adb + ["shell", "cat", remote + "/result"],
                                            text=True, capture_output=True, check=True)
                    suite.require("status=0" in result.stdout, "Android embedded result failed")
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    self.capture_failure("runtime-exit", error)
                    raise
                self.release_ports()

        process = AndroidProcess(self.adb + ["shell", command], directory, "client", dict(os.environ))
        self.processes.append(process)

        def ready():
            text = process.log_path.read_text(errors="replace")
            return all(f"listening on 127.0.0.1:{port}" in text for port in ports.values())

        try:
            suite.wait_until(ready, "Android listeners did not start", process, timeout=60)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            process.capture_failure("startup", error)
            raise
        return process, ports

    def close(self):
        for process in reversed(self.processes):
            process.stop()
        suite.require(self.remote.startswith("/data/local/tmp/naivefox-no-connect-"), "unsafe cleanup path")
        self.call("shell", "rm", "-rf", "--", self.remote, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--adb", type=Path, default=Path("/usr/bin/adb"))
    parser.add_argument("--serial")
    parser.add_argument("--host-alias", default="10.0.2.2")
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--transport", choices=("no-connect", "no-connect-hybrid",
                                                  "no-connect-hybrid-asymmetric"),
                        default="no-connect")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--work-dir", type=Path, help="private artifact parent below objdir")
    parser.add_argument("--parallel-batches", type=int, choices=range(1, 129), default=1)
    parser.add_argument("--classic-preamble", choices=("off", "default"), default="default")
    args = parser.parse_args()
    for name in ("objdir", "package", "caddy", "ndk", "adb"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    adb = [str(args.adb)] + (["-s", args.serial] if args.serial else [])
    abi = subprocess.check_output(adb + ["shell", "getprop", "ro.product.cpu.abi"], text=True).strip()
    suite.require(abi == "arm64-v8a", "selected Android device must be ARM64")
    root = (args.work_dir or args.objdir / "naivefox-fixture").resolve()
    suite.require(root.is_relative_to(args.objdir), "work directory must stay below objdir")
    root.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    work = Path(tempfile.mkdtemp(prefix="no-connect-android-", dir=root))
    fixture = None
    try:
        fixture = AndroidFixture(args, work)
        fixture.install_probes()
        inputs = SimpleNamespace(objdir=args.objdir, caddy=args.caddy,
                                 runtime=args.package / "lib/arm64-v8a/libxul.so",
                                 client_factory=fixture.start,
                                 parallel_batches=args.parallel_batches,
                                 classic_preamble=args.classic_preamble,
                                 transport=args.transport)
        protocols = ("h2", "h3") if args.protocol == "both" else (args.protocol,)
        protocol_runner = suite.run_smoke_protocol if args.smoke else suite.run_protocol
        results = [protocol_runner(inputs, work, protocol) for protocol in protocols]
        fixture.close()
        fixture = None
        baseline_env = dict(os.environ, NAIVEFOX_OBJDIR=str(args.package.parents[1]),
                            NAIVEFOX_ADB=str(args.adb), TMPDIR=str(work))
        if args.serial:
            baseline_env["NAIVEFOX_ANDROID_SERIAL"] = args.serial
        if not args.smoke:
            with (work / "embedded-lifecycle.log").open("wb") as log:
                subprocess.run([str(HERE / "run-android-embedded-tests.sh"),
                                "--package", str(args.package), "--direct-host", "--protocol",
                                "all" if args.protocol == "both" else args.protocol],
                               env=baseline_env, stdout=log, stderr=log, timeout=900, check=True)
        suite.private_json(work / "result.json", {"platform": "android-arm64", "status": "PASS", "targets": results})
        print(f"PASS Android ARM64 dual-transport matrix: {work}", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}. Private diagnostics: {work}", flush=True)
        return 1
    finally:
        if fixture:
            fixture.close()
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
