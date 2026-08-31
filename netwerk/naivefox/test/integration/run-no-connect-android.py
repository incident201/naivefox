#!/usr/bin/env python3

"""Run dual-transport and embedded-lifecycle gates on an ARM64 runtime."""

import argparse
import importlib.util
import json
import os
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
        suite.echo_wake = lambda ports, listener, target_port: self.probe(
            ports, listener, target_port, "idle")
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

    def start(self, args, directory, config, env, ports):
        remote = self.remote + "/client-" + secrets.token_hex(6)
        self.call("shell", "mkdir", "-p", remote + "/profile")
        self.call("shell", "chmod", "700", remote, remote + "/profile")
        config = dict(config)
        fixed_ports = getattr(args, "listener_ports", None)
        for listener in ports:
            if fixed_ports is not None:
                suite.require(ports[listener] not in self.ports,
                              "shared Android listener is still owned by an active runtime")
            else:
                while ports[listener] in self.ports:
                    ports[listener] = suite.free_port()
            self.ports.add(ports[listener])
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
        command = "cd " + shlex.quote(remote) + " && echo $$ > pid && exec " + shlex.join(argv)
        fixture = self

        class AndroidProcess(suite.Process):
            released_ports = False

            def release_ports(self):
                if not self.released_ports:
                    self.released_ports = True
                    for port in ports.values():
                        fixture.ports.discard(port)

            def executed_config(self):
                result = subprocess.run(fixture.adb + ["shell", "cat", remote_config],
                                        text=True, capture_output=True, check=True)
                return json.loads(result.stdout)

            def stop(self):
                if self.process.poll() is None:
                    fixture.call("shell", "touch", remote + "/stop")
                    try:
                        self.process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        result = subprocess.run(fixture.adb + ["shell", "cat", remote + "/pid"],
                                                text=True, capture_output=True, check=True)
                        pid = result.stdout.strip()
                        suite.require(pid.isdecimal(), "invalid owned Android harness PID")
                        fixture.call("shell", "kill", pid)
                super().stop()
                self.release_ports()

            def exited_cleanly(self):
                try:
                    status = self.process.wait(timeout=45)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("Android embedded runtime did not drain") from error
                suite.require(status == 0, "Android embedded runtime failed")
                result = subprocess.run(fixture.adb + ["shell", "cat", remote + "/result"],
                                        text=True, capture_output=True, check=True)
                suite.require("status=0" in result.stdout, "Android embedded result failed")
                self.release_ports()

        process = AndroidProcess(self.adb + ["shell", command], directory, "client", dict(os.environ))
        self.processes.append(process)

        def ready():
            text = process.log_path.read_text(errors="replace")
            return all(f"listening on 127.0.0.1:{port}" in text for port in ports.values())

        suite.wait_until(ready, "Android listeners did not start", process, timeout=60)
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
    parser.add_argument("--parallel-batches", type=int, choices=range(1, 129), default=1)
    parser.add_argument("--classic-preamble", choices=("off", "default"), default="default")
    args = parser.parse_args()
    for name in ("objdir", "package", "caddy", "ndk", "adb"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    adb = [str(args.adb)] + (["-s", args.serial] if args.serial else [])
    abi = subprocess.check_output(adb + ["shell", "getprop", "ro.product.cpu.abi"], text=True).strip()
    suite.require(abi == "arm64-v8a", "selected Android device must be ARM64")
    root = args.objdir / "naivefox-fixture"
    root.mkdir(exist_ok=True)
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
                                 classic_preamble=args.classic_preamble)
        protocols = ("h2", "h3") if args.protocol == "both" else (args.protocol,)
        results = [suite.run_protocol(inputs, work, protocol) for protocol in protocols]
        fixture.close()
        fixture = None
        baseline_env = dict(os.environ, NAIVEFOX_OBJDIR=str(args.package.parents[1]),
                            NAIVEFOX_ADB=str(args.adb), TMPDIR=str(work))
        if args.serial:
            baseline_env["NAIVEFOX_ANDROID_SERIAL"] = args.serial
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
