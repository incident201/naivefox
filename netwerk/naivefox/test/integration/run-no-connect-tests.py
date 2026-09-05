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
        with socket.socket(type=socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            if dual:
                with socket.socket(type=socket.SOCK_STREAM if udp else socket.SOCK_DGRAM) as other:
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
        result = subprocess.run(["openssl", *map(str, arguments)], stdout=log, stderr=log)
    require(result.returncode == 0, "isolated fixture certificate generation failed")


def issue_certificates(run):
    openssl(run, "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "2",
            "-keyout", run / "ca.key", "-out", run / "ca.crt", "-subj", "/CN=NaiveFox NoConnect Test Root",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    openssl(run, "req", "-new", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-keyout", run / "server.key", "-out", run / "server.csr", "-subj", "/CN=localhost")
    (run / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
    openssl(run, "x509", "-req", "-sha256", "-days", "2", "-in", run / "server.csr",
            "-CA", run / "ca.crt", "-CAkey", run / "ca.key", "-CAcreateserial",
            "-out", run / "server.crt", "-extfile", run / "server.ext")


class Process:
    def __init__(self, command, directory, name, env):
        self.log_path = directory / (name + ".log")
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen(command, cwd=directory, env=env, stdout=self.log, stderr=self.log)

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
        require(status == 0, "client did not exit successfully after draining streams")


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
    result = subprocess.run(["ss", "-H", "-lun" if udp else "-ltn", f"sport = :{port}"],
                            text=True, capture_output=True, check=True)
    return bool(result.stdout.strip())


def fixture_credentials():
    return "fixture user@" + secrets.token_hex(8), "p:/" + secrets.token_hex(24) + " %"


def caddyfile_text(allowed_ports=()):
    require(all(type(port) is int and 1 <= port <= 65535 for port in allowed_ports),
            "invalid fixture forward-proxy port")
    port_rule = "                ports " + " ".join(map(str, allowed_ports)) + "\n" if allowed_ports else ""
    return """{
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
            profile continuous-bulk-pipeline
            forward_proxy {
                basic_auth "{$NF_PROXY_USER}" "{$NF_PROXY_PASSWORD}"
                hide_ip
                hide_via
                probe_resistance
""" + port_rule + """                acl {
                    allow 127.0.0.1/32
                    deny all
                }
            }
        }
        respond 404
    }
}
"""


def prepare_application(run):
    source = Path(__file__).resolve().parent / "hybrid_app"
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
    hybrid = getattr(args, "transport", "no-connect") in (
        "no-connect-hybrid", "no-connect-hybrid-asymmetric")
    port = free_port(udp=protocol == "h3", dual=hybrid and protocol == "h3")
    caddyfile = run / "Caddyfile"
    caddyfile.write_text(caddyfile_text(getattr(args, "forward_proxy_ports", ())))
    env = dict(os.environ, NF_PROTOCOL=protocol, NF_PORT=str(port), NF_CERT=str(run / "server.crt"),
               NF_CERT_KEY=str(run / "server.key"), NF_PROXY_USER=user, NF_PROXY_PASSWORD=password,
               NF_ACCESS_LOG=str(run / "access.jsonl"),
               XDG_DATA_HOME=str(run / "caddy-data"), XDG_CONFIG_HOME=str(run / "caddy-config"))
    adapted = subprocess.run([str(args.caddy), "adapt", "--config", str(caddyfile), "--adapter", "caddyfile"],
                             env=env, text=True, capture_output=True)
    require(adapted.returncode == 0, "combined Caddyfile adaptation failed")
    config = json.loads(adapted.stdout)
    servers = config["apps"]["http"]["servers"]
    require(len(servers) == 1, "fixture must contain one combined Caddy server")
    server = next(iter(servers.values()))
    require(server["listen"] == [f"127.0.0.1:{port}"], "fixture listener escaped loopback")
    require(server["protocols"] == [protocol], "fixture protocol is not strict")
    handlers = [item for route in server["routes"] for item in route.get("handle", [])]
    while handlers:
        item = handlers.pop()
        if item.get("handler") == "naivefox_transport":
            item["stats_path"] = str(run / "server-stats.json")
            item["application_root"] = str(prepare_application(run))
        for route in item.get("routes", []):
            handlers.extend(route.get("handle", []))
    mutator = getattr(args, "server_mutator", None)
    if mutator is not None:
        mutator(server)
    if hybrid:
        server["protocols"] = ["h1", protocol]
    private_json(run / "caddy.json", config)
    process = Process([str(args.caddy), "run", "--config", str(run / "caddy.json")], run, "caddy", env)
    wait_until(lambda: socket_listeners(port, udp=protocol == "h3"), "Caddy listener did not start", process)
    if protocol == "h3":
        require(socket_listeners(port) == hybrid,
                "H3 fixture TCP availability does not match explicit hybrid policy")
    else:
        require(not socket_listeners(port, udp=True), "strict H2 fixture unexpectedly listens on UDP")
    return process, port


def proxy_uri(protocol, proxy_port, user, password):
    require((user is None) == (password is None), "partial fixture credentials")
    scheme = "quic" if protocol == "h3" else "https"
    credentials = "" if user is None else quote(user, safe="") + ":" + quote(password, safe="") + "@"
    return f"{scheme}://{credentials}localhost:{proxy_port}"


def client_config(protocol, proxy_port, transport, user, password, ports, connections,
                  classic_preamble="off"):
    config = {"listen": [f"socks://127.0.0.1:{ports['socks']}", f"http://127.0.0.1:{ports['http']}"],
              "proxy": proxy_uri(protocol, proxy_port, user, password), "transport": transport,
              "host-resolver-rules": "MAP localhost 127.0.0.1", "max-connections": connections, "log": ""}
    if classic_preamble == "off":
        config["preamble"] = {"mode": "off"}
    return config


def start_client(args, run, name, protocol, proxy_port, transport, user, password, connections, trusted=True):
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
        config = client_config(protocol, proxy_port, transport, user, password,
                               {"socks": socks_port, "http": http_port}, connections,
                               getattr(args, "classic_preamble", "off"))
    else:
        config = copy.deepcopy(baseline)
        config["transport"] = transport
    if getattr(args, "omit_transport", False):
        require(transport == "classic", "only the default classic fixture may omit transport")
        config.pop("transport", None)
    mapped_credentials = getattr(args, "proxy_credentials_by_listener", None)
    if mapped_credentials is not None:
        require(len(mapped_credentials) == len(config["listen"]), "fixture proxy mapping differs from listeners")
        config["proxy"] = [proxy_uri(protocol, proxy_port, *credentials) for credentials in mapped_credentials]
    config_path = Path(getattr(args, "client_config_path", directory / "config.json"))
    private_json(config_path, config)
    env = {key: value for key, value in os.environ.items()
           if key not in {"NAIVEFOX_PROFILE", "NAIVEFOX_PROXY_USER", "NAIVEFOX_PROXY_PASS", "SSL_CERT_FILE",
                          "SSLKEYLOGFILE", "MOZ_LOG", "MOZ_LOG_FILE", "LD_PRELOAD"}}
    env.update(LD_LIBRARY_PATH=str(args.runtime.parent), TMPDIR=str(directory),
               MOZ_CRASHREPORTER_DISABLE="1")
    if trusted:
        env["SSL_CERT_FILE"] = str(run / "ca.crt")
    factory = getattr(args, "client_factory", None)
    if factory is not None:
        return factory(args, directory, config, env, {"socks": socks_port, "http": http_port})
    process = Process([str(args.runtime), str(config_path)], directory, "client", env)
    def ready():
        text = process.log_path.read_text(errors="replace")
        return f"SOCKS5 listening on 127.0.0.1:{socks_port}" in text and f"HTTP CONNECT listening on 127.0.0.1:{http_port}" in text
    wait_until(ready, "client listeners did not start", process)
    return process, {"socks": socks_port, "http": http_port}


def open_tunnel(ports, listener, target_port, host="localhost", rejected=False, allow_early_eof=False, timeout=40):
    sock = socket.create_connection(("127.0.0.1", ports[listener]), timeout=min(20, timeout))
    sock.settimeout(timeout)
    try:
        if listener == "socks":
            sock.sendall(b"\x05\x01\x00")
            require(receive(sock, 2) == b"\x05\x00", "SOCKS negotiation failed")
            encoded = host.encode("ascii")
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(encoded)]) + encoded + struct.pack("!H", target_port))
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
            sock.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header.extend(receive(sock, 1))
                require(len(header) <= 16384, "HTTP CONNECT reply exceeds bound")
            success = bytes(header).split(b" ", 2)[1] == b"200"
        if rejected and success and allow_early_eof:
            # Ordinary forwardproxy may send CONNECT 200 before applying ACL.
            # A policy refusal must then close promptly without any target data;
            # callers also verify the forbidden target's accept count stays zero.
            sock.settimeout(5)
            require(not sock.recv(1), "policy-denied tunnel returned target bytes")
            success = False
        require(success != rejected, "unexpected local CONNECT success" if rejected else "local CONNECT failed")
        if rejected:
            sock.close()
            return None
        return sock
    except BaseException:
        sock.close()
        raise


def reject_policy(ports, listener, target_port, host="localhost"):
    return open_tunnel(ports, listener, target_port, host=host,
                       rejected=True, allow_early_eof=True)


def download(ports, listener, target_port, length=1024 * 1024, slow=False, host="localhost"):
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
        require(received == length and digest.digest() == expected,
                f"download integrity or half-close failed ({received}/{length} bytes; "
                f"sha256={digest.hexdigest()}, expected={expected.hex()})")


def upload(ports, listener, target_port, length=1024 * 1024, host="localhost"):
    with open_tunnel(ports, listener, target_port, host=host) as sock:
        sock.sendall(b"U" + struct.pack("!I", length))
        send_payload(sock, length)
        sock.shutdown(socket.SHUT_WR)
        result = receive(sock, 40)
        require(not sock.recv(1), "upload acknowledgement did not reach EOF")
        require(result == struct.pack("!Q", length) + payload_digest(length), "upload integrity or half-close failed")


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
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh" if os.name == "nt" else "ii", 1, 0))


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
                require(receive(sock, len(payload)) == payload,
                        "concurrent logical stream echo mismatch")
                sock.shutdown(socket.SHUT_WR)
                require(not sock.recv(1), "concurrent logical stream did not half-close")
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
        cause = next((error for error in failures if not isinstance(error, threading.BrokenBarrierError)), failures[0])
        raise RuntimeError("concurrent logical stream gate failed") from cause
    require(peak == count, "concurrent OPEN gate never reached the requested peak")


def auth_partition_streams(ports, target_port):
    first_payload = b"authenticated-carrier-first"
    second_payload = b"authenticated-carrier-second"
    with open_tunnel(ports, "socks", target_port) as first:
        first.sendall(b"E" + first_payload)
        require(receive(first, len(first_payload)) == first_payload, "valid carrier did not authenticate")
        open_tunnel(ports, "http", target_port, rejected=True)
        first.sendall(second_payload)
        require(receive(first, len(second_payload)) == second_payload, "bad credentials poisoned a valid carrier")
        with open_tunnel(ports, "socks", target_port) as second:
            second.sendall(b"E" + second_payload)
            require(receive(second, len(second_payload)) == second_payload, "valid credentials failed after rejection")
            second.shutdown(socket.SHUT_WR)
            require(not second.recv(1), "second authenticated stream did not drain")
        first.shutdown(socket.SHUT_WR)
        require(not first.recv(1), "first authenticated stream did not drain")


def target_variety(ports, first_port, second_port):
    for listener in ("socks", "http"):
        download(ports, listener, first_port, 32768, host="127.0.0.1")
        download(ports, listener, second_port, 32768, host="localhost")


def reject_credentials(args, run, protocol, proxy_port, transport, user, password,
                       target_port, processes):
    for name, rejected_user, rejected_password, trusted in (
        ("wrong-user", user + "-wrong", password, True),
        ("wrong-password", user, password + "-wrong", True),
        ("missing-credentials", None, None, True),
        ("untrusted", user, password, False),
    ):
        client, ports = start_client(args, run, f"{transport}-{name}", protocol, proxy_port,
                                     transport, rejected_user, rejected_password, 2, trusted=trusted)
        processes.append(client)
        for listener in ("socks", "http"):
            open_tunnel(ports, listener, target_port, rejected=True)
        client.exited_cleanly()


def check_port_policy(args, run, protocol, allowed_target, blocked_target, user, password):
    directory = run / "port-policy"
    directory.mkdir(mode=0o700)
    issue_certificates(directory)
    inputs = copy.copy(args)
    inputs.forward_proxy_ports = [allowed_target.server_address[1]]
    processes = []
    before = blocked_target.accepted_connections
    try:
        caddy, proxy_port = start_caddy(inputs, directory, protocol,
                                        allowed_target.server_address[1], user, password)
        processes.append(caddy)
        for transport in (getattr(args, "transport", "no-connect"), "classic"):
            client, ports = start_client(inputs, directory, transport, protocol, proxy_port,
                                         transport, user, password, 4)
            processes.append(client)
            for listener in ("socks", "http"):
                download(ports, listener, allowed_target.server_address[1], 32768)
                reject_policy(ports, listener, blocked_target.server_address[1])
            client.exited_cleanly()
        require(blocked_target.accepted_connections == before,
                "forward-proxy ports policy dialed a denied destination")
        return {"allowed_transfers": 4, "denied_connections": 4, "shared_policy": True}
    finally:
        for process in reversed(processes):
            process.stop()


def check_shared_config(args, run, protocol, target_port, user, password):
    directory = run / "shared-config"
    directory.mkdir(mode=0o700)
    issue_certificates(directory)
    inputs = copy.copy(args)
    inputs.client_config_path = directory / "config.json"
    processes = []
    selected_transport = getattr(args, "transport", "no-connect")
    sequence = ("classic", selected_transport, "classic", selected_transport)
    baseline = None
    classic_requests = 0
    try:
        caddy, proxy_port = start_caddy(inputs, directory, protocol, target_port, user, password)
        processes.append(caddy)
        for index, transport in enumerate(sequence):
            previous = access_requests(directory)
            client, ports = start_client(inputs, directory, f"phase-{index}", protocol,
                                         proxy_port, transport, user, password, 2)
            processes.append(client)
            actual = json.loads(inputs.client_config_path.read_text())
            if hasattr(client, "executed_config"):
                require(client.executed_config() == actual, "device executed a different shared config")
            require(actual["transport"] == transport, "shared config selected the wrong transport")
            stable = {name: value for name, value in actual.items() if name != "transport"}
            if baseline is None:
                baseline = stable
                inputs.listener_ports = dict(ports)
                inputs.base_client_config = copy.deepcopy(actual)
            else:
                require(stable == baseline and ports == inputs.listener_ports,
                        "shared config changed fields other than transport")
            before = inputs.client_config_path.read_bytes()
            for listener in ("socks", "http"):
                download(ports, listener, target_port, 32768)
            client.exited_cleanly()
            require(inputs.client_config_path.read_bytes() == before,
                    "client mutated the shared config")
            if transport == "classic":
                classic_requests += 2
                wait_until(lambda: sum(request.get("method") == "CONNECT"
                                       for request in access_requests(directory)) == classic_requests,
                           "shared classic config did not use CONNECT")
            else:
                requests = access_requests(directory)[len(previous):]
                require(bool(requests) and not any(request.get("method") == "CONNECT" for request in requests),
                        "shared no-connect config emitted CONNECT or sent no requests")
                require(not any(name.lower() in {"authorization", "proxy-authorization"}
                                for request in requests for name in request.get("headers", {})),
                        "shared no-connect config leaked Basic HTTP headers")
        return {"sequence": list(sequence), "only_transport_changed": True,
                "listener_ports_reused": True, "successful_transfers": 8}
    finally:
        for process in reversed(processes):
            process.stop()


def check_auth_partition(args, run, protocol, target, user, password):
    directory = run / "auth-partition"
    directory.mkdir(mode=0o700)
    issue_certificates(directory)
    inputs = copy.copy(args)
    inputs.proxy_credentials_by_listener = [(user, password), (user, password + "-wrong")]
    processes = []
    before = target.accepted_connections
    try:
        caddy, proxy_port = start_caddy(inputs, directory, protocol,
                                        target.server_address[1], user, password)
        processes.append(caddy)
        client, ports = start_client(inputs, directory, "client", protocol, proxy_port,
                                     getattr(args, "transport", "no-connect"), user, password, 3)
        processes.append(client)
        auth_partition_streams(ports, target.server_address[1])
        client.exited_cleanly()
        require(target.accepted_connections == before + 2,
                "bad credentials reused an authenticated carrier or reached the target")
        require(not any(request.get("method") == "CONNECT" for request in access_requests(directory)),
                "auth partition gate emitted outer CONNECT")
        return {"one_process": True, "shared_endpoint": True, "distinct_credentials": True,
                "bad_credentials_rejected": True, "valid_target_connections": 2}
    finally:
        for process in reversed(processes):
            process.stop()


def exercise(ports, target_port, label, parallel_batches=1, idle_seconds=2):
    for listener in ("socks", "http"):
        try:
            download(ports, listener, target_port, slow=True)
            print(f"PASS {label} {listener}: 1MiB slow download and half-close", flush=True)
            upload(ports, listener, target_port)
            print(f"PASS {label} {listener}: 1MiB slow-target upload and half-close", flush=True)
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"{label} {listener} transfer: {error}") from error
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for batch in range(parallel_batches):
            futures = [pool.submit(download if index % 2 == 0 else upload, ports,
                                   "socks" if index < 2 else "http", target_port, 256 * 1024)
                       for index in range(4)]
            for index, future in enumerate(futures):
                try:
                    future.result(timeout=60)
                except (OSError, RuntimeError) as error:
                    raise RuntimeError(f"{label} parallel batch {batch} transfer {index}: {error}") from error
    for listener in ("socks", "http"):
        echo_wake(ports, listener, target_port, idle_seconds if listener == "socks" else 2)


