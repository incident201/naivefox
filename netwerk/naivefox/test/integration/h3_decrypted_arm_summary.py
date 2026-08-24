#!/usr/bin/env python3

import argparse
import csv
from collections import defaultdict
from pathlib import Path

SUPPORTED_ARMS = (
    "off",
    "gate",
    "root",
    "document-complete",
    "tree-complete",
    "tree-complete-css",
    "tree-early-overlap",
    "tree-root-overlap",
    "tree-root-overlap-css",
    "tree-overlap",
)
REDACTED_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}
PRIVATE_VALUE_SEPARATOR = "\x1f"
SELECTED_GET_SEMANTIC_HEADERS = {
    ":method",
    ":scheme",
    ":authority",
    ":path",
    "referer",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "priority",
}
REQUIRED_GET_PSEUDO_HEADERS = {":method", ":scheme", ":authority", ":path"}


def split_values(value):
    return [item for item in value.split(";") if item]


def read_rows(root, cohort, suffix):
    path = root / f"decrypted-{cohort}-{suffix}.csv"
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def direction(row, proxy_port):
    if row["udp.dstport"] == proxy_port:
        return "client"
    if row["udp.srcport"] == proxy_port:
        return "server"
    raise ValueError("decrypted event is outside the outer proxy flow")


def safe_header_name(name):
    lowered = name.lower()
    if lowered in REDACTED_HEADER_NAMES:
        return "auth-or-cookie-redacted"
    return lowered


def private_values(value):
    return value.split(PRIVATE_VALUE_SEPARATOR) if value else []


def split_private_header_blocks(names, values, marker, count, context):
    require(
        len(names) == len(values) and bool(names),
        f"{context} header name/value alignment is ambiguous",
    )
    starts = [index for index, name in enumerate(names) if name == marker]
    require(
        len(starts) == count and starts[0] == 0,
        f"{context} header block cardinality is ambiguous",
    )
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(names)
        block = tuple(zip(names[start:end], values[start:end]))
        require(
            bool(block) and block[0][0] == marker,
            f"{context} header block boundary is ambiguous",
        )
        blocks.append(block)
    return blocks


def split_header_name_blocks(names, marker, count, context):
    starts = [index for index, name in enumerate(names) if name == marker]
    require(
        bool(names) and len(starts) == count and starts[0] == 0,
        f"{context} header block cardinality is ambiguous",
    )
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(names)
        block = names[start:end]
        require(
            bool(block) and block[0] == marker,
            f"{context} header block boundary is ambiguous",
        )
        blocks.append(block)
    return blocks


def ordered_unique(values):
    return list(dict.fromkeys(values))


def require_one_connection(connections, count, context):
    require(
        len(connections) in (1, count)
        and len(set(connections)) == 1
        and bool(connections[0]),
        f"{context} connection alignment is ambiguous",
    )


def read_http3_events(request_rows, header_rows, cohort, proxy_port):
    """Map packet-scoped tshark fields to individual H3 HEADERS streams."""
    headers_by_packet = {}
    for row in header_rows:
        key = (int(row["frame.number"]), direction(row, proxy_port))
        require(
            key not in headers_by_packet,
            f"{cohort} has duplicate packet-level header extracts",
        )
        headers_by_packet[key] = row

    seen_streams = defaultdict(set)
    events = {}
    used_header_packets = set()
    for row in sorted(request_rows, key=lambda item: int(item["frame.number"])):
        event_direction = direction(row, proxy_port)
        frame = int(row["frame.number"])
        event_time = float(row["frame.time_relative"])
        key = (frame, event_direction)
        require(
            key in headers_by_packet,
            f"{cohort} request/response event lacks a header-name extract",
        )
        header_row = headers_by_packet[key]

        streams = ordered_unique(split_values(row["quic.stream.stream_id"]))
        header_streams = ordered_unique(
            split_values(header_row["quic.stream.stream_id"])
        )
        require(
            bool(streams) and streams == header_streams,
            f"{cohort} H3 header stream identity is ambiguous",
        )
        connections = split_values(row["quic.connection.number"])
        header_connections = split_values(header_row["quic.connection.number"])
        require_one_connection(
            connections, len(streams), f"{cohort} H3 event"
        )
        require_one_connection(
            header_connections,
            len(header_streams),
            f"{cohort} H3 header extract",
        )
        connection = connections[0]
        require(
            set(header_connections) == {connection},
            f"{cohort} H3 header connection identity is ambiguous",
        )
        methods = split_values(row["http3.headers.method"])
        statuses = split_values(row["http3.headers.status"])
        require(
            bool(methods) != bool(statuses),
            f"{cohort} H3 event method/status semantics are ambiguous",
        )
        values = methods if methods else statuses
        marker = ":method" if methods else ":status"
        names = [
            safe_header_name(name)
            for name in split_values(header_row["http3.header.header.name"])
        ]
        blocks = split_header_name_blocks(
            names, marker, len(values), f"{cohort} H3 event"
        )
        candidate_streams = [
            stream
            for stream in streams
            if stream not in seen_streams[(event_direction, connection)]
        ]
        require(
            len(candidate_streams) == len(blocks),
            f"{cohort} H3 HEADERS/stream mapping is ambiguous",
        )

        for stream, value, block in zip(candidate_streams, values, blocks):
            seen_streams[(event_direction, connection)].add(stream)
            method = value if methods else ""
            status = value if statuses else ""
            event_key = (event_direction, connection, stream, method, status)
            require(
                event_key not in events,
                f"{cohort} has duplicate H3 HEADERS evidence",
            )
            events[event_key] = {
                "frame": frame,
                "time": event_time,
                "header_order": tuple(ordered_unique(block)),
                "header_set": frozenset(block),
            }
        used_header_packets.add(key)

    for key, row in headers_by_packet.items():
        names = [
            safe_header_name(name)
            for name in split_values(row["http3.header.header.name"])
        ]
        if ":method" in names or ":status" in names:
            require(
                key in used_header_packets,
                f"{cohort} header extract lacks request/response evidence",
            )
    return events


