#!/usr/bin/env python3
"""Native HTTP-startup/WS screen; all encrypted origin flows are one observer unit."""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import camouflage_features as features
import camouflage_superblocks as superblocks
from camouflage_browser_controller import firefox_preferences
from camouflage_capture_health import validate_dumpcap_log

INTEGRATION = Path(__file__).resolve().parent
VIEWS = ("initial_packets_16", "packets_17_32", "initial_packets_32", "initial_time_250ms", "whole")
TRANSPORTS = ("classic", "no-connect")
ARMS = tuple(f"native-{transport}-{kind}" for transport in TRANSPORTS for kind in ("socks", "http"))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, INTEGRATION / filename)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


native = module("hybrid_native_fixture", "run-no-connect-tests.py")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def require(value, message):
    if not value:
        raise RuntimeError(message)


def wait_for(predicate, message, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise RuntimeError(message)


def merge_outer_events(tcp, quic):
    result = []
    for family, items in (("tcp", tcp), ("quic", quic)):
        for item in items:
            event = dict(item)
            event["flow"] = ";".join(family + ":" + value for value in dict.fromkeys(features.split_values(event["flow"])))
            result.append(event)
    return sorted(result, key=lambda event: (event["time"], event["frame"]))


def outer_events(pcap, port):
    tcp, records = features.packet_events_h2(str(pcap), port)
    quic, _ = features.packet_events_h3(str(pcap), port)
    return merge_outer_events(tcp, quic), tcp, quic, records


def wire_summary(events):
    return {
        "wire_bytes": sum(event["wire_size"] for event in events),
        "packets": len(events),
        "client_wire_bytes": sum(event["wire_size"] for event in events if event["direction"] > 0),
        "server_wire_bytes": sum(event["wire_size"] for event in events if event["direction"] < 0),
        "outer_flows": len({value for event in events for value in features.split_values(event["flow"])}),
    }


def passive_document(pcap, port, protocol, row, sample):
    events, tcp, quic, records = outer_events(pcap, port)
    require(events and all(event["wire_size"] <= 1500 for event in events), "missing packets or offload superframes")
    tcp_flows = {event["flow"] for event in tcp}
    tcp_origins = {event["flow"] for event in tcp if event["direction"] > 0 and event["syn"] and not event["ack"]}
    require(tcp_flows == tcp_origins, "TCP flow started before capture")
    if protocol == "h2":
        require(tcp and not quic, "H2 startup used an unexpected UDP route")
    else:
        require(quic and any(event["direction"] > 0 and "0" in event["packet_types"] for event in quic), "missing captured H3 client Initial")
    startup = tcp if protocol == "h2" else quic
    startup_ids = list(dict.fromkeys(value for event in startup for value in features.split_values(event["flow"])))
    require(startup_ids and all(value.isdecimal() for value in startup_ids), "invalid startup flow identifiers")
    require(protocol != "h3" or len(startup_ids) == 1, "more than one startup QUIC connection")
    startup_id = startup_ids[0]
    flow_filter = ("tcp.stream" if protocol == "h2" else "quic.connection.number") + "==" + startup_id
    values = {}
    features.extract_handshake(str(pcap), protocol, port, values, flow_filter=flow_filter)
    all_handshakes = {}
    features.extract_handshake(str(pcap), protocol, port, all_handshakes)
    values.update({"whole_" + name: value for name, value in all_handshakes.items()})
    for index, identity in enumerate(dict.fromkeys(event["flow"] for event in tcp), 1):
        require(identity.isdecimal(), "invalid TCP flow identifier")
        supplemental = {}
        features.extract_handshake(str(pcap), "h2", port, supplemental, flow_filter="tcp.stream==" + identity)
        values.update({f"whole_tcp_connection_{index:03d}_" + name: value for name, value in supplemental.items()})
    if protocol == "h3":
        features.extract_transport_parameters(str(pcap), port, values)
        features.add_h3_features(values, quic)
    tcp_values = {}
    features.add_h2_features(tcp_values, tcp)
    if protocol == "h3":
        values.update({("whole_" + name if name.startswith("tcp_syn_") else name): value for name, value in tcp_values.items()})
    else:
        values.update(tcp_values)
        values["whole_tcp_syn_count"] = values["tcp_syn_count"]
        values["tcp_syn_count"] = float(sum(event["direction"] > 0 and event["syn"] and not event["ack"]
                                                  for event in tcp if event["flow"] == startup_id))
    record_values = {}
    features.add_tls_record_features(record_values, records)
    values.update({"whole_" + name: value for name, value in record_values.items()})
    if protocol == "h2":
        features.add_tls_record_features(values, [record for record in records if record["flow"] == startup_id])
    features.add_aggregate(values, "whole", events)
    features.add_aggregate(values, "lifecycle_tail_16", events[-16:])
    features.add_sequence_features(values, events)
    values["lifecycle_connection_count"] = float(len({value for event in events for value in features.split_values(event["flow"])}))
    values["lifecycle_reconnect_count"] = float(max(0, values["lifecycle_connection_count"] - 1))
    features.validate_features(values)
    return {
        "schema_version": features.SCHEMA_VERSION, "protocol": protocol,
        "scenario": row["scenario"], "label": row["label"], "naivefox_arm": row["naivefox_arm"],
        "session_id": sample, "experiment_block": row["experiment_block"], "features": values,
    }, wire_summary(events)


def verify_reference(proof_path, firefox, expected_base):
    proof = json.loads(proof_path.read_text())
    require(proof.get("schema_version") == 1 and proof.get("git_base") == expected_base, "reference proof has another Git base")
    task = proof.get("task_id", "")
    require(proof.get("task_url") == f"https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{task}", "reference proof source is not the official task API")
    for revision in (expected_base, proof.get("hg_revision")):
        require(f"index.gecko.v2.mozilla-central.revision.{revision}.firefox.linux64-opt" in proof.get("revision_routes", []), "reference proof lacks a revision route")
    files = proof.get("runtime_files_sha256", {})
    require({"firefox", "firefox-bin", "libxul.so", "libnss3.so", "libssl3.so", "application.ini"}.issubset(files), "reference proof lacks essential runtime files")
    runtime = firefox.parent
    for name, expected in files.items():
        path = (runtime / name).resolve(strict=True)
        require(path.is_relative_to(runtime) and digest(path) == expected, "Firefox runtime differs from the verified official artifact")
    require(digest(firefox) == files["firefox"], "selected Firefox executable is not the verified runtime")
    return proof


def matrix_view_feature_names(names, view):
    if view == "whole":
        return list(names)
    if view.startswith("initial_time_"):
        return [name for name in names if name.startswith("initial_" + view.removeprefix("initial_time_") + "_")]
    first, last = (17, 32) if view == "packets_17_32" else (1, int(view.removeprefix("initial_packets_")))
    selected = []
    for name in names:
        if name.startswith("packet_"):
            ordinal = name.split("_", 2)[1]
            if ordinal.isdecimal() and first <= int(ordinal) <= last:
                selected.append(name)
        elif first == 1 and name.startswith(f"initial_{last}_"):
            selected.append(name)
    return selected


def penalties(baseline_time, candidate_time, baseline_wire, candidate_wire):
    require(baseline_time > 0 and candidate_time > 0 and baseline_wire > 0, "invalid comparison denominator")
    return {
        "effective_rate_loss_percent": 100 * (1 - baseline_time / candidate_time),
        "completion_time_increase_percent": 100 * (candidate_time / baseline_time - 1),
        "extra_outer_traffic_percent": 100 * (candidate_wire / baseline_wire - 1),
    }


class Capture:
    def __init__(self, directory, name, port):
        self.pcap = directory / (name + ".pcapng")
        self.log_path = directory / (name + "-dumpcap.log")
        self.marker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.marker.bind(("127.0.0.1", 0))
        while self.marker.getsockname()[1] == port:
            self.marker.close()
            self.marker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.marker.bind(("127.0.0.1", 0))
        self.marker_port = self.marker.getsockname()[1]
        self.output = self.pcap.open("wb")
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen([
            "dumpcap", "-q", "-B", "32", "-i", "any", "-f", f"port {port} or udp port {self.marker_port}",
            "-a", "duration:120", "-w", "-",
        ], stdout=self.output, stderr=self.log)
        try:
            wait_for(lambda: self.process.poll() is not None or "File:" in self.log_path.read_text(), "capture did not start")
            require(self.process.poll() is None, "capture exited during startup")
            self.observe_nonce()
        except Exception:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
            self.output.close()
            self.log.close()
            self.marker.close()
            raise

    def observe_nonce(self):
        nonce = secrets.token_bytes(16)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            require(self.process.poll() is None, "capture exited before nonce observation")
            self.marker.sendto(nonce, self.marker.getsockname())
            result = subprocess.run(["tshark", "-n", "-r", str(self.pcap), "-Y",
                f"udp.dstport=={self.marker_port} && udp.payload=={nonce.hex(':')}",
                "-T", "fields", "-e", "frame.number"], text=True, capture_output=True, timeout=3)
            if result.stdout.strip():
                return
        raise RuntimeError("capture did not observe its own readiness/drain nonce")

    def stop(self):
        if self.output.closed:
            return
        failure = None
        try:
            self.observe_nonce()
        except Exception as error:
            failure = error
        self.stop_request_monotonic = time.monotonic()
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        self.process.wait(timeout=10)
        self.stop_complete_monotonic = time.monotonic()
        self.output.close()
        self.log.close()
        self.marker.close()
        validate_dumpcap_log(self.log_path.read_text())
        if failure:
            raise failure


