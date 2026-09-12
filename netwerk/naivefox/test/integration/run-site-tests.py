#!/usr/bin/env python3
"""Exercise HTML-derived NaiveFox startup against the actual Caddy binary."""

import argparse
import collections
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

spec = importlib.util.spec_from_file_location(
    "site_fixture", Path(__file__).with_name("transport_fixture.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def write_site(root, case):
    root.mkdir()
    if case == "root-only":
        (root / "index.html").write_text("<!doctype html><p>Standalone site</p>")
        return []
    (root / "css").mkdir()
    (root / "js").mkdir()
    (root / "pics").mkdir()
    # More than sixteen resources, a root above 16 KiB, and public files above
    # 64 KiB must work without admitting CSS/JS-discovered secondary requests.
    paths = []
    html = "<!doctype html><html><head><meta charset=utf-8>"
    html += "<!--" + " page content " * 1800 + "-->"
    for i in range(20):
        path = "/js/code-" + str(i) + ".js"
        (root / path[1:]).write_bytes(b"/* public resource */\n" * 8192)
        request_path = path + ("?version=" + "v" * 3072 if i == 0 else "")
        html += "<script defer src='" + request_path + "'></script>"
        paths.append(request_path)
    (root / "css/main.css").write_text(
        "@import url('/not-requested.css'); body{background:url('/large.png')}"
    )
    html += "<link rel=stylesheet href=/css/main.css>"
    paths.append("/css/main.css")
    (root / "pics/icon.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    )
    html += "<link rel=icon href=/pics/icon.svg><link rel=preload as=image href='/pics/icon.svg#one'>"
    paths.append("/pics/icon.svg")
    html += (
        "</head><body><template><img src=/inert.png></template><img src=/pics/icon.svg>"
    )
    html += "<a href=/unvisited.html>Next</a></body></html>"
    (root / "index.html").write_text(html)
    return paths


def run_case(args, base, protocol, case, supplied=None):
    run = base / (protocol + "-" + case)
    run.mkdir()
    inputs = copy.copy(args)
    if supplied:
        inputs.application_root = supplied
        # Expected inventory is explicitly derived from the provided reference.
        expected = [
            "/favicon.ico",
            "/assets/site.css",
            "/assets/css/world.css",
            "/assets/image-1.svg",
            "/assets/image-2.svg",
            "/assets/image-3.svg",
            "/assets/image-4.svg",
            "/assets/app.js",
            "/assets/js/site.js",
        ]
    else:
        inputs.application_root = run / "site"
        expected = write_site(inputs.application_root, case)
    fixture.issue_certificates(run)
    target = fixture.TargetServer()
    processes = []
    try:
        user, password = fixture.fixture_credentials()
        caddy, port = fixture.start_caddy(
            inputs, run, protocol, target.server_address[1], user, password
        )
        processes.append(caddy)
        # Separate new processes must repeat the complete startup; no cache or
        # inter-carrier sharing is allowed. Warm local streams reuse a carrier.
        for iteration in range(2):
            client, ports = fixture.start_client(
                inputs,
                run,
                "client-" + str(iteration),
                protocol,
                port,
                user,
                password,
                2,
            )
            processes.append(client)
            fixture.download(ports, "socks", target.server_address[1], 32768)
            fixture.wait_until(
                lambda: (
                    (
                        "NaiveFox HTTP/3 stream ready startup=20"
                        if protocol == "h3"
                        else "NaiveFox websocket ready startup=20"
                    )
                    in client.log_path.read_text(errors="replace")
                ),
                "site bootstrap did not reach persistent carrier",
                client,
                timeout=60,
            )
            fixture.download(ports, "http", target.server_address[1], 32768)
            client.exited_cleanly()
        caddy.stop()
        records = [
            json.loads(line) for line in (run / "access.jsonl").read_text().splitlines()
        ]
        requests = [record["request"] for record in records]
        counts = collections.Counter((item["method"], item["uri"]) for item in requests)
        fixture.require(counts[("GET", "/")] == 2, "cold roots did not repeat")
        for path in expected:
            fixture.require(
                counts[("GET", path)] == 2,
                "selected resource was omitted or duplicated",
            )
        allowed = {
            "/",
            *expected,
            "/api/sync",
            "/api/events/brief",
            "/api/events/state",
            "/api/realtime",
            "/api/stream",
            "/api/upload",
        }
        allowed.update("/media/chunk/" + str(i) for i in range(6, 18))
        fixture.require(
            all(
                item["uri"] in allowed and item["method"] != "CONNECT"
                for item in requests
            ),
            "unexpected secondary load, navigation or CONNECT",
        )
        for record in records:
            path = record["request"]["uri"]
            fixture.require(
                not any(
                    name.lower().startswith("x-app-")
                    for name in record.get("resp_headers", {})
                ),
                "public transport metadata leaked",
            )
            if path in {"/", *expected}:
                source = inputs.application_root / (
                    "index.html" if path == "/" else urlsplit(path).path[1:]
                )
                fixture.require(
                    record["status"] == 200 and record["size"] == source.stat().st_size,
                    "public source was padded, truncated or incomplete",
                )
        stats = json.loads((run / "server-stats.json").read_text())
        fixture.require(
            stats.get("startup_completed") == 2 and stats.get("ws_opened") == 2,
            "wrong complete HTTP/WS lifecycle",
        )
        fixture.require(
            not target.failures and target.accepted_connections == 4,
            "target bytes or stream lifecycle failed",
        )
        result = {
            "protocol": protocol,
            "case": case,
            "resources": len(expected),
            "cold_carriers": 2,
            "body_bytes_per_carrier": sum(
                (
                    inputs.application_root
                    / ("index.html" if p == "/" else urlsplit(p).path[1:])
                )
                .stat()
                .st_size
                for p in ["/", *expected]
            ),
            "cache_disabled": True,
            "recursive_loads": 0,
            "status": "PASS",
        }
        fixture.private_json(run / "result.json", result)
        print(
            "PASS",
            protocol,
            case,
            "resources",
            len(expected),
            "two cold starts, both listeners",
            flush=True,
        )
        return result
    finally:
        for process in reversed(processes):
            process.stop()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--astral-site",
        type=Path,
        help="Optional private, extracted Astral Oath reference",
    )
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.runtime = (args.runtime or args.objdir / "dist/bin/naivefox").resolve(
        strict=True
    )
    parent = (args.work_dir or args.objdir / "transport-tests").resolve()
    fixture.require(
        parent.is_relative_to(args.objdir),
        "work must remain below the object directory",
    )
    parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    run = Path(tempfile.mkdtemp(prefix="site-v2-", dir=parent))
    try:
        results = []
        for protocol in ("h2", "h3") if args.protocol == "both" else (args.protocol,):
            for case in ("root-only", "large-inventory"):
                results.append(run_case(args, run, protocol, case))
            if args.astral_site:
                results.append(
                    run_case(
                        args,
                        run,
                        protocol,
                        "astral",
                        args.astral_site.resolve(strict=True),
                    )
                )
        fixture.private_json(run / "result.json", {"status": "PASS", "cases": results})
        print("Private results:", run)
        return 0
    except (OSError, RuntimeError) as error:
        print("FAIL:", error, "Private diagnostics:", run)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