def access_requests(run):
    path = run / "access.jsonl"
    if not path.exists():
        return []
    return [json.loads(line).get("request", {}) for line in path.read_text().splitlines() if line.strip()]


def check_smoke_requests(requests, protocol, hybrid, classic=False):
    expected_protocol = "HTTP/3.0" if protocol == "h3" else "HTTP/2.0"
    connects = [item for item in requests if item.get("method") == "CONNECT"]
    require(requests and (len(connects) == 2 if classic else not connects),
            "smoke request methods did not match the selected transport")
    websocket = []
    for item in requests:
        realtime = item.get("uri", "").split("?", 1)[0] == "/api/realtime"
        if realtime:
            websocket.append(item)
        expected = "HTTP/1.1" if hybrid and not classic and realtime else expected_protocol
        require(item.get("proto") == expected,
                "smoke startup, WebSocket or classic CONNECT used the wrong protocol")
        if not classic:
            require(not any(name.lower() in {"authorization", "proxy-authorization"}
                            for name in item.get("headers", {})),
                    "application smoke exposed Basic credentials in origin headers")
    require(len(websocket) == (1 if hybrid and not classic else 0),
            "smoke selected the wrong WebSocket lifecycle")


def check_ws_capacity_policy(stats, transport, complete=False):
    capacities = stats.get("ws_cell_capacities", {})
    incoming = {int(key.split()[1]) for key in capacities if key.startswith("in ")}
    outgoing = {int(key.split()[1]) for key in capacities if key.startswith("out ")}
    if transport == "no-connect-hybrid-asymmetric":
        require(incoming and incoming <= {512, 4096, 16384, 131072},
                "asymmetric hybrid used an invalid client-to-server capacity")
        require(outgoing and outgoing <= {512, 8192, 65536, 262144},
                "asymmetric hybrid used an invalid server-to-client capacity")
        require((incoming - {512}) and (outgoing - {512}),
                "asymmetric hybrid never used directional activity capacity")
        if complete:
            require({4096, 16384, 131072} <= incoming and
                    {8192, 65536, 262144} <= outgoing,
                    "complete asymmetric gate did not exercise every directional state")
            activities = stats.get("ws_activities", {})
            require(all(activities.get(f"out {state}", 0) > 0
                        for state in ("interactive", "download", "upload", "mixed")),
                    "complete asymmetric gate missed a server activity state")
    else:
        require(incoming and outgoing and incoming <= {512, 65536, 262144} and
                outgoing <= {512, 65536, 262144},
                "generic hybrid capacity policy changed")


