#!/usr/bin/env python3
"""Compare complete fixed-work sessions; these are not residual distances."""

import json
from pathlib import Path
import statistics
import sys


def summarize(root):
    grouped = {"default": [], "replace": []}
    for path in sorted(root.glob("session-*/result.json")):
        result = json.loads(path.read_text())
        if not result.get("admitted"):
            raise ValueError("incomplete fixed-work session campaign")
        grouped[result["mode"]].append(result)
    if not all(grouped.values()) or len(grouped["default"]) != len(grouped["replace"]):
        raise ValueError("complete native/replacement pairs required")
    summary = {"campaign": root.name, "pairs": len(grouped["default"]), "wire": {}, "stages": {}}
    for mode, results in grouped.items():
        summary["wire"][mode] = statistics.mean(result["session_wire"]["bytes"] for result in results)
        for result in results:
            for check in result["session_exercise"]["checks"]:
                stage = summary["stages"].setdefault(check["stage"], {"default": [], "replace": [], "useful_bytes": check["useful_bytes"]})
                if stage["useful_bytes"] != check["useful_bytes"]:
                    raise ValueError("unequal useful workload")
                stage[mode].append(check["completion_ms"])
    summary["wire"]["growth_percent"] = 100 * (summary["wire"]["replace"] / summary["wire"]["default"] - 1)
    for stage in summary["stages"].values():
        for mode in grouped:
            stage[mode + "_samples_ms"] = stage[mode]
            stage[mode] = statistics.mean(stage[mode])
        stage["effective_rate_drop_percent"] = 100 * (1 - stage["default"] / stage["replace"])
    return summary


if __name__ == "__main__":
    for value in sys.argv[1:]:
        print(json.dumps(summarize(Path(value)), indent=2))
