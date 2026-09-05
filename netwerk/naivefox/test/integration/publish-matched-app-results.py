#!/usr/bin/env python3
"""Publish only a complete, independently audited primary matched-app campaign."""

import argparse
import configparser
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile


SCHEMA = "matched-active-application-v1-complete-session"
MANIFEST_SHA = "3caeae3d8a8509d1453bcebda06150a63fe39b72c255f8a14ffd838abb1ce525"
PROTOCOLS = ("h2", "h3")
LISTENERS = ("socks", "http")
MODES = ("classic", "no-connect")
ARMS = tuple(f"native-{mode}-{listener}" for mode in MODES for listener in LISTENERS)
GROUPS = (*ARMS, "firefox_a", "firefox_b")
PRIMARY_BLOCKS = 10
SAMPLES_PER_PROTOCOL = PRIMARY_BLOCKS * len(GROUPS)
TOTAL_SAMPLES = SAMPLES_PER_PROTOCOL * len(PROTOCOLS)
VIEWS = ("initial_packets_16", "packets_17_32", "initial_packets_32", "initial_time_250ms", "whole")
STAGES = ("download", "upload", "parallel", "small", "wake")
MESSAGE_COUNTS = {"client_binary": 21, "server_binary": 165, "client_json": 190, "server_json": 43}


class Rejected(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise Rejected(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def hash_text(value, length=64):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value), "invalid provenance digest")
    return value


def number(value, label, minimum=None):
    require(type(value) in (int, float) and math.isfinite(value), "non-finite numeric field: " + label)
    require(minimum is None or value >= minimum, "numeric bound failed: " + label)
    return value


def count(value, label, expected=None):
    require(type(value) is int and value >= 0, "invalid count: " + label)
    require(expected is None or value == expected, "unexpected count: " + label)
    return value


def equal_number(actual, expected, label):
    if expected is None:
        require(actual is None, "unexpected resolved ratio: " + label)
    else:
        number(actual, label)
        require(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8), "recomputed value differs: " + label)


class Inputs:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.digests = {}
        self.private_cold_reads = 0

    def read(self, relative, attest=True):
        path = (self.root / relative).resolve(strict=True)
        require(path.is_relative_to(self.root) and path.is_file(), "input escaped the completed campaign")
        body = path.read_bytes()
        require(len(body) <= 20 * 1024 * 1024, "JSON input exceeds the report bound")
        if attest:
            self.digests[relative] = sha(body)
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise Rejected("invalid campaign JSON") from error

    def cold(self, sample):
        app = sample["application"]
        if "startup_to_app_ws_ms" in app and "complete_app_ms" in app:
            start, complete = app["startup_to_app_ws_ms"], app["complete_app_ms"]
        else:
            # This fallback consumes only the two authorized relative numeric markers.
            private = self.read(f"{sample['protocol']}/private/{sample['sample']}/browser-result.json", attest=False)
            start, complete = private["websocket"]["open_ms"], private["websocket"]["close_ms"]
            del private
            self.private_cold_reads += 1
        number(start, "application WS-open marker", 0)
        number(complete, "application close marker", 0)
        require(complete + 2 >= start + 4000, "application lifecycle is missing the declared idle periods")
        return {"startup_to_app_ws_ms": start, "complete_app_ms": complete}


def manifest_contract(provenance):
    manifest = provenance.get("manifest")
    require(isinstance(manifest, dict) and sha(canonical(manifest)) == MANIFEST_SHA,
            "the predeclared canonical application manifest changed")
    require(provenance.get("manifest_sha256") == MANIFEST_SHA, "manifest identity mismatch")
    require(manifest.get("protocol") == "nfbench.app.v1" and manifest.get("chunk_bytes") == 65536
            and manifest.get("receive_window") == 524288 and manifest.get("max_jobs") == 4
            and manifest.get("bootstrap_rounds") == 20 and manifest.get("catalog_records_per_round") == 64
            and manifest.get("idle_before_ms") == manifest.get("idle_wake_ms") == 2000,
            "application limits differ from preregistration")
    require([stage["name"] for stage in manifest["stages"]] == list(STAGES), "stage graph differs")
    require([job["id"] for job in manifest["jobs"]] == list(range(1, 12)), "job graph differs")
    uploaded = sum(job["bytes"] for job in manifest["jobs"] if job["kind"] in ("upload", "echo"))
    downloaded = sum(job["bytes"] for job in manifest["jobs"] if job["kind"] in ("download", "echo"))
    require((uploaded, downloaded) == (1069056, 10506240), "useful application totals changed")
    return manifest, uploaded, downloaded


