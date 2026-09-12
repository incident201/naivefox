#!/usr/bin/env python3

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
from urllib.parse import quote


BLOCK = bytes(range(256)) * 256


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def receive(sock, count):
    result = bytearray()
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        require(bool(chunk), "stream ended before its declared length")
        result.extend(chunk)
    return bytes(result)


def payload_digest(length):
    digest = hashlib.sha256()
    for offset in range(0, length, len(BLOCK)):
        digest.update(BLOCK[: min(len(BLOCK), length - offset)])
    return digest.digest()


def send_payload(sock, length):
    for offset in range(0, length, len(BLOCK)):
        sock.sendall(BLOCK[: min(len(BLOCK), length - offset)])


class Target(socketserver.BaseRequestHandler):
    def handle(self):
        kind = None
        try:
            self.request.settimeout(30)
            kind = receive(self.request, 1)
            if kind == b"C":
                while self.request.recv(4096):
                    pass
                return
            if kind == b"E":
                while chunk := self.request.recv(4096):
                    self.request.sendall(chunk)
                self.request.shutdown(socket.SHUT_WR)
                return
            length = struct.unpack("!I", receive(self.request, 4))[0]
            require(length <= 8 * 1024 * 1024, "target request exceeds fixture bound")
            if kind == b"H":
                send_payload(self.request, length)
                self.request.shutdown(socket.SHUT_WR)
                body = receive(self.request, length)
                require(
                    hashlib.sha256(body).digest() == payload_digest(length),
                    "response-first upload mismatch",
                )
                require(
                    not self.request.recv(1), "response-first upload did not finish"
                )
                self.server.response_first_completed += 1
                return
            if kind == b"D":
                send_payload(self.request, length)
                self.request.shutdown(socket.SHUT_WR)
                return
            require(kind == b"U", "unknown fixture target request")
            digest = hashlib.sha256()
            remaining = length
            while remaining:
                chunk = self.request.recv(min(4096, remaining))
                require(bool(chunk), "upload ended early at target")
                remaining -= len(chunk)
                digest.update(chunk)
                time.sleep(0.0005)
            require(not self.request.recv(1), "upload did not end with half-close")
            self.request.sendall(struct.pack("!Q", length) + digest.digest())
            self.request.shutdown(socket.SHUT_WR)
        except (OSError, RuntimeError):
            if kind != b"C":
                self.server.failures.append("target stream failed")


class TargetServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, host="127.0.0.1"):
        super().__init__((host, 0), Target)
        self.failures = []
        self.accepted_connections = 0
        self.response_first_completed = 0
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def get_request(self):
        connection = super().get_request()
        self.accepted_connections += 1
        return connection

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


def free_port(udp=False, dual=False):
    for _ in range(100):
        with socket.socket(
            type=socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
        ) as sock:
            sock.bind(("127.0.0.1", 0))
            if dual:
                with socket.socket(
                    type=socket.SOCK_STREAM if udp else socket.SOCK_DGRAM
                ) as other:
                    try:
                        other.bind(sock.getsockname())
                    except OSError:
                        continue
            return sock.getsockname()[1]
    raise RuntimeError("no free dual-protocol fixture port")


def private_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def openssl(run, *arguments):
    with (run / "openssl.log").open("ab") as log:
        result = subprocess.run(
            ["openssl", *map(str, arguments)], stdout=log, stderr=log
        )
    require(result.returncode == 0, "isolated fixture certificate generation failed")


def issue_certificates(run):
    openssl(
        run,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-nodes",
        "-days",
        "2",
        "-keyout",
        run / "ca.key",
        "-out",
        run / "ca.crt",
        "-subj",
        "/CN=NaiveFox Test Root",
        "-addext",
        "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    openssl(
        run,
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-nodes",
        "-keyout",
        run / "server.key",
        "-out",
        run / "server.csr",
        "-subj",
        "/CN=localhost",
    )
    (run / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
    )
    openssl(
        run,
        "x509",
        "-req",
        "-sha256",
        "-days",
        "2",
        "-in",
        run / "server.csr",
        "-CA",
        run / "ca.crt",
        "-CAkey",
        run / "ca.key",
        "-CAcreateserial",
        "-out",
        run / "server.crt",
        "-extfile",
        run / "server.ext",
    )