def run_smoke_protocol(args, base, protocol):
    transport = getattr(args, "transport", "no-connect")
    hybrid = transport in ("no-connect-hybrid", "no-connect-hybrid-asymmetric")
    run = base / protocol
    run.mkdir(mode=0o700)
    issue_certificates(run)
    target = TargetServer()
    user, password = fixture_credentials()
    processes = []
    try:
        caddy, proxy_port = start_caddy(args, run, protocol, target.server_address[1], user, password)
        processes.append(caddy)
        candidate, ports = start_client(args, run, "candidate", protocol, proxy_port,
                                        transport, user, password, 6)
        processes.append(candidate)
        download(ports, "socks", target.server_address[1], 65536)
        if hybrid:
            wait_until(lambda: "No-connect hybrid websocket ready startup=20" in
                       candidate.log_path.read_text(errors="replace"),
                       "hybrid smoke never completed the WebSocket startup milestone", candidate, timeout=60)
        upload(ports, "socks", target.server_address[1], 65536)
        download(ports, "http", target.server_address[1], 65536)
        upload(ports, "http", target.server_address[1], 65536)
        for listener in ("socks", "http"):
            echo_wake(ports, listener, target.server_address[1], idle_seconds=0.05)
        candidate.exited_cleanly()
        requests = access_requests(run)
        check_smoke_requests(requests, protocol, hybrid)

        default_args = copy.copy(args)
        default_args.omit_transport = True
        default_args.classic_preamble = "default"
        classic, classic_ports = start_client(default_args, run, "default-classic", protocol,
                                               proxy_port, "classic", user, password, 2)
        processes.append(classic)
        default_config = json.loads((run / "default-classic/config.json").read_text())
        require("transport" not in default_config, "classic control did not use the absent transport default")
        if hasattr(classic, "executed_config"):
            require(classic.executed_config() == default_config, "device executed a different default config")
        for listener in ("socks", "http"):
            download(classic_ports, listener, target.server_address[1], 65536)
        classic.exited_cleanly()
        check_smoke_requests(access_requests(run)[len(requests):], protocol, hybrid, classic=True)
        caddy.stop()
        stats = json.loads((run / "server-stats.json").read_text())
        require(stats.get("connect") == 2 and stats.get("opens") == 6,
                "smoke transport/default selections did not open exactly the expected streams")
        require(not target.failures and target.accepted_connections == 8,
                "smoke target detected failed, truncated or extra streams")
        if hybrid:
            require(stats.get("ws_opened", 0) == 1 and stats.get("ws_messages_in", 0) >= 1 and
                    stats.get("ws_messages_out", 0) >= 1,
                    "hybrid smoke did not exchange bidirectional WebSocket cells")
            expected_subprotocol = ("nfc1.hybrid.a1" if transport == "no-connect-hybrid-asymmetric"
                                    else "nfc1.hybrid.v1")
            require(stats.get("ws_subprotocols") == {expected_subprotocol: 1},
                    "hybrid smoke selected the wrong shaping subprotocol")
            check_ws_capacity_policy(stats, transport)
        else:
            require(stats.get("ws_opened", 0) == 0, "finite HTTP smoke unexpectedly opened WebSocket")
        expected_protocol = "HTTP/3.0" if protocol == "h3" else "HTTP/2.0"
        expected_protocols = {expected_protocol, "HTTP/1.1"} if hybrid else {expected_protocol}
        require(set(stats["protocols"]) == expected_protocols,
                "smoke negotiated an unexpected outer protocol")
        summary = {"protocol": protocol, "transport": transport, "status": "PASS", "smoke": True,
                   "frontends": ["socks", "http"], "candidate_streams": 6, "default_classic_connects": 2,
                   "download_bytes": 131072, "upload_bytes": 131072,
                   "idle_echo_bytes_per_direction": 16384, "idle_seconds": 0.05,
                   "startup_milestone": 20 if hybrid else None,
                   "ws_opened": stats.get("ws_opened", 0), "no_connect_outer_connects": 0,
                   "graceful_exit": True, "default_transport_unchanged": True}
        private_json(run / "result.json", summary)
        print(f"PASS {protocol} {transport} smoke: both listeners, bytes, half-close, idle, startup, default classic, shutdown", flush=True)
        return summary
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


