#!/usr/bin/env python3
"""Exercise transport CLI parsing and optional shared-config live switching."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def live_checks(binary, caddy, work_dir, protocols):
    import importlib.util
    import secrets

    spec = importlib.util.spec_from_file_location(
        "no_connect_fixture", Path(__file__).with_name("run-no-connect-tests.py"))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    previous_umask = os.umask(0o077)
    run = Path(tempfile.mkdtemp(prefix="transport-cli-live-", dir=work_dir))
    summaries = []
    try:
        for protocol in protocols:
            directory = run / protocol
            directory.mkdir(mode=0o700)
            fixture.issue_certificates(directory)
            target = fixture.TargetServer()
            processes = []
            try:
                key, user, password = (
                    secrets.token_hex(32), secrets.token_hex(8), secrets.token_hex(24))
                server_args = argparse.Namespace(caddy=caddy)
                server, proxy_port = fixture.start_caddy(
                    server_args, directory, protocol, target.server_address[1],
                    key, user, password)
                processes.append(server)
                ports = {"socks": fixture.free_port(), "http": fixture.free_port()}
                while ports["http"] == ports["socks"]:
                    ports["http"] = fixture.free_port()
                scheme = "quic" if protocol == "h3" else "https"
                config = {
                    "listen": [f"socks://127.0.0.1:{ports['socks']}",
                               f"http://127.0.0.1:{ports['http']}"],
                    "proxy": f"{scheme}://{user}:{password}@localhost:{proxy_port}",
                    "no-connect-key": key, "preamble": {"mode": "off"},
                    "host-resolver-rules": "MAP localhost 127.0.0.1",
                    "max-connections": 2, "log": "",
                }
                config_path = directory / "shared-config.json"
                fixture.private_json(config_path, config)
                original = config_path.read_bytes()
                classic_count = 0
                for name, options in (
                    ("default-classic", []),
                    ("no-connect", ["--transport", "no-connect"]),
                    ("override-classic", ["--transport=classic"]),
                ):
                    before = fixture.access_requests(directory)
                    temporary = directory / name
                    temporary.mkdir(mode=0o700)
                    env = {key: value for key, value in os.environ.items() if key not in {
                        "NAIVEFOX_PROFILE", "NAIVEFOX_PROXY_USER", "NAIVEFOX_PROXY_PASS",
                        "SSLKEYLOGFILE", "MOZ_RUN_GTEST", "MOZ_LOG", "MOZ_LOG_FILE",
                        "LD_PRELOAD", "SSL_CERT_FILE"}}
                    env.update(LD_LIBRARY_PATH=str(binary.parent), TMPDIR=str(temporary),
                               SSL_CERT_FILE=str(directory / "ca.crt"),
                               MOZ_CRASHREPORTER_DISABLE="1")
                    client = fixture.Process(
                        [str(binary), str(config_path), *options], directory, name, env)
                    processes.append(client)

                    def ready():
                        text = client.log_path.read_text(errors="replace")
                        return (
                            f"SOCKS5 listening on 127.0.0.1:{ports['socks']}" in text and
                            f"HTTP CONNECT listening on 127.0.0.1:{ports['http']}" in text)

                    fixture.wait_until(ready, "shared-config client did not start", client)
                    for listener in ("socks", "http"):
                        fixture.download(ports, listener, target.server_address[1], 65536)
                    client.exited_cleanly()
                    fixture.require(config_path.read_bytes() == original,
                                    "transport override changed the shared config")
                    output = client.log_path.read_text(errors="replace")
                    fixture.require(not any(value in output for value in (key, user, password)),
                                    "transport override leaked authentication")
                    if name != "no-connect":
                        classic_count += 2
                        fixture.wait_until(
                            lambda: sum(request.get("method") == "CONNECT" for request in
                                        fixture.access_requests(directory)) == classic_count,
                            "classic CLI selection did not traverse CONNECT")
                    else:
                        requests = fixture.access_requests(directory)[len(before):]
                        fixture.require(bool(requests), "no-connect CLI sent no HTTP requests")
                        fixture.require(not any(request.get("method") == "CONNECT"
                                                for request in requests),
                                        "no-connect CLI emitted an outer CONNECT")
                        fixture.require(not any(
                            key.lower() in {"authorization", "proxy-authorization"}
                            for request in requests for key in request.get("headers", {})),
                            "no-connect forwarded unused classic credentials")
                    print(f"PASS {protocol} shared-config CLI {name}: both listeners", flush=True)
                server.stop()
                fixture.require(not target.failures, "shared-config target stream failed")
                summaries.append({"protocol": protocol, "status": "PASS",
                                  "unchanged_config": True, "classic_connects": classic_count,
                                  "no_connect_outer_connects": 0,
                                  "unused_classic_auth_headers": 0})
            finally:
                for process in reversed(processes):
                    process.stop()
                target.close()
        fixture.private_json(run / "result.json", {"status": "PASS", "targets": summaries})
        print(f"Shared-config active CLI checks passed. Private fixture: {run}", flush=True)
    finally:
        os.umask(previous_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--caddy", type=Path,
                        help="also test one unchanged authenticated config over live H2/H3")
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for name in ("MOZ_RUN_GTEST", "SSLKEYLOGFILE", "MOZ_LOG", "MOZ_LOG_FILE"):
        env.pop(name, None)
    env["LD_LIBRARY_PATH"] = str(binary.parent)
    checks = 0
    secret = "0123456789abcdef0123456789abcdef"
    with tempfile.TemporaryDirectory(prefix="transport-cli-", dir=args.work_dir) as temporary:
        work = Path(temporary)
        blocked_log = work / "log-is-a-directory"
        blocked_log.mkdir()
        base = {
            "listen": "socks://127.0.0.1:1080",
            "proxy": "https://proxy.invalid:443",
            "log": str(blocked_log),
        }
        ready = "NaiveFox config error: cannot open runtime log file"
        cli_error = "NaiveFox command line error:"

        def check(name, arguments, config, expected, status=2):
            nonlocal checks
            for filename in ("config.json", "selected.json"):
                path = work / filename
                path.write_text(json.dumps(config), encoding="utf-8")
                path.chmod(0o600)
            result = subprocess.run(
                [str(binary), *arguments], cwd=work, env=env,
                text=True, capture_output=True, timeout=10,
            )
            output = result.stdout + result.stderr
            if result.returncode != status or expected not in output:
                raise AssertionError(f"{name}: unexpected status or diagnostic (status {result.returncode})")
            if any(value in output for value in (secret, "switch-user", "switch-password")) or "SOCKS5 listening" in output or "NaiveFox started" in output:
                raise AssertionError(f"{name}: leaked key or started the runtime")
            checks += 1

        check("default classic", [], base, ready)
        check("positional config", ["selected.json"], base, ready)
        check("classic option uses default path", ["--transport", "classic"], base, ready)
        check("help documents transport", ["--help"], base, "--transport classic|no-connect", 0)

        keyed = {**base, "no-connect-key": secret,
                 "proxy": "https://switch-user:switch-password@proxy.invalid"}
        for json_mode in (None, "classic", "no-connect"):
            config = dict(keyed)
            if json_mode:
                config["transport"] = json_mode
            for arguments in (
                ["--transport", "no-connect"],
                ["selected.json", "--transport", "no-connect"],
                ["--transport", "no-connect", "selected.json"],
                ["selected.json", "--transport=no-connect"],
                ["--transport=no-connect", "selected.json"],
            ):
                check(f"no-connect precedence {json_mode} {arguments}", arguments, config, ready)
        native = {**keyed, "transport": "no-connect"}
        check("JSON no-connect without override", ["selected.json"], native, ready)
        check("classic override retains unused key", ["--transport=classic", "selected.json"], native, ready)
        check("classic override does not require unused key", ["--transport", "classic"], {**base, "transport": "no-connect"}, ready)
        check("default classic retains unused valid key", [], keyed, ready)
        check("explicit classic retains unused valid key", [], {**keyed, "transport": "classic"}, ready)
        check("override requires private config key", ["--transport=no-connect"], base, "no-connect transport requires no-connect-key")
        check("no-connect accepts unused classic credentials", ["--transport=no-connect"], keyed, ready)
        check("override still rejects active preamble", ["--transport=no-connect"], {**keyed, "preamble": {"mode": "document-complete", "path": "/"}}, "no-connect transport does not accept classic preamble")
        check("override still validates unused key", ["--transport=classic"], {**native, "no-connect-key": "short"}, "no-connect-key must contain 32 through 1024")
        check("override does not hide invalid JSON transport", ["--transport=classic"], {**base, "transport": "invalid"}, "transport must be classic or no-connect")

        for arguments in (
            ["--transport"],
            ["selected.json", "--transport"],
            ["--transport="],
            ["--transport", "Classic"],
            ["--transport=unknown"],
            ["--transport", "--help"],
            ["--transport=classic", "--transport=classic"],
            ["--transport", "classic", "--transport", "no-connect"],
            ["selected.json", "other.json", "--transport=classic"],
            ["--transport=classic", ""],
            ["--transport=classic", "--profile", "profile", "--runtime-smoke"],
            ["--profile", "profile", "--runtime-smoke", "--transport=no-connect"],
            ["--transport=no-connect", "--no-connect-key", secret],
        ):
            check(f"invalid arguments {arguments[0]}", arguments, native, cli_error)
    print(f"Transport CLI checks passed: {checks}; no runtime or network startup")
    if args.caddy:
        protocols = ("h2", "h3") if args.protocol == "both" else (args.protocol,)
        live_checks(binary, args.caddy.resolve(strict=True), args.work_dir, protocols)


if __name__ == "__main__":
    main()
