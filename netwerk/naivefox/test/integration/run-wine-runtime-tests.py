#!/usr/bin/env python3
"""Verify a relocated Windows package and its native carriers under Wine."""

import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import transport_fixture as fixture


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--caddy", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--wine-root", type=Path, default=Path.home() / ".mozbuild/wine")
    parser.add_argument("--wine-prefix", type=Path)
    args = parser.parse_args()
    args.objdir = args.objdir.resolve(strict=True)
    args.runtime = args.runtime.resolve(strict=True)
    args.caddy = args.caddy.resolve(strict=True)
    args.work_dir = args.work_dir.resolve()
    fixture.require(sys.platform == "linux", "Wine acceptance requires Linux")
    fixture.require(
        args.work_dir.is_relative_to(args.objdir) and not args.work_dir.exists(),
        "Wine artifacts require a new directory below the object directory",
    )
    wine_root = args.wine_root.resolve(strict=True)
    wine = wine_root / "bin/wine"
    winepath = wine_root / "bin/winepath"
    fixture.require(wine.is_file() and winepath.is_file(), "Wine tools are missing")
    os.umask(0o077)
    args.work_dir.mkdir(parents=True, mode=0o700)
    package = args.work_dir / "package"
    shutil.copytree(args.runtime.parent, package)
    tools = HERE.parent.parent / "tools"
    fixture.require(
        all((package / name).is_file() for name in (
            "naivefox.exe", "xul.dll", "mozglue.dll", "application.ini",
            "dependentlibs.list", "localization/en-US/netwerk/necko.ftl",
        )),
        "relocated Windows package is incomplete",
    )
    prefix = (args.wine_prefix or args.work_dir / "wine-prefix").resolve()
    library_paths = ":".join(
        str(wine_root / "lib/wine" / suffix)
        for suffix in ("x86_64-unix", "i386-unix", "x86_64-windows", "i386-windows")
    )
    wrapper = args.work_dir / "wine-naivefox.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + f"export WINEPREFIX={shlex.quote(str(prefix))}\n"
        + "export WINEARCH=win64 WINEDEBUG=-all\n"
        + f"export WINEDLLPATH={shlex.quote(library_paths)}\n"
        + f"winepath={shlex.quote(str(winepath))}\n"
        + 'for key in SSL_CERT_FILE NAIVEFOX_PROFILE; do\n'
        + '  if [[ -n ${!key:-} ]]; then export "$key=$("$winepath" -w "${!key}")"; fi\n'
        + 'done\nargs=("$@")\n'
        + 'if (( $# )) && [[ -f $1 ]]; then args[0]=$("$winepath" -w "$1"); fi\n'
        + f"exec {shlex.quote(str(wine))} {shlex.quote(str(package / args.runtime.name))} "
        + '"${args[@]}"\n'
    )
    wrapper.chmod(0o700)
    print(subprocess.check_output([str(wine), "--version"], text=True).strip(), flush=True)
    for script, work in (
        (tools / "runtime_smoke.py", "startup"),
        (HERE / "run-transport-cli-tests.py", "cli"),
    ):
        subprocess.run(
            [sys.executable, str(script), "--runtime", str(wrapper),
             "--work-dir", str(args.work_dir / work)],
            check=True,
        )
    spec = importlib.util.spec_from_file_location("wine_transport_tests", HERE / "run-transport-tests.py")
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    original_exit = fixture.Process.exited_cleanly

    def checked_exit(process):
        if str(process.process.args[0]) != str(wrapper):
            return original_exit(process)
        status = process.process.wait(timeout=20)
        fixture.require(status in (0, -15), f"Wine client exited with status {status}")
        return True

    fixture.Process.exited_cleanly = checked_exit
    try:
        output = args.work_dir / "network"
        output.mkdir(mode=0o700)
        native_args = SimpleNamespace(
            objdir=args.objdir, runtime=wrapper, caddy=args.caddy,
            cdn_proxy=None, application_root=None, output=output,
        )
        rows = [suite.run_protocol(native_args, protocol) for protocol in ("packet", "h2", "h3")]
        fixture.private_json(args.work_dir / "results.json", rows)
    finally:
        fixture.Process.exited_cleanly = original_exit
    print("PASS Windows package under Wine: startup, CLI, packet, WSS, strict H3", flush=True)


if __name__ == "__main__":
    main()
