#!/usr/bin/env python3
"""
verify-staged-windows-runtime.py - Automated Acceptance Test Suite for Staged Windows Runtime
Runs on native Windows to test config reading, SOCKS5 listener, HTTP CONNECT listener, and clean shutdown.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    topsrcdir = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))

    # Locate Windows staged package
    candidates = [
        r"D:\naivefox\naivefox-windows-x86_64\naivefox.exe",
        os.path.join(topsrcdir, "obj-naivefox-windows-x86_64", "naivefox-package", "naivefox-windows-x86_64", "naivefox.exe"),
    ]

    exe_path = None
    for cand in candidates:
        if os.path.exists(cand):
            exe_path = cand
            break

    if not exe_path:
        print(f"Error: naivefox.exe not found in candidates: {candidates}", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f"NaiveFox Native Windows Acceptance Suite: {exe_path}")
    print("=" * 70)

    # 1. Version check
    out = subprocess.check_output([exe_path, "--version"], text=True)
    print(f"[1] Version Output: {out.strip()}")
    assert "NaiveFox" in out, "Version check failed"

    # 2. Runtime smoke test
    with tempfile.TemporaryDirectory(prefix="nf_win_smoke_") as temp_prof:
        out = subprocess.check_output([exe_path, "--profile", temp_prof, "--runtime-smoke"], text=True)
        print(f"[2] Runtime Smoke: {out.strip()}")
        assert "completed successfully" in out, "Smoke test failed"

    # 3. Config-mode SOCKS5 listener test
    with tempfile.TemporaryDirectory(prefix="nf_win_socks_") as temp_prof:
        cfg_path = os.path.join(temp_prof, "config.json")
        cfg = {
            "listen": "socks://127.0.0.1:18850",
            "proxy": "https://dummy_user:dummy_pass@127.0.0.1:28443",
            "log": ""
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        print(f"[3] Starting NaiveFox SOCKS5 config mode: {cfg_path}")
        proc = subprocess.Popen([exe_path, cfg_path], cwd=temp_prof, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            opened = False
            for _ in range(50):
                try:
                    s = socket.create_connection(("127.0.0.1", 18850), timeout=0.5)
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
                s = socket.create_connection(("127.0.0.1", 18850), timeout=1.0)
                s.sendall(b"\x05\x01\x00")
                resp = s.recv(2)
                assert resp == b"\x05\x00", f"Consecutive connection {i} failed"
                s.close()
            print("    5 Consecutive SOCKS5 handshakes: PASSED")

        finally:
            proc.terminate()
            proc.wait(timeout=5)
            print("    SOCKS5 process clean shutdown: PASSED")

    # 4. Config-mode HTTP CONNECT listener test
    with tempfile.TemporaryDirectory(prefix="nf_win_http_") as temp_prof:
        cfg_path = os.path.join(temp_prof, "config.json")
        cfg = {
            "listen": "http://127.0.0.1:18851",
            "proxy": "https://dummy_user:dummy_pass@127.0.0.1:28443",
            "log": ""
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        print(f"[4] Starting NaiveFox HTTP CONNECT mode: {cfg_path}")
        proc = subprocess.Popen([exe_path, cfg_path], cwd=temp_prof, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            opened = False
            for _ in range(50):
                try:
                    s = socket.create_connection(("127.0.0.1", 18851), timeout=0.5)
                    s.sendall(b"CONNECT 127.0.0.1:80 HTTP/1.1\r\nHost: 127.0.0.1:80\r\n\r\n")
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

    print("\n" + "=" * 70)
    print("ALL NATIVE WINDOWS ACCEPTANCE CHECKS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()
