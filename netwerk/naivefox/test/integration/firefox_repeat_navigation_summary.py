#!/usr/bin/env python3

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import h3_decrypted_arm_summary as h3

ASSET_ROLES = {
    "stylesheet": "/camouflage/style.css?nav={nav}",
    "script": "/camouflage/app.js?nav={nav}",
    "image-64k": "/camouflage/resource?size=65536&nav={nav}",
    "image-128k": "/camouflage/resource?size=131072&nav={nav}",
    "image-256k": "/camouflage/resource?size=262144&nav={nav}",
    "api": "/camouflage/api?nav={nav}",
}
CONDITIONAL_REQUEST_HEADERS = {"if-modified-since", "if-none-match", "if-range"}
LOG_PREFIX = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) UTC - "
    r"\[(?P<process>[^]]+)\]: [A-Z]/[^ ]+ (?P<message>.*)$"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as source:
        header = source.readline()
        source.seek(0)
        delimiter = "\t" if "\t" in header else ","
        return list(csv.DictReader(source, delimiter=delimiter))


def constrained_header_blocks(
    path,
    marker,
    value_column,
    marker_column,
    proxy_port,
    expected_direction,
):
    constraints = []
    with path.open(encoding="utf-8") as source:
        private_separator = (
            "~" if "\t" in source.readline() else h3.PRIVATE_VALUE_SEPARATOR
        )

    def values(value):
        return value.split(private_separator) if value else []

    for row in read_csv(path):
        require(
            h3.direction(row, str(proxy_port)) == expected_direction,
            "private header block has the wrong direction",
        )
        marker_values = values(row[marker_column])
        streams = list(dict.fromkeys(values(row["quic.stream.stream_id"])))
        connections = values(row["quic.connection.number"])
        names = [name.lower() for name in values(row["http3.header.header.name"])]
        require(marker_values, "private header row lacks its block marker")
        require(
            len(streams) >= len(marker_values),
            "private header row has fewer streams than header blocks",
        )
        h3.require_one_connection(connections, len(streams), "repeat navigation")
        header_values = values(row[value_column])
        parsed = h3.split_private_header_blocks(
            names,
            header_values,
            marker,
            len(marker_values),
            "repeat navigation",
        )
        constraints.append({
            "blocks": tuple(zip(marker_values, parsed)),
            "connection": connections[0],
            "frame": int(row["frame.number"]),
            "streams": tuple(streams),
        })

    solutions = []

    def resolve(index, claimed, blocks):
        if len(solutions) > 1:
            return
        if index == len(constraints):
            solutions.append(tuple(blocks))
            return
        constraint = constraints[index]
        count = len(constraint["blocks"])
        for selected in combinations(constraint["streams"], count):
            keys = tuple((constraint["connection"], stream) for stream in selected)
            if any(key in claimed for key in keys):
                continue
            additions = []
            for stream, (value, headers) in zip(selected, constraint["blocks"]):
                addition = {
                    "connection": constraint["connection"],
                    "frame": constraint["frame"],
                    "headers": tuple(headers),
                    "stream": stream,
                    "value": value,
                }
                additions.append(addition)
            resolve(index + 1, claimed.union(keys), blocks + additions)

    resolve(0, set(), [])
    require(len(solutions) == 1, "header-to-stream constraint mapping is ambiguous")
    return list(solutions[0])


def private_header_blocks(path, marker, value_column, method_column, proxy_port):
    return constrained_header_blocks(
        path, marker, value_column, method_column, proxy_port, "client"
    )


def response_header_blocks(path, proxy_port):
    return constrained_header_blocks(
        path,
        ":status",
        "http3.headers.header.value",
        "http3.headers.status",
        proxy_port,
        "server",
    )


def response_content_length(block):
    selected = [value for name, value in block["headers"] if name == "content-length"]
    if len(selected) == 1 and not selected[0].isdigit():
        etags = [value for name, value in block["headers"] if name == "etag"]
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
        "resource response has invalid Content-Length semantics",
    )
    return int(selected[0])


