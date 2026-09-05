#!/usr/bin/env python3
"""Compare the same complete active Firefox application directly and through NaiveFox."""

import argparse
import concurrent.futures
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
from pathlib import Path
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
APP = HERE / "hybrid_app"


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


legacy = load_module("matched_app_capture_utilities", "carrier_capture.py")
features = legacy.features
native = legacy.native
ARMS = legacy.ARMS
VIEWS = legacy.VIEWS
require = legacy.require
write_json = legacy.write_json
digest = legacy.digest
MANIFEST_SHA = "3caeae3d8a8509d1453bcebda06150a63fe39b72c255f8a14ffd838abb1ce525"
CONTROL_COUNTS = {"binary_messages_sent": 21, "binary_messages_received": 165,
                  "control_messages_sent": 190, "control_messages_received": 43}


def warm_browser_navigation(driver, health_port, application_timeout):
    require(driver.capabilities.get("pageLoadStrategy") == "normal", "WebDriver must wait for completed navigation")
    driver.set_page_load_timeout(30)
    driver.get(f"https://127.0.0.1:{health_port}/health")
    require(driver.execute_script("return location.pathname === '/health' && document.readyState === 'complete'"),
            "common Firefox warmup did not complete")
    driver.get("about:blank")
    require(driver.execute_script("return document.documentURI === 'about:blank' && document.readyState === 'complete'"),
            "Firefox blank readiness failed")
    driver.set_page_load_timeout(application_timeout)


def expected_assets(directory):
    result = {}
    for path, file, size in (("/assets/site.css", "site.css", 12288),
                             ("/assets/app.js", "app.js", 24576),
                             *((f"/assets/image-{index}.svg", "image.svg", 8192) for index in range(1, 5))):
        source = directory / file
        body = source.read_bytes()
        require(len(body) <= size and (file != "app.js" or len(body) == size), "immutable asset size mismatch")
        result[path] = {"path": path, "bytes": size, "sha256": hashlib.sha256(body + b" " * (size - len(body))).hexdigest()}
    return result


def useful_totals(manifest):
    sent = sum(job["bytes"] for job in manifest["jobs"] if job["kind"] in ("upload", "echo"))
    received = sum(job["bytes"] for job in manifest["jobs"] if job["kind"] in ("download", "echo"))
    return sent, received


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate_consumer(consumer, assets, expected_protocol):
    require(isinstance(consumer, dict), "browser consumer proof is missing")
    navigation = consumer.get("navigation", {})
    require(navigation.get("decoded_body_size") == 4096 and navigation.get("response_status") == 200,
            "browser did not consume the complete root body")
    protocol = expected_protocol or navigation.get("next_hop_protocol")
    require(protocol in ("h2", "h3") and navigation.get("next_hop_protocol") == protocol, "browser root protocol differs")
    resources = consumer.get("resources", [])
    require(len(resources) == 6 and {item.get("path") for item in resources} == set(assets), "browser resource inventory differs")
    for item in resources:
        require(item.get("decoded_body_size") == assets[item["path"]]["bytes"] and item.get("response_status") == 200
                and item.get("next_hop_protocol") == protocol, "browser did not consume the expected asset body/protocol")
    images = consumer.get("images", [])
    require(len(images) == 4 and {item.get("path") for item in images} == {f"/assets/image-{index}.svg" for index in range(1, 5)}, "browser image inventory differs")
    require(all(item.get("complete") is True and item.get("decoded") is True
                and item.get("natural_width", 0) > 0 and item.get("natural_height", 0) > 0 for item in images), "browser image decoding failed")
    require(consumer.get("stylesheet_loaded") is True and finite(consumer.get("collected_ms")), "browser stylesheet/load proof is missing")
    return {"root_bytes": 4096, "asset_count": 6, "images_decoded": 4, "stylesheet_loaded": True, "http_protocol": protocol}


def validate_application(browser, backend, manifest, assets, expected_protocol=None):
    require(isinstance(browser, dict) and browser.get("manifest_sha256") == MANIFEST_SHA, "missing or mismatched application result")
    require(backend.get("manifest_sha256") == MANIFEST_SHA, "backend manifest differs")
    consumer = validate_consumer(browser.get("consumer"), assets, expected_protocol)
    expected_up, expected_down = useful_totals(manifest)
    require(browser.get("uploaded_bytes") == expected_up and browser.get("downloaded_bytes") == expected_down, "browser did not execute the complete active workload")
    require(browser.get("app_sha256") == assets["/assets/app.js"]["sha256"], "browser application script differs")
    require(len(browser.get("assets", [])) == 6 and {item["path"]: item for item in browser["assets"]} == assets, "browser asset inventory differs")
    ws = browser.get("websocket", {})
    require(ws.get("opened") == 1 and ws.get("closed") == 1 and ws.get("close_code") == 1000 and ws.get("clean") is True, "application WebSocket did not close normally")
    require(all(ws.get(key) == value for key, value in CONTROL_COUNTS.items()), "application message graph differs")
    require(backend.get("ws_opened") == 1 and backend.get("ws_closed") == 1 and backend.get("normal_closes") == 1
            and backend.get("bootstrap_completed") == 1 and backend.get("api_posts") == 20 and backend.get("api_gets") == 20
            and backend.get("catalog_records") == 1280 and backend.get("rejected", 0) == 0
            and backend.get("asset_failures", 0) == 0, "backend did not admit the complete application")
    connections = backend.get("connections", [])
    require(len(connections) == 1, "application opened more than one WebSocket")
    connection = connections[0]
    require(connection.get("normal_close") is True and connection.get("close_code") == 1000 and not connection.get("failure"), "backend application close failed")
    require(connection.get("bootstrap_pairs") == 20 and connection.get("parallel_batches") == 1
            and connection.get("parallel_job_count") == 4 and connection.get("peak_jobs") == 4, "application concurrency or bootstrap differs")
    require(connection.get("data_bytes_in") == connection.get("expected_data_bytes_in") == expected_up
            and connection.get("data_bytes_out") == connection.get("expected_data_bytes_out") == expected_down, "backend useful data totals differ")
    require((connection.get("data_messages_in"), connection.get("data_messages_out"),
             connection.get("control_messages_in"), connection.get("control_messages_out")) == (21, 165, 190, 43), "backend message graph differs")
    require(connection.get("open_order") == list(range(1, 12)), "application job order differs")
    cookie_hash = connection.get("asset_cookie_hash")
    group = backend.get("asset_groups", {}).get(cookie_hash, {}).get("responses", {})
    require(set(group) == set(assets), "application assets are missing or substituted by carrier assets")
    for path, expected in assets.items():
        response = group[path]
        require(all(response.get(key) == value for key, value in expected.items())
                and response.get("requests") == 1 and response.get("completed") == 1
                and response.get("written_bytes") == expected["bytes"], "actual application asset response differs")
    api = backend.get("api", [])
    require(len(api) == 40, "semantic bootstrap response inventory is incomplete")
    for index, response in enumerate(api):
        require(response.get("method") == ("POST" if index % 2 == 0 else "GET")
                and response.get("path") == f"/app/api/bootstrap/{index // 2}"
                and response.get("asset_cookie_hash") == cookie_hash
                and response.get("response_bytes", 0) > 0
                and len(response.get("response_sha256", "")) == 64, "semantic bootstrap graph differs")
    specifications = {job["id"]: job for job in manifest["jobs"]}
    actual = {job["id"]: job for job in connection.get("jobs", [])}
    require(set(actual) == set(specifications), "backend omitted application jobs")
    for identity, spec in specifications.items():
        job = actual[identity]
        require(all(job.get(key) == spec[key] for key in ("id", "kind", "bytes", "sha256"))
                and job.get("verified") is True
                and job.get("validated") == (spec["bytes"] if spec["kind"] in ("upload", "echo") else 0), "backend job failed integrity")
        require(job.get("received") == (spec["bytes"] if spec["kind"] in ("upload", "echo") else 0)
                and job.get("sent") == (spec["bytes"] if spec["kind"] in ("download", "echo") else 0), "backend job direction differs")
    stages = browser.get("stages", [])
    require(len(stages) == len(manifest["stages"]), "browser omitted active stages")
    summaries = []
    previous_end = ws.get("open_ms", 0) + manifest["idle_before_ms"]
    for specification, stage in zip(manifest["stages"], stages):
        require(stage.get("name") == specification["name"], "browser stage order differs")
        jobs = stage.get("jobs", [])
        require([job.get("id") for job in jobs] == specification["job_ids"], "browser stage job set differs")
        require(all(finite(stage.get(key)) for key in ("io_start_ms", "io_end_ms", "verified_ms")), "invalid application timing marker")
        require(stage["io_start_ms"] + 2 >= previous_end and stage["io_start_ms"] <= stage["io_end_ms"] <= stage["verified_ms"], "application timing order differs")
        if stage["name"] == "wake":
            require(stage["io_start_ms"] + 2 >= previous_end + manifest["idle_wake_ms"], "wake did not follow the declared idle period")
        for job in jobs:
            spec = specifications[job["id"]]
            require(all(job.get(key) == spec[key] for key in ("id", "kind", "bytes", "sha256")), "browser job integrity differs")
            require(all(finite(job.get(key)) for key in ("io_start_ms", "io_end_ms", "verified_ms"))
                    and job["io_start_ms"] <= job["io_end_ms"] <= job["verified_ms"], "browser hashed before its I/O completion marker")
        expected_sent = sum(specifications[job["id"]]["bytes"] for job in jobs if job["kind"] in ("upload", "echo"))
        expected_received = sum(specifications[job["id"]]["bytes"] for job in jobs if job["kind"] in ("download", "echo"))
        require(stage.get("sent_bytes") == expected_sent and stage.get("received_bytes") == expected_received
                and stage.get("useful_bytes") == sum(specifications[job["id"]]["bytes"] for job in jobs), "stage payload totals differ")
        summaries.append({"stage": stage["name"], "io_ms": stage["io_end_ms"] - stage["io_start_ms"],
                          "job_io_ms": [job["io_end_ms"] - job["io_start_ms"] for job in jobs],
                          "useful_bytes": stage["useful_bytes"], "sent_bytes": expected_sent, "received_bytes": expected_received})
        previous_end = stage["verified_ms"]
    require(finite(ws.get("close_ms")) and ws["close_ms"] >= previous_end, "application closed before verified completion")
    return {"manifest_sha256": MANIFEST_SHA, "app_sha256": browser["app_sha256"],
            "uploaded_bytes": expected_up, "downloaded_bytes": expected_down, "jobs_verified": 11,
            "asset_responses_verified": 6, "api_responses_verified": 40, "parallel_jobs": 4,
            "application_websockets": 1, "normal_close": True, "consumer": consumer, "stages": summaries,
            "startup_to_app_ws_ms": ws["open_ms"], "complete_app_ms": ws["close_ms"],
            "api_body_inventory": [{key: entry[key] for key in ("method", "path", "request_bytes", "request_sha256", "response_bytes", "response_sha256")} for entry in api]}