def run_protocol(args, base, protocol):
    transport = getattr(args, "transport", "no-connect")
    hybrid = transport in ("no-connect-hybrid", "no-connect-hybrid-asymmetric")
    run = base / protocol
    run.mkdir(mode=0o700)
    issue_certificates(run)
    target = TargetServer()
    second_target = TargetServer()
    denied_target = TargetServer("127.0.0.2")
    target_port = target.server_address[1]
    user, password = fixture_credentials()
    processes = []
    try:
        caddy, proxy_port = start_caddy(args, run, protocol, target_port, user, password)
        processes.append(caddy)
        batches = getattr(args, "parallel_batches", 1)
        ordinary_connections = 6 + 4 * batches
        variety_connections = 4
        policy_refusals = 2
        concurrent_connections = 40
        candidate, candidate_ports = start_client(
            args, run, "no-connect", protocol, proxy_port, transport, user, password,
            ordinary_connections + variety_connections + policy_refusals + concurrent_connections + 1)
        processes.append(candidate)
        classic, classic_ports = start_client(
            args, run, "classic", protocol, proxy_port, "classic", user, password,
            ordinary_connections + variety_connections + policy_refusals)
        processes.append(classic)
        exercise(candidate_ports, target_port, f"{protocol} {transport}", batches,
                 getattr(args, "idle_seconds", 2))
        target_variety(candidate_ports, target_port, second_target.server_address[1])
        concurrent_open_streams(candidate_ports, target_port, concurrent_connections)
        print(f"PASS {protocol} no-connect: 40 simultaneously open logical streams", flush=True)
        cancel_stream(candidate_ports, target_port)
        for listener in ("socks", "http"):
            reject_policy(candidate_ports, listener, denied_target.server_address[1], host="127.0.0.2")
        candidate.exited_cleanly()
        reject_credentials(args, run, protocol, proxy_port, transport, user, password,
                           target_port, processes)
        requests = access_requests(run)
        require(not any(item.get("method") == "CONNECT" for item in requests),
                "no-connect emitted an outer CONNECT")
        require(not any(name.lower() in {"authorization", "proxy-authorization"}
                        for item in requests for name in item.get("headers", {})),
                "no-connect exposed Basic credentials in origin HTTP headers")
        exercise(classic_ports, target_port, f"{protocol} classic", batches)
        target_variety(classic_ports, target_port, second_target.server_address[1])
        for listener in ("socks", "http"):
            reject_policy(classic_ports, listener, denied_target.server_address[1], host="127.0.0.2")
        classic.exited_cleanly()
        reject_credentials(args, run, protocol, proxy_port, "classic", user, password,
                           target_port, processes)
        require(denied_target.accepted_connections == 0,
                "forward-proxy ACL dialed a denied loopback address")
        caddy.stop()
        stats_path = run / "server-stats.json"
        require(stats_path.exists(), "server did not write protocol counters")
        stats = json.loads(stats_path.read_text())
        require(stats["connect"] >= ordinary_connections + variety_connections + policy_refusals,
                "classic did not traverse the shared forward proxy")
        require(stats["opens"] >= ordinary_connections + variety_connections + concurrent_connections,
                "no-connect did not open expected logical streams")
        require(stats["rejected"] >= 1, "bad Basic credentials were not rejected")
        if hybrid:
            require(stats.get("ws_opened", 0) >= 1, "hybrid never established WebSocket")
            require(stats.get("ws_messages_in", 0) >= 1 and stats.get("ws_messages_out", 0) >= 1,
                    "hybrid did not exchange bidirectional WebSocket cells")
            expected_subprotocol = ("nfc1.hybrid.a1" if transport == "no-connect-hybrid-asymmetric"
                                    else "nfc1.hybrid.v1")
            require(set(stats.get("ws_subprotocols", {})) == {expected_subprotocol},
                    "hybrid selected the wrong shaping subprotocol")
            check_ws_capacity_policy(stats, transport, complete=True)
            if getattr(args, "idle_seconds", 2) >= 27:
                require(stats.get("idle_heartbeats", 0) >= 1,
                        "hybrid long idle did not exercise application heartbeat")
        else:
            require(stats["idle_started"] >= 1, "no-connect idle state was not exercised")
            require(stats["idle_completed"] >= 1, "no-connect idle poll never completed")
        peers = stats.get("peers", [])
        require(sum(peer.get("reset", 0) for peer in peers) >= 1,
                "abrupt local cancellation did not reset its logical stream")
        peaks = [peer.get("peak_streams", 0) for peer in peers]
        require(len(peers) >= 2 and sum(peaks) >= concurrent_connections,
                "concurrent streams did not use additional carrier sessions")
        require(max(peaks, default=0) <= 32, "one carrier exceeded its stream bound")
        expected_protocol = "HTTP/3.0" if protocol == "h3" else "HTTP/2.0"
        expected_protocols = {expected_protocol, "HTTP/1.1"} if hybrid else {expected_protocol}
        require(set(stats["protocols"]) == expected_protocols, "carrier negotiated an unexpected outer protocol")
        port_policy = check_port_policy(args, run, protocol, target, second_target, user, password)
        shared_config = check_shared_config(args, run, protocol, target_port, user, password)
        auth_partition = check_auth_partition(args, run, protocol, target, user, password)
        require(not target.failures and not second_target.failures,
                "target detected a truncated or failed data stream")
        summary = {"protocol": protocol, "transport": transport, "same_caddy_process": True,
                   "strict_udp_only": protocol == "h3" and not hybrid,
                   "websocket_tcp": hybrid, "ws_opened": stats.get("ws_opened", 0),
                   "no_connect_outer_connects": 0, "classic_connects": stats["connect"],
                   "classic_preamble": getattr(args, "classic_preamble", "off"), "parallel_batches": batches,
                   "logical_opens": stats["opens"], "idle_started": stats["idle_started"],
                   "idle_completed": stats["idle_completed"], "concurrent_open_streams": concurrent_connections,
                   "carrier_sessions": len(peers), "peak_streams_per_carrier": peaks,
                   "shared_basic_auth": True, "credential_rejection_cases_per_transport": 3,
                   "credential_rejection_frontends": ["socks", "http"],
                   "unlisted_target_hosts": ["localhost", "127.0.0.1"],
                   "unlisted_target_port_count": 2, "shared_acl_refusals": 4,
                   "optional_forward_proxy_ports": port_policy, "shared_config_switch": shared_config,
                   "carrier_auth_partition": auth_partition,
                   "status": "PASS"}
        private_json(run / "result.json", summary)
        print(f"PASS {protocol}: shared Basic auth/policy, classic/no-connect, both listeners, integrity, half-close, concurrency, idle, TLS", flush=True)
        return summary
    finally:
        for process in reversed(processes):
            process.stop()
        denied_target.close()
        second_target.close()
        target.close()


