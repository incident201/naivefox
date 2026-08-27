#!/usr/bin/env python3

import argparse
import json
import time
from urllib.parse import parse_qs, urlsplit


def validate_records(lines, *, completion, port):
    expected_host = f"localhost:{port}"
    completion_matches = []
    request_count = 0
    for line_number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"inner H2 access log line {line_number} is not JSON"
            ) from error
        request = record.get("request")
        if not isinstance(request, dict) or request.get("host") != expected_host:
            continue
        request_count += 1
        if request.get("proto") != "HTTP/2.0":
            raise ValueError(
                f"inner request used unexpected protocol {request.get('proto')!r}"
            )
        uri = urlsplit(request.get("uri", ""))
        if uri.path != "/camouflage/complete":
            continue
        if parse_qs(uri.query) != {"token": [completion]}:
            continue
        completion_matches.append((request, record))

    if request_count == 0:
        raise ValueError("inner H2 access-log slice contains no measured requests")
    if len(completion_matches) != 1:
        raise ValueError(
            "inner H2 access-log slice must contain exactly one matching "
            f"completion request, found {len(completion_matches)}"
        )
    request, record = completion_matches[0]
    if request.get("method") != "POST":
        raise ValueError("inner H2 completion request is not POST")
    if record.get("status") != 204:
        raise ValueError(
            f"inner H2 completion status is not 204: {record.get('status')!r}"
        )


def validate_file(path, *, offset, completion, port):
    with open(path, "rb") as stream:
        stream.seek(offset)
        payload = stream.read()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("inner H2 access-log slice is not UTF-8") from error
    lines = [line for line in text.splitlines() if line]
    validate_records(lines, completion=completion, port=port)


def validate_file_with_wait(path, *, offset, completion, port, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            validate_file(path, offset=offset, completion=completion, port=port)
            return
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--access-log", required=True)
    parser.add_argument("--offset", required=True, type=int)
    parser.add_argument("--completion", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()
    try:
        validate_file_with_wait(
            args.access_log,
            offset=args.offset,
            completion=args.completion,
            port=args.port,
            wait_seconds=args.wait_seconds,
        )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
