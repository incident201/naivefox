#!/usr/bin/env python3
"""Compare complete fixed-work sessions; these are not residual distances."""

import json
from pathlib import Path
import statistics
import sys


def summarize(root):
    rows = [json.loads(path.read_text()) for path in sorted(root.glob("session-*/result.json"))]
    variants = rows and all(result["mode"] == "replace" for result in rows)
    control, candidate = "default", "replace"
    if variants:
        candidates = {result["app_profile"] for result in rows} - {"continuous-v1"}
        if len(candidates) != 1:
            raise ValueError("one candidate profile required")
        control, candidate = "continuous-v1", candidates.pop()
    grouped = {control: [], candidate: []}
    for result in rows:
        if not result.get("admitted"):
            raise ValueError("incomplete fixed-work session campaign")
        grouped[result["app_profile"] if variants else result["mode"]].append(result)
    if not all(grouped.values()) or len(grouped[control]) != len(grouped[candidate]):
        raise ValueError("complete paired comparison required")
    summary = {"campaign": root.name, "pairs": len(grouped[control]), "control": control, "candidate": candidate, "wire": {}, "stages": {}, "curl_stages": {}}
    for mode, results in grouped.items():
        summary["wire"][mode] = statistics.mean(result["session_wire"]["bytes"] for result in results)
        for result in results:
            for check in result["session_exercise"]["checks"]:
                stage = summary["stages"].setdefault(check["stage"], {control: [], candidate: [], "useful_bytes": check["useful_bytes"]})
                if stage["useful_bytes"] != check["useful_bytes"]:
                    raise ValueError("unequal useful workload")
                stage[mode].append(check["completion_ms"])
                if "curl_completion_ms" in check:
                    exact = summary["curl_stages"].setdefault(check["stage"], {control: [], candidate: []})
                    exact[mode].append(check["curl_completion_ms"])
    summary["wire"]["growth_percent"] = 100 * (summary["wire"][candidate] / summary["wire"][control] - 1)
    for stage in summary["stages"].values():
        if any(len(stage[mode]) != len(grouped[mode]) for mode in grouped):
            raise ValueError("incomplete stage comparison")
        for mode in grouped:
            stage[mode + "_samples_ms"] = stage[mode]
            stage[mode] = statistics.mean(stage[mode])
        stage["effective_rate_drop_percent"] = 100 * (1 - stage[control] / stage[candidate])
    for stage in summary["curl_stages"].values():
        if any(len(stage[mode]) != len(grouped[mode]) for mode in grouped):
            raise ValueError("incomplete precise timing comparison")
        for mode in grouped:
            stage[mode + "_samples_ms"] = stage[mode]
            stage[mode] = statistics.mean(stage[mode])
        stage["completion_reduction_percent"] = 100 * (1 - stage[candidate] / stage[control])
    return summary


if __name__ == "__main__":
    for value in sys.argv[1:]:
        print(json.dumps(summarize(Path(value)), indent=2))
