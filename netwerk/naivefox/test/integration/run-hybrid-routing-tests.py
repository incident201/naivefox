#!/usr/bin/env python3
"""Verify native hybrid routing without DNS for the logical TLS authority."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("hybrid_routing_fixture", HERE / "run-no-connect-tests.py")
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
LOGICAL_HOST = "carrier.invalid"


def issue_mapped_certificate(run):
    fixture.issue_certificates(run)
    fixture.openssl(
        run, "req", "-new", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-keyout", run / "server.key", "-out", run / "server.csr",
        "-subj", "/CN=" + LOGICAL_HOST,
    )
    (run / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:" + LOGICAL_HOST + "\n"
    )
    fixture.openssl(
        run, "x509", "-req", "-sha256", "-days", "2",
        "-in", run / "server.csr", "-CA", run / "ca.crt",
        "-CAkey", run / "ca.key", "-CAserial", run / "ca.srl",
        "-out", run / "server.crt", "-extfile", run / "server.ext",
    )


def run_protocol(args, protocol):
    run = args.work_dir / protocol
    run.mkdir(mode=0o700)
    target = fixture.TargetServer()
    processes = []
    result = {"protocol": protocol, "logical_host": LOGICAL_HOST,
              "mapped_host": "127.0.0.1", "certificate_has_physical_ip_san": False,
              "status": "FAIL"}
    try:
        issue_mapped_certificate(run)
        user, password = fixture.fixture_credentials()
        args.forward_proxy_ports = (target.server_address[1],)
        caddy, proxy_port = fixture.start_caddy(
            args, run, protocol, target.server_address[1], user, password)
        processes.append(caddy)
        ports = {"socks": fixture.free_port(), "http": fixture.free_port()}
        while ports["socks"] == ports["http"]:
            ports["http"] = fixture.free_port()
        args.listener_ports = ports
        config = fixture.client_config(
            protocol, proxy_port, args.transport, user, password, ports, 4)
        config["proxy"] = config["proxy"].replace(
            "@localhost:", "@" + LOGICAL_HOST + ":")
        config["host-resolver-rules"] = "MAP " + LOGICAL_HOST + " 127.0.0.1"
        args.base_client_config = config
        client, _ = fixture.start_client(
            args, run, "client", protocol, proxy_port, args.transport,
            user, password, 4)
        processes.append(client)
        for listener in ("socks", "http"):
            fixture.download(ports, listener, target.server_address[1], 2 * 1024 * 1024)
            fixture.upload(ports, listener, target.server_address[1], 1024 * 1024)
        fixture.require(
            "No-connect hybrid websocket ready startup=20" in client.log_path.read_text(),
            "mapped WSS did not reach the application milestone")
        client.exited_cleanly()
        caddy.stop()
        stats = json.loads((run / "server-stats.json").read_text())
        startup_protocol = "HTTP/2.0" if protocol == "h2" else "HTTP/3.0"
        fixture.require(stats.get("protocols") == {startup_protocol: 47, "HTTP/1.1": 1},
                        "mapped carrier negotiated an unexpected route")
        fixture.require(stats.get("ws_opened") == 1 and stats.get("ws_closed") == 1,
                        "mapped carrier did not preserve one WebSocket")
        fixture.require(stats.get("ws_startup_min_up") == 20 and
                        stats.get("ws_startup_min_down") == 20,
                        "mapped WebSocket opened before startup completion")
        fixture.require(stats.get("ws_messages_in", 0) > 0 and
                        stats.get("ws_messages_out", 0) > 0,
                        "mapped WebSocket did not carry bidirectional cells")
        fixture.require(stats.get("connect") == 0 and stats.get("rejected") == 0,
                        "mapped carrier leaked CONNECT or rejected valid traffic")
        fixture.require(not target.failures, "mapped carrier truncated target data")
        requests = fixture.access_requests(run)
        fixture.require(requests and all(
            request.get("host") == f"{LOGICAL_HOST}:{proxy_port}" for request in requests),
            "physical address replaced the logical HTTP authority")
        result.update(status="PASS", frontends=["socks", "http"],
                      download_bytes_per_frontend=2 * 1024 * 1024,
                      upload_bytes_per_frontend=1024 * 1024,
                      ws_opened=1, startup_exchanges=20, logical_authority_preserved=True)
        print(f"PASS {protocol}: nonresolving logical authority, mapped WSS, both frontends, byte-exact transfer", flush=True)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()
        fixture.private_json(run / "result.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.work_dir = args.work_dir.resolve()
    fixture.require(args.work_dir.is_relative_to(args.objdir / "hybrid-ws") and
                    not args.work_dir.exists(),
                    "routing artifacts require a new directory below the objdir hybrid-ws subtree")
    os.umask(0o077)
    args.work_dir.mkdir(parents=True, mode=0o700)
    args.transport = "no-connect-hybrid"
    rows = [run_protocol(args, protocol) for protocol in ("h2", "h3")]
    fixture.private_json(args.work_dir / "results.json", rows)


if __name__ == "__main__":
    main()
