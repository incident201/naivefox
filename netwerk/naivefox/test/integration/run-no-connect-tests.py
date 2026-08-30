#!/usr/bin/env python3

import argparse
import concurrent.futures
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

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Target)
        self.failures = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


def free_port(udp=False):
    with socket.socket(type=socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def start_caddy(args, run, protocol, target_port, key, user, password):
    port = free_port(udp=protocol == "h3")
    caddyfile = run / "Caddyfile"
    caddyfile.write_text("""{
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
            key {$NF_TRANSPORT_KEY}
            allowed_targets {$NF_TARGET}
            profile continuous-bulk-pipeline
        }
        forward_proxy {
            basic_auth {$NF_CLASSIC_USER} {$NF_CLASSIC_PASSWORD}
            hosts localhost
            ports {$NF_TARGET_PORT}
            acl {
                allow 127.0.0.1/32
                deny all
            }
        }
        respond 404
    }
}
""")
    env = dict(os.environ, NF_PROTOCOL=protocol, NF_PORT=str(port), NF_CERT=str(run / "server.crt"),
               NF_CERT_KEY=str(run / "server.key"), NF_TRANSPORT_KEY=key,
               NF_CLASSIC_USER=user, NF_CLASSIC_PASSWORD=password, NF_TARGET=f"localhost:{target_port}",
               NF_TARGET_PORT=str(target_port), NF_ACCESS_LOG=str(run / "access.jsonl"),
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
        for route in item.get("routes", []):
            handlers.extend(route.get("handle", []))
    mutator = getattr(args, "server_mutator", None)
    if mutator is not None:
        mutator(server)
    private_json(run / "caddy.json", config)
    process = Process([str(args.caddy), "run", "--config", str(run / "caddy.json")], run, "caddy", env)
    wait_until(lambda: socket_listeners(port, udp=protocol == "h3"), "Caddy listener did not start", process)
    if protocol == "h3":
        require(not socket_listeners(port), "strict H3 fixture unexpectedly listens on TCP")
    else:
        require(not socket_listeners(port, udp=True), "strict H2 fixture unexpectedly listens on UDP")
    return process, port


def start_client(args, run, name, protocol, proxy_port, transport, key, user, password, connections, trusted=True):
    directory = run / name
    directory.mkdir(mode=0o700)
    socks_port = free_port()
    http_port = free_port()
    while http_port == socks_port:
        http_port = free_port()
    scheme = "quic" if protocol == "h3" else "https"
    authority = f"localhost:{proxy_port}"
    config = {"listen": [f"socks://127.0.0.1:{socks_port}", f"http://127.0.0.1:{http_port}"],
              "proxy": f"{scheme}://{authority}", "transport": transport,
              "host-resolver-rules": "MAP localhost 127.0.0.1", "max-connections": connections, "log": ""}
    if transport == "no-connect":
        config["no-connect-key"] = key
    else:
        config["proxy"] = f"{scheme}://{user}:{password}@{authority}"
        config["preamble"] = {"mode": "off"}
    private_json(directory / "config.json", config)
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
    process = Process([str(args.runtime), str(directory / "config.json")], directory, "client", env)
    def ready():
        text = process.log_path.read_text(errors="replace")
        return f"SOCKS5 listening on 127.0.0.1:{socks_port}" in text and f"HTTP CONNECT listening on 127.0.0.1:{http_port}" in text
    wait_until(ready, "client listeners did not start", process)
    return process, {"socks": socks_port, "http": http_port}


def open_tunnel(ports, listener, target_port, host="localhost", rejected=False):
    sock = socket.create_connection(("127.0.0.1", ports[listener]), timeout=20)
    sock.settimeout(40)
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
        require(success != rejected, "unexpected local CONNECT success" if rejected else "local CONNECT failed")
        if rejected:
            sock.close()
            return None
        return sock
    except BaseException:
        sock.close()
        raise


def download(ports, listener, target_port, length=1024 * 1024, slow=False):
    with open_tunnel(ports, listener, target_port) as sock:
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
        require(received == length and digest.digest() == payload_digest(length), "download integrity or half-close failed")


def upload(ports, listener, target_port, length=1024 * 1024):
    with open_tunnel(ports, listener, target_port) as sock:
        sock.sendall(b"U" + struct.pack("!I", length))
        send_payload(sock, length)
        sock.shutdown(socket.SHUT_WR)
        result = receive(sock, 40)
        require(not sock.recv(1), "upload acknowledgement did not reach EOF")
        require(result == struct.pack("!Q", length) + payload_digest(length), "upload integrity or half-close failed")


def echo_wake(ports, listener, target_port):
    with open_tunnel(ports, listener, target_port) as sock:
        sock.sendall(b"E" + BLOCK[:4096])
        require(receive(sock, 4096) == BLOCK[:4096], "initial echo failed")
        time.sleep(2)
        sock.sendall(BLOCK[256:4352])
        require(receive(sock, 4096) == BLOCK[256:4352], "idle wake echo failed")
        sock.shutdown(socket.SHUT_WR)
        require(not sock.recv(1), "echo half-close did not drain")


def cancel_stream(ports, target_port):
    with open_tunnel(ports, "socks", target_port) as sock:
        sock.sendall(b"C")
        time.sleep(0.05)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh" if os.name == "nt" else "ii", 1, 0))


def exercise(ports, target_port, label):
    for listener in ("socks", "http"):
        try:
            download(ports, listener, target_port, slow=True)
            print(f"PASS {label} {listener}: 1MiB slow download and half-close", flush=True)
            upload(ports, listener, target_port)
            print(f"PASS {label} {listener}: 1MiB slow-target upload and half-close", flush=True)
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"{label} {listener} transfer: {error}") from error
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download if index % 2 == 0 else upload, ports,
                               "socks" if index < 2 else "http", target_port, 256 * 1024)
                   for index in range(4)]
        for future in futures:
            future.result(timeout=60)
    for listener in ("socks", "http"):
        echo_wake(ports, listener, target_port)


