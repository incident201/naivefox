#!/usr/bin/env python3

import argparse
import json
import re

PREAMBLE_RESULT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble result=(?P<result>\S+) "
    r"status=0x[0-9a-fA-F]+ "
    r"http=(?P<http>\d+) bytes=(?P<bytes>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
ROOT_OVERLAP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble root-overlap admission=(?P<admission>\S+) "
    r"root_done=(?P<root_done>[01]) "
    r"started_resources=(?P<started_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
ROOT_OVERLAP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble root-overlap drain=complete "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_OVERLAP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-overlap admission=(?P<admission>\S+) "
    r"response_accepted=(?P<response_accepted>[01]) "
    r"root_done=(?P<root_done>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_OVERLAP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-overlap drain=complete root_done=1 "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_START_OVERLAP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-start-overlap admission=(?P<admission>\S+) "
    r"request_committed=(?P<request_committed>[01]) "
    r"root_done=(?P<root_done>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_START_OVERLAP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-start-overlap drain=complete root_done=1 "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
ESTABLISHED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"established target=\S+ outer=(?P<protocol>h2|h3) padding=yes$"
)


def validate_sample(arm, protocol, log_text, feature_document):
    log_lines = log_text.splitlines()
    supported_arms = (
        "off",
        "gate",
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-handshake-confirmed",
        "document-overlap",
        "document-start-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    if arm not in supported_arms:
        raise ValueError("unsupported NaiveFox arm")
    if protocol not in ("h2", "h3"):
        raise ValueError("unsupported outer protocol")
    if arm == "root-pmtud-control" and protocol != "h3":
        raise ValueError("root-pmtud-control requires h3")
    if arm == "document-handshake-confirmed" and protocol != "h3":
        raise ValueError("document-handshake-confirmed requires h3")

    result_lines = [line for line in log_lines if " preamble result=" in line]
    parsed_results = [PREAMBLE_RESULT.fullmatch(line) for line in result_lines]
    if any(result is None for result in parsed_results):
        raise ValueError("malformed preamble result evidence")
    preamble_arms = (
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-handshake-confirmed",
        "document-overlap",
        "document-start-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    overlapping_arms = (
        "document-overlap",
        "document-start-overlap",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    if arm in preamble_arms:
        if len(parsed_results) != 1:
            raise ValueError(f"{arm} arm requires exactly one preamble result")
        result = parsed_results[0]
        if result["result"] != "success" or result["protocol"] != protocol:
            raise ValueError(f"{arm} arm preamble did not succeed on selected protocol")
        if not 200 <= int(result["http"]) < 300:
            raise ValueError(f"{arm} arm preamble success has invalid HTTP status")
    elif parsed_results:
        raise ValueError(f"{arm} arm unexpectedly ran a preamble")

    if arm in overlapping_arms and any(
        " preamble background drain timed out" in line for line in log_lines
    ):
        raise ValueError(f"{arm} arm preamble background drain timed out")

    document_admission_lines = [
        line for line in log_lines if " preamble document-overlap admission=" in line
    ]
    parsed_document_admissions = [
        DOCUMENT_OVERLAP_ADMISSION.fullmatch(line) for line in document_admission_lines
    ]
    if any(admission is None for admission in parsed_document_admissions):
        raise ValueError("malformed document-overlap admission evidence")
    document_drain_lines = [
        line for line in log_lines if " preamble document-overlap drain=" in line
    ]
    parsed_document_drains = [
        DOCUMENT_OVERLAP_DRAIN.fullmatch(line) for line in document_drain_lines
    ]
    if any(drain is None for drain in parsed_document_drains):
        raise ValueError("malformed document-overlap drain evidence")

    document_start_admission_lines = [
        line
        for line in log_lines
        if " preamble document-start-overlap admission=" in line
    ]
    parsed_document_start_admissions = [
        DOCUMENT_START_OVERLAP_ADMISSION.fullmatch(line)
        for line in document_start_admission_lines
    ]
    if any(admission is None for admission in parsed_document_start_admissions):
        raise ValueError("malformed document-start-overlap admission evidence")
    document_start_drain_lines = [
        line for line in log_lines if " preamble document-start-overlap drain=" in line
    ]
    parsed_document_start_drains = [
        DOCUMENT_START_OVERLAP_DRAIN.fullmatch(line)
        for line in document_start_drain_lines
    ]
    if any(drain is None for drain in parsed_document_start_drains):
        raise ValueError("malformed document-start-overlap drain evidence")

    admission_lines = [
        line for line in log_lines if " preamble root-overlap admission=" in line
    ]
    parsed_admissions = [
        ROOT_OVERLAP_ADMISSION.fullmatch(line) for line in admission_lines
    ]
    if any(admission is None for admission in parsed_admissions):
        raise ValueError("malformed tree-root-overlap admission evidence")
    drain_lines = [
        line for line in log_lines if " preamble root-overlap drain=" in line
    ]
    parsed_drains = [ROOT_OVERLAP_DRAIN.fullmatch(line) for line in drain_lines]
    if any(drain is None for drain in parsed_drains):
        raise ValueError("malformed tree-root-overlap drain evidence")
    established_lines = [line for line in log_lines if " established target=" in line]
    parsed_established = [ESTABLISHED.fullmatch(line) for line in established_lines]
    if any(established is None for established in parsed_established):
        raise ValueError("malformed CONNECT-established evidence")
    if arm == "document-overlap":
        if len(parsed_document_admissions) != 1:
            raise ValueError(
                "document-overlap requires exactly one causal admission marker"
            )
        admission = parsed_document_admissions[0]
        if (
            admission["admission"] != "response-headers"
            or admission["response_accepted"] != "1"
            or admission["root_done"] != "0"
            or admission["protocol"] != protocol
        ):
            raise ValueError("document-overlap causal admission state is invalid")
        if len(parsed_document_drains) != 1:
            raise ValueError(
                "document-overlap requires exactly one completed drain marker"
            )
        drain = parsed_document_drains[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "document-overlap requires exactly one matching "
                "CONNECT-established marker"
            )
        established_line, _ = matching_established[0]
        if (
            result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
            or drain["protocol"] != protocol
            or int(drain["completed_resources"]) != 0
        ):
            raise ValueError("document-overlap lifecycle marker identity differs")
        admission_index = log_lines.index(document_admission_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(document_drain_lines[0])
        established_index = log_lines.index(established_line)
        if not (
            admission_index < result_index < drain_index
            and result_index < established_index
        ):
            raise ValueError("document-overlap lifecycle markers have invalid ordering")
    elif parsed_document_admissions or parsed_document_drains:
        raise ValueError(f"{arm} arm unexpectedly logged document-overlap lifecycle")
    if arm == "document-start-overlap":
        if len(parsed_document_start_admissions) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one causal admission marker"
            )
        admission = parsed_document_start_admissions[0]
        if (
            admission["admission"] != "request-committed"
            or admission["request_committed"] != "1"
            or admission["root_done"] != "0"
            or admission["protocol"] != protocol
        ):
            raise ValueError("document-start-overlap causal admission state is invalid")
        if len(parsed_document_start_drains) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one completed drain marker"
            )
        drain = parsed_document_start_drains[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one matching "
                "CONNECT-established marker"
            )
        established_line, _ = matching_established[0]
        if (
            result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
            or drain["protocol"] != protocol
            or int(drain["completed_resources"]) != 0
        ):
            raise ValueError("document-start-overlap lifecycle marker identity differs")
        admission_index = log_lines.index(document_start_admission_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(document_start_drain_lines[0])
        established_index = log_lines.index(established_line)
        if not (
            admission_index < result_index < drain_index
            and admission_index < established_index
        ):
            raise ValueError(
                "document-start-overlap lifecycle markers have invalid ordering"
            )
    elif parsed_document_start_admissions or parsed_document_start_drains:
        raise ValueError(
            f"{arm} arm unexpectedly logged document-start-overlap lifecycle"
        )
    if arm in (
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-warm-css-304",
    ):
        expected_resources = (
            1 if arm in ("tree-root-overlap-css", "tree-warm-css-304") else 2
        )
        if len(parsed_admissions) != 1:
            raise ValueError(
                "tree-root-overlap requires exactly one causal admission marker"
            )
        admission = parsed_admissions[0]
        if (
            admission["admission"] != "started-resources"
            or admission["root_done"] != "1"
            or int(admission["started_resources"]) != expected_resources
            or admission["protocol"] != protocol
        ):
            raise ValueError("tree-root-overlap causal admission state is invalid")
        if len(parsed_drains) != 1:
            raise ValueError(
                "tree-root-overlap requires exactly one completed drain marker"
            )
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "tree-root-overlap requires exactly one matching "
                "CONNECT-established marker"
            )
        drain = parsed_drains[0]
        established_line, established = matching_established[0]
        if (
            result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
            or drain["protocol"] != protocol
        ):
            raise ValueError("tree-root-overlap lifecycle marker identity differs")
        if int(drain["completed_resources"]) != expected_resources:
            raise ValueError(
                "tree-root-overlap fixture resource completion count is invalid"
            )
        admission_index = log_lines.index(admission_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(drain_lines[0])
        established_index = log_lines.index(established_line)
        if not (
            admission_index < result_index < drain_index
            and result_index < established_index
        ):
            raise ValueError(
                "tree-root-overlap lifecycle markers have invalid ordering"
            )
    elif parsed_admissions or parsed_drains:
        raise ValueError(f"{arm} arm unexpectedly logged root-overlap lifecycle")

    if arm != "off":
        if feature_document.get("protocol") != protocol:
            raise ValueError("feature document protocol does not match sample")
        connections = feature_document.get("features", {}).get(
            "lifecycle_connection_count"
        )
        if connections != 1.0:
            raise ValueError(
                f"{arm} arm requires one physical outer connection, got {connections}"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm",
        choices=(
            "off",
            "gate",
            "root",
            "root-pmtud-control",
            "document-complete",
            "document-handshake-confirmed",
            "document-overlap",
            "document-start-overlap",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-warm-css-304",
            "tree-overlap",
        ),
        required=True,
    )
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--features", required=True)
    args = parser.parse_args()
    with open(args.log, encoding="utf-8", errors="replace") as stream:
        log_text = stream.read()
    with open(args.features, encoding="utf-8") as stream:
        feature_document = json.load(stream)
    try:
        validate_sample(args.arm, args.protocol, log_text, feature_document)
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