class Process:
    def __init__(self, command, directory, name, env):
        self.log_path = directory / (name + ".log")
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen(
            command, cwd=directory, env=env, stdout=self.log, stderr=self.log
        )

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()

    def exited_cleanly(self):
        try:
            status = self.process.wait(timeout=20)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("client did not complete bounded shutdown") from error
        require(status == 0, "process did not exit successfully after draining streams")
        return True


def wait_until(predicate, message, process=None, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if process is not None:
            require(process.process.poll() is None, message + ": process exited")
        time.sleep(0.05)
    raise RuntimeError(message)


def socket_listeners(port, udp=False):
    result = subprocess.run(
        ["ss", "-H", "-lun" if udp else "-ltn", f"sport = :{port}"],
        text=True,
        capture_output=True,
        check=True,
    )
    return bool(result.stdout.strip())


def fixture_credentials():
    return "fixture user@" + secrets.token_hex(8), "p:/" + secrets.token_hex(24) + " %"


def caddyfile_text(allowed_ports=()):
    require(
        all(type(port) is int and 1 <= port <= 65535 for port in allowed_ports),
        "invalid fixture destination port",
    )
    ports = (
        "            ports " + " ".join(map(str, allowed_ports)) + "\n"
        if allowed_ports
        else ""
    )
    return (
        """{
    admin off
    auto_https disable_redirects
    skip_install_trust
    grace_period 250ms
    servers {
        protocols {$NF_PROTOCOL}
    }
}
https://:{$NF_PORT} {
    bind 127.0.0.1
    tls {$NF_CERT} {$NF_CERT_KEY}
    log {
        output file {$NF_ACCESS_LOG}
    }
    route {
        naivefox_transport {
            basic_auth "{$NF_PROXY_USER}" "{$NF_PROXY_PASSWORD}"
            allow 127.0.0.1/32
            deny all
"""
        + ports
        + """        }
        respond 404
    }
}
"""
    )


def prepare_application(run):
    source = Path(__file__).resolve().parent / "http_app"
    root = run / "application"
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_bytes((source / "index.html").read_bytes())
    for name in ("site.css", "app.js"):
        (assets / name).write_bytes((source / name).read_bytes())
    for index in range(1, 5):
        (assets / f"image-{index}.svg").write_bytes((source / "image.svg").read_bytes())
    return root.resolve()


def start_caddy(args, run, protocol, target_port, user, password):
    port = free_port(udp=protocol == "h3", dual=True)
    caddyfile = run / "Caddyfile"
    caddyfile.write_text(caddyfile_text(getattr(args, "allowed_ports", ())))
    env = dict(
        os.environ,
        NF_PROTOCOL=protocol,
        NF_PORT=str(port),
        NF_CERT=str(run / "server.crt"),
        NF_CERT_KEY=str(run / "server.key"),
        NF_PROXY_USER=user,
        NF_PROXY_PASSWORD=password,
        NF_ACCESS_LOG=str(run / "access.jsonl"),
        XDG_DATA_HOME=str(run / "caddy-data"),
        XDG_CONFIG_HOME=str(run / "caddy-config"),
    )
    adapted = subprocess.run(
        [
            str(args.caddy),
            "adapt",
            "--config",
            str(caddyfile),
            "--adapter",
            "caddyfile",
        ],
        env=env,
        text=True,
        capture_output=True,
    )
    require(adapted.returncode == 0, "NaiveFox Caddyfile adaptation failed")
    config = json.loads(adapted.stdout)
    servers = config["apps"]["http"]["servers"]
    require(len(servers) == 1, "fixture must contain one NaiveFox Caddy server")
    server = next(iter(servers.values()))
    require(
        server["listen"] == [f"127.0.0.1:{port}"], "fixture listener escaped loopback"
    )
    require(server["protocols"] == [protocol], "fixture protocol is not strict")
    handlers = [item for route in server["routes"] for item in route.get("handle", [])]
    while handlers:
        item = handlers.pop()
        if item.get("handler") == "naivefox_transport":
            item["stats_path"] = str(run / "server-stats.json")
            item["application_root"] = str(
                getattr(args, "application_root", None) or prepare_application(run)
            )
        for route in item.get("routes", []):
            handlers.extend(route.get("handle", []))
    mutator = getattr(args, "server_mutator", None)
    if mutator is not None:
        mutator(server)
    server["protocols"] = ["h1", protocol]
    private_json(run / "caddy.json", config)
    process = Process(
        [str(args.caddy), "run", "--config", str(run / "caddy.json")], run, "caddy", env
    )
    wait_until(
        lambda: socket_listeners(port, udp=protocol == "h3"),
        "Caddy listener did not start",
        process,
    )
    if protocol == "h3":
        require(socket_listeners(port), "H3 server TCP canary listener missing")
    else:
        require(
            not socket_listeners(port, udp=True),
            "strict H2 fixture unexpectedly listens on UDP",
        )
    return process, port


def proxy_uri(protocol, proxy_port, user, password):
    require((user is None) == (password is None), "partial fixture credentials")
    scheme = "quic" if protocol == "h3" else "https"
    credentials = (
        ""
        if user is None
        else quote(user, safe="") + ":" + quote(password, safe="") + "@"
    )
    return f"{scheme}://{credentials}localhost:{proxy_port}"


def client_config(protocol, proxy_port, user, password, ports, connections):
    return {
        "listen": [
            f"socks://127.0.0.1:{ports['socks']}",
            f"http://127.0.0.1:{ports['http']}",
        ],
        "proxy": proxy_uri(protocol, proxy_port, user, password),
        "host-resolver-rules": "MAP localhost 127.0.0.1",
        "max-connections": connections,
        "log": "",
    }


def start_client(
    args, run, name, protocol, proxy_port, user, password, connections=0, trusted=True
):
    directory = run / name
    directory.mkdir(mode=0o700)
    selected_ports = getattr(args, "listener_ports", None)
    if selected_ports is None:
        socks_port = free_port()
        http_port = free_port()
        while http_port == socks_port:
            http_port = free_port()
    else:
        socks_port, http_port = selected_ports["socks"], selected_ports["http"]
        require(socks_port != http_port, "shared listeners must use distinct ports")
    baseline = getattr(args, "base_client_config", None)
    if baseline is None:
        config = client_config(
            protocol,
            proxy_port,
            user,
            password,
            {"socks": socks_port, "http": http_port},
            connections,
        )
    else:
        config = copy.deepcopy(baseline)
    mapped_credentials = getattr(args, "proxy_credentials_by_listener", None)
    if mapped_credentials is not None:
        require(
            len(mapped_credentials) == len(config["listen"]),
            "fixture proxy mapping differs from listeners",
        )
        config["proxy"] = [
            proxy_uri(protocol, proxy_port, *credentials)
            for credentials in mapped_credentials
        ]
    config_path = Path(getattr(args, "client_config_path", directory / "config.json"))
    private_json(config_path, config)
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "NAIVEFOX_PROFILE",
            "NAIVEFOX_PROXY_USER",
            "NAIVEFOX_PROXY_PASS",
            "SSL_CERT_FILE",
            "SSLKEYLOGFILE",
            "MOZ_LOG",
            "MOZ_LOG_FILE",
            "LD_PRELOAD",
        }
    }
    env.update(
        LD_LIBRARY_PATH=str(args.runtime.parent),
        TMPDIR=str(directory),
        MOZ_CRASHREPORTER_DISABLE="1",
    )
    if trusted:
        env["SSL_CERT_FILE"] = str(run / "ca.crt")
    factory = getattr(args, "client_factory", None)
    if factory is not None:
        return factory(
            args, directory, config, env, {"socks": socks_port, "http": http_port}
        )
    process = Process([str(args.runtime), str(config_path)], directory, "client", env)

    def ready():
        text = process.log_path.read_text(errors="replace")
        return (
            f"SOCKS5 listening on 127.0.0.1:{socks_port}" in text
            and f"HTTP CONNECT listening on 127.0.0.1:{http_port}" in text
        )

    wait_until(ready, "client listeners did not start", process)
    return process, {"socks": socks_port, "http": http_port}