def validate_sample(sample, protocol, index, schedule_row, manifest, uploaded, downloaded, app_sha):
    require(isinstance(sample, dict) and sample.get("admitted") is True and not sample.get("failure"), "participant was not admitted")
    require(sample.get("sample") == f"sample-{index:03d}" and sample.get("protocol") == protocol, "sample identity mismatch")
    require(all(sample.get(key) == value for key, value in schedule_row.items()), "sample differs from its frozen schedule")
    require(sample.get("scenario") == "matched_application", "wrong workload scenario")
    arm = sample.get("naivefox_arm")
    reference = arm == "reference"
    require((reference and sample.get("label") in ("firefox_a", "firefox_b"))
            or (arm in ARMS and sample.get("label") == "naivefox"), "unknown participant role")
    if reference:
        require(sample.get("selected_listener") is None, "reference used a local proxy listener")
    else:
        require(sample.get("selected_listener") == arm.rsplit("-", 1)[1], "native listener differs from its arm")
    count(sample.get("carrier_websockets"), "carrier WebSockets", int("no-connect" in arm))
    for key in ("capture_drops", "network_mutations", "pre_navigation_origin_packets", "pre_navigation_origin_requests"):
        count(sample.get(key), key, 0)
    route = sample.get("routing", {})
    require(route.get("verified") is True and route.get("verified_before_active_work") is True, "application routing was not independently proved")
    require(route.get("target_owned_by") == ("Firefox" if reference else "Caddy proxy"), "application bypassed its declared route")
    require(count(route.get("target_connections"), "target socket ownership") > 0, "missing target socket ownership")
    proxy_connections = count(route.get("selected_listener_connections"), "local listener ownership")
    require(proxy_connections == 0 if reference else proxy_connections > 0, "listener route was not proved")
    number(route.get("observation_relative_to_browser_open_ms"), "routing observation")
    app = sample.get("application", {})
    require(app.get("manifest_sha256") == MANIFEST_SHA and app.get("app_sha256") == app_sha, "participant application source changed")
    for key, expected in (("jobs_verified", 11), ("asset_responses_verified", 6), ("api_responses_verified", 40),
                          ("parallel_jobs", 4), ("application_websockets", 1),
                          ("uploaded_bytes", uploaded), ("downloaded_bytes", downloaded)):
        count(app.get(key), key, expected)
    require(app.get("normal_close") is True, "application did not close normally")
    consumer = app.get("consumer", {})
    for key, expected in (("asset_count", 6), ("images_decoded", 4), ("root_bytes", 4096)):
        count(consumer.get(key), key, expected)
    require(consumer.get("stylesheet_loaded") is True, "stylesheet was not consumed")
    require(consumer.get("http_protocol") == (protocol if reference else "h2"), "application HTTP protocol differs")
    stages = app.get("stages", [])
    require(len(stages) == 5 and [stage.get("stage") for stage in stages] == list(STAGES), "active application stages are incomplete")
    jobs = {job["id"]: job for job in manifest["jobs"]}
    for stage, declaration in zip(stages, manifest["stages"]):
        number(stage.get("io_ms"), "stage I/O duration", 0)
        per_job = stage.get("job_io_ms", [])
        require(len(per_job) == len(declaration["job_ids"]), "per-job I/O markers are incomplete")
        for value in per_job:
            number(value, "job I/O duration", 0)
        members = [jobs[identity] for identity in declaration["job_ids"]]
        count(stage.get("useful_bytes"), "stage useful bytes", sum(job["bytes"] for job in members))
        count(stage.get("sent_bytes"), "stage upload bytes", sum(job["bytes"] for job in members if job["kind"] in ("upload", "echo")))
        count(stage.get("received_bytes"), "stage download bytes", sum(job["bytes"] for job in members if job["kind"] in ("download", "echo")))
    wire = sample.get("whole", {})
    require(wire.get("capture_copy") == "receive_after_qdisc" and wire.get("outer_ipv4_only") is True, "wrong packet observer")
    total = count(wire.get("wire_bytes"), "complete-session IP bytes")
    require(total > 0 and count(wire.get("packets"), "observer packets") > 0, "empty observer")
    require(count(wire.get("client_wire_bytes"), "client IP bytes") + count(wire.get("server_wire_bytes"), "server IP bytes") == total,
            "wire directions do not sum to total bytes")
    flows = count(wire.get("outer_flows"), "outer flows")
    tcp = count(wire.get("tcp_flows"), "TCP flows")
    require(flows > 0 and count(wire.get("tcp_fin_completed"), "TCP FIN completion") + count(wire.get("tcp_reset_completed"), "TCP RST completion") == tcp,
            "TCP lifecycle is incomplete")
    if protocol == "h2":
        require(tcp > 0 and flows == tcp, "H2 observer contains an unexpected transport")
    elif not reference and "no-connect" not in arm:
        require(tcp == 0, "strict classic H3 emitted outer TCP")
    else:
        require(tcp > 0 and flows > tcp, "H3 startup plus application/carrier WSS is missing")
    drain = sample.get("drain", {})
    require(drain.get("both_direction_queues_empty") is True and drain.get("nonce_after_final_wire_observation") is True,
            "final receive-side drain was not proved")
    count(drain.get("live_producers"), "live drain producers", 0)
    quiet = number(drain.get("required_quiet_ms"), "declared drain quiet period", 120)
    require(number(drain.get("observed_quiet_ms"), "observed drain quiet period", 0) + 1e-6 >= quiet, "capture stopped before its drain")
    number(drain.get("drain_ms"), "observed drain duration", 0)
    teardown = sample.get("process_teardown", {})
    count(teardown.get("harness_forced_kills"), "harness forced kills", 0)
    count(teardown.get("live_owned_processes"), "surviving owned processes", 0)
    require(teardown.get("webdriver_quit_completed") is True and teardown.get("webdriver_service_returncode") == 0,
            "WebDriver shutdown did not complete normally")
    hash_text(sample.get("root_sha256"))
    return sample["label"] if reference else arm


