#!/usr/bin/env python3
"""Compare original and optimized no-connect with shared Firefox A/B controls."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("paired_native_matrix", HERE / "run-matched-app-matrix.py")
matched = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = matched
spec.loader.exec_module(matched)
require = matched.require
matched.ARMS = ("native-before-socks", "native-before-http",
                "native-no-connect-socks", "native-no-connect-http")


class PairedCampaign(matched.Campaign):
    def __init__(self, args, protocol):
        super().__init__(args, protocol)
        self.before = args
        self.after = copy.copy(args)
        for name in ("runtime", "runtime_manifest", "caddy", "caddy_build_id"):
            setattr(self.after, name, getattr(args, "after_" + name))

    def sample(self, row, index):
        original_arm = row["naivefox_arm"]
        before = original_arm == "reference" or original_arm.startswith("native-before-")
        self.args = self.before if before else self.after
        actual = dict(row)
        if original_arm.startswith("native-before-"):
            actual["naivefox_arm"] = original_arm.replace("native-before-", "native-no-connect-", 1)
        try:
            super().sample(actual, index)
        finally:
            path = self.root / f"sample-{index:03d}.json"
            if path.exists():
                result = json.loads(path.read_text())
                result["naivefox_arm"] = original_arm
                result["runtime_variant"] = "before" if before else "after"
                result["runtime_sha256"] = matched.digest(self.args.runtime)
                result["caddy_sha256"] = matched.digest(self.args.caddy)
                matched.write_json(path, result)
                if self.samples and self.samples[-1]["sample"] == result["sample"]:
                    self.samples[-1].update(result)
            feature = self.root / "features" / f"sample-{index:03d}.json"
            if feature.exists():
                document = json.loads(feature.read_text())
                document["naivefox_arm"] = original_arm
                matched.write_json(feature, document)
            self.args = self.before


def summarize(campaign, analysis):
    mean = statistics.fmean
    result = []
    for listener in ("socks", "http"):
        arm = "native-no-connect-" + listener
        candidate = [r for r in campaign.samples if r["naivefox_arm"] == arm]
        residuals = analysis["protocols"][campaign.protocol]["views"]
        row = {"startup_protocol": campaign.protocol, "listener": listener,
               "blocks": campaign.args.blocks,
               "whole_ip_bytes": mean(r["whole"]["wire_bytes"] for r in candidate),
               "residual": {v: residuals[v]["arms"][arm] for v in matched.VIEWS},
               "comparisons": {}}
        for baseline in ("before", "firefox"):
            control_arm = "reference" if baseline == "firefox" else "native-before-" + listener
            control = [r for r in campaign.samples if r["naivefox_arm"] == control_arm]
            old_bytes = mean(r["whole"]["wire_bytes"] for r in control)
            comparison = {"baseline_whole_ip_bytes": old_bytes,
                          "extra_complete_session_traffic_percent": 100 * (row["whole_ip_bytes"] / old_bytes - 1),
                          "stages": {}}
            if baseline == "before":
                comparison["residual"] = {v: residuals[v]["arms"][control_arm] for v in matched.VIEWS}
            for index, stage in enumerate(campaign.manifest["stages"]):
                def duration(rows):
                    return mean(mean(r["application"]["stages"][index]["job_io_ms"]) if index >= 3
                                else r["application"]["stages"][index]["io_ms"] for r in rows)
                old, new = duration(control), duration(candidate)
                resolved = old > 0 and new > 0
                metrics = {"baseline_io_ms": old, "candidate_io_ms": new,
                           "time_increase_percent": 100 * (new / old - 1) if resolved else None,
                           "timer_resolved_ratio": resolved}
                if index < 3:
                    metrics["effective_rate_loss_percent"] = 100 * (1 - old / new) if resolved else None
                comparison["stages"][stage["name"]] = metrics
            row["comparisons"][baseline] = comparison
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("objdir", "root", "runtime", "runtime-manifest", "caddy", "caddy-build-id",
                 "after-runtime", "after-runtime-manifest", "after-caddy", "after-caddy-build-id",
                 "backend", "firefox", "geckodriver", "reference-proof"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--asset-dir", type=Path, default=matched.APP)
    parser.add_argument("--firefox-base", required=True)
    parser.add_argument("--carrier-profile", default="native-stream-v2")
    parser.add_argument("--protocol", choices=("h2", "h3", "both"), default="both")
    parser.add_argument("--link", choices=("loopback", "rtt40-20mbps"), required=True)
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    require(1 <= args.blocks <= 30 and 30 <= args.timeout <= 150, "invalid campaign bounds")
    matched.assert_isolated_namespace()
    args.objdir = args.objdir.resolve(strict=True)
    args.root = args.root.resolve()
    require(args.root.is_relative_to(args.objdir / "no-connect/matched-app") and not args.root.exists(),
            "new paired campaign must be under the object directory")
    base = matched.run_quiet(["git", "-C", HERE, "merge-base", "HEAD", "firefox-upstream"]).stdout.strip()
    require(base == args.firefox_base and matched.digest(args.asset_dir / "manifest.json") == matched.MANIFEST_SHA,
            "source base or application manifest changed")
    proof = matched.legacy.verify_reference(args.reference_proof, args.firefox, base)
    frozen = [Path(__file__), HERE / "run-matched-app-matrix.py", HERE / "audit-matched-app-results.py",
              *(HERE / n for n in ("carrier_capture.py", "run-no-connect-tests.py", "camouflage_features.py",
                 "analyze-camouflage-arms.py", "analyze-camouflage.py", "camouflage_superblocks.py",
                 "camouflage_browser_controller.py", "camouflage_capture_health.py", "monitor-network-mutations.py")),
              *(args.asset_dir / n for n in ("manifest.json", "app.js", "app.template.js", "render-app.py",
                 "main.go", "go.mod", "go.sum", "site.css", "image.svg", "index.html")),
              args.backend, args.geckodriver, args.reference_proof]
    frozen += [args.firefox.parent / name for name in proof["runtime_files_sha256"]]
    runtimes = {}
    for prefix in ("", "after_"):
        runtime, manifest = getattr(args, prefix + "runtime"), getattr(args, prefix + "runtime_manifest")
        identity, files = matched.verify_native_runtime(manifest, runtime)
        caddy, caddy_proof = getattr(args, prefix + "caddy"), getattr(args, prefix + "caddy_build_id")
        server = matched.verify_caddy_build_id(caddy_proof, caddy)
        frozen += [*files, manifest, caddy, caddy_proof]
        runtimes["after" if prefix else "before"] = {"runtime": identity, "server": server}
    args.frozen_files = list(dict.fromkeys(frozen))
    os.umask(0o077)
    args.root.mkdir(parents=True)
    runtime = args.root / "runtime"
    runtime.mkdir()
    os.environ.update(TMPDIR=str(runtime), XDG_RUNTIME_DIR=str(runtime), LD_LIBRARY_PATH=str(args.firefox.parent),
                      MOZ_HEADLESS="1", MOZ_CRASHREPORTER_DISABLE="1", MOZ_CRASHREPORTER_NO_REPORT="1")
    matched.tempfile.tempdir = str(runtime)
    for key in ("SSLKEYLOGFILE", "DISPLAY", "WAYLAND_DISPLAY", "MOZ_LOG", "MOZ_LOG_FILE"):
        os.environ.pop(key, None)
    provenance = {"schema": "paired-no-connect-optimization-v1", "manifest": json.loads((args.asset_dir / "manifest.json").read_text()),
                  "blocks_per_protocol": args.blocks, "seed": args.seed, "link": args.link,
                  "verified_reference": proof, "runtimes": runtimes, "shared_firefox_controls": True,
                  "source_sha256": {str(path): matched.digest(path) for path in args.frozen_files},
                  "screening_only": args.blocks < 30}
    matched.write_json(args.root / "provenance.json", provenance)
    rows = []
    for protocol in (("h2", "h3") if args.protocol == "both" else (args.protocol,)):
        campaign = PairedCampaign(args, protocol)
        try:
            campaign.start()
            report = campaign.run()
            rows += summarize(campaign, report)
            matched.write_json(args.root / "matrix.json", {"schema": provenance["schema"], "rows": rows,
                               "shared_firefox_controls": True, "screening_only": args.blocks < 30})
        finally:
            campaign.close()
    print("Paired no-connect comparison complete", flush=True)


if __name__ == "__main__":
    main()
