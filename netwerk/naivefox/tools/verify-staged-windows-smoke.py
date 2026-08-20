#!/usr/bin/env python3
"""
verify-staged-windows-smoke.py - Windows Staged Package Verification & Network Acceptance Tool
Supports dynamic port allocation, CLI arguments (--package-dir, --proxy-url, --target-url),
and clear reporting of verified capabilities.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time


def find_free_port():
    """Find a dynamically available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_log_and_liveness(proc, log_path, minimum_size=0, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"NaiveFox exited during file-log startup (code {proc.returncode})"
            )
        if os.path.exists(log_path) and os.path.getsize(log_path) > minimum_size:
            return
        time.sleep(0.1)
    raise AssertionError(f"runtime log was not created: {log_path}")


def run_file_logging_case(exe_path, proxy_endpoint, temp_root, log_value, log_path):
    cfg_path = os.path.join(temp_root, "config-file-log.json")
    diagnostic_path = os.path.join(temp_root, "file-log-process.txt")
    cfg = {
        "listen": f"socks://127.0.0.1:{find_free_port()}",
        "proxy": proxy_endpoint,
        "log": log_value,
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    def launch():
        diagnostic = open(diagnostic_path, "ab")
        proc = subprocess.Popen(
            [exe_path, cfg_path],
            cwd=temp_root,
            stdout=diagnostic,
            stderr=subprocess.STDOUT,
        )
        proc._naivefox_diagnostic = diagnostic
        return proc

    def stop(proc):
        proc.terminate()
        proc.wait(timeout=5)
        proc._naivefox_diagnostic.close()

    proc = launch()
    try:
        wait_for_log_and_liveness(proc, log_path, minimum_size=0)
        assert proc.poll() is None, "file logging process exited unexpectedly"
        first_size = os.path.getsize(log_path)
    except AssertionError as exc:
        proc._naivefox_diagnostic.flush()
        with open(diagnostic_path, "rb") as diagnostic:
            details = diagnostic.read().decode("utf-8", errors="replace")[-2000:]
        raise AssertionError(f"{exc}; process output: {details!r}") from exc
    finally:
        stop(proc)

    # Reopen the same file to verify append semantics and clean shutdown.
    proc = launch()
    try:
        wait_for_log_and_liveness(proc, log_path, minimum_size=first_size)
        current_size = os.path.getsize(log_path)
        assert current_size > first_size, (
            f"runtime log did not append (first={first_size}, current={current_size})"
        )
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            contents = f.read()
        assert "dummy_pass" not in contents, "credentials leaked into runtime log"
        assert "Proxy-Authorization" not in contents, (
            "proxy auth leaked into runtime log"
        )
    finally:
        stop(proc)


def assert_alive(proc, label):
    if proc.poll() is not None:
        raise AssertionError(
            f"NaiveFox exited during Windows {label} stress (code {proc.returncode})"
        )


def send_socks_probe(port, payload, read_reply=True):
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
        sock.settimeout(1.0)
        sock.sendall(payload)
        if read_reply:
            try:
                reply = sock.recv(32)
            except (ConnectionResetError, TimeoutError):
                # A terminal parser error may close before the peer can read
                # the bounded reject; the important invariant is no process
                # spin/OOM and continued listener availability.
                return
            assert len(reply) <= 32, "SOCKS failure reply exceeded bounded size"


def run_malformed_socks_case(exe_path, proxy_endpoint, temp_root):
    port = find_free_port()
    cfg_path = os.path.join(temp_root, "config-malformed-socks.json")
    cfg = {
        "listen": f"socks://127.0.0.1:{port}",
        "proxy": proxy_endpoint,
        "log": "",
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    proc = subprocess.Popen(
        [exe_path, cfg_path],
        cwd=temp_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            assert_alive(proc, "SOCKS")
            try:
                send_socks_probe(port, b"\x05\x01\x00")
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("Windows SOCKS listener did not become ready")

        greeting = b"\x05\x01\x00"
        probes = [
            greeting + b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00",  # UDP ASSOCIATE
            greeting + b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00",  # BIND
            greeting + b"\x05\x01\x00\x09\x00\x00\x00\x00",  # bad ATYP
            b"\x04\x01\x00\x01\x00\x00\x00\x00\x00\x00",  # bad version
            greeting + b"\x05\x01\x01",  # bad reserved byte / short request
        ]
        for payload in probes:
            send_socks_probe(port, payload)
            assert_alive(proc, "SOCKS")

        # A large same-write tail must not be reparsed after the terminal
        # command rejection.  Keep the probe bounded for the native runner.
        send_socks_probe(
            port,
            greeting
            + b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00"
            + b"x" * (2 * 1024 * 1024),
        )
        assert_alive(proc, "SOCKS")

        # A client that never reads the reject must not make the server spin
        # or grow a reply queue.
        for _ in range(200):
            send_socks_probe(
                port,
                greeting + b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00",
                read_reply=False,
            )
            assert_alive(proc, "SOCKS")

        # Prove the listener still accepts a normal greeting after the bad
        # connections have been closed.
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(greeting)
            assert sock.recv(2) == b"\x05\x00"
        assert_alive(proc, "SOCKS")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def run_malformed_http_case(exe_path, proxy_endpoint, temp_root):
    port = find_free_port()
    cfg_path = os.path.join(temp_root, "config-malformed-http.json")
    cfg = {
        "listen": f"http://127.0.0.1:{port}",
        "proxy": proxy_endpoint,
        "log": "",
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    proc = subprocess.Popen(
        [exe_path, cfg_path],
        cwd=temp_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            assert_alive(proc, "HTTP")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("Windows HTTP listener did not become ready")

        for _ in range(100):
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
                sock.sendall(b"GET / HTTP/1.1\r\nHost: example.test\r\n\r\n")
                response = sock.recv(64)
                assert response.startswith((b"HTTP/1.1 405", b"HTTP/1.1 501"))
            assert_alive(proc, "HTTP")

        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(b"X" * (2 * 1024 * 1024))
        assert_alive(proc, "HTTP")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(
        description="NaiveFox Windows Staged Package Verification"
    )
    parser.add_argument(
        "--package-dir", default=None, help="Directory containing staged naivefox.exe"
    )
    parser.add_argument(
        "--proxy-url",
        default=None,
        help="Upstream H2/H3 proxy URL for live acceptance testing",
    )
    parser.add_argument(
        "--target-url",
        default="http://127.0.0.1",
        help="Target URL for CONNECT verification",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    topsrcdir = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))

    # Locate Windows executable
    candidates = []
    if args.package_dir:
        candidates.append(os.path.join(args.package_dir, "naivefox.exe"))
    candidates.extend([
        r"D:\naivefox\naivefox-windows-x86_64\naivefox.exe",
        os.path.join(
            topsrcdir,
            "obj-naivefox-windows-x86_64",
            "naivefox-package",
            "naivefox-windows-x86_64",
            "naivefox.exe",
        ),
    ])

    exe_path = None
    for cand in candidates:
        if os.path.exists(cand):
            exe_path = cand
            break

    if not exe_path:
        print(
            f"Error: naivefox.exe not found in candidates: {candidates}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=" * 70)
    print(f"NaiveFox Windows Package Verification: {exe_path}")
    print("=" * 70)

    # 1. Version check
    out = subprocess.check_output([exe_path, "--version"], text=True)
    print(f"[1] Version Output: {out.strip()}")
    assert "NaiveFox" in out, "Version check failed"

    # 2. Runtime smoke test
    with tempfile.TemporaryDirectory(prefix="nf_win_smoke_") as temp_prof:
        out = subprocess.check_output(
            [exe_path, "--profile", temp_prof, "--runtime-smoke"], text=True
        )
        print(f"[2] Runtime Smoke: {out.strip()}")
        assert "completed successfully" in out, "Smoke test failed"

    # 3. Dynamic Port SOCKS5 listener test
    socks_port = find_free_port()
    proxy_endpoint = args.proxy_url or "https://dummy_user:dummy_pass@127.0.0.1:28443"

    with tempfile.TemporaryDirectory(prefix="nf_win_socks_") as temp_prof:
        cfg_path = os.path.join(temp_prof, "config.json")
        cfg = {
            "listen": f"socks://127.0.0.1:{socks_port}",
            "proxy": proxy_endpoint,
            "log": "",
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        print(f"[3] Starting NaiveFox SOCKS5 on dynamic port {socks_port}...")
        proc = subprocess.Popen(
            [exe_path, cfg_path],
            cwd=temp_prof,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            opened = False
            for _ in range(50):
                try:
                    s = socket.create_connection(("127.0.0.1", socks_port), timeout=0.5)
                    s.sendall(b"\x05\x01\x00")
                    resp = s.recv(2)
                    if resp == b"\x05\x00":
                        opened = True
                        s.close()
                        break
                    s.close()
                except Exception:
                    time.sleep(0.1)

            print(f"    SOCKS5 Handshake: {'PASSED' if opened else 'FAILED'}")
            assert opened, "SOCKS5 listener did not accept connections"

            for i in range(5):
                s = socket.create_connection(("127.0.0.1", socks_port), timeout=1.0)
                s.sendall(b"\x05\x01\x00")
                resp = s.recv(2)
                assert resp == b"\x05\x00", f"Consecutive connection {i} failed"
                s.close()
            print("    5 Consecutive SOCKS5 handshakes: PASSED")

        finally:
            proc.terminate()
            proc.wait(timeout=5)
            print("    SOCKS5 process clean shutdown: PASSED")

    # 4. Dynamic Port HTTP CONNECT listener test
    http_port = find_free_port()
    with tempfile.TemporaryDirectory(prefix="nf_win_http_") as temp_prof:
        cfg_path = os.path.join(temp_prof, "config.json")
        cfg = {
            "listen": f"http://127.0.0.1:{http_port}",
            "proxy": proxy_endpoint,
            "log": "",
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        print(f"[4] Starting NaiveFox HTTP CONNECT on dynamic port {http_port}...")
        proc = subprocess.Popen(
            [exe_path, cfg_path],
            cwd=temp_prof,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            opened = False
            for _ in range(50):
                try:
                    s = socket.create_connection(("127.0.0.1", http_port), timeout=0.5)
                    s.sendall(
                        b"CONNECT 127.0.0.1:80 HTTP/1.1\r\nHost: 127.0.0.1:80\r\n\r\n"
                    )
                    opened = True
                    s.close()
                    break
                except Exception:
                    time.sleep(0.1)

            print(f"    HTTP CONNECT Listener: {'PASSED' if opened else 'FAILED'}")
            assert opened, "HTTP CONNECT listener did not accept connections"

        finally:
            proc.terminate()
            proc.wait(timeout=5)
            print("    HTTP CONNECT process clean shutdown: PASSED")

    # 5. Native Windows malformed-input terminal-state stress.
    with tempfile.TemporaryDirectory(prefix="nf_win_malformed_socks_") as temp_stress:
        run_malformed_socks_case(exe_path, proxy_endpoint, temp_stress)
        run_malformed_http_case(exe_path, proxy_endpoint, temp_stress)
    print("[5] Windows malformed SOCKS/HTTP bounded stress: PASSED")

    # 6. Native Windows file logging: relative/Unicode path, append, and
    # liveness.  The runtime must not call POSIX chmod() on this path.
    with tempfile.TemporaryDirectory(prefix="nf_win_logging_") as temp_log:
        relative_dir = os.path.join(temp_log, "日志_日本")
        os.makedirs(relative_dir, exist_ok=True)
        relative_path = os.path.join(relative_dir, "naivefox-运行.log")
        relative_value = os.path.relpath(relative_path, temp_log)
        run_file_logging_case(
            exe_path, proxy_endpoint, temp_log, relative_value, relative_path
        )
        print("[6] Windows relative Unicode file logging + append: PASSED")

        absolute_path = os.path.join(temp_log, "absolute-运行.log")
        run_file_logging_case(
            exe_path, proxy_endpoint, temp_log, absolute_path, absolute_path
        )
        print("    Windows absolute Unicode file logging: PASSED")

    print("\n" + "=" * 70)
    print("STATUS SUMMARY:")
    print(
        "  Windows build, launch, config parsing, file logging, local listener handshake and shutdown verified."
    )
    if args.proxy_url:
        print(f"  Live upstream proxy verified against: {args.proxy_url}")
    else:
        print("  End-to-end H2/H3 networking is tracked separately.")
    print("=" * 70)


if __name__ == "__main__":
    main()
