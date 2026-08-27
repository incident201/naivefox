#!/usr/bin/env python3

import argparse
import csv
import re
from pathlib import Path

import h2_decrypted_parity_summary as parity

COHORTS = ("firefox-proxied", "naivefox-default", "naivefox-urgent")
URGENT_MARKER_PREFIX = "diagnostic-first-socks-tunnel-urgent-start"
CANONICAL_LOG_PREFIX = r"\[[0-9]{4}/[0-9]{6}\.[0-9]{6}:INFO:naivefox\] "
URGENT_MARKER = re.compile(
    CANONICAL_LOG_PREFIX + r"Connection (?P<connection>[0-9]+) "
    r"diagnostic-first-socks-tunnel-urgent-start applied=1 "
    r"incremental=(?P<incremental>[01]) protocol=(?P<protocol>h2|h3)"
)
ESTABLISHED_MARKER = re.compile(r"Outer protocol: (?P<protocol>h2|h3)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def first_connect(events, cohort):
    connects = [
        event
        for event in events
        if event["direction"] == "client" and event["method"] == "CONNECT"
    ]
    require(connects, f"{cohort} has no outer CONNECT")
    require(
        len({event["stream"] for event in connects}) == len(connects),
        f"{cohort} CONNECT stream identities are ambiguous",
    )
    return min(connects, key=lambda event: (event["frame"], int(event["stream"])))


def read_priority_signature(root, cohort, proxy_port, connect):
    rows = parity.read_rows(root, cohort, "connect-priority")
    matches = []
    for row in rows:
        require(
            parity.direction(row, proxy_port) == "client",
            f"{cohort} CONNECT priority extract contains a server row",
        )
        if int(row["frame.number"]) != connect["frame"]:
            continue
        types = parity.values(row["http2.type"])
        streams = parity.values(row["http2.streamid"])
        methods = parity.values(row["http2.headers.method"])
        require(
            types and len(types) == len(streams),
            f"{cohort} CONNECT priority frame mapping is ambiguous",
        )
        header_streams = [
            stream for frame_type, stream in zip(types, streams) if frame_type == "1"
        ]
        require(
            len(header_streams) == 1 and len(methods) == 1,
            f"{cohort} CONNECT priority method mapping is ambiguous",
        )
        require(
            "2" not in types,
            f"{cohort} CONNECT priority frame has coalesced PRIORITY evidence",
        )
        if methods[0] != "CONNECT" or header_streams[0] != connect["stream"]:
            continue
        signature = (
            tuple(parity.values(row["http2.flags.priority"])),
            tuple(parity.values(row["http2.stream_dependency"])),
            tuple(parity.values(row["http2.headers.weight_real"])),
        )
        require(
            all(len(values) == 1 for values in signature),
            f"{cohort} CONNECT priority field sets are ambiguous",
        )
        matches.append(signature)
    require(
        len(matches) == 1,
        f"{cohort} first CONNECT priority evidence is ambiguous",
    )
    return matches[0]


def validate_product_markers(root):
    results = {}
    for cohort, expected in (
        ("naivefox-default", False),
        ("naivefox-urgent", True),
    ):
        path = root / f"{cohort}-naivefox.log"
        require(path.is_file(), f"{cohort} product log is missing")
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        prefixed = [
            (index, line)
            for index, line in enumerate(lines)
            if URGENT_MARKER_PREFIX in line
        ]
        established = [
            (index, match)
            for index, line in enumerate(lines)
            if (match := ESTABLISHED_MARKER.fullmatch(line))
        ]
        require(established, f"{cohort} has no CONNECT-established evidence")
        require(
            established[0][1].group("protocol") == "h2",
            f"{cohort} first established outer protocol is not H2",
        )
        if not expected:
            require(
                not prefixed,
                "naivefox-default unexpectedly applied the urgent-start diagnostic",
            )
            results[cohort] = True
            continue
        require(
            len(prefixed) == 1,
            "naivefox-urgent must have exactly one urgent-start applied marker",
        )
        marker_index, marker_line = prefixed[0]
        marker = URGENT_MARKER.fullmatch(marker_line)
        require(marker is not None, "naivefox-urgent applied marker is malformed")
        require(
            marker.group("connection") == "1",
            "naivefox-urgent marker is not bound to fresh-process Connection 1",
        )
        require(
            marker.group("protocol") == "h2",
            "naivefox-urgent marker is not bound to H2",
        )
        require(
            marker_index < established[0][0],
            "naivefox-urgent marker follows CONNECT-established evidence",
        )
        results[cohort] = True
    return results


def classify_mechanism(records):
    reference = records["firefox-proxied"]
    default = records["naivefox-default"]
    urgent = records["naivefox-urgent"]
    for cohort, record in records.items():
        require(
            any(record["signature"]),
            f"{cohort} has no CONNECT scheduling evidence",
        )
    urgent_matches = urgent["signature"] == reference["signature"]
    default_matches = default["signature"] == reference["signature"]
    urgent_header_compatible = urgent["priority_header"] == reference["priority_header"]
    all_headers_equal = (
        len({record["priority_header"] for record in records.values()}) == 1
    )
    if urgent_matches and not default_matches and urgent_header_compatible:
        return "native-match"
    if urgent_matches and default_matches and all_headers_equal:
        return "wire-null"
    return "native-mismatch"


def write_outputs(root, events_path, summary_path, proxy_port):
    marker_validation = validate_product_markers(root)
    records = {}
    settings = {}
    client_tls = {}
    server_tls = {}
    for cohort in COHORTS:
        cohort_events, cohort_settings, cohort_client_tls, cohort_server_tls = (
            parity.summarize_cohort(root, cohort, proxy_port)
        )
        require(
            not any(event["status"] == "407" for event in cohort_events),
            f"{cohort} encountered a proxy authentication challenge",
        )
        require(
            not any(
                event["direction"] == "client" and event["method"] == "GET"
                for event in cohort_events
            ),
            f"{cohort} emitted an outer GET instead of a tunneled navigation",
        )
        connect = first_connect(cohort_events, cohort)
        responses = [
            event
            for event in cohort_events
            if event["direction"] == "server"
            and event["stream"] == connect["stream"]
            and event["status"] == "200"
        ]
        require(responses, f"{cohort} first CONNECT did not succeed")
        has_padding = "padding" in connect["header_set"]
        if cohort == "firefox-proxied":
            require(
                not has_padding,
                "ordinary Firefox CONNECT unexpectedly has Naive padding",
            )
        else:
            require(has_padding, f"{cohort} first CONNECT lacks Naive padding")
            require(
                any("padding" in response["header_set"] for response in responses),
                f"{cohort} first CONNECT lacks response padding",
            )
        records[cohort] = {
            "events": cohort_events,
            "connect": connect,
            "priority_header": "priority" in connect["header_set"],
            "signature": read_priority_signature(root, cohort, proxy_port, connect),
        }
        settings[cohort] = cohort_settings
        client_tls[cohort] = cohort_client_tls
        server_tls[cohort] = cohort_server_tls

    reference = "firefox-proxied"
    for cohort in COHORTS[1:]:
        require(settings[cohort] == settings[reference], "same-base H2 SETTINGS differ")
        require(
            client_tls[cohort] == client_tls[reference],
            "same-base semantic ClientHello differs",
        )
        require(
            server_tls[cohort] == server_tls[reference],
            "same-base server TLS negotiation differs",
        )
    mechanism_verdict = classify_mechanism(records)

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
        for cohort in COHORTS:
            events = records[cohort]["events"]
            origin = min(event["time"] for event in events)
            stream_indices = {
                stream: index
                for index, stream in enumerate(
                    sorted({event["stream"] for event in events}, key=int), 1
                )
            }
            for ordinal, event in enumerate(
                sorted(events, key=lambda item: (item["frame"], int(item["stream"]))),
                1,
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
        output.write("capture_scope=same_base_h2_connect_priority_diagnostic\n")
        output.write("inner_transport=https\n")
        output.write("cohorts=firefox-proxied,naivefox-default,naivefox-urgent\n")
        output.write("physical_tcp_connections_each=1\n")
        output.write("outer_clienthello_connections_each=1\n")
        output.write("selected_alpn=h2\n")
        output.write("semantic_clienthello_equal=yes\n")
        output.write("server_negotiation_equal=yes\n")
        output.write("client_settings_equal=yes\n")
        output.write("proxy_authentication_challenge_absent=yes\n")
        for cohort in COHORTS:
            present = "yes" if records[cohort]["priority_header"] else "no"
            output.write(f"{cohort}_first_connect_priority_header={present}\n")
        default_matches = (
            records["naivefox-default"]["signature"]
            == records["firefox-proxied"]["signature"]
        )
        urgent_matches = (
            records["naivefox-urgent"]["signature"]
            == records["firefox-proxied"]["signature"]
        )
        urgent_header_compatible = (
            records["naivefox-urgent"]["priority_header"]
            == records["firefox-proxied"]["priority_header"]
        )
        all_headers_equal = (
            len({record["priority_header"] for record in records.values()}) == 1
        )
        output.write(
            "naivefox_default_scheduling_matches_firefox="
            f"{'yes' if default_matches else 'no'}\n"
        )
        output.write(
            "naivefox_urgent_scheduling_matches_firefox="
            f"{'yes' if urgent_matches else 'no'}\n"
        )
        output.write(
            "naivefox_urgent_priority_presence_compatible="
            f"{'yes' if urgent_header_compatible else 'no'}\n"
        )
        output.write(
            "all_cohort_priority_presence_equal="
            f"{'yes' if all_headers_equal else 'no'}\n"
        )
        output.write(f"mechanism_verdict={mechanism_verdict}\n")
        output.write("priority_presence_validation=passed\n")
        output.write("scheduling_evidence_validation=passed\n")
        output.write(
            "naivefox_default_urgent_marker_validation="
            f"{'passed' if marker_validation['naivefox-default'] else 'failed'}\n"
        )
        output.write(
            "naivefox_urgent_first_tunnel_marker_validation="
            f"{'passed' if marker_validation['naivefox-urgent'] else 'failed'}\n"
        )
        output.write("header_values_retained=no\n")
        output.write("credential_header_names_redacted=yes\n")
        output.write("passive_classifier_used=no\n")
        output.write("raw_capture_material=deleted_after_success\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--proxy-port", required=True)
    args = parser.parse_args()
    write_outputs(args.input_dir, args.events, args.summary, args.proxy_port)


if __name__ == "__main__":
    main()
