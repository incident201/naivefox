#!/usr/bin/env python3

import argparse
import csv
import io
import json
import math
import os
import re
import subprocess

SCHEMA_VERSION = 1
METADATA_FIELDS = (
    "schema_version",
    "protocol",
    "scenario",
    "label",
    "naivefox_arm",
    "session_id",
    "experiment_block",
)
FORBIDDEN_FEATURE_TERMS = (
    "absolute_timestamp",
    "authority",
    "canary",
    "cohort",
    "credential",
    "decrypted",
    "destination_port",
    "filename",
    "header",
    "label",
    "method",
    "naivefox_arm",
    "password",
    "path",
    "plaintext",
    "process_duration",
    "profile",
    "query",
    "session_id",
    "source_port",
    "status",
    "stream_id",
)


def split_values(value):
    return [item for item in (value or "").split(";") if item]


def number(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def integer(value, default=0):
    return int(number(value, default))


def truthy(value):
    return str(value).lower() in {"1", "true", "yes", "set"}


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(
        ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
    )


def token(value):
    value = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return value[:80] or "empty"


def numeric_token(value):
    try:
        parsed = int(value, 0)
    except ValueError:
        return token(value)
    if parsed & 0x0F0F == 0x0A0A and parsed <= 0xFFFF:
        return "grease"
    return f"0x{parsed:04x}"


def quic_transport_parameter_token(value):
    try:
        parsed = int(value, 0)
    except ValueError:
        return token(value)
    if parsed >= 27 and (parsed - 27) % 31 == 0:
        return "grease"
    return f"0x{parsed:04x}"


def tshark_rows(pcap, decode, display_filter, fields):
    command = [
        "tshark",
        "-r",
        pcap,
        *decode,
        "-Y",
        display_filter,
        "-T",
        "fields",
        "-E",
        "header=y",
        "-E",
        "separator=,",
        "-E",
        "quote=d",
        "-E",
        "occurrence=a",
        "-E",
        "aggregator=;",
    ]
    for field in fields:
        command.extend(("-e", field))
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return list(csv.DictReader(io.StringIO(result.stdout)))


def prefer_transmit_copy(rows):
    transmit = [row for row in rows if row.get("sll.pkttype") == "4"]
    return transmit or rows


def add_categorical(features, prefix, values, ordered=False):
    normalized = [numeric_token(value) for value in values]
    for value in set(normalized):
        features[f"{prefix}_{value}"] = 1.0
    features[f"{prefix}_count"] = float(len(normalized))
    if ordered:
        for index, value in enumerate(normalized[:64], 1):
            features[f"{prefix}_position_{index:03d}_{value}"] = 1.0


def parse_tcp_option_order(raw):
    data = re.sub(r"[^0-9a-fA-F]", "", raw or "")
    if len(data) % 2 or not data:
        return []
    payload = bytes.fromhex(data)
    result = []
    offset = 0
    while offset < len(payload):
        kind = payload[offset]
        result.append(kind)
        if kind == 0:
            break
        if kind == 1:
            offset += 1
            continue
        if offset + 1 >= len(payload):
            break
        length = payload[offset + 1]
        if length < 2:
            break
        offset += length
    return result


def extract_handshake(pcap, protocol, server_port, features):
    if protocol == "h2":
        decode = ["-d", f"tcp.port=={server_port},tls"]
        client_filter = f"tcp.dstport=={server_port} && tls.handshake.type==1"
        server_filter = f"tcp.srcport=={server_port} && tls.handshake.type==2"
    else:
        decode = ["-d", f"udp.port=={server_port},quic"]
        client_filter = f"udp.dstport=={server_port} && tls.handshake.type==1"
        server_filter = f"udp.srcport=={server_port} && tls.handshake.type==2"
    fields = [
        "tls.handshake.length",
        "tls.record.version",
        "tls.handshake.version",
        "tls.handshake.extensions.supported_version",
        "tls.handshake.ciphersuite",
        "tls.handshake.extension.type",
        "tls.handshake.extensions_supported_group",
        "tls.handshake.sig_hash_alg",
        "tls.handshake.extensions_key_share_group",
        "tls.handshake.extensions_alpn_str",
        "tls.handshake.extensions_server_name",
    ]
    hellos = tshark_rows(pcap, decode, client_filter, fields)
    features["tls_client_hello_count"] = float(len(hellos))
    if hellos:
        hello = hellos[0]
        features["tls_client_hello_length"] = number(
            split_values(hello["tls.handshake.length"])[0]
            if split_values(hello["tls.handshake.length"])
            else 0
        )
        features["tls_sni_present"] = float(
            bool(split_values(hello["tls.handshake.extensions_server_name"]))
        )
        for name, field, ordered in (
            (
                "tls_supported_version",
                "tls.handshake.extensions.supported_version",
                False,
            ),
            ("tls_cipher", "tls.handshake.ciphersuite", True),
            ("tls_extension", "tls.handshake.extension.type", True),
            ("tls_group", "tls.handshake.extensions_supported_group", True),
            ("tls_signature", "tls.handshake.sig_hash_alg", True),
            ("tls_key_share_group", "tls.handshake.extensions_key_share_group", True),
        ):
            add_categorical(features, name, split_values(hello[field]), ordered)
        for alpn in split_values(hello["tls.handshake.extensions_alpn_str"]):
            features[f"tls_alpn_{token(alpn)}"] = 1.0
        features["tls_alpn_count"] = float(
            len(split_values(hello["tls.handshake.extensions_alpn_str"]))
        )
    server_fields = [
        "tls.handshake.version",
        "tls.handshake.extensions.supported_version",
        "tls.handshake.ciphersuite",
        "tls.handshake.extensions_key_share_selected_group",
    ]
    server_hellos = tshark_rows(pcap, decode, server_filter, server_fields)
    features["tls_server_hello_count"] = float(len(server_hellos))
    if server_hellos:
        hello = server_hellos[0]
        for name, field in (
            ("tls_server_version", "tls.handshake.extensions.supported_version"),
            ("tls_server_cipher", "tls.handshake.ciphersuite"),
            (
                "tls_server_key_share",
                "tls.handshake.extensions_key_share_selected_group",
            ),
        ):
            values = split_values(hello[field])
            if values:
                features[f"{name}_{numeric_token(values[0])}"] = 1.0


def packet_events_h2(pcap, server_port):
    fields = [
        "frame.number",
        "frame.time_relative",
        "frame.len",
        "sll.pkttype",
        "ip.len",
        "ipv6.plen",
        "tcp.stream",
        "tcp.srcport",
        "tcp.dstport",
        "tcp.len",
        "tcp.window_size_value",
        "tcp.flags.syn",
        "tcp.flags.ack",
        "tcp.flags.fin",
        "tcp.flags.reset",
        "tcp.flags.ece",
        "tcp.flags.cwr",
        "tcp.options",
        "tcp.options.mss_val",
        "tcp.options.wscale.shift",
        "tcp.options.timestamp.tsval",
        "tcp.options.tfo.request",
        "tcp.options.tfo.cookie",
        "tcp.analysis.retransmission",
        "tcp.analysis.fast_retransmission",
        "tcp.analysis.out_of_order",
        "tcp.analysis.lost_segment",
        "tls.record.length",
    ]
    rows = tshark_rows(
        pcap,
        ["-d", f"tcp.port=={server_port},tls"],
        f"tcp.port=={server_port}",
        fields,
    )
    rows = prefer_transmit_copy(rows)
    events = []
    records = []
    for row in rows:
        outbound = row["tcp.dstport"] == str(server_port)
        wire_size = integer(row["ip.len"])
        if not wire_size and row["ipv6.plen"]:
            wire_size = integer(row["ipv6.plen"]) + 40
        if not wire_size:
            wire_size = integer(row["frame.len"])
        event = {
            "frame": integer(row["frame.number"]),
            "time": number(row["frame.time_relative"]),
            "direction": 1 if outbound else -1,
            "transport_size": integer(row["tcp.len"]),
            "wire_size": wire_size,
            "flow": row["tcp.stream"],
            "syn": truthy(row["tcp.flags.syn"]),
            "ack": truthy(row["tcp.flags.ack"]),
            "fin": truthy(row["tcp.flags.fin"]),
            "rst": truthy(row["tcp.flags.reset"]),
            "retransmission": bool(
                row["tcp.analysis.retransmission"]
                or row["tcp.analysis.fast_retransmission"]
            ),
            "out_of_order": bool(row["tcp.analysis.out_of_order"]),
            "lost_segment": bool(row["tcp.analysis.lost_segment"]),
            "row": row,
        }
        events.append(event)
        for length in split_values(row["tls.record.length"]):
            records.append({
                "time": event["time"],
                "direction": event["direction"],
                "length": integer(length),
            })
    return sorted(events, key=lambda item: (item["time"], item["frame"])), records


def packet_events_h3(pcap, server_port):
    fields = [
        "frame.number",
        "frame.time_relative",
        "frame.len",
        "sll.pkttype",
        "ip.len",
        "ipv6.plen",
        "udp.srcport",
        "udp.dstport",
        "udp.length",
        "quic.connection.number",
        "quic.version",
        "quic.long.packet_type",
        "quic.long.packet_type_v2",
        "quic.dcil",
        "quic.scil",
        "quic.packet_length",
    ]
    rows = tshark_rows(
        pcap,
        ["-d", f"udp.port=={server_port},quic"],
        f"udp.port=={server_port}",
        fields,
    )
    rows = prefer_transmit_copy(rows)
    events = []
    for row in rows:
        outbound = row["udp.dstport"] == str(server_port)
        wire_size = integer(row["ip.len"])
        if not wire_size and row["ipv6.plen"]:
            wire_size = integer(row["ipv6.plen"]) + 40
        if not wire_size:
            wire_size = integer(row["frame.len"])
        packet_types = split_values(row["quic.long.packet_type"])
        packet_types.extend(
            {
                "0": "3",  # Retry
                "1": "0",  # Initial
                "2": "1",  # 0-RTT
                "3": "2",  # Handshake
            }.get(value, value)
            for value in split_values(row["quic.long.packet_type_v2"])
        )
        events.append({
            "frame": integer(row["frame.number"]),
            "time": number(row["frame.time_relative"]),
            "direction": 1 if outbound else -1,
            "transport_size": integer(row["udp.length"]),
            "wire_size": wire_size,
            "flow": row["quic.connection.number"],
            "versions": split_values(row["quic.version"]),
            "packet_types": packet_types,
            "dcil": split_values(row["quic.dcil"]),
            "scil": split_values(row["quic.scil"]),
            "syn": False,
            "ack": False,
            "fin": False,
            "rst": False,
            "retransmission": False,
            "out_of_order": False,
            "lost_segment": False,
            "row": row,
        })
    return sorted(events, key=lambda item: (item["time"], item["frame"])), []


def add_h3_tcp_probe_features(pcap, server_port, features):
    fields = [
        "sll.pkttype",
        "tcp.stream",
        "tcp.srcport",
        "tcp.dstport",
        "tcp.len",
        "tcp.flags.syn",
        "tcp.flags.ack",
        "tcp.flags.reset",
    ]
    rows = prefer_transmit_copy(
        tshark_rows(pcap, [], f"tcp.port=={server_port}", fields)
    )
    client = [row for row in rows if row["tcp.dstport"] == str(server_port)]
    server = [row for row in rows if row["tcp.srcport"] == str(server_port)]
    features["quic_tcp_probe_packet_count"] = float(len(rows))
    features["quic_tcp_probe_connection_count"] = float(
        len({row["tcp.stream"] for row in rows if row["tcp.stream"]})
    )
    features["quic_tcp_probe_client_syn_count"] = float(
        sum(
            truthy(row["tcp.flags.syn"]) and not truthy(row["tcp.flags.ack"])
            for row in client
        )
    )
    features["quic_tcp_probe_server_syn_ack_count"] = float(
        sum(
            truthy(row["tcp.flags.syn"]) and truthy(row["tcp.flags.ack"])
            for row in server
        )
    )
    features["quic_tcp_probe_client_rst_count"] = float(
        sum(truthy(row["tcp.flags.reset"]) for row in client)
    )
    features["quic_tcp_probe_server_rst_count"] = float(
        sum(truthy(row["tcp.flags.reset"]) for row in server)
    )
    features["quic_tcp_probe_payload_packet_count"] = float(
        sum(integer(row["tcp.len"]) > 0 for row in rows)
    )
    features["quic_tcp_probe_payload_bytes"] = float(
        sum(integer(row["tcp.len"]) for row in rows)
    )


def add_aggregate(features, prefix, events):
    if not events:
        features[f"{prefix}_packet_count"] = 0.0
        return
    origin = events[0]["time"]
    times = [event["time"] for event in events]
    deltas = [
        max(0.0, (events[index]["time"] - events[index - 1]["time"]) * 1000)
        for index in range(1, len(events))
    ]
    client = [event for event in events if event["direction"] > 0]
    server = [event for event in events if event["direction"] < 0]
    features[f"{prefix}_packet_count"] = float(len(events))
    features[f"{prefix}_client_packet_count"] = float(len(client))
    features[f"{prefix}_server_packet_count"] = float(len(server))
    features[f"{prefix}_client_transport_bytes"] = float(
        sum(event["transport_size"] for event in client)
    )
    features[f"{prefix}_server_transport_bytes"] = float(
        sum(event["transport_size"] for event in server)
    )
    features[f"{prefix}_client_wire_bytes"] = float(
        sum(event["wire_size"] for event in client)
    )
    features[f"{prefix}_server_wire_bytes"] = float(
        sum(event["wire_size"] for event in server)
    )
    features[f"{prefix}_duration_ms"] = max(times[-1] - origin, 0.0) * 1000
    server_times = [event["time"] for event in server]
    features[f"{prefix}_first_server_response_ms"] = (
        max(0.0, server_times[0] - origin) * 1000 if server_times else 0.0
    )
    for name, values in (
        ("delta_ms", deltas),
        ("transport_size", [event["transport_size"] for event in events]),
        ("wire_size", [event["wire_size"] for event in events]),
    ):
        for label, fraction in (
            ("p10", 0.10),
            ("p50", 0.50),
            ("p90", 0.90),
            ("p95", 0.95),
            ("p99", 0.99),
        ):
            features[f"{prefix}_{name}_{label}"] = percentile(values, fraction)
    directions = ["c" if event["direction"] > 0 else "s" for event in events]
    features[f"{prefix}_direction_changes"] = float(
        sum(left != right for left, right in zip(directions, directions[1:]))
    )
    for width, name in ((2, "bigram"), (3, "trigram")):
        for index in range(len(directions) - width + 1):
            value = "".join(directions[index : index + width])
            key = f"{prefix}_direction_{name}_{value}"
            features[key] = features.get(key, 0.0) + 1.0
    runs = []
    run_length = 0
    previous = None
    for direction in directions:
        if direction == previous:
            run_length += 1
        else:
            if run_length:
                runs.append(run_length)
            previous = direction
            run_length = 1
    runs.append(run_length)
    features[f"{prefix}_direction_run_count"] = float(len(runs))
    features[f"{prefix}_direction_run_p50"] = percentile(runs, 0.5)
    features[f"{prefix}_direction_run_max"] = float(max(runs))
    features[f"{prefix}_idle_gap_max_ms"] = max(deltas, default=0.0)
    features[f"{prefix}_retransmission_count"] = float(
        sum(event["retransmission"] for event in events)
    )
    features[f"{prefix}_out_of_order_count"] = float(
        sum(event["out_of_order"] for event in events)
    )
    features[f"{prefix}_lost_segment_count"] = float(
        sum(event["lost_segment"] for event in events)
    )
    for threshold in (100, 500, 2000):
        features[f"{prefix}_idle_gap_over_{threshold}ms"] = float(
            sum(delta > threshold for delta in deltas)
        )
    for threshold in (20, 50, 100, 250):
        burst_lengths = []
        length = 1
        for delta in deltas:
            if delta > threshold:
                burst_lengths.append(length)
                length = 1
            else:
                length += 1
        burst_lengths.append(length)
        features[f"{prefix}_bursts_{threshold}ms"] = float(len(burst_lengths))
        features[f"{prefix}_burst_packets_{threshold}ms_p50"] = percentile(
            burst_lengths, 0.5
        )
        features[f"{prefix}_burst_packets_{threshold}ms_max"] = float(
            max(burst_lengths)
        )


def add_sequence_features(features, events):
    if not events:
        return
    origin = events[0]["time"]
    previous = origin
    for index, event in enumerate(events[:128], 1):
        prefix = f"packet_{index:03d}"
        features[f"{prefix}_direction"] = float(event["direction"])
        features[f"{prefix}_transport_size_signed"] = float(
            event["direction"] * event["transport_size"]
        )
        features[f"{prefix}_wire_size_signed"] = float(
            event["direction"] * event["wire_size"]
        )
        features[f"{prefix}_delta_ms"] = max(0.0, event["time"] - previous) * 1000
        features[f"{prefix}_elapsed_ms"] = max(0.0, event["time"] - origin) * 1000
        previous = event["time"]
    for window in (16, 32, 64, 128):
        add_aggregate(features, f"initial_{window}", events[:window])
    for milliseconds in (50, 100, 250, 500, 1000, 2000):
        selected = [
            event for event in events if (event["time"] - origin) * 1000 <= milliseconds
        ]
        add_aggregate(features, f"initial_{milliseconds}ms", selected)
    add_aggregate(features, "steady_after_32", events[32:])
    add_aggregate(
        features,
        "steady_after_2000ms",
        [event for event in events if event["time"] - origin >= 2.0],
    )


def add_tls_record_features(features, records):
    features["tls_record_count"] = float(len(records))
    for index, record in enumerate(records[:128], 1):
        features[f"tls_record_{index:03d}_signed_length"] = float(
            record["direction"] * record["length"]
        )
    for direction_name, direction in (("client", 1), ("server", -1)):
        values = [
            record["length"] for record in records if record["direction"] == direction
        ]
        features[f"tls_record_{direction_name}_count"] = float(len(values))
        features[f"tls_record_{direction_name}_bytes"] = float(sum(values))
        for label, fraction in (
            ("p10", 0.10),
            ("p50", 0.50),
            ("p90", 0.90),
            ("p95", 0.95),
            ("p99", 0.99),
        ):
            features[f"tls_record_{direction_name}_length_{label}"] = percentile(
                values, fraction
            )


def add_h2_features(features, events):
    flows = {event["flow"] for event in events if event["flow"]}
    features["lifecycle_connection_count"] = float(len(flows))
    features["lifecycle_reconnect_count"] = float(max(len(flows) - 1, 0))
    for name, predicate in (
        ("client_fin", lambda event: event["fin"] and event["direction"] > 0),
        ("server_fin", lambda event: event["fin"] and event["direction"] < 0),
        ("client_rst", lambda event: event["rst"] and event["direction"] > 0),
        ("server_rst", lambda event: event["rst"] and event["direction"] < 0),
    ):
        features[f"lifecycle_{name}_count"] = float(
            sum(predicate(event) for event in events)
        )
    add_aggregate(features, "lifecycle_tail_16", events[-16:])
    client_syns = [
        event
        for event in events
        if event["direction"] > 0 and event["syn"] and not event["ack"]
    ]
    features["tcp_syn_count"] = float(len(client_syns))
    if client_syns:
        row = client_syns[0]["row"]
        features["tcp_syn_mss"] = number(row["tcp.options.mss_val"])
        features["tcp_syn_window_scale"] = number(row["tcp.options.wscale.shift"])
        features["tcp_syn_window"] = number(row["tcp.window_size_value"])
        features["tcp_syn_sack_permitted"] = float(
            4 in parse_tcp_option_order(row["tcp.options"])
        )
        features["tcp_syn_timestamps"] = float(
            bool(split_values(row["tcp.options.timestamp.tsval"]))
        )
        features["tcp_syn_fast_open"] = float(
            bool(row["tcp.options.tfo.request"] or row["tcp.options.tfo.cookie"])
        )
        features["tcp_syn_ecn"] = float(
            truthy(row["tcp.flags.ece"]) or truthy(row["tcp.flags.cwr"])
        )
        order = parse_tcp_option_order(row["tcp.options"])
        if order:
            features[
                "tcp_syn_option_order_" + "_".join(str(value) for value in order)
            ] = 1.0


def extract_transport_parameters(pcap, server_port, features):
    fields = [
        "tls.quic.parameter.type",
        "tls.quic.parameter.max_idle_timeout",
        "tls.quic.parameter.max_udp_payload_size",
        "tls.quic.parameter.initial_max_data",
        "tls.quic.parameter.initial_max_stream_data_bidi_local",
        "tls.quic.parameter.initial_max_stream_data_bidi_remote",
        "tls.quic.parameter.initial_max_stream_data_uni",
        "tls.quic.parameter.initial_max_streams_bidi",
        "tls.quic.parameter.initial_max_streams_uni",
        "tls.quic.parameter.ack_delay_exponent",
        "tls.quic.parameter.max_ack_delay",
        "tls.quic.parameter.active_connection_id_limit",
        "tls.quic.parameter.max_datagram_frame_size",
        "tls.quic.parameter.min_ack_delay",
    ]
    rows = tshark_rows(
        pcap,
        ["-d", f"udp.port=={server_port},quic"],
        f"udp.dstport=={server_port} && tls.quic.parameter.type",
        fields,
    )
    features["quic_transport_parameter_rows"] = float(len(rows))
    for row in rows:
        for field in fields[1:]:
            values = split_values(row[field])
            if values:
                features[f"quic_tp_{field.rsplit('.', 1)[-1]}"] = number(values[0])
        for value in split_values(row[fields[0]]):
            features[f"quic_tp_type_{quic_transport_parameter_token(value)}"] = 1.0


def add_h3_features(features, events):
    flows = {event["flow"] for event in events if event["flow"]}
    features["lifecycle_connection_count"] = float(len(flows))
    features["lifecycle_reconnect_count"] = float(max(len(flows) - 1, 0))
    add_aggregate(features, "lifecycle_tail_16", events[-16:])
    versions = [value for event in events for value in event["versions"]]
    add_categorical(features, "quic_version", versions)
    phases = []
    initial = []
    retry_count = 0
    zero_rtt_count = 0
    version_negotiation = 0
    for event in events:
        packet_types = event["packet_types"] or ["short"]
        for packet_type in packet_types:
            phase = {
                "0": "initial",
                "1": "zero_rtt",
                "2": "handshake",
                "3": "retry",
            }.get(packet_type, "short")
            phases.append((event["direction"], phase))
            if phase == "initial":
                initial.append(event)
            if phase == "retry":
                retry_count += 1
            if phase == "zero_rtt":
                zero_rtt_count += 1
        if any(value in {"0", "0x00000000"} for value in event["versions"]):
            version_negotiation += 1
    features["quic_initial_packet_count"] = float(len(initial))
    features["quic_retry_packet_count"] = float(retry_count)
    features["quic_zero_rtt_packet_count"] = float(zero_rtt_count)
    features["quic_version_negotiation_packet_count"] = float(version_negotiation)
    dcid = [value for event in initial for value in event["dcil"]]
    scid = [value for event in initial for value in event["scil"]]
    add_categorical(features, "quic_initial_dcid_length", dcid)
    add_categorical(features, "quic_initial_scid_length", scid)
    for index, event in enumerate(initial[:16], 1):
        features[f"quic_initial_{index:02d}_signed_datagram_length"] = float(
            event["direction"] * event["transport_size"]
        )
    for index, (direction, phase) in enumerate(phases[:24], 1):
        side = "c" if direction > 0 else "s"
        features[f"quic_phase_position_{index:02d}_{side}_{phase}"] = 1.0


def validate_features(features):
    for name, value in features.items():
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"unsafe feature name: {name}")
        if any(term in name for term in FORBIDDEN_FEATURE_TERMS):
            raise ValueError(f"forbidden feature name: {name}")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"feature is not finite numeric data: {name}")


