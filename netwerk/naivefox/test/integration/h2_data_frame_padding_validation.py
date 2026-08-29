#!/usr/bin/env python3

import argparse
import io
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def field_elements(element, name):
    return [field for field in element.iter("field") if field.get("name") == name]


def field_text(field):
    shown = field.get("show")
    if shown is not None:
        return shown
    value = field.get("value")
    if value is None:
        return ""
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return value


def field_number(element, name):
    fields = field_elements(element, name)
    if not fields:
        return None
    shown = field_text(fields[0]).strip()
    try:
        return int(shown, 0)
    except ValueError:
        value = fields[0].get("value", "")
        try:
            return int(value, 16)
        except ValueError as exc:
            raise ValueError(f"invalid {name} value: {shown or value}") from exc


def field_boolean(element, name):
    fields = field_elements(element, name)
    if not fields:
        return False
    value = field_text(fields[0]).strip().lower()
    if value in {"true", "1", "yes", "set"}:
        return True
    if value in {"false", "0", "no", "not set"}:
        return False
    raw = fields[0].get("value", "")
    if raw in {"01", "1"}:
        return True
    if raw in {"00", "0"}:
        return False
    raise ValueError(f"invalid {name} boolean: {value or raw}")


def header_pairs(proto):
    pairs = []
    for header in field_elements(proto, "http2.header"):
        names = field_elements(header, "http2.header.name")
        values = field_elements(header, "http2.header.value")
        if len(names) == 1 and len(values) == 1:
            pairs.append((field_text(names[0]).lower(), field_text(values[0])))
    return pairs


def parse_pdml(stream):
    if isinstance(stream, (bytes, bytearray)):
        stream = io.BytesIO(stream)
    root = ET.parse(stream).getroot()
    events = []
    for packet in root.findall("packet"):
        tcp_stream = field_number(packet, "tcp.stream")
        source_port = field_number(packet, "tcp.srcport")
        destination_port = field_number(packet, "tcp.dstport")
        if tcp_stream is None or source_port is None or destination_port is None:
            continue
        for proto in packet.iter("proto"):
            if proto.get("name") != "http2":
                continue
            frame_type = field_number(proto, "http2.type")
            stream_id = field_number(proto, "http2.streamid")
            if frame_type is None or stream_id is None:
                continue
            methods = [
                field_text(field)
                for field in field_elements(proto, "http2.headers.method")
            ]
            statuses = [
                field_text(field)
                for field in field_elements(proto, "http2.headers.status")
            ]
            events.append({
                "tcp_stream": tcp_stream,
                "source_port": source_port,
                "destination_port": destination_port,
                "type": frame_type,
                "stream_id": stream_id,
                "methods": methods,
                "statuses": statuses,
                "headers": header_pairs(proto),
                "padded": field_boolean(proto, "http2.flags.padded"),
                "padding_length": field_number(proto, "http2.padding"),
            })
    return events


def marker_values(event):
    return [
        value
        for name, value in event["headers"]
        if name == "padding" and value.startswith("~9")
    ]


def validate_events(events, proxy_port):
    connect_requests = []
    for event in events:
        if (
            event["type"] == 1
            and event["destination_port"] == proxy_port
            and "CONNECT" in event["methods"]
        ):
            connect_requests.append(event)
    if not connect_requests:
        raise ValueError("decrypted H2 capture has no client CONNECT")

    connect_keys = set()
    for event in connect_requests:
        key = (event["tcp_stream"], event["stream_id"])
        if key in connect_keys:
            raise ValueError("decrypted H2 capture repeats a CONNECT stream identity")
        markers = marker_values(event)
        if len(markers) != 1 or not 16 <= len(markers[0]) <= 32:
            raise ValueError("CONNECT request lacks one bounded H2 DATA padding marker")
        connect_keys.add(key)

    responses = defaultdict(list)
    for event in events:
        key = (event["tcp_stream"], event["stream_id"])
        if (
            key in connect_keys
            and event["type"] == 1
            and event["source_port"] == proxy_port
            and "200" in event["statuses"]
        ):
            responses[key].append(event)
    for key in connect_keys:
        if len(responses[key]) != 1:
            raise ValueError("CONNECT stream lacks one decrypted 200 response")
        markers = marker_values(responses[key][0])
        if len(markers) != 1 or not 30 <= len(markers[0]) <= 61:
            raise ValueError("CONNECT response lacks one bounded echoed marker")

    padded_by_connect = defaultdict(int)
    padded_total = 0
    for event in events:
        if event["type"] != 0 or not event["padded"]:
            continue
        padded_total += 1
        key = (event["tcp_stream"], event["stream_id"])
        if event["source_port"] != proxy_port or key not in connect_keys:
            raise ValueError("PADDED DATA escaped the server CONNECT response stream")
        padding_length = event["padding_length"]
        if padding_length is None or not 0 <= padding_length <= 255:
            raise ValueError("PADDED DATA lacks a valid Pad Length field")
        padded_by_connect[key] += 1

    for key in connect_keys:
        count = padded_by_connect[key]
        if not 1 <= count <= 8:
            raise ValueError(
                f"CONNECT stream has {count} PADDED DATA frames; expected 1..8"
            )
    return {
        "connect_streams": len(connect_keys),
        "padded_data_frames": padded_total,
    }


def extract_events(tshark, pcap, keylog, proxy_port):
    command = [
        tshark,
        "-n",
        "-r",
        str(pcap),
        "-o",
        f"tls.keylog_file:{keylog}",
        "-d",
        f"tcp.port=={proxy_port},tls",
        "-Y",
        f"tcp.port=={proxy_port} && http2",
        "-T",
        "pdml",
    ]
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return parse_pdml(completed.stdout)


def main():
    parser = argparse.ArgumentParser()
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
        parser.error("--proxy-port is out of range")
    result = validate_events(
        extract_events(args.tshark, args.pcap, args.keylog, args.proxy_port),
        args.proxy_port,
    )
    print(
        "h2_data_frame_padding=validated "
        f"connect_streams={result['connect_streams']} "
        f"padded_data_frames={result['padded_data_frames']}"
    )


if __name__ == "__main__":
    main()