def read_get_request_semantics(root, cohort, proxy_port):
    rows = read_rows(root, cohort, "get-header-values")
    require(rows, f"{cohort} has no private GET header blocks")
    blocks = []
    for row in rows:
        require(
            direction(row, proxy_port) == "client",
            f"{cohort} private GET header extract contains a server block",
        )
        methods = private_values(row["http3.headers.method"])
        connections = private_values(row["quic.connection.number"])
        streams = private_values(row["quic.stream.stream_id"])
        names = [
            name.lower() for name in private_values(row["http3.header.header.name"])
        ]
        values = private_values(row["http3.headers.header.value"])
        require(
            bool(methods)
            and len(methods) == len(streams)
            and all(method == "GET" for method in methods),
            f"{cohort} private GET method/stream cardinality is ambiguous",
        )
        require_one_connection(
            connections, len(streams), f"{cohort} private GET header block"
        )
        header_blocks = split_private_header_blocks(
            names,
            values,
            ":method",
            len(streams),
            f"{cohort} private GET",
        )
        for stream, method, block in zip(streams, methods, header_blocks):
            block_names = [name for name, _ in block]
            require(
                not REDACTED_HEADER_NAMES.intersection(block_names),
                f"{cohort} private GET header block contains auth or cookie semantics",
            )
            require(
                all(
                    block_names.count(name) == 1 for name in REQUIRED_GET_PSEUDO_HEADERS
                ),
                f"{cohort} private GET block lacks required pseudo-header semantics",
            )
            selected = tuple(
                (name, value)
                for name, value in block
                if name in SELECTED_GET_SEMANTIC_HEADERS
            )
            selected_names = [name for name, _ in selected]
            require(
                selected_names.count(":method") == 1
                and dict(selected)[":method"] == method,
                f"{cohort} private GET block has invalid :method semantics",
            )
            try:
                numeric_stream = int(stream, 0)
            except ValueError as error:
                raise ValueError(
                    f"{cohort} private GET stream id is not numeric"
                ) from error
            blocks.append((int(row["frame.number"]), numeric_stream, selected))
    roles = (
        ("root", "stylesheet")
        if cohort in ("tree-complete-css", "tree-root-overlap-css")
        else ("root", "stylesheet", "script")
    )
    require(
        len(blocks) == len(roles),
        f"{cohort} private GET semantics have the wrong resource count",
    )
    require(
        len({stream for _, stream, _ in blocks}) == len(blocks),
        f"{cohort} private GET semantics contain duplicate streams",
    )
    blocks.sort(key=lambda item: (item[0], item[1]))
    return {
        role: semantics
        for role, (_, _, semantics) in zip(roles, blocks)
    }