def request_role(path, nav, completion):
    root = (
        "/camouflage/index.html?scenario=browser_page&size=262144&count=4"
        f"&idle_ms=5000&completion={completion}&nav={nav}"
    )
    if path == root:
        return "root"
    for role, pattern in ASSET_ROLES.items():
        if path == pattern.format(nav=nav):
            return role
    return None


def normalized_headers(headers, nav, completion):
    normalized = []
    for name, value in headers:
        value = value.replace(nav, "{nav}").replace(completion, "{completion}")
        normalized.append((name, value))
    return tuple(normalized)


def validate_browser_identity(path):
    with path.open(encoding="utf-8") as source:
        evidence = json.load(source)
    first = evidence.get("navigation_1", {})
    second = evidence.get("navigation_2", {})
    for field in (
        "browser_pid",
        "content_pid",
        "current_window_handle",
        "webdriver_session_id",
    ):
        require(first.get(field), f"navigation 1 lacks {field}")
        require(second.get(field), f"navigation 2 lacks {field}")
        require(first[field] == second[field], f"Firefox changed {field}")
    for index, navigation in enumerate((first, second), start=1):
        require(
            navigation.get("window_handles") == [navigation["current_window_handle"]],
            f"navigation {index} did not use exactly one stable Firefox tab",
        )
    for navigation in (first, second):
        require(
            navigation.get("browsing_context_id"),
            "navigation lacks a browsing context id",
        )
    return {
        "browser_pid_stable": True,
        "browsing_context_stable": (
            first["browsing_context_id"] == second["browsing_context_id"]
        ),
        "content_process_stable": True,
        "single_tab_stable": True,
        "webdriver_session_stable": True,
    }