def extract(args):
    if args.naivefox_arm == "root-pmtud-control" and args.protocol != "h3":
        raise SystemExit("root-pmtud-control requires h3")
    if (
        args.naivefox_arm == "document-handshake-confirmed"
        and args.protocol != "h3"
    ):
        raise SystemExit("document-handshake-confirmed requires h3")
    if args.naivefox_arm == "document-carrier-dispatch" and args.protocol != "h3":
        raise SystemExit("document-carrier-dispatch requires h3")
    if (
        args.naivefox_arm == "document-cold-winner-handoff"
        and args.protocol != "h3"
    ):
        raise SystemExit("document-cold-winner-handoff requires h3")
    if args.naivefox_arm == "document-native-cache-open" and args.protocol != "h3":
        raise SystemExit("document-native-cache-open requires h3")
    if args.naivefox_arm == "document-native-channel-open" and args.protocol != "h3":
        raise SystemExit("document-native-channel-open requires h3")
    if (
        args.naivefox_arm == "tree-resource-committed-overlap-css"
        and args.protocol != "h3"
    ):
        raise SystemExit("tree-resource-committed-overlap-css requires h3")
    if (
        args.naivefox_arm == "tree-resource-native-cache-committed-overlap"
        and args.protocol != "h3"
    ):
        raise SystemExit(
            "tree-resource-native-cache-committed-overlap requires h3"
        )
    if (
        args.naivefox_arm
        in (
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
        )
        and args.protocol != "h3"
    ):
        raise SystemExit(f"{args.naivefox_arm} requires h3")
    features = {}
    extract_handshake(args.pcap, args.protocol, args.server_port, features)
    if args.protocol == "h2":
        events, records = packet_events_h2(args.pcap, args.server_port)
    else:
        events, records = packet_events_h3(args.pcap, args.server_port)
        add_h3_tcp_probe_features(args.pcap, args.server_port, features)
        extract_transport_parameters(args.pcap, args.server_port, features)
    if not events:
        raise SystemExit("capture contains no packets for the selected endpoint")
    add_aggregate(features, "whole", events)
    add_sequence_features(features, events)
    add_tls_record_features(features, records)
    if args.protocol == "h2":
        add_h2_features(features, events)
    else:
        add_h3_features(features, events)
    validate_features(features)
    document = {
        "schema_version": SCHEMA_VERSION,
        "protocol": args.protocol,
        "scenario": args.scenario,
        "label": args.label,
        "naivefox_arm": getattr(args, "naivefox_arm", None) or "",
        "session_id": args.session_id,
        "experiment_block": args.experiment_block or "",
        "features": features,
    }
    temporary = args.output + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(document, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    os.replace(temporary, args.output)


def merge(args):
    documents = []
    for name in sorted(os.listdir(args.input_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(args.input_dir, name), encoding="utf-8") as stream:
            document = json.load(stream)
        if document.get("schema_version") != SCHEMA_VERSION:
            raise SystemExit(f"unsupported feature schema in {name}")
        validate_features(document["features"])
        documents.append(document)
    if not documents:
        raise SystemExit("no extracted feature documents found")
    counts = {}
    blocks = {}
    sessions = set()
    feature_names = set()
    for document in documents:
        key = (document["protocol"], document["label"])
        counts[key] = counts.get(key, 0) + 1
        block = document.get("experiment_block") or document["session_id"]
        blocks.setdefault((document["protocol"], block), []).append(document)
        if document["session_id"] in sessions:
            raise SystemExit(f"duplicate session id: {document['session_id']}")
        sessions.add(document["session_id"])
        feature_names.update(document["features"])
    expected_per_cohort = getattr(args, "expected_per_cohort", None)
    expected_superblocks = getattr(args, "expected_superblocks", None)
    expected_superblock_arms = getattr(
        args, "expected_superblock_arms", "off,gate,root"
    )
    if expected_per_cohort and expected_superblocks:
        raise SystemExit("cohort and superblock expectations are mutually exclusive")
    if expected_per_cohort:
        protocols = {document["protocol"] for document in documents}
        for protocol in protocols:
            for label in ("firefox_a", "firefox_b", "naivefox"):
                if counts.get((protocol, label), 0) != expected_per_cohort:
                    raise SystemExit(
                        f"{protocol}/{label} has {counts.get((protocol, label), 0)} "
                        f"samples, expected {expected_per_cohort}"
                    )
        expected_labels = ["firefox_a", "firefox_b", "naivefox"]
        for (protocol, block), members in sorted(blocks.items()):
            labels = sorted(document["label"] for document in members)
            scenarios = {document["scenario"] for document in members}
            if labels != expected_labels or len(scenarios) != 1:
                raise SystemExit(
                    f"incomplete experiment block {protocol}/{block}: "
                    f"labels={labels}, scenarios={sorted(scenarios)}"
                )
    if expected_superblocks:
        selected_arms = tuple(
            arm.strip() for arm in expected_superblock_arms.split(",") if arm.strip()
        )
        expected_members = {
            ("firefox_a", "reference"),
            ("firefox_b", "reference"),
            *(("naivefox", arm) for arm in selected_arms),
        }
        protocols = {document["protocol"] for document in documents}
        for protocol in protocols:
            protocol_blocks = [key for key in blocks if key[0] == protocol]
            if len(protocol_blocks) != expected_superblocks:
                raise SystemExit(
                    f"{protocol} has {len(protocol_blocks)} superblocks, "
                    f"expected {expected_superblocks}"
                )
        for (protocol, block), members in sorted(blocks.items()):
            actual_members = {
                (document["label"], document.get("naivefox_arm", ""))
                for document in members
            }
            scenarios = {document["scenario"] for document in members}
            if (
                len(members) != len(expected_members)
                or actual_members != expected_members
                or len(scenarios) != 1
            ):
                raise SystemExit(
                    f"incomplete experiment superblock {protocol}/{block}: "
                    f"members={sorted(actual_members)}, scenarios={sorted(scenarios)}"
                )
    fieldnames = [*METADATA_FIELDS, *sorted(feature_names)]
    temporary = args.output + ".tmp"
    with open(temporary, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for document in documents:
            row = {name: document.get(name, "") for name in METADATA_FIELDS}
            row.update(document["features"])
            writer.writerow(row)
    os.replace(temporary, args.output)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    extract_parser = commands.add_parser("extract")
    extract_parser.add_argument("--pcap", required=True)
    extract_parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    extract_parser.add_argument("--server-port", type=int, required=True)
    extract_parser.add_argument("--scenario", required=True)
    extract_parser.add_argument(
        "--label", choices=("firefox_a", "firefox_b", "naivefox"), required=True
    )
    extract_parser.add_argument("--session-id", required=True)
    extract_parser.add_argument("--experiment-block")
    extract_parser.add_argument(
        "--naivefox-arm",
        choices=(
            "reference",
            "off",
            "gate",
            "root",
            "root-pmtud-control",
            "document-complete",
            "document-carrier-dispatch",
            "document-cold-winner-handoff",
            "document-native-cache-open",
            "document-native-channel-open",
            "document-handshake-confirmed",
            "document-overlap",
            "document-start-overlap",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-resource-committed-overlap-css",
            "tree-resource-native-cache-committed-overlap",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
            "tree-warm-css-304",
            "tree-overlap",
        ),
    )
    extract_parser.add_argument("--output", required=True)
    extract_parser.set_defaults(function=extract)
    merge_parser = commands.add_parser("merge")
    merge_parser.add_argument("--input-dir", required=True)
    merge_parser.add_argument("--output", required=True)
    merge_parser.add_argument("--expected-per-cohort", type=int)
    merge_parser.add_argument("--expected-superblocks", type=int)
    merge_parser.add_argument("--expected-superblock-arms", default="off,gate,root")
    merge_parser.set_defaults(function=merge)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
