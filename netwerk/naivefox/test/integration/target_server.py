#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SMALL_BODY = b"naivefox-fixture-small\n"
PATTERN = bytes(range(251))
MAX_BODY = 64 * 1024 * 1024
SVG_PREFIX = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><!--'
SVG_SUFFIX = b'--><rect width="8" height="8" fill="#476f9f"/></svg>'
COMPLETIONS = set()
COMPLETIONS_LOCK = threading.Lock()
COMPLETION_TOKEN = re.compile(r"^[0-9a-f]{32}$")
REQUEST_JOURNAL_LOCK = threading.Lock()


def configured_camouflage_asset_size(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        size = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1024 <= size <= 4 * 1024 * 1024:
        raise ValueError(f"{name} must be between 1024 and 4194304 bytes")
    return size


CAMOUFLAGE_STYLE_SIZE = configured_camouflage_asset_size(
    "NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE", 64 * 1024
)
CAMOUFLAGE_SCRIPT_SIZE = configured_camouflage_asset_size(
    "NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE", 128 * 1024
)


def sized_source_asset(size, prefix, filler):
    if len(prefix) > size:
        raise ValueError("source asset prefix exceeds its declared size")
    repetitions, padding = divmod(size - len(prefix), len(filler))
    return prefix + filler * repetitions + b" " * padding


CAMOUFLAGE_STYLE_CSS = sized_source_asset(
    CAMOUFLAGE_STYLE_SIZE,
    (
        b":root{color-scheme:light;background:#f4f6f8;color:#243447}"
        b"body{margin:0;font-family:system-ui,sans-serif}"
        b"main{max-width:72rem;margin:auto;padding:2rem}\n"
    ),
    b"/* controlled component stylesheet module */\n",
)
CAMOUFLAGE_APP_JS = sized_source_asset(
    CAMOUFLAGE_SCRIPT_SIZE,
    (
        b'(()=>{"use strict";document.documentElement.dataset.fixture='
        b'"controlled";})();\n'
    ),
    b"/* controlled browser application module */\n",
)
CAMOUFLAGE_STYLE_ETAG = (
    '"naivefox-style-' + hashlib.sha256(CAMOUFLAGE_STYLE_CSS).hexdigest() + '"'
)


def pattern_bytes(offset, length):
    start = offset % len(PATTERN)
    data = PATTERN[start:] + PATTERN[:start]
    return (data * ((length + len(data) - 1) // len(data)))[:length]


def write_completion(completion_dir, token):
    path = os.path.join(completion_dir, token)
    temporary = f"{path}.{threading.get_native_id()}.tmp"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, b"complete\n")
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    def send_bytes(
        self, status, body, content_type="application/octet-stream", headers=()
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def journal_cache_request(self, status):
        referer = self.headers.get("Referer", "")
        completion = ""
        if referer:
            completion = parse_qs(urlparse(referer).query).get("completion", [""])[0]
        entry = {
            "accept": self.headers.get("Accept", ""),
            "completion": completion if COMPLETION_TOKEN.fullmatch(completion) else "",
            "etag": CAMOUFLAGE_STYLE_ETAG,
            "host": self.headers.get("Host", ""),
            "if_none_match": self.headers.get("If-None-Match", ""),
            "listener": "https"
            if isinstance(self.connection, ssl.SSLSocket)
            else "http",
            "method": "GET",
            "path": "/camouflage/style.css",
            "priority": self.headers.get("Priority", ""),
            "referer": referer,
            "sec_fetch_dest": self.headers.get("Sec-Fetch-Dest", ""),
            "sec_fetch_mode": self.headers.get("Sec-Fetch-Mode", ""),
            "sec_fetch_site": self.headers.get("Sec-Fetch-Site", ""),
            "status": status,
        }
        encoded = (json.dumps(entry, sort_keys=True) + "\n").encode()
        with REQUEST_JOURNAL_LOCK:
            descriptor = os.open(
                self.request_journal,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)

    def send_camouflage_style(self):
        if_none_match = self.headers.get("If-None-Match", "")
        status = 304 if if_none_match.strip() == CAMOUFLAGE_STYLE_ETAG else 200
        self.journal_cache_request(status)
        self.send_response(status)
        self.send_header("ETag", CAMOUFLAGE_STYLE_ETAG)
        self.send_header("Cache-Control", "no-cache")
        if status == 200:
            self.send_header("Content-Type", "text/css")
            self.send_header("Content-Length", str(len(CAMOUFLAGE_STYLE_CSS)))
        self.end_headers()
        if status == 200:
            self.wfile.write(CAMOUFLAGE_STYLE_CSS)

    def send_pattern(self, status, size, content_type="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        offset = 0
        while offset < size:
            length = min(65536, size - offset)
            self.wfile.write(pattern_bytes(offset, length))
            offset += length

    def send_svg(self, size):
        size = max(size, len(SVG_PREFIX) + len(SVG_SUFFIX))
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        self.wfile.write(SVG_PREFIX)
        remaining = size - len(SVG_PREFIX) - len(SVG_SUFFIX)
        block = b"x" * 65536
        while remaining:
            length = min(remaining, len(block))
            self.wfile.write(block[:length])
            remaining -= length
        self.wfile.write(SVG_SUFFIX)

    def camouflage_page(self, query):
        scenario = query.get("scenario", ["browser_page"])[0]
        size = min(max(int(query.get("size", [262144])[0]), 1), 16 * 1024 * 1024)
        count = min(max(int(query.get("count", [4])[0]), 1), 16)
        idle_ms = min(max(int(query.get("idle_ms", [5000])[0]), 0), 120000)
        head = (
            "<!doctype html><meta charset=utf-8>"
            "<title>NaiveFox controlled browser workload</title>"
        )
        if scenario == "initial":
            body = '<img src="/camouflage/resource?size=16384">'
        elif scenario == "browser_page":
            body = (
                '<link rel="stylesheet" href="/camouflage/style.css">'
                '<script src="/camouflage/app.js"></script>'
                '<img src="/camouflage/resource?size=65536">'
                '<img src="/camouflage/resource?size=131072">'
                '<img src="/camouflage/resource?size=262144">'
                '<img src="/camouflage/api">'
            )
        elif scenario == "warm_css":
            body = '<link rel="stylesheet" href="/camouflage/style.css">'
        elif scenario == "sequential":
            body = """<script>
function get(){let x=new XMLHttpRequest();x.open('GET','/camouflage/api',false);x.send()}
function wait(ms){let end=performance.now()+ms;while(performance.now()<end){}}
get();wait(100);get();wait(500);get();wait(2000);get();
</script>"""
        elif scenario in ("burst", "concurrent"):
            body = "".join(
                f'<img src="/camouflage/resource?size={size}&item={index}">'
                for index in range(count)
            )
        elif scenario == "bulk_download":
            body = f'<img src="/camouflage/resource?size={size}">'
        elif scenario == "bulk_upload":
            body = f"""<script>
let x=new XMLHttpRequest();x.open('POST','/camouflage/upload',false);
x.setRequestHeader('Content-Type','application/octet-stream');x.send('x'.repeat({size}));
document.body.textContent=x.status;
</script>"""
        elif scenario == "bidirectional":
            body = f"""<script>
let x=new XMLHttpRequest();x.open('POST','/camouflage/upload',false);
x.setRequestHeader('Content-Type','application/octet-stream');x.send('x'.repeat({size}));
</script><img src="/camouflage/resource?size={size}">"""
        elif scenario == "idle":
            body = f"""<script>
function get(){{let x=new XMLHttpRequest();x.open('GET','/camouflage/api',false);x.send()}}
get();let end=performance.now()+{idle_ms};while(performance.now()<end){{}}get();
</script>"""
        else:
            return None
        completion = query.get("completion", [""])[0]
        if completion:
            if not COMPLETION_TOKEN.fullmatch(completion):
                return None
            body += f"""<script>
window.addEventListener('load',async()=>{{
await fetch('/camouflage/complete?token={completion}',{{method:'POST'}});
}});
</script>"""
        return (head + body).encode()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self.send_bytes(200, b"ready\n", "text/plain")
        elif parsed.path == "/small":
            self.send_bytes(200, SMALL_BODY, "text/plain")
        elif parsed.path == "/large":
            size = min(int(query.get("size", [4 * 1024 * 1024])[0]), MAX_BODY)
            if size < 0:
                self.send_error(400)
                return
            self.send_pattern(200, size)
        elif parsed.path == "/observer":
            size = min(int(query.get("size", [4 * 1024 * 1024])[0]), MAX_BODY)
            if size < 0:
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            block = b"naivefox-observer-traffic\n"
            remaining = size
            while remaining:
                data = (
                    block * ((min(remaining, 65536) + len(block) - 1) // len(block))
                )[: min(remaining, 65536)]
                self.wfile.write(data)
                remaining -= len(data)
        elif parsed.path == "/delay":
            delay_ms = min(max(int(query.get("ms", [250])[0]), 0), 10000)
            time.sleep(delay_ms / 1000)
            self.send_bytes(200, SMALL_BODY, "text/plain")
        elif parsed.path == "/early-close":
            count = min(max(int(query.get("after", [64])[0]), 1), 65536)
            self.send_response(200)
            self.send_header("Content-Length", str(count + 1024))
            self.end_headers()
            self.wfile.write(pattern_bytes(0, count))
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        elif parsed.path in ("/camouflage", "/camouflage/", "/camouflage/index.html"):
            body = self.camouflage_page(query)
            if body is None:
                self.send_error(400)
                return
            self.send_bytes(
                200,
                body,
                "text/html; charset=utf-8",
                (("Referrer-Policy", "strict-origin-when-cross-origin"),),
            )
        elif parsed.path == "/camouflage/style.css":
            self.send_camouflage_style()
        elif parsed.path == "/camouflage/app.js":
            self.send_bytes(200, CAMOUFLAGE_APP_JS, "application/javascript")
        elif parsed.path == "/camouflage/api":
            self.send_bytes(
                200, b'{"status":"ok","items":[1,2,3,4]}\n', "application/json"
            )
        elif parsed.path == "/camouflage/status":
            token = query.get("token", [""])[0]
            with COMPLETIONS_LOCK:
                complete = token in COMPLETIONS
            self.send_bytes(200 if complete else 202, b"\n", "text/plain")
        elif parsed.path == "/camouflage/resource":
            size = min(max(int(query.get("size", [65536])[0]), 1), MAX_BODY)
            self.send_svg(size)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/camouflage/complete":
            query = parse_qs(parsed.query)
            token = query.get("token", [""])[0]
            if not COMPLETION_TOKEN.fullmatch(token):
                self.send_error(400)
                return
            with COMPLETIONS_LOCK:
                COMPLETIONS.add(token)
            self.send_bytes(204, b"")
            self.wfile.flush()
            write_completion(self.completion_dir, token)
            return
        if parsed.path not in ("/upload", "/slow-upload", "/camouflage/upload"):
            self.send_error(404)
            return
        delay_ms = 0
        if parsed.path == "/slow-upload":
            query = parse_qs(parsed.query)
            delay_ms = min(max(int(query.get("ms", [1])[0]), 0), 100)
        try:
            remaining = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self.send_error(400)
            return
        if remaining < 0 or remaining > MAX_BODY:
            self.send_error(413)
            return
        total = remaining
        digest = hashlib.sha256()
        while remaining:
            chunk_size = 4096 if delay_ms else 65536
            chunk = self.rfile.read(min(chunk_size, remaining))
            if not chunk:
                self.send_error(400)
                return
            digest.update(chunk)
            remaining -= len(chunk)
            if delay_ms:
                time.sleep(delay_ms / 1000)
        body = (
            json.dumps(
                {"bytes": total, "sha256": digest.hexdigest()}, sort_keys=True
            ).encode()
            + b"\n"
        )
        self.send_bytes(200, body, "application/json")


def serve(args):
    Handler.completion_dir = args.completion_dir
    Handler.request_journal = args.request_journal
    httpd = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    httpsd = ThreadingHTTPServer(("127.0.0.1", args.https_port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    httpsd.socket = context.wrap_socket(httpsd.socket, server_side=True)
    ready = {
        "http_port": httpd.server_address[1],
        "https_port": httpsd.server_address[1],
    }
    temporary = args.ready_file + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(ready, stream, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.ready_file)
    threads = [
        threading.Thread(target=httpd.serve_forever, daemon=True),
        threading.Thread(target=httpsd.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
    signal.signal(signal.SIGINT, lambda *_args: stopped.set())
    stopped.wait()
    httpd.shutdown()
    httpsd.shutdown()
    httpd.server_close()
    httpsd.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-port", type=int, default=0)
    parser.add_argument("--https-port", type=int, default=0)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--completion-dir", required=True)
    parser.add_argument("--request-journal", required=True)
    parser.add_argument("--ready-file", required=True)
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
