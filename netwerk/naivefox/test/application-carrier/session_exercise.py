"""Late-work, bulk and idle checks for an already-running browser transport."""

import hashlib
import json
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

import camouflage_features as features
from camouflage_capture_health import validate_dumpcap_log


def state(worker):
    if worker is None:
        return {"phase": "native-control", "alive": True, "error": None, "dynamic": 0, "idle": 0, "wake": 0}
    value = worker.execute_script("return {phase:window.__NFC_PHASE__,alive:!!window.__NFC_ALIVE__,error:window.__NFC_ERROR__,dynamic:window.__NFC_DYNAMIC_ROUNDS__,idle:window.__NFC_IDLE_POLLS__,wake:window.__NFC_IDLE_WAKE_POSTS__}")
    if not value or value["error"] or not value["alive"]:
        raise RuntimeError("continuous worker stopped during session exercise")
    return value


def settle(worker, seconds=2):
    if worker is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + 15
    since = None
    while time.monotonic() < deadline:
        if state(worker)["phase"] == "idle":
            since = since or time.monotonic()
            if time.monotonic() - since >= seconds:
                return
        else:
            since = None
        time.sleep(0.05)
    raise RuntimeError("continuous worker did not settle")


def run(worker, directory, fixture, ready, target_port, http_port, outer_port, protocol, launch, idle_seconds=0):
    checks = []
    def direct(path):
        return subprocess.check_output(["curl", "--silent", "--show-error", "--fail", "--noproxy", "*", f"http://127.0.0.1:{http_port}" + path])

    def job(name, kind, path, upload=None):
        output = directory / (name + ".body")
        scheme = "socks5h" if kind == "socks" else "http"
        args = ["curl", "--silent", "--show-error", "--fail", "--max-time", "40", "--noproxy", "", "--proxy", scheme + "://" + ready[kind],
                "--http2", "--cacert", fixture / "pki/root.crt", "--output", output, f"https://localhost:{target_port}" + path]
        if upload is not None:
            args += ["--data-binary", "@" + str(upload)]
        started = time.monotonic()
        return launch(args, name + ".log"), output, started

    def await_jobs(name, jobs, expected=None, upload=None):
        print(json.dumps({"session_stage": name, "status": "running"}), flush=True)
        started = min(job[2] for job in jobs)
        deadline = started + 45
        idle_seen = False
        while time.monotonic() < deadline:
            current = state(worker)
            if all(process.poll() is not None for process, _, _ in jobs):
                break
            if current["phase"] == "idle" and time.monotonic() - started > 0.2:
                idle_seen = True
            time.sleep(0.01)
        elapsed = (time.monotonic() - started) * 1000
        if any(process.poll() != 0 for process, _, _ in jobs):
            raise RuntimeError("session transfer failed: " + name)
        useful = 0
        for _, output, _ in jobs:
            body = output.read_bytes()
            if upload is None:
                if hashlib.sha256(body).digest() != hashlib.sha256(expected).digest():
                    raise RuntimeError("session transfer digest: " + name)
                useful += len(body)
            else:
                reply = json.loads(body)
                if reply.get("bytes") != len(upload) or reply.get("sha256") != hashlib.sha256(upload).hexdigest():
                    raise RuntimeError("session upload digest")
                useful += len(upload)
        value = {"stage": name, "connections": len(jobs), "useful_bytes": useful, "completion_ms": round(elapsed, 3), "idle_seen_while_pending": idle_seen}
        checks.append(value)
        (directory / "session-checks.json").write_text(json.dumps(checks, indent=2) + "\n")
        print(json.dumps(value), flush=True)

    settle(worker)
    expected = direct("/camouflage/resource?size=1048576")
    await_jobs("download-after-idle", [job("late-download", "socks", "/camouflage/resource?size=1048576")], expected)
    settle(worker)
    expected = direct("/camouflage/delay?ms=0")
    await_jobs("delayed-server-response", [job("delayed", "http", "/camouflage/delay?ms=1500")], expected)
    if worker is not None and not checks[-1]["idle_seen_while_pending"]:
        raise RuntimeError("delayed server response did not exercise idle")
    settle(worker)
    payload = bytes(range(256)) * 4096
    upload = directory / "upload.body"
    upload.write_bytes(payload)
    await_jobs("slow-upload-after-idle", [job("upload", "http", "/camouflage/slow-upload?ms=1", upload)], upload=payload)
    settle(worker)
    expected = direct("/camouflage/resource?size=524288")
    await_jobs("mixed-concurrent-after-idle", [job(f"parallel-{index}", "socks" if index % 2 == 0 else "http", "/camouflage/resource?size=524288") for index in range(4)], expected)
    settle(worker)
    idle_measurement = None
    if idle_seconds:
        print(json.dumps({"session_stage": "idle-wire", "seconds": idle_seconds}), flush=True)
        before = state(worker)
        with tempfile.TemporaryDirectory(prefix="naivefox-carrier-idle-") as temporary:
            pcap = Path(temporary) / "idle.pcapng"
            capture = launch(["dumpcap", "-q", "-i", "any", "-f", f"port {outer_port}", "-a", f"duration:{idle_seconds+5}", "-a", "filesize:32768", "-w", pcap], "idle-dumpcap.log")
            deadline = time.monotonic() + 5
            while "File:" not in (directory / "idle-dumpcap.log").read_text():
                if capture.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("idle capture startup")
                time.sleep(0.01)
            start = time.monotonic()
            try:
                while time.monotonic() - start < idle_seconds:
                    current = state(worker)
                    if current["phase"] != "idle" or current["dynamic"] != before["dynamic"] or current["wake"] != before["wake"]:
                        raise RuntimeError("unexpected activity inside idle measurement")
                    time.sleep(0.25)
            finally:
                if capture.poll() is None:
                    capture.send_signal(signal.SIGINT)
                capture.wait(timeout=10)
            elapsed = time.monotonic() - start
            validate_dumpcap_log((directory / "idle-dumpcap.log").read_text())
            destination = directory / "idle.pcapng"
            shutil.move(pcap, destination)
        events, _ = (features.packet_events_h2 if protocol == "h2" else features.packet_events_h3)(str(destination), outer_port)
        if protocol == "h3" and features.packet_events_h2(str(destination), outer_port)[0]:
            raise RuntimeError("TCP traffic in strict H3 idle capture")
        wire = sum(event["wire_size"] for event in events)
        idle_measurement = {"duration_seconds": round(elapsed, 3), "wire_bytes": wire, "packets": len(events), "extrapolated_wire_bytes_per_hour": round(wire * 3600 / elapsed), "application_poll_starts": state(worker)["idle"] - before["idle"], "quic_initial_packets": sum("0" in event.get("packet_types", []) for event in events)}
        print(json.dumps({"idle_measurement": idle_measurement}), flush=True)
    expected = direct("/camouflage/resource?size=4096")
    await_jobs("final-wake", [job("final-wake", "socks", "/camouflage/resource?size=4096")], expected)
    settle(worker, 0.25)
    return {"checks": checks, "idle_measurement": idle_measurement, "final_state": state(worker)}