def residual_entry(value, n):
    require(isinstance(value, dict), "missing arm residual")
    mean = number(value.get("mean_distance"), "residual mean", 0)
    ci = value.get("bootstrap_ci95")
    require(isinstance(ci, list) and len(ci) == 2, "missing residual interval")
    low, high = (number(x, "residual interval", 0) for x in ci)
    require(low <= high <= 1 and mean <= 1, "residual is outside the declared bounded distance")
    return {"mean_distance": mean, "bootstrap_ci95": [low, high], "samples": n}


def verify_audit(audit, inputs, samples, analyses):
    require(audit.get("schema") == "independent-matched-app-audit-v1", "unknown independent audit schema")
    require(audit.get("status") == "PASS", "independent audit did not pass")
    require(audit.get("source_matrix_sha256") == inputs.digests["matrix.json"], "independent audit covers a different matrix")
    require(set(audit.get("protocols", {})) == set(PROTOCOLS), "independent audit is incomplete")
    verified = {}
    for protocol in PROTOCOLS:
        part = audit["protocols"][protocol]
        count(part.get("participants"), "independently audited participants", SAMPLES_PER_PROTOCOL)
        for key in ("all_active_jobs_and_routes", "all_backend_useful_bytes_and_message_graphs_verified", "all_performance_and_traffic_ratios_recomputed", "all_sample_wire_totals_recomputed_from_raw_capture"):
            require(part.get(key) is True, "independent audit did not verify " + key)
        cohort = [sample for sample in samples if sample["protocol"] == protocol]
        require(part.get("raw_receive_ip_bytes") == sum(sample["whole"]["wire_bytes"] for sample in cohort)
                and part.get("raw_receive_packets") == sum(sample["whole"]["packets"] for sample in cohort),
                "independently counted raw receive totals differ from public samples")
        views = part.get("independently_recomputed_residuals", {})
        require(set(views) == set(VIEWS), "independent residual audit is incomplete")
        for view in VIEWS:
            analysis_features = analyses[protocol]["protocols"][protocol]["views"][view].get("features")
            expected_features = len(analysis_features) if isinstance(analysis_features, list) else count(analysis_features, "analysis view feature count")
            require(expected_features > 0 and views[view].get("all_six_arms_recomputed") is True
                    and count(views[view].get("features"), "independent feature count") == expected_features,
                    "not all six residual arms were independently recomputed")
        verified[protocol] = {"participants": SAMPLES_PER_PROTOCOL, "raw_ip_bytes": part["raw_receive_ip_bytes"],
                              "raw_packets": part["raw_receive_packets"], "all_six_arms_all_five_views_recomputed": True,
                              "audited_input_bundle_sha256": hash_text(part.get("audited_input_bundle_sha256")),
                              "audited_input_files": count(part.get("audited_input_files"), "audited raw/sidecar inputs", 403)}
    return {"status": "PASS", "schema": audit["schema"], "matrix_sha256": audit["source_matrix_sha256"],
            "audit_source_sha256": hash_text(audit.get("audit_source_sha256")), "protocols": verified}


