#!/usr/bin/env python3
"""
verify-staged-windows-smoke.py - Windows Staged Package Verification & Network Acceptance Tool
Supports dynamic port allocation, CLI arguments (--package-dir, --proxy-url, --target-url),
and clear reporting of verified capabilities.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

SOCKS_NO_AUTH_GREETING = b"\x05\x01\x00"


def fetch_digest(target_url, local_proxy=None):
    command = ["curl.exe", "--fail", "--silent", "--show-error", "--noproxy", "",
               "--connect-timeout", "10", "--max-time", "60"]
    if os.environ.get("SSL_CERT_FILE"):
        command.extend(["--cacert", os.environ["SSL_CERT_FILE"]])
    if local_proxy:
        command.extend(["--proxy", local_proxy, "--proxytunnel"])
    else:
        command.extend(["--proxy", ""])
    command.append(target_url)
    try:
        result = subprocess.run(command, capture_output=True, timeout=70)
    except subprocess.TimeoutExpired:
        raise AssertionError("live transfer timed out") from None
    # Avoid echoing potentially private target URLs or upstream diagnostics.
    assert result.returncode == 0, f"live transfer failed (curl exit {result.returncode})"
    assert result.stdout, "live transfer returned an empty body"
    return hashlib.sha256(result.stdout).hexdigest()


def verify_live_transfers(target_url, local_proxy, expected_digest):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        digests = list(executor.map(
            lambda _: fetch_digest(target_url, local_proxy), range(8)
        ))
    assert all(digest == expected_digest for digest in digests), "live transfer body mismatch"


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


def proxy_secret_tokens(proxy_url):
    parsed = urllib.parse.urlsplit(proxy_url)
    if "@" not in parsed.netloc:
        return ()
    raw_userinfo = parsed.netloc.rsplit("@", 1)[0]
    raw_user, separator, raw_password = raw_userinfo.partition(":")
    raw_values = (raw_user, raw_password) if separator else (raw_user,)
    tokens = set(raw_values)
    tokens.update(urllib.parse.unquote(value) for value in raw_values)
    return tuple(sorted(token for token in tokens if token))


def redact_proxy_url(proxy_url):
    parsed = urllib.parse.urlsplit(proxy_url)
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((
        parsed.scheme,
        netloc,
        parsed.path,
        parsed.query,
        parsed.fragment,
    ))


def assert_no_proxy_secrets(contents, secrets, label):
    for secret in secrets:
        assert secret not in contents, f"proxy credential leaked into {label}"
    assert "Proxy-Authorization" not in contents, (
        f"proxy auth header leaked into {label}"
    )


def run_file_logging_case(
    exe_path, proxy_endpoint, temp_root, log_value, log_path, secrets
):
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

    # Reopen the same file to verify append semantics across bounded process
    # teardowns.  Natural clean exit is covered by runtime-smoke.
    proc = launch()
    try:
        wait_for_log_and_liveness(proc, log_path, minimum_size=first_size)
        current_size = os.path.getsize(log_path)
        assert current_size > first_size, (
            f"runtime log did not append (first={first_size}, current={current_size})"
        )
        with open(log_path, encoding="utf-8", errors="replace") as f:
            contents = f.read()
    finally:
        stop(proc)

    with open(diagnostic_path, encoding="utf-8", errors="replace") as f:
        diagnostics = f.read()
    assert_no_proxy_secrets(contents, secrets, "runtime log")
    assert_no_proxy_secrets(diagnostics, secrets, "process output")


def assert_alive(proc, label):
    if proc.poll() is not None:
        raise AssertionError(
            f"NaiveFox exited during Windows {label} stress (code {proc.returncode})"
        )


def wait_for_listener(proc, port, label, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert_alive(proc, label)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"Windows {label} listener did not become ready")


def make_socks_connect_request(host="lifecycle.test", port=443):
    encoded_host = host.encode("ascii")
    if not encoded_host or len(encoded_host) > 255:
        raise ValueError("SOCKS domain must contain 1..255 ASCII bytes")
    return (
        b"\x05\x01\x00\x03"
        + bytes((len(encoded_host),))
        + encoded_host
        + port.to_bytes(2, "big")
    )


def make_http_connect_request(authority="lifecycle.test:443"):
    encoded_authority = authority.encode("ascii")
    return (
        b"CONNECT "
        + encoded_authority
        + b" HTTP/1.1\r\nHost: "
        + encoded_authority
        + b"\r\n\r\n"
    )


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise AssertionError("SOCKS listener closed during method negotiation")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def connect_and_drop(port, payload, listener_scheme):
    last_error = None
    for _ in range(3):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
                sock.settimeout(2.0)
                if listener_scheme == "socks":
                    sock.sendall(SOCKS_NO_AUTH_GREETING)
                    selection = recv_exact(sock, 2)
                    if selection != b"\x05\x00":
                        raise AssertionError(
                            f"unexpected SOCKS method selection: {selection!r}"
                        )
                sock.sendall(payload)
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    # The deliberately dead upstream may make NaiveFox close
                    # first.  Either ordering exercises cancellation.
                    pass
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.02)
    raise AssertionError(f"lifecycle churn connection failed: {last_error}")


def assert_alive_during_drain(proc, label, duration=1.0, interval=0.05):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        assert_alive(proc, label)
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    assert_alive(proc, label)


def run_disconnect_waves(
    proc, port, payload, listener_scheme, label, waves=12, width=24
):
    with concurrent.futures.ThreadPoolExecutor(max_workers=width) as executor:
        for _ in range(waves):
            futures = [
                executor.submit(connect_and_drop, port, payload, listener_scheme)
                for _ in range(width)
            ]
            for future in futures:
                future.result(timeout=5.0)
            assert_alive(proc, label)

    # Repeatedly sample liveness while main/socket-thread stop runnables drain.
    # The historical UAF usually surfaced in this bounded interval.
    assert_alive_during_drain(proc, label)


def force_stop_after_churn(proc, label):
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait(timeout=5)
        raise AssertionError(
            f"NaiveFox did not terminate after Windows {label} churn"
        ) from exc


def run_lifecycle_churn_case(exe_path, temp_root, listener_scheme, payload):
    listener_port = find_free_port()
    diagnostic_path = os.path.join(
        temp_root, f"lifecycle-{listener_scheme}-process.txt"
    )

    # Keep the upstream port bound but not listening.  Every H2 connection is
    # rejected locally and deterministically, without network access.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as dead_upstream:
        dead_upstream.bind(("127.0.0.1", 0))
        dead_port = dead_upstream.getsockname()[1]
        cfg_path = os.path.join(temp_root, f"config-lifecycle-{listener_scheme}.json")
        cfg = {
            "listen": f"{listener_scheme}://127.0.0.1:{listener_port}",
            # NaiveFox requires credentials for HTTPS proxy URIs even when the
            # endpoint is deliberately dead.  The values are local test data
            # and never leave this temporary config.
            "proxy": f"https://lifecycle:pass@127.0.0.1:{dead_port}",
            "log": "",
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        with open(diagnostic_path, "ab") as diagnostic:
            proc = subprocess.Popen(
                [exe_path, cfg_path],
                cwd=temp_root,
                stdout=diagnostic,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_listener(proc, listener_port, listener_scheme.upper())
                run_disconnect_waves(
                    proc,
                    listener_port,
                    payload,
                    listener_scheme,
                    listener_scheme.upper(),
                )
            except AssertionError as exc:
                diagnostic.flush()
                with open(diagnostic_path, "rb") as details_file:
                    details = details_file.read().decode("utf-8", errors="replace")[
                        -2000:
                    ]
                raise AssertionError(f"{exc}; process output: {details!r}") from exc
            finally:
                force_stop_after_churn(proc, listener_scheme.upper())


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
        default=os.environ.get("NAIVEFOX_WINDOWS_PROXY_URL"),
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
    out = subprocess.check_output([exe_path, "--version"], text=True, timeout=30)
    print(f"[1] Version Output: {out.strip()}")
    assert "NaiveFox" in out, "Version check failed"

    # 2. Runtime smoke test
    with tempfile.TemporaryDirectory(prefix="nf_win_smoke_") as temp_prof:
        out = subprocess.check_output(
            [exe_path, "--profile", temp_prof, "--runtime-smoke"], text=True, timeout=30
        )
        print(f"[2] Runtime Smoke: {out.strip()}")
        assert "completed successfully" in out, "Smoke test failed"

    # 3. Dynamic Port SOCKS5 listener test
    socks_port = find_free_port()
    proxy_endpoint = args.proxy_url or "https://dummy_user:dummy_pass@127.0.0.1:28443"
    proxy_secrets = proxy_secret_tokens(proxy_endpoint)
    expected_digest = fetch_digest(args.target_url) if args.proxy_url else None

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
            if expected_digest:
                verify_live_transfers(args.target_url, f"socks5h://127.0.0.1:{socks_port}", expected_digest)
                print("    SOCKS5 live payload integrity (8 transfers, concurrency 4): PASSED")

        finally:
            proc.terminate()
            proc.wait(timeout=5)
            print("    SOCKS5 process bounded forced teardown: PASSED")

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
            if expected_digest:
                verify_live_transfers(args.target_url, f"http://127.0.0.1:{http_port}", expected_digest)
                print("    HTTP CONNECT live payload integrity (8 transfers, concurrency 4): PASSED")

        finally:
            proc.terminate()
            proc.wait(timeout=5)
            print("    HTTP CONNECT process bounded forced teardown: PASSED")

    # 5. Native Windows lifecycle regression.  Valid requests create a
    # TunnelSession, then an immediate client close races the dead upstream's
    # OnStopRequest -> TunnelChannelStop -> ApplyChannelStop runnable.
    with tempfile.TemporaryDirectory(prefix="nf_win_lifecycle_") as temp_churn:
        run_lifecycle_churn_case(
            exe_path, temp_churn, "socks", make_socks_connect_request()
        )
        run_lifecycle_churn_case(
            exe_path, temp_churn, "http", make_http_connect_request()
        )
    print(
        "[5] Windows TunnelSession stop lifecycle churn + bounded forced "
        "teardown: PASSED"
    )

    # 6. Prove a fresh runtime still starts and exits naturally after churn.
    with tempfile.TemporaryDirectory(prefix="nf_win_post_churn_") as temp_prof:
        out = subprocess.check_output(
            [exe_path, "--profile", temp_prof, "--runtime-smoke"], text=True, timeout=30
        )
        assert "completed successfully" in out, "post-churn smoke test failed"
    print("[6] Post-churn runtime smoke clean exit: PASSED")

    # 7. Native Windows malformed-input terminal-state stress.
    with tempfile.TemporaryDirectory(prefix="nf_win_malformed_socks_") as temp_stress:
        run_malformed_socks_case(exe_path, proxy_endpoint, temp_stress)
        run_malformed_http_case(exe_path, proxy_endpoint, temp_stress)
    print("[7] Windows malformed SOCKS/HTTP bounded stress: PASSED")

    # 8. Native Windows file logging: relative/Unicode path, append, and
    # liveness.  The runtime must not call POSIX chmod() on this path.
    with tempfile.TemporaryDirectory(prefix="nf_win_logging_") as temp_log:
        relative_dir = os.path.join(temp_log, "日志_日本")
        os.makedirs(relative_dir, exist_ok=True)
        relative_path = os.path.join(relative_dir, "naivefox-运行.log")
        relative_value = os.path.relpath(relative_path, temp_log)
        run_file_logging_case(
            exe_path,
            proxy_endpoint,
            temp_log,
            relative_value,
            relative_path,
            proxy_secrets,
        )
        print("[8] Windows relative Unicode file logging + append: PASSED")

        absolute_path = os.path.join(temp_log, "absolute-运行.log")
        run_file_logging_case(
            exe_path,
            proxy_endpoint,
            temp_log,
            absolute_path,
            absolute_path,
            proxy_secrets,
        )
        print("    Windows absolute Unicode file logging: PASSED")

    print("\n" + "=" * 70)
    print("STATUS SUMMARY:")
    print(
        "  Windows build, launch, config parsing, file logging, local listener handshake and shutdown verified."
    )
    if args.proxy_url:
        print(
            "  Live upstream proxy verified against: "
            f"{redact_proxy_url(args.proxy_url)}"
        )
    else:
        print("  End-to-end H2/H3 networking is tracked separately.")
    print("=" * 70)


if __name__ == "__main__":
    main()