def read_response_content_lengths(root, cohort, proxy_port):
    rows = read_rows(root, cohort, "response-header-values")
    lengths = {}
    mapped_streams = set()
    for row in rows:
        require(
            direction(row, proxy_port) == "server",
            f"{cohort} private response header extract contains a client block",
        )
        statuses = private_values(row["http3.headers.status"])
        connections = private_values(row["quic.connection.number"])
        streams = private_values(row["quic.stream.stream_id"])
        names = [
            name.lower() for name in private_values(row["http3.header.header.name"])
        ]
        values = private_values(row["http3.headers.header.value"])
        # CONNECT response packets can carry DATA for another stream, which
        # makes tshark's packet-level status/stream columns intentionally
        # ambiguous.  They are irrelevant to the asset-size invariant.  Apply
        # the strict one-block-per-stream rule only to packets that actually
        # expose a Content-Length value we intend to retain.
        if "content-length" not in names:
            continue
        candidate_streams = [
            stream for stream in streams if stream not in mapped_streams
        ]
        require(
            bool(statuses)
            and len(statuses) == len(candidate_streams)
            and all(status == "200" for status in statuses),
            f"{cohort} private response status/stream cardinality is ambiguous",
        )
        require_one_connection(
            connections,
            len(streams),
            f"{cohort} private response header block",
        )
        header_blocks = split_private_header_blocks(
            names,
            values,
            ":status",
            len(statuses),
            f"{cohort} private response",
        )
        # tshark flattens fields from every H3 stream carried by a packet.  A
        # stream whose response HEADERS were already mapped can only be the
        # DATA-only companion here.  Remove those known streams, then require
        # an exact one-to-one mapping between the remaining stream occurrences
        # and new HEADERS blocks.  A previously unseen DATA stream therefore
        # remains ambiguous and is rejected.
        for stream, status, block in zip(candidate_streams, statuses, header_blocks):
            require(
                stream not in mapped_streams,
                f"{cohort} has duplicate mapped response stream",
            )
            mapped_streams.add(stream)
            block_names = [name for name, _ in block]
            require(
                not REDACTED_HEADER_NAMES.intersection(block_names),
                f"{cohort} private response block contains auth or cookie semantics",
            )
            require(
                block_names.count(":status") == 1 and dict(block)[":status"] == status,
                f"{cohort} private response block has invalid :status semantics",
            )
            selected = [value for name, value in block if name == "content-length"]
            if not selected:
                continue
            require(
                len(selected) == 1 and selected[0].isdigit(),
                f"{cohort} response has invalid content-length semantics",
            )
            # Every validated arm is confined to one physical outer
            # connection, so the H3 stream id is the stable key shared with
            # the sanitized events.
            require(
                stream not in lengths,
                f"{cohort} has duplicate response size evidence",
            )
            lengths[stream] = int(selected[0])
    return lengths


def validate_expected_get_request_semantics(cohort, semantics):
    expected = {
        "root": {
            ":path": "/camouflage/index.html",
            "sec-fetch-site": "none",
            "sec-fetch-mode": "navigate",
            "sec-fetch-dest": "document",
        },
        "stylesheet": {
            ":path": "/camouflage/style.css",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-dest": "style",
        },
        "script": {
            ":path": "/camouflage/app.js",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-dest": "script",
        },
    }
    root_values = dict(semantics["root"])
    root_url = (
        f"{root_values[':scheme']}://{root_values[':authority']}{root_values[':path']}"
    )
    for role in semantics:
        expected_values = expected[role]
        selected = semantics[role]
        names = [name for name, _ in selected]
        require(
            len(names) == len(set(names)),
            f"{cohort} {role} selected request semantics contain duplicates",
        )
        actual = dict(selected)
        require(
            all(actual.get(name) == value for name, value in expected_values.items()),
            f"{cohort} {role} expected request semantics differ",
        )
        referer = actual.get("referer", "")
        if role == "root":
            require(
                not referer,
                f"{cohort} root GET unexpectedly has referer semantics",
            )
        else:
            require(
                referer == root_url,
                f"{cohort} {role} GET referer does not equal the computed root URL",
            )