def provenance_summary(provenance):
    frozen = provenance.get("frozen_inputs_sha256", {})
    require(isinstance(frozen, dict) and frozen, "missing frozen input hashes")
    for label, value in frozen.items():
        require(isinstance(label, str) and not label.startswith(("/", "\\")) and ":" not in label
                and ".." not in label.split("/") and label.split("/", 1)[0] in ("native_runtime", "reference_runtime", "source", "tools", "reference"),
                "unsafe frozen input label")
        hash_text(value)
    def required(label):
        require(label in frozen, "required frozen input is missing: " + label)
        return frozen[label]
    reference = provenance.get("verified_reference", {})
    require(reference.get("schema_version") == 1, "unknown official reference proof schema")
    source = hash_text(provenance.get("source_revision"), 40)
    base = hash_text(reference.get("git_base"), 40)
    hg = hash_text(reference.get("hg_revision"), 40)
    task = reference.get("task_id")
    require(isinstance(task, str) and re.fullmatch(r"[A-Za-z0-9_-]{22}", task), "invalid official Firefox task identity")
    task_url = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/" + task
    artifact_url = f"https://firefoxci.taskcluster-artifacts.net/{task}/0/public/build/target.tar.xz"
    require(reference.get("task_url") == task_url and reference.get("artifact_url") == artifact_url,
            "reference proof is not the declared official artifact")
    for revision in (base, hg):
        require(f"index.gecko.v2.mozilla-central.revision.{revision}.firefox.linux64-opt" in reference.get("revision_routes", []),
                "official reference revision route is missing")
    runtime_files = reference.get("runtime_files_sha256", {})
    require({"firefox", "firefox-bin", "libxul.so", "libnss3.so", "libssl3.so", "application.ini"}.issubset(runtime_files), "reference proof omits essential runtime members")
    for name, value in runtime_files.items():
        require(required("reference_runtime/" + name) == hash_text(value), "reference runtime member differs from the verified artifact")
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(provenance.get("native_artifact", {}).get("application_ini", ""))
    package = provenance.get("native_artifact", {}).get("manifest", {})
    require(package.get("format_version") == 1 and isinstance(package.get("files"), list) and package["files"], "native package manifest is missing")
    members = set()
    package_bytes = 0
    for member in package["files"]:
        name = member.get("path")
        require(isinstance(name, str) and name not in members and not name.startswith(("/", "\\")) and ":" not in name and ".." not in name.split("/"), "unsafe native package member")
        members.add(name)
        require(required("native_runtime/" + name) == hash_text(member.get("sha256")), "native package member differs from frozen runtime")
        package_bytes += count(member.get("size"), "native package member bytes")
    require(package.get("total_bytes") == package_bytes, "native package byte accounting differs")
    require({key.removeprefix("native_runtime/") for key in frozen if key.startswith("native_runtime/")} == members | {"manifest.json"}, "native frozen package inventory is incomplete")
    native_build = config.get("App", "BuildID", fallback="")
    reference_build = reference.get("build_id", "")
    require(re.fullmatch(r"[0-9]{14}", native_build) and re.fullmatch(r"[0-9]{14}", reference_build), "missing artifact build identity")
    native_inventory = {key: value for key, value in frozen.items() if key.startswith("native_runtime/")}
    reference_inventory = {key: value for key, value in frozen.items() if key.startswith("reference_runtime/")}
    return {
        "capture_source_revision": source, "firefox_git_base": base, "firefox_hg_revision": hg,
        "source_revision_scope": "Capture checkout identity; executable identities are separately attested by hashes and build IDs.",
        "native": {"build_id": native_build, "launcher_sha256": required("native_runtime/naivefox"),
                   "executable_sha256": required("native_runtime/runtime/naivefox"), "libxul_sha256": required("native_runtime/runtime/libxul.so"),
                   "package_manifest_sha256": required("native_runtime/manifest.json"), "inventory_sha256": sha(canonical(native_inventory)), "inventory_members": len(native_inventory)},
        "firefox": {"build_id": reference_build, "executable_sha256": required("reference_runtime/firefox"),
                    "firefox_bin_sha256": required("reference_runtime/firefox-bin"), "libxul_sha256": required("reference_runtime/libxul.so"),
                    "inventory_sha256": sha(canonical(reference_inventory)), "inventory_members": len(reference_inventory),
                    "proof_sha256": required("reference/proof.json"), "task_id": task, "task_url": task_url,
                    "artifact_url": artifact_url, "archive_sha512": hash_text(reference.get("archive_sha512"), 128),
                    "archive_bytes": count(reference.get("archive_bytes"), "reference archive bytes")},
        "server": {"caddy_sha256": required("tools/caddy"), "application_backend_sha256": required("tools/nfbench-app")},
        "application": {"manifest_sha256": required("source/hybrid_app/manifest.json"), "script_sha256": required("source/hybrid_app/app.js"),
                        "backend_source_sha256": required("source/hybrid_app/main.go"), "stylesheet_source_sha256": required("source/hybrid_app/site.css"),
                        "image_source_sha256": required("source/hybrid_app/image.svg")},
        "capture_harness_sha256": required("source/run-matched-app-matrix.py"),
        "frozen_input_inventory_sha256": sha(canonical(frozen)), "frozen_input_members": len(frozen),
    }


