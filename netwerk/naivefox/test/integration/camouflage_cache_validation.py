#!/usr/bin/env python3

import argparse
import hashlib
import json
from pathlib import Path


def load_entries(path, start_offset=0):
    entries = []
    with Path(path).open("r", encoding="utf-8") as stream:
        stream.seek(start_offset)
        lines = stream.read().splitlines()
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed cache journal line {number}") from error
        expected = {
            "accept",
            "completion",
            "etag",
            "host",
            "if_none_match",
            "listener",
            "method",
            "path",
            "priority",
            "referer",
            "sec_fetch_dest",
            "sec_fetch_mode",
            "sec_fetch_site",
            "status",
        }
        if set(entry) != expected:
            raise ValueError(f"unexpected cache journal schema on line {number}")
        if entry["method"] != "GET" or entry["path"] != "/camouflage/style.css":
            raise ValueError(f"unexpected cache journal request on line {number}")
        if not isinstance(entry["status"], int):
            raise ValueError(f"invalid cache journal status on line {number}")
        entries.append(entry)
    return entries


def validate_transport(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    features = document.get("features", {})
    if features.get("lifecycle_connection_count") != 1.0:
        raise ValueError("cache diagnostic requires one physical outer connection")
    if features.get("tls_client_hello_count") != 1.0:
        raise ValueError("cache diagnostic requires exactly one outer ClientHello")
    if features.get("quic_zero_rtt_packet_count") != 0.0:
        raise ValueError("cache diagnostic forbids measured H3 0-RTT")


def validate_cache_sequence(entries, role, warm_token, measure_token):
    if role not in ("reference", "naivefox"):
        raise ValueError("cache diagnostic role must be reference or naivefox")
    expected_entry_count = 2 if role == "reference" else 3
    if len(entries) != expected_entry_count:
        raise ValueError("cache journal slice contains unexpected CSS requests")
    if any(entry["completion"] not in (warm_token, measure_token) for entry in entries):
        raise ValueError("cache journal slice contains unattributed CSS requests")
    warm = [entry for entry in entries if entry["completion"] == warm_token]
    measured = [entry for entry in entries if entry["completion"] == measure_token]
    if len(warm) != 1:
        raise ValueError("warm phase must contain exactly one CSS request")
    if warm[0]["status"] != 200 or warm[0]["if_none_match"]:
        raise ValueError("warm CSS request was not an unconditional 200")
    etag = warm[0]["etag"]
    if not etag or not etag.startswith('"') or not etag.endswith('"'):
        raise ValueError("warm CSS response lacks a stable quoted ETag")

    if role == "reference":
        if len(measured) != 1:
            raise ValueError("reference measure phase must contain one CSS request")
        conditional = measured
        cold_inner = []
    else:
        if len(measured) != 2:
            raise ValueError(
                "NaiveFox measure phase must contain outer cached and inner fresh CSS requests"
            )
        conditional = [entry for entry in measured if entry["if_none_match"]]
        cold_inner = [entry for entry in measured if not entry["if_none_match"]]
        if len(cold_inner) != 1 or cold_inner[0]["status"] != 200:
            raise ValueError("NaiveFox inner browser CSS request was not fresh")

    if len(conditional) != 1:
        raise ValueError("measure phase lacks exactly one conditional CSS request")
    cached = conditional[0]
    if cached["if_none_match"] != etag or cached["etag"] != etag:
        raise ValueError("measure CSS validator does not match the warmed ETag")
    if cached["status"] != 304:
        raise ValueError("conditional measure CSS request did not receive 304")
    if cached["listener"] != "http":
        raise ValueError("conditional CSS did not traverse the outer Caddy route")
    for name in (
        "accept",
        "host",
        "priority",
        "referer",
        "sec_fetch_dest",
        "sec_fetch_mode",
        "sec_fetch_site",
    ):
        if not cached[name]:
            raise ValueError(f"conditional CSS lacks {name} request semantics")
    if cold_inner and cold_inner[0]["listener"] != "https":
        raise ValueError("fresh inner CSS did not use the HTTPS target listener")
    semantic_fields = (
        "accept",
        "host",
        "priority",
        "referer",
        "sec_fetch_dest",
        "sec_fetch_mode",
        "sec_fetch_site",
    )
    semantics = json.dumps(
        {name: cached[name] for name in semantic_fields},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return {
        "role": role,
        "warm_200": 1,
        "measure_304": 1,
        "fresh_inner_200": len(cold_inner),
        "semantics_sha256": hashlib.sha256(semantics).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", required=True)
    parser.add_argument("--role", choices=("reference", "naivefox"), required=True)
    parser.add_argument("--warm-token", required=True)
    parser.add_argument("--measure-token", required=True)
    parser.add_argument("--start-offset", type=int, required=True)
    parser.add_argument("--features", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = validate_cache_sequence(
        load_entries(args.journal, args.start_offset),
        args.role,
        args.warm_token,
        args.measure_token,
    )
    validate_transport(args.features)
    destination = Path(args.output)
    descriptor = destination.open("x", encoding="utf-8")
    with descriptor:
        for name in (
            "role",
            "warm_200",
            "measure_304",
            "fresh_inner_200",
            "semantics_sha256",
        ):
            descriptor.write(f"{name}={result[name]}\n")


if __name__ == "__main__":
    main()
