#!/usr/bin/env python3
"""Bounded, private loopback integration checks for the finite H2 prototype."""

import concurrent.futures
import contextlib
import hashlib
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camouflage_naivefox_config import build_config, write_config
from robustness_client import connect_socks
from target_server import SMALL_BODY, pattern_bytes


def choose_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def client(root, arm, bad_auth=False):
    port = choose_port()
    label = arm + ("-bad-auth" if bad_auth else "")
    config = build_config(
        arm,
        "h2",
        port,
        int(os.environ["NAIVEFOX_FIXTURE_PROXY_PORT"]),
        os.environ["NAIVEFOX_FIXTURE_USER"],
        "invalid" if bad_auth else os.environ["NAIVEFOX_FIXTURE_PASS"],
        max_connections=1 if bad_auth else 9,
    )
    config_path = root / (label + ".json")
    log_path = root / (label + ".log")
    write_config(config_path, config)
    env = os.environ.copy()
    env.pop("SSLKEYLOGFILE", None)
    env.pop("MOZ_LOG", None)
    env.pop("MOZ_LOG_FILE", None)
    env["LD_LIBRARY_PATH"] = os.environ["NAIVEFOX_CAPTURE_NAIVEFOX_LIBDIR"]
    env["SSL_CERT_FILE"] = os.environ["NAIVEFOX_FIXTURE_CA"]
    env["MOZ_CRASHREPORTER_DISABLE"] = "1"
    with log_path.open("xb") as log:
        proc = subprocess.Popen(
            [os.environ["NAIVEFOX_CAPTURE_NAIVEFOX_BIN"], str(config_path)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            readiness_polls = (
                1500 if os.environ.get("NAIVEFOX_FINITE_GDB") == "1" else 150
            )
            for _ in range(readiness_polls):
                if " listening on " in log_path.read_text(errors="replace"):
                    break
                if proc.poll() is not None:
                    raise RuntimeError("client exited before readiness")
                time.sleep(0.02)
            else:
                raise RuntimeError("client readiness timeout")
            yield port
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("bounded client did not drain and exit") from error
        except BaseException:
            print(f"FAIL {arm}: client_process_status={proc.poll()}", flush=True)
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError("client did not terminate cleanly")
        if proc.returncode != 0:
            raise RuntimeError(f"client shutdown was not clean: {proc.returncode}")
    text = log_path.read_text(errors="replace")
    if not bad_auth and "finite-exchanges rotated=1" not in text:
        raise RuntimeError("finite responses did not rotate")
    if not bad_auth and ("read-through" in arm) != (
        "finite-exchanges streamed-before-stop=1" in text
    ):
        raise RuntimeError("finite response delivery mode was not verified")
    if not bad_auth and ("both-read-through" in arm) != (
        "finite-exchanges upload-read-through=1" in text
    ):
        raise RuntimeError("finite upload streaming mode was not verified")
    if not bad_auth and ("budgeted" in arm) != (
        "finite-exchanges budgeted-download-complete=1 bytes=65536" in text
    ):
        raise RuntimeError("finite download byte-budget mode was not verified")
    for marker in (
        "finite-exchanges download-window-deferred=1 initial=1 maximum=4",
        "finite-exchanges download-window-expanded=1 trigger=first-data window=4",
    ):
        if not bad_auth and ("data-window" in arm) != (marker in text):
            raise RuntimeError("finite data-activated receive window was not verified")


def tunnel(arm, port, tls=False):
    target = int(
        os.environ[
            "NAIVEFOX_FIXTURE_HTTPS_PORT" if tls else "NAIVEFOX_FIXTURE_HTTP_PORT"
        ]
    )
    if arm.endswith("socks"):
        sock = connect_socks(port, "localhost", target)
    else:
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        sock.sendall(
            f"CONNECT localhost:{target} HTTP/1.1\r\nHost: localhost:{target}\r\n\r\n".encode()
        )
        head = bytearray()
        while not head.endswith(b"\r\n\r\n") and len(head) < 4096:
            part = sock.recv(1)
            if not part:
                raise RuntimeError("HTTP listener closed before CONNECT response")
            head.extend(part)
        if not head.startswith(b"HTTP/1.1 200 "):
            sock.close()
            raise RuntimeError("HTTP CONNECT rejected")
    sock.settimeout(15)
    if tls:
        ctx = ssl.create_default_context(cafile=os.environ["NAIVEFOX_FIXTURE_CA"])
        sock = ctx.wrap_socket(sock, server_hostname="localhost")
    return sock, target


def download(arm, port, size=None, tls=False, half_close=False, slow=False):
    sock, target = tunnel(arm, port, tls)
    with sock:
        path = "/small" if size is None else f"/large?size={size}"
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: localhost:{target}\r\nConnection: close\r\n\r\n".encode()
        )
        if half_close:
            sock.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(sock)
        response.begin()
        assert response.status == 200, "unexpected target response status"
        body = bytearray()
        while chunk := response.read(2048):
            body.extend(chunk)
            if slow:
                time.sleep(0.0001)
        expected = SMALL_BODY if size is None else pattern_bytes(0, size)
        actual = bytes(body)
        if actual != expected:
            first = next(
                (
                    index
                    for index, pair in enumerate(zip(actual, expected))
                    if pair[0] != pair[1]
                ),
                min(len(actual), len(expected)),
            )
            raise AssertionError(
                f"download integrity mismatch: expected_bytes={len(expected)} "
                f"actual_bytes={len(actual)} first_mismatch_offset={first} "
                f"expected_sha256={hashlib.sha256(expected).hexdigest()} "
                f"actual_sha256={hashlib.sha256(actual).hexdigest()}"
            )


def upload(arm, port):
    size = 768 * 1024
    body = pattern_bytes(0, size)
    sock, target = tunnel(arm, port)
    with sock:
        sock.sendall(
            f"POST /slow-upload?ms=1 HTTP/1.1\r\nHost: localhost:{target}\r\n"
            f"Content-Length: {size}\r\nConnection: close\r\n\r\n".encode()
        )
        for offset in range(0, size, 1371):
            sock.sendall(body[offset : offset + 1371])
        sock.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(sock)
        response.begin()
        assert response.status == 200
        assert json.loads(response.read()) == {
            "bytes": size,
            "sha256": hashlib.sha256(body).hexdigest(),
        }, "upload integrity or half-close failed"


def main():
    root = Path(
        tempfile.mkdtemp(
            prefix="finite-exchange-probes.",
            dir=Path(os.environ["NAIVEFOX_FIXTURE_RUN_DIR"]).parent.parent,
        )
    )
    print(f"private finite probe diagnostics: {root}", flush=True)
    arms = (
        "h2-finite-socks",
        "h2-finite-http-connect",
        "h2-finite-read-through-socks",
        "h2-finite-read-through-http-connect",
        "h2-finite-both-read-through-socks",
        "h2-finite-both-read-through-http-connect",
        "h2-finite-both-read-through-budgeted-socks",
        "h2-finite-both-read-through-budgeted-http-connect",
        "h2-finite-both-read-through-budgeted-data-window-socks",
        "h2-finite-both-read-through-budgeted-data-window-http-connect",
    )
    selected = os.environ.get("NAIVEFOX_FINITE_PROBE_ARMS")
    if selected:
        requested = tuple(selected.split(","))
        if not requested or any(arm not in arms for arm in requested):
            raise ValueError("unknown finite probe arm")
        arms = requested
    for arm in arms:
        with client(root, arm) as port:
            download(arm, port, tls=True)
            download(arm, port, half_close=True)
            download(arm, port, size=1024 * 1024, slow=True)
            upload(arm, port)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
                futures = [
                    workers.submit(download, arm, port, 128 * 1024) for _ in range(4)
                ]
                for future in futures:
                    future.result()
            sock, target = tunnel(arm, port)
            sock.sendall(
                f"GET /delay?ms=1500 HTTP/1.1\r\nHost: localhost:{target}\r\n\r\n".encode()
            )
            sock.close()
        with client(root, arm, bad_auth=True) as port:
            try:
                sock, _ = tunnel(arm, port)
            except (OSError, RuntimeError):
                pass
            else:
                sock.close()
                raise AssertionError("invalid upstream credentials accepted")
        print(
            f"PASS {arm}: TLS, 1MiB slow download, 768KiB upload, half-close, concurrency, cancel, bad-auth, shutdown"
        )


if __name__ == "__main__":
    main()
