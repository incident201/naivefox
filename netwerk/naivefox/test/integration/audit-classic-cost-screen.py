#!/usr/bin/env python3
"""Recount classic-baseline application costs from raw captures and endpoint records."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("classic_cost_audit", HERE / "audit-matched-app-results.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    root = args.input.resolve(strict=True)
    matrix = audit.read(root / "matrix.json")
    proof = audit.read(root / "provenance.json")
    audit.check(matrix.get("comparison_baseline") == proof.get("comparison_baseline") == "classic"
                and matrix["purpose"] == "pilot" and matrix["screening_only"], "wrong comparison contract")
    audit.check({row["startup_protocol"] for row in matrix["rows"]} == {"h2", "h3"}
                and len(matrix["rows"]) == 12, "incomplete H2/H3 comparison")
    output = root / "cost-audit.json"
    output.write_text(json.dumps({"status": "IN_PROGRESS"}) + chr(10))
    result = {}
    mean = statistics.fmean
    for protocol in ("h2", "h3"):
        result[protocol] = audit.audit_protocol(root, protocol, [], proof["manifest"])
        samples = [audit.read(path) for path in sorted((root/protocol).glob("sample-*.json"))]
        blocks = {}
        for sample in samples:
            name = sample["label"] if sample["naivefox_arm"] == "reference" else sample["naivefox_arm"]
            block = blocks.setdefault(sample["experiment_block"], {})
            audit.check(name not in block, "duplicate participant")
            block[name] = sample
        expected = {"firefox_a", "firefox_b", *(f"native-{transport}-{listener}"
                    for transport in ("classic", "no-connect", "no-connect-hybrid-asymmetric")
                    for listener in ("socks", "http"))}
        audit.check(1 <= len(blocks) <= 2 and all(set(block) == expected for block in blocks.values()),
                    "incomplete classic-baseline paired block")
        rows = [row for row in matrix["rows"] if row["startup_protocol"] == protocol]
        for row in rows:
            audit.check(row["baseline"] == "classic" and row["blocks"] == len(blocks), "baseline/count differs")
            old = [item for item in samples if item["naivefox_arm"] == "native-classic-"+row["listener"]]
            new = [item for item in samples if item["naivefox_arm"] == "native-"+row["transport"]+"-"+row["listener"]]
            before, after = [mean(item["whole"]["wire_bytes"] for item in group) for group in (old, new)]
            audit.close(row["baseline_whole_ip_bytes"], before, "classic IP bytes")
            audit.close(row["whole_ip_bytes"], after, "candidate IP bytes")
            audit.close(row["extra_ip_bytes"], after-before, "extra IP bytes")
            audit.close(row["extra_complete_session_traffic_percent"], 100*(after/before-1), "IP overhead")
            for metric in ("startup_to_app_ws_ms", "complete_app_ms"):
                a, b = [mean(item["application"][metric] for item in group) for group in (old,new)]
                audit.close(row[metric]["baseline"], a, "classic application timer")
                audit.close(row[metric]["candidate"], b, "candidate application timer")
                audit.close(row[metric]["time_increase_percent"], 100*(b/a-1), "application timer ratio")
            for index, stage in enumerate(proof["manifest"]["stages"]):
                a,b = [mean(mean(item["application"]["stages"][index]["job_io_ms"]) if index >= 3
                            else item["application"]["stages"][index]["io_ms"] for item in group)
                       for group in (old,new)]
                actual = row["stages"][stage["name"]]
                audit.close(actual["baseline_io_ms"], a, "classic stage")
                audit.close(actual["candidate_io_ms"], b, "candidate stage")
                audit.close(actual["time_increase_percent"], 100*(b/a-1), "stage ratio")
                if index < 3:
                    useful = sum(job["bytes"] for job in proof["manifest"]["jobs"] if job["id"] in stage["job_ids"])
                    audit.close(actual["baseline_mbit_s"], useful*8/a/1000, "classic goodput")
                    audit.close(actual["candidate_mbit_s"], useful*8/b/1000, "candidate goodput")
                    audit.close(actual["goodput_change_percent"], 100*(a/b-1), "goodput ratio")
        result[protocol]["capture_windows"] = audit.audit_distances(root/protocol, protocol)
        result[protocol]["residual_audited"] = True
    output.write_text(json.dumps({"status":"PASS", "scope":"classic-baseline cost, workload, route, raw IP and capture-distance audit",
        "matrix_sha256":audit.file_hash(root/"matrix.json"),
        "audit_sha256":audit.file_hash(Path(__file__)), "protocols":result},indent=2)+chr(10))
    print(json.dumps({"status":"PASS","audit":str(output)}))


if __name__ == "__main__":
    main()