def access_requests(run):
    path = run / "access.jsonl"
    if not path.exists():
        return []
    return [json.loads(line).get("request", {}) for line in path.read_text().splitlines() if line.strip()]


def run_protocol(args, base, protocol):
    run = base / protocol
    run.mkdir(mode=0o700)
    issue_certificates(run)
    target = TargetServer()
    target_port = target.server_address[1]
    key, user, password = secrets.token_hex(32), secrets.token_hex(8), secrets.token_hex(24)
    processes = []
    try:
        caddy, proxy_port = start_caddy(args, run, protocol, target_port, key, user, password)
        processes.append(caddy)
        candidate, candidate_ports = start_client(args, run, "no-connect", protocol, proxy_port,
                                                  "no-connect", key, user, password, 13)
        processes.append(candidate)
        classic, classic_ports = start_client(args, run, "classic", protocol, proxy_port,
                                              "classic", key, user, password, 10)
        processes.append(classic)
        exercise(candidate_ports, target_port, f"{protocol} no-connect")
        cancel_stream(candidate_ports, target_port)
        for listener in ("socks", "http"):
            open_tunnel(candidate_ports, listener, target_port, host="127.0.0.1", rejected=True)
        candidate.exited_cleanly()
        for name, trusted, credential in (("bad-key", True, secrets.token_hex(32)), ("untrusted", False, key)):
            client, ports = start_client(args, run, name, protocol, proxy_port, "no-connect", credential,
                                         user, password, 1, trusted=trusted)
            processes.append(client)
            open_tunnel(ports, "socks", target_port, rejected=True)
            client.exited_cleanly()
        require(not any(item.get("method") == "CONNECT" for item in access_requests(run)),
                "no-connect emitted an outer CONNECT")
        exercise(classic_ports, target_port, f"{protocol} classic")
        classic.exited_cleanly()
        caddy.stop()
        stats_path = run / "server-stats.json"
        require(stats_path.exists(), "server did not write protocol counters")
        stats = json.loads(stats_path.read_text())
        require(stats["connect"] >= 10, "classic did not traverse the combined server's CONNECT handler")
        require(stats["opens"] >= 10, "no-connect did not open expected logical streams")
        require(stats["rejected"] >= 1, "wrong application key was not rejected")
        require(stats["idle_started"] >= 1, "no-connect idle state was not exercised")
        require(stats["idle_completed"] >= 1, "no-connect idle poll never completed")
        require(sum(peer.get("reset", 0) for peer in stats.get("peers", [])) >= 1,
                "abrupt local cancellation did not reset its logical stream")
        expected_protocol = "HTTP/3.0" if protocol == "h3" else "HTTP/2.0"
        require(set(stats["protocols"]) == {expected_protocol}, "carrier negotiated an unexpected outer protocol")
        require(not target.failures, "target detected a truncated or failed data stream")
        summary = {"protocol": protocol, "same_caddy_process": True, "strict_udp_only": protocol == "h3",
                   "no_connect_outer_connects": 0, "classic_connects": stats["connect"],
                   "logical_opens": stats["opens"], "idle_started": stats["idle_started"],
                   "idle_completed": stats["idle_completed"], "status": "PASS"}
        private_json(run / "result.json", summary)
        print(f"PASS {protocol}: classic/no-connect, both listeners, integrity, half-close, concurrency, idle, ACL/auth/trust", flush=True)
        return summary
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


def main():
    parser = argparse.ArgumentParser(description="Validate both native transports against one combined loopback Caddy.")
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    root = args.objdir / "naivefox-fixture"
    root.mkdir(exist_ok=True)
    previous_umask = os.umask(0o077)
    run = Path(tempfile.mkdtemp(prefix="no-connect-", dir=root))
    try:
        modules = subprocess.check_output([str(args.caddy), "list-modules"], text=True)
        for module in ("http.handlers.forward_proxy", "http.handlers.naivefox_transport"):
            require(module in modules.splitlines(), "combined Caddy module is missing")
        results = [run_protocol(args, run, protocol) for protocol in
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
