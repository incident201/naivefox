#!/usr/bin/env bash

set -euo pipefail
umask 022

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../../.." && pwd)
repo_root=$(realpath -- "$repo_root")

if [[ ! -d "$repo_root/.git" ]]; then
  printf 'export must run from the full minimal git checkout: %s\n' "$repo_root" >&2
  exit 2
fi
if [[ -n $(git -C "$repo_root" status --porcelain=v1) ]]; then
  printf 'minimal checkout must be clean before export\n' >&2
  exit 2
fi

source_commit=$(git -C "$repo_root" rev-parse HEAD)
commit_epoch=$(git -C "$repo_root" show -s --format=%ct HEAD)
commit_iso=$(git -C "$repo_root" show -s --format=%cI HEAD)
output=${1:-${NAIVEFOX_EXPORT_DIR:-$(dirname "$repo_root")/naivefox-minimal-source}}
output=$(realpath -m -- "$output")
case "$output" in
  "$repo_root"|"$repo_root"/*)
    printf 'export output must be outside the full Firefox checkout: %s\n' "$output" >&2
    exit 2
    ;;
esac
if [[ -e "$output" ]]; then
  printf 'refusing to overwrite an existing export path: %s\n' "$output" >&2
  exit 2
fi

mkdir -p -- "$(dirname "$output")"
tmp=$(mktemp -d "$(dirname "$output")/.naivefox-minimal-source.XXXXXX")
cleanup() {
  local status=$?
  if [[ -d "$tmp" ]]; then
    rm -rf -- "$tmp"
  fi
  return "$status"
}
trap cleanup EXIT

python3 - "$repo_root" "$tmp" "$source_commit" "$commit_epoch" "$commit_iso" \
  "$script_dir/../reports/closure-report-linux-x86_64.json" \
  "$script_dir/../reports/closure-report-windows-x86_64.json" <<'PY'
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections import defaultdict

repo = pathlib.Path(sys.argv[1]).resolve()
stage = pathlib.Path(sys.argv[2]).resolve()
source_commit, commit_epoch, commit_iso = sys.argv[3:6]
report_paths = [pathlib.Path(x).resolve() for x in sys.argv[6:]]

entries = {}
categories = defaultdict(set)
missing = []
ignored_generated = set()

def norm(value):
    value = str(value).replace("\\", "/")
    p = pathlib.PurePosixPath(value)
    if p.is_absolute() or value.startswith("/") or ".." in p.parts:
        raise SystemExit(f"unsafe repository path in closure: {value}")
    return value

def add(source, category, destination=None):
    if not source:
        return
    source = norm(source)
    # Closure reports intentionally retain a small amount of provenance from
    # generated depfile/backend inventories.  These are not source inputs and
    # must never be copied into a standalone tree.
    if source.startswith("objdir/") or source.startswith("obj-"):
        ignored_generated.add(source)
        return
    destination = norm(destination or source)
    source_path = repo / source
    if not source_path.is_file():
        missing.append((source, category))
        return
    previous = entries.get(destination)
    if previous and previous["source"] != source:
        raise SystemExit(
            f"two sources map to one export path {destination}: "
            f"{previous['source']} and {source}"
        )
    entries[destination] = {"source": source, "category": category}
    categories[category].add(destination)

def tracked_under(relative, category):
    relative = norm(relative).rstrip("/")
    raw = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "-z", "--", relative],
    )
    paths = [x for x in raw.decode("utf-8", "surrogateescape").split("\0") if x]
    if not paths:
        raise SystemExit(f"explicit export input has no tracked files: {relative}")
    for path in paths:
        add(path, category)

def tracked_siblings(relative, category):
    relative = norm(relative)
    directory = str(pathlib.PurePosixPath(relative).parent)
    raw = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "-z", "--", directory],
    )
    for path in raw.decode("utf-8", "surrogateescape").split("\0"):
        if not path:
            continue
        suffix = pathlib.PurePosixPath(path).suffix
        name = pathlib.PurePosixPath(path).name
        if suffix in {".mozbuild", ".py", ".in", ".configure"} or \
           name.startswith("sources."):
            add(path, category)

for report_path in report_paths:
    if not report_path.is_file():
        raise SystemExit(f"closure report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    provenance = report["report_provenance"]
    if provenance["source_commit_sha"] != "8fd1f47a67bcfe14471896c8bf488428a8a240ae":
        raise SystemExit(
            f"unexpected audited source in {report_path}: "
            f"{provenance['source_commit_sha']}"
        )
    build = report["build_inputs"]
    for key in (
        "cxx_translation_units", "headers", "cargo_manifests", "xpidl_inputs",
        "ipdl_inputs", "webidl_binding_inputs", "generators_and_python_scripts",
        "mozbuild_definition_inputs", "runtime_resource_sources", "licenses_and_notices",
    ):
        for value in build.get(key, []):
            add(value, f"report:{key}")
    for key in ("cargo_root_manifest", "cargo_lockfile", "cargo_config_template"):
        add(build.get(key), f"report:{key}")
    glean = build.get("glean", {})
    for key in ("generator_scripts", "metrics_yaml_inputs", "pings_yaml_inputs"):
        for value in glean.get(key, []):
            add(value, f"report:glean:{key}")
    for crate in report["rust_closure"]["crates"]:
        manifest = crate.get("manifest_path")
        add(manifest, "report:rust:manifest")
        if manifest:
            tracked_under(str(pathlib.PurePosixPath(manifest).parent), "report:rust:crate-tree")
        for value in crate.get("source_paths", []):
            add(value, "report:rust:source")

# The project tree is the product source of truth.  Git tracking makes this an
# allowlist, not a copy-and-delete blacklist, and excludes ignored objdirs and
# local credentials by construction.
tracked_under("netwerk/naivefox", "project")

# Firefox build/bootstrap inputs deliberately kept as explicit infrastructure.
for tree in ("build", "config", "python", "tools", "third_party/python", "nsprpub"):
    tracked_under(tree, f"explicit:{tree}")
for tree in ("dom/bindings", "gfx/harfbuzz/src", "intl/icu/source"):
    tracked_under(tree, f"explicit:{tree}")
for path in (
    "mach", "configure", "configure.py", "moz.configure", "Cargo.toml", "Cargo.lock",
    ".cargo/config.toml.in", "LICENSE",
    # The configure graph reads this even for the non-browser NaiveFox
    # application; it is a small tracked version input, not browser runtime.
    "browser/config/version.txt", "browser/config/version_display.txt",
    # toolkit/moz.configure probes this header before the selected NSS build
    # graph is evaluated.
    "security/nss/lib/nss/nss.h",
    "netwerk/naivefox/moz.configure", "toolkit/moz.configure", "js/moz.configure",
    "js/ffi.configure", "memory/moz.configure",
):
    add(path, "explicit:bootstrap")
tracked_under("build/moz.configure", "explicit:configure")

# A moz.build may include a sibling sources.mozbuild, Python generator, or
# template without that file appearing in a compiler depfile.  Add only those
# tracked siblings, never an entire unrelated directory.
for path in list(entries):
    if path.endswith("/moz.build") or path == "moz.build":
        tracked_siblings(path, "explicit:mozbuild-sibling")

# Product-facing root files are aliases, while the original project paths stay
# available for the Firefox build and for traceability.
for source, destination in (
    ("netwerk/naivefox/README.md", "README.md"),
    ("netwerk/naivefox/MINIMISATION-TASK.MD", "MINIMISATION-TASK.MD"),
    ("netwerk/naivefox/UPSTREAM.md", "UPSTREAM.md"),
    ("netwerk/naivefox/ROADMAP.md", "ROADMAP.md"),
    ("netwerk/naivefox/KNOWN-ISSUES.md", "KNOWN-ISSUES.md"),
    ("netwerk/naivefox/PRE-EXPORT-AUDIT.md", "PRE-EXPORT-AUDIT.md"),
    ("netwerk/naivefox/TEST-REPORT.md", "TEST-REPORT.md"),
):
    add(source, "product-root", destination)

if missing:
    details = "\n".join(f"{path} ({kind})" for path, kind in sorted(set(missing)))
    raise SystemExit(f"closure references missing repository files:\n{details}")

for destination, entry in sorted(entries.items()):
    source_path = repo / entry["source"]
    destination_path = stage / destination
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.is_symlink():
        resolved = source_path.resolve()
        try:
            resolved.relative_to(repo)
        except ValueError:
            raise SystemExit(f"source symlink escapes checkout: {source_path}")
        if not resolved.is_file():
            raise SystemExit(f"source symlink is not a regular file: {source_path}")
        shutil.copy2(resolved, destination_path)
    else:
        shutil.copy2(source_path, destination_path)
    os.utime(destination_path, (int(commit_epoch), int(commit_epoch)))

manifest = {
    "manifest_version": 1,
    "source_commit": source_commit,
    "audited_closure_source_commit": "8fd1f47a67bcfe14471896c8bf488428a8a240ae",
    "firefox_base_commit": "8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6",
    "generated_at": commit_iso,
    "entries": [
        {"path": destination, **entry}
        for destination, entry in sorted(entries.items())
    ],
    "category_counts": {
        category: len(paths) for category, paths in sorted(categories.items())
    },
    "ignored_generated_inputs": sorted(ignored_generated),
}
canonical = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
manifest["manifest_sha256"] = manifest_hash
(stage / "minimal-source.manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
(stage / "UPSTREAM-BASE").write_text(
    "Firefox base SHA: 8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6\n"
    f"NaiveFox SHA: {source_commit}\n"
    f"Minimal SHA: {source_commit}\n"
    "Audited closure source SHA: 8fd1f47a67bcfe14471896c8bf488428a8a240ae\n"
    "Closure report snapshot SHA: 6fa615ff8206108122c0ec9178c649ed5db43c41\n"
    f"Export manifest version: 1\nExport manifest SHA-256: {manifest_hash}\n"
    f"Generated at: {commit_iso}\n",
    encoding="utf-8",
)
for generated in (stage / "minimal-source.manifest.json", stage / "UPSTREAM-BASE"):
    os.utime(generated, (int(commit_epoch), int(commit_epoch)))
print(f"entries={len(entries)}")
print(f"manifest_sha256={manifest_hash}")
print(f"source_commit={source_commit}")
PY

python3 "$script_dir/validate-minimal-source.py" "$tmp"
mv -- "$tmp" "$output"
trap - EXIT
printf 'exported %s (%s files, %s)\n' "$output" \
  "$(find "$output" -type f | wc -l)" "$(du -sh "$output" | awk '{print $1}')"
