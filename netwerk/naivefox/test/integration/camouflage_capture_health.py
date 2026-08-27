#!/usr/bin/env python3

import argparse
import re

SUMMARY = re.compile(r"Packets received/dropped on interface .*?:\s*(\d+)\s*/\s*(\d+)")
DETAIL = re.compile(r"\b(pcap|dumpcap|flushed|ps_ifdrop):(\d+)\b")


def validate_dumpcap_log(text):
    summaries = SUMMARY.findall(text)
    if not summaries:
        raise ValueError("dumpcap did not report final interface statistics")
    dropped = sum(int(value) for _received, value in summaries)
    details = [(name, int(value)) for name, value in DETAIL.findall(text)]
    if dropped or any(value for _name, value in details):
        detail = ", ".join(f"{name}={value}" for name, value in details)
        raise ValueError(f"dumpcap reported dropped packets ({detail})")
    if "Packets captured:" not in text:
        raise ValueError("dumpcap did not report a completed capture")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    args = parser.parse_args()
    with open(args.log, encoding="utf-8", errors="replace") as stream:
        log = stream.read()
    try:
        validate_dumpcap_log(log)
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