def ratios(samples, source_matrix, residuals, cold):
    rows = []
    actual_rows = {(row.get("startup_protocol"), row.get("listener")): row for row in source_matrix.get("rows", [])}
    require(len(source_matrix.get("rows", [])) == 4 and set(actual_rows) == {(p, l) for p in PROTOCOLS for l in LISTENERS}, "matrix does not contain exactly four no-connect rows")
    def cohort(protocol, arm):
        return [s for s in samples if s["protocol"] == protocol and s["naivefox_arm"] == arm]
    def wire(records):
        return statistics.fmean(record["whole"]["wire_bytes"] for record in records)
    def stage_time(records, index, latency):
        stages = [record["application"]["stages"][index] for record in records]
        return statistics.fmean(statistics.fmean(stage["job_io_ms"]) if latency else stage["io_ms"] for stage in stages)
    for protocol in PROTOCOLS:
        for listener in LISTENERS:
            original = actual_rows[(protocol, listener)]
            count(original.get("blocks"), "matrix paired blocks", 10)
            arm = f"native-no-connect-{listener}"
            candidate = cohort(protocol, arm)
            candidate_wire = wire(candidate)
            equal_number(original.get("whole_ip_bytes"), candidate_wire, "candidate complete-session bytes")
            row = {"startup_protocol": protocol, "listener": listener, "blocks": 10, "samples": 10,
                   "whole_ip_bytes_mean": candidate_wire,
                   "residual": {view: residuals[protocol][view][arm] for view in VIEWS}, "comparisons": {}}
            require(set(original.get("comparisons", {})) == {"firefox", "classic", "no-connect"}, "a comparison baseline is missing")
            for view in VIEWS:
                equal_number(original["residual"][view]["mean_distance"], row["residual"][view]["mean_distance"], "matrix no-connect residual")
                require(original["residual"][view]["bootstrap_ci95"] == row["residual"][view]["bootstrap_ci95"], "matrix no-connect interval differs")
            for baseline in ("firefox", "classic"):
                controls = cohort(protocol, "reference" if baseline == "firefox" else f"native-{baseline}-{listener}")
                old = original["comparisons"][baseline]
                baseline_wire = wire(controls)
                output = {"baseline_samples": len(controls), "baseline_whole_ip_bytes_mean": baseline_wire,
                          "extra_complete_session_traffic_percent": 100 * (candidate_wire / baseline_wire - 1), "stages": {}}
                equal_number(old.get("baseline_whole_ip_bytes"), baseline_wire, "baseline complete-session bytes")
                equal_number(old.get("extra_complete_session_traffic_percent"), output["extra_complete_session_traffic_percent"], "complete-session traffic ratio")
                for marker in ("startup_to_app_ws_ms", "complete_app_ms"):
                    new_time = statistics.fmean(cold[(s["protocol"], s["sample"])][marker] for s in candidate)
                    old_time = statistics.fmean(cold[(s["protocol"], s["sample"])][marker] for s in controls)
                    values = {"baseline_ms": old_time, "candidate_ms": new_time,
                              "time_increase_percent": 100 * (new_time / old_time - 1) if old_time > 0 else None}
                    if marker in old:
                        equal_number(old[marker].get("baseline"), old_time, "cold baseline marker")
                        equal_number(old[marker].get("candidate"), new_time, "cold candidate marker")
                        equal_number(old[marker].get("time_increase_percent"), values["time_increase_percent"], "cold marker ratio")
                    output[marker] = values
                require(set(old.get("stages", {})) == set(STAGES), "a stage comparison is missing")
                for index, stage in enumerate(STAGES):
                    latency = stage in ("small", "wake")
                    old_time, new_time = stage_time(controls, index, latency), stage_time(candidate, index, latency)
                    resolved = old_time > 0 and new_time > 0
                    values = {"baseline_io_ms": old_time, "candidate_io_ms": new_time,
                              "time_increase_percent": 100 * (new_time / old_time - 1) if resolved else None,
                              "timer_resolved_ratio": resolved,
                              "measurement": "mean single-echo I/O latency" if latency else "complete stage I/O duration"}
                    if not latency:
                        useful = candidate[0]["application"]["stages"][index]["useful_bytes"]
                        values.update(effective_rate_loss_percent=100 * (1 - old_time / new_time) if resolved else None,
                                      baseline_mbit_s=useful * 8 / old_time / 1000 if old_time > 0 else None,
                                      candidate_mbit_s=useful * 8 / new_time / 1000 if new_time > 0 else None)
                    original_stage = old["stages"][stage]
                    require(set(original_stage) == set(values), "stage metric schema changed")
                    for key, value in values.items():
                        if isinstance(value, (str, bool)):
                            require(original_stage.get(key) == value, "stage metric interpretation changed")
                        else:
                            equal_number(original_stage.get(key), value, "stage metric " + stage + "/" + key)
                    output["stages"][stage] = values
                if baseline != "firefox":
                    base_arm = f"native-{baseline}-{listener}"
                    for view in VIEWS:
                        equal_number(old["residual"][view]["mean_distance"], residuals[protocol][view][base_arm]["mean_distance"], "baseline residual copy")
                        require(old["residual"][view]["bootstrap_ci95"] == residuals[protocol][view][base_arm]["bootstrap_ci95"], "baseline residual interval copy")
                    output["residual"] = {view: residuals[protocol][view][base_arm] for view in VIEWS}
                row["comparisons"][baseline] = output
            rows.append(row)
    return rows


