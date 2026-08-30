#!/usr/bin/env python3
import hashlib
import argparse
import json
import shutil
import subprocess
from pathlib import Path

from costs import summarize


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main(root, name, profile):
    if Path(name).name != name or name in (".", ".."):
        raise ValueError("snapshot name must be one directory component")
    evidence = root / name
    evidence.mkdir(mode=0o700)
    manifest = {"schema_version": 1, "purpose": "experimental baseline, not default promotion",
                "profile": profile,
                "firefox_base": "0b76543aaeeeb2a5748ce2675ee36e7c94cb1125",
                "firefox_ci_task": "L5Q0X7WRRqCc5qenw0iRZQ", "files": {}}
    for name in ("caddy", "bridge"):
        source = root / "bin" / name
        destination = evidence / "bin" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(source, destination)
        manifest["files"]["bin/" + name] = digest(destination)
    repository = Path("/home/zubastik/naivefox-transport")
    manifest["server_revision"] = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(repository), "bundle", "create", str(evidence / "transport.bundle"), "--all"], check=True)
    campaigns = []
    for campaign in sorted(root.iterdir()):
        if campaign == evidence or not campaign.is_dir():
            continue
        results = sorted(campaign.glob("*/result.json"))
        if not results:
            continue
        target = evidence / campaign.name
        target.mkdir()
        for result in results:
            destination = target / (result.parent.name + "-result.json")
            shutil.copy2(result, destination)
        for name in ("schedule.json", "features.csv", "analysis.json", "analysis.md", "timing-schedule.json", "provenance.json", "outer-shaping.json", "outer-shaping-final.json"):
            source = campaign / name
            if source.exists():
                shutil.copy2(source, target / name)
        if (campaign / "analysis.json").exists():
            (target / "costs.json").write_text(json.dumps(summarize(campaign), indent=2) + "\n")
        campaigns.append(campaign.name)
    manifest["campaigns"] = campaigns
    for path in sorted(evidence.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(evidence))] = digest(path)
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"snapshot": str(evidence), "manifest_sha256": digest(evidence / "manifest.json"), "campaigns": len(campaigns)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--name", default="baseline-v1-evidence")
    parser.add_argument("--profile", default="16 rounds; 4096 up; 4x24576 + 12x131072 down; two-second capture")
    args = parser.parse_args()
    main(args.root, args.name, args.profile)
