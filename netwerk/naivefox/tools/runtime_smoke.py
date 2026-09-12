#!/usr/bin/env python3
"""Initialize and drain the current runtime through its local frontend."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_runtime_smoke(exe_path, directory):
    port = find_free_port()
    config_path = os.path.join(directory, "runtime.json")
    with open(config_path, "w", encoding="utf-8") as output:
        json.dump(
            {
                "listen": f"socks://127.0.0.1:{port}",
                "proxy": "https://fixture:fixture@127.0.0.1:9",
                "max-connections": 1,
                "log": "",
            },
            output,
        )
    env = dict(os.environ, NAIVEFOX_PROFILE=directory)
    log_path = os.path.join(directory, "runtime.log")
    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            [exe_path, config_path], stdout=log, stderr=subprocess.STDOUT, env=env
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, "runtime exited before opening listener"
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "runtime listener did not start"
                    time.sleep(0.05)
            assert process.wait(timeout=20) == 0, "bounded runtime did not exit cleanly"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    with open(log_path, encoding="utf-8", errors="replace") as log:
        return log.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    run_runtime_smoke(
        str(args.runtime.resolve(strict=True)), str(args.work_dir.resolve())
    )
    print("PASS: runtime initialization and bounded frontend shutdown")


if __name__ == "__main__":
    main()
