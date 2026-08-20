#!/usr/bin/env bash

set -euo pipefail
umask 022

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(realpath -- "$script_dir/../../..")
plan_only=false
if [[ ${1:-} == "--plan-only" ]]; then
  plan_only=true
  shift
fi
if [[ $# -gt 1 ]]; then
  printf 'usage: %s [--plan-only] [OUTPUT_DIRECTORY]\n' "$0" >&2
  exit 2
fi
if [[ ! -d "$repo_root/.git" ]]; then
  printf 'export must run from the full minimal git checkout: %s\n' "$repo_root" >&2
  exit 2
fi
if [[ -n $(git -C "$repo_root" status --porcelain=v1) ]]; then
  printf 'minimal checkout must be clean before planning/export\n' >&2
  exit 2
fi

output=${1:-${NAIVEFOX_EXPORT_DIR:-$(dirname "$repo_root")/naivefox-minimal-source}}
output=$(realpath -m -- "$output")
case "$output" in
  "$repo_root"|"$repo_root"/*)
    printf 'export output must be outside the full Firefox checkout: %s\n' "$output" >&2
    exit 2
    ;;
esac
if ! $plan_only && [[ -e "$output" ]]; then
  printf 'refusing to overwrite an existing export path: %s\n' "$output" >&2
  exit 2
fi

plan=$(mktemp /tmp/naivefox-minimal-source-plan.XXXXXX.json)
tmp=
cleanup() {
  status=$?
  trap - EXIT
  rm -f -- "$plan"
  if [[ -n "$tmp" && -d "$tmp" ]]; then
    parent=$(realpath -- "$(dirname "$tmp")")
    resolved=$(realpath -- "$tmp")
    case "$resolved" in
      "$parent"/.naivefox-minimal-source.*) rm -rf -- "$resolved" ;;
      *) printf 'refusing unsafe temporary cleanup: %s\n' "$resolved" >&2 ;;
    esac
  fi
  exit "$status"
}
trap cleanup EXIT

python3 "$script_dir/assert-closure.py"
python3 "$script_dir/minimal-source-plan.py" \
  --repo "$repo_root" \
  --output "$plan" \
  --configure-report "$script_dir/../reports/configure-inputs-linux-x86_64.json" \
  --configure-report "$script_dir/../reports/configure-inputs-windows-x86_64.json" \
  --build-report "$script_dir/../reports/build-inputs-linux-x86_64.json" \
  --build-report "$script_dir/../reports/build-inputs-windows-x86_64.json" \
  --closure-report "$script_dir/../reports/closure-report-linux-x86_64.json" \
  --closure-report "$script_dir/../reports/closure-report-windows-x86_64.json"

if $plan_only; then
  python3 - "$plan" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
print(f"plan_entries={len(data['entries'])}")
print(f"plan_sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
print(f"minimal_export_commit={data['minimal_export_commit']}")
PY
  trap - EXIT
  rm -f -- "$plan"
  exit 0
fi

mkdir -p -- "$(dirname "$output")"
tmp=$(mktemp -d "$(dirname "$output")/.naivefox-minimal-source.XXXXXX")
python3 - "$repo_root" "$tmp" "$plan" <<'PY'
import hashlib
import json
import os
import pathlib
import shutil
import sys

repo = pathlib.Path(sys.argv[1]).resolve(strict=True)
stage = pathlib.Path(sys.argv[2]).resolve(strict=True)
plan_path = pathlib.Path(sys.argv[3]).resolve(strict=True)
plan = json.loads(plan_path.read_text(encoding="utf-8"))
epoch = int(plan["commit_epoch"])
generated_contents = plan.pop("generated_contents")

for entry in plan["entries"]:
    destination = stage / entry["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = entry["source"]
    if source is None:
        destination.write_text(generated_contents[entry["path"]], encoding="utf-8")
    else:
        source_path = repo / source
        if source_path.is_symlink():
            source_path = source_path.resolve(strict=True)
        shutil.copyfile(source_path, destination)
    actual = hashlib.sha256(destination.read_bytes()).hexdigest()
    if actual != entry["sha256"]:
        raise SystemExit(f"content changed while exporting: {entry['path']}")
    os.chmod(destination, int(entry["mode"], 8))
    os.utime(destination, (epoch, epoch))

manifest = dict(plan)
manifest["manifest_version"] = manifest.pop("plan_version")
canonical = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
manifest["manifest_sha256"] = manifest_hash
(stage / "minimal-source.manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
configure_sources = ",".join(manifest["configure_report_source_commits"])
closure_sources = ",".join(manifest["closure_report_source_commits"])
(stage / "UPSTREAM-BASE").write_text(
    f"Firefox base SHA: {manifest['firefox_base_commit']}\n"
    f"NaiveFox reference SHA: {manifest['naivefox_reference_commit']}\n"
    f"Minimal export SHA: {manifest['minimal_export_commit']}\n"
    f"Build report source SHA: {manifest['build_report_source_commit']}\n"
    f"Configure report source SHA(s): {configure_sources}\n"
    f"Closure report source SHA(s): {closure_sources}\n"
    "Minimal-source publication SHA: NOT_YET_PUBLISHED\n"
    f"Export manifest version: {manifest['manifest_version']}\n"
    f"Export manifest SHA-256: {manifest_hash}\n"
    f"Generated at: {manifest['generated_at']}\n",
    encoding="utf-8",
)
for generated in (stage / "minimal-source.manifest.json", stage / "UPSTREAM-BASE"):
    os.chmod(generated, 0o644)
    os.utime(generated, (epoch, epoch))
for directory in sorted(
    (path for path in stage.rglob("*") if path.is_dir()),
    key=lambda path: len(path.parts),
    reverse=True,
):
    os.chmod(directory, 0o755)
    os.utime(directory, (epoch, epoch))
os.chmod(stage, 0o755)
os.utime(stage, (epoch, epoch))
print(f"entries={len(manifest['entries'])}")
print(f"manifest_sha256={manifest_hash}")
PY

python3 "$script_dir/validate-minimal-source.py" "$tmp"
mv -T -- "$tmp" "$output"
tmp=
trap - EXIT
rm -f -- "$plan"
printf 'exported %s (%s files, %s)\n' "$output" \
  "$(find "$output" -type f | wc -l)" "$(du -sh "$output" | awk '{print $1}')"
