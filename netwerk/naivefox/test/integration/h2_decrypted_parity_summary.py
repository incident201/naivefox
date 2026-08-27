#!/usr/bin/env python3

import argparse
import csv
from pathlib import Path


SEPARATOR = "\x1f"
REDACTED_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}
SUPPORTED_ARMS = {
    "gate",
    "root",
    "document-complete",
    "document-start-overlap",
    "tree-complete",
    "tree-early-overlap",
    "tree-root-overlap",
    "tree-overlap",
    "tree-native-parser-document-start-overlap-css",
}
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


def require(condition, message):
    if not condition:
        raise ValueError(message)


def successful_status(status):
    try:
        value = int(status)
    except (TypeError, ValueError):
        return False
    return 200 <= value < 300


def values(value):
    return [item for item in (value or "").split(SEPARATOR) if item]


def direction(row, proxy_port):
    if row["tcp.dstport"] == proxy_port:
        return "client"
    if row["tcp.srcport"] == proxy_port:
        return "server"
    raise ValueError("decrypted H2 row is outside the proxy flow")


def read_rows(root, cohort, suffix):
    with (root / f"{cohort}-{suffix}.csv").open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def safe_name(name):
    lowered = name.lower()
    return "auth-or-cookie-redacted" if lowered in REDACTED_NAMES else lowered


def split_blocks(names, marker, count, context):
    starts = [index for index, name in enumerate(names) if name == marker]
    require(
        bool(names) and len(starts) == count and starts[0] == 0,
        f"{context} header block cardinality is ambiguous",
    )
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(names)
        block = names[start:end]
        require(block and block[0] == marker, f"{context} boundary is ambiguous")
        blocks.append(block)
    return blocks


def parse_frames(rows, cohort, proxy_port):
    end_stream = {}
    tcp_streams = set()
    for row in rows:
        tcp_stream = row["tcp.stream"]
        require(tcp_stream, f"{cohort} frame lacks TCP connection identity")
        tcp_streams.add(tcp_stream)
        types = values(row["http2.type"])
        streams = values(row["http2.streamid"])
        flags = values(row["http2.flags"])
        require(
            types and len(types) == len(streams) == len(flags),
            f"{cohort} H2 frame/type/stream/flags alignment is ambiguous",
        )
        event_direction = direction(row, proxy_port)
        frame = int(row["frame.number"])
        for frame_type, stream, raw_flags in zip(types, streams, flags):
            try:
                flag_bits = int(raw_flags, 0)
            except ValueError as error:
                raise ValueError(f"{cohort} H2 flags are not numeric") from error
            if frame_type in {"0", "1"} and flag_bits & 0x1:
                key = (event_direction, tcp_stream, stream)
                end_stream[key] = max(frame, end_stream.get(key, frame))
    return end_stream, tcp_streams


def parse_headers(rows, cohort, proxy_port):
    events = []
    for row in sorted(rows, key=lambda item: int(item["frame.number"])):
        tcp_stream = row["tcp.stream"]
        require(tcp_stream, f"{cohort} HEADERS lacks TCP connection identity")
        types = values(row["http2.type"])
        streams = values(row["http2.streamid"])
        require(
            types and len(types) == len(streams),
            f"{cohort} H2 HEADERS frame/stream alignment is ambiguous",
        )
        header_streams = [
            stream for frame_type, stream in zip(types, streams) if frame_type == "1"
        ]
        methods = values(row["http2.headers.method"])
        statuses = values(row["http2.headers.status"])
        require(
            bool(methods) != bool(statuses),
            f"{cohort} H2 method/status semantics are ambiguous",
        )
        semantic_values = methods if methods else statuses
        marker = ":method" if methods else ":status"
        names = [safe_name(name) for name in values(row["http2.header.name"])]
        blocks = split_blocks(names, marker, len(semantic_values), f"{cohort} H2")
        require(
            len(header_streams) == len(blocks),
            f"{cohort} H2 HEADERS block/stream mapping is ambiguous",
        )
        for stream, semantic, block in zip(header_streams, semantic_values, blocks):
            events.append({
                "frame": int(row["frame.number"]),
                "time": float(row["frame.time_relative"]),
                "direction": direction(row, proxy_port),
                "tcp_stream": tcp_stream,
                "stream": stream,
                "method": semantic if methods else "",
                "status": semantic if statuses else "",
                "header_order": tuple(dict.fromkeys(block)),
                "header_set": frozenset(block),
            })
    require(events, f"{cohort} has no decrypted H2 HEADERS")
    identities = {
        (
            event["direction"],
            event["tcp_stream"],
            event["stream"],
            event["method"],
            event["status"],
        )
        for event in events
    }
    require(len(identities) == len(events), f"{cohort} has duplicate H2 HEADERS")
    return events


