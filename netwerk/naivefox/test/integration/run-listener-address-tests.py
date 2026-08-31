#!/usr/bin/env python3
"""Exercise explicit IPv4 and wildcard listeners through a local interface.

Only client listeners intentionally leave loopback. Caddy and the echo target
stay on loopback, with forwardproxy restricted to that one target port. Private
state stays under --objdir/naivefox-fixture and is removed after a successful run.
"""

import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True


def fixture_module():
    path = Path(__file__).with_name("run-no-connect-tests.py")
    spec = importlib.util.spec_from_file_location("listener_address_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reserve_ports(bind_address):
    reservations = []
    try:
        for _ in range(2):
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.bind((bind_address, 0))
            reservations.append(connection)
        return dict(zip(("socks", "http"), (sock.getsockname()[1] for sock in reservations)))
    finally:
        for connection in reservations:
            connection.close()


def open_tunnel(fixture, address, port, kind, target_port):
    connection = socket.create_connection((address, port), timeout=20)
    connection.settimeout(40)
    try:
        if kind == "socks":
            connection.sendall(b"\x05\x01\x00")
            fixture.require(fixture.receive(connection, 2) == b"\x05\x00", "SOCKS negotiation failed")
            hostname = b"localhost"
            connection.sendall(b"\x05\x01\x00\x03" + bytes([len(hostname)]) + hostname
                               + struct.pack("!H", target_port))
            head = fixture.receive(connection, 4)
            fixture.require(head[:3] == b"\x05\x00\x00", "SOCKS CONNECT failed")
            if head[3] == 1:
                fixture.receive(connection, 6)
            elif head[3] == 4:
                fixture.receive(connection, 18)
            elif head[3] == 3:
                fixture.receive(connection, fixture.receive(connection, 1)[0] + 2)
            else:
                raise RuntimeError("invalid SOCKS address type")
        else:
            authority = f"localhost:{target_port}"
            connection.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
            response = bytearray()
            while not response.endswith(b"\r\n\r\n"):
                response.extend(fixture.receive(connection, 1))
                fixture.require(len(response) <= 16384, "HTTP CONNECT response too large")
            fixture.require(response.split(b" ", 2)[1] == b"200", "HTTP CONNECT failed")
        return connection
    except BaseException:
        connection.close()
        raise


def exercise_address(fixture, ports, address, target_port):
    payload = bytes(range(256)) * 256
    for kind in ("socks", "http"):
        with open_tunnel(fixture, address, ports[kind], kind, target_port) as connection:
            connection.sendall(b"E" + payload)
            connection.shutdown(socket.SHUT_WR)
            received = bytearray()
            while chunk := connection.recv(16384):
                received.extend(chunk)
                fixture.require(len(received) <= len(payload), "echo exceeded the sent payload")
            fixture.require(received == payload, "listener transfer or half-close failed")


def client_environment(runtime, directory, certificate):
    excluded = {"NAIVEFOX_PROFILE", "NAIVEFOX_PROXY_USER", "NAIVEFOX_PROXY_PASS",
                "SSL_CERT_FILE", "SSLKEYLOGFILE", "MOZ_LOG", "MOZ_LOG_FILE", "LD_PRELOAD"}
    environment = {key: value for key, value in os.environ.items() if key not in excluded}
    environment.update(LD_LIBRARY_PATH=str(runtime.parent), TMPDIR=str(directory),
                       SSL_CERT_FILE=str(certificate), MOZ_CRASHREPORTER_DISABLE="1")
    return environment


def run_client(fixture, args, run, protocol, proxy_port, transport, credentials,
               bind_address, target_port):
    binding = "wildcard" if bind_address == "0.0.0.0" else "explicit-interface"
    directory = run / f"{transport}-{binding}"
    directory.mkdir(mode=0o700)
    ports = reserve_ports(bind_address)
    addresses = [args.address, "127.0.0.1"] if binding == "wildcard" else [args.address]
    config = fixture.client_config(protocol, proxy_port, transport, *credentials,
                                   ports, len(addresses) * 2)
    config["listen"] = [f"socks://{bind_address}:{ports['socks']}",
                        f"http://{bind_address}:{ports['http']}"]
    config_path = directory / "config.json"
    fixture.private_json(config_path, config)
    process = fixture.Process([str(args.runtime), str(config_path)], directory, "client",
                              client_environment(args.runtime, directory, run / "ca.crt"))
    try:
        def ready():
            text = process.log_path.read_text(errors="replace")
            return (f"SOCKS5 listening on {bind_address}:{ports['socks']}" in text
                    and f"HTTP CONNECT listening on {bind_address}:{ports['http']}" in text)
        fixture.wait_until(ready, "requested client listeners did not start", process)
        for port in ports.values():
            output = subprocess.check_output(["ss", "-H", "-ltn", f"sport = :{port}"], text=True)
            fixture.require(any(line.split()[3] == f"{bind_address}:{port}"
                                for line in output.splitlines()), "kernel listener address differs from config")
        for address in addresses:
            exercise_address(fixture, ports, address, target_port)
        process.exited_cleanly()
    finally:
        process.stop()
    return {"protocol": protocol, "transport": transport, "binding": binding,
            "nonloopback_connections": 2, "loopback_connections": 2 if binding == "wildcard" else 0,
            "payload_bytes_per_connection": 65536, "half_close": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--address", required=True, help="assigned non-loopback IPv4 address of this Linux host")
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--transport", choices=("classic", "no-connect", "both"), default="both")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("this gate runs a native Linux client; it does not claim Windows or Android coverage")
    address = ipaddress.ip_address(args.address)
    if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_multicast:
        parser.error("--address must be an assigned non-loopback unicast IPv4 address")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((args.address, 0))  # Fail before starting processes if the address is not assigned.
    args.objdir = args.objdir.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    fixture = fixture_module()
    owned_processes = []
    original_process = fixture.Process

    class TrackedProcess(original_process):
        def __init__(self, *arguments, **keywords):
            super().__init__(*arguments, **keywords)
            owned_processes.append(self)

    # Also track a helper's process if its readiness check raises before return.
    fixture.Process = TrackedProcess
    root = args.objdir / "naivefox-fixture"
    root.mkdir(exist_ok=True)
    previous_umask = os.umask(0o077)
    work = Path(tempfile.mkdtemp(prefix="listener-address-", dir=root))
    results = []
    success = False
    try:
        for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,)):
            run = work / protocol
            run.mkdir(mode=0o700)
            fixture.issue_certificates(run)
            target = fixture.TargetServer()
            caddy = None
            try:
                target_port = target.server_address[1]
                args.forward_proxy_ports = (target_port,)
                credentials = fixture.fixture_credentials()
                caddy, proxy_port = fixture.start_caddy(args, run, protocol, target_port, *credentials)
                for transport in (("classic", "no-connect") if args.transport == "both" else (args.transport,)):
                    for bind_address in ("0.0.0.0", args.address):
                        results.append(run_client(fixture, args, run, protocol, proxy_port,
                                                  transport, credentials, bind_address, target_port))
                fixture.require(not target.failures, "target reported a stream failure")
            finally:
                if caddy is not None:
                    caddy.stop()
                target.close()
        result = root / f"{work.name}-result.json"
        fixture.private_json(result, {"status": "PASS", "platform": "linux", "cases": results})
        success = True
        print(f"PASS: non-loopback and wildcard listeners; sanitized result: {result}")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}. Private diagnostics: {work}", flush=True)
        return 1
    finally:
        for process in reversed(owned_processes):
            process.stop()
        os.umask(previous_umask)
        if success:
            shutil.rmtree(work)


if __name__ == "__main__":
    raise SystemExit(main())
