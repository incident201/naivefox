#!/usr/bin/env python3

import argparse
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile


spec = importlib.util.spec_from_file_location(
    "no_connect_runtime", Path(__file__).with_name("run-no-connect-tests.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

CASES = ("profile", "auth-mode-missing", "auth-mode-legacy", "append", "capacity", "truncated", "sequence", "reserved", "redirect", "auth-prompt", "protocol")


def mutation(case):
    def apply(server):
        if case == "protocol":
            return
        header = bytearray(b"NFC1" + struct.pack("!IIHH", 0, 16, 0, 0))
        if case == "sequence":
            header[7] = 1
        if case == "reserved":
            header[14] = 1
        body = bytes(header) + bytes((100 if case == "truncated" else 8192) - 16)
        if case == "append":
            body += bytes(16)
        path = "/api/events/brief"
        response = {
            "handler": "static_response", "status_code": 200,
            "headers": {
                "Content-Type": ["application/octet-stream"],
                "Content-Length": [str(len(body)) if case == "append" else "8192"],
                "X-App-Capacity": ["8191" if case == "capacity" else "8192"],
                "X-App-State": ["idle"], "Cache-Control": ["no-store"],
            },
            "body": body.decode("ascii"),
        }
        if case in ("profile", "auth-mode-missing", "auth-mode-legacy"):
            path = "/"
            response = {"handler": "static_response", "status_code": 200,
                        "headers": {"Content-Type": ["text/html"],
                                    "Content-Length": ["4096"],
                                    "X-App-Profile": ["incompatible" if case == "profile" else "native-stream-v1"],
                                    "X-App-Realtime": ["websocket-v1"],
                                    "Set-Cookie": ["app_session=" + "0" * 64 + "; Path=/; Secure; HttpOnly"]},
                        "body": "x" * 4096}
            if case == "profile":
                response["headers"]["X-App-Auth"] = ["basic"]
            if case == "auth-mode-legacy":
                response["headers"]["X-App-Auth"] = ["key"]
        elif case == "redirect":
            path = "/"
            response = {"handler": "static_response", "status_code": 302,
                        "headers": {"Location": ["/redirected"]}}
        elif case == "auth-prompt":
            path = "/"
            response = {"handler": "static_response", "status_code": 401,
                        "headers": {"WWW-Authenticate": ['Basic realm="fixture"']}}
        server["routes"].insert(0, {"match": [{"path": [path]}], "handle": [response]})
    return apply


def run_case(args, base, protocol, case):
    run = base / f"{protocol}-{case}"
    run.mkdir(mode=0o700)
    fixture.issue_certificates(run)
    target = fixture.TargetServer()
    processes = []
    try:
        user, password = fixture.fixture_credentials()
        args.server_mutator = mutation(case)
        caddy, port = fixture.start_caddy(args, run, protocol, target.server_address[1], user, password)
        processes.append(caddy)
        client_protocol = ("h3" if protocol == "h2" else "h2") if case == "protocol" else protocol
        client, ports = fixture.start_client(args, run, "client", client_protocol, port,
                                            "no-connect", user, password, 1)
        processes.append(client)
        fixture.open_tunnel(ports, "socks", target.server_address[1], rejected=True)
        client.exited_cleanly()
        caddy.stop()
        stats = json.loads((run / "server-stats.json").read_text())
        fixture.require(stats["connect"] == 0, "adversarial response triggered outer CONNECT fallback")
        requests = fixture.access_requests(run)
        if case == "redirect":
            fixture.require(not any(item.get("uri") == "/redirected" for item in requests),
                            "native client followed an origin redirect")
        if case in ("profile", "auth-mode-missing", "auth-mode-legacy", "redirect", "auth-prompt", "protocol"):
            fixture.require(stats["opens"] == 0, "bootstrap rejection still opened a target")
            fixture.require(not any(name.startswith("POST ") for name in stats["requests"]),
                            "bootstrap rejection still sent application authentication")
        if case == "protocol":
            if client_protocol == "h3":
                fixture.require(not stats["requests"], "H3 attempted an HTTP fallback")
            else:
                fixture.require(len(requests) <= 1 and
                                all(item.get("method") == "GET" and item.get("uri") == "/" and
                                    item.get("proto") == "HTTP/1.1" for item in requests),
                                "H2 continued after refusing the root negotiation protocol")
        if case == "auth-prompt":
            fixture.require(sum(item.get("uri") == "/" for item in requests) == 1,
                            "unexpected authentication retry")
        if case == "truncated":
            entries = [json.loads(line) for line in (run / "access.jsonl").read_text().splitlines()]
            broken = [entry for entry in entries if entry.get("request", {}).get("uri") == "/api/events/brief"]
            fixture.require(bool(broken) and broken[-1]["size"] == 100 and
                            broken[-1].get("resp_headers", {}).get("Content-Length") == ["8192"],
                            "fixture did not emit the intended truncated response")
        result = {"protocol": protocol, "case": case, "status": "PASS", "outer_connects": 0}
        fixture.private_json(run / "result.json", result)
        print(f"PASS {protocol}: reject {case}", flush=True)
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


def main():
    parser = argparse.ArgumentParser(description="Fail-closed native no-connect HTTP envelope and protocol checks.")
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(strict=True)
    root = (args.work_dir or args.objdir / "naivefox-fixture").resolve()
    fixture.require(root.is_relative_to(args.objdir), "work directory must stay below objdir")
    root.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    run = Path(tempfile.mkdtemp(prefix="no-connect-adversarial-", dir=root))
    try:
        results = [run_case(args, run, protocol, case)
                   for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,))
                   for case in (args.case or CASES)]
        fixture.private_json(run / "result.json", {"status": "PASS", "cases": results})
        print(f"Private fixture and sanitized result: {run}")
    except (OSError, RuntimeError) as error:
        print(f"FAIL: {error}. Private diagnostics: {run}", flush=True)
        return 1
    finally:
        os.umask(previous_umask)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