def split_private_header_blocks(names, raw_values, marker, count, context):
    require(
        len(names) == len(raw_values) and bool(names),
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
        block = tuple(zip(names[start:end], raw_values[start:end]))
        require(block and block[0][0] == marker, f"{context} boundary is ambiguous")
        blocks.append(block)
    return blocks


def read_get_request_semantics(root, cohort, proxy_port, public_gets):
    rows = read_rows(root, cohort, "get-header-values")
    blocks = []
    for row in rows:
        require(
            direction(row, proxy_port) == "client",
            f"{cohort} private GET header extract contains a server block",
        )
        tcp_stream = row["tcp.stream"]
        require(tcp_stream, f"{cohort} private GET block lacks TCP identity")
        types = values(row["http2.type"])
        streams = values(row["http2.streamid"])
        require(
            types and len(types) == len(streams),
            f"{cohort} private GET frame/stream alignment is ambiguous",
        )
        header_streams = [
            stream for frame_type, stream in zip(types, streams) if frame_type == "1"
        ]
        methods = values(row["http2.headers.method"])
        require(
            bool(methods)
            and len(methods) == len(header_streams)
            and all(method == "GET" for method in methods),
            f"{cohort} private GET method/HEADERS-stream mapping is ambiguous",
        )
        names = [name.lower() for name in values(row["http2.header.name"])]
        raw_values = values(row["http2.header.value"])
        header_blocks = split_private_header_blocks(
            names, raw_values, ":method", len(methods), f"{cohort} private GET"
        )
        require(
            len(header_blocks) == len(header_streams),
            f"{cohort} private GET HEADERS block/stream mapping is ambiguous",
        )
        for stream, method, block in zip(header_streams, methods, header_blocks):
            block_names = [name for name, _ in block]
            require(
                not REDACTED_NAMES.intersection(block_names),
                f"{cohort} private GET block contains auth or cookie semantics",
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
            blocks.append({
                "frame": int(row["frame.number"]),
                "tcp_stream": tcp_stream,
                "stream": stream,
                "selected": selected,
            })
    private_identities = {
        (block["frame"], block["tcp_stream"], block["stream"]) for block in blocks
    }
    public_identities = {
        (event["frame"], event["tcp_stream"], event["stream"]) for event in public_gets
    }
    require(
        len(private_identities) == len(blocks)
        and private_identities == public_identities,
        f"{cohort} private/public GET stream mapping is ambiguous",
    )
    blocks.sort(key=lambda item: (item["frame"], int(item["stream"])))
    return blocks


def validate_expected_get_request_semantics(cohort, semantics, arm):
    expected_count = (
        0
        if arm == "gate"
        else 2
        if arm == "tree-native-parser-document-start-overlap-css"
        else 3
        if arm.startswith("tree-")
        else 1
    )
    require(
        len(semantics) == expected_count,
        f"{cohort} private GET semantics must contain exactly {expected_count} blocks",
    )
    if not semantics:
        return None
    by_path = {}
    for record in semantics:
        selected = record["selected"]
        names = [name for name, _ in selected]
        require(
            len(names) == len(set(names)),
            f"{cohort} selected GET semantics contain duplicates",
        )
        actual = dict(selected)
        path = actual.get(":path", "")
        require(path and path not in by_path, f"{cohort} GET paths are ambiguous")
        by_path[path] = (record, actual)
    root_path = "/camouflage/index.html"
    require(root_path in by_path, f"{cohort} lacks root document GET semantics")
    root_record, root = by_path[root_path]
    require(
        root.get(":method") == "GET"
        and root.get(":scheme") == "https"
        and root.get("sec-fetch-site") == "none"
        and root.get("sec-fetch-mode") == "navigate"
        and root.get("sec-fetch-dest") == "document"
        and root.get("priority") == "u=0, i"
        and "referer" not in root,
        f"{cohort} root document request semantics differ",
    )
    if not arm.startswith("tree-"):
        return (
            root_record["frame"],
            root_record["tcp_stream"],
            root_record["stream"],
        )
    expected_paths = {root_path, "/camouflage/style.css"}
    if arm != "tree-native-parser-document-start-overlap-css":
        expected_paths.add("/camouflage/app.js")
    require(set(by_path) == expected_paths, f"{cohort} tree resource paths differ")
    root_url = f"{root[':scheme']}://{root[':authority']}{root_path}"
    resources = [("/camouflage/style.css", "style")]
    if arm != "tree-native-parser-document-start-overlap-css":
        resources.append(("/camouflage/app.js", "script"))
    for path, destination in resources:
        _, resource = by_path[path]
        require(
            resource.get(":method") == "GET"
            and resource.get(":scheme") == root[":scheme"]
            and resource.get(":authority") == root[":authority"]
            and resource.get("referer") == root_url
            and resource.get("sec-fetch-site") == "same-origin"
            and resource.get("sec-fetch-mode") == "no-cors"
            and resource.get("sec-fetch-dest") == destination
            and resource.get("priority") == "u=2",
            f"{cohort} {destination} resource request semantics differ",
        )
    return (
        root_record["frame"],
        root_record["tcp_stream"],
        root_record["stream"],
    )


def settings_signature(rows, cohort, proxy_port):
    require(rows, f"{cohort} has no client H2 SETTINGS")
    ignored = {
        "frame.number",
        "frame.time_relative",
        "tcp.srcport",
        "tcp.dstport",
        "tcp.stream",
    }
    signature = []
    streams = set()
    for row in rows:
        require(
            direction(row, proxy_port) == "client",
            f"{cohort} SETTINGS direction differs",
        )
        require(row["tcp.stream"], f"{cohort} SETTINGS lacks TCP identity")
        streams.add(row["tcp.stream"])
        signature.append(
            tuple((name, row[name]) for name in row if name not in ignored)
        )
    require(len(streams) == 1, f"{cohort} SETTINGS span multiple TCP connections")
    return tuple(signature)


def grease(value):
    try:
        parsed = int(value, 0)
    except ValueError:
        return value
    return "GREASE" if parsed & 0x0F0F == 0x0A0A else value


def tls_signature(rows, cohort, proxy_port, client):
    require(rows, f"{cohort} lacks TLS negotiation evidence")
    ignored = {
        "frame.number",
        "frame.time_relative",
        "tcp.srcport",
        "tcp.dstport",
        "tcp.stream",
    }
    unordered = {
        "tls.handshake.extensions.supported_version",
        "tls.handshake.ciphersuite",
        "tls.handshake.extension.type",
        "tls.handshake.extensions_supported_group",
        "tls.handshake.sig_hash_alg",
        "tls.handshake.extensions_key_share_group",
    }
    signatures = set()
    for row in rows:
        require(
            direction(row, proxy_port) == ("client" if client else "server"),
            f"{cohort} TLS negotiation direction differs",
        )
        fields = []
        for name, raw in row.items():
            if name in ignored:
                continue
            selected = [grease(item) for item in values(raw)]
            if name == "tls.handshake.extensions_server_name":
                selected = ["present"] if selected else []
            elif name in unordered:
                selected.sort()
            fields.append((name, tuple(selected)))
        require(
            any(selected for _, selected in fields),
            f"{cohort} TLS negotiation extract is empty",
        )
        signatures.add(tuple(fields))
    require(
        len(signatures) == 1,
        f"{cohort} TLS negotiation evidence is internally inconsistent",
    )
    return next(iter(signatures))


def summarize_cohort(root, cohort, proxy_port):
    frames = read_rows(root, cohort, "frames")
    headers = read_rows(root, cohort, "headers")
    client_hellos = read_rows(root, cohort, "clienthello")
    server_hellos = read_rows(root, cohort, "serverhello")
    syns = read_rows(root, cohort, "syn")
    alpns = read_rows(root, cohort, "alpn")
    settings = read_rows(root, cohort, "settings")
    require(frames, f"{cohort} has no decrypted H2 frames")
    end_stream, frame_connections = parse_frames(frames, cohort, proxy_port)
    events = parse_headers(headers, cohort, proxy_port)
    event_connections = {event["tcp_stream"] for event in events}
    hello_connections = {
        row["tcp.stream"] for row in client_hellos if row["tcp.stream"]
    }
    syn_connections = {row["tcp.stream"] for row in syns if row["tcp.stream"]}
    all_connections = (
        frame_connections | event_connections | hello_connections | syn_connections
    )
    require(
        len(all_connections) == 1, f"{cohort} must use one physical TCP/H2 connection"
    )
    require(
        len(hello_connections) == 1,
        f"{cohort} must emit one outer ClientHello connection",
    )
    require(
        len(syn_connections) == 1, f"{cohort} must emit one outer client SYN connection"
    )
    require(
        alpns
        and all(
            "h2" in values(row["tls.handshake.extensions_alpn_str"]) for row in alpns
        ),
        f"{cohort} did not negotiate h2 ALPN",
    )
    connection = next(iter(all_connections))
    require(
        all(event["tcp_stream"] == connection for event in events),
        f"{cohort} H2 events changed TCP connection",
    )
    for event in events:
        event["end_stream_frame"] = end_stream.get(
            (event["direction"], connection, event["stream"]), ""
        )
    return (
        events,
        settings_signature(settings, cohort, proxy_port),
        tls_signature(client_hellos, cohort, proxy_port, True),
        tls_signature(server_hellos, cohort, proxy_port, False),
    )


def validate(
    reference,
    arm_events,
    arm,
    reference_settings,
    arm_settings,
    reference_client_tls,
    arm_client_tls,
    reference_server_tls,
    arm_server_tls,
    root_request_identity,
):
    require(reference_settings == arm_settings, "same-base client H2 SETTINGS differ")
    require(
        reference_client_tls == arm_client_tls,
        "same-base semantic TLS ClientHello differs",
    )
    require(
        reference_server_tls == arm_server_tls,
        "same-base TLS server negotiation differs",
    )
    reference_requests = [
        event
        for event in reference
        if event["direction"] == "client" and event["method"]
    ]
    require(
        any(event["method"] == "GET" for event in reference_requests),
        "reference has no outer GET",
    )
    require(
        not any(event["method"] == "CONNECT" for event in reference_requests),
        "reference unexpectedly used CONNECT",
    )

    requests = [
        event
        for event in arm_events
        if event["direction"] == "client" and event["method"]
    ]
    connects = [event for event in requests if event["method"] == "CONNECT"]
    require(connects, f"{arm} must emit at least one outer CONNECT")
    require(
        len({event["stream"] for event in connects}) == len(connects),
        f"{arm} CONNECT streams are not unique",
    )
    connect = min(connects, key=lambda event: event["frame"])
    for candidate in connects:
        require(
            "padding" in candidate["header_set"],
            f"{arm} CONNECT lacks request padding",
        )
        connect_responses = [
            event
            for event in arm_events
            if event["direction"] == "server"
            and event["stream"] == candidate["stream"]
            and event["status"] == "200"
        ]
        require(connect_responses, f"{arm} CONNECT lacks successful response")
        require(
            any("padding" in event["header_set"] for event in connect_responses),
            f"{arm} CONNECT lacks response padding",
        )

    gets = [event for event in requests if event["method"] == "GET"]
    expected_gets = (
        0
        if arm == "gate"
        else 2
        if arm == "tree-native-parser-document-start-overlap-css"
        else 3
        if arm.startswith("tree-")
        else 1
    )
    require(
        len(gets) == expected_gets,
        f"{arm} must emit exactly {expected_gets} preamble GETs",
    )
    if arm == "tree-native-parser-document-start-overlap-css":
        root_get = next(
            event
            for event in gets
            if (event["frame"], event["tcp_stream"], event["stream"])
            == root_request_identity
        )
        style_get = next(event for event in gets if event is not root_get)
        require(
            root_get["frame"] < connect["frame"] < style_get["frame"],
            f"{arm} must emit root GET before CONNECT and CSS GET after CONNECT",
        )
    else:
        require(
            all(event["frame"] < connect["frame"] for event in gets),
            f"{arm} preamble GET did not precede CONNECT",
        )
    responses = []
    for get in gets:
        matches = [
            event
            for event in arm_events
            if event["direction"] == "server"
            and event["stream"] == get["stream"]
            and (
                successful_status(event["status"])
                if arm == "document-start-overlap"
                else event["status"] == "200"
            )
        ]
        require(matches, f"{arm} preamble GET lacks successful response")
        responses.extend(matches)
    if arm in {"root", "document-complete"}:
        require(
            all(
                event["end_stream_frame"] != ""
                and event["end_stream_frame"] < connect["frame"]
                for event in responses
            ),
            f"{arm} document did not complete before CONNECT",
        )
    elif arm == "tree-complete":
        require(
            all(
                event["end_stream_frame"] != ""
                and event["end_stream_frame"] < connect["frame"]
                for event in responses
            ),
            "tree-complete resource did not complete before CONNECT",
        )
    elif arm in {"tree-early-overlap", "tree-overlap"}:
        require(
            any(
                event["frame"] < connect["frame"] < event["end_stream_frame"]
                for event in responses
                if event["end_stream_frame"] != ""
            ),
            f"{arm} lacks HEADERS < CONNECT < END_STREAM overlap",
        )
        if arm == "tree-early-overlap":
            require(
                root_request_identity is not None,
                "tree-early-overlap lacks private root path identity",
            )
            root_gets = [
                event
                for event in gets
                if (
                    event["frame"],
                    event["tcp_stream"],
                    event["stream"],
                )
                == root_request_identity
            ]
            require(
                len(root_gets) == 1,
                "tree-early-overlap private root identity is ambiguous",
            )
            root_get = root_gets[0]
            root_responses = [
                event
                for event in responses
                if event["tcp_stream"] == root_get["tcp_stream"]
                and event["stream"] == root_get["stream"]
            ]
            require(
                root_responses
                and all(
                    event["end_stream_frame"] != ""
                    and event["end_stream_frame"] < connect["frame"]
                    for event in root_responses
                ),
                "tree-early-overlap root did not complete before CONNECT",
            )
    elif arm == "tree-root-overlap":
        require(
            root_request_identity is not None,
            "tree-root-overlap lacks private root path identity",
        )
        root_gets = [
            event
            for event in gets
            if (event["frame"], event["tcp_stream"], event["stream"])
            == root_request_identity
        ]
        require(
            len(root_gets) == 1,
            "tree-root-overlap private root identity is ambiguous",
        )
        root_get = root_gets[0]
        root_responses = [
            event
            for event in responses
            if event["tcp_stream"] == root_get["tcp_stream"]
            and event["stream"] == root_get["stream"]
        ]
        require(
            root_responses
            and all(
                event["end_stream_frame"] != ""
                and event["end_stream_frame"] < connect["frame"]
                for event in root_responses
            ),
            "tree-root-overlap root did not complete before CONNECT",
        )
        require(
            all(
                any(
                    response["tcp_stream"] == get["tcp_stream"]
                    and response["stream"] == get["stream"]
                    and response["end_stream_frame"] != ""
                    for response in responses
                )
                for get in gets
            ),
            "tree-root-overlap lacks END_STREAM for an expected resource",
        )
        # Asset END_STREAM order relative to CONNECT is report-only. Product
        # admission is established by the causal lifecycle markers.
    elif arm == "document-start-overlap":
        require(
            all(event["end_stream_frame"] != "" for event in responses),
            "document-start-overlap document lacks END_STREAM",
        )
    elif arm == "tree-native-parser-document-start-overlap-css":
        require(
            all(event["end_stream_frame"] != "" for event in responses),
            f"{arm} root or stylesheet lacks END_STREAM",
        )


def write_outputs(root, events_path, summary_path, proxy_port, arm):
    require(arm in SUPPORTED_ARMS, "unsupported H2 diagnostic arm")
    (
        reference,
        reference_settings,
        reference_client_tls,
        reference_server_tls,
    ) = summarize_cohort(root, "reference", proxy_port)
    (
        candidate,
        candidate_settings,
        candidate_client_tls,
        candidate_server_tls,
    ) = summarize_cohort(root, arm, proxy_port)
    reference_gets = [
        event
        for event in reference
        if event["direction"] == "client" and event["method"] == "GET"
    ]
    candidate_gets = [
        event
        for event in candidate
        if event["direction"] == "client" and event["method"] == "GET"
    ]
    candidate_connects = [
        event
        for event in candidate
        if event["direction"] == "client" and event["method"] == "CONNECT"
    ]
    candidate_semantics = read_get_request_semantics(
        root, arm, proxy_port, candidate_gets
    )
    root_request_identity = validate_expected_get_request_semantics(
        arm, candidate_semantics, arm
    )
    root_overlap_observed = None
    if arm == "tree-root-overlap":
        connect_frame = min(event["frame"] for event in candidate_connects)
        root_key = (root_request_identity[1], root_request_identity[2])
        asset_streams = {
            (event["tcp_stream"], event["stream"])
            for event in candidate_gets
            if (event["tcp_stream"], event["stream"]) != root_key
        }
        root_overlap_observed = any(
            (event["tcp_stream"], event["stream"]) in asset_streams
            and event["frame"] < connect_frame < event["end_stream_frame"]
            for event in candidate
            if event["direction"] == "server"
            and event["status"] == "200"
            and event["end_stream_frame"] != ""
        )
    validate(
        reference,
        candidate,
        arm,
        reference_settings,
        candidate_settings,
        reference_client_tls,
        candidate_client_tls,
        reference_server_tls,
        candidate_server_tls,
        root_request_identity,
    )
    cohorts = (("reference", reference), (arm, candidate))
    fieldnames = (
        "cohort",
        "ordinal",
        "direction",
        "packet_position",
        "time_from_first_h2_ms",
        "stream_index",
        "method",
        "status",
        "header_name_order",
        "header_name_set",
        "end_stream_packet_position",
    )
    with events_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for cohort, events in cohorts:
            origin = min(event["time"] for event in events)
            stream_indices = {
                stream: index
                for index, stream in enumerate(
                    sorted({event["stream"] for event in events}, key=int), 1
                )
            }
            for ordinal, event in enumerate(
                sorted(events, key=lambda item: (item["frame"], int(item["stream"]))), 1
            ):
                writer.writerow({
                    "cohort": cohort,
                    "ordinal": ordinal,
                    "direction": event["direction"],
                    "packet_position": event["frame"],
                    "time_from_first_h2_ms": f"{(event['time'] - origin) * 1000:.3f}",
                    "stream_index": stream_indices[event["stream"]],
                    "method": event["method"],
                    "status": event["status"],
                    "header_name_order": ";".join(event["header_order"]),
                    "header_name_set": ";".join(sorted(event["header_set"])),
                    "end_stream_packet_position": event["end_stream_frame"],
                })
    with summary_path.open("w", encoding="utf-8") as output:
        output.write("capture_scope=same_base_h2_decrypted_outer_sequence\n")
        output.write("inner_transport=https\n")
        output.write(f"arm={arm}\n")
        output.write("reference_tcp_connections=1\n")
        output.write(f"{arm}_tcp_connections=1\n")
        output.write("reference_outer_clienthello_connections=1\n")
        output.write(f"{arm}_outer_clienthello_connections=1\n")
        output.write("selected_alpn=h2\n")
        output.write("semantic_clienthello_equal=yes\n")
        output.write("server_negotiation_equal=yes\n")
        output.write("client_settings_equal=yes\n")
        output.write(f"reference_outer_get_count={len(reference_gets)}\n")
        output.write(f"{arm}_preamble_get_count={len(candidate_gets)}\n")
        output.write(f"{arm}_outer_connect_count={len(candidate_connects)}\n")
        if arm == "tree-native-parser-document-start-overlap-css":
            output.write(f"{arm}_root_before_first_connect=yes\n")
            output.write(f"{arm}_stylesheet_after_first_connect=yes\n")
        else:
            output.write(f"{arm}_preamble_before_first_connect=yes\n")
        output.write(f"{arm}_sequence_validation=passed\n")
        if arm != "gate":
            output.write(f"{arm}_root_document_request_semantics=yes\n")
        if arm.startswith("tree-"):
            output.write(f"{arm}_resource_request_semantics=yes\n")
        if arm == "tree-native-parser-document-start-overlap-css":
            output.write(
                "tree-native-parser-document-start-overlap-css_"
                "wire_order=root_get_connect_css_get\n"
            )
            output.write(
                "tree-native-parser-document-start-overlap-css_"
                "root_and_css_end_stream=yes\n"
            )
        if arm == "tree-root-overlap":
            output.write("tree-root-overlap_wire_overlap_is_admission=no\n")
            output.write(
                "tree-root-overlap_wire_overlap_observed="
                f"{'yes' if root_overlap_observed else 'no'}\n"
            )
        if arm == "document-start-overlap":
            output.write("document-start-overlap_document_end_stream=yes\n")
            output.write("document-start-overlap_end_stream_position_is_admission=no\n")
        output.write("header_values_retained=no\n")
        output.write("credential_header_names_redacted=yes\n")
        output.write("raw_capture_material=deleted_after_success\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--proxy-port", required=True)
    parser.add_argument("--arm", required=True)
    args = parser.parse_args()
    write_outputs(args.input_dir, args.events, args.summary, args.proxy_port, args.arm)


if __name__ == "__main__":
    main()
