#!/usr/bin/env python3

import argparse
import importlib.util
import hashlib
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

CASES = ("hello-missing", "hello-profile", "hello-site", "hello-stream",
         "hello-sequence", "hello-duplicate", "site-body-mismatch", "cookie-missing",
         "cookie-malformed", "append", "capacity", "truncated", "sequence",
         "reserved", "redirect", "auth-prompt", "protocol")


def fixture_identity(root):
    digest = hashlib.sha256()
    def field(value):
        digest.update(struct.pack("!Q", len(value)))
        digest.update(value)
    field(b"naivefox-site-v2")
    field(hashlib.sha256((root / "index.html").read_bytes()).digest())
    resources = [("/assets/site.css", "style", "text/css"),
                 ("/assets/app.js", "script", "text/javascript")]
    resources += [(f"/assets/image-{i}.svg", "image", "image/svg+xml") for i in range(1, 5)]
    for path, kind, mime in resources:
        for value in (path, kind, mime):
            field(value.encode())
        field(hashlib.sha256((root / path[1:]).read_bytes()).digest())
    return digest.hexdigest()


def mutation(case):
    def apply(server):
        if case == "protocol":
            return
        handlers = [item for route in server["routes"] for item in route.get("handle", [])]
        root = None
        while handlers:
            item = handlers.pop()
            if item.get("handler") == "naivefox_transport":
                root = Path(item["application_root"])
            for route in item.get("routes", []):
                handlers.extend(route.get("handle", []))
        if root is None:
            raise RuntimeError("fixture application root is missing")
        profile = "incompatible" if case == "hello-profile" else "native-stream-v2"
        identity = "0" * 64 if case == "hello-site" else fixture_identity(root)
        payload = (profile + "\n" + identity).encode()
        frame = struct.pack("!B3xIII", 9, int(case == "hello-stream"),
                            int(case == "hello-sequence"), len(payload)) + payload
        count = 1
        if case == "hello-missing":
            frame, count = b"", 0
        elif case == "hello-duplicate":
            frame, count = frame * 2, 2
        header = bytearray(b"NFC1" + struct.pack("!IIHH", int(case == "sequence"),
                                               16 + len(frame), count, 0))
        if case == "reserved":
            header[14] = 1
        capacity = 8191 if case == "capacity" else 8192
        body = bytes(header) + frame + bytes(capacity - 16 - len(frame))
        if case == "truncated":
            body = body[:100]
        elif case == "append":
            body += bytes(16)
        path = "/api/events/brief"
        response = {
            "handler": "static_response", "status_code": 200,
            "headers": {"Content-Type": ["application/octet-stream"],
                        "Content-Length": ["8192" if case == "truncated" else str(len(body))],
                        "Cache-Control": ["no-store"]},
            "body": body.decode("ascii") if case != "hello-duplicate" else "",
        }
        if case in ("cookie-missing", "cookie-malformed"):
            path = "/"
            html = (root / "index.html").read_text()
            response = {"handler": "static_response", "status_code": 200,
                        "headers": {"Content-Type": ["text/html"],
                                    "Content-Length": [str(len(html.encode()))]},
                        "body": html}
            if case == "cookie-malformed":
                response["headers"]["Set-Cookie"] = ["session=invalid; Path=/; Secure; HttpOnly"]
        elif case == "site-body-mismatch":
            path = "/assets/site.css"
            response = {"handler": "static_response", "status_code": 200,
                        "headers": {"Content-Type": ["text/css"], "Content-Length": ["7"]},
                        "body": "body {}"}
        elif case == "redirect":
            path = "/"
            response = {"handler": "static_response", "status_code": 302,
                        "headers": {"Location": ["/redirected"]}}
        elif case == "auth-prompt":
            path = "/"
            response = {"handler": "static_response", "status_code": 401,
                        "headers": {"WWW-Authenticate": ['Basic realm="fixture"']}}
        handles = [response]
        if case == "hello-duplicate":
            directory = root.parent / "adversarial-response"
            directory.mkdir(exist_ok=True)
            (directory / "cell.bin").write_bytes(body)
            handles = [{"handler": "rewrite", "uri": "/cell.bin"},
                       {"handler": "file_server", "root": str(directory)}]
        server["routes"].insert(0, {"match": [{"path": [path]}], "handle": handles})
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
        fixture.require(stats["opens"] == 0, "unconfirmed contract opened a target")
        posts = sum(item.get("method") == "POST" for item in requests)
        if case in ("cookie-missing", "cookie-malformed", "redirect", "auth-prompt", "protocol"):
            fixture.require(posts == 0, "public bootstrap rejection sent authentication")
        else:
            fixture.require(posts == 1, "contract rejection did not stop after AUTH")
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
