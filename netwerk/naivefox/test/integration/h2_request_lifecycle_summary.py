#!/usr/bin/env python3

"""Sanitize Caddy access-log slices into a diagnostic-only H2 timeline.

Caddy logs after a handler returns. Subtracting its handler duration from the
log timestamp estimates request start; it is not a packet or Necko timestamp.
The result must never be used as passive camouflage feature input.
"""

import argparse
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROLES = ("firefox_a", "firefox_b", "socks", "http")
PAGE_PATHS = {
    "/camouflage/index.html": "root",
    "/camouflage/style.css": "stylesheet",
    "/camouflage/app.js": "script",
    "/camouflage/api": "api",
    "/camouflage/complete": "complete",
}
IMAGE_SIZES = {
    "65536": "image_small",
    "131072": "image_medium",
    "262144": "image_large",
}
PAGE_EVENTS = (
    "root",
    "stylesheet",
    "script",
    "image_small",
    "image_medium",
    "image_large",
    "api",
    "complete",
)


def access_records(lines):
    records = []
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"access-log line {number} is not JSON") from error
        if not isinstance(record, dict):
            raise ValueError("access-log record is not an object")
        logger = record.get("logger", "")
        if not isinstance(logger, str) or not logger.startswith("http.log.access"):
            continue
        if not isinstance(record.get("request"), dict):
            raise ValueError("access-log record has no request object")
        records.append(record)
    return records


def read_slice(path, offset):
    if offset < 0:
        raise ValueError("access-log offset is negative")
    with open(path, "rb") as stream:
        if stream.seek(0, 2) < offset:
            raise ValueError("access log was truncated after the sample marker")
        stream.seek(offset)
        payload = stream.read()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("access-log slice is not UTF-8") from error
    return access_records(line for line in text.splitlines() if line)


def request_event(record):
    if record["request"].get("proto") != "HTTP/2.0":
        raise ValueError("measured request did not use H2")
    end = record.get("ts")
    duration = record.get("duration")
    size = record.get("size")
    for value in (end, duration):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("access-log timing is not finite numeric data")
    if end <= 0 or duration < 0 or duration > end:
        raise ValueError("access-log timing is outside its valid range")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("access-log response size is invalid")
    return {"start": end - duration, "end": end, "duration": duration, "size": size}


def page_events(records, *, host, completion, root_only=False):
    found = {}
    for record in records:
        request = record["request"]
        if request.get("host") != host or request.get("method") == "CONNECT":
            continue
        uri = urlsplit(request.get("uri", ""))
        query = parse_qs(uri.query)
        name = PAGE_PATHS.get(uri.path)
        if uri.path == "/camouflage/resource":
            name = IMAGE_SIZES.get(query.get("size", [None])[0])
            if set(query) != {"size"} or len(query["size"]) != 1:
                name = None
        elif name == "root":
            if query.get("completion") != [completion] or query.get("scenario") != [
                "browser_page"
            ]:
                raise ValueError("root request does not match the measured navigation")
            if set(query) - {
                "scenario",
                "size",
                "count",
                "idle_ms",
                "completion",
            } or any(len(values) != 1 for values in query.values()):
                raise ValueError("root request has a noncanonical shape")
        elif name == "complete":
            if query != {"token": [completion]}:
                raise ValueError(
                    "completion request does not match the measured navigation"
                )
        elif query:
            name = None
        if name is None or (root_only and name != "root"):
            raise ValueError("unexpected measured page request in access-log slice")
        if name in found:
            raise ValueError("duplicate measured page request in access-log slice")
        expected_method, expected_status = (
            ("POST", 204) if name == "complete" else ("GET", 200)
        )
        if (
            request.get("method") != expected_method
            or record.get("status") != expected_status
        ):
            raise ValueError("measured page request has an unexpected method or status")
        found[name] = request_event(record)
    expected = {"root"} if root_only else set(PAGE_EVENTS)
    if set(found) != expected:
        raise ValueError("access-log slice lacks the exact measured page request set")
    root_start = found["root"]["start"]
    if any(event["start"] < root_start - 0.001 for event in found.values()):
        raise ValueError("page request precedes its root outside logging tolerance")
    return found


