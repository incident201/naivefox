#!/usr/bin/env python3
"""
assert-closure.py - Automated Policy & Closure Assertions Enforcer
Validates generated closure reports against strict architectural boundaries.
Fails with non-zero exit code if any forbidden objects, libraries, or paths are detected.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from provenance import (
    CANONICAL_REPORT_PATHS,
    TARGET_SPECS,
    load_and_validate_report_bundle,
    validate_evidence_head,
)


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _current_commit(topsrcdir):
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=topsrcdir, text=True
    ).strip()


def _git_commit_exists(topsrcdir, sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        return False
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=topsrcdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def _check_repo_path(path, topsrcdir, violations, field, allow_objdir=False):
    """Validate a report path without accepting developer/cache paths."""
    if not isinstance(path, str) or not path:
        violations.append(f"{field} is empty or not a string")
        return
    normalized = path.replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or "/home/" in normalized
        or "/Users/" in normalized
        or "\\Users\\" in path
    ):
        violations.append(f"{field} is not repository-relative: {path}")
        return
    parts = [part for part in normalized.split("/") if part]
    if ".." in parts:
        violations.append(f"{field} escapes the repository: {path}")
        return
    if allow_objdir and normalized.startswith("objdir/"):
        return
    full = os.path.join(topsrcdir, *parts)
    if not os.path.exists(full):
        violations.append(f"{field} does not exist in checkout: {path}")


def _check_source_lists(report, topsrcdir, violations):
    build = report.get("build_inputs", {})
    rust = report.get("rust_closure", {})
    for index, crate in enumerate(rust.get("crates", [])):
        _check_repo_path(
            crate.get("manifest_path"),
            topsrcdir,
            violations,
            f"rust_closure.crates[{index}].manifest_path",
        )
        for source_index, source_path in enumerate(crate.get("source_paths", [])):
            _check_repo_path(
                source_path,
                topsrcdir,
                violations,
                f"rust_closure.crates[{index}].source_paths[{source_index}]",
            )

    list_fields = [
        "cxx_translation_units",
        "headers",
        "xpidl_inputs",
        "ipdl_inputs",
        "webidl_binding_inputs",
        "generators_and_python_scripts",
        "mozbuild_definition_inputs",
        "runtime_resource_sources",
        "licenses_and_notices",
    ]
    for field in list_fields:
        values = build.get(field, report.get(field, []))
        if not isinstance(values, list):
            violations.append(f"build_inputs.{field} is not a list")
            continue
        for index, path in enumerate(values):
            _check_repo_path(
                path, topsrcdir, violations, f"build_inputs.{field}[{index}]"
            )

    for field in ("cargo_root_manifest", "cargo_lockfile", "cargo_config_template"):
        _check_repo_path(
            build.get(field), topsrcdir, violations, f"build_inputs.{field}"
        )

    for index, path in enumerate(build.get("cargo_manifests", [])):
        _check_repo_path(
            path, topsrcdir, violations, f"build_inputs.cargo_manifests[{index}]"
        )

    glean = build.get("glean", {})
    if not isinstance(glean, dict):
        violations.append("build_inputs.glean is not an object")
    else:
        for field in ("generator_scripts", "metrics_yaml_inputs", "pings_yaml_inputs"):
            values = glean.get(field, [])
            if not isinstance(values, list):
                violations.append(f"build_inputs.glean.{field} is not a list")
                continue
            for index, path in enumerate(values):
                _check_repo_path(
                    path, topsrcdir, violations, f"build_inputs.glean.{field}[{index}]"
                )
        for index, path in enumerate(glean.get("cache_inputs", [])):
            _check_repo_path(
                path,
                topsrcdir,
                violations,
                f"build_inputs.glean.cache_inputs[{index}]",
                allow_objdir=True,
            )
        for index, path in enumerate(glean.get("generated_outputs_sample", [])):
            normalized = str(path).replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                violations.append(
                    f"build_inputs.glean.generated_outputs_sample[{index}] is not relative: {path}"
                )


def _forbidden_platform_crate_tokens(platform):
    tokens = ["core-foundation", "darwin", "haiku", "redox"]
    if platform != "android":
        tokens.append("android")
    if platform != "windows":
        tokens.append("windows")
    return tokens


def assert_closure(report_path, topsrcdir):
    if not os.path.exists(report_path):
        print(f"FAIL: Closure report not found: {report_path}", file=sys.stderr)
        return False

    with open(report_path, encoding="utf-8") as f:
        raw_text = f.read()
        report = json.loads(raw_text)

    violations = []
    provenance = report.get("report_provenance", {})
    target = provenance.get("target_triple", report_path)
    target_spec = next(
        (value for value in TARGET_SPECS if value["cargo_target"] == target), None
    )
    platform = target_spec["platform"] if target_spec else None
    if target_spec is None:
        violations.append(f"unknown closure target triple: {target}")
    else:
        if provenance.get("target") != target_spec["name"]:
            violations.append("closure report target name does not match target triple")
        if provenance.get("platform") != platform:
            violations.append("closure report platform does not match target triple")

    report_commit = provenance.get("source_commit_sha")
    if not provenance.get("source_worktree_clean"):
        violations.append("closure report was not collected from a clean checkout")
    try:
        release = validate_evidence_head(Path(topsrcdir))
    except ValueError as error:
        violations.append(str(error))
        release = None
    if release and report_commit != release.source_commit:
        violations.append("closure report does not attest direct evidence parent S")
    if release and provenance.get("firefox_base_sha") != release.firefox_base_commit:
        violations.append("closure report Firefox base does not match source S")
    if (
        release
        and provenance.get("naivefox_reference_sha")
        != release.naivefox_reference_commit
    ):
        violations.append("closure report NaiveFox reference does not match source S")
    if not _git_commit_exists(topsrcdir, provenance.get("source_commit_sha")):
        violations.append("source_commit_sha is not an existing commit")
    if not _git_commit_exists(topsrcdir, provenance.get("firefox_base_sha")):
        violations.append("firefox_base_sha is not an existing commit")
    if not provenance.get("analyzer_version", "").startswith("2.7."):
        violations.append(
            "report was not generated by the strict target-aware analyzer"
        )
    analyzer_path = os.path.join(
        topsrcdir, "netwerk", "naivefox", "tools", "analyze-full-closure.py"
    )
    with open(analyzer_path, "rb") as analyzer_file:
        analyzer_sha256 = hashlib.sha256(analyzer_file.read()).hexdigest()
    if provenance.get("analyzer_sha256") != analyzer_sha256:
        violations.append("closure report analyzer hash does not match current tool")
    if report.get("rust_closure", {}).get("platform_filter") != target:
        violations.append("Rust closure platform filter does not match target triple")
    if report.get("rust_closure", {}).get("root_package") != "gkrust-naivefox":
        violations.append("Rust closure root is not gkrust-naivefox")

    _check_source_lists(report, topsrcdir, violations)

    # 1. Assert NO absolute developer paths in raw JSON
    for bad_pattern in [
        r"/home/[a-zA-Z0-9_-]+",
        r"[A-Za-z]:\\[a-zA-Z0-9_-]+",
        r"[A-Za-z]:/[a-zA-Z0-9_-]+",
    ]:
        if re.search(bad_pattern, raw_text):
            violations.append(
                f"Forbidden absolute developer path found matching '{bad_pattern}'"
            )

    # 2. Assert NO DOM / Layout / GFX implementation objects
    direct_objects = report.get("direct_objects", [])
    for obj in direct_objects:
        p = obj.get("path", "")
        # Check against forbidden substrings
        if "abseil" in p.lower():
            violations.append(f"Forbidden Abseil object in link closure: {p}")
        if "jsoncpp" in p.lower():
            violations.append(f"Forbidden JsonCPP object in link closure: {p}")
        if "harfbuzz" in p.lower() and ("Unified_cpp" in p or "hb-" in p):
            violations.append(
                f"Forbidden HarfBuzz shaper implementation object in link closure: {p}"
            )
        if "layout/" in p:
            violations.append(f"Forbidden layout engine object in link closure: {p}")
        if "tools/profiler" in p and "ProfilerNaiveFoxStub" not in p:
            violations.append(
                f"Forbidden heavy Gecko Profiler object in link closure: {p}"
            )
        if "breakpad" in p.lower() or "lul" in p.lower():
            violations.append(
                f"Forbidden Breakpad / LUL unwinder object in link closure: {p}"
            )

        lower = p.lower()
        if "dom/" in lower and "leandombindings" not in lower:
            violations.append(f"DOM implementation object in link closure: {p}")
        if "gfx/" in lower and "profilernaivefoxstub" not in lower:
            violations.append(f"GFX implementation object in link closure: {p}")
        if "harfbuzz" in lower:
            violations.append(f"HarfBuzz implementation object in link closure: {p}")

    # The filtered Cargo graph must not smuggle a platform-specific crate into
    # the other target.  These names are intentionally conservative: a new
    # platform crate should make the audit fail and be reviewed explicitly.
    rust_crates = report.get("rust_closure", {}).get("crates", [])
    crate_names = {str(c.get("name", "")).lower() for c in rust_crates}
    if crate_names.intersection({
        "firefox-on-glean",
        "glean",
        "glean-core",
        "glean-ffi",
    }):
        violations.append(
            "Global Firefox Glean Rust runtime is reachable; NaiveFox must use "
            "the project-owned no-op metric shims"
        )
    forbidden_platform = _forbidden_platform_crate_tokens(platform)
    for crate_name in sorted(crate_names):
        if any(token in crate_name for token in forbidden_platform):
            violations.append(
                f"Platform-inapplicable Rust crate in {target} closure: {crate_name}"
            )
        if any(token in crate_name for token in ("harfbuzz-sys", "abseil", "jsoncpp")):
            violations.append(f"Forbidden implementation Rust crate: {crate_name}")

    # 3. Assert NO Desktop UI libraries in dynamic dependencies (Linux DT_NEEDED)
    if platform in {"desktop-linux", "android"}:
        forbidden_dt_needed = [
            "libgtk-3",
            "libgdk-3",
            "libcairo",
            "libpango",
            "libatk",
            "libX11",
            "libXext",
            "libXrender",
            "libxcb",
            "libwayland",
        ]
        dyn_deps = report.get("dynamic_dependencies", [])
        for dep in dyn_deps:
            for bad in forbidden_dt_needed:
                if bad in dep:
                    violations.append(
                        f"Forbidden desktop UI shared library in DT_NEEDED: {dep}"
                    )

    # 4. Assert the minimized Rust closure is present and SpiderMonkey stays
    # out of the NaiveFox product graph.
    static_libs = report.get("static_libraries", [])
    js_static = next((s for s in static_libs if "js_static" in s.get("path", "")), None)
    gkrust = next((s for s in static_libs if "gkrust" in s.get("path", "")), None)

    if js_static:
        violations.append(
            "SpiderMonkey js_static unexpectedly present in NaiveFox closure"
        )
    if not gkrust or gkrust.get("member_count", 0) == 0:
        violations.append("Rust gkrust is missing or has 0 archive members")

    # 5. Assert NO unreachable heavy crates in Rust closure
    forbidden_crates = [
        "aa-stroke",
        "adblock",
        "alsa",
        "alsa-sys",
        "audio_thread_priority",
        "browser_engine",
        "webrtc",
        "wgpu",
        "ash",
        "autofill",
    ]
    for bad_crate in forbidden_crates:
        if bad_crate in crate_names:
            violations.append(
                f"Forbidden unreachable crate found in Rust closure: {bad_crate}"
            )

    # The parent-only build still generates the small C++ metric ABI needed by
    # retained Necko/XPCOM sources, but must not silently re-enable Firefox's
    # browser-wide metrics/pings index. Keep this allowlist synchronized with
    # toolkit/components/glean/moz.build.
    glean = report.get("build_inputs", {}).get("glean", {})
    allowed_metrics = {
        "dom/security/metrics.yaml",
        "intl/locale/metrics.yaml",
        "ipc/metrics.yaml",
        "modules/libjar/metrics.yaml",
        "modules/libpref/metrics.yaml",
        "netwerk/cache2/metrics.yaml",
        "netwerk/dns/metrics.yaml",
        "netwerk/metrics.yaml",
        "netwerk/protocol/http/metrics.yaml",
        "netwerk/protocol/websocket/metrics.yaml",
        "security/certverifier/metrics.yaml",
        "security/ct/metrics.yaml",
        "security/manager/ssl/metrics.yaml",
        "security/sandbox/metrics.yaml",
        "storage/metrics.yaml",
        "toolkit/components/antitracking/metrics.yaml",
        "toolkit/components/processtools/metrics.yaml",
        "toolkit/components/startup/metrics.yaml",
        "toolkit/components/url-classifier/metrics.yaml",
        "toolkit/profile/metrics.yaml",
        "toolkit/xre/metrics.yaml",
        "xpcom/metrics.yaml",
        "toolkit/components/glean/tags.yaml",
    }
    for field in ("metrics_yaml_inputs", "pings_yaml_inputs"):
        for path in glean.get(field, []):
            if path not in allowed_metrics and path != "netwerk/pings.yaml":
                violations.append(
                    f"Global/non-allowlisted Glean schema in {field}: {path}"
                )

    # Print summary
    if violations:
        print(
            f"\n[ASSERTION FAILED] {target}: {len(violations)} violations detected:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return False

    print(
        f"[ASSERTION PASSED] {target}: All architectural closure rules strictly verified."
    )
    return True


def main():
    topsrcdir = _repo_root()
    reports = [
        os.path.join(
            topsrcdir,
            "netwerk",
            "naivefox",
            "reports",
            f"closure-report-{target['name']}.json",
        )
        for target in TARGET_SPECS
    ]

    try:
        release = validate_evidence_head(Path(topsrcdir))
        load_and_validate_report_bundle(
            Path(topsrcdir),
            [Path(topsrcdir) / path for path in CANONICAL_REPORT_PATHS],
            release.source_commit,
            release.firefox_base_commit,
            release.naivefox_reference_commit,
        )
    except (OSError, ValueError) as error:
        print(f"\nClosure evidence provenance FAILED: {error}", file=sys.stderr)
        sys.exit(1)

    all_passed = True
    for rep in reports:
        if not assert_closure(rep, topsrcdir):
            all_passed = False

    if not all_passed:
        print("\nClosure assertion enforcement FAILED.", file=sys.stderr)
        sys.exit(1)

    print("\nAll target closure assertions successfully passed.")


if __name__ == "__main__":
    main()
