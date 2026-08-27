#!/usr/bin/env python3

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

SUPPORTED_ARMS = (
    "off",
    "gate",
    "root",
    "root-pmtud-control",
    "document-complete",
    "document-carrier-dispatch",
    "document-cold-winner-handoff",
    "document-native-cache-open",
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
    "tree-native-parser-document-start-overlap-css",
    "tree-native-parser-document-start-navigation-stop-css",
    "tree-native-parser-document-start-response-stop-css",
    "tree-native-parser-document-handoff-overlap-css",
    "tree-native-parser-retarget-overlap-css",
    "tree-native-parser-ipc-rendezvous-overlap-css",
    "tree-native-parser-root-rendezvous-overlap-css",
    "tree-native-parser-process-overlap-css",
    "tree-native-parser-full-process-overlap-css",
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
ROOT_PATH_PATTERN = re.compile(
    r"/camouflage/index\.html\?scenario=browser_page&size=262144&count=4"
    r"&idle_ms=5000&completion=[0-9a-f]{32}"
)
CONFIRMED_LIFECYCLE_PATTERNS = {
    "connected": re.compile(
        r"h3\.state session=(\S+) ci=(\S+) .*cause=connected(?:\s|$)"
    ),
    "wait": re.compile(
        r"h3\.preamble_confirm_gate action=wait session=(\S+) ci=(\S+) "
        r"transport_confirmed=0(?:\s|$)"
    ),
    "observed": re.compile(
        r"h3\.transport_confirmation action=observed session=(\S+) ci=(\S+) "
        r"transport_confirmed=1(?:\s|$)"
    ),
    "release": re.compile(
        r"h3\.preamble_confirm_gate action=release session=(\S+) ci=(\S+) "
        r"transport_confirmed=1(?:\s|$)"
    ),
}
CARRIER_DISPATCH_LIFECYCLE_PATTERNS = {
    "created": re.compile(
        r"h3\.carrier_dispatch action=carrier-created gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) ci=(?P<ci>\S+) use_he=0 "
        r"fetch_https_rr=0 parallel_limit=1\s*$"
    ),
    "establishment": re.compile(
        r"h3\.carrier_dispatch action=carrier-establishment-start "
        r"gate=(?P<gate>\S+) carrier=(?P<carrier>\S+) ci=(?P<ci>\S+)\s*$"
    ),
    "configured": re.compile(
        r"h3\.carrier_dispatch action=document-configured gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) document=(?P<document>\S+) "
        r"ci=(?P<ci>\S+) caps=[0-9a-fA-F]+\s*$"
    ),
    "waiting": re.compile(
        r"h3\.carrier_dispatch action=document-waiting gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) document=(?P<document>\S+) "
        r"carrier_complete=0(?: path=spdy-pending)?\s*$"
    ),
    "carrier_activated": re.compile(
        r"h3\.carrier_dispatch action=carrier-activated connection=\S+ "
        r"session=(?P<session>\S+) carrier=(?P<carrier>\S+) connected=0\s*$"
    ),
    "connection_connected": re.compile(
        r"h3\.carrier_dispatch action=connection-connected connection=\S+ "
        r"session=(?P<session>\S+)\s*$"
    ),
    "read": re.compile(
        r"h3\.carrier_dispatch action=carrier-read-complete gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) request_bytes=0 "
        r"result=base-stream-closed rv=(?P<rv>[0-9a-fA-F]+)\s*$"
    ),
    "complete": re.compile(
        r"h3\.carrier_dispatch action=carrier-complete gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) result=00000000 "
        r"carrier_read_complete=1\s*$"
    ),
    "dispatch": re.compile(
        r"h3\.carrier_dispatch action=document-normal-dispatch "
        r"gate=(?P<gate>\S+) carrier=(?P<carrier>\S+) "
        r"document=(?P<document>\S+) carrier_complete=1"
        r"(?: path=spdy-pending)?\s*$"
    ),
    "document_activated": re.compile(
        r"h3\.carrier_dispatch action=document-activated gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) connection=\S+ "
        r"session=(?P<session>\S+) document=(?P<document>\S+) "
        r"connected=1 wildcard=[01]\s*$"
    ),
    "document_attached": re.compile(
        r"h3\.carrier_dispatch action=document-attached gate=(?P<gate>\S+) "
        r"carrier=(?P<carrier>\S+) session=(?P<session>\S+) "
        r"document=(?P<document>\S+) via=add-stream carrier_complete=1\s*$"
    ),
    "headers": re.compile(
        r"h3\.carrier_dispatch action=document-headers-emitted "
        r"gate=(?P<gate>\S+) carrier=(?P<carrier>\S+) "
        r"session=(?P<session>\S+) document=(?P<document>\S+) "
        r"stream_id=(?P<stream_id>\d+) carrier_complete=1\s*$"
    ),
}
CARRIER_CONNECTED_PATTERN = re.compile(
    r"h3\.state session=(?P<session>\S+) ci=(?P<ci>\S+) .*"
    r"cause=connected(?:\s|$)"
)


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


def numeric_field(value, context):
    try:
        return int(value, 0)
    except ValueError as error:
        raise ValueError(f"{context} is not numeric") from error


def aligned_numeric_events(row, id_field, value_fields, context):
    identifiers = split_values(row[id_field])
    if not identifiers:
        return []
    values = [split_values(row[field]) for field in value_fields]
    require(
        all(len(items) == len(identifiers) for items in values),
        f"{context} field cardinality is ambiguous",
    )
    return [
        (
            numeric_field(identifier, f"{context} stream id"),
            *(
                numeric_field(items[index], f"{context} {field}")
                for field, items in zip(value_fields, values)
            ),
        )
        for index, identifier in enumerate(identifiers)
    ]


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
    ordered_rows = sorted(request_rows, key=lambda item: int(item["frame.number"]))
    for row_index, row in enumerate(ordered_rows):
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
        require_one_connection(connections, len(streams), f"{cohort} H3 event")
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
        if len(candidate_streams) > len(blocks):
            forced_future_header_streams = set()
            for future_row in ordered_rows[row_index + 1 :]:
                if direction(future_row, proxy_port) != event_direction:
                    continue
                future_connections = split_values(
                    future_row["quic.connection.number"]
                )
                if connection not in future_connections:
                    continue
                future_streams = ordered_unique(
                    split_values(future_row["quic.stream.stream_id"])
                )
                future_methods = split_values(
                    future_row["http3.headers.method"]
                )
                future_statuses = split_values(
                    future_row["http3.headers.status"]
                )
                if bool(future_methods) == bool(future_statuses):
                    continue
                future_values = (
                    future_methods if future_methods else future_statuses
                )
                if len(future_streams) == len(future_values):
                    forced_future_header_streams.update(future_streams)
            candidate_streams = [
                stream
                for stream in candidate_streams
                if stream not in forced_future_header_streams
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
    if cohort in (
        "tree-complete-css",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-start-overlap-css",
        "tree-native-parser-document-start-navigation-stop-css",
        "tree-native-parser-document-start-response-stop-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
    ):
        roles = ("root", "stylesheet")
    elif cohort.startswith("tree-"):
        roles = ("root", "stylesheet", "script")
    else:
        roles = ("root",)
    require(
        len(blocks) == len(roles),
        f"{cohort} private GET semantics have the wrong resource count",
    )
    require(
        len({stream for _, stream, _ in blocks}) == len(blocks),
        f"{cohort} private GET semantics contain duplicate streams",
    )
    blocks.sort(key=lambda item: (item[0], item[1]))
    return {role: semantics for role, (_, _, semantics) in zip(roles, blocks)}


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
            # tshark's CSV writer does not double RFC-quoted ETag values.  A
            # quoted ETag can therefore donate its opening quote to the CSV
            # field delimiter and leave the final quote on the following
            # Content-Length value.  Admit only that exact, unambiguous shape;
            # every other non-numeric length remains fail-closed.
            if len(selected) == 1 and not selected[0].isdigit():
                etags = [value for name, value in block if name == "etag"]
                if (
                    len(etags) == 1
                    and not etags[0].startswith('"')
                    and etags[0].endswith('"')
                    and selected[0].endswith('"')
                    and selected[0][:-1].isdigit()
                ):
                    selected = [selected[0][:-1]]
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
        if role == "root":
            root_path = actual.get(":path", "")
            require(
                root_path == "/camouflage/index.html"
                or ROOT_PATH_PATTERN.fullmatch(root_path) is not None,
                f"{cohort} root expected request path differs",
            )
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


def read_handshake_done_position(root, cohort, proxy_port):
    rows = read_rows(root, cohort, "handshake-done")
    require(rows, f"{cohort} has no decrypted server HANDSHAKE_DONE")
    packet_connections = set()
    for row in read_rows(root, cohort, "packets"):
        connections = ordered_unique(split_values(row["quic.connection.number"]))
        require(
            len(connections) == 1,
            f"{cohort} QUIC packet connection identity is ambiguous",
        )
        packet_connections.add(connections[0])
    require(
        len(packet_connections) == 1,
        f"{cohort} must use exactly one physical outer QUIC connection",
    )

    evidence = []
    for row in rows:
        require(
            direction(row, proxy_port) == "server",
            f"{cohort} HANDSHAKE_DONE is not server-to-client",
        )
        connections = ordered_unique(split_values(row["quic.connection.number"]))
        require(
            len(connections) == 1 and connections[0] in packet_connections,
            f"{cohort} HANDSHAKE_DONE connection identity is ambiguous",
        )
        frame_types = split_values(row["quic.frame_type"])
        matching_types = []
        for frame_type in frame_types:
            try:
                if int(frame_type, 0) == 0x1E:
                    matching_types.append(frame_type)
            except ValueError as error:
                raise ValueError(
                    f"{cohort} HANDSHAKE_DONE frame type is ambiguous"
                ) from error
        require(
            bool(matching_types),
            f"{cohort} HANDSHAKE_DONE frame cardinality is ambiguous",
        )
        evidence.append(int(row["frame.number"]))
    return min(evidence)


def validate_confirmed_lifecycle(root, cohort):
    path = root / f"decrypted-{cohort}-private-lifecycle.moz_log"
    require(path.is_file(), f"{cohort} private lifecycle log is missing")
    events = {name: [] for name in CONFIRMED_LIFECYCLE_PATTERNS}
    with path.open(encoding="utf-8", errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            for name, pattern in CONFIRMED_LIFECYCLE_PATTERNS.items():
                match = pattern.search(line)
                if match:
                    events[name].append((line_number, match.group(1), match.group(2)))
    for name, matches in events.items():
        require(
            len(matches) == 1,
            f"{cohort} lifecycle must contain exactly one {name} marker",
        )
    sessions = {match[1] for matches in events.values() for match in matches}
    require(
        len(sessions) == 1,
        f"{cohort} lifecycle markers do not share one H3 session id",
    )
    outer_connection_infos = {
        events[name][0][2] for name in ("wait", "observed", "release")
    }
    require(
        len(outer_connection_infos) == 1,
        f"{cohort} confirmation gate markers do not share one outer connection-info id",
    )
    positions = {name: matches[0][0] for name, matches in events.items()}
    require(
        positions["wait"] < positions["observed"] < positions["release"]
        and positions["connected"] < positions["observed"] < positions["release"],
        f"{cohort} lifecycle marker order is invalid",
    )


def validate_carrier_dispatch_lifecycle(root, cohort):
    path = root / f"decrypted-{cohort}-private-lifecycle.moz_log"
    require(path.is_file(), f"{cohort} private lifecycle log is missing")
    events = {name: [] for name in CARRIER_DISPATCH_LIFECYCLE_PATTERNS}
    connected = []
    carrier_lines = 0
    with path.open(encoding="utf-8", errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            require(
                "h3.preamble_confirm_gate action=wait" not in line
                and "h3.preamble_confirm_gate action=release" not in line,
                f"{cohort} unexpectedly used the handshake-confirmed gate",
            )
            connected_match = CARRIER_CONNECTED_PATTERN.search(line)
            if connected_match:
                connected.append((line_number, connected_match.groupdict()))
            if "h3.carrier_dispatch" not in line:
                continue
            carrier_lines += 1
            matches = []
            for name, pattern in CARRIER_DISPATCH_LIFECYCLE_PATTERNS.items():
                match = pattern.search(line)
                if match:
                    matches.append((name, match))
            require(
                len(matches) == 1,
                f"{cohort} contains an unknown or ambiguous carrier lifecycle marker",
            )
            name, match = matches[0]
            events[name].append((line_number, match.groupdict()))
    require(
        carrier_lines == sum(len(matches) for matches in events.values()),
        f"{cohort} carrier marker count is invalid",
    )
    singleton_events = (
        "created",
        "establishment",
        "carrier_activated",
        "connection_connected",
        "read",
        "complete",
        "document_attached",
        "headers",
    )
    for name in singleton_events:
        require(
            len(events[name]) == 1,
            f"{cohort} lifecycle must contain exactly one {name} marker",
        )
    for name in ("configured", "waiting", "dispatch", "document_activated"):
        require(events[name], f"{cohort} lifecycle has no {name} marker")
    require(connected, f"{cohort} lifecycle has no connected H3 state marker")

    gates = {
        data["gate"]
        for name, matches in events.items()
        for _, data in matches
        if "gate" in data
    }
    carriers = {
        data["carrier"]
        for name, matches in events.items()
        for _, data in matches
        if "carrier" in data
    }
    require(
        len(gates) == 1 and len(carriers) == 1,
        f"{cohort} carrier lifecycle gate or carrier ids do not match",
    )
    connection_infos = {
        data["ci"]
        for name in ("created", "establishment", "configured")
        for _, data in events[name]
    }
    require(
        len(connection_infos) == 1,
        f"{cohort} carrier lifecycle connection-info ids do not match",
    )
    carrier_session = events["carrier_activated"][0][1]["session"]
    connected_for_session = [
        event for event in connected if event[1]["session"] == carrier_session
    ]
    require(
        len(connected_for_session) == 1,
        f"{cohort} lifecycle must contain exactly one connected state for the carrier session",
    )
    session_ids = {
        data["session"]
        for name in (
            "carrier_activated",
            "connection_connected",
            "document_activated",
            "document_attached",
            "headers",
        )
        for _, data in events[name]
    }
    session_ids.add(connected_for_session[0][1]["session"])
    require(
        len(session_ids) == 1,
        f"{cohort} carrier lifecycle markers do not share one H3 session id",
    )
    gate_id = next(iter(gates))
    carrier_id = next(iter(carriers))
    session_id = next(iter(session_ids))
    configured_documents = {
        data["document"] for _, data in events["configured"]
    }
    dispatched_documents = {
        data["document"] for _, data in events["dispatch"]
    }
    activated_documents = {
        data["document"] for _, data in events["document_activated"]
    }
    final_document_id = events["headers"][0][1]["document"]
    for value, role in (
        (gate_id, "gate"),
        (session_id, "session"),
        (carrier_id, "carrier"),
        (final_document_id, "document"),
    ):
        try:
            normalized = value[2:] if value.startswith("0x") else value
            valid_id = bool(normalized) and int(normalized, 16) != 0
        except ValueError:
            valid_id = False
        require(valid_id, f"{cohort} lifecycle has an invalid {role} id")
    require(
        carrier_id != final_document_id,
        f"{cohort} carrier and document transaction ids must differ",
    )
    require(
        final_document_id in configured_documents
        and final_document_id in dispatched_documents
        and final_document_id in activated_documents
        and events["document_attached"][0][1]["document"] == final_document_id,
        f"{cohort} final document identity is not preserved through normal dispatch",
    )
    created_position = events["created"][0][0]
    establishment_position = events["establishment"][0][0]
    connected_position = connected_for_session[0][0]
    carrier_activated_position = events["carrier_activated"][0][0]
    connection_connected_position = events["connection_connected"][0][0]
    read_position = events["read"][0][0]
    complete_position = events["complete"][0][0]
    attached_position = events["document_attached"][0][0]
    headers_position = events["headers"][0][0]
    require(
        created_position < establishment_position
        and any(position < connected_position for position, _ in events["waiting"])
        and establishment_position < carrier_activated_position
        < connected_position
        < connection_connected_position
        < read_position
        < complete_position
        and all(position > complete_position for position, _ in events["dispatch"])
        and all(
            position > complete_position for position, _ in events["document_activated"]
        )
        and complete_position < attached_position < headers_position,
        f"{cohort} carrier lifecycle marker order is invalid",
    )
    return {
        "session_id": session_id,
        "carrier_id": carrier_id,
        "document_id": final_document_id,
        "stream_id": events["headers"][0][1]["stream_id"],
    }


def validate_native_cache_open_lifecycle(root, cohort):
    path = root / f"decrypted-{cohort}-private-lifecycle.moz_log"
    require(path.is_file(), f"{cohort} private lifecycle log is missing")
    expected = ("open-begin", "callback-pending", "callback", "trigger-network")
    events = {action: [] for action in expected}
    with path.open(encoding="utf-8", errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            if "h3.native_cache_open" not in line:
                continue
            require(
                "action=contract-failed" not in line
                and "action=open-failed" not in line,
                f"{cohort} native cache-open contract failed",
            )
            match = re.search(
                r"action=([a-z-]+).*channel=((?:0x)?[0-9a-fA-F]+)", line
            )
            require(match is not None, f"{cohort} malformed native cache marker")
            action = match.group(1)
            require(action in events, f"{cohort} unknown native cache marker")
            events[action].append((line_number, match.group(2), line))
    for action, matches in events.items():
        require(
            len(matches) == 1,
            f"{cohort} lifecycle must contain exactly one {action} marker",
        )
    channels = {matches[0][1] for matches in events.values()}
    require(len(channels) == 1, f"{cohort} native cache markers changed channel")
    callback_line = events["callback"][0][2]
    callback_status = re.search(r"status=([0-9a-fA-F]{8})", callback_line)
    require(
        re.search(r"entry=(?:\(nil\)|0|0x0)(?:\s|$)", callback_line) is not None
        and "new=0" in callback_line
        and callback_status is not None
        and int(callback_status.group(1), 16) != 0,
        f"{cohort} native cache callback was not a cold read-only miss",
    )
    positions = [events[action][0][0] for action in expected]
    require(
        positions == sorted(positions) and len(set(positions)) == len(positions),
        f"{cohort} native cache lifecycle order is invalid",
    )


def validate_native_channel_open_lifecycle(root, cohort):
    path = root / f"decrypted-{cohort}-private-lifecycle.moz_log"
    require(path.is_file(), f"{cohort} private lifecycle log is missing")
    expected = (
        "open-begin",
        "callback-pending",
        "classifier-predicate",
        "classifier-db-service",
        "classifier-uri-principal",
        "classifier-mode",
        "classifier-classify",
        "classifier-suspended",
        "callback",
        "trigger-network",
        "classifier-complete",
    )
    events = {action: [] for action in expected}
    with path.open(encoding="utf-8", errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            if "h3.native_channel_open" not in line:
                continue
            require(
                "action=contract-failed" not in line
                and "action=open-failed" not in line,
                f"{cohort} native channel-open contract failed",
            )
            match = re.search(
                r"action=([a-z-]+).*channel=((?:0x)?[0-9a-fA-F]+)", line
            )
            require(match is not None, f"{cohort} malformed native channel marker")
            action = match.group(1)
            require(action in events, f"{cohort} unknown native channel marker")
            events[action].append((line_number, match.group(2), line))
    for action, matches in events.items():
        require(
            len(matches) == 1,
            f"{cohort} lifecycle must contain exactly one {action} marker",
        )
    channels = {matches[0][1] for matches in events.values()}
    require(len(channels) == 1, f"{cohort} native channel markers changed channel")

    line = {action: events[action][0][2] for action in expected}
    require(
        "inhibit_caching=0" in line["open-begin"]
        and "expected_mode=normal" in line["open-begin"],
        f"{cohort} did not use a normal writable cache open",
    )
    require(
        "result=1" in line["classifier-predicate"]
        and "triggering_system=1" in line["classifier-predicate"]
        and "bypass=0" in line["classifier-predicate"],
        f"{cohort} Safe Browsing classification predicate is invalid",
    )
    require(
        "exists=1" in line["classifier-db-service"],
        f"{cohort} local URL classifier DB service is unavailable",
    )
    principal = line["classifier-uri-principal"]
    require(
        "triggering_system=1" in principal
        and "uri_system=0" in principal
        and "uri_content=1" in principal
        and "uri_match=1" in principal
        and "attrs_match=1" in principal,
        f"{cohort} classifier principal contract is invalid",
    )
    fixture_uri = re.search(r"\buri=(\S+)", principal)
    require(
        fixture_uri is not None
        and re.fullmatch(
            r"https://localhost:\d+/camouflage/index\.html"
            r"\?scenario=browser_page&size=262144&count=4"
            r"&idle_ms=5000&completion=[0-9a-f]{32}",
            fixture_uri.group(1),
        )
        is not None,
        f"{cohort} classifier URI is not the fixture document URI",
    )
    require(
        "local_db=1" in line["classifier-mode"]
        and "real_time_mode=0" in line["classifier-mode"],
        f"{cohort} classifier did not use the controlled local DB path",
    )
    require(
        "status=00000000" in line["classifier-classify"]
        and "expect_callback=1" in line["classifier-classify"],
        f"{cohort} classifier did not start a genuine asynchronous callback",
    )
    suspended = re.search(r"suspend_count=(\d+)", line["classifier-suspended"])
    require(
        suspended is not None and int(suspended.group(1)) > 0,
        f"{cohort} classifier did not suspend the channel",
    )
    callback = line["callback"]
    require(
        re.search(r"entry=(?!\(nil\)|0(?:x0)?(?:\s|$))\S+", callback)
        is not None
        and "new=1" in callback
        and "status=00000000" in callback,
        f"{cohort} cache callback was not a new writable entry",
    )
    require(
        "new_writable_entry=1" in line["trigger-network"]
        and "classifier_started=1" in line["trigger-network"],
        f"{cohort} network trigger preceded cache/classifier admission",
    )
    require(
        "status=00000000" in line["classifier-complete"]
        and "resume_status=00000000" in line["classifier-complete"]
        and "asynchronous=1" in line["classifier-complete"],
        f"{cohort} classifier did not complete and resume asynchronously",
    )
    positions = [events[action][0][0] for action in expected]
    require(
        positions == sorted(positions) and len(set(positions)) == len(positions),
        f"{cohort} native channel lifecycle order is invalid",
    )


def validate_cold_winner_handoff_lifecycle(root, cohort):
    path = root / f"decrypted-{cohort}-private-lifecycle.moz_log"
    require(path.is_file(), f"{cohort} private lifecycle log is missing")
    expected = (
        "document-configured",
        "attempt-selected",
        "init-post",
        "init-run",
        "dns-start",
        "dns-complete",
        "udp-attempt-start",
        "connection-racing",
        "activate-callback-post",
        "activate-callback-run",
        "winner-ready",
        "exact-dispatch-complete",
        "winner-publish",
    )
    events = {action: [] for action in expected}
    with path.open(encoding="utf-8", errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            unrelated_carrier_dispatch = any(
                f"h3.carrier_dispatch action={action}" in line
                for action in (
                    "carrier-created",
                    "carrier-establishment-start",
                    "document-configured",
                    "document-waiting",
                    "carrier-read-complete",
                    "carrier-complete",
                    "document-normal-dispatch",
                    "document-activated",
                    "document-attached",
                    "document-headers-emitted",
                )
            )
            require(
                "h3.preamble_confirm_gate action=wait" not in line
                and "h3.preamble_confirm_gate action=release" not in line
                and not unrelated_carrier_dispatch,
                f"{cohort} used an unrelated preamble scheduling mechanism",
            )
            if "h3.cold_winner_handoff" not in line:
                continue
            require(
                "action=terminal-failure" not in line,
                f"{cohort} cold winner handoff reached terminal failure",
            )
            action_match = re.search(r"action=([a-z-]+)", line)
            require(action_match is not None, f"{cohort} malformed winner marker")
            action = action_match.group(1)
            require(action in events, f"{cohort} unknown cold winner marker {action}")
            fields = dict(
                re.findall(
                    r"\b(document|attempt|carrier|conn|ci)=([^\s]+)", line
                )
            )
            events[action].append((line_number, fields, line))

    for action, matches in events.items():
        require(
            len(matches) == 1,
            f"{cohort} lifecycle must contain exactly one {action} marker",
        )
    positions = [events[action][0][0] for action in expected]
    require(
        positions == sorted(positions) and len(set(positions)) == len(positions),
        f"{cohort} cold winner lifecycle order is invalid",
    )

    documents = {
        match[1]["document"]
        for action in (
            "document-configured",
            "attempt-selected",
            "init-post",
            "init-run",
            "dns-start",
            "dns-complete",
            "udp-attempt-start",
            "winner-ready",
            "exact-dispatch-complete",
            "winner-publish",
        )
        for match in events[action]
    }
    attempts = {
        match[1]["attempt"]
        for action in (
            "init-post",
            "init-run",
            "dns-start",
            "dns-complete",
            "udp-attempt-start",
        )
        for match in events[action]
    }
    carriers = {
        match[1]["carrier"]
        for action in (
            "connection-racing",
            "activate-callback-post",
            "activate-callback-run",
            "winner-ready",
            "exact-dispatch-complete",
        )
        for match in events[action]
    }
    connections = {
        match[1]["conn"]
        for action in (
            "connection-racing",
            "winner-ready",
            "exact-dispatch-complete",
            "winner-publish",
        )
        for match in events[action]
    }
    require(
        len(documents) == len(attempts) == len(carriers) == len(connections) == 1,
        f"{cohort} cold winner ownership identities changed",
    )
    document = next(iter(documents))
    carrier = next(iter(carriers))
    require(
        document != carrier,
        f"{cohort} establishment carrier is the real document",
    )
    for identity, role in (
        (document, "document"),
        (next(iter(attempts)), "attempt"),
        (carrier, "carrier"),
        (next(iter(connections)), "connection"),
    ):
        try:
            normalized = identity[2:] if identity.startswith("0x") else identity
            valid = bool(normalized) and int(normalized, 16) != 0
        except ValueError:
            valid = False
        require(valid, f"{cohort} has invalid {role} identity")

    require(
        "speculative=0 pending=1 transport=proxy-h3 attempts=1"
        in events["attempt-selected"][0][2]
        and "route-match=1" in events["attempt-selected"][0][2]
        and "pending-owned=1" in events["init-post"][0][2]
        and "registered=1" in events["init-run"][0][2]
        and "physical-proxy=1 candidates-max=1" in events["dns-start"][0][2]
        and "candidate=1 candidates-total=1 protocol=h3 proxy-aware=1"
        in events["udp-attempt-start"][0][2]
        and "racing=1 before-activate=1"
        in events["connection-racing"][0][2]
        and "rv=00000000" in events["dns-complete"][0][2]
        and "rv=00000000" in events["activate-callback-post"][0][2]
        and "rv=00000000" in events["activate-callback-run"][0][2]
        and "pending-removed=1 dispatched=1 racing=1"
        in events["exact-dispatch-complete"][0][2]
        and "racing=0 exact-dispatch=1" in events["winner-publish"][0][2],
        f"{cohort} cold winner contract fields are invalid",
    )
    return {
        "document_id": document,
        "carrier_id": carrier,
        "connection_id": next(iter(connections)),
    }


def observed_client_bidirectional_streams(root, cohort):
    streams = set()
    for row in read_rows(root, cohort, "lifecycle"):
        for value in split_values(row["quic.stream.stream_id"]):
            try:
                stream_id = int(value, 0)
            except ValueError as error:
                raise ValueError(
                    f"{cohort} contains an ambiguous QUIC stream id"
                ) from error
            if stream_id % 4 == 0:
                streams.add(str(stream_id))
    return streams


def read_h3_initialization_position(root, cohort, proxy_port):
    rows = read_rows(root, cohort, "unidirectional-streams")
    require(rows, f"{cohort} has no client H3 unidirectional stream initialization")
    type_streams = defaultdict(set)
    type_frames = defaultdict(list)
    connections_seen = set()
    for row in rows:
        require(
            direction(row, proxy_port) == "client",
            f"{cohort} H3 initialization contains a server stream",
        )
        stream_types = split_values(row["http3.stream_uni_type"])
        streams = split_values(row["quic.stream.stream_id"])
        connections = split_values(row["quic.connection.number"])
        require(
            bool(stream_types) and len(stream_types) == len(streams),
            f"{cohort} H3 initialization type/stream cardinality is ambiguous",
        )
        require_one_connection(
            connections, len(streams), f"{cohort} H3 initialization"
        )
        connections_seen.add(connections[0])
        for stream_type, stream in zip(stream_types, streams):
            try:
                numeric_type = int(stream_type, 0)
            except ValueError as error:
                raise ValueError(
                    f"{cohort} H3 unidirectional stream type is ambiguous"
                ) from error
            type_streams[numeric_type].add(stream)
            type_frames[numeric_type].append(int(row["frame.number"]))
    require(
        len(connections_seen) == 1,
        f"{cohort} H3 initialization used multiple outer connections",
    )
    require(
        set(type_streams) == {0, 2, 3}
        and all(len(streams) == 1 for streams in type_streams.values()),
        f"{cohort} lacks exact control/QPACK stream initialization",
    )
    return max(frame for frames in type_frames.values() for frame in frames)


def normalized_h3_settings(root, cohort):
    rows = read_rows(root, cohort, "settings")
    require(rows, f"{cohort} has no client H3 SETTINGS")
    normalized = []
    connections_seen = set()
    for row in rows:
        connections = ordered_unique(split_values(row["quic.connection.number"]))
        require(
            len(connections) == 1,
            f"{cohort} H3 SETTINGS connection identity is ambiguous",
        )
        connections_seen.add(connections[0])
        normalized.append(
            tuple(
                (name, tuple(split_values(value)))
                for name, value in row.items()
                if name != "quic.connection.number"
            )
        )
    require(
        len(connections_seen) == 1,
        f"{cohort} H3 SETTINGS used multiple outer connections",
    )
    return tuple(normalized)


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
        packet_connections = ordered_unique(split_values(row["quic.connection.number"]))
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
        fin_values = [value.lower() for value in split_values(row["quic.stream.fin"])]
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
        require_one_connection(connections, len(streams), f"{cohort} stream FIN event")
        if len(connections) == 1:
            connections = connections * len(streams)
        event_direction = direction(row, proxy_port)
        frame = int(row["frame.number"])
        for connection, stream, fin in zip(connections, streams, fin_values):
            if fin not in {"1", "true"}:
                continue
            key = (event_direction, connection, stream)
            stream_fin_positions[key] = max(frame, stream_fin_positions.get(key, frame))

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
        row_connections = ordered_unique(split_values(row["quic.connection.number"]))
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


def validate_response_stop_wire_lifecycle(
    root,
    cohort,
    proxy_port,
    connect_stream,
    css_stream,
    css_response_frame,
    css_content_length,
):
    connect_stream = numeric_field(connect_stream, f"{cohort} CONNECT stream id")
    css_stream = numeric_field(css_stream, f"{cohort} CSS stream id")
    lifecycle_rows = read_rows(root, cohort, "lifecycle")
    connect_data = []
    stop_sending = []
    reset_stream = []
    css_server_fins = []
    css_data_length = 0

    for row in lifecycle_rows:
        frame = numeric_field(row["frame.number"], f"{cohort} lifecycle frame")
        event_time_ms = float(row["frame.time_relative"]) * 1000
        connections = ordered_unique(split_values(row["quic.connection.number"]))
        require(
            len(connections) == 1,
            f"{cohort} lifecycle event connection identity is ambiguous",
        )
        connection = connections[0]
        event_direction = direction(row, proxy_port)

        data_events = []
        if split_values(row["http3.frame_type"]):
            stream_ids = split_values(row["quic.stream.stream_id"])
            frame_types = split_values(row["http3.frame_type"])
            frame_lengths = split_values(row["http3.frame_length"])
            require(
                bool(stream_ids) and len(frame_types) == len(frame_lengths),
                f"{cohort} H3 DATA field cardinality is ambiguous",
            )
            unique_stream_ids = ordered_unique(stream_ids)
            if len(unique_stream_ids) == 1:
                stream_ids = unique_stream_ids * len(frame_types)
            else:
                require(
                    len(stream_ids) == len(frame_types),
                    f"{cohort} H3 DATA stream mapping is ambiguous",
                )
            data_events = [
                (
                    numeric_field(stream, f"{cohort} H3 DATA stream id"),
                    numeric_field(frame_type, f"{cohort} H3 frame type"),
                    numeric_field(frame_length, f"{cohort} H3 frame length"),
                )
                for stream, frame_type, frame_length in zip(
                    stream_ids, frame_types, frame_lengths
                )
            ]
        for stream, frame_type, frame_length in data_events:
            if (
                event_direction == "server"
                and stream == connect_stream
                and frame_type == 0
                and frame_length > 0
            ):
                connect_data.append((frame, event_time_ms, connection, frame_length))
            if (
                event_direction == "server"
                and stream == css_stream
                and frame_type == 0
                and frame_length > 0
            ):
                css_data_length += frame_length

        fin_values = [value.lower() for value in split_values(row["quic.stream.fin"])]
        if fin_values:
            stream_ids = split_values(row["quic.stream.stream_id"])
            require(
                len(stream_ids) == len(fin_values),
                f"{cohort} stream FIN field cardinality is ambiguous",
            )
            for stream, fin in zip(stream_ids, fin_values):
                require(
                    fin in {"0", "false", "1", "true"},
                    f"{cohort} stream FIN value is ambiguous",
                )
                if (
                    event_direction == "server"
                    and numeric_field(stream, f"{cohort} FIN stream id") == css_stream
                    and fin in {"1", "true"}
                ):
                    css_server_fins.append(frame)

        for stream, error in aligned_numeric_events(
            row,
            "quic.ss.stream_id",
            ("quic.ss.application_error_code",),
            f"{cohort} STOP_SENDING event",
        ):
            if stream == css_stream:
                stop_sending.append(
                    (frame, event_time_ms, connection, event_direction, error)
                )

        for stream, error, final_size in aligned_numeric_events(
            row,
            "quic.rsts.stream_id",
            (
                "quic.rsts.application_error_code",
                "quic.rsts.final_size",
            ),
            f"{cohort} RESET_STREAM event",
        ):
            if stream == css_stream:
                reset_stream.append(
                    (
                        frame,
                        event_time_ms,
                        connection,
                        event_direction,
                        error,
                        final_size,
                    )
                )

    require(connect_data, f"{cohort} has no positive server CONNECT H3 DATA")
    require(
        len(stop_sending) == 1,
        f"{cohort} must emit exactly one CSS STOP_SENDING",
    )
    require(
        len(reset_stream) == 1,
        f"{cohort} must receive exactly one CSS RESET_STREAM",
    )
    require(not css_server_fins, f"{cohort} CSS stream reached FIN before cancellation")

    first_connect_data = min(connect_data, key=lambda event: event[0])
    stop = stop_sending[0]
    reset = reset_stream[0]
    require(
        stop[3] == "client" and reset[3] == "server",
        f"{cohort} CSS cancellation directions are invalid",
    )
    require(
        stop[4] == 0x10C and reset[4] == 0x10C,
        f"{cohort} CSS cancellation did not use H3_REQUEST_CANCELLED",
    )
    require(
        first_connect_data[2] == stop[2] == reset[2],
        f"{cohort} response-stop lifecycle used different QUIC identities",
    )
    require(
        css_response_frame < first_connect_data[0] < stop[0] < reset[0],
        f"{cohort} lacks CSS 200 < server CONNECT data < STOP_SENDING < RESET_STREAM",
    )
    require(
        0 < css_data_length < css_content_length,
        f"{cohort} observed CSS DATA body is not partial",
    )
    return {
        "server_connect_data_packet_position": first_connect_data[0],
        "server_connect_data_time_ms": f"{first_connect_data[1]:.3f}",
        "stop_sending_packet_position": stop[0],
        "stop_sending_time_ms": f"{stop[1]:.3f}",
        "reset_stream_packet_position": reset[0],
        "reset_stream_time_ms": f"{reset[1]:.3f}",
        "reset_stream_final_size": reset[5],
        "css_data_length_before_reset": css_data_length,
        "css_content_length": css_content_length,
    }


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
            "document-carrier-dispatch",
            "document-cold-winner-handoff",
        ):
            request_methods = [row["method"] for row in requests if row["method"]]
            require(
                request_methods.count("GET") == 1
                and request_methods.count("CONNECT") >= 1
                and len(request_methods)
                == 1 + request_methods.count("CONNECT"),
                f"{arm} emitted an unexpected outer HTTP request",
            )
        if arm in (
            "document-native-cache-open",
            "document-native-channel-open",
        ):
            request_methods = [row["method"] for row in requests if row["method"]]
            require(
                request_methods.count("GET") == 1
                and request_methods.count("CONNECT") >= 1
                and len(request_methods) == 1 + request_methods.count("CONNECT"),
                f"{arm} emitted an unexpected outer HTTP request",
            )
        if arm in (
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
            "tree-native-parser-document-start-overlap-css",
            "tree-native-parser-document-start-navigation-stop-css",
            "tree-native-parser-document-start-response-stop-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
            "tree-native-parser-process-overlap-css",
            "tree-native-parser-full-process-overlap-css",
            "tree-overlap",
        ):
            expected_gets = (
                2
                if arm
                in (
                    "tree-complete-css",
                    "tree-root-overlap-css",
                    "tree-resource-committed-overlap-css",
                    "tree-resource-native-cache-committed-overlap",
                    "tree-native-parser-preload-overlap-css",
                    "tree-native-parser-document-start-overlap-css",
                    "tree-native-parser-document-start-navigation-stop-css",
                    "tree-native-parser-document-start-response-stop-css",
                    "tree-native-parser-document-handoff-overlap-css",
                    "tree-native-parser-retarget-overlap-css",
                    "tree-native-parser-ipc-rendezvous-overlap-css",
                    "tree-native-parser-root-rendezvous-overlap-css",
                    "tree-native-parser-process-overlap-css",
                    "tree-native-parser-full-process-overlap-css",
                )
                else 3
                if arm.startswith("tree-")
                else 1
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
                if arm not in (
                    "tree-native-parser-document-start-overlap-css",
                    "tree-native-parser-document-start-navigation-stop-css",
                    "tree-native-parser-document-start-response-stop-css",
                ):
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
                "tree-native-parser-document-start-navigation-stop-css",
                "tree-native-parser-document-start-response-stop-css",
            ):
                root_get = sorted(
                    gets, key=lambda row: row["packet_position"]
                )[0]
                root_response_headers = [
                    row
                    for row in response_headers
                    if row["connection_index"] == root_get["connection_index"]
                    and row["stream_id"] == root_get["stream_id"]
                ]
                require(
                    len(root_response_headers) == 1
                    and len(response_headers) == len(gets),
                    f"{arm} lacks exactly one successful response for each GET",
                )
            else:
                require(
                    len(response_headers) == len(gets),
                    f"{arm} lacks exactly one successful response for every GET",
                )
            if arm in (
                "root",
                "root-pmtud-control",
                "document-complete",
                "document-carrier-dispatch",
                "document-cold-winner-handoff",
                "document-native-cache-open",
                "document-native-channel-open",
                "document-handshake-confirmed",
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
                if arm in (
                    "root",
                    "root-pmtud-control",
                    "document-complete",
                    "document-carrier-dispatch",
                    "document-cold-winner-handoff",
                    "document-native-cache-open",
                    "document-native-channel-open",
                    "document-handshake-confirmed",
                ):
                    observed_fins = [
                        row["stream_fin_packet_position"]
                        for row in response_headers
                        if row["stream_fin_packet_position"] != ""
                    ]
                    require(
                        len(observed_fins) == len(response_headers)
                        and all(
                            position < connects[0]["packet_position"]
                            for position in observed_fins
                        ),
                        f"{arm} CONNECT preceded the completed root stream",
                    )
            if arm == "document-overlap":
                require(
                    all(
                        row["packet_position"] < connects[0]["packet_position"]
                        for row in response_headers
                    ),
                    "document-overlap CONNECT preceded root response HEADERS",
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
                expected_assets = (
                    1
                    if arm.endswith("-css")
                    or arm
                    in (
                        "tree-resource-native-cache-committed-overlap",
                        "tree-native-parser-preload-overlap-css",
                        "tree-native-parser-document-start-overlap-css",
                        "tree-native-parser-document-start-navigation-stop-css",
                        "tree-native-parser-document-start-response-stop-css",
                        "tree-native-parser-document-handoff-overlap-css",
                        "tree-native-parser-retarget-overlap-css",
                        "tree-native-parser-ipc-rendezvous-overlap-css",
                        "tree-native-parser-root-rendezvous-overlap-css",
                        "tree-native-parser-process-overlap-css",
                        "tree-native-parser-full-process-overlap-css",
                    )
                    else 2
                )
                if arm not in (
                    "tree-native-parser-document-start-navigation-stop-css",
                    "tree-native-parser-document-start-response-stop-css",
                ):
                    require(
                        len(asset_responses) == expected_assets,
                        f"{arm} lacks one or more asset response headers",
                    )
                root_stream = (
                    ordered_gets[0]["connection_index"],
                    ordered_gets[0]["stream_id"],
                )
                root_responses = [
                    row
                    for row in response_headers
                    if (row["connection_index"], row["stream_id"]) == root_stream
                ]
                if arm not in (
                    "tree-native-parser-document-start-overlap-css",
                    "tree-native-parser-document-start-navigation-stop-css",
                    "tree-native-parser-document-start-response-stop-css",
                ):
                    require(
                        root_responses
                        and all(
                            row["stream_fin_packet_position"] != ""
                            and row["stream_fin_packet_position"]
                            < connects[0]["packet_position"]
                            for row in root_responses
                        ),
                        f"{arm} root FIN did not precede CONNECT",
                    )
                if arm in ("tree-complete", "tree-complete-css"):
                    observed_asset_fins = [
                        row["stream_fin_packet_position"]
                        for row in asset_responses
                        if row["stream_fin_packet_position"] != ""
                    ]
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
                elif arm in (
                    "tree-native-parser-document-start-overlap-css",
                    "tree-native-parser-document-start-navigation-stop-css",
                    "tree-native-parser-document-start-response-stop-css",
                    "tree-root-overlap",
                    "tree-root-overlap-css",
                    "tree-resource-committed-overlap-css",
                    "tree-resource-native-cache-committed-overlap",
                    "tree-native-parser-preload-overlap-css",
                    "tree-native-parser-document-handoff-overlap-css",
                    "tree-native-parser-retarget-overlap-css",
                    "tree-native-parser-ipc-rendezvous-overlap-css",
                    "tree-native-parser-root-rendezvous-overlap-css",
                    "tree-native-parser-process-overlap-css",
                    "tree-native-parser-full-process-overlap-css",
                ):
                    root_stream = (
                        ordered_gets[0]["connection_index"],
                        ordered_gets[0]["stream_id"],
                    )
                    root_responses = [
                        row
                        for row in response_headers
                        if (row["connection_index"], row["stream_id"]) == root_stream
                    ]
                    if arm not in (
                        "tree-native-parser-document-start-overlap-css",
                        "tree-native-parser-document-start-navigation-stop-css",
                        "tree-native-parser-document-start-response-stop-css",
                    ):
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
                    elif arm == "tree-native-parser-document-start-overlap-css":
                        stylesheet_get = ordered_gets[1]
                        stylesheet_responses = [
                            row
                            for row in asset_responses
                            if row["connection_index"]
                            == stylesheet_get["connection_index"]
                            and row["stream_id"] == stylesheet_get["stream_id"]
                        ]
                        connect_position = connects[0]["packet_position"]
                        require(
                            ordered_gets[0]["packet_position"]
                            < connect_position
                            < stylesheet_get["packet_position"],
                            f"{arm} lacks root GET < CONNECT < CSS GET",
                        )
                        require(
                            len(stylesheet_responses) == 1
                            and stylesheet_responses[0][
                                "stream_fin_packet_position"
                            ]
                            != ""
                            and stylesheet_get["packet_position"]
                            < stylesheet_responses[0]["packet_position"]
                            <= stylesheet_responses[0][
                                "stream_fin_packet_position"
                            ],
                            f"{arm} lacks CSS GET < response HEADERS <= CSS FIN",
                        )
                    else:
                        stylesheet_get = ordered_gets[1]
                        stylesheet_all_responses = [
                            row
                            for row in cohorts[arm]
                            if row["direction"] == "server"
                            and row["status"]
                            and row["connection_index"]
                            == stylesheet_get["connection_index"]
                            and row["stream_id"] == stylesheet_get["stream_id"]
                        ]
                        stylesheet_responses = [
                            row
                            for row in asset_responses
                            if row["connection_index"]
                            == stylesheet_get["connection_index"]
                            and row["stream_id"] == stylesheet_get["stream_id"]
                        ]
                        connect_position = connects[0]["packet_position"]
                        require(
                            ordered_gets[0]["packet_position"]
                            < connect_position
                            < stylesheet_get["packet_position"],
                            f"{arm} lacks root GET < CONNECT < CSS GET",
                        )
                        require(
                            all(
                                row["status"] == "200"
                                for row in stylesheet_all_responses
                            ),
                            f"{arm} observed a non-200 CSS response",
                        )
                        require(
                            len(stylesheet_responses) == 1,
                            f"{arm} lacks one successful CSS response HEADERS",
                        )
                        require(
                            all(
                                stylesheet_get["packet_position"]
                                < row["packet_position"]
                                for row in stylesheet_responses
                            ),
                            f"{arm} CSS response preceded its GET",
                        )
                    if arm in (
                        "tree-native-parser-preload-overlap-css",
                        "tree-native-parser-document-handoff-overlap-css",
                        "tree-native-parser-retarget-overlap-css",
                        "tree-native-parser-ipc-rendezvous-overlap-css",
                        "tree-native-parser-root-rendezvous-overlap-css",
                        "tree-native-parser-process-overlap-css",
                        "tree-native-parser-full-process-overlap-css",
                    ):
                        stylesheet_get = ordered_gets[1]
                        root_fin = root_responses[0]["stream_fin_packet_position"]
                        stylesheet_responses = [
                            row
                            for row in asset_responses
                            if row["connection_index"]
                            == stylesheet_get["connection_index"]
                            and row["stream_id"] == stylesheet_get["stream_id"]
                        ]
                        require(
                            root_fin != ""
                            and root_fin < stylesheet_get["packet_position"],
                            f"{arm} CSS GET preceded root response DATA/FIN",
                        )
                        require(
                            len(stylesheet_responses) == 1
                            and stylesheet_get["packet_position"]
                            < connects[0]["packet_position"]
                            < stylesheet_responses[0]["stream_fin_packet_position"],
                            f"{arm} lacks CSS GET < CONNECT < CSS FIN",
                        )
                elif arm == "tree-overlap":
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
                            row["stream_fin_packet_position"]
                            < connects[0]["packet_position"]
                            for row in root_responses
                        ),
                        "tree-overlap root FIN did not precede CONNECT",
                    )
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
        "document-overlap" not in arms or "document-complete" in arms,
        "document-overlap decrypted validation requires document-complete",
    )
    require(
        "document-handshake-confirmed" not in arms
        or "document-complete" in arms,
        "document-handshake-confirmed decrypted validation requires "
        "document-complete",
    )
    require(
        "document-carrier-dispatch" not in arms or "document-complete" in arms,
        "document-carrier-dispatch decrypted validation requires document-complete",
    )
    require(
        "document-cold-winner-handoff" not in arms or "document-complete" in arms,
        "document-cold-winner-handoff decrypted validation requires document-complete",
    )
    require(
        "document-native-cache-open" not in arms or "document-complete" in arms,
        "document-native-cache-open decrypted validation requires document-complete",
    )
    require(
        "document-native-channel-open" not in arms or "document-complete" in arms,
        "document-native-channel-open decrypted validation requires "
        "document-complete",
    )
    require(
        "document-start-overlap" not in arms
        or {"document-complete", "document-overlap"}.issubset(arms),
        "document-start-overlap decrypted validation requires "
        "document-complete and document-overlap",
    )
    require(
        "tree-native-parser-document-start-overlap-css" not in arms
        or "document-start-overlap" in arms,
        "tree-native-parser-document-start-overlap-css decrypted validation "
        "requires document-start-overlap",
    )
    require(
        "tree-native-parser-document-start-navigation-stop-css" not in arms
        or "tree-native-parser-document-start-overlap-css" in arms,
        "tree-native-parser-document-start-navigation-stop-css decrypted "
        "validation requires tree-native-parser-document-start-overlap-css",
    )
    require(
        "tree-native-parser-document-start-response-stop-css" not in arms
        or "tree-native-parser-document-start-navigation-stop-css" in arms,
        "tree-native-parser-document-start-response-stop-css decrypted "
        "validation requires "
        "tree-native-parser-document-start-navigation-stop-css",
    )
    require(
        "tree-early-overlap" not in arms or "tree-complete" in arms,
        "tree-early-overlap decrypted validation requires tree-complete",
    )
    require(
        "tree-overlap" not in arms or "tree-complete" in arms,
        "tree-overlap decrypted validation requires tree-complete",
    )
    require(
        "root-pmtud-control" not in arms or "root" in arms,
        "root-pmtud-control decrypted validation requires root",
    )
    require(
        "tree-root-overlap" not in arms or "tree-complete" in arms,
        "tree-root-overlap decrypted validation requires tree-complete",
    )
    require(
        "tree-root-overlap-css" not in arms or "tree-complete-css" in arms,
        "tree-root-overlap-css decrypted validation requires tree-complete-css",
    )
    require(
        "tree-resource-committed-overlap-css" not in arms
        or "tree-complete-css" in arms,
        "tree-resource-committed-overlap-css decrypted validation requires "
        "tree-complete-css",
    )
    require(
        "tree-resource-native-cache-committed-overlap" not in arms
        or "tree-complete-css" in arms,
        "tree-resource-native-cache-committed-overlap decrypted validation "
        "requires tree-complete-css",
    )
    require(
        "tree-native-parser-preload-overlap-css" not in arms
        or "tree-complete-css" in arms,
        "tree-native-parser-preload-overlap-css decrypted validation "
        "requires tree-complete-css",
    )
    require(
        "tree-native-parser-document-handoff-overlap-css" not in arms
        or {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
        }.issubset(arms),
        "tree-native-parser-document-handoff-overlap-css decrypted validation "
        "requires tree-complete-css and "
        "tree-native-parser-preload-overlap-css",
    )
    require(
        "tree-native-parser-retarget-overlap-css" not in arms
        or {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
        }.issubset(arms),
        "tree-native-parser-retarget-overlap-css decrypted validation requires "
        "tree-complete-css, tree-native-parser-preload-overlap-css, and "
        "tree-native-parser-document-handoff-overlap-css",
    )
    require(
        "tree-native-parser-ipc-rendezvous-overlap-css" not in arms
        or {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
        }.issubset(arms),
        "tree-native-parser-ipc-rendezvous-overlap-css decrypted validation "
        "requires tree-complete-css, tree-native-parser-preload-overlap-css, "
        "tree-native-parser-document-handoff-overlap-css, and "
        "tree-native-parser-retarget-overlap-css",
    )
    require(
        "tree-native-parser-root-rendezvous-overlap-css" not in arms
        or "tree-native-parser-ipc-rendezvous-overlap-css" in arms,
        "tree-native-parser-root-rendezvous-overlap-css decrypted validation "
        "requires tree-native-parser-ipc-rendezvous-overlap-css",
    )
    require(
        "tree-native-parser-process-overlap-css" not in arms
        or "tree-native-parser-root-rendezvous-overlap-css" in arms,
        "tree-native-parser-process-overlap-css decrypted validation "
        "requires tree-native-parser-root-rendezvous-overlap-css",
    )
    require(
        "tree-native-parser-full-process-overlap-css" not in arms
        or "tree-native-parser-process-overlap-css" in arms,
        "tree-native-parser-full-process-overlap-css decrypted validation "
        "requires tree-native-parser-process-overlap-css",
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
    confirmed_admission_validated = False
    carrier_dispatch_admission_validated = False
    cold_winner_handoff_admission_validated = False
    native_cache_open_admission_validated = False
    native_channel_open_admission_validated = False
    if "document-carrier-dispatch" in arms:
        cohort = "document-carrier-dispatch"
        carrier_identity = validate_carrier_dispatch_lifecycle(root, cohort)
        carrier_gets = [
            row
            for row in cohorts[cohort]
            if row["direction"] == "client" and row["method"] == "GET"
        ]
        carrier_connects = [
            row
            for row in cohorts[cohort]
            if row["direction"] == "client" and row["method"] == "CONNECT"
        ]
        require(
            len(carrier_gets) == 1
            and carrier_identity["stream_id"] == carrier_gets[0]["stream_id"],
            f"{cohort} lifecycle document stream does not match the wire GET",
        )
        expected_bidi_streams = {
            row["stream_id"] for row in (*carrier_gets, *carrier_connects)
        }
        observed_bidi_streams = observed_client_bidirectional_streams(root, cohort)
        require(
            carrier_gets[0]["stream_id"] in observed_bidi_streams
            and observed_bidi_streams <= expected_bidi_streams,
            f"{cohort} contains an unexplained client bidirectional stream",
        )
        carrier_dispatch_admission_validated = True
    if "document-cold-winner-handoff" in arms:
        cohort = "document-cold-winner-handoff"
        validate_cold_winner_handoff_lifecycle(root, cohort)
        winner_gets = [
            row
            for row in cohorts[cohort]
            if row["direction"] == "client" and row["method"] == "GET"
        ]
        winner_connects = [
            row
            for row in cohorts[cohort]
            if row["direction"] == "client" and row["method"] == "CONNECT"
        ]
        require(len(winner_gets) == 1, f"{cohort} must emit exactly one GET")
        expected_bidi_streams = {
            row["stream_id"] for row in (*winner_gets, *winner_connects)
        }
        observed_bidi_streams = observed_client_bidirectional_streams(root, cohort)
        require(
            winner_gets[0]["stream_id"] in observed_bidi_streams
            and observed_bidi_streams <= expected_bidi_streams,
            f"{cohort} contains a carrier request or unexplained client stream",
        )
        for row in read_rows(root, cohort, "packets"):
            if direction(row, proxy_port) != "client":
                continue
            for packet_type in split_values(row["quic.long.packet_type"]):
                try:
                    is_zero_rtt = int(packet_type, 0) == 1
                except ValueError as error:
                    raise ValueError(
                        f"{cohort} client long-header packet type is ambiguous"
                    ) from error
                require(not is_zero_rtt, f"{cohort} unexpectedly emitted 0-RTT")
        cold_winner_handoff_admission_validated = True
    if "document-native-cache-open" in arms:
        validate_native_cache_open_lifecycle(root, "document-native-cache-open")
        native_cache_open_admission_validated = True
    if "document-native-channel-open" in arms:
        validate_native_channel_open_lifecycle(
            root, "document-native-channel-open"
        )
        native_channel_open_admission_validated = True
    if "document-handshake-confirmed" in arms:
        cohort = "document-handshake-confirmed"
        for row in read_rows(root, cohort, "packets"):
            if direction(row, proxy_port) != "client":
                continue
            for packet_type in split_values(row["quic.long.packet_type"]):
                try:
                    is_zero_rtt = int(packet_type, 0) == 1
                except ValueError as error:
                    raise ValueError(
                        f"{cohort} client long-header packet type is ambiguous"
                    ) from error
                require(
                    not is_zero_rtt,
                    f"{cohort} unexpectedly emitted client 0-RTT",
                )
        initialization_position = read_h3_initialization_position(
            root, cohort, proxy_port
        )
        handshake_done_position = read_handshake_done_position(
            root, cohort, proxy_port
        )
        gets = [
            row
            for row in cohorts[cohort]
            if row["direction"] == "client" and row["method"] == "GET"
        ]
        require(
            len(gets) == 1
            and initialization_position < handshake_done_position
            < gets[0]["packet_position"],
            f"{cohort} did not preserve H3 initialization < HANDSHAKE_DONE < GET",
        )
        require(
            normalized_h3_settings(root, "document-complete")
            == normalized_h3_settings(root, cohort),
            "document H3 SETTINGS differ for handshake-confirmed",
        )
        validate_confirmed_lifecycle(root, cohort)
        confirmed_admission_validated = True
    preamble_semantics = {}
    root_response_sizes = {}
    preamble_arms = [
        arm
        for arm in arms
        if arm
        not in (
            "off",
            "gate",
        )
    ]
    for arm in preamble_arms:
        preamble_semantics[arm] = read_get_request_semantics(root, arm, proxy_port)
        validate_expected_get_request_semantics(arm, preamble_semantics[arm])
        response_lengths = read_response_content_lengths(root, arm, proxy_port)
        ordered_gets = sorted(
            (
                row
                for row in cohorts[arm]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        require(bool(ordered_gets), f"{arm} lacks its root GET")
        root_stream = ordered_gets[0]["stream_id"]
        require(
            root_stream in response_lengths,
            f"{arm} lacks root content-length evidence",
        )
        root_response_sizes[arm] = response_lengths[root_stream]
    if {"root", "root-pmtud-control"}.issubset(arms):
        require(
            preamble_semantics["root"]["root"]
            == preamble_semantics["root-pmtud-control"]["root"],
            "root selected header values/order differ for PMTUD control",
        )
        require(
            root_response_sizes["root"] == root_response_sizes["root-pmtud-control"],
            "root response content-length differs for PMTUD control",
        )
    if {"document-complete", "document-overlap"}.issubset(arms):
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-overlap"]["root"],
            "document selected header values/order differ between complete and overlap",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-overlap"],
            "document response content-length differs between complete and overlap",
        )
    if {"document-complete", "document-handshake-confirmed"}.issubset(arms):
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-handshake-confirmed"]["root"],
            "document selected header values/order differ for handshake-confirmed",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-handshake-confirmed"],
            "document response content-length differs for handshake-confirmed",
        )
    if {"document-complete", "document-carrier-dispatch"}.issubset(arms):
        complete_wire_get = next(
            row
            for row in cohorts["document-complete"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        carrier_wire_get = next(
            row
            for row in cohorts["document-carrier-dispatch"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        require(
            complete_wire_get["header_name_order"]
            == carrier_wire_get["header_name_order"]
            and complete_wire_get["header_name_set"]
            == carrier_wire_get["header_name_set"],
            "document sanitized header names/order differ for carrier-dispatch",
        )
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-carrier-dispatch"]["root"],
            "document selected header values/order differ for carrier-dispatch",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-carrier-dispatch"],
            "document response content-length differs for carrier-dispatch",
        )
    if {"document-complete", "document-cold-winner-handoff"}.issubset(arms):
        complete_wire_get = next(
            row
            for row in cohorts["document-complete"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        winner_wire_get = next(
            row
            for row in cohorts["document-cold-winner-handoff"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        require(
            complete_wire_get["header_name_order"]
            == winner_wire_get["header_name_order"]
            and complete_wire_get["header_name_set"]
            == winner_wire_get["header_name_set"],
            "document sanitized header names/order differ for cold winner",
        )
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-cold-winner-handoff"]["root"],
            "document selected header values/order differ for cold winner",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-cold-winner-handoff"],
            "document response content-length differs for cold winner",
        )
    if {"document-complete", "document-native-cache-open"}.issubset(arms):
        complete_wire_get = next(
            row
            for row in cohorts["document-complete"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        cache_wire_get = next(
            row
            for row in cohorts["document-native-cache-open"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        require(
            complete_wire_get["header_name_order"]
            == cache_wire_get["header_name_order"]
            and complete_wire_get["header_name_set"]
            == cache_wire_get["header_name_set"],
            "document sanitized header names/order differ for native cache-open",
        )
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-native-cache-open"]["root"],
            "document selected header values/order differ for native cache-open",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-native-cache-open"],
            "document response content-length differs for native cache-open",
        )
    if {"document-complete", "document-native-channel-open"}.issubset(arms):
        complete_wire_get = next(
            row
            for row in cohorts["document-complete"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        channel_wire_get = next(
            row
            for row in cohorts["document-native-channel-open"]
            if row["direction"] == "client" and row["method"] == "GET"
        )
        require(
            complete_wire_get["header_name_order"]
            == channel_wire_get["header_name_order"]
            and complete_wire_get["header_name_set"]
            == channel_wire_get["header_name_set"],
            "document sanitized header names/order differ for native channel-open",
        )
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-native-channel-open"]["root"],
            "document selected header values/order differ for native channel-open",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-native-channel-open"],
            "document response content-length differs for native channel-open",
        )
    if {
        "document-complete",
        "document-overlap",
        "document-start-overlap",
    }.issubset(arms):
        require(
            preamble_semantics["document-complete"]["root"]
            == preamble_semantics["document-start-overlap"]["root"],
            "document selected header values/order differ for start overlap",
        )
        require(
            root_response_sizes["document-complete"]
            == root_response_sizes["document-start-overlap"],
            "document response content-length differs for start overlap",
        )
    tree_semantics = {}
    tree_asset_sizes = {}
    response_stop_wire_outcomes = {}
    for arm in arms:
        if not arm.startswith("tree-"):
            continue
        tree_semantics[arm] = preamble_semantics[arm]
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
        if arm != "tree-native-parser-document-start-navigation-stop-css":
            require(
                all(stream in response_lengths for stream in asset_streams),
                f"{arm} lacks asset content-length evidence",
            )
        tree_asset_sizes[arm] = tuple(
            response_lengths[stream]
            for stream in asset_streams
            if stream in response_lengths
        )
    if {
        "document-start-overlap",
        "tree-native-parser-document-start-overlap-css",
    }.issubset(arms):
        treatment = "tree-native-parser-document-start-overlap-css"
        require(
            preamble_semantics["document-start-overlap"]["root"]
            == tree_semantics[treatment]["root"],
            "native-parser document-start root selected header values/order differ",
        )
        require(
            root_response_sizes["document-start-overlap"]
            == root_response_sizes[treatment],
            "native-parser document-start root response content-length differs",
        )
    if {
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-start-overlap-css",
    }.issubset(arms):
        baseline = "tree-native-parser-preload-overlap-css"
        treatment = "tree-native-parser-document-start-overlap-css"
        for role in ("root", "stylesheet"):
            require(
                tree_semantics[baseline][role] == tree_semantics[treatment][role],
                "native-parser document-start "
                f"{role} selected header values/order differ from parser control",
            )
        require(
            tree_asset_sizes[baseline] == tree_asset_sizes[treatment],
            "native-parser document-start CSS asset content-length differs "
            "from parser control",
        )
    if {
        "tree-native-parser-document-start-overlap-css",
        "tree-native-parser-document-start-navigation-stop-css",
    }.issubset(arms):
        baseline = "tree-native-parser-document-start-overlap-css"
        treatment = "tree-native-parser-document-start-navigation-stop-css"
        for role in ("root", "stylesheet"):
            require(
                tree_semantics[baseline][role] == tree_semantics[treatment][role],
                "native-parser navigation-stop "
                f"{role} selected header values/order differ from control",
            )
        require(
            root_response_sizes[baseline] == root_response_sizes[treatment],
            "native-parser navigation-stop root response content-length "
            "differs from control",
        )
    if {
        "tree-native-parser-document-start-navigation-stop-css",
        "tree-native-parser-document-start-response-stop-css",
    }.issubset(arms):
        baseline = "tree-native-parser-document-start-navigation-stop-css"
        treatment = "tree-native-parser-document-start-response-stop-css"
        for role in ("root", "stylesheet"):
            require(
                tree_semantics[baseline][role] == tree_semantics[treatment][role],
                "native-parser response-stop "
                f"{role} selected header values/order differ from control",
            )
        require(
            root_response_sizes[baseline] == root_response_sizes[treatment],
            "native-parser response-stop root response content-length "
            "differs from control",
        )
        require(
            tree_asset_sizes[baseline] == tree_asset_sizes[treatment],
            "native-parser response-stop CSS asset content-length differs "
            "from control",
        )

        ordered_gets = sorted(
            (
                row
                for row in cohorts[treatment]
                if row["direction"] == "client" and row["method"] == "GET"
            ),
            key=lambda row: row["packet_position"],
        )
        connects = [
            row
            for row in cohorts[treatment]
            if row["direction"] == "client" and row["method"] == "CONNECT"
        ]
        require(
            len(ordered_gets) == 2 and len(connects) == 1,
            f"{treatment} must contain root GET, CSS GET, and one CONNECT",
        )
        css_get = ordered_gets[1]
        css_responses = [
            row
            for row in cohorts[treatment]
            if row["direction"] == "server"
            and row["status"] == "200"
            and row["connection_index"] == css_get["connection_index"]
            and row["stream_id"] == css_get["stream_id"]
        ]
        require(
            len(css_responses) == 1,
            f"{treatment} must contain exactly one successful CSS response",
        )
        response_stop_wire_outcomes = validate_response_stop_wire_lifecycle(
            root,
            treatment,
            proxy_port,
            connects[0]["stream_id"],
            css_get["stream_id"],
            css_responses[0]["packet_position"],
            tree_asset_sizes[treatment][0],
        )
    if "tree-complete" in arms:
        for arm in preamble_arms:
            require(
                preamble_semantics[arm]["root"]
                == preamble_semantics["tree-complete"]["root"],
                f"{arm} root selected header values/order differ from tree-complete",
            )
            require(
                root_response_sizes[arm] == root_response_sizes["tree-complete"],
                f"{arm} root response content-length differs from tree-complete",
            )
    if {"tree-complete", "tree-overlap"}.issubset(arms):
        for role in ("root", "stylesheet", "script"):
            require(
                tree_semantics["tree-complete"][role]
                == tree_semantics["tree-overlap"][role],
                f"tree {role} GET selected header values/order differ between complete and overlap",
            )
        require(
            tree_asset_sizes["tree-complete"] == tree_asset_sizes["tree-overlap"],
            "tree asset content-lengths differ between complete and overlap",
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
            tree_asset_sizes["tree-complete"] == tree_asset_sizes["tree-root-overlap"],
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
    if {
        "tree-complete-css",
        "tree-resource-committed-overlap-css",
    }.issubset(arms):
        for role in ("root", "stylesheet"):
            require(
                tree_semantics["tree-complete-css"][role]
                == tree_semantics["tree-resource-committed-overlap-css"][role],
                f"resource-committed {role} GET selected header values/order differ",
            )
        require(
            tree_asset_sizes["tree-complete-css"]
            == tree_asset_sizes["tree-resource-committed-overlap-css"],
            "resource-committed CSS asset content-length differs",
        )
    if {
        "tree-complete-css",
        "tree-resource-native-cache-committed-overlap",
    }.issubset(arms):
        for role in ("root", "stylesheet"):
            require(
                tree_semantics["tree-complete-css"][role]
                == tree_semantics[
                    "tree-resource-native-cache-committed-overlap"
                ][role],
                f"native-cache-committed {role} GET selected header values/order differ",
            )
        require(
            tree_asset_sizes["tree-complete-css"]
            == tree_asset_sizes[
                "tree-resource-native-cache-committed-overlap"
            ],
            "native-cache-committed CSS asset content-length differs",
        )
    if {
        "tree-complete-css",
        "tree-native-parser-preload-overlap-css",
    }.issubset(arms):
        for role in ("root", "stylesheet"):
            require(
                tree_semantics["tree-complete-css"][role]
                == tree_semantics["tree-native-parser-preload-overlap-css"][role],
                f"native-parser-preload {role} GET selected header values/order differ",
            )
    if {
        "tree-complete-css",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
    }.issubset(arms):
        for baseline in (
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
        ):
            for role in ("root", "stylesheet"):
                require(
                    tree_semantics[baseline][role]
                    == tree_semantics["tree-native-parser-retarget-overlap-css"][role],
                    f"native-parser-retarget {role} GET selected header "
                    f"values/order differ from {baseline}",
                )
            require(
                tree_asset_sizes[baseline]
                == tree_asset_sizes["tree-native-parser-retarget-overlap-css"],
                "native-parser-retarget CSS asset content-length differs from "
                f"{baseline}",
            )
        require(
            tree_asset_sizes["tree-complete-css"]
            == tree_asset_sizes["tree-native-parser-preload-overlap-css"],
            "native-parser-preload CSS asset content-length differs",
        )
    if {
        "tree-complete-css",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
    }.issubset(arms):
        treatment = "tree-native-parser-ipc-rendezvous-overlap-css"
        for baseline in (
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
        ):
            for role in ("root", "stylesheet"):
                require(
                    tree_semantics[baseline][role]
                    == tree_semantics[treatment][role],
                    f"native-parser-ipc-rendezvous {role} GET selected header "
                    f"values/order differ from {baseline}",
                )
            require(
                tree_asset_sizes[baseline] == tree_asset_sizes[treatment],
                "native-parser-ipc-rendezvous CSS asset content-length differs "
                f"from {baseline}",
            )
    if {
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
    }.issubset(arms):
        treatment = "tree-native-parser-process-overlap-css"
        baseline = "tree-native-parser-root-rendezvous-overlap-css"
        for role in ("root", "stylesheet"):
            require(
                tree_semantics[baseline][role]
                == tree_semantics[treatment][role],
                f"native-parser-process {role} GET selected header values/order "
                "differ from root-rendezvous control",
            )
        require(
            tree_asset_sizes[baseline] == tree_asset_sizes[treatment],
            "native-parser-process CSS asset content-length differs from "
            "root-rendezvous control",
        )
    if {
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
    }.issubset(arms):
        treatment = "tree-native-parser-full-process-overlap-css"
        baseline = "tree-native-parser-process-overlap-css"
        for role in ("root", "stylesheet"):
            require(
                tree_semantics[baseline][role]
                == tree_semantics[treatment][role],
                f"native-parser-full-process {role} GET selected header "
                "values/order differ from process control",
            )
        require(
            tree_asset_sizes[baseline] == tree_asset_sizes[treatment],
            "native-parser-full-process CSS asset content-length differs "
            "from process control",
        )
    if {
        "tree-complete-css",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
    }.issubset(arms):
        treatment = "tree-native-parser-root-rendezvous-overlap-css"
        for baseline in (
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
        ):
            for role in ("root", "stylesheet"):
                require(
                    tree_semantics[baseline][role]
                    == tree_semantics[treatment][role],
                    f"native-parser-root-rendezvous {role} GET selected header "
                    f"values/order differ from {baseline}",
                )
            require(
                tree_asset_sizes[baseline] == tree_asset_sizes[treatment],
                "native-parser-root-rendezvous CSS asset content-length differs "
                f"from {baseline}",
            )
    if {
        "tree-complete-css",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
    }.issubset(arms):
        for baseline in (
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
        ):
            for role in ("root", "stylesheet"):
                require(
                    tree_semantics[baseline][role]
                    == tree_semantics[
                        "tree-native-parser-document-handoff-overlap-css"
                    ][role],
                    f"native-parser-document-handoff {role} GET selected "
                    f"header values/order differ from {baseline}",
                )
            require(
                tree_asset_sizes[baseline]
                == tree_asset_sizes[
                    "tree-native-parser-document-handoff-overlap-css"
                ],
                "native-parser-document-handoff CSS asset content-length "
                f"differs from {baseline}",
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
                preamble_mode = (
                    "document-complete"
                    if cohort in ("root", "root-pmtud-control")
                    else cohort
                )
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
                    "tree-resource-committed-overlap-css",
                    "tree-resource-native-cache-committed-overlap",
                    "tree-native-parser-preload-overlap-css",
                    "tree-native-parser-document-handoff-overlap-css",
                    "tree-native-parser-retarget-overlap-css",
                    "tree-native-parser-ipc-rendezvous-overlap-css",
                    "tree-native-parser-root-rendezvous-overlap-css",
                    "tree-native-parser-process-overlap-css",
                    "tree-native-parser-full-process-overlap-css",
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
        for cohort in sorted(tree_asset_sizes):
            if (
                cohort
                == "tree-native-parser-document-start-navigation-stop-css"
                and not tree_asset_sizes[cohort]
            ):
                continue
            destination.write(
                f"{cohort}_asset_content_lengths="
                f"{','.join(str(size) for size in tree_asset_sizes[cohort])}\n"
            )
        if {"tree-complete", "tree-overlap"}.issubset(arms):
            destination.write("tree_request_semantics_match=yes\n")
            destination.write("tree_asset_sizes_match=yes\n")
        if {"tree-complete", "tree-early-overlap"}.issubset(arms):
            destination.write("tree_early_overlap_request_semantics_match=yes\n")
            destination.write("tree_early_overlap_asset_sizes_match=yes\n")
        if {"tree-complete", "tree-root-overlap"}.issubset(arms):
            destination.write("tree_root_overlap_request_semantics_match=yes\n")
            destination.write("tree_root_overlap_asset_sizes_match=yes\n")
            destination.write("tree_root_overlap_wire_overlap_is_admission=no\n")
        if {"tree-complete-css", "tree-root-overlap-css"}.issubset(arms):
            destination.write("tree_root_overlap_css_request_semantics_match=yes\n")
            destination.write("tree_root_overlap_css_asset_sizes_match=yes\n")
            destination.write("tree_root_overlap_css_wire_overlap_is_admission=no\n")
        if {
            "tree-complete-css",
            "tree-resource-committed-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_resource_committed_request_semantics_match=yes\n"
            )
            destination.write("tree_resource_committed_asset_sizes_match=yes\n")
            destination.write(
                "tree_resource_committed_response_order_is_admission=no\n"
            )
        if {
            "tree-complete-css",
            "tree-resource-native-cache-committed-overlap",
        }.issubset(arms):
            destination.write(
                "tree_resource_native_cache_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_resource_native_cache_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_resource_native_cache_response_order_is_admission=no\n"
            )
        if {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_preload_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_preload_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_preload_overlap_validated=yes\n"
            )
        if {
            "document-start-overlap",
            "tree-native-parser-document-start-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_document_start_root_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_root_response_size_match=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_css_semantics_validated=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_root_get_before_connect=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_css_get_after_connect=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_css_response_fin_validated=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_late_barrier_required=no\n"
            )
        if {
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-start-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_document_start_parser_control_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_document_start_parser_control_asset_size_match=yes\n"
            )
        if {
            "tree-native-parser-document-start-overlap-css",
            "tree-native-parser-document-start-navigation-stop-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_navigation_stop_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_navigation_stop_root_response_size_match=yes\n"
            )
            destination.write(
                "tree_native_parser_navigation_stop_root_get_before_connect=yes\n"
            )
            destination.write(
                "tree_native_parser_navigation_stop_css_get_after_connect=yes\n"
            )
            destination.write(
                "tree_native_parser_navigation_stop_full_css_fin_required=no\n"
            )
            destination.write(
                "tree_native_parser_navigation_stop_runtime_lifecycle_validated=yes\n"
            )
        if {
            "tree-native-parser-document-start-navigation-stop-css",
            "tree-native-parser-document-start-response-stop-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_response_stop_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_response_stop_root_response_size_match=yes\n"
            )
            destination.write(
                "tree_native_parser_response_stop_asset_size_match=yes\n"
            )
            destination.write(
                "tree_native_parser_response_stop_server_connect_data_before_stop=yes\n"
            )
            destination.write(
                "tree_native_parser_response_stop_stop_before_reset=yes\n"
            )
            destination.write(
                "tree_native_parser_response_stop_h3_request_cancelled=yes\n"
            )
            destination.write(
                "tree_native_parser_response_stop_css_fin_observed=no\n"
            )
            destination.write(
                "tree_native_parser_response_stop_partial_css_data=yes\n"
            )
            for key in (
                "server_connect_data_packet_position",
                "server_connect_data_time_ms",
                "stop_sending_packet_position",
                "stop_sending_time_ms",
                "reset_stream_packet_position",
                "reset_stream_time_ms",
                "reset_stream_final_size",
                "css_data_length_before_reset",
                "css_content_length",
            ):
                destination.write(
                    f"tree_native_parser_response_stop_{key}="
                    f"{response_stop_wire_outcomes[key]}\n"
                )
        if {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_document_handoff_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_document_handoff_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_document_handoff_overlap_validated=yes\n"
            )
        if {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_retarget_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_retarget_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_retarget_overlap_validated=yes\n"
            )
        if {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_ipc_rendezvous_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_ipc_rendezvous_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_ipc_rendezvous_overlap_validated=yes\n"
            )
        if {
            "tree-complete-css",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_root_rendezvous_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_root_rendezvous_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_root_rendezvous_overlap_validated=yes\n"
            )
        if {
            "tree-native-parser-root-rendezvous-overlap-css",
            "tree-native-parser-process-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_process_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_process_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_process_overlap_validated=yes\n"
            )
        if {
            "tree-native-parser-process-overlap-css",
            "tree-native-parser-full-process-overlap-css",
        }.issubset(arms):
            destination.write(
                "tree_native_parser_full_process_request_semantics_match=yes\n"
            )
            destination.write(
                "tree_native_parser_full_process_asset_sizes_match=yes\n"
            )
            destination.write(
                "tree_native_parser_full_process_overlap_validated=yes\n"
            )
        if {"root", "root-pmtud-control"}.issubset(arms):
            destination.write("root_pmtud_control_request_semantics_match=yes\n")
            destination.write("root_pmtud_control_response_size_match=yes\n")
            destination.write("root_pmtud_control_wire_pmtud_claim=no\n")
        if {"document-complete", "document-overlap"}.issubset(arms):
            destination.write("document_overlap_request_semantics_match=yes\n")
            destination.write("document_overlap_response_size_match=yes\n")
            destination.write("document_overlap_wire_overlap_is_admission=no\n")
        if carrier_dispatch_admission_validated:
            destination.write("document_carrier_dispatch_single_connection=yes\n")
            destination.write("document_carrier_dispatch_single_client_hello=yes\n")
            destination.write("document_carrier_dispatch_no_carrier_request=yes\n")
            destination.write("document_carrier_dispatch_real_document_deferred=yes\n")
            destination.write("document_carrier_dispatch_normal_cm_dispatch=yes\n")
            destination.write("document_carrier_dispatch_same_session=yes\n")
            destination.write("document_carrier_dispatch_request_semantics_match=yes\n")
            destination.write("document_carrier_dispatch_response_size_match=yes\n")
            destination.write("document_carrier_dispatch_root_fin_before_connect=yes\n")
            destination.write("document_carrier_dispatch_lifecycle_exact=yes\n")
        if cold_winner_handoff_admission_validated:
            destination.write("document_cold_winner_single_connection=yes\n")
            destination.write("document_cold_winner_single_client_hello=yes\n")
            destination.write("document_cold_winner_single_proxy_attempt=yes\n")
            destination.write("document_cold_winner_no_carrier_request=yes\n")
            destination.write("document_cold_winner_no_zero_rtt=yes\n")
            destination.write("document_cold_winner_real_document_pending=yes\n")
            destination.write("document_cold_winner_racing_until_dispatch=yes\n")
            destination.write("document_cold_winner_async_activation_callback=yes\n")
            destination.write("document_cold_winner_exact_dispatch=yes\n")
            destination.write("document_cold_winner_publish_after_dispatch=yes\n")
            destination.write("document_cold_winner_request_semantics_match=yes\n")
            destination.write("document_cold_winner_response_size_match=yes\n")
            destination.write("document_cold_winner_root_fin_before_connect=yes\n")
        if native_cache_open_admission_validated:
            destination.write("document_native_cache_open_single_connection=yes\n")
            destination.write("document_native_cache_open_single_client_hello=yes\n")
            destination.write("document_native_cache_open_readonly_miss=yes\n")
            destination.write("document_native_cache_open_callback_async=yes\n")
            destination.write("document_native_cache_open_trigger_after_callback=yes\n")
            destination.write("document_native_cache_open_request_semantics_match=yes\n")
            destination.write("document_native_cache_open_response_size_match=yes\n")
            destination.write("document_native_cache_open_root_fin_before_connect=yes\n")
        if native_channel_open_admission_validated:
            destination.write("document_native_channel_open_single_connection=yes\n")
            destination.write("document_native_channel_open_single_client_hello=yes\n")
            destination.write("document_native_channel_open_cache_new_writable=yes\n")
            destination.write("document_native_channel_open_triggering_system=yes\n")
            destination.write("document_native_channel_open_uri_principal=yes\n")
            destination.write("document_native_channel_open_local_db=yes\n")
            destination.write("document_native_channel_open_expect_callback=yes\n")
            destination.write("document_native_channel_open_suspend_resume=yes\n")
            destination.write("document_native_channel_open_request_semantics_match=yes\n")
            destination.write("document_native_channel_open_response_size_match=yes\n")
            destination.write("document_native_channel_open_root_fin_before_connect=yes\n")
        if confirmed_admission_validated:
            destination.write(
                "document_handshake_confirmed_single_connection=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_single_client_hello=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_server_handshake_done_before_get=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_h3_initialization_before_handshake_done=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_settings_match=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_no_client_zero_rtt=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_request_semantics_match=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_response_size_match=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_root_fin_before_connect=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_lifecycle_exact=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_lifecycle_order_valid=yes\n"
            )
            destination.write(
                "document_handshake_confirmed_lifecycle_ids_match=yes\n"
            )
        if {
            "document-complete",
            "document-overlap",
            "document-start-overlap",
        }.issubset(arms):
            destination.write(
                "document_start_overlap_request_semantics_match=yes\n"
            )
            destination.write(
                "document_start_overlap_response_size_match=yes\n"
            )
            destination.write("document_start_overlap_get_before_connect=yes\n")
        if preamble_arms:
            destination.write("root_semantics_and_content_length_validated=yes\n")
        if "tree-complete" in arms and len(preamble_arms) > 1:
            destination.write("tree_complete_root_parity=yes\n")
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
