#!/usr/bin/env python3
"""Check the single product CLI and strict configuration rejection."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve(strict=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cli-", dir=args.work_dir) as directory:
        root = Path(directory)
        env = dict(
            os.environ,
            LD_LIBRARY_PATH=str(runtime.parent),
            TMPDIR=str(root),
            XDG_RUNTIME_DIR=str(root),
        )
        for key in ("MOZ_LOG", "MOZ_LOG_FILE", "SSLKEYLOGFILE", "NAIVEFOX_PROFILE"):
            env.pop(key, None)

        def run(arguments, expected):
            result = subprocess.run(
                [str(runtime), *arguments],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=5,
            )
            if result.returncode != expected:
                raise RuntimeError("unexpected CLI result for " + repr(arguments))
            return result.stdout + result.stderr

        help_text = run(["--help"], 0)
        if "--transport" in help_text or "--runtime-smoke" in help_text:
            raise RuntimeError("retired CLI remains advertised")
        if not run(["--version"], 0).strip():
            raise RuntimeError("version output missing")
        for arguments in (
            ["--unknown"],
            ["--transport", "naivefox"],
            ["--runtime-smoke"],
            ["one", "two"],
            ["--version", "extra"],
        ):
            run(arguments, 2)
        valid = {
            "listen": "socks://127.0.0.1:1080",
            "proxy": "https://fixture:fixture@localhost:443",
        }
        invalid = [
            "{",
            "[]",
            "{}",
            '{"listen":true,"proxy":"https://localhost"}',
            '{"listen":"socks://127.0.0.1:1080","listen":"socks://127.0.0.1:1081","proxy":"https://localhost"}',
            json.dumps({**valid, "proxy": "auto://localhost"}),
            json.dumps({**valid, "unknown": True}),
            json.dumps({**valid, "transport": "naivefox"}),
            json.dumps({**valid, "preamble": {"mode": "off"}}),
            json.dumps({**valid, "extra-headers": "X-Test: retired"}),
            json.dumps({**valid, "max-connections": -1}),
            json.dumps({**valid, "no-post-quantum": "true"}),
            " " * (1024 * 1024 + 1),
        ]
        for value in invalid:
            (root / "config.json").write_text(value)
            run([], 2)
            run([str(root / "config.json")], 2)
    print("PASS: current CLI, default config path, strict fields and bounds")


if __name__ == "__main__":
    main()
