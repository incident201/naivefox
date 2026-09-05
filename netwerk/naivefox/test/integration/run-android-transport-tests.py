#!/usr/bin/env python3
"""Exercise the public embedded transport argument on an ARM64 device."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("android_transport_fixture", HERE / "run-no-connect-android.py")
android = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(android)
suite = android.suite


def selection_cases():
    return (
        ("default-classic", None, None, "classic"),
        ("argument-no-connect", None, "no-connect", "no-connect"),
        ("argument-classic", None, "classic", "classic"),
        ("json-no-connect", "no-connect", None, "no-connect"),
        ("override-json-classic", "no-connect", "classic", "classic"),
        ("override-json-no-connect", "classic", "no-connect", "no-connect"),
    )


def check_requests(requests, transport, protocol):
    connects = [request for request in requests if request.get("method") == "CONNECT"]
    websocket = [request for request in requests if request.get("uri", "").split("?", 1)[0] == "/api/realtime"]
    expected_protocol = "HTTP/3.0" if protocol == "h3" else "HTTP/2.0"
    if transport == "classic":
        suite.require(len(connects) == 6 and not websocket,
                      "classic embedded argument did not select six outer CONNECTs")
    else:
        suite.require(requests and not connects,
                      "application embedded argument emitted CONNECT or sent no requests")
        suite.require(any(request.get("method") == "POST" for request in requests),
                      "application embedded argument did not send an API carrier")
        suite.require(not any(name.lower() in {"authorization", "proxy-authorization", "x-classic-only"}
                              for request in requests for name in request.get("headers", {})),
                      "application transport emitted classic-only headers")
        suite.require(bool(websocket) == (transport == "no-connect"),
                      "embedded argument selected the wrong realtime lifecycle")
    for request in requests:
        expected = "HTTP/1.1" if request in websocket else expected_protocol
        suite.require(request.get("proto") == expected,
                      "embedded selection negotiated an unexpected outer protocol")


def run_protocol(args, fixture, work, protocol):
    directory = work / protocol
    directory.mkdir(mode=0o700)
    suite.issue_certificates(directory)
    target = suite.TargetServer()
    user, password = suite.fixture_credentials()
    server = None
    summaries = []
    frozen = {}
    ports = {"socks": 1, "http": 2}
    try:
        server_args = SimpleNamespace(caddy=args.caddy,
                                      transport="no-connect")
        server, proxy_port = suite.start_caddy(server_args, directory, protocol,
                                              target.server_address[1], user, password)
        for index, (name, json_transport, override, expected) in enumerate(selection_cases()):
            phase = directory / name
            phase.mkdir(mode=0o700)
            config = {
                "listen": [],
                "proxy": suite.proxy_uri(protocol, proxy_port, user, password),
                "preamble": {"mode": "document-complete", "path": "/"},
                "extra-headers": "X-Classic-Only: embedded-argument\r\n",
                "outer-session-gate": True,
                "max-connections": 0,
                "log": "",
            }
            if json_transport is not None:
                config["transport"] = json_transport
            config_path = directory / ("json-" + (json_transport or "default") + ".json")
            rejected = ("", "unknown", "Classic", "no-connect ", "no-connect-hybrid", "no-connect-hybrid-asymmetric") if index == 0 else ()
            inputs = SimpleNamespace(client_config_path=config_path, preserve_client_config=True,
                                     transport_override=override, rejected_transports=rejected)
            if index:
                inputs.listener_ports = dict(ports)
            previous = len(suite.access_requests(directory))
            environment = {"SSL_CERT_FILE": str(directory / "ca.crt")}
            client, ports = fixture.start(inputs, phase, config, environment, dict(ports))
            try:
                actual = config_path.read_bytes()
                if config_path not in frozen:
                    frozen[config_path] = actual
                suite.require(actual == frozen[config_path] == client.executed_config_bytes(),
                              "embedded selection did not consume the unchanged shared JSON bytes")
                fixture.probe(ports, "socks", target.server_address[1], "download", 65536)
                if expected == "no-connect":
                    suite.wait_until(lambda: "No-connect websocket ready startup=20" in
                                     client.log_path.read_text(errors="replace"),
                                     "embedded no-connect never completed its WebSocket milestone", client, timeout=60)
                fixture.probe(ports, "socks", target.server_address[1], "upload", 65536)
                fixture.probe(ports, "http", target.server_address[1], "download", 65536)
                fixture.probe(ports, "http", target.server_address[1], "upload", 65536)
                for listener in ("socks", "http"):
                    fixture.probe(ports, listener, target.server_address[1], "idle", 50)
                client.stop()
                client.exited_cleanly()
                values = client.result_values()
                suite.require(values.get("status") == "0" and values.get("stop_requested") == "1",
                              "embedded transport selection did not stop cleanly across threads")
                suite.require(values.get("rejected_transports") == str(len(rejected)),
                              "invalid embedded selectors were not rejected before the successful run")
                suite.require(config_path.read_bytes() == frozen[config_path] == client.executed_config_bytes(),
                              "embedded transport run changed its shared JSON configuration")
                output = client.log_path.read_text(errors="replace")
                suite.require(user not in output and password not in output,
                              "embedded selector test leaked credentials")
                requests = suite.access_requests(directory)[previous:]
                check_requests(requests, expected, protocol)
                summaries.append({"case": name, "json_transport": json_transport,
                                  "argument": override, "selected": expected, "status": "PASS",
                                  "unchanged_json_bytes": True, "transfers": 6,
                                  "download_bytes": 131072, "upload_bytes": 131072,
                                  "idle_echo_bytes_per_direction": 16384,
                                  "rejected_selectors_before_valid_run": len(rejected)})
                print(f"PASS Android {protocol} embedded argument {name}: both listeners", flush=True)
            finally:
                client.stop()
        server.stop()
        stats = json.loads((directory / "server-stats.json").read_text())
        suite.require(stats.get("ws_opened", 0) >= 2 and stats.get("ws_messages_in", 0) >= 2 and
                      stats.get("ws_messages_out", 0) >= 2,
                      "embedded no-connect selections did not exchange bidirectional WebSocket cells")
        suite.require(stats.get("ws_subprotocols") ==
                      {"nfc1.stream.v1": 3},
                      "embedded selectors did not use the current WS protocol")
        suite.require(not target.failures and target.accepted_connections == len(summaries) * 6,
                      "embedded selector workloads did not complete exactly once")
        result = {"protocol": protocol, "status": "PASS", "cases": summaries,
                  "same_listener_ports": True, "shared_json_files": len(frozen),
                  "hybrid_tcp_explicit": True, "ws_opened": stats["ws_opened"]}
        suite.private_json(directory / "result.json", result)
        return result
    finally:
        if server:
            server.stop()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("objdir", "package", "caddy", "ndk", "work-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--adb", type=Path, default=Path("/usr/bin/adb"))
    parser.add_argument("--serial")
    parser.add_argument("--host-alias", default="10.0.2.2")
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    args = parser.parse_args()
    for name in ("objdir", "package", "caddy", "ndk", "adb"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    adb = [str(args.adb)] + (["-s", args.serial] if args.serial else [])
    abi = subprocess.check_output(adb + ["shell", "getprop", "ro.product.cpu.abi"], text=True).strip()
    api = subprocess.check_output(adb + ["shell", "getprop", "ro.build.version.sdk"], text=True).strip()
    suite.require(abi == "arm64-v8a" and api.isdecimal() and int(api) >= 26,
                  "embedded transport gate requires an online ARM64 API-26+ device")
    previous_umask = os.umask(0o077)
    work = Path(tempfile.mkdtemp(prefix="embedded-transport-", dir=args.work_dir))
    fixture = None
    try:
        fixture = android.AndroidFixture(args, work)
        protocols = ("h2", "h3") if args.protocol == "both" else (args.protocol,)
        results = [run_protocol(args, fixture, work, protocol) for protocol in protocols]
        fixture.close()
        fixture = None
        suite.private_json(work / "result.json", {"platform": "android-arm64", "status": "PASS", "targets": results})
        print(f"PASS Android embedded transport argument: {work}", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}. Private diagnostics: {work}", flush=True)
        return 1
    finally:
        if fixture:
            fixture.close()
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