def summarize_cohort(root, cohort, proxy_port):
    request_rows = read_rows(root, cohort, "requests")
    header_rows = read_rows(root, cohort, "header-names")
    packet_rows = read_rows(root, cohort, "packets")
    lifecycle_rows = read_rows(root, cohort, "lifecycle")
    clienthello_rows = read_rows(root, cohort, "clienthello")
    if not packet_rows:
        raise ValueError(f"{cohort} has no decrypted QUIC packets")

    first_packet_time = min(float(row["frame.time_relative"]) for row in packet_rows)
    connection_first_frame = {}
    for row in packet_rows:
        packet_connections = ordered_unique(
            split_values(row["quic.connection.number"])
        )
        require(
            len(packet_connections) == 1,
            f"{cohort} QUIC packet connection identity is ambiguous",
        )
        connection = packet_connections[0]
        frame = int(row["frame.number"])
        connection_first_frame[connection] = min(
            frame, connection_first_frame.get(connection, frame)
        )
    connection_indices = {
        connection: index
        for index, (connection, _) in enumerate(
            sorted(connection_first_frame.items(), key=lambda item: item[1]), start=1
        )
    }

    stream_fin_positions = {}
    for row in lifecycle_rows:
        fin_values = [
            value.lower() for value in split_values(row["quic.stream.fin"])
        ]
        if not fin_values:
            continue
        streams = split_values(row["quic.stream.stream_id"])
        connections = split_values(row["quic.connection.number"])
        require(
            bool(streams) and len(streams) == len(fin_values),
            f"{cohort} stream FIN/stream cardinality is ambiguous",
        )
        require(
            all(value in {"0", "false", "1", "true"} for value in fin_values),
            f"{cohort} stream FIN value is ambiguous",
        )
        require_one_connection(
            connections, len(streams), f"{cohort} stream FIN event"
        )
        if len(connections) == 1:
            connections = connections * len(streams)
        event_direction = direction(row, proxy_port)
        frame = int(row["frame.number"])
        for connection, stream, fin in zip(connections, streams, fin_values):
            if fin not in {"1", "true"}:
                continue
            key = (event_direction, connection, stream)
            stream_fin_positions[key] = max(
                frame, stream_fin_positions.get(key, frame)
            )

    events = read_http3_events(request_rows, header_rows, cohort, proxy_port)
    if not events:
        raise ValueError(f"{cohort} has no decrypted H3 request/response events")

    first_http3_time = min(event["time"] for event in events.values())
    output = []
    for key, event in sorted(
        events.items(),
        key=lambda item: (item[1]["frame"], item[1]["time"], item[0]),
    ):
        event_direction, connection, stream, method, status = key
        frame = event["frame"]
        event_time = event["time"]
        output.append({
            "cohort": cohort,
            "event_ordinal": len(output) + 1,
            "direction": event_direction,
            "packet_position": frame,
            "time_from_first_packet_ms": f"{(event_time - first_packet_time) * 1000:.3f}",
            "time_from_first_h3_event_ms": f"{(event_time - first_http3_time) * 1000:.3f}",
            "connection_index": connection_indices[connection],
            "stream_id": stream,
            "method": method,
            "status": status,
            "header_name_order": ";".join(event["header_order"]),
            "header_name_set": ";".join(sorted(event["header_set"])),
            "stream_fin_packet_position": stream_fin_positions.get(
                (event_direction, connection, stream), ""
            ),
        })
    clienthello_connections = set()
    for row in clienthello_rows:
        row_connections = ordered_unique(
            split_values(row["quic.connection.number"])
        )
        require(
            len(row_connections) == 1,
            f"{cohort} outer ClientHello connection identity is ambiguous",
        )
        require(
            row_connections[0] in connection_indices,
            f"{cohort} outer ClientHello has an unknown connection identity",
        )
        clienthello_connections.add(row_connections[0])
    return output, len(connection_indices), len(clienthello_connections)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(cohorts, connections, client_hellos, arms):
    require(
        connections["reference"] == 1,
        "reference must use exactly one physical outer QUIC connection",
    )
    require(
        client_hellos["reference"] == 1,
        "reference must emit exactly one unique outer ClientHello",
    )
    reference_methods = [row for row in cohorts["reference"] if row["method"]]
    require(
        any(row["method"] == "GET" for row in reference_methods),
        "reference has no decrypted outer GET",
    )
    require(
        not any(row["method"] == "CONNECT" for row in reference_methods),
        "reference unexpectedly used outer CONNECT",
    )
    reference_gets = [row for row in reference_methods if row["method"] == "GET"]
    require(
        all("alt-used" in row["header_name_set"].split(";") for row in reference_gets),
        "reference GET is missing fixture Alt-Used",
    )
    for arm in arms:
        if arm != "off":
            require(
                connections[arm] == 1,
                f"{arm} must use exactly one physical outer QUIC connection",
            )
            require(
                client_hellos[arm] == 1,
                f"{arm} must emit exactly one unique outer ClientHello",
            )
        requests = [row for row in cohorts[arm] if row["direction"] == "client"]
        connects = [row for row in requests if row["method"] == "CONNECT"]
        require(connects, f"{arm} has no decrypted outer CONNECT")
        for connect in connects:
            require(
                "padding" in connect["header_name_set"].split(";"),
                f"{arm} CONNECT has no request padding header",
            )
            responses = [
                row
                for row in cohorts[arm]
                if row["direction"] == "server"
                and row["connection_index"] == connect["connection_index"]
                and row["stream_id"] == connect["stream_id"]
            ]
            require(
                any(row["status"] == "200" for row in responses),
                f"{arm} CONNECT has no successful response",
            )
            require(
                any(
                    "padding" in row["header_name_set"].split(";") for row in responses
                ),
                f"{arm} CONNECT has no response padding header",
            )
        gets = [row for row in requests if row["method"] == "GET"]
        if arm in (
            "root",
            "document-complete",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-overlap",
        ):
            expected_gets = (
                2
                if arm in ("tree-complete-css", "tree-root-overlap-css")
                else 3 if arm.startswith("tree-") else 1
            )
            require(
                len(gets) == expected_gets,
                f"{arm} must emit exactly {expected_gets} outer GET requests",
            )
            for get in gets:
                require(
                    "alt-used" not in get["header_name_set"].split(";"),
                    f"{arm} preamble GET unexpectedly used fixture Alt-Svc mapping",
                )
                require(
                    get["packet_position"] < connects[0]["packet_position"],
                    f"{arm} GET did not precede CONNECT",
                )
                require(
                    get["connection_index"] == connects[0]["connection_index"],
                    f"{arm} GET and CONNECT used different QUIC connections",
                )
                responses = [
                    row
                    for row in cohorts[arm]
                    if row["direction"] == "server"
                    and row["connection_index"] == get["connection_index"]
                    and row["stream_id"] == get["stream_id"]
                ]
                require(
                    any(row["status"] == "200" for row in responses),
                    f"{arm} preamble GET has no successful response",
                )
            response_headers = [
                row
                for row in cohorts[arm]
                if row["direction"] == "server"
                and row["status"] == "200"
                and any(
                    row["connection_index"] == get["connection_index"]
                    and row["stream_id"] == get["stream_id"]
                    for get in gets
                )
            ]
            if arm in (
                "root",
                "document-complete",
                "tree-complete",
                "tree-complete-css",
            ):
                require(
                    all(
                        row["packet_position"] < connects[0]["packet_position"]
                        for row in response_headers
                    ),
                    f"{arm} CONNECT preceded a preamble response header",
                )
                if arm in ("root", "document-complete"):
                    observed_fins = [
                        row["stream_fin_packet_position"]
                        for row in response_headers
                        if row["stream_fin_packet_position"] != ""
                    ]
                    if observed_fins:
                        require(
                            all(
                                position < connects[0]["packet_position"]
                                for position in observed_fins
                            ),
                            f"{arm} CONNECT preceded an observed preamble stream FIN",
                        )
            if arm.startswith("tree-"):
                ordered_gets = sorted(gets, key=lambda row: row["packet_position"])
                asset_streams = {
                    (row["connection_index"], row["stream_id"])
                    for row in ordered_gets[1:]
                }
                asset_responses = [
                    row
                    for row in response_headers
                    if (row["connection_index"], row["stream_id"]) in asset_streams
                ]
                expected_assets = 1 if arm.endswith("-css") else 2
                require(
                    len(asset_responses) == expected_assets,
                    f"{arm} lacks one or more asset response headers",
                )
                if arm in ("tree-complete", "tree-complete-css"):
                    observed_asset_fins = [
                        row["stream_fin_packet_position"]
                        for row in asset_responses
                        if row["stream_fin_packet_position"] != ""
                    ]
                    require(
                        len(observed_asset_fins) == len(asset_responses),
                        "tree-complete lacks an observed FIN for every asset stream",
                    )
                    require(
                        all(
                            position < connects[0]["packet_position"]
                            for position in observed_asset_fins
                        ),
                        "tree-complete asset stream FIN did not precede CONNECT",
                    )
                elif arm == "tree-early-overlap":
                    root_stream = (
                        ordered_gets[0]["connection_index"],
                        ordered_gets[0]["stream_id"],
                    )
                    root_responses = [
                        row
                        for row in response_headers
                        if (row["connection_index"], row["stream_id"]) == root_stream
                    ]
                    require(
                        root_responses
                        and all(
                            row["stream_fin_packet_position"] != ""
                            and row["stream_fin_packet_position"]
                            < connects[0]["packet_position"]
                            for row in root_responses
                        ),
                        "tree-early-overlap root FIN did not precede CONNECT",
                    )
                    require(
                        any(
                            row["packet_position"]
                            < connects[0]["packet_position"]
                            < row["stream_fin_packet_position"]
                            for row in asset_responses
                            if row["stream_fin_packet_position"] != ""
                        ),
                        "tree-early-overlap lacks resource HEADERS < CONNECT < same-resource FIN evidence",
                    )
                elif arm in ("tree-root-overlap", "tree-root-overlap-css"):
                    root_stream = (
                        ordered_gets[0]["connection_index"],
                        ordered_gets[0]["stream_id"],
                    )
                    root_responses = [
                        row
                        for row in response_headers
                        if (row["connection_index"], row["stream_id"])
                        == root_stream
                    ]
                    require(
                        root_responses
                        and all(
                            row["stream_fin_packet_position"] != ""
                            and row["stream_fin_packet_position"]
                            < connects[0]["packet_position"]
                            for row in root_responses
                        ),
                        "tree-root-overlap root FIN did not precede CONNECT",
                    )
                    require(
                        all(
                            row["stream_fin_packet_position"] != ""
                            for row in asset_responses
                        ),
                        "tree-root-overlap lacks an observed FIN for every asset stream",
                    )
                elif arm == "tree-overlap":
                    require(
                        any(
                            row["packet_position"]
                            < connects[0]["packet_position"]
                            < row["stream_fin_packet_position"]
                            for row in asset_responses
                            if row["stream_fin_packet_position"] != ""
                        ),
                        "tree-overlap lacks resource HEADERS < CONNECT < resource FIN evidence",
                    )
        else:
            require(not gets, f"{arm} unexpectedly emitted an outer GET")
    if {"tree-complete", "tree-overlap"}.issubset(arms):
        complete_gets = sorted(
            (
                row
                for row in cohorts["tree-complete"]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        overlap_gets = sorted(
            (
                row
                for row in cohorts["tree-overlap"]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        request_roles = ("root", "stylesheet", "script")
        for role, complete, overlap in zip(request_roles, complete_gets, overlap_gets):
            require(
                complete["header_name_order"] == overlap["header_name_order"]
                and complete["header_name_set"] == overlap["header_name_set"],
                f"tree {role} GET request semantics differ between complete and overlap",
            )
    if {"tree-complete", "tree-early-overlap"}.issubset(arms):
        complete_gets = sorted(
            (
                row
                for row in cohorts["tree-complete"]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        early_gets = sorted(
            (
                row
                for row in cohorts["tree-early-overlap"]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        for role, complete, early in zip(
            ("root", "stylesheet", "script"), complete_gets, early_gets
        ):
            require(
                complete["header_name_order"] == early["header_name_order"]
                and complete["header_name_set"] == early["header_name_set"],
                f"tree {role} GET request semantics differ between complete and early-overlap",
            )
    if {"tree-complete", "tree-root-overlap"}.issubset(arms):
        complete_gets = sorted(
            (
                row
                for row in cohorts["tree-complete"]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        root_overlap_gets = sorted(
            (
                row
                for row in cohorts["tree-root-overlap"]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        for role, complete, root_overlap in zip(
            ("root", "stylesheet", "script"), complete_gets, root_overlap_gets
        ):
            require(
                complete["header_name_order"] == root_overlap["header_name_order"]
                and complete["header_name_set"] == root_overlap["header_name_set"],
                f"tree {role} GET request semantics differ between complete and root-overlap",
            )
    for arm in arms:
        if arm != "off":
            require(
                connections[arm] == 1,
                f"{arm} did not use exactly one physical QUIC connection",
            )


def write_outputs(root, events_path, summary_path, proxy_port, arms):
    arms = tuple(arms)
    require(bool(arms), "at least one NaiveFox arm is required")
    require(len(set(arms)) == len(arms), "NaiveFox arms must be unique")
    require(
        all(arm in SUPPORTED_ARMS for arm in arms),
        "unsupported NaiveFox arm",
    )
    require(
        "tree-early-overlap" not in arms or "tree-complete" in arms,
        "tree-early-overlap decrypted validation requires tree-complete",
    )
    require(
        "tree-root-overlap" not in arms or "tree-complete" in arms,
        "tree-root-overlap decrypted validation requires tree-complete",
    )
    require(
        "tree-root-overlap-css" not in arms or "tree-complete-css" in arms,
        "tree-root-overlap-css decrypted validation requires tree-complete-css",
    )
    cohorts_to_read = ("reference", *arms)
    cohorts = {}
    connections = {}
    client_hellos = {}
    for cohort in cohorts_to_read:
        (
            cohorts[cohort],
            connections[cohort],
            client_hellos[cohort],
        ) = summarize_cohort(root, cohort, proxy_port)
    validate(cohorts, connections, client_hellos, arms)
    tree_semantics = {}
    tree_asset_sizes = {}
    for arm in arms:
        if not arm.startswith("tree-"):
            continue
        tree_semantics[arm] = read_get_request_semantics(root, arm, proxy_port)
        validate_expected_get_request_semantics(arm, tree_semantics[arm])
        response_lengths = read_response_content_lengths(root, arm, proxy_port)
        tree_gets = sorted(
            (
                row
                for row in cohorts[arm]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        asset_streams = [row["stream_id"] for row in tree_gets[1:]]
        require(
            all(stream in response_lengths for stream in asset_streams),
            f"{arm} lacks asset content-length evidence",
        )
        tree_asset_sizes[arm] = tuple(
            response_lengths[stream] for stream in asset_streams
        )
    if {"tree-complete", "tree-overlap"}.issubset(arms):
        for role in ("root", "stylesheet", "script"):
            require(
                tree_semantics["tree-complete"][role]
                == tree_semantics["tree-overlap"][role],
                f"tree {role} GET selected header values/order differ between complete and overlap",
            )
    if {"tree-complete", "tree-early-overlap"}.issubset(arms):
        for role in ("root", "stylesheet", "script"):
            require(
                tree_semantics["tree-complete"][role]
                == tree_semantics["tree-early-overlap"][role],
                f"tree {role} GET selected header values/order differ between complete and early-overlap",
            )
        require(
            tree_asset_sizes["tree-complete"] == tree_asset_sizes["tree-early-overlap"],
            "tree asset content-lengths differ between complete and early-overlap",
        )
    if {"tree-complete", "tree-root-overlap"}.issubset(arms):
        for role in ("root", "stylesheet", "script"):
            require(
                tree_semantics["tree-complete"][role]
                == tree_semantics["tree-root-overlap"][role],
                f"tree {role} GET selected header values/order differ between complete and root-overlap",
            )
        require(
            tree_asset_sizes["tree-complete"]
            == tree_asset_sizes["tree-root-overlap"],
            "tree asset content-lengths differ between complete and root-overlap",
        )
    if {"tree-complete-css", "tree-root-overlap-css"}.issubset(arms):
        for role in ("root", "stylesheet"):
            require(
                tree_semantics["tree-complete-css"][role]
                == tree_semantics["tree-root-overlap-css"][role],
                f"tree CSS-only {role} GET selected header values/order differ",
            )
        require(
            tree_asset_sizes["tree-complete-css"]
            == tree_asset_sizes["tree-root-overlap-css"],
            "tree CSS-only asset content-lengths differ",
        )
    fieldnames = [
        "cohort",
        "event_ordinal",
        "direction",
        "packet_position",
        "time_from_first_packet_ms",
        "time_from_first_h3_event_ms",
        "connection_index",
        "stream_id",
        "method",
        "status",
        "header_name_order",
        "header_name_set",
        "stream_fin_packet_position",
    ]
    with events_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        for cohort in cohorts_to_read:
            writer.writerows(cohorts[cohort])
    with summary_path.open("w", encoding="utf-8") as destination:
        destination.write("capture_scope=same_base_h3_decrypted_outer_sequence\n")
        destination.write("inner_transport=https\n")
        destination.write(f"cohorts=reference,{','.join(arms)}\n")
        destination.write("header_values_retained=no\n")
        destination.write("credential_header_names_redacted=yes\n")
        for cohort in cohorts_to_read:
            requests = [row for row in cohorts[cohort] if row["method"]]
            destination.write(f"{cohort}_quic_connections={connections[cohort]}\n")
            destination.write(f"{cohort}_outer_client_hellos={client_hellos[cohort]}\n")
            destination.write(
                f"{cohort}_request_sequence="
                + ",".join(
                    f"{row['method']}@stream{row['stream_id']}" for row in requests
                )
                + "\n"
            )
            for method in ("GET", "CONNECT"):
                selected = [row for row in requests if row["method"] == method]
                if selected:
                    destination.write(
                        f"{cohort}_first_{method.lower()}_packet_position="
                        f"{selected[0]['packet_position']}\n"
                    )
                    destination.write(
                        f"{cohort}_first_{method.lower()}_time_from_first_packet_ms="
                        f"{selected[0]['time_from_first_packet_ms']}\n"
                    )
            if cohort in arms and cohort not in ("off", "gate"):
                preamble_mode = "document-complete" if cohort == "root" else cohort
                gets = [row for row in requests if row["method"] == "GET"]
                connects = [row for row in requests if row["method"] == "CONNECT"]
                connect_position = connects[0]["packet_position"]
                responses = [
                    row
                    for row in cohorts[cohort]
                    if row["direction"] == "server"
                    and row["status"] == "200"
                    and any(
                        row["connection_index"] == get["connection_index"]
                        and row["stream_id"] == get["stream_id"]
                        for get in gets
                    )
                ]
                response_positions = [row["packet_position"] for row in responses]
                fin_positions = [
                    row["stream_fin_packet_position"]
                    for row in responses
                    if row["stream_fin_packet_position"] != ""
                ]
                header_overlap = any(
                    position > connect_position for position in response_positions
                )
                fin_overlap = any(
                    position > connect_position for position in fin_positions
                )
                resource_stream_overlap = False
                if cohort in (
                    "tree-early-overlap",
                    "tree-root-overlap",
                    "tree-root-overlap-css",
                    "tree-overlap",
                ):
                    ordered_gets = sorted(gets, key=lambda row: row["packet_position"])
                    asset_streams = {
                        (row["connection_index"], row["stream_id"])
                        for row in ordered_gets[1:]
                    }
                    resource_stream_overlap = any(
                        (row["connection_index"], row["stream_id"]) in asset_streams
                        and row["packet_position"]
                        < connect_position
                        < row["stream_fin_packet_position"]
                        for row in responses
                        if row["stream_fin_packet_position"] != ""
                    )
                destination.write(f"{cohort}_preamble_mode={preamble_mode}\n")
                destination.write(f"{cohort}_outer_get_count={len(gets)}\n")
                destination.write(
                    f"{cohort}_connect_after_all_get_requests="
                    f"{'yes' if all(row['packet_position'] < connect_position for row in gets) else 'no'}\n"
                )
                destination.write(
                    f"{cohort}_connect_after_all_response_headers="
                    f"{'yes' if all(position < connect_position for position in response_positions) else 'no'}\n"
                )
                fin_order = "unknown"
                if fin_positions:
                    fin_order = (
                        "yes"
                        if all(
                            position < connect_position for position in fin_positions
                        )
                        else "no"
                    )
                destination.write(
                    f"{cohort}_connect_after_all_observed_server_fins={fin_order}\n"
                )
                evidence = []
                if header_overlap:
                    evidence.append("response-header-after-connect")
                if fin_overlap:
                    evidence.append("server-fin-after-connect")
                if resource_stream_overlap:
                    evidence.append("resource-stream-spans-connect")
                destination.write(
                    f"{cohort}_overlap_observed={'yes' if evidence else 'no'}\n"
                )
                destination.write(
                    f"{cohort}_overlap_evidence="
                    f"{','.join(evidence) if evidence else 'none-observed'}\n"
                )
        if {"tree-complete", "tree-overlap"}.issubset(arms):
            destination.write("tree_request_semantics_match=yes\n")
        if {"tree-complete", "tree-early-overlap"}.issubset(arms):
            destination.write("tree_early_overlap_request_semantics_match=yes\n")
            destination.write("tree_early_overlap_asset_sizes_match=yes\n")
        if {"tree-complete", "tree-root-overlap"}.issubset(arms):
            destination.write("tree_root_overlap_request_semantics_match=yes\n")
            destination.write("tree_root_overlap_asset_sizes_match=yes\n")
            destination.write("tree_root_overlap_wire_overlap_is_admission=no\n")
        if {"tree-complete-css", "tree-root-overlap-css"}.issubset(arms):
            destination.write(
                "tree_root_overlap_css_request_semantics_match=yes\n"
            )
            destination.write("tree_root_overlap_css_asset_sizes_match=yes\n")
            destination.write(
                "tree_root_overlap_css_wire_overlap_is_admission=no\n"
            )
        if any(arm.startswith("tree-") for arm in arms):
            destination.write("tree_expected_request_semantics=yes\n")
        destination.write("raw_capture_material=deleted_after_success\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--proxy-port", required=True)
    parser.add_argument(
        "--arms",
        default="off,gate,root,tree-complete,tree-overlap",
    )
    args = parser.parse_args()
    write_outputs(
        args.input_dir,
        args.events,
        args.summary,
        args.proxy_port,
        [arm for arm in args.arms.split(",") if arm],
    )


if __name__ == "__main__":
    main()