def read_lifecycle_lines(paths):
    lines = []
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as source:
            for number, raw in enumerate(source, start=1):
                match = LOG_PREFIX.match(raw.rstrip("\n"))
                if match is None:
                    continue
                timestamp = (
                    datetime
                    .strptime(match.group("time"), "%Y-%m-%d %H:%M:%S.%f")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
                lines.append({
                    "file": path,
                    "message": match.group("message"),
                    "number": number,
                    "process": match.group("process"),
                    "time": timestamp,
                })
    return sorted(
        lines, key=lambda line: (line["time"], str(line["file"]), line["number"])
    )


def one_line(lines, pattern, description, after=None, before=None):
    matches = []
    for line in lines:
        if after is not None and line["time"] < after:
            continue
        if before is not None and line["time"] > before:
            continue
        match = re.search(pattern, line["message"])
        if match is not None:
            matches.append((line, match))
    require(len(matches) == 1, f"{description} lifecycle mapping is ambiguous")
    return matches[0]


def child_channel_for_uri(lines, uri):
    candidates = []
    for index, line in enumerate(lines):
        if line["message"] != f"uri={uri}":
            continue
        preceding = lines[max(0, index - 8) : index]
        creations = []
        for candidate in preceding:
            match = re.search(
                r"Creating HttpChannelChild @([0-9a-f]+)$",
                candidate["message"],
            )
            if match is not None and candidate["file"] == line["file"]:
                creations.append(match.group(1))
        if len(creations) == 1:
            candidates.append(creations[0])
    require(len(set(candidates)) == 1, "root child channel mapping is ambiguous")
    return candidates[0]


def stylesheet_descriptor_for_uri(lines, uri, after, before):
    candidates = []
    for index, line in enumerate(lines):
        if line["time"] < after or line["time"] > before:
            continue
        if line["message"] != "css::Loader::LoadSheet(aURL, aObserver) api call":
            continue
        if index + 1 >= len(lines):
            continue
        uri_line = lines[index + 1]
        if (
            uri_line["file"] == line["file"]
            and uri_line["number"] == line["number"] + 1
            and uri_line["process"] == line["process"]
            and uri_line["message"] == f"  Non-document sheet uri: '{uri}'"
            and 0 <= uri_line["time"] - line["time"] <= 0.001
        ):
            candidates.append(line)
    require(
        len(candidates) == 1,
        "parser-discovered stylesheet descriptor lifecycle mapping is ambiguous",
    )
    return candidates[0]


def parse_navigation_lifecycle(
    parent_lines, child_lines, root_uri, css_uri, root_event, css_event, wire_offset
):
    root_wire = root_event["time"] + wire_offset
    css_wire = css_event["time"] + wire_offset
    root_cache, root_cache_match = one_line(
        parent_lines,
        rf"nsHttpChannel::OnCacheEntryAvailable \[this=([0-9a-f]+).* for {re.escape(root_uri)}$",
        "root parent channel",
        before=css_wire,
    )
    root_channel = root_cache_match.group(1)
    suspend, _ = one_line(
        parent_lines,
        rf"nsHttpChannel::Suspend \[this={root_channel}\]$",
        "root Suspend",
        after=root_wire,
        before=css_wire,
    )
    resume, _ = one_line(
        parent_lines,
        rf"nsHttpChannel::ResumeInternal \[this={root_channel}\]$",
        "root Resume",
        after=suspend["time"],
        before=css_wire,
    )

    root_child = child_channel_for_uri(child_lines, root_uri)
    parser_body, _ = one_line(
        child_lines,
        rf"HttpChannelChild::DoOnDataAvailable \[this={root_child}, request=[0-9a-f]+\]$",
        "HTML5 parser body",
        after=resume["time"],
        before=css_wire,
    )
    require(
        parser_body["process"].endswith(": HTML5 Parser"),
        "root body was not delivered on the HTML5 Parser thread",
    )
    descriptor = stylesheet_descriptor_for_uri(
        child_lines,
        css_uri,
        parser_body["time"],
        css_wire,
    )
    child_open, child_open_match = one_line(
        child_lines,
        rf"HttpChannelChild::AsyncOpen \[this=([0-9a-f]+) uri={re.escape(css_uri)}\]$",
        "CSS child AsyncOpen",
        after=descriptor["time"],
        before=css_wire,
    )
    css_child = child_open_match.group(1)
    require(css_child != root_child, "CSS reused the root child channel")

    child_openargs_ready, child_openargs_match = one_line(
        child_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=child-openargs-ready "
        rf"child={css_child} channelId=([0-9]+) browserId=[0-9a-f]+ "
        r"contentWindowId=[0-9]+$",
        "CSS child open-args readiness",
        after=child_open["time"],
        before=css_wire,
    )
    channel_id = child_openargs_match.group(1)
    child_send_return, _ = one_line(
        child_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=child-send-return "
        rf"child={css_child} channelId={channel_id} sent=1$",
        "CSS child constructor send return",
        after=child_openargs_ready["time"],
        before=css_wire,
    )

    parent_recv, parent_recv_match = one_line(
        parent_lines,
        rf"HttpChannelParent RecvAsyncOpen \[this=([0-9a-f]+) uri={re.escape(css_uri)},",
        "CSS parent RecvAsyncOpen",
        after=child_open["time"],
        before=css_wire,
    )
    parent_channel = parent_recv_match.group(1)
    parent_alloc, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-alloc-enter "
        rf"necko=[0-9a-f]+ channelId={channel_id} browserId=[0-9a-f]+$",
        "CSS parent actor allocation",
        after=child_openargs_ready["time"],
        before=parent_recv["time"],
    )
    parent_recv_enter, parent_recv_enter_match = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-recv-enter "
        rf"actor=([0-9a-f]+) channelId={channel_id} browserId=[0-9a-f]+$",
        "CSS parent constructor receive",
        after=parent_alloc["time"],
        before=parent_recv["time"],
    )
    require(
        int(parent_recv_enter_match.group(1), 16) - int(parent_channel, 16) == 8,
        "CSS IPDL actor/object pointer relationship changed",
    )
    parent_wait_start, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-wait-start "
        rf"parent={parent_channel} channelId={channel_id}$",
        "CSS parent background wait start",
        after=parent_recv["time"],
        before=css_wire,
    )
    parent_link_return, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC "
        rf"phase=parent-registrar-link-return parent={parent_channel} "
        rf"channelId={channel_id} ready=[01]$",
        "CSS parent registrar link",
        after=parent_wait_start["time"],
        before=css_wire,
    )
    background_init, background_init_match = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=background-init-enter "
        rf"background=([0-9a-f]+) channelId={channel_id}$",
        "CSS background actor initialization",
        after=child_send_return["time"],
        before=css_wire,
    )
    background = background_init_match.group(1)
    background_dispatch_run, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC "
        rf"phase=background-main-dispatch-run background={background} "
        rf"channelId={channel_id}$",
        "CSS background main-thread dispatch",
        after=background_init["time"],
        before=css_wire,
    )
    background_link_return, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC "
        rf"phase=background-registrar-link-return background={background} "
        rf"channelId={channel_id}$",
        "CSS background registrar link",
        after=background_dispatch_run["time"],
        before=css_wire,
    )
    parent_background_ready, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-background-ready "
        rf"parent={parent_channel} background={background} "
        rf"channelId={channel_id}$",
        "CSS parent background readiness",
        after=max(parent_wait_start["time"], background_dispatch_run["time"]),
        before=css_wire,
    )
    parent_wait_resolved, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-wait-resolved "
        rf"parent={parent_channel} channelId={channel_id} status=success$",
        "CSS parent background wait resolution",
        after=parent_background_ready["time"],
        before=css_wire,
    )
    parent_try_invoke, _ = one_line(
        parent_lines,
        rf"NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC phase=parent-try-invoke "
        rf"parent={parent_channel} channelId={channel_id} barrier=1 rv=0$",
        "CSS parent async-open barrier release",
        after=parent_wait_resolved["time"],
        before=css_wire,
    )
    parent_invoke, _ = one_line(
        parent_lines,
        rf"HttpChannelParent::InvokeAsyncOpen \[this={parent_channel} rv=0\]$",
        "CSS parent InvokeAsyncOpen",
        after=parent_recv["time"],
        before=css_wire,
    )
    require(
        parent_try_invoke["time"] <= parent_invoke["time"],
        "CSS parent invoked before its background barrier release",
    )
    channel_open, channel_open_match = one_line(
        parent_lines,
        r"nsHttpChannel::AsyncOpen \[this=([0-9a-f]+)\]$",
        "CSS parent nsHttpChannel AsyncOpen",
        after=parent_invoke["time"],
        before=parent_invoke["time"] + 0.0001,
    )
    channel = channel_open_match.group(1)
    dispatch, _ = one_line(
        parent_lines,
        rf"nsHttpChannel::DispatchTransaction \[this={channel}, aTransWithStickyConn=0\]$",
        "CSS channel DispatchTransaction",
        after=channel_open["time"],
        before=css_wire,
    )
    transaction_owned, transaction_owned_match = one_line(
        parent_lines,
        rf"nsHttpChannel {channel} created nsHttpTransaction ([0-9a-f]+)$",
        "CSS channel-to-transaction ownership",
        after=dispatch["time"],
        before=css_wire,
    )
    transaction_interface = transaction_owned_match.group(1)
    transaction = f"{int(transaction_interface, 16) - 0x10:x}"
    transaction_created, _ = one_line(
        parent_lines,
        rf"Creating nsHttpTransaction @{transaction}$",
        "CSS transaction creation",
        after=dispatch["time"],
        before=transaction_owned["time"],
    )
    require(
        0 <= transaction_owned["time"] - transaction_created["time"] <= 0.0001,
        "CSS transaction interface/base pointer relationship changed",
    )
    socket_new, _ = one_line(
        parent_lines,
        rf"nsHttpConnectionMgr::OnMsgNewTransaction \[trans={transaction}\]$",
        "CSS socket new transaction",
        after=dispatch["time"],
        before=css_wire,
    )
    socket_dispatch, _ = one_line(
        parent_lines,
        rf"nsHttpConnectionMgr::DispatchTransaction .* trans={transaction} .*isHttp3=1\]$",
        "CSS socket H3 dispatch",
        after=socket_new["time"],
        before=css_wire,
    )
    add_stream, _ = one_line(
        parent_lines,
        rf"Http3Session::AddStream [0-9a-f]+ atrans={transaction}\.$",
        "CSS H3 AddStream",
        after=socket_dispatch["time"],
        before=css_wire,
    )

    ordered = (
        root_wire,
        suspend["time"],
        resume["time"],
        parser_body["time"],
        descriptor["time"],
        child_open["time"],
        child_openargs_ready["time"],
        # The child send return and parent allocation are concurrent after the
        # pre-send marker, so their relative order is not required here.
        parent_alloc["time"],
        parent_recv_enter["time"],
        parent_recv["time"],
        parent_wait_start["time"],
        parent_wait_resolved["time"],
        parent_try_invoke["time"],
        parent_invoke["time"],
        channel_open["time"],
        dispatch["time"],
        socket_new["time"],
        socket_dispatch["time"],
        add_stream["time"],
        css_wire,
    )
    require(list(ordered) == sorted(ordered), "CSS lifecycle order is invalid")
    require(
        child_openargs_ready["time"] <= child_send_return["time"],
        "CSS constructor returned before its open args were ready",
    )
    require(
        child_send_return["time"] <= background_init["time"],
        "CSS background actor started before the primary constructor returned",
    )
    require(
        background_init["time"]
        <= background_dispatch_run["time"]
        <= background_link_return["time"],
        "CSS background actor branch order is invalid",
    )
    require(
        parent_wait_start["time"] <= parent_link_return["time"],
        "CSS parent registrar returned before its wait began",
    )
    return {
        "root_headers_to_css_get_ms": (css_wire - root_wire) * 1000,
        "root_suspend_to_resume_ms": (resume["time"] - suspend["time"]) * 1000,
        "root_resume_to_parser_body_ms": (parser_body["time"] - resume["time"]) * 1000,
        "parser_body_to_css_descriptor_ms": (descriptor["time"] - parser_body["time"])
        * 1000,
        "css_descriptor_to_child_async_open_ms": (
            child_open["time"] - descriptor["time"]
        )
        * 1000,
        "css_child_async_open_to_parent_recv_ms": (
            parent_recv["time"] - child_open["time"]
        )
        * 1000,
        "css_child_async_open_to_openargs_ready_ms": (
            child_openargs_ready["time"] - child_open["time"]
        )
        * 1000,
        "css_openargs_ready_to_send_return_ms": (
            child_send_return["time"] - child_openargs_ready["time"]
        )
        * 1000,
        "css_openargs_ready_to_parent_alloc_ms": (
            parent_alloc["time"] - child_openargs_ready["time"]
        )
        * 1000,
        "css_send_return_to_parent_alloc_ms": (
            parent_alloc["time"] - child_send_return["time"]
        )
        * 1000,
        "css_parent_alloc_to_recv_enter_ms": (
            parent_recv_enter["time"] - parent_alloc["time"]
        )
        * 1000,
        "css_parent_recv_enter_to_do_async_open_ms": (
            parent_recv["time"] - parent_recv_enter["time"]
        )
        * 1000,
        "css_parent_do_async_open_to_wait_start_ms": (
            parent_wait_start["time"] - parent_recv["time"]
        )
        * 1000,
        "css_parent_wait_start_to_resolved_ms": (
            parent_wait_resolved["time"] - parent_wait_start["time"]
        )
        * 1000,
        "css_background_init_to_main_run_ms": (
            background_dispatch_run["time"] - background_init["time"]
        )
        * 1000,
        "css_background_main_run_to_link_return_ms": (
            background_link_return["time"] - background_dispatch_run["time"]
        )
        * 1000,
        "css_background_ready_to_wait_resolved_ms": (
            parent_wait_resolved["time"] - parent_background_ready["time"]
        )
        * 1000,
        "css_wait_resolved_to_invoke_ms": (
            parent_invoke["time"] - parent_wait_resolved["time"]
        )
        * 1000,
        "css_parent_recv_to_invoke_ms": (parent_invoke["time"] - parent_recv["time"])
        * 1000,
        "css_parent_invoke_to_channel_async_open_ms": (
            channel_open["time"] - parent_invoke["time"]
        )
        * 1000,
        "css_channel_async_open_to_dispatch_ms": (
            dispatch["time"] - channel_open["time"]
        )
        * 1000,
        "css_dispatch_to_socket_new_ms": (socket_new["time"] - dispatch["time"]) * 1000,
        "css_socket_new_to_h3_dispatch_ms": (
            socket_dispatch["time"] - socket_new["time"]
        )
        * 1000,
        "css_h3_dispatch_to_add_stream_ms": (
            add_stream["time"] - socket_dispatch["time"]
        )
        * 1000,
        "css_add_stream_to_wire_get_ms": (css_wire - add_stream["time"]) * 1000,
    }


