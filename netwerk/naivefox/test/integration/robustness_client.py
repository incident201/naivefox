#!/usr/bin/env python3

import argparse
import hashlib
import json
import socket
import time


def connect_socks(port, host, target_port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        raise RuntimeError("SOCKS method negotiation failed")
    encoded = host.encode("ascii")
    sock.sendall(
        b"\x05\x01\x00\x03"
        + bytes((len(encoded),))
        + encoded
        + target_port.to_bytes(2, "big")
    )
    reply = b""
    while len(reply) < 10:
        chunk = sock.recv(10 - len(reply))
        if not chunk:
            raise RuntimeError("SOCKS reply closed early")
        reply += chunk
    if reply[1] != 0:
        raise RuntimeError(f"SOCKS CONNECT failed with reply {reply[1]}")
    return sock


def slow_download(args):
    sock = connect_socks(args.socks_port, "localhost", args.target_port)
    request = (
        f"GET /large?size={args.size} HTTP/1.1\r\n"
        f"Host: localhost:{args.target_port}\r\nConnection: close\r\n\r\n"
    ).encode()
    sock.sendall(request)
    headers = bytearray()
    body_size = 0
    headers_done = False
    while True:
        try:
            chunk = sock.recv(4096)
        except TimeoutError as error:
            raise RuntimeError(
                f"slow download stalled after {body_size} body bytes"
            ) from error
        if not chunk:
            break
        if headers_done:
            body_size += len(chunk)
        else:
            headers.extend(chunk)
            split = headers.find(b"\r\n\r\n")
            if split >= 0:
                response_headers = bytes(headers[:split])
                if not response_headers.startswith(b"HTTP/1.1 200 "):
                    raise RuntimeError("slow download returned a non-200 response")
                expected_length = f"Content-Length: {args.size}".encode()
                if expected_length not in response_headers:
                    raise RuntimeError("slow download length header was unexpected")
                body_size += len(headers) - split - 4
                headers.clear()
                headers_done = True
            elif len(headers) > 16384:
                raise RuntimeError("slow download response headers are too large")
        time.sleep(0.0005)
    if not headers_done or body_size != args.size:
        raise RuntimeError(
            f"slow download size mismatch: expected {args.size}, got {body_size}"
        )


def stalled_upload(args):
    sock = connect_socks(args.socks_port, "localhost", args.target_port)
    header = (
        f"POST /slow-upload?ms=1 HTTP/1.1\r\n"
        f"Host: localhost:{args.target_port}\r\n"
        f"Content-Length: {args.size}\r\nConnection: close\r\n\r\n"
    ).encode()
    sock.sendall(header)
    payload = b"x" * 65536
    remaining = args.size
    while remaining:
        amount = min(len(payload), remaining)
        sock.sendall(payload[:amount])
        remaining -= amount
    sock.shutdown(socket.SHUT_WR)
    response = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
        if len(response) > 16384:
            raise RuntimeError("upload response was too large")
    split = response.find(b"\r\n\r\n")
    if split < 0 or not response.startswith(b"HTTP/1.1 200 "):
        raise RuntimeError("slow upload did not return HTTP 200")
    result = json.loads(response[split + 4 :])
    expected_digest = hashlib.sha256()
    remaining = args.size
    while remaining:
        amount = min(len(payload), remaining)
        expected_digest.update(payload[:amount])
        remaining -= amount
    expected_hash = expected_digest.hexdigest()
    if result != {"bytes": args.size, "sha256": expected_hash}:
        raise RuntimeError("slow upload integrity check failed")


def local_disconnect(args):
    sock = connect_socks(args.socks_port, "localhost", args.target_port)
    request = (
        f"GET /delay?ms=1500 HTTP/1.1\r\nHost: localhost:{args.target_port}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    sock.sendall(request)
    sock.close()


def half_close(args):
    sock = connect_socks(args.socks_port, "localhost", args.target_port)
    request = (
        f"GET /small HTTP/1.1\r\nHost: localhost:{args.target_port}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    sock.sendall(request)
    sock.shutdown(socket.SHUT_WR)
    response = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
    if b"naivefox-fixture-small" not in response:
        raise RuntimeError("half-close response was truncated")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "slow-download",
            "stalled-upload",
            "local-disconnect",
            "half-close",
        ],
    )
    parser.add_argument("--socks-port", type=int, required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--size", type=int, default=8 * 1024 * 1024)
    args = parser.parse_args()
    {
        "slow-download": slow_download,
        "stalled-upload": stalled_upload,
        "local-disconnect": local_disconnect,
        "half-close": half_close,
    }[args.mode](args)


if __name__ == "__main__":
    main()