class OwnedProcess:
    def __init__(self, command, directory, name, env):
        self.log = (directory / f"{name}.log").open("wb")
        self.process = subprocess.Popen(list(map(str, command)), cwd=directory, env=env, stdout=self.log, stderr=self.log)
        self.name = name
        self.directory = directory
        self.forced = False
        self.stop_signal = None
        self.graceful_requested = False

    def stop(self, graceful=True, signal_number=signal.SIGTERM):
        self.graceful_requested = self.graceful_requested or graceful
        if self.process.poll() is None:
            self.stop_signal = signal_number
            self.process.send_signal(signal_number)
            try:
                self.process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                self.forced = True
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()
        write_json(self.directory / f"{self.name}-process-exit.json", {
            "returncode": self.process.returncode, "requested_signal": signal.Signals(self.stop_signal).name if self.stop_signal is not None else None,
            "harness_forced_kill": self.forced, "graceful_required": self.graceful_requested})
        if graceful:
            require(not self.forced and self.process.returncode == 0, f"{self.name} did not stop gracefully")


def process_identity(pid):
    try:
        values = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return values[19], values[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


def remember_tree(pid, known):
    identity = process_identity(pid)
    if identity is None:
        return
    known.setdefault(pid, identity[0])
    result = subprocess.run(["ps", "-o", "pid=", "--ppid", str(pid)], text=True, capture_output=True)
    for value in result.stdout.split():
        remember_tree(int(value), known)


def live_owned(known):
    return [pid for pid, start in known.items() if (current := process_identity(pid)) and current[0] == start and current[1] != "Z"]


class CompleteCapture(legacy.Capture):
    def __init__(self, directory, port):
        self.pcap = directory / "session-raw.pcapng"
        self.log_path = directory / "dumpcap.log"
        self.marker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.marker.bind(("127.0.0.1", 0))
        while self.marker.getsockname()[1] == port:
            self.marker.close()
            self.marker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.marker.bind(("127.0.0.1", 0))
        self.marker_port = self.marker.getsockname()[1]
        self.output = self.pcap.open("wb")
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen(["dumpcap", "-q", "-B", "32", "-i", "any", "-f",
            f"port {port} or icmp or icmp6 or udp port {self.marker_port}", "-a", "duration:180", "-w", "-"],
            stdout=self.output, stderr=self.log)
        try:
            legacy.wait_for(lambda: self.process.poll() is not None or "File:" in self.log_path.read_text(), "capture did not start")
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
            require(self.process.poll() is None, "capture exited before RX nonce observation")
            self.marker.sendto(nonce, self.marker.getsockname())
            result = subprocess.run(["tshark", "-n", "-r", str(self.pcap), "-Y",
                f"sll.pkttype==0 && udp.dstport=={self.marker_port} && udp.payload=={nonce.hex(':')}",
                "-T", "fields", "-e", "frame.number"], text=True, capture_output=True, timeout=3)
            if result.stdout.strip():
                return
        raise RuntimeError("capture did not observe its own receive-side nonce")


def run_quiet(command, **kwargs):
    return subprocess.run(list(map(str, command)), check=True, text=True, capture_output=True, **kwargs)


def expected_filter_rules(port):
    rules = {}
    priority = 10
    for protocol in (6, 17):
        for destination, flow in ((True, "1:2"), (False, "1:3")):
            rules[priority] = (flow, {(protocol << 16, 0x00ff0000, 8),
                (port if destination else port << 16, 0x0000ffff if destination else 0xffff0000, 20)})
            priority += 1
    for protocol in (6, 17):
        for source, flow in ((True, "1:2"), (False, "1:3")):
            rules[priority] = (flow, {(1 << 16, 0x00ff0000, 8), (0x45000000, 0xff000000, 0),
                (0x45000000, 0xff000000, 28), (protocol << 16, 0x00ff0000, 36),
                (port << 16 if source else port, 0xffff0000 if source else 0x0000ffff, 48)})
            priority += 1
    return rules


def verify_filter_text(text, port):
    observed = {}
    current = None
    for line in text.splitlines():
        if line.startswith("filter "):
            match = re.search(r"protocol ip pref (\d+).*flowid (\S+)", line)
            current = int(match.group(1)) if match else None
            if match:
                require(current not in observed, "duplicate outer classifier priority")
                observed[current] = (match.group(2), set())
        elif current is not None and (match := re.search(r"match ([0-9a-f]+)/([0-9a-f]+) at (\d+)", line)):
            observed[current][1].add((int(match.group(1), 16), int(match.group(2), 16), int(match.group(3))))
    require(observed == expected_filter_rules(port), "outer classifier protocol/port/direction keys differ")


def assert_isolated_namespace():
    current = os.readlink("/proc/self/ns/net")
    require(current != os.readlink("/proc/1/ns/net"), "refusing host network namespace mutation")
    require(os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED") == "1", "isolated namespace entry is missing")
    links = json.loads(run_quiet(["ip", "-j", "address", "show"]).stdout)
    require({entry["ifname"] for entry in links} == {"lo", "naivefox0"}, "unexpected isolated network interfaces")
    by_name = {entry["ifname"]: entry for entry in links}
    require(by_name["lo"]["mtu"] == 1500 and "UP" in by_name["lo"].get("flags", []), "isolated loopback topology differs")
    require(any(address.get("local") == "127.0.0.1" for address in by_name["lo"].get("addr_info", [])), "loopback address differs")
    require(any(address.get("local") == "192.0.2.1" and address.get("prefixlen") == 32 for address in by_name["naivefox0"].get("addr_info", [])), "isolated dummy address differs")
    routes = json.loads(run_quiet(["ip", "-j", "route", "show"]).stdout)
    defaults = [entry for entry in routes if entry.get("dst") == "default"]
    require(len(defaults) == 1 and defaults[0].get("dev") == "naivefox0", "isolated default route differs")
    return current


class OuterLink:
    def __init__(self, port, profile):
        self.port, self.profile = port, profile
        self.installed = False
        self.namespace = None

    def install(self):
        self.namespace = assert_isolated_namespace()
        if self.profile == "loopback":
            return
        run_quiet(["tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "prio", "bands", "3", "priomap", *(["0"] * 16)])
        self.installed = True
        for parent, handle in (("1:2", "20:"), ("1:3", "30:")):
            run_quiet(["tc", "qdisc", "add", "dev", "lo", "parent", parent, "handle", handle,
                       "netem", "delay", "20ms", "rate", "20mbit", "limit", "10000"])
        priority = 10
        for protocol in ("6", "17"):
            for direction, flow in (("dport", "1:2"), ("sport", "1:3")):
                run_quiet(["tc", "filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:", "prio", str(priority),
                           "u32", "match", "ip", "protocol", protocol, "0xff", "match", "ip", direction, str(self.port), "0xffff", "flowid", flow])
                priority += 1
        for quoted_protocol in ("6", "17"):
            for offset, flow in (("48", "1:2"), ("50", "1:3")):
                run_quiet(["tc", "filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:", "prio", str(priority), "u32",
                           "match", "ip", "protocol", "1", "0xff", "match", "u8", "0x45", "0xff", "at", "0",
                           "match", "u8", "0x45", "0xff", "at", "28", "match", "u8", quoted_protocol, "0xff", "at", "37",
                           "match", "u16", str(self.port), "0xffff", "at", offset, "flowid", flow])
                priority += 1
        self.validate()

    def validate(self):
        qdiscs = json.loads(run_quiet(["tc", "-j", "-s", "qdisc", "show", "dev", "lo"]).stdout)
        filters = json.loads(run_quiet(["tc", "-j", "-s", "filter", "show", "dev", "lo", "parent", "1:"]).stdout) if self.profile != "loopback" else []
        filter_text = ""
        if self.profile != "loopback":
            filter_text = run_quiet(["tc", "-d", "filter", "show", "dev", "lo", "parent", "1:"]).stdout
            verify_filter_text(filter_text, self.port)
            shaped = [entry for entry in qdiscs if entry.get("kind") == "netem"]
            require({entry.get("parent") for entry in shaped} == {"1:2", "1:3"}, "missing independent outer link directions")
            text = run_quiet(["tc", "qdisc", "show", "dev", "lo"]).stdout
            require(text.count("delay 20ms") == 2 and text.count("rate 20Mbit") == 2, "link parameters differ from the declaration")
            require(sum(entry.get("drops", 0) for entry in qdiscs) == 0, "outer link dropped packets")
            flows = [entry.get("options", {}).get("flowid") for entry in filters]
            require(flows.count("1:2") == 4 and flows.count("1:3") == 4, "outer filter topology differs")
        return {"profile": self.profile, "qdiscs": qdiscs, "filters": filters, "filter_text": filter_text,
                "one_way_delay_ms": 20 if self.profile != "loopback" else 0,
                "uplink_mbit": 20 if self.profile != "loopback" else 0,
                "downlink_mbit": 20 if self.profile != "loopback" else 0}

    def close(self):
        if self.installed:
            require(assert_isolated_namespace() == self.namespace, "namespace identity changed before qdisc removal")
            run_quiet(["tc", "qdisc", "del", "dev", "lo", "root"])
            self.installed = False


def ack_covers(ack, target):
    return ((ack - target) & 0xffffffff) < 0x80000000


def validate_tcp_termination(pcap, port):
    rows = features.tshark_rows(str(pcap), [], f"sll.pkttype==0 && tcp.port=={port} && !icmp && !icmpv6",
        ["tcp.stream", "tcp.dstport", "tcp.srcport", "tcp.flags.syn", "tcp.flags.ack", "tcp.flags.fin", "tcp.flags.reset",
         "tcp.seq_raw", "tcp.ack_raw", "tcp.len", "frame.number"])
    groups = {}
    for row in rows:
        groups.setdefault(row["tcp.stream"], []).append(row)
    finished = reset = 0
    for group in groups.values():
        require(any(features.truthy(row["tcp.flags.syn"]) and not features.truthy(row["tcp.flags.ack"])
                    and row["tcp.dstport"] == str(port) for row in group), "TCP origin is missing")
        if any(features.truthy(row["tcp.flags.reset"]) for row in group):
            reset += 1
            continue
        fins = [row for row in group if features.truthy(row["tcp.flags.fin"])]
        require(any(row["tcp.dstport"] == str(port) for row in fins) and any(row["tcp.srcport"] == str(port) for row in fins), "TCP flow lacks complete FIN directions")
        for fin in fins:
            target = (int(fin["tcp.seq_raw"]) + int(fin["tcp.len"] or 0) + 1) & 0xffffffff
            require(any(int(row["frame.number"]) >= int(fin["frame.number"])
                        and row["tcp.srcport"] == fin["tcp.dstport"] and features.truthy(row["tcp.flags.ack"])
                        and ack_covers(int(row["tcp.ack_raw"]), target) for row in group), "TCP FIN is not acknowledged")
        finished += 1
    return {"tcp_flows": len(groups), "tcp_fin_completed": finished, "tcp_reset_completed": reset}


def icmp_feedback_event(entry, port, epoch):
    lengths = features.split_values(entry["ip.len"])
    header_lengths = features.split_values(entry["ip.hdr_len"])
    require(len(lengths) >= 2 and header_lengths[:2] == ["20", "20"], "unmodeled ICMP header layout")
    source = entry.get("udp.srcport") or entry.get("tcp.srcport")
    destination = entry.get("udp.dstport") or entry.get("tcp.dstport")
    require((source == str(port)) != (destination == str(port)), "ICMP does not identify one origin direction")
    length = int(lengths[0])
    return {"frame": int(entry["frame.number"]), "time": float(entry["frame.time_epoch"]) - epoch,
            "direction": 1 if source == str(port) else -1, "wire_size": length,
            "transport_size": length - 20, "flow": "", "syn": False, "ack": False, "fin": False, "rst": False,
            "retransmission": False, "out_of_order": False, "lost_segment": False, "row": entry}


def origin_wire_summary(events):
    summary = legacy.wire_summary(events)
    summary["outer_flows"] = len({identity for event in events for identity in features.split_values(event.get("flow", "")) if identity})
    return summary


def observer_document(directory, port, protocol, row, sample):
    raw = directory / "session-raw.pcapng"
    receive = directory / "session-observer.pcapng"
    relevant = f"((tcp.port=={port} || udp.port=={port}) && !icmpv6)"
    addresses = features.tshark_rows(str(raw), [], f"(tcp.port=={port} || udp.port=={port}) && !icmp && !icmpv6", ["ip.src", "ip.dst", "ip.hdr_len", "ipv6.src", "ipv6.dst", "sll.pkttype"])
    ipv6 = features.tshark_rows(str(raw), [], f"ipv6 && (tcp.port=={port} || udp.port=={port})", ["frame.number"])
    require(not ipv6, "IPv6 origin traffic bypassed the declared outer link")
    require(addresses and all(not entry["ipv6.src"] and not entry["ipv6.dst"]
                and entry["ip.src"] == "127.0.0.1" and entry["ip.dst"] == "127.0.0.1"
                and entry["ip.hdr_len"] == "20" for entry in addresses), "outer endpoint bypassed the fixed IPv4 route")
    require(any(entry["sll.pkttype"] == "0" for entry in addresses), "receive-side packet copy is unavailable")
    run_quiet(["tshark", "-n", "-r", raw, "-Y", f"sll.pkttype==0 && {relevant}", "-w", receive])
    transport = directory / "session-transport.pcapng"
    run_quiet(["tshark", "-n", "-r", receive, "-Y", "!icmp && !icmpv6", "-w", transport])
    document, _ = legacy.passive_document(transport, port, protocol, row, sample)
    events, tcp, quic, records = legacy.outer_events(transport, port)
    reference_epoch = features.tshark_rows(str(transport), [], "frame.number==1", ["frame.time_epoch"])
    require(reference_epoch, "observer transport trace is empty")
    epoch = float(reference_epoch[0]["frame.time_epoch"])
    icmp = features.tshark_rows(str(receive), [], f"icmp && (udp.port=={port} || tcp.port=={port})", ["frame.number", "frame.time_epoch", "ip.len", "ip.hdr_len", "udp.srcport", "udp.dstport", "tcp.srcport", "tcp.dstport", "icmp.type"])
    frame_rows = features.tshark_rows(str(receive), [], "!icmp && !icmpv6", ["frame.number"])
    frame_numbers = [int(item["frame.number"]) for item in frame_rows]
    for event in events:
        event["frame"] = frame_numbers[event["frame"] - 1]
    events.extend(icmp_feedback_event(entry, port, epoch) for entry in icmp)
    events.sort(key=lambda event: (event["time"], event["frame"]))
    require(all(event["wire_size"] <= 1500 for event in events), "oversized offload packet reached observer")
    values = document["features"]
    features.add_aggregate(values, "whole", events)
    features.add_aggregate(values, "lifecycle_tail_16", events[-16:])
    features.add_sequence_features(values, events)
    values["whole_icmp_packet_count"] = len(icmp)
    values["whole_icmp_wire_bytes"] = sum(int(features.split_values(entry["ip.len"])[0]) for entry in icmp)
    features.validate_features(values)
    return document, {**origin_wire_summary(events), **validate_tcp_termination(transport, port),
                      "icmp_feedback_packets": len(icmp), "outer_ipv4_only": True, "capture_copy": "receive_after_qdisc"}


def queues_empty(snapshot):
    shaped = [item for item in snapshot["qdiscs"] if item.get("kind") == "netem"]
    if snapshot["profile"] == "loopback":
        return True
    require(len(shaped) == 2 and all("backlog" in item and "qlen" in item for item in shaped), "qdisc queue evidence is missing")
    return all(item["backlog"] == 0 and item["qlen"] == 0 for item in shaped)


def latest_outer_packet(pcap, port):
    result = subprocess.run(["tshark", "-n", "-r", str(pcap), "-Y",
        f"sll.pkttype==0 && (tcp.port=={port} || udp.port=={port})", "-T", "fields", "-e", "frame.time_epoch"],
        text=True, capture_output=True, timeout=10)
    values = [float(value) for value in result.stdout.splitlines() if value]
    return max(values) if values else None


def drain_complete(capture, link, port, known, directory):
    started = time.monotonic()
    quiet_seconds = 0.12 if link.profile != "loopback" else 0.01
    observed = None
    while time.monotonic() - started < 25:
        require(not live_owned(known), "owned network producer survived shutdown")
        snapshot = link.validate()
        latest = latest_outer_packet(capture.pcap, port)
        if queues_empty(snapshot) and latest is not None and time.time() - latest >= quiet_seconds:
            try:
                validate_tcp_termination(capture.pcap, port)
            except RuntimeError:
                pass
            else:
                capture.observe_nonce()
                after = latest_outer_packet(capture.pcap, port)
                if after == latest and queues_empty(link.validate()) and time.time() - after >= quiet_seconds:
                    observed = {"required_quiet_ms": 1000 * quiet_seconds,
                                "observed_quiet_ms": 1000 * (time.time() - after),
                                "drain_ms": 1000 * (time.monotonic() - started),
                                "live_producers": 0, "both_direction_queues_empty": True,
                                "nonce_after_final_wire_observation": True}
                    break
        time.sleep(0.01)
    require(observed is not None, "complete-session drain did not converge")
    write_json(directory / "drain.json", observed)
    return observed


def choose_ports(count):
    result = []
    while len(result) < count:
        port = native.free_port()
        if port in result:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            try:
                udp.bind(("127.0.0.1", port))
            except OSError:
                continue
        result.append(port)
    return result


def established_owners(port):
    rows = run_quiet(["ss", "-Hntp", "state", "established", "dst", f"127.0.0.1:{port}"]).stdout.splitlines()
    owners = []
    for row in rows:
        pids = {int(value) for value in re.findall(r"pid=(\d+)", row)}
        require(pids, "socket ownership is unavailable")
        owners.append(pids)
    return owners


def validate_routing_owners(reference, outer_port, inner_port, listener_port, caddy_pid, firefox_pids):
    target = established_owners(outer_port if reference else inner_port)
    require(target, "application WebSocket has no observable endpoint connection")
    expected = set(firefox_pids) if reference else {caddy_pid}
    require(all(owners.issubset(expected) for owners in target), "application traffic bypassed its declared route")
    proxy = [] if reference else established_owners(listener_port)
    require(reference or (proxy and all(owners.issubset(firefox_pids) for owners in proxy)), "application bypassed the selected local listener")
    return {"verified": True, "target_connections": len(target), "selected_listener_connections": len(proxy),
            "target_owned_by": "Firefox" if reference else "Caddy proxy"}


class Campaign:
    def __init__(self, args, protocol):
        self.args, self.protocol = args, protocol
        self.root = args.root / protocol
        self.root.mkdir(mode=0o700)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.outer_port, self.inner_port, self.backend_port, self.health_port = choose_ports(4)
        self.link = OuterLink(self.outer_port, args.link)
        self.fixture = self.private / "fixture"
        self.fixture.mkdir(mode=0o700)
        self.assets = expected_assets(args.asset_dir)
        self.manifest = json.loads((args.asset_dir / "manifest.json").read_text())
        self.user, self.password = native.fixture_credentials()
        self.samples = []
        self.common_api_inventory = None
        self.input_hashes = {str(path): digest(path) for path in args.frozen_files}
        self.root_body_sha = None
        self.active_processes = []
        self.active_browsers = []

    def check_inputs(self):
        require(all(digest(Path(path)) == expected for path, expected in self.input_hashes.items()), "a frozen input changed during collection")

    def start(self):
        native.issue_certificates(self.fixture)
        trusted = self.fixture / "trusted"
        trusted.mkdir()
        run_quiet(["certutil", "-N", "-d", "sql:" + str(trusted), "--empty-password"])
        run_quiet(["certutil", "-A", "-d", "sql:" + str(trusted), "-n", "Matched App Fixture Root", "-t", "CT,,", "-i", self.fixture / "ca.crt"])
        self.link.install()
        write_json(self.private / "link-initial.json", self.link.validate())
        self.preflight()

    def services(self, directory):
        backend = OwnedProcess([self.args.backend, "--listen", f"127.0.0.1:{self.backend_port}",
            "--stats", directory / "backend-stats.json", "--ready", directory / "backend-ready.json", "--asset-dir", self.args.asset_dir], directory, "backend", os.environ.copy())
        self.active_processes.append(backend)
        legacy.wait_for(lambda: (directory / "backend-ready.json").exists() or backend.process.poll() is not None, "application backend did not start")
        require(backend.process.poll() is None, "application backend exited")
        ready = json.loads((directory / "backend-ready.json").read_text())
        require(ready.get("port") == self.backend_port and ready.get("manifest_sha256") == MANIFEST_SHA, "backend readiness differs from frozen inputs")
        (directory / "Caddyfile").write_text(native.caddyfile_text((self.inner_port,)))
        env = dict(os.environ, NF_PROTOCOL=self.protocol, NF_PORT=str(self.outer_port),
                   NF_CERT=str(self.fixture / "server.crt"), NF_CERT_KEY=str(self.fixture / "server.key"),
                   NF_PROXY_USER=self.user, NF_PROXY_PASSWORD=self.password, NF_ACCESS_LOG=str(directory / "access.jsonl"),
                   XDG_DATA_HOME=str(directory / "caddy-data"), XDG_CONFIG_HOME=str(directory / "caddy-config"))
        adapted = run_quiet([self.args.caddy, "adapt", "--config", directory / "Caddyfile", "--adapter", "caddyfile"], env=env)
        config = json.loads(adapted.stdout)
        servers = config["apps"]["http"]["servers"]
        require(len(servers) == 1, "unexpected Caddy server template")
        outer = next(iter(servers.values()))
        inner = copy.deepcopy(outer)
        health = copy.deepcopy(outer)
        health["listen"] = [f"127.0.0.1:{self.health_port}"]
        health["protocols"] = ["h1", "h2"]
        health["routes"] = [{"match": [{"path": ["/health"]}],
                             "handle": [{"handler": "static_response", "status_code": 200, "body": "fixture ready\n"}]},
                            {"handle": [{"handler": "static_response", "status_code": 404}]}]
        servers["matched_inner"] = inner
        servers["matched_health"] = health
        for name, server, port, protocols in (("outer", outer, self.outer_port, ["h1", self.protocol]),
                                               ("inner", inner, self.inner_port, ["h1", "h2"])):
            server["listen"] = [f"127.0.0.1:{port}"]
            server["protocols"] = protocols
            module = None
            def find(value):
                nonlocal module
                if isinstance(value, dict):
                    if value.get("handler") == "naivefox_transport":
                        module = copy.deepcopy(value)
                    for item in value.values():
                        find(item)
                elif isinstance(value, list):
                    for item in value:
                        find(item)
            find(server)
            require(module is not None, "real native transport handler is missing")
            module["stats_path"] = str(directory / f"{name}-carrier-stats.json")
            module["application_root"] = str(native.prepare_application(directory))
            proxy = {"handler": "reverse_proxy", "upstreams": [{"dial": f"127.0.0.1:{self.backend_port}"}]}
            server["routes"] = [
                {"match": [{"path": ["/health"]}], "handle": [{"handler": "static_response", "status_code": 200, "body": "fixture ready\n"}]},
                {"match": [{"path": ["/assets/*", "/app/api/bootstrap/*"]}], "handle": [copy.deepcopy(proxy)]},
                {"match": [{"path": ["/api/realtime"], "header": {"Sec-Websocket-Protocol": ["nfbench.app.v1"]}}], "handle": [copy.deepcopy(proxy)]},
                {"handle": [module, {"handler": "static_response", "status_code": 404}]},
            ]
        write_json(directory / "caddy.json", config)
        caddy = OwnedProcess([self.args.caddy, "run", "--config", directory / "caddy.json"], directory, "caddy", env)
        self.active_processes.append(caddy)
        legacy.wait_for(lambda: (native.socket_listeners(self.outer_port) and native.socket_listeners(self.inner_port)) or caddy.process.poll() is not None, "Caddy endpoints did not start")
        require(caddy.process.poll() is None, "Caddy exited during startup")
        require(native.socket_listeners(self.outer_port, udp=True) == (self.protocol == "h3"), "outer startup protocol listener differs")
        return caddy, backend

    def preflight(self):
        directory = self.private / "tls-preflight"
        directory.mkdir()
        caddy = backend = None
        root_hashes = []
        try:
            caddy, backend = self.services(directory)
            for name, port in (("outer", self.outer_port), ("inner", self.inner_port)):
                jar = directory / (name + ".cookies")
                for index, path in enumerate(("/", *self.assets)):
                    output = directory / f"{name}-{index}.body"
                    result = run_quiet(["curl", "--silent", "--show-error", "--fail", "--noproxy", "*", "--http1.1",
                        "--resolve", f"localhost:{port}:127.0.0.1", "--cacert", self.fixture / "ca.crt",
                        "--cookie", jar, "--cookie-jar", jar, "--output", output,
                        "--write-out", "%{http_code} %{ssl_verify_result}", f"https://localhost:{port}{path}"])
                    require(result.stdout == "200 0", "TLS preflight did not validate an ordinary successful response")
                    if path == "/":
                        require(output.stat().st_size == 4096, "root representation length differs")
                        root_hashes.append(digest(output))
                    else:
                        require(output.stat().st_size == self.assets[path]["bytes"] and digest(output) == self.assets[path]["sha256"], "actual TLS asset body differs from immutable producer")
            require(len(set(root_hashes)) == 1, "outer and inner root bodies differ")
            self.root_body_sha = root_hashes[0]
            write_json(self.root / "tls-preflight.json", {"root_bytes": 4096, "root_sha256": self.root_body_sha,
                       "asset_responses_equal": True, "assets": list(self.assets.values()), "certificate_verification": True})
        finally:
            if caddy:
                caddy.stop()
            if backend:
                backend.stop()
        deadline = time.monotonic() + 10
        empty_since = None
        while time.monotonic() < deadline:
            if queues_empty(self.link.validate()):
                empty_since = empty_since or time.monotonic()
                if time.monotonic() - empty_since >= (0.12 if self.args.link != "loopback" else 0.01):
                    return
            else:
                empty_since = None
            time.sleep(0.01)
        raise RuntimeError("preflight traffic did not drain")

    def client(self, directory, kind, transport):
        port = native.free_port()
        config = {"listen": f"{kind}://127.0.0.1:{port}",
                  "proxy": native.proxy_uri(self.protocol, self.outer_port, self.user, self.password),
                  "transport": transport, "host-resolver-rules": "MAP localhost 127.0.0.1", "max-connections": 0, "log": ""}
        write_json(directory / "native.json", config)
        env = {key: value for key, value in os.environ.items() if key not in ("NAIVEFOX_PROFILE", "SSLKEYLOGFILE", "SSL_CERT_FILE", "MOZ_LOG", "MOZ_LOG_FILE", "LD_PRELOAD")}
        env.update(SSL_CERT_FILE=str(self.fixture / "ca.crt"), LD_LIBRARY_PATH=str(self.args.runtime.parent), TMPDIR=str(directory), MOZ_CRASHREPORTER_DISABLE="1")
        process = OwnedProcess([self.args.runtime, directory / "native.json"], directory, "native", env)
        self.active_processes.append(process)
        legacy.wait_for(lambda: native.socket_listeners(port) or process.process.poll() is not None, "selected local listener did not start")
        require(process.process.poll() is None, "native client exited during startup")
        return process, port

    def browser(self, directory, kind=None, local_port=0):
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
        import shutil
        profile = directory / "browser-profile"
        shutil.copytree(self.fixture / "trusted", profile)
        preferences = legacy.firefox_preferences(self.protocol if kind is None else "h2", self.outer_port,
            local_port if kind == "socks" else 0, local_port if kind == "http" else 0, self.inner_port)
        preferences.update({"browser.safebrowsing.realTime.enabled": False,
                            "browser.safebrowsing.globalCache.enabled": False,
                            "browser.safebrowsing.provider.google5.enabled": False})
        write_json(directory / "browser-preferences.json", preferences)
        (profile / "user.js").write_text("".join(f"user_pref({json.dumps(key)}, {json.dumps(value)});\n" for key, value in preferences.items()))
        options = Options()
        options.binary_location = str(self.args.firefox)
        options.profile = str(profile)
        options.accept_insecure_certs = False
        options.page_load_strategy = "normal"
        options.add_argument("-headless")
        options.add_argument("--width=1280")
        options.add_argument("--height=720")
        service = Service(executable_path=str(self.args.geckodriver), service_args=["--profile-root", str(directory)],
                          log_output=str(directory / "webdriver.log"), env=os.environ.copy())
        driver = webdriver.Firefox(options=options, service=service)
        owned = {}
        remember_tree(driver.capabilities["moz:processID"], owned)
        remember_tree(driver.service.process.pid, owned)
        self.active_browsers.append((driver, owned))
        warm_browser_navigation(driver, self.health_port, self.args.timeout)
        return driver, owned

    def stop_browser(self, driver, known):
        remember_tree(driver.capabilities["moz:processID"], known)
        if driver.service.process is not None:
            remember_tree(driver.service.process.pid, known)
        service_process = driver.service.process
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(driver.quit)
        try:
            future.result(timeout=30)
        finally:
            pool.shutdown(wait=False)
        legacy.wait_for(lambda: not live_owned(known), "Firefox process tree survived graceful quit", timeout=20)
        return {"webdriver_quit_completed": True, "webdriver_service_returncode": service_process.poll() if service_process else None,
                "live_owned_processes": 0, "harness_forced_kills": 0}

    def sample(self, row, index):
        name = f"sample-{index:03d}"
        directory = self.private / name
        directory.mkdir()
        result = {"sample": name, **row, "admitted": False}
        arm = row["naivefox_arm"]
        reference = arm == "reference"
        kind = "http" if arm.endswith("-http") else "socks"
        transport = None if reference else arm[len("native-"):-(len(kind) + 1)]
        caddy = backend = client = monitor = driver = capture = None
        browser_owned = {}
        all_owned = {}
        try:
            self.check_inputs()
            caddy, backend = self.services(directory)
            monitor = legacy.native.Process([sys.executable, HERE / "monitor-network-mutations.py", "--ready", directory / "network-ready",
                "--events", directory / "network-events", "--done", directory / "network-done"], directory, "network", os.environ.copy())
            legacy.wait_for(lambda: (directory / "network-ready").exists() or monitor.process.poll() is not None, "network monitor did not start")
            require(monitor.process.poll() is None, "network monitor exited")
            capture = CompleteCapture(directory, self.outer_port)
            local_port = 0
            if not reference:
                client, local_port = self.client(directory, kind, transport)
            driver, browser_owned = self.browser(directory, None if reference else kind, local_port)
            for process in (caddy, backend, client):
                if process:
                    remember_tree(process.process.pid, all_owned)
            remember_tree(driver.capabilities["moz:processID"], browser_owned)
            remember_tree(driver.service.process.pid, browser_owned)
            capture.observe_nonce()
            before_navigation = features.tshark_rows(str(capture.pcap), [],
                f"tcp.port=={self.outer_port} || udp.port=={self.outer_port}", ["frame.number"])
            require(not before_navigation, "native/browser startup or warmup contacted the measured origin before navigation")
            access = [json.loads(line) for line in (directory / "access.jsonl").read_text().splitlines() if line.strip()]
            origin_requests = [entry for entry in access if entry.get("request", {}).get("host", "").endswith(f":{self.outer_port}")]
            require(not origin_requests, "pre-navigation origin request contaminated the cold session")
            result["pre_navigation_origin_packets"] = 0
            result["pre_navigation_origin_requests"] = 0
            navigation_epoch = time.time()
            navigation_clock = time.monotonic()
            driver.get(f"https://localhost:{self.outer_port if reference else self.inner_port}/")
            app_result = None
            routing = None
            routing_epoch = None
            while time.monotonic() - navigation_clock < self.args.timeout:
                value = driver.execute_script("return {result:window.__NFB_RESULT__||null,error:window.__NFB_ERROR__||null}")
                backend_path = directory / "backend-stats.json"
                if routing is None and backend_path.exists():
                    current = json.loads(backend_path.read_text())
                    if current.get("ws_opened") == 1 and current.get("ws_closed") == 0:
                        remember_tree(driver.capabilities["moz:processID"], browser_owned)
                        routing = validate_routing_owners(reference, self.outer_port, self.inner_port, local_port,
                            caddy.process.pid, set(browser_owned))
                        routing_epoch = time.time()
                if isinstance(value, dict):
                    require(not value.get("error"), "matched application reported a terminal failure")
                    if value.get("result"):
                        app_result = value["result"]
                        break
                require(caddy.process.poll() is None and backend.process.poll() is None and (client is None or client.process.poll() is None), "a producer exited before application completion")
                time.sleep(0.05)
            require(app_result is not None, "matched application did not complete all active jobs")
            write_json(directory / "browser-result.json", app_result)
            open_epoch = (app_result["time_origin_ms"] + app_result["websocket"]["open_ms"]) / 1000
            active_epoch = (app_result["time_origin_ms"] + app_result["stages"][0]["io_start_ms"]) / 1000
            require(routing is not None and routing_epoch < active_epoch, "accepted application WebSocket routing was not verified before active work")
            result["routing"] = {**routing, "verified_before_active_work": True,
                                 "observation_relative_to_browser_open_ms": 1000 * (routing_epoch - open_epoch)}
            app_done_ms = 1000 * (time.monotonic() - navigation_clock)
            browser_shutdown = self.stop_browser(driver, browser_owned)
            driver = None
            all_owned.update(browser_owned)
            if client:
                client.stop(signal_number=signal.SIGINT)
                client = None
            caddy.stop()
            caddy = None
            backend.stop()
            backend = None
            require(not live_owned(all_owned), "owned producers survived shutdown")
            result["drain"] = drain_complete(capture, self.link, self.outer_port, all_owned, directory)
            capture.stop()
            capture = None
            monitor.stop()
            require(monitor.process.returncode == 0 and (directory / "network-done").exists()
                    and not (directory / "network-events").read_bytes(), "network mutated during the complete session")
            monitor = None
            backend_result = json.loads((directory / "backend-stats.json").read_text())
            proof = validate_application(app_result, backend_result, self.manifest, self.assets,
                "h3" if reference and self.protocol == "h3" else "h2")
            inventory = proof.pop("api_body_inventory")
            if self.common_api_inventory is None:
                self.common_api_inventory = inventory
            require(inventory == self.common_api_inventory, "actual semantic API bytes differ across participants")
            carrier = json.loads((directory / "outer-carrier-stats.json").read_text())
            if reference:
                require(carrier.get("opens", 0) == 0 and carrier.get("ws_opened", 0) == 0
                        and carrier.get("upload_bytes", 0) == 0 and carrier.get("download_bytes", 0) == 0, "reference generated native carrier filler or target traffic")
            elif transport == "classic":
                require(carrier.get("connect", 0) > 0 and carrier.get("ws_opened", 0) == 0, "classic did not use its native CONNECT path")
            else:
                require(carrier.get("connect", 0) == 0, "no-connect emitted an outer CONNECT")
                expected_ws = 1 if transport == "no-connect" else 0
                require(carrier.get("ws_opened", 0) == expected_ws, "native carrier WebSocket count differs")
                if expected_ws:
                    require(carrier.get("ws_startup_min_up") == 20 and carrier.get("ws_startup_min_down") == 20, "native WS bypassed startup completion")
                    expected_subprotocol = "nfc1.stream.v1"
                    require(carrier.get("ws_subprotocols") == {expected_subprotocol: 1},
                            "native carrier selected the wrong WebSocket shaping protocol")
            document, wire = observer_document(directory, self.outer_port, self.protocol, row, name)
            result.update(application=proof, whole=wire, app_done_ms=app_done_ms,
                          process_teardown={"live_owned_processes": 0, "harness_forced_kills": 0, "producer_count": len(all_owned), **browser_shutdown},
                          capture_drops=0, network_mutations=0, root_sha256=self.root_body_sha,
                          selected_listener=None if reference else kind, carrier_websockets=carrier.get("ws_opened", 0),
                          carrier_shape={key: carrier.get(key, 0) for key in
                                         ("upload_bytes", "download_bytes", "upload_filler", "download_filler",
                                          "upload_useful", "download_useful", "ws_messages_in", "ws_messages_out",
                                          "ws_upload_bytes", "ws_download_bytes", "ws_upload_filler", "ws_download_filler",
                                          "ws_upload_useful", "ws_download_useful",
                                          "ws_cell_capacities", "ws_subprotocols", "ws_activities", "ws_hints")})
            write_json(directory / "timing-origin-private.json", {"navigation_epoch": navigation_epoch,
                "performance_time_origin_ms": app_result["time_origin_ms"], "first_download_io_ms": app_result["stages"][0]["io_start_ms"]})
            write_json(self.private / "link-final.json", self.link.validate())
            self.check_inputs()
            write_json(self.root / "features" / (name + ".json"), document)
            result["admitted"] = True
        except Exception as error:
            import traceback
            (directory / "failure.txt").write_text(traceback.format_exc())
            result["failure"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        finally:
            if driver:
                try:
                    self.stop_browser(driver, browser_owned)
                except Exception:
                    for pid, start in browser_owned.items():
                        current = process_identity(pid)
                        if current and current[0] == start and current[1] != "Z":
                            os.kill(pid, signal.SIGKILL)
            for process in (client, caddy, backend):
                if process:
                    process.stop(graceful=False)
            if capture:
                try:
                    capture.stop()
                except Exception:
                    pass
            if monitor:
                monitor.stop()
            write_json(self.root / (name + ".json"), result)
        require(result["admitted"], "participant failed; private diagnostics retained: " + name)
        self.samples.append(result)
        print(json.dumps({"protocol": self.protocol, "sample": name, "arm": arm, "admitted": True}), flush=True)

    def run(self):
        blocks = self.args.blocks
        legacy.superblocks.SUPPORTED_ARMS = tuple(dict.fromkeys((*legacy.superblocks.SUPPORTED_ARMS, *ARMS)))
        schedule = legacy.superblocks.schedule_rows(self.args.seed, self.protocol, blocks, ["matched_application"], ARMS)
        write_json(self.root / "schedule.json", schedule)
        (self.root / "features").mkdir()
        for index, row in enumerate(schedule):
            self.sample(row, index)
        features.merge(SimpleNamespace(input_dir=str(self.root / "features"), output=str(self.root / "features.csv"),
            expected_superblocks=blocks, expected_superblock_arms=",".join(ARMS)))
        analysis = legacy.module("matched_complete_session_analysis", "analyze-camouflage-arms.py")
        analysis.SUPERBLOCKS.SUPPORTED_ARMS = tuple(dict.fromkeys((*analysis.SUPERBLOCKS.SUPPORTED_ARMS, *ARMS)))
        analysis.ANALYSIS.view_feature_names = legacy.matrix_view_feature_names
        rows, names = analysis.load_dataset(str(self.root / "features.csv"))
        report = analysis.build_report(SimpleNamespace(mode="gate", seed=self.args.seed, bootstrap=1000,
            permutations=999, min_blocks=30, views=VIEWS), rows, names)
        report["methodology"]["observer_schema"] = "matched-active-application-v1-complete-session"
        report["methodology"]["whole_scope"] = "navigation through normal application close and all producer shutdown, empty qdisc queues, TCP terminal evidence and final capture drain"
        report["methodology"]["early_views"] = "strict packet indices/window aggregates; no future handshake or TLS-record ordinal leakage"
        write_json(self.root / "analysis.json", report)
        analysis.write_summary(str(self.root / "analysis.md"), report)
        write_json(self.root / "semantic-api-inventory.json", self.common_api_inventory)
        return report

    def close(self):
        for driver, known in self.active_browsers:
            if live_owned(known):
                try:
                    self.stop_browser(driver, known)
                except Exception:
                    for pid in live_owned(known):
                        os.kill(pid, signal.SIGKILL)
        for process in reversed(self.active_processes):
            process.stop(graceful=False)
        self.link.close()


def summarize(campaign, report):
    output = []
    reference = [sample for sample in campaign.samples if sample["naivefox_arm"] == "reference"]
    average = lambda records, key: statistics.fmean(record["whole"][key] for record in records)
    def stage_time(records, index):
        stages = [record["application"]["stages"][index] for record in records]
        if stages[0]["stage"] in ("small", "wake"):
            return statistics.fmean(statistics.fmean(stage["job_io_ms"]) for stage in stages)
        return statistics.fmean(stage["io_ms"] for stage in stages)
    for kind in ("socks", "http"):
        arm = f"native-no-connect-{kind}"
        candidates = [sample for sample in campaign.samples if sample["naivefox_arm"] == arm]
        row = {"startup_protocol": campaign.protocol, "listener": kind, "blocks": campaign.args.blocks,
               "residual": {view: report["protocols"][campaign.protocol]["views"][view]["arms"][arm] for view in VIEWS},
               "whole_ip_bytes": average(candidates, "wire_bytes"), "comparisons": {}}
        for baseline in ("firefox", "classic"):
            controls = reference if baseline == "firefox" else [sample for sample in campaign.samples if sample["naivefox_arm"] == f"native-{baseline}-{kind}"]
            values = {"baseline_whole_ip_bytes": average(controls, "wire_bytes"),
                      "extra_complete_session_traffic_percent": 100 * (average(candidates, "wire_bytes") / average(controls, "wire_bytes") - 1), "stages": {}}
            for metric in ("startup_to_app_ws_ms", "complete_app_ms"):
                old = statistics.fmean(item["application"][metric] for item in controls)
                new = statistics.fmean(item["application"][metric] for item in candidates)
                values[metric] = {"baseline": old, "candidate": new, "time_increase_percent": 100 * (new / old - 1) if old > 0 else None}
            if baseline != "firefox":
                values["residual"] = {view: report["protocols"][campaign.protocol]["views"][view]["arms"][f"native-{baseline}-{kind}"] for view in VIEWS}
            for index, stage in enumerate(candidates[0]["application"]["stages"]):
                old_time, new_time = stage_time(controls, index), stage_time(candidates, index)
                latency = stage["stage"] in ("small", "wake")
                resolved = old_time > 0 and new_time > 0
                measurement = {"baseline_io_ms": old_time, "candidate_io_ms": new_time,
                    "time_increase_percent": 100 * (new_time / old_time - 1) if resolved else None,
                    "timer_resolved_ratio": resolved,
                    "measurement": "mean single-echo I/O latency" if latency else "complete stage I/O duration"}
                if not latency:
                    measurement.update(effective_rate_loss_percent=100 * (1 - old_time / new_time) if resolved else None,
                        baseline_mbit_s=stage["useful_bytes"] * 8 / old_time / 1000 if old_time > 0 else None,
                        candidate_mbit_s=stage["useful_bytes"] * 8 / new_time / 1000 if new_time > 0 else None)
                values["stages"][stage["stage"]] = measurement
            row["comparisons"][baseline] = values
        output.append(row)
    return output


def verify_native_runtime(manifest_path, runtime):
    manifest = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    files = []
    for item in manifest.get("files", []):
        path = (root / item["path"]).resolve(strict=True)
        require(path.is_relative_to(root) and path.stat().st_size == item["size"] and digest(path) == item["sha256"], "native package differs from its runtime manifest")
        files.append(path)
    require(runtime in files and runtime.parent / "libxul.so" in files, "selected native executable is not the manifest runtime")
    version = run_quiet([runtime, "--version"], env=dict(os.environ, LD_LIBRARY_PATH=str(runtime.parent))).stdout.strip()
    return {"manifest": manifest, "version_output": version,
            "application_ini": (runtime.parent / "application.ini").read_text(),
            "provenance_note": "Native mapped build is attested by package hashes and build ID, separately from the test harness commit."}, files


def verify_caddy_build_id(path, caddy=None):
    if path.suffix == ".json":
        proof = json.loads(path.read_text())
        require(proof.get("schema") == "naivefox-local-caddy-build-v1" and caddy is not None,
                "unsupported local Caddy build proof")
        require(digest(caddy) == proof["binary_sha256"], "local Caddy binary differs")
        require(proof.get("go_version") == "go1.25.12"
                and proof.get("source_revision"), "local Caddy provenance is incomplete")
        for name, expected in proof["source_files_sha256"].items():
            source = (path.parent / "source" / name).resolve(strict=True)
            require(source.is_relative_to((path.parent / "source").resolve())
                    and digest(source) == expected, "local Caddy source snapshot differs")
        require(proof["source_files_sha256"] and
                digest(path.parent / "caddy.build-info") == proof["build_info_sha256"],
                "local Caddy build metadata differs")
        return proof

    values = dict(line.split("=", 1) for line in (HERE / "versions.env").read_text().splitlines()
                  if line and not line.startswith("#"))
    expected = (f"caddy={values['CADDY_VERSION']} xcaddy={values['XCADDY_VERSION']} "
                f"module={values['FORWARDPROXY_MODULE']}@{values['FORWARDPROXY_VERSION']}="
                f"{values['FORWARDPROXY_REPLACEMENT']}@{values['FORWARDPROXY_COMMIT']} "
                f"transport={values['NAIVEFOX_TRANSPORT_MODULE']}@{values['NAIVEFOX_TRANSPORT_COMMIT']} "
                f"go={values['GO_VERSION']}")
    require(path.read_text().strip() == expected, "Caddy build ID differs from the pinned server source")
    return expected


def main():
    global ARMS
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("objdir", "root", "caddy", "caddy-build-id", "backend", "firefox", "geckodriver", "reference-proof", "runtime-manifest"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path, default=APP)
    parser.add_argument("--firefox-base", required=True)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--link", choices=("loopback", "rtt40-20mbps"), required=True)
    parser.add_argument("--purpose", choices=("pilot", "primary"), required=True)
    parser.add_argument("--blocks", type=int, default=10)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    require(os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED") == "1", "isolated namespace is required")
    require(1 <= args.blocks <= 30 and 30 <= args.timeout <= 150, "campaign bounds are invalid")
    require(args.purpose != "primary" or (args.link == "rtt40-20mbps" and args.blocks == 10), "primary link or sample contract differs")
    args.objdir = args.objdir.resolve(strict=True)
    args.root = args.root.resolve()
    require(args.root.is_relative_to(args.objdir / "no-connect/matched-app") and not args.root.exists(), "new campaign root must be beneath the common matched-app subtree")
    for name in ("caddy", "caddy_build_id", "backend", "firefox", "geckodriver", "reference_proof", "asset_dir", "runtime_manifest"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.runtime = args.runtime.resolve(strict=True)
    base = run_quiet(["git", "-C", HERE, "merge-base", "HEAD", "firefox-upstream"]).stdout.strip()
    require(base == args.firefox_base and digest(args.asset_dir / "manifest.json") == MANIFEST_SHA, "frozen Firefox base or manifest differs")
    reference_proof = legacy.verify_reference(args.reference_proof, args.firefox, base)
    native_proof, native_files = verify_native_runtime(args.runtime_manifest, args.runtime)
    caddy_build_id = verify_caddy_build_id(args.caddy_build_id, args.caddy)
    source_files = [Path(__file__), *(HERE / name for name in ("carrier_capture.py", "run-no-connect-tests.py",
        "camouflage_features.py", "analyze-camouflage-arms.py", "analyze-camouflage.py", "camouflage_superblocks.py",
        "camouflage_browser_controller.py", "camouflage_capture_health.py", "monitor-network-mutations.py", "versions.env")),
        *(args.asset_dir / name for name in ("manifest.json", "app.js", "app.template.js", "render-app.py", "main.go", "go.mod", "go.sum", "site.css", "image.svg", "index.html"))]
    named_inputs = {"source/" + str(path.relative_to(HERE)): path for path in source_files}
    named_inputs.update({"native_runtime/" + str(path.relative_to(args.runtime_manifest.parent)): path for path in native_files})
    named_inputs.update({"reference_runtime/" + name: args.firefox.parent / name for name in reference_proof["runtime_files_sha256"]})
    named_inputs.update({"native_runtime/manifest.json": args.runtime_manifest, "reference/proof.json": args.reference_proof,
                         "tools/caddy": args.caddy, "tools/caddy.build-id": args.caddy_build_id,
                         "tools/nfbench-app": args.backend})
    args.frozen_files = list(named_inputs.values())
    os.umask(0o077)
    args.root.mkdir(parents=True)
    runtime = args.root / "runtime"
    runtime.mkdir()
    os.environ.update(TMPDIR=str(runtime), XDG_RUNTIME_DIR=str(runtime), LD_LIBRARY_PATH=str(args.firefox.parent),
                      MOZ_HEADLESS="1", MOZ_CRASHREPORTER_DISABLE="1", MOZ_CRASHREPORTER_NO_REPORT="1")
    tempfile.tempdir = str(runtime)
    for key in ("SSLKEYLOGFILE", "DISPLAY", "WAYLAND_DISPLAY", "MOZ_LOG", "MOZ_LOG_FILE"):
        os.environ.pop(key, None)
    proof = {"schema": "matched-active-application-v1-complete-session", "purpose": args.purpose,
        "source_revision": run_quiet(["git", "-C", HERE, "rev-parse", "HEAD"]).stdout.strip(),
        "frozen_inputs_sha256": {name: digest(path) for name, path in named_inputs.items()},
        "native_artifact": native_proof, "verified_reference": reference_proof, "manifest": json.loads((args.asset_dir / "manifest.json").read_text()),
        "caddy_build_id": caddy_build_id,
        "manifest_sha256": MANIFEST_SHA, "seed": args.seed, "blocks_per_protocol": args.blocks,
        "link": args.link, "observer": "receive-side complete origin TCP/QUIC and attributable ICMP; no fixed crop or per-stage wire allocation",
        "local_listener_topology": "only the selected listener", "screening_only": True,
        "comparison_baseline": "classic"}
    write_json(args.root / "provenance.json", proof)
    matrix = []
    for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,)):
        campaign = Campaign(args, protocol)
        try:
            campaign.start()
            report = campaign.run()
            matrix.extend(summarize(campaign, report))
            write_json(args.root / "matrix.json", {"schema": proof["schema"], "purpose": args.purpose,
                                                   "screening_only": True, "comparison_baseline": proof["comparison_baseline"],
                                                   "rows": matrix})
        finally:
            campaign.close()
    print(json.dumps({"result": str(args.root / "matrix.json"), "status": "matched_workload_screening"}), flush=True)


if __name__ == "__main__":
    main()