def open_tunnel(
    ports, listener, target_port, host="localhost", rejected=False, timeout=40
):
    sock = socket.create_connection(
        ("127.0.0.1", ports[listener]), timeout=min(20, timeout)
    )
    sock.settimeout(timeout)
    try:
        if listener == "socks":
            sock.sendall(b"\x05\x01\x00")
            require(receive(sock, 2) == b"\x05\x00", "SOCKS negotiation failed")
            encoded = host.encode("ascii")
            sock.sendall(
                b"\x05\x01\x00\x03"
                + bytes([len(encoded)])
                + encoded
                + struct.pack("!H", target_port)
            )
            head = receive(sock, 4)
            require(head[0] == 5 and head[2] == 0, "invalid SOCKS reply")
            success = head[1] == 0
            if head[3] == 1:
                receive(sock, 6)
            elif head[3] == 4:
                receive(sock, 18)
            elif head[3] == 3:
                receive(sock, receive(sock, 1)[0] + 2)
            else:
                raise RuntimeError("invalid SOCKS address type")
        else:
            authority = f"{host}:{target_port}"
            sock.sendall(
                f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode()
            )
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header.extend(receive(sock, 1))
                require(len(header) <= 16384, "HTTP CONNECT reply exceeds bound")
            success = bytes(header).split(b" ", 2)[1] == b"200"
        require(
            success != rejected,
            "unexpected local CONNECT success" if rejected else "local CONNECT failed",
        )
        if rejected:
            sock.close()
            return None
        return sock
    except BaseException:
        sock.close()
        raise