def make_report(inputs):
    provenance = inputs.read("provenance.json")
    require(provenance.get("purpose") == "primary", "pilot and diagnostic campaigns cannot produce this report")
    require(provenance.get("schema") == SCHEMA and provenance.get("screening_only") is True, "wrong primary campaign schema")
    count(provenance.get("blocks_per_protocol"), "predeclared primary paired blocks", 10)
    require(provenance.get("link") == "rtt40-20mbps", "primary link is not the predeclared 40-ms RTT / 20-Mbit/s-per-direction link")
    count(provenance.get("seed"), "frozen schedule seed")
    require(provenance.get("local_listener_topology") == "only the selected listener", "local listener topology changed")
    manifest, uploaded, downloaded = manifest_contract(provenance)
    safe_provenance = provenance_summary(provenance)
    app_sha = safe_provenance["application"]["script_sha256"]
    matrix = inputs.read("matrix.json")
    require(matrix.get("purpose") == "primary" and matrix.get("schema") == SCHEMA and matrix.get("screening_only") is True, "matrix is not the matching primary study")
    samples, analyses, cold = [], {}, {}
    group_counts, root_hashes = Counter(), set()
    residuals = {}
    for protocol in PROTOCOLS:
        directory = inputs.root / protocol
        expected_names = {f"sample-{index:03d}.json" for index in range(SAMPLES_PER_PROTOCOL)}
        require({path.name for path in directory.glob("sample-*.json")} == expected_names, "missing, extra or resampled public participant identities")
        schedule = inputs.read(protocol + "/schedule.json")
        require(isinstance(schedule, list) and len(schedule) == SAMPLES_PER_PROTOCOL, "the complete primary schedule is required")
        blocks = defaultdict(list)
        for index, declaration in enumerate(schedule):
            sample = inputs.read(f"{protocol}/sample-{index:03d}.json")
            group = validate_sample(sample, protocol, index, declaration, manifest, uploaded, downloaded, app_sha)
            group_counts[(protocol, group)] += 1
            blocks[sample["experiment_block"]].append(group)
            samples.append(sample)
            root_hashes.add(sample["root_sha256"])
            cold[(protocol, sample["sample"])] = inputs.cold(sample)
        require(len(blocks) == 10 and all(Counter(groups) == Counter(GROUPS) for groups in blocks.values()), "a randomized paired block is missing or duplicated")
        require(all(group_counts[(protocol, group)] == 10 for group in GROUPS), "a participant cohort does not have exactly ten samples")
        analysis = inputs.read(protocol + "/analysis.json")
        analyses[protocol] = analysis
        require(analysis.get("screening_only") is True and set(analysis.get("views_selected", [])) == set(VIEWS)
                and set(analysis.get("protocols", {})) == {protocol}, "analysis view or protocol family changed")
        require(analysis.get("methodology", {}).get("observer_schema") == SCHEMA, "analysis does not use the complete matched observer")
        conclusion = analysis.get("conclusion", {})
        require(conclusion.get("supports_absolute_camouflage_verdict") is False and conclusion.get("supports_relative_arm_inference") is False,
                "ten-block analysis cannot claim inferential camouflage acceptance")
        part = analysis["protocols"][protocol]
        inference = part.get("inference", {})
        require(inference.get("blocks") == 10 and inference.get("minimum_blocks") == 30 and inference.get("supports_paired_inference") is False,
                "paired inference floor differs from preregistration")
        residuals[protocol] = {}
        for view in VIEWS:
            arms = part["views"][view].get("arms", {})
            require(set(arms) == set(ARMS), "residual report omits one of the four native arms")
            residuals[protocol][view] = {arm: residual_entry(arms[arm], 10) for arm in ARMS}
    require(len(samples) == TOTAL_SAMPLES and len(root_hashes) == 1, "complete cohort or common root identity failed")
    audit = verify_audit(inputs.read("independent-audit.json"), inputs, samples, analyses)
    rows = ratios(samples, matrix, residuals, cold)
    declared_cohorts = [{"protocol": p, "participant": group, "samples": group_counts[(p, group)]} for p in PROTOCOLS for group in GROUPS]
    flow_ranges = []
    for protocol in PROTOCOLS:
        for group in GROUPS:
            cohort = [s for s in samples if s["protocol"] == protocol and (s["label"] if s["naivefox_arm"] == "reference" else s["naivefox_arm"]) == group]
            flows = [s["whole"]["outer_flows"] for s in cohort]
            flow_ranges.append({"protocol": protocol, "participant": group, "minimum": min(flows), "maximum": max(flows)})
    safe_provenance["application"]["root_sha256"] = next(iter(root_hashes))
    safe_provenance["input_provenance_sha256"] = inputs.digests["provenance.json"]
    safe_provenance["input_matrix_sha256"] = inputs.digests["matrix.json"]
    safe_provenance["independent_audit_sha256"] = inputs.digests["independent-audit.json"]
    safe_provenance["report_generator_sha256"] = sha(Path(__file__).read_bytes())
    safe_provenance["public_input_bundle_sha256"] = sha(canonical(inputs.digests))
    return {
        "schema": "naivefox-no-connect-matched-primary-public-v1", "status": "COMPLETE_AUDITED_SCREENING", "purpose": "primary",
        "screening_only": True, "provenance": safe_provenance,
        "design": {"participants": TOTAL_SAMPLES, "participants_per_protocol": SAMPLES_PER_PROTOCOL, "blocks_per_protocol": PRIMARY_BLOCKS,
                   "participants_per_block": len(GROUPS), "seed": provenance["seed"], "cohorts": declared_cohorts,
                   "same_firefox_application_for_every_participant": True, "manifest": manifest,
                   "application_useful_bytes": {"client_to_server": uploaded, "server_to_client": downloaded},
                   "declared_application_ws_message_counts": MESSAGE_COUNTS,
                   "message_count_proof": "Frozen collector admission plus independent browser/backend job, byte, direction, message-count and concurrency audit; raw backend records are not copied into this report.",
                   "link": {"rtt_ms": 40, "one_way_delay_ms": 20, "client_to_origin_mbit_s": 20, "origin_to_client_mbit_s": 20,
                            "address_family": "IPv4", "mtu_bytes": 1500, "offloads_disabled": True},
                   "observer": "All receive-side outer-origin TCP/QUIC and attributable ICMP through application close, producer shutdown, empty shaping queues and observed drain.",
                   "whole_is_complete_session": True, "per_stage_wire_attribution": False,
                   "failed_participant_resampling_within_campaign": False,
                   "resampling_evidence": "Exactly the 120 scheduled identities, one admitted sidecar each, a matching fail-fast source-frozen collector and independent audit; no pilot or prior failed campaign samples are included."},
        "admission": {"admitted_participants": len(samples), "verified_application_jobs": len(samples) * len(manifest["jobs"]),
                      "verified_application_asset_responses": len(samples) * 6, "verified_semantic_api_responses": len(samples) * 40,
                      "normal_application_websockets": len(samples), "no_connect_carrier_websockets": sum(item["carrier_websockets"] for item in samples),
                      "route_proofs": len(samples), "cold_origin_empty_before_navigation": len(samples), "capture_drops": 0, "network_mutations": 0,
                      "harness_forced_kills": 0, "surviving_owned_producers": 0,
                      "tcp_flows": sum(sample["whole"]["tcp_flows"] for sample in samples),
                      "tcp_fin_completed": sum(sample["whole"]["tcp_fin_completed"] for sample in samples),
                      "tcp_reset_completed": sum(sample["whole"]["tcp_reset_completed"] for sample in samples),
                      "browser_shutdown_evidence": "Normal application close, successful WebDriver quit/service exit 0, and no surviving tracked PIDs; no stronger claim about undocumented WebDriver internals.",
                      "private_numeric_cold_marker_fallbacks": inputs.private_cold_reads, "outer_flow_ranges": flow_ranges},
        "independent_audit": audit, "no_connect_rows": rows, "all_native_arm_residuals": residuals,
        "interpretation": {"distance": "Bounded passive-feature excess outside matched Firefox A/B control envelope; lower is closer under this declared metric.",
                           "confidence_intervals": "Descriptive conditional bootstrap intervals, not confirmatory acceptance intervals.",
                           "effective_rate_loss_percent": "100 * (1 - baseline_mean_io_ms / candidate_mean_io_ms); negative means higher effective rate.",
                           "time_increase_percent": "100 * (candidate_mean_io_ms / baseline_mean_io_ms - 1); small/wake use mean single-echo I/O latency.",
                           "extra_complete_session_traffic_percent": "100 * (candidate_mean_complete_IP_bytes / baseline_mean_complete_IP_bytes - 1).",
                           "cold_markers": "Browser performance-clock navigation-to-application-WS-open and navigation-to-clean-app-close; WebDriver polling wall time is not substituted."},
        "limitations": ["Ten paired blocks per protocol are below the preregistered thirty-block inference floor; no absolute camouflage or inferential relative-arm verdict is supported.",
                        "Results apply to one fixed active application and the declared symmetric 40-ms RTT / 20-Mbit/s-per-direction link, not arbitrary sites or Internet paths.",
                        "Whole covers startup, all active jobs, the two declared idle periods and teardown; it is not an arbitrary long-lived WS lifetime or a two-second cold crop.",
                        "Timing and traffic are reported separately. All carrier filler, connection establishment and attributable teardown traffic remain in complete-session IP totals.",
                        "One application or carrier WebSocket does not imply one physical connection. Every observed outer flow is included.",
                        "Runtime binary/package hashes and build identities are distinct from the capture checkout revision; no absent native source-to-binary attestation is invented.",
                        "The Caddy executable is pinned by its measured binary hash; no immutable module source-to-binary attestation is included.",
                        "This measurement provides no Windows live-runtime or Android device result."],
    }


