#!/usr/bin/env python3

"""Inspect decrypted outer-H3 lifecycle events before a second ClientHello.

This helper is intentionally private-only.  It reads a raw capture and an NSS
key log, and emits no HTTP header values, request targets, connection IDs, or
TLS secrets.  The output is suitable for diagnosing whether a new physical
QUIC connection was preceded by an H3 GOAWAY, QUIC CONNECTION_CLOSE, or a
stream-local RESET_STREAM/STOP_SENDING on the original connection.
"""

import argparse
import csv
import json
import subprocess
from pathlib import Path


FIELDS = (
    "frame.number",
    "frame.time_relative",
    "udp.srcport",
    "udp.dstport",
    "quic.connection.number",
    "tls.handshake.type",
    "http3.frame_type",
    "quic.rsts.stream_id",
    "quic.rsts.application_error_code",
    "quic.rsts.final_size",
    "quic.ss.stream_id",
    "quic.ss.application_error_code",
    "quic.cc.error_code",
    "quic.cc.error_code.app",
    "quic.cc.frame_type",
)


def split_values(value):
    return [item for item in value.split(";") if item]


def numeric_values(value):
    result = []
    for item in split_values(value):
        result.append(int(item, 0))
    return result


def packet_direction(row, proxy_port):
    if row["udp.dstport"] == str(proxy_port):
        return "client_to_proxy"
    if row["udp.srcport"] == str(proxy_port):
        return "proxy_to_client"
    raise ValueError("selected packet is outside the requested outer proxy flow")


def summarize_rows(rows, proxy_port):
    client_hellos = []
    seen_connections = set()
    for row in sorted(rows, key=lambda item: int(item["frame.number"])):
        if 1 not in numeric_values(row["tls.handshake.type"]):
            continue
        if row["udp.dstport"] != str(proxy_port):
            continue
        connections = split_values(row["quic.connection.number"])
        if len(connections) != 1:
            raise ValueError("ClientHello has ambiguous QUIC connection identity")
        connection = connections[0]
        if connection in seen_connections:
            continue
        seen_connections.add(connection)
        client_hellos.append(
            {
                "connection": connection,
                "frame": int(row["frame.number"]),
                "time": float(row["frame.time_relative"]),
            }
        )

    if len(client_hellos) < 2:
        raise ValueError("capture has fewer than two physical outer ClientHellos")

    first_hello, second_hello = client_hellos[:2]
    boundary = second_hello["frame"]
    event_rows = []
    h3_decoded = False
    for row in sorted(rows, key=lambda item: int(item["frame.number"])):
        frame = int(row["frame.number"])
        if frame >= boundary:
            continue
        if first_hello["connection"] not in split_values(
            row["quic.connection.number"]
        ):
            continue
        h3_types = numeric_values(row["http3.frame_type"])
        h3_decoded = h3_decoded or bool(h3_types)
        kinds = []
        if 7 in h3_types:
            kinds.append("H3_GOAWAY")
        if row["quic.rsts.stream_id"]:
            kinds.append("RESET_STREAM")
        if row["quic.ss.stream_id"]:
            kinds.append("STOP_SENDING")
        if row["quic.cc.error_code"] or row["quic.cc.error_code.app"]:
            kinds.append("CONNECTION_CLOSE")
        if not kinds:
            continue
        event_rows.append(
            {
                "frame": frame,
                "time_relative_ms": round(
                    float(row["frame.time_relative"]) * 1000, 3
                ),
                "direction": packet_direction(row, proxy_port),
                "kinds": kinds,
                "reset_stream_ids": split_values(row["quic.rsts.stream_id"]),
                "reset_error_codes": split_values(
                    row["quic.rsts.application_error_code"]
                ),
                "stop_sending_stream_ids": split_values(row["quic.ss.stream_id"]),
                "stop_sending_error_codes": split_values(
                    row["quic.ss.application_error_code"]
                ),
                "connection_close_transport_error_codes": split_values(
                    row["quic.cc.error_code"]
                ),
                "connection_close_application_error_codes": split_values(
                    row["quic.cc.error_code.app"]
                ),
                "connection_close_trigger_frame_types": split_values(
                    row["quic.cc.frame_type"]
                ),
            }
        )

    if not h3_decoded:
        raise ValueError(
            "no H3 frames from the first connection were decrypted before "
            "the second ClientHello; absence of GOAWAY would be inconclusive"
        )

    kind_counts = {
        kind: sum(kind in event["kinds"] for event in event_rows)
        for kind in (
            "H3_GOAWAY",
            "CONNECTION_CLOSE",
            "RESET_STREAM",
            "STOP_SENDING",
        )
    }
    return {
        "scope": "private_outer_h3_before_second_clienthello",
        "proxy_port": proxy_port,
        "first_connection_index": 1,
        "second_connection_index": 2,
        "first_clienthello_frame": first_hello["frame"],
        "second_clienthello_frame": second_hello["frame"],
        "second_clienthello_time_relative_ms": round(second_hello["time"] * 1000, 3),
        "first_connection_h3_decrypted_before_boundary": True,
        "event_counts": kind_counts,
        "outer_shutdown_signal_before_second_clienthello": bool(
            kind_counts["H3_GOAWAY"] or kind_counts["CONNECTION_CLOSE"]
        ),
        "stream_abort_signal_before_second_clienthello": bool(
            kind_counts["RESET_STREAM"] or kind_counts["STOP_SENDING"]
        ),
        "events": event_rows,
    }


def extract_rows(pcap, keylog, proxy_port, tshark):
    display_filter = (
        f"udp.port=={proxy_port} && quic && "
        "(tls.handshake.type==1 || http3.frame_type || "
        "quic.rsts.stream_id || quic.ss.stream_id || "
        "quic.cc.error_code || quic.cc.error_code.app)"
    )
    command = [
        tshark,
        "-r",
        str(pcap),
        "-d",
        f"udp.port=={proxy_port},quic",
        "-o",
        f"tls.keylog_file:{keylog}",
        "-Y",
        display_filter,
        "-T",
        "fields",
        "-E",
        "header=y",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=a",
        "-E",
        "aggregator=;",
    ]
    for field in FIELDS:
        command.extend(("-e", field))
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return list(csv.DictReader(completed.stdout.splitlines(), delimiter="\t"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True, type=Path)
    parser.add_argument("--keylog", required=True, type=Path)
    parser.add_argument("--proxy-port", required=True, type=int)
    parser.add_argument("--tshark", default="tshark")
    args = parser.parse_args()
    if not args.pcap.is_file() or not args.pcap.stat().st_size:
        parser.error("--pcap must name a non-empty capture")
    if not args.keylog.is_file() or not args.keylog.stat().st_size:
        parser.error("--keylog must name a non-empty NSS key log")
    if not 1 <= args.proxy_port <= 65535:
        parser.error("--proxy-port is outside the valid TCP/UDP port range")
    rows = extract_rows(args.pcap, args.keylog, args.proxy_port, args.tshark)
    print(json.dumps(summarize_rows(rows, args.proxy_port), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
