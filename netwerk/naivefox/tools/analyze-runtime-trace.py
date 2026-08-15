#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
from pathlib import Path


QUOTED_PATH = re.compile(r'"((?:\\.|[^"\\])*)"')


def decode_path(value: str) -> str:
    return bytes(value, "utf-8").decode("unicode_escape")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("trace_directories", nargs="+", type=Path)
    args = parser.parse_args()

    runtime = args.runtime.resolve(strict=True)
    files: set[Path] = set()
    trace_count = 0
    for trace_directory in args.trace_directories:
        for trace in sorted(trace_directory.glob("trace*")):
            if not trace.is_file():
                continue
            trace_count += 1
            for line in trace.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if re.search(r"\)\s+=\s+-1(?:\s|$)", line):
                    continue
                for match in QUOTED_PATH.finditer(line):
                    candidate = Path(decode_path(match.group(1)))
                    if not candidate.is_absolute():
                        continue
                    try:
                        relative = candidate.resolve(strict=False).relative_to(runtime)
                    except ValueError:
                        continue
                    source = runtime / relative
                    if source.is_file():
                        files.add(relative)

    entries = []
    total_bytes = 0
    for relative in sorted(files, key=lambda path: path.as_posix()):
        source = runtime / relative
        size = source.stat().st_size
        total_bytes += size
        entries.append({
            "path": relative.as_posix(),
            "sha256": sha256(source),
            "size": size,
        })

    report = {
        "format_version": 1,
        "observed_file_count": len(entries),
        "observed_total_bytes": total_bytes,
        "trace_file_count": trace_count,
        "files": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
