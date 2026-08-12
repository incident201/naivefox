#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
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


def pattern_bytes(offset, length):
    start = offset % len(PATTERN)
    data = PATTERN[start:] + PATTERN[:start]
    return (data * ((length + len(data) - 1) // len(data)))[:length]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    def send_bytes(self, status, body, content_type="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            offset = 0
            while offset < size:
                length = min(65536, size - offset)
                self.wfile.write(pattern_bytes(offset, length))
                offset += length
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
        else:
            self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != "/upload":
            self.send_error(404)
            return
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
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                self.send_error(400)
                return
            digest.update(chunk)
            remaining -= len(chunk)
        body = json.dumps(
            {"bytes": total, "sha256": digest.hexdigest()}, sort_keys=True
        ).encode() + b"\n"
        self.send_bytes(200, body, "application/json")


def serve(args):
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
    parser.add_argument("--ready-file", required=True)
    serve(parser.parse_args())


if __name__ == "__main__":
    main()

