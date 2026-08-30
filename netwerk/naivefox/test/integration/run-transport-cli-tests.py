#!/usr/bin/env python3
"""Exercise transport CLI precedence before runtime/network startup."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
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
            if secret in output or "SOCKS5 listening" in output or "NaiveFox started" in output:
                raise AssertionError(f"{name}: leaked key or started the runtime")
            checks += 1

        check("default classic", [], base, ready)
        check("positional config", ["selected.json"], base, ready)
        check("classic option uses default path", ["--transport", "classic"], base, ready)
        check("help documents transport", ["--help"], base, "--transport classic|no-connect", 0)

        keyed = {**base, "no-connect-key": secret}
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
        check("classic override discards retained key", ["--transport=classic", "selected.json"], native, ready)
        check("classic override does not require unused key", ["--transport", "classic"], {**base, "transport": "no-connect"}, ready)
        check("classic JSON rejects accidental key", [], keyed, "no-connect-key requires no-connect transport")
        check("override requires private config key", ["--transport=no-connect"], base, "no-connect transport requires no-connect-key")
        check("override still rejects upstream credentials", ["--transport=no-connect"], {**keyed, "proxy": "https://user:password@proxy.invalid"}, "no-connect transport does not accept proxy credentials")
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


if __name__ == "__main__":
    main()
