#!/usr/bin/env python3
"""Export only aggregate finite-exchange counters, never runtime log contents."""

import argparse
import json
import os
import re
from pathlib import Path

ARMS = tuple(
    f"h2-finite-{mode}{listener}"
    for mode in (
        "",
        "read-through-",
        "both-read-through-",
        "both-read-through-budgeted-",
    )
    for listener in ("socks", "http-connect")
)
PREFIX = r"^(?:\[[^\]\r\n]+\] )?Connection (\d+) finite-exchanges "
READY = re.compile(
    PREFIX + r"ready=1 block-bytes=65536 upload-window=2 download-window=4$", re.M
)
CLOSED = re.compile(
    PREFIX + r"closed=1 uploads=(\d+) downloads=(\d+) "
    r"upload-bytes=(\d+) download-bytes=(\d+) uploads-started=(\d+) "
    r"downloads-started=(\d+) full-download-bodies=(\d+)$",
    re.M,
)
FIELDS = (
    "completed_upload_responses",
    "completed_download_responses",
    "upload_body_bytes_started",
    "download_body_bytes_delivered",
    "started_upload_requests",
    "started_download_requests",
    "full_download_bodies",
)


def extract_counts(arm, log_text):
    if arm not in ARMS:
        raise ValueError("unsupported finite counter arm")
    ready = READY.findall(log_text)
    closed = CLOSED.findall(log_text)
    if (
        not ready
        or len(ready) != len(set(ready))
        or sorted(ready) != sorted(row[0] for row in closed)
        or log_text.count("finite-exchanges closed=") != len(closed)
        or log_text.count("finite-exchanges ready=") != len(ready)
    ):
        raise ValueError("finite counters lack unique matching terminal evidence")
    totals = dict.fromkeys(FIELDS, 0)
    for row in closed:
        values = tuple(int(value) for value in row[1:])
        up, down, up_bytes, down_bytes, up_started, down_started, full = values
        if (
            any(value > (1 << 64) - 1 for value in values)
            or up > up_started
            or down > down_started
            or full > down
            or up_bytes > 65536 * up_started
            or down_bytes > 65536 * down_started
        ):
            raise ValueError("inconsistent finite exchange counters")
        for field, value in zip(FIELDS, values, strict=True):
            totals[field] += value
    return {
        "schema": 1,
        "arm": arm,
        "scope": "whole product session including post-capture shutdown; excludes open/close requests",
        "connection_count": len(ready),
        "counts": totals,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = extract_counts(args.arm, args.log.read_text(encoding="utf-8"))
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