def reject_policy(ports, listener, target_port, host="localhost"):
    return open_tunnel(ports, listener, target_port, host=host, rejected=True)


def download(
    ports, listener, target_port, length=1024 * 1024, slow=False, host="localhost"
):
    with open_tunnel(ports, listener, target_port, host=host) as sock:
        if slow:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        sock.sendall(b"D" + struct.pack("!I", length))
        sock.shutdown(socket.SHUT_WR)
        digest = hashlib.sha256()
        received = 0
        while chunk := sock.recv(4096 if slow else 65536):
            digest.update(chunk)
            received += len(chunk)
            require(received <= length, "download exceeded declared length")
            if slow:
                time.sleep(0.0005)
        expected = payload_digest(length)
        require(
            received == length and digest.digest() == expected,
            f"download integrity or half-close failed ({received}/{length} bytes; "
            f"sha256={digest.hexdigest()}, expected={expected.hex()})",
        )


def upload(ports, listener, target_port, length=1024 * 1024, host="localhost"):
    with open_tunnel(ports, listener, target_port, host=host) as sock:
        sock.sendall(b"U" + struct.pack("!I", length))
        send_payload(sock, length)
        sock.shutdown(socket.SHUT_WR)
        result = receive(sock, 40)
        require(not sock.recv(1), "upload acknowledgement did not reach EOF")
        require(
            result == struct.pack("!Q", length) + payload_digest(length),
            "upload integrity or half-close failed",
        )


def echo_wake(ports, listener, target_port, idle_seconds=2):
    with open_tunnel(ports, listener, target_port) as sock:
        sock.sendall(b"E" + BLOCK[:4096])
        require(receive(sock, 4096) == BLOCK[:4096], "initial echo failed")
        time.sleep(idle_seconds)
        sock.sendall(BLOCK[256:4352])
        require(receive(sock, 4096) == BLOCK[256:4352], "idle wake echo failed")
        sock.shutdown(socket.SHUT_WR)
        require(not sock.recv(1), "echo half-close did not drain")


def cancel_stream(ports, target_port):
    with open_tunnel(ports, "socks", target_port) as sock:
        sock.sendall(b"C")
        time.sleep(0.05)
        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_LINGER,
            struct.pack("hh" if os.name == "nt" else "ii", 1, 0),
        )


