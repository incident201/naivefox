#!/usr/bin/env python3
"""Independently recount the cost results of a bounded asymmetric application screen."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("screen_audit", HERE / "audit-matched-app-results.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    root = args.input.resolve(strict=True)
    matrix = audit.read(root / "matrix.json")
    proof = audit.read(root / "provenance.json")
    audit.check(matrix["purpose"] == "pilot" and matrix["screening_only"], "not a short screen")
    output = root / "cost-audit.json"
    output.write_text(json.dumps({"status": "IN_PROGRESS"}) + "\n")
    summary = {}
    for protocol in sorted({row["startup_protocol"] for row in matrix["rows"]}):
        rows = [row for row in matrix["rows"] if row["startup_protocol"] == protocol]
        result = audit.audit_protocol(root, protocol, [], proof["manifest"])
        samples = [audit.read(path) for path in sorted((root / protocol).glob("sample-*.json"))]
        blocks = {}
        for sample in samples:
            name = sample["label"] if sample["naivefox_arm"] == "reference" else sample["naivefox_arm"]
            block = blocks.setdefault(sample["experiment_block"], {})
            audit.check(name not in block, "duplicate participant")
            block[name] = sample
        expected = {"firefox_a", "firefox_b", *(
            f"native-{transport}-{listener}"
            for transport in ("no-connect-hybrid", "no-connect-hybrid-asymmetric")
            for listener in ("socks", "http"))}
        audit.check(1 <= len(blocks) <= 2 and all(set(block) == expected for block in blocks.values()),
                    "incomplete asymmetric paired block")
        mean = statistics.fmean
        for row in rows:
            controls = [item for item in samples if item["naivefox_arm"] ==
                        "native-no-connect-hybrid-" + row["listener"]]
            candidates = [item for item in samples if item["naivefox_arm"] ==
                          "native-no-connect-hybrid-asymmetric-" + row["listener"]]
            old_wire, new_wire = [mean(item["whole"]["wire_bytes"] for item in group)
                                  for group in (controls, candidates)]
            audit.close(row["generic_complete_ip_bytes"], old_wire, "generic IP total")
            audit.close(row["asymmetric_complete_ip_bytes"], new_wire, "asymmetric IP total")
            audit.close(row["firefox_complete_ip_bytes"],
                        mean(item["whole"]["wire_bytes"] for item in samples if item["naivefox_arm"] == "reference"),
                        "Firefox IP total")
            audit.close(row["complete_ip_reduction_percent"], 100 * (1-new_wire/old_wire), "IP ratio")
            old_fill, new_fill = [mean(item["carrier_shape"]["ws_upload_filler"] +
                                     item["carrier_shape"]["ws_download_filler"] for item in group)
                                  for group in (controls, candidates)]
            audit.close(row["generic_transport_filler_bytes"], old_fill, "generic filler total")
            audit.close(row["asymmetric_transport_filler_bytes"], new_fill, "asymmetric filler total")
            audit.close(row["transport_filler_reduction_percent"], 100 * (1-new_fill/old_fill), "filler ratio")
            for index, stage in enumerate(proof["manifest"]["stages"]):
                old, new = [mean(mean(item["application"]["stages"][index]["job_io_ms"]) if index >= 3
                                 else item["application"]["stages"][index]["io_ms"] for item in group)
                            for group in (controls, candidates)]
                actual = row["stages"][stage["name"]]
                audit.close(actual["generic_io_ms"], old, "control I/O time")
                audit.close(actual["asymmetric_io_ms"], new, "candidate I/O time")
                audit.close(actual["time_increase_percent"], 100 * (new/old-1), "I/O ratio")
                limit = 15 if index >= 3 else 10
                audit.check(actual["gate_limit_percent"] == limit and
                            actual["gate_pass"] == (100 * (new/old-1) <= limit),
                            "stage admission differs")
            gate = row["potential_gate"]
            eligible = proof["link"] == "rtt40-20mbps"
            traffic_ok = 100 * (1-new_wire/old_wire) >= 15
            filler_ok = 100 * (1-new_fill/old_fill) >= 30
            stages_ok = all(value["gate_pass"] for value in row["stages"].values())
            audit.check(gate["eligible_controlled_link"] == eligible and
                        gate["complete_ip_reduction_at_least_15_percent"] == traffic_ok and
                        gate["filler_reduction_at_least_30_percent"] == filler_ok and
                        gate["stage_regressions_within_limits"] == stages_ok and
                        gate["pass"] == (eligible and traffic_ok and filler_ok and stages_ok),
                        "screen admission differs")
        result["residual_audited"] = False
        summary[protocol] = result
    output.write_text(json.dumps({"status": "PASS", "scope": "short-screen cost, workload, route and raw IP audit",
        "matrix_sha256": audit.file_hash(root / "matrix.json"), "audit_sha256": audit.file_hash(Path(__file__)),
        "protocols": summary}, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "audit": str(output)}))


if __name__ == "__main__":
    main()
