#!/usr/bin/env python3

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import tempfile
import threading
import time


def fixture_module():
    path = Path(__file__).with_name("run-no-connect-tests.py")
    spec = importlib.util.spec_from_file_location("no_connect_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows_job(process):
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
            ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000
    if not handle or not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        if handle:
            kernel.CloseHandle(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    return kernel, handle


def worker(path):
    module = fixture_module()
    job = json.loads(path.read_text())
    if job["action"] == "call":
        allowed = {"download", "upload", "echo_wake", "cancel_stream", "open_tunnel"}
        module.require(job["function"] in allowed, "unknown Windows workload")
        getattr(module, job["function"])(*job["args"], **job["kwargs"])
        return 0
    module.require(job["action"] == "client", "unknown Windows worker action")
    directory = Path(job["directory"])
    ports = {"socks": module.free_port(), "http": module.free_port()}
    while ports["http"] == ports["socks"]:
        ports["http"] = module.free_port()
    config = job["config"]
    config["listen"] = [
        f"socks://127.0.0.1:{ports['socks']}", f"http://127.0.0.1:{ports['http']}"]
    module.private_json(directory / "config.json", config)
    env = {key: value for key, value in os.environ.items() if key not in {
        "NAIVEFOX_PROFILE", "NAIVEFOX_PROXY_USER", "NAIVEFOX_PROXY_PASS",
        "SSL_CERT_FILE", "SSLKEYLOGFILE", "MOZ_LOG", "MOZ_LOG_FILE",
        "LD_PRELOAD", "LD_LIBRARY_PATH"}}
    runtime = Path(job["runtime"])
    profile = directory / "profile"
    profile.mkdir()
    env.update(PATH=str(runtime.parent) + os.pathsep + env.get("PATH", ""),
               NAIVEFOX_PROFILE=str(profile), MOZ_CRASHREPORTER_DISABLE="1")
    if job.get("ca"):
        env["SSL_CERT_FILE"] = job["ca"]
    original_popen = subprocess.Popen

    def hidden_popen(*arguments, **keywords):
        keywords["creationflags"] = (
            keywords.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW)
        return original_popen(*arguments, **keywords)

    subprocess.Popen = hidden_popen
    try:
        process = module.Process(
            [str(runtime), str(directory / "config.json")], directory, "client", env)
    finally:
        subprocess.Popen = original_popen
    kernel = handle = None
    try:
        kernel, handle = windows_job(process.process)

        def ready():
            text = process.log_path.read_text(errors="replace")
            return (f"SOCKS5 listening on 127.0.0.1:{ports['socks']}" in text and
                    f"HTTP CONNECT listening on 127.0.0.1:{ports['http']}" in text)

        module.wait_until(ready, "native Windows listeners did not start",
                          process, timeout=30)
        module.private_json(directory / "ready.json", ports)
        while process.process.poll() is None:
            if (directory / "stop").exists():
                process.stop()
                return 0
            time.sleep(0.05)
        return process.process.returncode
    finally:
        process.stop()
        if handle:
            kernel.CloseHandle(handle)


def relay(host_pid, target_pid, port, protocol):
    kind = socket.SOCK_DGRAM if protocol == "h3" else socket.SOCK_STREAM
    with open(f"/proc/{host_pid}/ns/net") as namespace:
        os.setns(namespace.fileno(), os.CLONE_NEWNET)
    front = socket.socket(socket.AF_INET, kind)
    front.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    front.bind(("127.0.0.1", port))
    if kind == socket.SOCK_STREAM:
        front.listen(64)
    with open(f"/proc/{target_pid}/ns/net") as namespace:
        os.setns(namespace.fileno(), os.CLONE_NEWNET)

    def connection(client):
        try:
            with client, socket.create_connection(("127.0.0.1", port), timeout=10) as back:
                client.settimeout(None)
                back.settimeout(None)
                with selectors.DefaultSelector() as poll:
                    poll.register(client, selectors.EVENT_READ, back)
                    poll.register(back, selectors.EVENT_READ, client)
                    while poll.get_map():
                        for event, _ in poll.select(60):
                            data = event.fileobj.recv(65536)
                            if data:
                                event.data.sendall(data)
                            else:
                                poll.unregister(event.fileobj)
                                event.data.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    print("ready", flush=True)
    if kind == socket.SOCK_STREAM:
        while True:
            client, _ = front.accept()
            threading.Thread(target=connection, args=(client,), daemon=True).start()
    else:
        peers = {}
        with selectors.DefaultSelector() as poll:
            poll.register(front, selectors.EVENT_READ)
            while True:
                for event, _ in poll.select():
                    if event.fileobj is front:
                        data, peer = front.recvfrom(65536)
                        if peer not in peers:
                            if len(peers) >= 64:
                                raise RuntimeError("Windows fixture UDP peer bound exceeded")
                            back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            back.connect(("127.0.0.1", port))
                            peers[peer] = back
                            poll.register(back, selectors.EVENT_READ, peer)
                        peers[peer].send(data)
                    else:
                        front.sendto(event.fileobj.recv(65536), event.data)


def winpath(path):
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


class NativeClient:
    def __init__(self, module, process, directory):
        self.module = module
        self.process = process
        self.directory = directory

    def exited_cleanly(self):
        try:
            result = self.process.wait(timeout=45)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("native Windows client did not drain") from error
        self.module.require(result == 0, "native Windows client did not exit cleanly")

    def stop(self):
        if self.process.poll() is None:
            (self.directory / "stop").touch()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def run_inside(args):
    module = fixture_module()
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    root = args.objdir / "naivefox-fixture"
    root.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="no-connect-windows-", dir=root))
    worker_script = winpath(Path(__file__).resolve())
    logs = []
    relays = []
    sequence_lock = threading.Lock()
    sequence = 0

    def launch(job, directory, label):
        specification = directory / (label + ".json")
        module.private_json(specification, job)
        log = (directory / (label + ".log")).open("wb")
        logs.append(log)
        return subprocess.Popen(
            [str(args.windows_python), worker_script, "--worker", winpath(specification)],
            stdout=log, stderr=subprocess.STDOUT)

    def client_factory(unused, directory, config, env, ports):
        job = {"action": "client", "directory": winpath(directory),
               "runtime": winpath(args.runtime), "config": config,
               "ca": winpath(env["SSL_CERT_FILE"]) if env.get("SSL_CERT_FILE") else None}
        process = launch(job, directory, "worker")
        client = NativeClient(module, process, directory)
        try:
            module.wait_until(lambda: (directory / "ready.json").exists(),
                              "native Windows helper did not become ready", client, timeout=40)
            return client, json.loads((directory / "ready.json").read_text())
        except BaseException:
            client.stop()
            raise

    def call(name, *positional, **keywords):
        nonlocal sequence
        with sequence_lock:
            sequence += 1
            label = f"workload-{sequence}"
        process = launch({"action": "call", "function": name,
                          "args": positional, "kwargs": keywords}, run, label)
        try:
            result = process.wait(timeout=90)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError(f"native Windows {name} timed out") from error
        module.require(result == 0, f"native Windows {name} failed; inspect {label}.log")

    original_start = module.start_caddy

    def start_caddy(*arguments):
        process, port = original_start(*arguments)
        protocol = arguments[2]
        bridge = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--relay",
             str(args.host_pid), str(os.getpid()), str(port), protocol],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if bridge.stdout.readline().strip() != "ready":
            process.stop()
            bridge.wait(timeout=5)
            raise RuntimeError("Windows loopback relay failed to start")
        relays.append(bridge)
        return process, port

    args.client_factory = client_factory
    module.start_caddy = start_caddy
    for name in ("download", "upload", "echo_wake", "cancel_stream", "open_tunnel"):
        setattr(module, name, lambda *a, _name=name, **kw: call(_name, *a, **kw))
    try:
        modules = subprocess.check_output([str(args.caddy), "list-modules"], text=True)
        for expected in ("http.handlers.forward_proxy", "http.handlers.naivefox_transport"):
            module.require(expected in modules.splitlines(), "combined Caddy module is missing")
        protocols = ("h2", "h3") if args.protocol == "both" else (args.protocol,)
        results = [module.run_protocol(args, run, protocol) for protocol in protocols]
        module.private_json(run / "result.json",
                            {"status": "PASS", "platform": "windows-x86_64",
                             "native_process": True, "targets": results})
        print(f"Native Windows acceptance PASS. Private fixture: {run}", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}. Private diagnostics: {run}", flush=True)
        return 1
    finally:
        for bridge in relays:
            if bridge.poll() is None:
                bridge.terminate()
                bridge.wait(timeout=10)
        for log in logs:
            log.close()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(Path(sys.argv[2]))
    if len(sys.argv) == 6 and sys.argv[1] == "--relay":
        return relay(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    parser = argparse.ArgumentParser(
        description="Run the shared dual-transport fixture against native Windows NaiveFox from WSL.")
    parser.add_argument("--objdir", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--caddy", required=True, type=Path)
    parser.add_argument("--windows-python", required=True, type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--host-pid", type=int)
    args = parser.parse_args()
    for name in ("objdir", "runtime", "caddy", "windows_python"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    os.umask(0o077)
    if args.host_pid is None:
        command = ["unshare", "--net", sys.executable, str(Path(__file__).resolve()),
                   *sys.argv[1:], "--host-pid", str(os.getpid())]
        return subprocess.run(command).returncode
    return run_inside(args)


if __name__ == "__main__":
    raise SystemExit(main())