def concurrent_open_streams(ports, target_port, count=40):
    require(count > 32, "concurrent OPEN gate must exceed one carrier")
    lock = threading.Lock()
    active = 0
    peak = 0

    def all_open():
        with lock:
            require(active == count, "logical streams were not simultaneously open")

    barrier = threading.Barrier(count, action=all_open)

    def transfer(index):
        nonlocal active, peak
        counted = False
        try:
            with open_tunnel(ports, "socks", target_port) as sock:
                with lock:
                    active += 1
                    peak = max(peak, active)
                    counted = True
                barrier.wait(timeout=60)
                payload = b"logical-stream-" + struct.pack("!I", index) + BLOCK[:64]
                sock.sendall(b"E" + payload)
                require(
                    receive(sock, len(payload)) == payload,
                    "concurrent logical stream echo mismatch",
                )
                sock.shutdown(socket.SHUT_WR)
                require(
                    not sock.recv(1), "concurrent logical stream did not half-close"
                )
        except BaseException:
            barrier.abort()
            raise
        finally:
            if counted:
                with lock:
                    active -= 1

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(transfer, index) for index in range(count)]
        for future in futures:
            try:
                future.result(timeout=90)
            except Exception as error:
                failures.append(error)
    if failures:
        cause = next(
            (
                error
                for error in failures
                if not isinstance(error, threading.BrokenBarrierError)
            ),
            failures[0],
        )
        raise RuntimeError("concurrent logical stream gate failed") from cause
    require(peak == count, "concurrent OPEN gate never reached the requested peak")


def auth_partition_streams(ports, target_port):
    first_payload = b"authenticated-carrier-first"
    second_payload = b"authenticated-carrier-second"
    with open_tunnel(ports, "socks", target_port) as first:
        first.sendall(b"E" + first_payload)
        require(
            receive(first, len(first_payload)) == first_payload,
            "valid carrier did not authenticate",
        )
        open_tunnel(ports, "http", target_port, rejected=True)
        first.sendall(second_payload)
        require(
            receive(first, len(second_payload)) == second_payload,
            "bad credentials poisoned a valid carrier",
        )
        with open_tunnel(ports, "socks", target_port) as second:
            second.sendall(b"E" + second_payload)
            require(
                receive(second, len(second_payload)) == second_payload,
                "valid credentials failed after rejection",
            )
            second.shutdown(socket.SHUT_WR)
            require(not second.recv(1), "second authenticated stream did not drain")
        first.shutdown(socket.SHUT_WR)
        require(not first.recv(1), "first authenticated stream did not drain")


def target_variety(ports, first_port, second_port):
    for listener in ("socks", "http"):
        download(ports, listener, first_port, 32768, host="127.0.0.1")
        download(ports, listener, second_port, 32768, host="localhost")


def exercise(ports, target_port, label, parallel_batches=1, idle_seconds=2):
    for listener in ("socks", "http"):
        try:
            download(ports, listener, target_port, slow=True)
            print(
                f"PASS {label} {listener}: 1MiB slow download and half-close",
                flush=True,
            )
            upload(ports, listener, target_port)
            print(
                f"PASS {label} {listener}: 1MiB slow-target upload and half-close",
                flush=True,
            )
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"{label} {listener} transfer: {error}") from error
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for batch in range(parallel_batches):
            futures = [
                pool.submit(
                    download if index % 2 == 0 else upload,
                    ports,
                    "socks" if index < 2 else "http",
                    target_port,
                    256 * 1024,
                )
                for index in range(4)
            ]
            for index, future in enumerate(futures):
                try:
                    future.result(timeout=60)
                except (OSError, RuntimeError) as error:
                    raise RuntimeError(
                        f"{label} parallel batch {batch} transfer {index}: {error}"
                    ) from error
    for listener in ("socks", "http"):
        echo_wake(
            ports, listener, target_port, idle_seconds if listener == "socks" else 2
        )


