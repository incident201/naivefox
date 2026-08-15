#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path


NEEDED = re.compile(r"\(NEEDED\).*\[([^]]+)\]")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objdir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    objdir = args.objdir.resolve(strict=True)
    binary = objdir / "dist/bin/naivefox"
    libxul = objdir / "dist/bin/libxul.so"
    link_list = objdir / "toolkit/library/build/libxul_so.list"
    for required in (binary, libxul, link_list):
        if not required.is_file():
            raise SystemExit(f"required build output is missing: {required}")

    link_root = link_list.parent
    inputs = []
    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"bytes": 0, "files": 0})
    for line in link_list.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("-"):
            continue
        path = (link_root / value).resolve(strict=False)
        if not path.is_file():
            continue
        relative = path.relative_to(objdir).as_posix()
        size = path.stat().st_size
        inputs.append({"path": relative, "size": size})
        group = relative.split("/", 1)[0]
        groups[group]["bytes"] += size
        groups[group]["files"] += 1

    dynamic = subprocess.run(
        ["readelf", "-d", str(libxul)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    needed = sorted(
        match.group(1)
        for line in dynamic.splitlines()
        if (match := NEEDED.search(line))
    )

    report = {
        "format_version": 1,
        "libxul": {
            "bytes": libxul.stat().st_size,
            "needed": needed,
            "sha256": sha256(libxul),
        },
        "link_inputs": {
            "count": len(inputs),
            "groups": [
                {"group": group, **values}
                for group, values in sorted(
                    groups.items(), key=lambda item: (-item[1]["bytes"], item[0])
                )
            ],
            "total_bytes": sum(item["size"] for item in inputs),
        },
        "naivefox": {
            "bytes": binary.stat().st_size,
            "sha256": sha256(binary),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