def analyze(args):
    root = Path(args.root)
    proxy_port = str(args.proxy_port)
    tokens = (args.nav1, args.nav2, args.completion1, args.completion2)
    require(
        all(re.fullmatch(r"[0-9a-f]{32}", token) for token in tokens),
        "navigation/completion token format is invalid",
    )
    require(len(set(tokens)) == len(tokens), "navigation/completion tokens are reused")
    request_rows = read_csv(root / "repeat-requests.csv")
    header_rows = read_csv(root / "repeat-header-names.csv")
    events = h3.read_http3_events(request_rows, header_rows, "repeat", proxy_port)
    wire_offsets = [
        float(row["frame.time_epoch"]) - float(row["frame.time_relative"])
        for row in request_rows
        if row["frame.time_epoch"] and row["frame.time_relative"]
    ]
    require(wire_offsets, "request extract lacks epoch timestamps")
    require(
        max(wire_offsets) - min(wire_offsets) < 0.00001,
        "capture relative/epoch timestamp mapping is unstable",
    )
    wire_offset = sum(wire_offsets) / len(wire_offsets)
    gets = private_header_blocks(
        root / "repeat-get-header-values.csv",
        ":method",
        "http3.headers.header.value",
        "http3.headers.method",
        proxy_port,
    )
    responses = response_header_blocks(
        root / "repeat-response-header-values.csv", proxy_port
    )
    require(len(gets) == 14, "repeat navigation did not emit exactly fourteen GETs")

    by_navigation = []
    stream_roles = {}
    for index, (nav, completion) in enumerate(
        ((args.nav1, args.completion1), (args.nav2, args.completion2)), start=1
    ):
        roles = {}
        for block in gets:
            headers = dict(block["headers"])
            require(headers.get(":method") == "GET", "non-GET in private GET extract")
            role = request_role(headers.get(":path", ""), nav, completion)
            if role is None:
                continue
            require(role not in roles, f"navigation {index} duplicated {role}")
            require(
                not CONDITIONAL_REQUEST_HEADERS.intersection(headers),
                f"navigation {index} used a conditional request",
            )
            roles[role] = block
            stream_roles[(block["connection"], block["stream"])] = (index, role)
        expected = {"root", *ASSET_ROLES}
        require(
            set(roles) == expected, f"navigation {index} resource set is incomplete"
        )
        by_navigation.append(roles)

    require(
        max(block["frame"] for block in by_navigation[0].values())
        < by_navigation[1]["root"]["frame"],
        "navigation request sequences overlap",
    )
    for role in ("root", *ASSET_ROLES):
        first = normalized_headers(
            by_navigation[0][role]["headers"], args.nav1, args.completion1
        )
        second = normalized_headers(
            by_navigation[1][role]["headers"], args.nav2, args.completion2
        )
        require(first == second, f"{role} request semantics changed")

    response_by_stream = {}
    for block in responses:
        key = (block["connection"], block["stream"])
        if key not in stream_roles:
            continue
        require(key not in response_by_stream, "duplicate response HEADERS")
        require(block["value"] == "200", "resource response was not HTTP 200")
        headers = dict(block["headers"])
        require(
            not h3.REDACTED_HEADER_NAMES.intersection(headers),
            "resource response contains auth or cookie semantics",
        )
        require("content-length" in headers, "resource response lacks Content-Length")
        response_by_stream[key] = response_content_length(block)
    require(
        set(response_by_stream) == set(stream_roles),
        "not every root/resource stream has one response",
    )
    for role in ("root", *ASSET_ROLES):
        first = by_navigation[0][role]
        second = by_navigation[1][role]
        require(
            response_by_stream[(first["connection"], first["stream"])]
            == response_by_stream[(second["connection"], second["stream"])],
            f"{role} response size changed",
        )

    connections = {block["connection"] for block in gets}
    require(len(connections) == 1, "navigations did not share one QUIC identity")
    client_hellos = read_csv(root / "repeat-clienthello.csv")
    hello_connections = {
        row["quic.connection.number"]
        for row in client_hellos
        if row["quic.connection.number"]
    }
    require(len(client_hellos) == 1, "capture does not contain one ClientHello")
    require(hello_connections == connections, "ClientHello QUIC identity mismatch")

    parent_log = root / "repeat-lifecycle.moz_log"
    child_logs = sorted(root.glob("repeat-lifecycle.child-*.moz_log"))
    require(parent_log.is_file(), "parent lifecycle log is missing")
    require(child_logs, "child lifecycle log is missing")
    parent_lines = read_lifecycle_lines((parent_log,))
    child_lines = read_lifecycle_lines(child_logs)
    lifecycles = []
    for index, roles in enumerate(by_navigation, start=1):
        root_block = roles["root"]
        css_block = roles["stylesheet"]
        root_event = events.get((
            "server",
            root_block["connection"],
            root_block["stream"],
            "",
            "200",
        ))
        css_event = events.get((
            "client",
            css_block["connection"],
            css_block["stream"],
            "GET",
            "",
        ))
        require(root_event is not None, f"navigation {index} root response is missing")
        require(css_event is not None, f"navigation {index} CSS GET is missing")
        root_uri = (
            f"https://localhost:{args.proxy_port}{dict(root_block['headers'])[':path']}"
        )
        css_uri = (
            f"https://localhost:{args.proxy_port}{dict(css_block['headers'])[':path']}"
        )
        lifecycles.append(
            parse_navigation_lifecycle(
                parent_lines,
                child_lines,
                root_uri,
                css_uri,
                root_event,
                css_event,
                wire_offset,
            )
        )

    identity = validate_browser_identity(Path(args.navigation_evidence))
    result = {
        **identity,
        "cache_busting_asset_urls": True,
        "cold_unconditional_responses": True,
        "one_client_hello": True,
        "one_quic_identity": True,
        "request_semantics_equal": True,
        "response_sizes_equal": True,
        "navigation_2_minus_1_ms": (
            lifecycles[1]["root_headers_to_css_get_ms"]
            - lifecycles[0]["root_headers_to_css_get_ms"]
        ),
    }
    for index, lifecycle in enumerate(lifecycles, start=1):
        for name, value in lifecycle.items():
            result[f"navigation_{index}_{name}"] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--navigation-evidence", required=True)
    parser.add_argument("--nav1", required=True)
    parser.add_argument("--nav2", required=True)
    parser.add_argument("--completion1", required=True)
    parser.add_argument("--completion2", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = analyze(args)
    output = Path(args.output)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write("reference_repeat_navigation=pass\n")
        for name in sorted(result):
            value = result[name]
            if isinstance(value, bool):
                value = int(value)
            elif isinstance(value, float):
                value = f"{value:.3f}"
            stream.write(f"{name}={value}\n")
    temporary.replace(output)


if __name__ == "__main__":
    main()
