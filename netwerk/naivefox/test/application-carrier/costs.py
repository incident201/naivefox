#!/usr/bin/env python3
import json
import statistics
import sys
from pathlib import Path


def summarize(root):
    groups = {}
    for path in sorted((root / "features").glob("*.json")):
        feature = json.loads(path.read_text())
        arm = feature["naivefox_arm"]
        result = json.loads((root / path.stem / "result.json").read_text())
        groups.setdefault(arm, []).append((feature["features"], result))
    output = {"campaign": root.name, "arms": {}}
    for arm, rows in groups.items():
        output["arms"][arm] = {
            "n": len(rows),
            "wire_bytes": statistics.mean(f["whole_client_wire_bytes"] + f["whole_server_wire_bytes"] for f, _ in rows),
            "server_wire_bytes": statistics.mean(f["whole_server_wire_bytes"] for f, _ in rows),
            "client_wire_bytes": statistics.mean(f["whole_client_wire_bytes"] for f, _ in rows),
        }
        if "target_done_ms" in rows[0][1]:
            output["arms"][arm]["completion_ms"] = statistics.mean(r["target_done_ms"] for _, r in rows)
    comparisons = {}
    for kind in ("socks", "http"):
        old = output["arms"].get("application-default-" + kind)
        new = output["arms"].get("application-replace-" + kind)
        if old and new:
            comparisons[kind] = {
                "wire_increase_percent": 100 * (new["wire_bytes"] / old["wire_bytes"] - 1),
                "completion_increase_percent": 100 * (new["completion_ms"] / old["completion_ms"] - 1),
                "effective_page_rate_decrease_percent": 100 * (1 - old["completion_ms"] / new["completion_ms"]),
            }
    output["comparisons"] = comparisons
    return output


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(summarize(Path(arg)), indent=2))