def summarize(
    outer_records, inner_records, *, role, completion, proxy_port, inner_port
):
    if role not in ROLES:
        raise ValueError("unsupported lifecycle role")
    if not re.fullmatch(r"[0-9a-f]{32}", completion):
        raise ValueError("invalid completion token")
    if any(
        isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
        for port in (proxy_port, inner_port)
    ):
        raise ValueError("invalid fixture port")
    candidate = role in ("socks", "http")
    for record in outer_records:
        request = record["request"]
        expected_port = inner_port if request.get("method") == "CONNECT" else proxy_port
        if request.get("host") != f"localhost:{expected_port}":
            raise ValueError("outer access-log slice contains an unexpected authority")
    if any(
        record["request"].get("host") != f"localhost:{inner_port}"
        or record["request"].get("method") == "CONNECT"
        for record in inner_records
    ):
        raise ValueError("inner access-log slice contains an unexpected request")
    outer = page_events(
        outer_records,
        host=f"localhost:{proxy_port}",
        completion=completion,
        root_only=candidate,
    )
    connects = [
        record
        for record in outer_records
        if record["request"].get("method") == "CONNECT"
    ]
    if len(connects) != int(candidate):
        raise ValueError("access-log slice has an unexpected CONNECT count")
    inner = {}
    connect = None
    if candidate:
        record = connects[0]
        if (
            record["request"].get("host") != f"localhost:{inner_port}"
            or record.get("status") != 200
        ):
            raise ValueError("CONNECT does not identify the measured successful tunnel")
        connect = request_event(record)
        inner = page_events(
            inner_records, host=f"localhost:{inner_port}", completion=completion
        )
    elif inner_records:
        raise ValueError("direct reference unexpectedly contains inner requests")

    origin = outer["root"]["start"]
    if connect is not None and (
        connect["start"] < origin - 0.001
        or inner["root"]["start"] < connect["start"] - 0.001
    ):
        raise ValueError("nested request order exceeds logging tolerance")
    events = []
    for prefix, page in (("outer", outer), ("inner", inner)):
        for name, event in page.items():
            events.append({
                "event": f"{prefix}_{name}",
                "request_start_ms": round((event["start"] - origin) * 1000, 3),
                "handler_duration_ms": round(event["duration"] * 1000, 3),
                "response_bytes": event["size"],
            })
    if connect is not None:
        events.append({
            "event": "outer_connect",
            "request_start_ms": round((connect["start"] - origin) * 1000, 3),
        })
    events.sort(key=lambda item: (item["request_start_ms"], item["event"]))

    workload = inner if candidate else outer
    root = workload["root"]
    first_asset = min(
        workload[name]["start"]
        for name in PAGE_EVENTS
        if name not in ("root", "complete")
    )
    deltas = {
        "workload_root_to_first_asset_ms": (first_asset - root["start"]) * 1000,
        "workload_root_to_stylesheet_ms": (
            workload["stylesheet"]["start"] - root["start"]
        )
        * 1000,
        "workload_root_to_script_ms": (workload["script"]["start"] - root["start"])
        * 1000,
        "workload_root_end_to_stylesheet_ms": (
            workload["stylesheet"]["start"] - root["end"]
        )
        * 1000,
        "outer_root_to_workload_completion_ms": (workload["complete"]["start"] - origin)
        * 1000,
    }
    if connect is not None:
        deltas.update({
            "outer_root_to_connect_ms": (connect["start"] - origin) * 1000,
            "connect_to_inner_root_ms": (root["start"] - connect["start"]) * 1000,
            "outer_root_to_inner_root_ms": (root["start"] - origin) * 1000,
        })
    return {
        "schema_version": 1,
        "diagnostic": "h2_nested_request_lifecycle",
        "passive_feature_input": False,
        "time_source": "caddy_log_timestamp_minus_handler_duration",
        "time_origin": "outer_root_handler_start_estimate",
        "role": role,
        "events": events,
        "intervals_ms": {name: round(value, 3) for name, value in deltas.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outer-access-log", required=True)
    parser.add_argument("--outer-offset", required=True, type=int)
    parser.add_argument("--inner-access-log")
    parser.add_argument("--inner-offset", type=int, default=0)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--completion", required=True)
    parser.add_argument("--proxy-port", required=True, type=int)
    parser.add_argument("--inner-port", required=True, type=int)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--experiment-block", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--wait-seconds", type=float, default=2)
    args = parser.parse_args()
    if not re.fullmatch(r"h2_s[0-9]{6}", args.session_id) or not re.fullmatch(
        r"h2_b[0-9]{6}", args.experiment_block
    ):
        parser.error("invalid structural sample identity")
    if not 0 <= args.wait_seconds <= 5:
        parser.error("wait must be between zero and five seconds")
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            result = summarize(
                read_slice(args.outer_access_log, args.outer_offset),
                read_slice(args.inner_access_log, args.inner_offset)
                if args.inner_access_log
                else [],
                role=args.role,
                completion=args.completion,
                proxy_port=args.proxy_port,
                inner_port=args.inner_port,
            )
            break
        except ValueError as error:
            if time.monotonic() >= deadline:
                parser.error(str(error))
            time.sleep(0.05)
    result.update(session_id=args.session_id, experiment_block=args.experiment_block)
    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    Path(args.output).chmod(0o600)


if __name__ == "__main__":
    main()
