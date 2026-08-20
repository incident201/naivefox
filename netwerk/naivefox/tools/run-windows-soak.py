#!/usr/bin/env python3
"""Bounded native-Windows NaiveFox soak and malformed-input stress runner.

The upstream proxy URI is supplied only through NAIVEFOX_SOAK_PROXY_URL.  The
URI is written to a private temporary config and is never printed or copied to
the repository.  The runner intentionally uses the staged executable, not an
objdir binary.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psutil


README_URL = (
    "https://raw.githubusercontent.com/incident201/naivefox/naivefox/"
    "netwerk/naivefox/README.md"
)
ARCHIVE_URL = (
    "https://github.com/klzgrad/naiveproxy/archive/refs/tags/"
    "v150.0.7871.63-1.tar.gz"
)
PAGE_URL = "https://www.example.com/"


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def curl_fetch(url, output, socks_port=None, timeout=60):
    command = [
        "curl.exe",
        "--fail",
        "--silent",
        "--show-error",
        "--noproxy",
        "",
        "--connect-timeout",
        "15",
        "--max-time",
        str(timeout),
        "--output",
        str(output),
    ]
    if socks_port is not None:
        command.extend(["--socks5-hostname", f"127.0.0.1:{socks_port}"])
    command.append(url)
    started = time.monotonic()
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout + 10,
    )
    elapsed = time.monotonic() - started
    return result.returncode, elapsed, result.stderr[-400:]


def wait_for_socks(port, proc):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"NaiveFox exited before SOCKS readiness: {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
                sock.sendall(b"\x05\x01\x00")
                if sock.recv(2) == b"\x05\x00":
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("timed out waiting for native Windows SOCKS listener")


def malformed_stress(port, count=20, include_large=False):
    greeting = b"\x05\x01\x00"
    bad = greeting + b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00"
    bad_atyp = greeting + b"\x05\x01\x00\x09\x00\x00\x00\x00"
    bad_version = b"\x04\x01\x00\x01\x00\x00\x00\x00\x00\x00"
    probes = [bad, bad_atyp, bad_version]
    for index in range(count):
        payload = probes[index % len(probes)]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.settimeout(1)
                sock.sendall(payload)
                try:
                    sock.recv(32)
                except (ConnectionResetError, TimeoutError, socket.timeout):
                    pass
        except OSError:
            # A terminal reject is allowed to close the local connection; the
            # caller checks the process liveness immediately afterwards.
            pass
    if include_large:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                sock.sendall(bad + b"x" * (2 * 1024 * 1024))
        except OSError:
            pass


def process_stats(proc):
    try:
        current = psutil.Process(proc.pid)
        info = current.memory_info()
        values = {
            "rss": info.rss,
            "threads": current.num_threads(),
        }
        try:
            values["handles"] = current.num_handles()
        except (AttributeError, psutil.AccessDenied, psutil.NoSuchProcess):
            values["handles"] = None
        return values
    except (psutil.AccessDenied, psutil.NoSuchProcess, ProcessLookupError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--protocol", choices=("h2", "h3"), default="h3")
    parser.add_argument("--duration", type=int, default=600)
    args = parser.parse_args()
    if args.duration < 600 or args.duration > 900:
        raise SystemExit("duration must be between 600 and 900 seconds")

    proxy_url = os.environ.get("NAIVEFOX_SOAK_PROXY_URL", "")
    if not proxy_url or "@" not in proxy_url:
        raise SystemExit("NAIVEFOX_SOAK_PROXY_URL must be supplied in the environment")
    expected_scheme = "quic://" if args.protocol == "h3" else "https://"
    if not proxy_url.startswith(expected_scheme):
        raise SystemExit(f"proxy scheme must be {expected_scheme} for --protocol {args.protocol}")

    package = Path(args.package_dir).resolve()
    executable = package / "naivefox.exe"
    if not executable.is_file():
        raise SystemExit(f"missing staged executable: {executable}")

    run_root = Path(tempfile.mkdtemp(prefix="naivefox-windows-soak-"))
    client_log = run_root / "client.log"
    config_path = run_root / "config.json"
    socks_port = free_port()
    config_path.write_text(
        json.dumps(
            {
                "listen": f"socks://127.0.0.1:{socks_port}",
                "proxy": proxy_url,
                "log": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    baseline_dir = run_root / "baseline"
    baseline_dir.mkdir()
    baseline_paths = {
        "readme": baseline_dir / "README.md",
        "archive": baseline_dir / "naiveproxy.tar.gz",
        "page": baseline_dir / "example.html",
    }
    target_urls = {
        "readme": README_URL,
        "archive": ARCHIVE_URL,
        "page": PAGE_URL,
    }

    proc = None
    samples = []
    samples_lock = threading.Lock()
    stop_monitor = threading.Event()

    try:
        for name, url in target_urls.items():
            code, _, error = curl_fetch(url, baseline_paths[name], timeout=90)
            if code != 0:
                raise RuntimeError(f"direct baseline fetch failed for {name}: {error!r}")
        baseline_hashes = {name: sha256(path) for name, path in baseline_paths.items()}
        baseline_sizes = {name: path.stat().st_size for name, path in baseline_paths.items()}

        log_stream = open(client_log, "wb")
        proc = subprocess.Popen(
            [str(executable), str(config_path)],
            cwd=str(run_root),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
        )
        wait_for_socks(socks_port, proc)

        def monitor():
            while not stop_monitor.wait(1.0):
                with samples_lock:
                    samples.append((time.monotonic(), process_stats(proc)))

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

        start = time.monotonic()
        deadline = start + args.duration
        attempts = []
        wave_index = 0
        next_load = start
        next_stress = start + 15
        while time.monotonic() < deadline:
            now = time.monotonic()
            if proc.poll() is not None:
                raise RuntimeError(f"NaiveFox exited during soak: {proc.returncode}")

            if now >= next_stress:
                malformed_stress(
                    socks_port,
                    count=20,
                    include_large=(wave_index % 4 == 0),
                )
                if proc.poll() is not None:
                    raise RuntimeError("NaiveFox exited after malformed stress")
                next_stress += 30

            if now >= next_load:
                wave_index += 1
                names = ["readme"]
                if wave_index % 2 == 0:
                    names.append("page")
                if wave_index % 4 == 0:
                    names.append("archive")
                if wave_index % 5 == 0:
                    names = ["readme", "page", "archive", "readme"]

                def one(name, ordinal):
                    output = run_root / f"wave-{wave_index:03d}-{ordinal}-{name}.bin"
                    code, elapsed, error = curl_fetch(
                        target_urls[name], output, socks_port=socks_port, timeout=60
                    )
                    good = (
                        code == 0
                        and output.is_file()
                        and output.stat().st_size == baseline_sizes[name]
                        and sha256(output) == baseline_hashes[name]
                    )
                    return {
                        "name": name,
                        "code": code,
                        "elapsed": elapsed,
                        "bytes": output.stat().st_size if output.exists() else 0,
                        "integrity": good,
                        "error": error if code else "",
                    }

                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(one, name, index) for index, name in enumerate(names)]
                    wave_results = [future.result() for future in futures]
                attempts.extend(wave_results)
                if any(not result["integrity"] for result in wave_results):
                    raise RuntimeError("integrity failure during Windows soak wave")
                next_load += 30

            time.sleep(0.2)

        # A final request immediately before stopping proves the process was
        # alive at the end of the complete observation window.
        final_output = run_root / "final-readme.bin"
        code, elapsed, error = curl_fetch(
            README_URL, final_output, socks_port=socks_port, timeout=60
        )
        final_good = (
            code == 0
            and final_output.is_file()
            and final_output.stat().st_size == baseline_sizes["readme"]
            and sha256(final_output) == baseline_hashes["readme"]
        )
        attempts.append(
            {
                "name": "final-readme",
                "code": code,
                "elapsed": elapsed,
                "bytes": final_output.stat().st_size if final_output.exists() else 0,
                "integrity": final_good,
                "error": error if code else "",
            }
        )
        if not final_good:
            raise RuntimeError("final Windows soak request failed integrity")

        stop_monitor.set()
        monitor_thread.join(timeout=2)
        if proc.poll() is not None:
            raise RuntimeError(f"NaiveFox exited before soak shutdown: {proc.returncode}")
        log_stream.flush()
        log_stream.close()
        contents = client_log.read_text(encoding="utf-8", errors="replace")
        if "Proxy-Authorization" in contents or "dummy_pass" in contents:
            raise RuntimeError("credentials appeared in native soak log")
        expected_protocol = f"Outer protocol: {args.protocol}"
        protocol_count = contents.count(expected_protocol)
        padding_count = contents.count("Padding negotiated: yes")
        if protocol_count < len(attempts) or padding_count < len(attempts):
            raise RuntimeError(
                f"missing protocol/padding records: protocol={protocol_count} "
                f"padding={padding_count} requests={len(attempts)}"
            )

        with samples_lock:
            observed = [item[1] for item in samples if item[1] is not None]
        rss = [item["rss"] for item in observed]
        threads = [item["threads"] for item in observed]
        handles = [item["handles"] for item in observed if item["handles"] is not None]
        summary = {
            "protocol": args.protocol,
            "duration_seconds": args.duration,
            "waves": len(attempts),
            "requests_ok": sum(1 for item in attempts if item["integrity"]),
            "requests_total": len(attempts),
            "bytes": sum(item["bytes"] for item in attempts),
            "sample_seconds": len(observed),
            "rss_mib_min": min(rss) / 2**20 if rss else None,
            "rss_mib_max": max(rss) / 2**20 if rss else None,
            "rss_mib_final": rss[-1] / 2**20 if rss else None,
            "threads_max": max(threads) if threads else None,
            "handles_max": max(handles) if handles else None,
            "outer_protocol_records": protocol_count,
            "padding_yes_records": padding_count,
            "credentials_absent": True,
        }
        print(json.dumps(summary, sort_keys=True))
    finally:
        stop_monitor.set()
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if "log_stream" in locals() and not log_stream.closed:
                log_stream.close()
        # Keep only the caller's stdout summary.  The config, credentials,
        # bodies, profile and native log are all private transient material.
        shutil.rmtree(run_root, ignore_errors=True)


if __name__ == "__main__":
    main()