def section(report):
    lines = ["### Matched active application: audited classic/no-connect matrix", "",
             "A fresh primary run admitted all 120 participants in ten randomized paired blocks per protocol. "
             "Firefox A/B and every native arm executed the same application, all eleven verified jobs and one normal application WebSocket close. "
             "The outer link used 40 ms RTT and 20 Mbit/s independently in each direction; Whole includes complete startup, activity, idle and teardown.", "",
             "| Startup / listener | p1–16 distance | p17–32 distance | Whole distance |",
             "| --- | ---: | ---: | ---: |"]
    for row in report["no_connect_rows"]:
        values = row["residual"]
        lines.append(f"| {row['startup_protocol'].upper()} / {row['listener']} | {values['initial_packets_16']['mean_distance']:.5f} | {values['packets_17_32']['mean_distance']:.5f} | {values['whole']['mean_distance']:.5f} |")
    def percent(value):
        return "unresolved" if value is None else f"{value:+.2f}%"
    lines.extend(["", "The distance is a bounded feature diagnostic, not a detection probability. "
                  "The following costs use direct Firefox running the same active application as the baseline. "
                  "Download and upload are the 8-MiB and 1-MiB stages; traffic covers the entire completed session.", "",
                  "| Startup / listener | Download rate loss vs Firefox | Upload rate loss vs Firefox | Extra session IP traffic vs Firefox |",
                  "| --- | ---: | ---: | ---: |"])
    for row in report["no_connect_rows"]:
        base = row["comparisons"]["firefox"]
        lines.append(f"| {row['startup_protocol'].upper()} / {row['listener']} | {percent(base['stages']['download']['effective_rate_loss_percent'])} | {percent(base['stages']['upload']['effective_rate_loss_percent'])} | {percent(base['extra_complete_session_traffic_percent'])} |")
    lines.extend(["", "The next costs compare no-connect with classic. "
                  "Positive rate loss or latency increase is worse; negative values are improvements. "
                  "Traffic is the complete-session outer IP total, with no arbitrary per-stage tail allocation.", "",
                  "| Startup / listener | Download rate loss | Upload rate loss | Small echo latency increase | Extra session traffic |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for row in report["no_connect_rows"]:
        base = row["comparisons"]["classic"]
        lines.append(f"| {row['startup_protocol'].upper()} / {row['listener']} | {percent(base['stages']['download']['effective_rate_loss_percent'])} | {percent(base['stages']['upload']['effective_rate_loss_percent'])} | {percent(base['stages']['small']['time_increase_percent'])} | {percent(base['extra_complete_session_traffic_percent'])} |")
    lines.extend(["", "The [machine-readable matrix](test/integration/evidence/no-connect-matrix.json) includes all four native-arm residual means/intervals/counts in all five views, "
                  "all four no-connect rows, Firefox/classic baseline speed and complete-session traffic comparisons, and browser-clock startup/application-completion costs. "
                  "It also records immutable artifact identities and the independent raw-capture, workload, routing and numerical audit.", "",
                  "Ten blocks remain descriptive screening below the thirty-block inference floor. "
                  "These results neither establish absolute indistinguishability nor promote a default transport; prior idle-reference and failed-pilot datasets are not included.", ""])
    lines.extend(["The measured Caddy is identified by its exact binary hash; this report does not assert an immutable module source-to-binary attestation. "
                  "It contains no Windows live-runtime or Android device measurements.", ""])
    return "\n".join(lines)


def atomic_output(path, body, replace, output_root):
    path = path.resolve()
    require(path.is_relative_to(output_root), "draft outputs must remain under the completed campaign's parent directory")
    if path.exists():
        require(path.is_file() and (replace or path.read_bytes() == body), "choose a fresh draft output or explicitly use --replace")
        if path.read_bytes() == body:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".matched-public-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="completed primary matched-app campaign")
    parser.add_argument("--output", type=Path, help="sanitized JSON draft below the input campaign's parent directory")
    parser.add_argument("--section", type=Path, help="proposed CAPTURE Markdown section below the input campaign's parent directory")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        require(args.check_only or (args.output is not None and args.section is not None), "both draft output paths are required")
        inputs = Inputs(args.input)
        report = make_report(inputs)
        data = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()
        markdown = section(report).encode()
        if not args.check_only:
            require(not args.output.resolve().is_relative_to(inputs.root) and not args.section.resolve().is_relative_to(inputs.root), "do not modify frozen campaign inputs")
            atomic_output(args.output, data, args.replace, inputs.root.parent)
            atomic_output(args.section, markdown, args.replace, inputs.root.parent)
        print("PASS: complete primary 120-participant audit and sanitized report validation")
        return 0
    except (Rejected, OSError, ValueError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, Rejected) else type(error).__name__
        print("REFUSED: " + message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
