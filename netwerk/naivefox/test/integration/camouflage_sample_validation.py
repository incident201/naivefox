#!/usr/bin/env python3

import argparse
import json
import re

PREAMBLE_RESULT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection \d+ preamble result=(\S+) "
    r"status=0x[0-9a-fA-F]+ "
    r"http=(\d+) bytes=(\d+) protocol=(h2|h3)$"
)


def validate_sample(arm, protocol, log_text, feature_document):
    supported_arms = (
        "off",
        "gate",
        "root",
        "document-complete",
        "tree-complete",
        "tree-early-overlap",
        "tree-overlap",
    )
    if arm not in supported_arms:
        raise ValueError("unsupported NaiveFox arm")
    if protocol not in ("h2", "h3"):
        raise ValueError("unsupported outer protocol")

    result_lines = [
        line for line in log_text.splitlines() if " preamble result=" in line
    ]
    parsed_results = [PREAMBLE_RESULT.fullmatch(line) for line in result_lines]
    if any(result is None for result in parsed_results):
        raise ValueError("malformed preamble result evidence")
    preamble_arms = (
        "root",
        "document-complete",
        "tree-complete",
        "tree-early-overlap",
        "tree-overlap",
    )
    if arm in preamble_arms:
        if len(parsed_results) != 1:
            raise ValueError(f"{arm} arm requires exactly one preamble result")
        result = parsed_results[0]
        if result.group(1) != "success" or result.group(4) != protocol:
            raise ValueError(f"{arm} arm preamble did not succeed on selected protocol")
        if not 200 <= int(result.group(2)) < 300:
            raise ValueError(f"{arm} arm preamble success has invalid HTTP status")
    elif parsed_results:
        raise ValueError(f"{arm} arm unexpectedly ran a preamble")

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
            "document-complete",
            "tree-complete",
            "tree-early-overlap",
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