def main():
    parser = argparse.ArgumentParser(description="Validate both native transports against one combined loopback Caddy.")
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--transport", choices=("no-connect", "no-connect-hybrid",
                                                  "no-connect-hybrid-asymmetric"),
                        default="no-connect")
    parser.add_argument("--smoke", action="store_true", help="bounded basic byte/lifecycle gate without the full concurrency matrix")
    parser.add_argument("--work-dir", type=Path, help="private artifact parent below objdir")
    parser.add_argument("--idle-seconds", type=int, choices=range(2, 31), default=2, metavar="2..30")
    parser.add_argument("--classic-preamble", choices=("off", "default"), default="off")
    parser.add_argument("--parallel-batches", type=int, choices=range(1, 129), default=1, metavar="1..128")
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    root = (args.work_dir or args.objdir / "naivefox-fixture").resolve()
    require(root.is_relative_to(args.objdir), "work directory must stay below objdir")
    root.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    run = Path(tempfile.mkdtemp(prefix="no-connect-", dir=root))
    try:
        modules = subprocess.check_output([str(args.caddy), "list-modules"], text=True)
        for module in ("http.handlers.forward_proxy", "http.handlers.naivefox_transport"):
            require(module in modules.splitlines(), "combined Caddy module is missing")
        protocol_runner = run_smoke_protocol if args.smoke else run_protocol
        results = [protocol_runner(args, run, protocol) for protocol in
                   (("h2", "h3") if args.protocol == "both" else (args.protocol,))]
        private_json(run / "result.json", {"status": "PASS", "targets": results})
        print(f"Private fixture and sanitized result: {run}")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}. Private diagnostics: {run}", flush=True)
        return 1
    finally:
        os.umask(previous_umask)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
