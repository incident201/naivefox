#!/usr/bin/env python3
"""Bind a Firefox runtime to an official Taskcluster artifact and Git/Hg routes."""

import argparse
import configparser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request


def digest(source, algorithm="sha256"):
    return hashlib.file_digest(source, algorithm).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--git-base", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--firefox", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", args.task) or not re.fullmatch(r"[0-9a-f]{40}", args.git_base):
        parser.error("invalid immutable task or Git revision")
    task_url = f"https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{args.task}"
    artifact_base = f"https://firefoxci.taskcluster-artifacts.net/{args.task}/0/public/build/"
    task = json.load(urllib.request.urlopen(task_url, timeout=30))
    checksums = urllib.request.urlopen(artifact_base + "target.checksums", timeout=30).read().decode()
    metadata = json.load(urllib.request.urlopen(artifact_base + "target.json", timeout=30))
    hg = metadata["moz_source_stamp"]
    routes = [f"index.gecko.v2.mozilla-central.revision.{revision}.firefox.linux64-opt" for revision in (args.git_base, hg)]
    if not all(route in task.get("routes", []) for route in routes) or task["payload"]["env"]["GECKO_HEAD_REV"] != hg:
        raise SystemExit("official task does not bind the requested Git and Hg revisions")
    rows = [line.split() for line in checksums.splitlines()]
    matches = [row for row in rows if len(row) == 4 and row[1] == "sha512" and row[3] == "target.tar.xz"]
    if len(matches) != 1:
        raise SystemExit("missing unique official archive checksum")
    checksum = matches[0]
    with args.archive.open("rb") as source:
        archive_sha512 = digest(source, "sha512")
    if archive_sha512 != checksum[0] or args.archive.stat().st_size != int(checksum[2]):
        raise SystemExit("archive differs from the official artifact")
    runtime = args.firefox.resolve(strict=True).parent
    hashes = {}
    with tarfile.open(args.archive, mode="r|xz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = PurePosixPath(member.name)
            if not name.parts or name.parts[0] != "firefox" or ".." in name.parts:
                raise SystemExit("unexpected archive member")
            relative = Path(*name.parts[1:])
            actual = runtime / relative
            if not actual.resolve(strict=True).is_relative_to(runtime):
                raise SystemExit("runtime member escaped its package")
            expected = digest(archive.extractfile(member))
            with actual.open("rb") as source:
                observed = digest(source)
            if expected != observed:
                raise SystemExit("runtime member differs from official archive: " + str(relative))
            hashes[relative.as_posix()] = expected
    if not {"firefox", "firefox-bin", "libxul.so", "libnss3.so", "libssl3.so", "application.ini"}.issubset(hashes):
        raise SystemExit("runtime proof lacks essential files")
    application = configparser.ConfigParser()
    application.read(runtime / "application.ini")
    if application["App"]["SourceStamp"] != hg or application["App"]["BuildID"] != metadata["buildid"]:
        raise SystemExit("runtime source stamp differs from official task metadata")
    proof = {"schema_version": 1, "task_id": args.task, "git_base": args.git_base,
             "hg_revision": hg, "build_id": metadata["buildid"], "task_url": task_url,
             "revision_routes": routes, "artifact_url": artifact_base + "target.tar.xz",
             "checksums_url": artifact_base + "target.checksums", "archive_sha512": archive_sha512,
             "archive_bytes": args.archive.stat().st_size, "runtime_files_sha256": hashes}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"proof": str(args.output), "verified_runtime_files": len(hashes), "git_base": args.git_base, "hg_revision": hg}))


if __name__ == "__main__":
    main()
