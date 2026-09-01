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
TRANSPORTS = ("classic", "no-connect", "no-connect-hybrid")
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


class Campaign:
    def __init__(self, args, protocol):
        self.args, self.protocol = args, protocol
        self.root = args.root / protocol
        self.root.mkdir(mode=0o700)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.fixture_state = self.private / "fixture"
        self.fixture = self.fixture_state
        self.fixture_processes = []
        self.samples = []
        self.expected_downloads = {}

    def start(self):
        self.fixture.mkdir()
        native.issue_certificates(self.fixture)
        pki = self.fixture / "pki"
        pki.mkdir()
        for name, source in (("root.crt", "ca.crt"), ("target.crt", "server.crt"), ("target.key", "server.key")):
            (pki / name).symlink_to(self.fixture / source)
        trusted = self.fixture / "profiles/trusted"
        trusted.mkdir(parents=True)
        for command in (["certutil", "-N", "-d", "sql:" + str(trusted), "--empty-password"],
                        ["certutil", "-A", "-d", "sql:" + str(trusted), "-n", "NaiveFox Matrix Root", "-t", "CT,,", "-i", str(pki / "root.crt")]):
            result = subprocess.run(command, text=True, capture_output=True)
            require(result.returncode == 0, "isolated NSS fixture trust failed")
        (self.fixture / "completions").mkdir()
        target = native.Process([sys.executable, str(INTEGRATION / "target_server.py"),
            "--cert", str(pki / "target.crt"), "--key", str(pki / "target.key"),
            "--completion-dir", str(self.fixture / "completions"),
            "--request-journal", str(self.fixture / "cache-requests.jsonl"),
            "--ready-file", str(self.fixture / "target-ready.json")], self.fixture, "target", os.environ.copy())
        self.fixture_processes.append(target)
        wait_for(lambda: (self.fixture / "target-ready.json").exists() or target.process.poll() is not None, "target fixture did not start")
        require(target.process.poll() is None, "target fixture exited")
        ready = json.loads((self.fixture / "target-ready.json").read_text())
        self.target_port = native.free_port()
        self.port = native.free_port(udp=self.protocol == "h3")
        while self.port == self.target_port:
            self.port = native.free_port(udp=self.protocol == "h3")
        user, password = native.fixture_credentials()
        self.values = {"NAIVEFOX_FIXTURE_USER": user, "NAIVEFOX_FIXTURE_PASS": password,
                       "NAIVEFOX_FIXTURE_HTTPS_PORT": ready["https_port"], "NAIVEFOX_FIXTURE_HTTP_PORT": ready["http_port"]}
        env = dict(os.environ, NAIVEFOX_FIXTURE_INNER_H2_PORT=str(self.target_port),
                   NAIVEFOX_FIXTURE_HTTP_PORT=str(ready["http_port"]),
                   NAIVEFOX_FIXTURE_TARGET_CERT=str(pki / "target.crt"), NAIVEFOX_FIXTURE_TARGET_KEY=str(pki / "target.key"),
                   NAIVEFOX_FIXTURE_INNER_H2_ACCESS_LOG=str(self.fixture / "inner-h2-access.jsonl"),
                   XDG_DATA_HOME=str(self.fixture / "inner-data"), XDG_CONFIG_HOME=str(self.fixture / "inner-config"))
        adapted = subprocess.run([str(self.args.caddy), "adapt", "--config", str(INTEGRATION / "Caddyfile-inner-h2"), "--adapter", "caddyfile"], env=env, text=True, capture_output=True)
        require(adapted.returncode == 0, "inner-H2 configuration adaptation failed")
        write_json(self.fixture / "inner.json", json.loads(adapted.stdout))
        inner = native.Process([str(self.args.caddy), "run", "--config", str(self.fixture / "inner.json")], self.fixture, "inner", env)
        self.fixture_processes.append(inner)
        wait_for(lambda: native.socket_listeners(self.target_port) or inner.process.poll() is not None, "inner-H2 listener did not start")
        require(inner.process.poll() is None, "inner-H2 server exited")
        (self.fixture / "Caddyfile").write_text(native.caddyfile_text((self.target_port,)))
        env.update(NF_PROTOCOL=self.protocol, NF_PORT=str(self.port), NF_CERT=str(pki / "target.crt"),
                   NF_CERT_KEY=str(pki / "target.key"), NF_PROXY_USER=user, NF_PROXY_PASSWORD=password,
                   NF_ACCESS_LOG=str(self.fixture / "access.jsonl"))
        adapted = subprocess.run([str(self.args.caddy), "adapt", "--config", str(self.fixture / "Caddyfile"), "--adapter", "caddyfile"], env=env, text=True, capture_output=True)
        require(adapted.returncode == 0, "outer configuration adaptation failed")
        self.base_config = json.loads(adapted.stdout)
        if self.args.warm_bytes:
            for size in sorted({self.args.warm_bytes, 4096, 524288}):
                output = self.fixture / f"expected-{size}.body"
                result = subprocess.run(["curl", "--silent", "--show-error", "--fail", "--noproxy", "*",
                    "--output", str(output), f"http://127.0.0.1:{ready['http_port']}/camouflage/resource?size={size}"],
                    text=True, capture_output=True, timeout=30)
                require(result.returncode == 0 and output.stat().st_size == size, "direct warm-workload fixture failed")
                self.expected_downloads[size] = digest(output)
        self.base_forward = None
        def find(value):
            if isinstance(value, dict):
                if value.get("handler") == "naivefox_transport":
                    self.base_forward = value.get("forward_proxy")
                for item in value.values():
                    find(item)
            elif isinstance(value, list):
                for item in value:
                    find(item)
        find(self.base_config)
        require(self.base_forward is not None, "fixture shared forward-proxy authority absent")

    def close(self):
        for process in reversed(self.fixture_processes):
            process.stop()

    def caddy(self, directory):
        config = json.loads(json.dumps(self.base_config))
        servers = config["apps"]["http"]["servers"]
        require(len(servers) == 1, "unexpected outer fixture server count")
        server = next(iter(servers.values()))
        server["protocols"] = ["h1", self.protocol]
        server["routes"] = [{"handle": [{
            "handler": "naivefox_transport", "profile": "continuous-bulk-pipeline",
            "forward_proxy": self.base_forward, "stats_path": str(directory / "server-stats.json"),
        }]}]
        write_json(directory / "caddy.json", config)
        process = native.Process([str(self.args.caddy), "run", "--config", str(directory / "caddy.json")], directory, "caddy",
                                 dict(os.environ, XDG_DATA_HOME=str(directory / "caddy-data"), XDG_CONFIG_HOME=str(directory / "caddy-config")))
        wait_for(lambda: process.process.poll() is not None or "server running" in process.log_path.read_text(), "sample Caddy did not start")
        require(process.process.poll() is None, "sample Caddy exited")
        return process

    def browser(self, directory, kind=None, port=0):
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
        profile = directory / "browser-profile"
        shutil.copytree(self.fixture / "profiles/trusted", profile)
        prefs = firefox_preferences(self.protocol if kind is None else "h2", self.port,
                                    port if kind == "socks" else 0, port if kind == "http" else 0, self.target_port)
        prefs.update({"browser.safebrowsing.realTime.enabled": False,
                      "browser.safebrowsing.globalCache.enabled": False,
                      "browser.safebrowsing.provider.google5.enabled": False})
        (profile / "user.js").write_text("".join(f"user_pref({json.dumps(name)}, {json.dumps(value)});\n" for name, value in prefs.items()))
        options = Options()
        options.binary_location = str(self.args.firefox)
        options.profile = str(profile)
        options.accept_insecure_certs = False
        options.page_load_strategy = "none"
        options.add_argument("-headless")
        service = Service(executable_path=str(self.args.geckodriver), log_output=str(directory / "webdriver.log"),
                          service_args=["--profile-root", str(directory)], env=os.environ.copy())
        driver = webdriver.Firefox(options=options, service=service)
        driver.get("about:blank")
        if kind is None and self.protocol == "h3":
            token = secrets.token_hex(16)
            driver.get(f"https://127.0.0.1:{self.values['NAIVEFOX_FIXTURE_HTTPS_PORT']}/camouflage/index.html?scenario=initial&completion={token}")
            wait_for(lambda: (self.fixture / "completions" / token).exists(), "reference H3 storage warmup failed")
            driver.get("about:blank")
        return driver

    def transfer(self, directory, ports, kind, name, size, upload=False):
        output = directory / (name + ".body")
        scheme = "socks5h" if kind == "socks" else "http"
        command = ["curl", "--silent", "--show-error", "--fail", "--max-time", "60", "--noproxy", "",
                   "--proxy", f"{scheme}://127.0.0.1:{ports[kind]}", "--http2", "--cacert", str(self.fixture / "pki/root.crt"),
                   "--output", str(output), "--write-out", "%{time_total}"]
        if upload:
            body = directory / (name + ".upload")
            payload = (bytes(range(256)) * ((size + 255) // 256))[:size]
            body.write_bytes(payload)
            command.extend(["--data-binary", "@" + str(body)])
            path = "/camouflage/upload"
        else:
            path = "/camouflage/resource?size=" + str(size)
        command.append(f"https://localhost:{self.target_port}" + path)
        started = time.monotonic()
        result = subprocess.run(command, text=True, capture_output=True, timeout=65)
        returned = time.monotonic()
        require(result.returncode == 0, "warm transfer failed: " + name)
        if upload:
            reply = json.loads(output.read_text())
            require(reply.get("bytes") == size and reply.get("sha256") == hashlib.sha256(payload).hexdigest(), "warm upload integrity failure")
        else:
            require(output.stat().st_size == size and digest(output) == self.expected_downloads[size], "warm download integrity failure")
        return {"useful_bytes": size, "completion_ms": 1000 * float(result.stdout), "started": started, "returned": returned}

    def warm(self, directory, ports, kind):
        rows = []
        for name, size, upload in (("download", self.args.warm_bytes, False), ("upload", 1048576, True),
                                   ("small", 4096, False), ("parallel", 524288, False)):
            capture = Capture(directory, "warm-" + name, self.port)
            try:
                if name == "parallel":
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                        jobs = [pool.submit(self.transfer, directory, ports, kind, f"parallel-{index}", size) for index in range(4)]
                        values = [job.result() for job in jobs]
                    origin = min(value["started"] for value in values)
                    completed = max(value["returned"] for value in values)
                    value = {"useful_bytes": sum(item["useful_bytes"] for item in values),
                             "completion_ms": max((item["started"] - origin) * 1000 + item["completion_ms"] for item in values)}
                else:
                    value = self.transfer(directory, ports, kind, name, size, upload)
                    completed = value.pop("returned")
                    value.pop("started")
                time.sleep(0.2)
            finally:
                capture.stop()
            events, _, _, _ = outer_events(capture.pcap, self.port)
            require(events and all(event["wire_size"] <= 1500 for event in events), "warm packet admission failure")
            rows.append({"stage": name, "settle_after_validation_ms": 200,
                         "post_curl_return_stop_request_ms": 1000 * (capture.stop_request_monotonic - completed),
                         "post_curl_return_stop_complete_ms": 1000 * (capture.stop_complete_monotonic - completed),
                         **value, **wire_summary(events)})
        totals = {key: sum(row[key] for row in rows) for key in ("wire_bytes", "packets", "client_wire_bytes", "server_wire_bytes")}
        return {"stages": rows, "observer_windows": "sum of disjoint per-stage captures; each stops at least 200ms after validation, actual tails recorded", **totals}

    def sample(self, row, index):
        name = f"sample-{index:03d}"
        directory = self.private / name
        directory.mkdir(mode=0o700)
        result = {"sample": name, "protocol": self.protocol, **row, "admitted": False}
        caddy = client = monitor = driver = capture = None
        arm = row["naivefox_arm"]
        reference = arm == "reference"
        kind = "http" if arm.endswith("-http") else "socks"
        transport = "reference" if reference else arm[len("native-"):-(len(kind) + 1)]
        journal = self.fixture / "inner-h2-access.jsonl"
        offset = journal.stat().st_size if journal.exists() else 0
        try:
            caddy = self.caddy(directory)
            monitor = native.Process([sys.executable, str(INTEGRATION / "monitor-network-mutations.py"),
                "--ready", str(directory / "network-ready"), "--events", str(directory / "network-events"),
                "--done", str(directory / "network-done")], directory, "network", os.environ.copy())
            wait_for(lambda: (directory / "network-ready").exists() or monitor.process.poll() is not None, "network monitor failed")
            require(monitor.process.poll() is None, "network monitor exited")
            ports = None
            if not reference:
                shutil.copyfile(self.fixture / "pki/root.crt", directory / "ca.crt")
                options = SimpleNamespace(runtime=self.args.runtime, classic_preamble="default")
                client, ports = native.start_client(options, directory, "client", self.protocol, self.port, transport,
                    self.values["NAIVEFOX_FIXTURE_USER"], self.values["NAIVEFOX_FIXTURE_PASS"], 0)
            driver = self.browser(directory, None if reference else kind, 0 if reference else ports[kind])
            capture = Capture(directory, "cold", self.port)
            completion = secrets.token_hex(16)
            url = f"https://localhost:{self.port}/#realtime" if reference else f"https://localhost:{self.target_port}/camouflage/index.html?scenario=browser_page&asset_base=262144&completion={completion}"
            started = time.monotonic()
            started_epoch = time.time()
            driver.get(url)
            done = False
            while time.monotonic() - started < self.args.capture_seconds:
                if not done:
                    if reference:
                        state = driver.execute_script("return {done:!!window.__NFC_DONE__,phase:window.__NFC_PHASE__,error:window.__NFC_ERROR__||null}")
                        if not isinstance(state, dict):
                            time.sleep(0.01)
                            continue
                        require(not state.get("error"), "reference SPA reported failure")
                        done = state.get("phase") == "realtime" and state.get("done")
                    else:
                        done = (self.fixture / "completions" / completion).exists()
                    if done:
                        result["completion_ms"] = 1000 * (time.monotonic() - started)
                time.sleep(0.01)
            if transport == "no-connect-hybrid":
                require("No-connect hybrid websocket ready startup=20" in client.log_path.read_text(errors="replace"),
                        "hybrid did not finish startup and establish WS inside the capture window")
            capture.stop()
            capture = None
            require(done, "workload did not complete within the predeclared capture window")
            write_json(directory / "capture-boundary.json", {"start_epoch": started_epoch,
                "end_epoch": started_epoch + self.args.capture_seconds})
            sliced = directory / "cold-window.pcapng"
            subprocess.run(["editcap", "-A", f"{started_epoch:.9f}", "-B", f"{started_epoch + self.args.capture_seconds:.9f}",
                            str(directory / "cold.pcapng"), str(sliced)], check=True, capture_output=True)
            document, wire = passive_document(sliced, self.port, self.protocol, row, name)
            result["cold"] = {"completion_ms": result.pop("completion_ms"), **wire}
            if not reference and self.args.warm_bytes:
                result["warm"] = self.warm(directory, ports, kind)
            monitor.stop()
            require(monitor.process.returncode == 0 and (directory / "network-done").exists() and not (directory / "network-events").read_bytes(), "network changed during sample")
            monitor = None
            if not reference:
                with journal.open() as source:
                    source.seek(offset)
                    requests = [json.loads(line) for line in source if line.strip()]
                require(requests and all(item.get("request", {}).get("proto") == "HTTP/2.0" for item in requests), "inner workload did not exclusively use HTTPS/H2")
            driver.quit()
            driver = None
            if client:
                client.stop()
                client = None
            caddy.stop()
            caddy = None
            stats = json.loads((directory / "server-stats.json").read_text())
            requests = stats.get("requests", {})
            require(stats.get("connect", 0) == 0 if transport != "classic" else stats.get("connect", 0) > 0, "outer CONNECT contract failed")
            if transport != "classic":
                for path in ("GET /", "GET /assets/site.css", "GET /assets/app.js", *(f"GET /assets/image-{number}.svg" for number in range(1, 5))):
                    require(requests.get(path, 0) >= 1, "startup asset graph incomplete")
                require(requests.get("POST /api/sync", 0) >= 20, "startup API graph incomplete")
            expects_ws = reference or transport == "no-connect-hybrid"
            require(stats.get("ws_opened", 0) == (1 if expects_ws else 0), "wrong number of realtime sessions")
            if expects_ws:
                require(stats.get("startup_completed", 0) == 1 and stats.get("ws_startup_min_up") == 20
                        and stats.get("ws_startup_min_down") == 20, "WS opened before exact startup completion")
            startup_protocol = "HTTP/3.0" if self.protocol == "h3" else "HTTP/2.0"
            expected_protocols = {startup_protocol, "HTTP/1.1"} if expects_ws else {startup_protocol}
            require(set(stats.get("protocols", {})) == expected_protocols, "startup transport downgrade or unexpected WS route")
            result["admission"] = {"startup_graph": "passed", "inner_h2": not reference,
                "network_mutations": 0, "capture_drops": 0, "realtime_sessions": stats.get("ws_opened", 0),
                "outer_connects": stats.get("connect", 0),
                "ws_messages_in": stats.get("ws_messages_in", 0), "ws_messages_out": stats.get("ws_messages_out", 0),
                "ws_cell_capacities": stats.get("ws_cell_capacities", {})}
            write_json(self.root / "features" / (name + ".json"), document)
            result["admitted"] = True
        except Exception as error:
            import traceback
            (directory / "failure.txt").write_text(traceback.format_exc())
            result["failure"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        finally:
            if capture:
                capture.stop()
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            for process in (monitor, client, caddy):
                if process:
                    process.stop()
            write_json(self.root / (name + ".json"), result)
        require(result["admitted"], "sample failed; private diagnostics retained: " + name)
        self.samples.append(result)
        print(json.dumps({"protocol": self.protocol, "sample": name, "arm": arm, "admitted": True}), flush=True)

    def run(self):
        arms = ARMS if not self.args.without_classic else tuple(arm for arm in ARMS if "classic" not in arm)
        superblocks.SUPPORTED_ARMS = tuple(dict.fromkeys((*superblocks.SUPPORTED_ARMS, *ARMS)))
        schedule = superblocks.schedule_rows(self.args.seed, self.protocol, self.args.blocks, ["browser_page"], arms)
        if self.args.plumbing_arm:
            schedule = [{"protocol": self.protocol, "label": "firefox_a" if self.args.plumbing_arm == "reference" else "naivefox",
                         "naivefox_arm": self.args.plumbing_arm, "scenario": "browser_page", "experiment_block": self.protocol + "_plumbing"}]
        write_json(self.root / "schedule.json", schedule)
        (self.root / "features").mkdir()
        for index, row in enumerate(schedule):
            self.sample(row, index)
        if self.args.plumbing_arm:
            return None
        features.merge(SimpleNamespace(input_dir=str(self.root / "features"), output=str(self.root / "features.csv"),
                                       expected_superblocks=self.args.blocks, expected_superblock_arms=",".join(arms)))
        analysis = module("hybrid_arm_analysis", "analyze-camouflage-arms.py")
        analysis.SUPERBLOCKS.SUPPORTED_ARMS = tuple(dict.fromkeys((*analysis.SUPERBLOCKS.SUPPORTED_ARMS, *ARMS)))
        analysis.ANALYSIS.view_feature_names = matrix_view_feature_names
        rows, names = analysis.load_dataset(str(self.root / "features.csv"))
        report = analysis.build_report(SimpleNamespace(mode="gate", seed=self.args.seed, bootstrap=1000,
                                       permutations=999, min_blocks=30, views=VIEWS), rows, names)
        report["methodology"]["observer_schema"] = "native-hybrid-origin-v1-strict-packet-windows"
        report["methodology"]["early_views"] = "chronological packet indices and window aggregates only; no TLS-record ordinals or unbounded handshake summaries"
        write_json(self.root / "analysis.json", report)
        analysis.write_summary(str(self.root / "analysis.md"), report)
        return report


def summarize(campaign, report):
    output = []
    samples = campaign.samples
    for kind in ("socks", "http"):
        arm = f"native-no-connect-hybrid-{kind}"
        candidate = [sample for sample in samples if sample["naivefox_arm"] == arm]
        row = {"protocol": campaign.protocol, "listener": kind, "blocks": campaign.args.blocks,
               "residual": {view: report["protocols"][campaign.protocol]["views"][view]["arms"][arm] for view in VIEWS},
               "comparisons": {}}
        for baseline in ("classic", "no-connect"):
            old = [sample for sample in samples if sample["naivefox_arm"] == f"native-{baseline}-{kind}"]
            if not old:
                continue
            avg = lambda rows, phase, key: statistics.fmean(value[phase][key] for value in rows)
            values = penalties(avg(old, "cold", "completion_ms"), avg(candidate, "cold", "completion_ms"),
                               avg(old, "cold", "wire_bytes"), avg(candidate, "cold", "wire_bytes"))
            values.update(baseline_mean_ms=avg(old, "cold", "completion_ms"), candidate_mean_ms=avg(candidate, "cold", "completion_ms"),
                          baseline_wire_bytes=avg(old, "cold", "wire_bytes"), candidate_wire_bytes=avg(candidate, "cold", "wire_bytes"))
            comparison = {"cold": values, "residual": {
                view: report["protocols"][campaign.protocol]["views"][view]["arms"][f"native-{baseline}-{kind}"]
                for view in VIEWS}}

            if campaign.args.warm_bytes:
                comparison["warm"] = {"extra_outer_traffic_percent": 100 * (avg(candidate, "warm", "wire_bytes") / avg(old, "warm", "wire_bytes") - 1), "stages": {}}
                for index, stage in enumerate(candidate[0]["warm"]["stages"]):
                    old_time = statistics.fmean(item["warm"]["stages"][index]["completion_ms"] for item in old)
                    new_time = statistics.fmean(item["warm"]["stages"][index]["completion_ms"] for item in candidate)
                    old_wire = statistics.fmean(item["warm"]["stages"][index]["wire_bytes"] for item in old)
                    new_wire = statistics.fmean(item["warm"]["stages"][index]["wire_bytes"] for item in candidate)
                    comparison["warm"]["stages"][stage["stage"]] = {"baseline_ms": old_time, "candidate_ms": new_time,
                        "baseline_wire_bytes": old_wire, "candidate_wire_bytes": new_wire,
                        **penalties(old_time, new_time, old_wire, new_wire)}
            row["comparisons"][baseline] = comparison
        output.append(row)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--firefox", type=Path, required=True)
    parser.add_argument("--geckodriver", type=Path, required=True)
    parser.add_argument("--firefox-base", required=True, help="verified official reference Git base; must match current Firefox merge-base")
    parser.add_argument("--reference-proof", type=Path, required=True, help="proof created by verify-hybrid-reference.py outside the isolated namespace")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--blocks", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026083101)
    parser.add_argument("--capture-seconds", type=float, default=2)
    parser.add_argument("--warm-bytes", type=int, default=8388608)
    parser.add_argument("--without-classic", action="store_true")
    parser.add_argument("--plumbing-arm", choices=("reference", *ARMS), help="one participant only; never computes residual comparisons")
    parser.add_argument("--discard-private-on-success", action="store_true", help="remove captures/profiles only after successful analysis; default retains them for audit")
    args = parser.parse_args()
    require(1 <= args.blocks <= 64 and 2 <= args.capture_seconds <= 15 and 0 <= args.warm_bytes <= 16777216, "invalid bounded campaign inputs")
    require(os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK") == "1" and os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED") == "1", "one-shot isolated network namespace required")
    source_base = subprocess.check_output(["git", "-C", str(INTEGRATION), "merge-base", "HEAD", "firefox-upstream"], text=True).strip()
    require(args.firefox_base == source_base, "reference base differs from the current Firefox source base")
    args.objdir = args.objdir.resolve(strict=True)
    args.root = args.root.resolve()
    require(args.root.is_relative_to(args.objdir / "hybrid-ws") and not args.root.exists(), "root must be a new directory below the warm objdir hybrid-ws subtree")
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    for name in ("firefox", "caddy", "geckodriver"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    reference_proof = verify_reference(args.reference_proof, args.firefox, source_base)
    os.umask(0o077)
    args.root.mkdir(parents=True)
    runtime = args.root / "runtime"
    runtime.mkdir()
    os.environ.update(TMPDIR=str(runtime), XDG_RUNTIME_DIR=str(runtime), MOZ_HEADLESS="1", LD_LIBRARY_PATH=str(args.firefox.parent))
    tempfile.tempdir = str(runtime)
    for name in ("SSLKEYLOGFILE", "DISPLAY", "WAYLAND_DISPLAY", "MOZ_LOG", "MOZ_LOG_FILE"):
        os.environ.pop(name, None)
    write_json(args.root / "provenance.json", {
        "runtime_sha256": digest(args.runtime), "libxul_sha256": digest(args.runtime.parent / "libxul.so"),
        "firefox_sha256": digest(args.firefox), "firefox_libxul_sha256": digest(args.firefox.parent / "libxul.so"),
        "firefox_application_ini": (args.firefox.parent / "application.ini").read_text(),
        "firefox_base": source_base,
        "verified_reference": reference_proof,
        "reference_manifest": (args.objdir / "naivefox-capture-reference/REFERENCE-MANIFEST").read_text(),
        "caddy_sha256": digest(args.caddy), "runner_sha256": digest(Path(__file__)),
        "source_revision": subprocess.check_output(["git", "-C", str(INTEGRATION), "rev-parse", "HEAD"], text=True).strip(),
        "observer_unit": "all TCP and QUIC traffic to origin port, chronologically merged; no loopback proxy/target traffic",
        "observer_schema": "native-hybrid-origin-v1-strict-packet-windows",
        "capture_window_seconds": args.capture_seconds, "capture_readiness": "own auxiliary UDP nonce observed in actual pcap before work and before stop; auxiliary port never enters origin features",
        "cold_window_enforcement": "epoch-bounded editcap slice from navigation dispatch; original capture retained",
        "seed": args.seed, "blocks_per_protocol": args.blocks,
        "warm_download_bytes": args.warm_bytes, "plumbing_arm": args.plumbing_arm, "reference_lifecycle": "same-origin SPA bootstrap20 then H1 WSS; empty application capacity",
        "network": "isolated loopback MTU1500 offloads disabled; unshaped", "screening_only": True,
        "private_inputs_retained_for_audit": not args.discard_private_on_success,
        "denominators": "native classic and native no-connect with identical inner HTTPS/H2 work; rate_loss=100*(1-old_time/new_time); traffic_excess=100*(new_IP_wire/old_IP_wire-1)",
    })
    matrix = []
    for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,)):
        campaign = Campaign(args, protocol)
        try:
            campaign.start()
            report = campaign.run()
            if report is None:
                matrix.extend(campaign.samples)
                write_json(args.root / "plumbing.json", {"plumbing_only": True, "samples": matrix})
            else:
                matrix.extend(summarize(campaign, report))
                write_json(args.root / "matrix.json", {"screening_only": True, "rows": matrix})
        finally:
            campaign.close()
        if args.discard_private_on_success:
            shutil.rmtree(campaign.private)
    if args.discard_private_on_success:
        shutil.rmtree(runtime)
    filename = "plumbing.json" if args.plumbing_arm else "matrix.json"
    print(json.dumps({"result": str(args.root / filename), "status": "plumbing_only" if args.plumbing_arm else "screening_only"}), flush=True)


if __name__ == "__main__":
    main()
