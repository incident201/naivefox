#!/usr/bin/env python3
"""Independent arithmetic and raw receive-capture audit of a completed campaign."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess


def read(path):
    return json.loads(path.read_text())


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check(value, message):
    if not value:
        raise RuntimeError(message)


def close(left, right, message):
    check(math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-9), message)


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def selected(names, view):
    if view == "whole":
        return list(names)
    if view.startswith("initial_time_"):
        return [name for name in names if name.startswith("initial_" + view.removeprefix("initial_time_") + "_")]
    first, last = (17, 32) if view == "packets_17_32" else (1, int(view.removeprefix("initial_packets_")))
    return [name for name in names if (name.startswith("packet_") and name.split("_", 2)[1].isdecimal()
            and first <= int(name.split("_", 2)[1]) <= last)
            or (first == 1 and name.startswith(f"initial_{last}_"))]


def audit_distances(folder, protocol):
    metadata = {"schema_version", "protocol", "scenario", "label", "session_id", "experiment_block", "naivefox_arm"}
    with (folder / "features.csv").open() as stream:
        reader = csv.DictReader(stream)
        names = [name for name in reader.fieldnames if name not in metadata]
        data = list(reader)
    blocks = {}
    counts = [f"initial_{phase}_packet_count" for phase in ("16", "32", "64", "128", "50ms", "100ms", "250ms", "500ms", "1000ms", "2000ms")]
    counts += ["steady_after_32_packet_count", "steady_after_2000ms_packet_count", "lifecycle_tail_16_packet_count"]
    for row in data:
        values = {name: float(row[name] or 0) for name in names}
        for count in counts:
            if count in names:
                values[count.removesuffix("_packet_count") + "_present"] = float(values[count] > 0)
        block = blocks.setdefault(row["experiment_block"], {})
        key = row["label"] if row["naivefox_arm"] == "reference" else row["naivefox_arm"]
        check(key not in block, "duplicate arm in paired block")
        block[key] = values
    for count in counts:
        indicator = count.removesuffix("_packet_count") + "_present"
        if count in names and indicator not in names:
            names.append(indicator)
    report = read(folder / "analysis.json")["protocols"][protocol]
    check(all(len(block) == 6 and "firefox_a" in block and "firefox_b" in block for block in blocks.values()), "incomplete paired blocks")
    arms = sorted(set(next(iter(blocks.values()))) - {"firefox_a", "firefox_b"})
    result = {}
    for view, reported in report["views"].items():
        features = selected(names, view)
        check(len(features) == reported["features"], "feature window definition differs")
        scale = {}
        for name in features:
            controls = [(block["firefox_a"][name], block["firefox_b"][name]) for block in blocks.values()]
            tolerance = 1e-9 * (1 + max(abs(value) for pair in controls for value in pair))
            radius = percentile([abs(a - b) / 2 for a, b in controls], 0.75)
            scale[name] = (radius if radius > tolerance else 0, tolerance)
        arm_values = {arm: [] for arm in arms}
        for block in blocks.values():
            for arm in arms:
                scores = []
                for name in features:
                    a, b, candidate = block["firefox_a"][name], block["firefox_b"][name], block[arm][name]
                    excess = max(0, abs(candidate - (a + b) / 2) - abs(a - b) / 2)
                    radius, tolerance = scale[name]
                    score = 0 if excess <= tolerance else 1 if radius == 0 else min(excess / radius, 4) / 4
                    scores.append(score)
                arm_values[arm].append(statistics.fmean(scores))
        for arm in arms:
            close(statistics.fmean(arm_values[arm]), reported["arms"][arm]["mean_distance"], "residual distance mean")
            close(statistics.median(arm_values[arm]), reported["arms"][arm]["median_block_distance"], "residual distance median")
        result[view] = {"features": len(features), "all_four_arms_recomputed": True}
    return result


def audit_protocol(root, protocol, rows, manifest):
    folder = root / protocol
    samples = [read(path) for path in sorted(folder.glob("sample-*.json"))]
    check(len(samples) == len(read(folder / "schedule.json")), "sample inventory differs from schedule")
    specifications = {job["id"]: job for job in manifest["jobs"]}
    wire_totals = {"bytes": 0, "packets": 0}
    audited_inputs = {name: file_hash(folder / name) for name in ("schedule.json", "features.csv", "analysis.json")}
    for sample in samples:
        check(sample["admitted"], "failed participant in final campaign")
        private = folder / "private" / sample["sample"]
        audited_inputs[sample["sample"] + ".json"] = file_hash(folder / (sample["sample"] + ".json"))
        for name in ("browser-result.json", "backend-stats.json", "caddy.json", "session-raw.pcapng"):
            audited_inputs[sample["sample"] + "/" + name] = file_hash(private / name)
        browser, backend = read(private / "browser-result.json"), read(private / "backend-stats.json")
        jobs = [job for stage in browser["stages"] for job in stage["jobs"]]
        check(len(jobs) == 11 and {job["id"] for job in jobs} == set(specifications), "browser job inventory mismatch")
        for job in jobs:
            check(all(job[key] == specifications[job["id"]][key] for key in ("id", "kind", "bytes", "sha256")), "browser useful job differs")
        check(len(backend["connections"]) == 1 and backend["connections"][0]["normal_close"], "backend did not finish a single WS")
        connection = backend["connections"][0]
        expected_up = sum(job["bytes"] for job in specifications.values() if job["kind"] in ("upload", "echo"))
        expected_down = sum(job["bytes"] for job in specifications.values() if job["kind"] in ("download", "echo"))
        check((browser["uploaded_bytes"], browser["downloaded_bytes"], connection["data_bytes_in"], connection["data_bytes_out"])
              == (expected_up, expected_down, expected_up, expected_down), "independent useful byte/direction mismatch")
        backend_jobs = connection["jobs"]
        check(len(backend_jobs) == 11 and {job["id"] for job in backend_jobs} == set(specifications), "backend job inventory mismatch")
        for job in backend_jobs:
            spec = specifications[job["id"]]
            received = spec["bytes"] if spec["kind"] in ("upload", "echo") else 0
            sent = spec["bytes"] if spec["kind"] in ("download", "echo") else 0
            check(all(job[key] == spec[key] for key in ("id", "kind", "bytes", "sha256")) and job["verified"] is True
                  and (job["received"], job["sent"], job["validated"]) == (received, sent, received), "independent backend job verification failed")
        ws = browser["websocket"]
        check((ws["opened"], ws["closed"], ws["close_code"], ws["clean"], connection["close_code"])
              == (1, 1, 1000, True, 1000), "independent normal application close mismatch")
        check(tuple(ws[key] for key in ("binary_messages_sent", "binary_messages_received", "control_messages_sent", "control_messages_received"))
              == (21, 165, 190, 43), "browser message graph mismatch")
        check(tuple(connection[key] for key in ("data_messages_in", "data_messages_out", "control_messages_in", "control_messages_out"))
              == (21, 165, 190, 43), "backend message graph mismatch")
        check((connection["parallel_batches"], connection["parallel_job_count"], connection["peak_jobs"])
              == (1, 4, 4) and connection["open_order"] == list(range(1, 12)), "backend concurrency/order mismatch")
        check((backend["api_posts"], backend["api_gets"], backend["catalog_records"], len(backend["api"]))
              == (20, 20, 1280, 40), "semantic API inventory mismatch")
        check(sample["pre_navigation_origin_packets"] == sample["pre_navigation_origin_requests"] == 0, "prewarmed origin")
        check(sample["routing"]["verified_before_active_work"], "unverified route")
        check(sample["process_teardown"]["harness_forced_kills"] == sample["process_teardown"]["live_owned_processes"] == 0, "incomplete shutdown")
        servers = read(private / "caddy.json")["apps"]["http"]["servers"]
        outer = [server for name, server in servers.items() if name not in ("matched_inner", "matched_health")]
        check(len(outer) == 1, "ambiguous outer server")
        port = int(outer[0]["listen"][0].rsplit(":", 1)[1])
        fields = subprocess.run(["tshark", "-n", "-r", str(private / "session-raw.pcapng"), "-Y",
            f"sll.pkttype==0 && (tcp.port=={port} || udp.port=={port}) && !icmpv6", "-T", "fields",
            "-E", "aggregator=;", "-e", "ip.len", "-e", "icmp.type", "-e", "tcp.srcport", "-e", "tcp.dstport",
            "-e", "udp.srcport", "-e", "udp.dstport"], capture_output=True, text=True, check=True)
        total = up = down = packets = 0
        for line in fields.stdout.splitlines():
            values = line.split("\t")
            check(len(values) == 6, "invalid raw packet field row")
            length, icmp, ts, td, us, ud = values
            size = int(length.split(";")[0])
            check(0 < size <= 1500, "bad raw observer IP size")
            source, destination = ts or us, td or ud
            check((source == str(port)) != (destination == str(port)), "ambiguous raw packet direction")
            client = destination == str(port)
            if icmp:
                client = not client
            up += size if client else 0
            down += 0 if client else size
            total += size
            packets += 1
        expected = sample["whole"]
        check((total, packets, up, down) == (expected["wire_bytes"], expected["packets"], expected["client_wire_bytes"], expected["server_wire_bytes"]), "raw capture totals differ from published sample")
        wire_totals["bytes"] += total
        wire_totals["packets"] += packets
    mean = statistics.fmean
    def stage_time(items, index):
        return mean(mean(item["application"]["stages"][index]["job_io_ms"]) if index >= 3
                    else item["application"]["stages"][index]["io_ms"] for item in items)
    for row in rows:
        candidates = [item for item in samples if item["naivefox_arm"] == "native-no-connect-" + row["listener"]]
        close(row["whole_ip_bytes"], mean(item["whole"]["wire_bytes"] for item in candidates), "candidate mean bytes")
        for baseline, comparison in row["comparisons"].items():
            arm = "reference" if baseline == "firefox" else "native-" + baseline + "-" + row["listener"]
            controls = [item for item in samples if item["naivefox_arm"] == arm]
            old_wire, new_wire = (mean(item["whole"]["wire_bytes"] for item in cohort) for cohort in (controls, candidates))
            close(comparison["extra_complete_session_traffic_percent"], 100 * (new_wire / old_wire - 1), "extra traffic percent")
            for index, stage in enumerate(manifest["stages"]):
                old, new = stage_time(controls, index), stage_time(candidates, index)
                actual = comparison["stages"][stage["name"]]
                close(actual["baseline_io_ms"], old, "baseline I/O duration")
                close(actual["candidate_io_ms"], new, "candidate I/O duration")
                if old <= 0 or new <= 0:
                    check(actual["time_increase_percent"] is None and not actual["timer_resolved_ratio"], "unresolved timer reported as a ratio")
                    if index < 3:
                        check(actual["effective_rate_loss_percent"] is None, "unresolved rate ratio")
                    continue
                close(actual["time_increase_percent"], 100 * (new / old - 1), "time increase")
                if index < 3:
                    close(actual["effective_rate_loss_percent"], 100 * (1 - old / new), "rate loss")
    return {"participants": len(samples), "raw_receive_packets": wire_totals["packets"],
            "raw_receive_ip_bytes": wire_totals["bytes"], "all_active_jobs_and_routes": True,
            "all_backend_useful_bytes_and_message_graphs_verified": True,
            "audited_input_files": len(audited_inputs),
            "audited_input_bundle_sha256": hashlib.sha256(json.dumps(audited_inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "all_sample_wire_totals_recomputed_from_raw_capture": True, "all_performance_and_traffic_ratios_recomputed": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    output = args.root / "independent-audit.json"
    output.write_text(json.dumps({"schema": "independent-matched-app-audit-v1", "status": "IN_PROGRESS"}) + "\n")
    provenance = read(args.root / "provenance.json")
    matrix = read(args.root / "matrix.json")
    result = {"schema": "independent-matched-app-audit-v1", "source_matrix_sha256": hashlib.sha256((args.root / "matrix.json").read_bytes()).hexdigest(),
              "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "protocols": {}}
    for protocol in sorted({row["startup_protocol"] for row in matrix["rows"]}):
        result["protocols"][protocol] = audit_protocol(args.root, protocol,
            [row for row in matrix["rows"] if row["startup_protocol"] == protocol], provenance["manifest"])
        result["protocols"][protocol]["independently_recomputed_residuals"] = audit_distances(args.root / protocol, protocol)
        print(protocol + " independent raw capture and arithmetic audit PASS", flush=True)
    result["status"] = "PASS"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
