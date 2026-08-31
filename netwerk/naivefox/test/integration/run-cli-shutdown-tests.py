#!/usr/bin/env python3
"""Exercise POSIX CLI shutdown with unfinished requests on both local frontends."""

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time


def require(value, message):
    if not value:
        raise RuntimeError(message)


def reserve_ports():
    reservations = [socket.socket() for _ in range(2)]
    try:
        for reservation in reservations:
            reservation.bind(("127.0.0.1", 0))
        return [reservation.getsockname()[1] for reservation in reservations]
    finally:
        for reservation in reservations:
            reservation.close()


def connect_when_ready(port, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        require(process.poll() is None, "CLI exited before listener readiness")
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            connection.settimeout(5)
            return connection
        except (TimeoutError, ConnectionRefusedError):
            time.sleep(0.02)
    raise RuntimeError("CLI listener did not become ready")


def run_case(runtime, root, transport, stop_signal):
    directory = root / (transport + "-" + signal.Signals(stop_signal).name.lower())
    directory.mkdir(mode=0o700)
    ports = reserve_ports()
    config = {"listen": [f"socks://127.0.0.1:{ports[0]}", f"http://127.0.0.1:{ports[1]}"],
              "proxy": f"https://fixture:{secrets.token_hex(16)}@127.0.0.1:9",
              "transport": transport, "max-connections": 0, "log": ""}
    config_path = directory / "config.json"
    config_path.write_text(json.dumps(config) + "\n")
    env = {key: value for key, value in os.environ.items() if key not in
           ("NAIVEFOX_PROFILE", "SSLKEYLOGFILE", "SSL_CERT_FILE", "MOZ_LOG", "MOZ_LOG_FILE", "LD_PRELOAD", "MOZ_RUN_GTEST")}
    env.update(TMPDIR=str(directory), LD_LIBRARY_PATH=str(runtime.parent), MOZ_CRASHREPORTER_DISABLE="1")
    result = {"transport": transport, "signal": signal.Signals(stop_signal).name, "passed": False}
    connections = []
    process = None
    try:
        with (directory / "native.log").open("wb") as log:
            process = subprocess.Popen([str(runtime), str(config_path)], cwd=directory, env=env, stdout=log, stderr=log)
            connections = [connect_when_ready(port, process) for port in ports]
            connections[0].sendall(b"\x05")
            connections[1].sendall(b"CONNECT ")
            time.sleep(0.05)
            started = time.monotonic()
            process.send_signal(stop_signal)
            process.wait(timeout=15)
            result.update(returncode=process.returncode, stop_ms=1000 * (time.monotonic() - started))
            require(process.returncode == 0, "CLI signal did not complete through its normal exit path")
            for connection in connections:
                received = 0
                while True:
                    try:
                        data = connection.recv(1024)
                    except ConnectionResetError:
                        break
                    if not data:
                        break
                    received += len(data)
                    require(received <= 4096, "unexpected data after an incomplete local request")
            for port in ports:
                with socket.socket() as probe:
                    probe.settimeout(1)
                    require(probe.connect_ex(("127.0.0.1", port)) != 0, "CLI listener survived shutdown")
            result.update(passed=True, unfinished_frontends_closed=2, listeners_remaining=0, forced_kills=0)
    except Exception as error:
        result["failure"] = str(error)
        raise
    finally:
        for connection in connections:
            connection.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
            result["forced_kills"] = 1
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    require(os.name == "posix", "this is the POSIX CLI signal regression")
    objdir = args.objdir.resolve(strict=True)
    runtime = (args.runtime or objdir / "dist/bin/naivefox").resolve(strict=True)
    root = args.work_dir.resolve()
    require(root.is_relative_to(objdir / "hybrid-ws") and not root.exists(), "new fixture root must be beneath the common objdir hybrid-ws subtree")
    os.umask(0o077)
    root.mkdir(parents=True, mode=0o700)
    results = []
    for transport in ("classic", "no-connect", "no-connect-hybrid"):
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            results.append(run_case(runtime, root, transport, stop_signal))
    summary = {"passed": True, "cases": len(results), "unfinished_frontends_closed": 12, "results": results}
    (root / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"passed": True, "cases": len(results), "unfinished_frontends_closed": 12, "result": str(root / "result.json")}))


if __name__ == "__main__":
    main()