def response_first_transfer(ports, listener, target_port, length=65536):
    with open_tunnel(ports, listener, target_port) as sock:
        sock.sendall(b"H" + struct.pack("!I", length))
        body = receive(sock, length)
        require(
            hashlib.sha256(body).digest() == payload_digest(length),
            "response-first download mismatch",
        )
        require(not sock.recv(1), "response EOF was not delivered before request FIN")
        send_payload(sock, length)
        sock.shutdown(socket.SHUT_WR)


def response_first_half_close(ports, listener, target, length=65536):
    before = target.response_first_completed
    response_first_transfer(ports, listener, target.server_address[1], length)
    wait_until(
        lambda: target.response_first_completed == before + 1,
        "response-first upload was lost",
    )


def access_requests(run):
    path = run / "access.jsonl"
    return [
        json.loads(line)["request"]
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def validate_carrier_stats(stats, protocol):
    selected, excluded = (
        ("h3_opened", "ws_opened") if protocol == "h3" else ("ws_opened", "h3_opened")
    )
    require(stats[selected] >= 1 and stats[excluded] == 0, "carrier path differs")
    peers = [peer for peer in stats["peers"] if peer["opened"]]
    require(
        len(peers) >= 2
        and all(0 < peer["peak_streams"] <= 32 for peer in peers)
        and sum(peer["peak_streams"] for peer in peers) >= 40,
        "concurrent streams did not use bounded carriers",
    )


def verify_idle_h3_transition(run, ports, target_port, server):
    echo_wake(ports, "socks", target_port, 0.05)
    wait_until(
        lambda: (
            sum(
                request["method"] == "GET" and request["uri"] == "/api/events/brief"
                for request in access_requests(run)
            )
            >= 6
        ),
        "HTTP/3 startup did not complete",
        server,
        timeout=90,
    )
    began = time.monotonic()
    echo_wake(ports, "http", target_port, 0.05)
    elapsed_ms = (time.monotonic() - began) * 1000
    require(elapsed_ms < 10000, "idle HTTP/3 transition waited for heartbeat")
    return elapsed_ms


def run_protocol(args, base, protocol):
    """Shared native platform gate; Linux additionally captures strict routing."""
    run = base / protocol
    run.mkdir(mode=0o700)
    issue_certificates(run)
    target = TargetServer()
    processes = []
    try:
        user, password = fixture_credentials()
        server, port = start_caddy(
            args, run, protocol, target.server_address[1], user, password
        )
        processes.append(server)
        client, ports = start_client(args, run, "valid", protocol, port, user, password)
        processes.append(client)
        transition_ms = (
            verify_idle_h3_transition(run, ports, target.server_address[1], server)
            if protocol == "h3"
            else None
        )
        exercise(
            ports,
            target.server_address[1],
            protocol,
            getattr(args, "parallel_batches", 1),
        )
        for listener in ("socks", "http"):
            response_first_half_close(ports, listener, target)
        concurrent_open_streams(ports, target.server_address[1])
        cancel_stream(ports, target.server_address[1])
        download(ports, "http", target.server_address[1], 65536)
        client.stop()
        client.exited_cleanly()
        for name, trusted, credential in (
            ("invalid-auth", True, password + "wrong"),
            ("untrusted-ca", False, password),
        ):
            before = target.accepted_connections
            rejected, rejected_ports = start_client(
                args, run, name, protocol, port, user, credential, trusted=trusted
            )
            processes.append(rejected)
            for listener in ("socks", "http"):
                open_tunnel(
                    rejected_ports, listener, target.server_address[1], rejected=True
                )
            rejected.stop()
            rejected.exited_cleanly()
            require(
                target.accepted_connections == before, "rejected session reached target"
            )
        server.stop()
        server.exited_cleanly()
        stats = json.loads((run / "server-stats.json").read_text())
        require(
            stats["opens"] >= 50 and not target.failures,
            "target integrity or stream count failed",
        )
        validate_carrier_stats(stats, protocol)
        result = {
            "protocol": protocol,
            "passed": True,
            "opens": stats["opens"],
            "response_first_half_closes": target.response_first_completed,
            "idle_h3_transition_ms": transition_ms,
        }
        private_json(run / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()
