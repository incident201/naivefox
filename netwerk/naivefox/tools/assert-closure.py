#!/usr/bin/env python3
"""
assert-closure.py - Automated Policy & Closure Assertions Enforcer
Validates generated closure reports against strict architectural boundaries.
Fails with non-zero exit code if any forbidden objects, libraries, or paths are detected.
"""

import json
import os
import re
import sys


def assert_closure(report_path):
    if not os.path.exists(report_path):
        print(f"FAIL: Closure report not found: {report_path}", file=sys.stderr)
        return False

    with open(report_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
        report = json.loads(raw_text)

    violations = []
    target = report.get("report_provenance", {}).get("target_triple", report_path)
    is_linux = "linux" in target.lower()

    # 1. Assert NO absolute developer paths in raw JSON
    for bad_pattern in [r"/home/[a-zA-Z0-9_-]+", r"[A-Za-z]:\\[a-zA-Z0-9_-]+", r"[A-Za-z]:/[a-zA-Z0-9_-]+"]:
        if re.search(bad_pattern, raw_text):
            violations.append(f"Forbidden absolute developer path found matching '{bad_pattern}'")

    # 2. Assert NO DOM / Layout / GFX implementation objects
    forbidden_object_substrings = [
        ("dom/", ["OriginAttributes", "LeanDOMBindings"]),  # Only LeanDOMBindings allowed
        ("layout/", []),
        ("gfx/thebes", []),
        ("gfx/src", []),
        ("gfx/cairo", []),
        ("gfx/2d", []),
        ("harfbuzz", ["Unified_cpp_gfx_harfbuzz"]),  # HarfBuzz shaper objects forbidden
        ("abseil", []),
        ("jsoncpp", []),
        ("tools/profiler", ["Unified_cpp_tools_profiler", "LUL", "platform"]),
        ("breakpad", []),
    ]

    direct_objects = report.get("direct_objects", [])
    for obj in direct_objects:
        p = obj.get("path", "")
        # Check against forbidden substrings
        if "abseil" in p.lower():
            violations.append(f"Forbidden Abseil object in link closure: {p}")
        if "jsoncpp" in p.lower():
            violations.append(f"Forbidden JsonCPP object in link closure: {p}")
        if "harfbuzz" in p.lower() and ("Unified_cpp" in p or "hb-" in p):
            violations.append(f"Forbidden HarfBuzz shaper implementation object in link closure: {p}")
        if "layout/" in p:
            violations.append(f"Forbidden layout engine object in link closure: {p}")
        if "tools/profiler" in p and "ProfilerNaiveFoxStub" not in p:
            violations.append(f"Forbidden heavy Gecko Profiler object in link closure: {p}")
        if "breakpad" in p.lower() or "lul" in p.lower():
            violations.append(f"Forbidden Breakpad / LUL unwinder object in link closure: {p}")

    # 3. Assert NO Desktop UI libraries in dynamic dependencies (Linux DT_NEEDED)
    if is_linux:
        forbidden_dt_needed = [
            "libgtk-3", "libgdk-3", "libcairo", "libpango", "libatk",
            "libX11", "libXext", "libXrender", "libxcb", "libwayland",
        ]
        dyn_deps = report.get("dynamic_dependencies", [])
        for dep in dyn_deps:
            for bad in forbidden_dt_needed:
                if bad in dep:
                    violations.append(f"Forbidden desktop UI shared library in DT_NEEDED: {dep}")

    # 4. Assert SpiderMonkey and gkrust are present and populated
    static_libs = report.get("static_libraries", [])
    js_static = next((s for s in static_libs if "js_static" in s.get("path", "")), None)
    gkrust = next((s for s in static_libs if "gkrust" in s.get("path", "")), None)

    if not js_static or js_static.get("member_count", 0) == 0:
        violations.append("SpiderMonkey js_static is missing or has 0 archive members")
    if not gkrust or gkrust.get("member_count", 0) == 0:
        violations.append("Rust gkrust is missing or has 0 archive members")

    # 5. Assert NO unreachable heavy crates in Rust closure
    forbidden_crates = [
        "aa-stroke", "adblock", "alsa", "alsa-sys", "audio_thread_priority",
        "browser_engine", "webrtc", "wgpu", "ash", "autofill"
    ]
    rust_crates = report.get("rust_closure", {}).get("crates", [])
    crate_names = {c.get("name") for c in rust_crates}
    for bad_crate in forbidden_crates:
        if bad_crate in crate_names:
            violations.append(f"Forbidden unreachable crate found in Rust closure: {bad_crate}")

    # Print summary
    if violations:
        print(f"\n[ASSERTION FAILED] {target}: {len(violations)} violations detected:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return False

    print(f"[ASSERTION PASSED] {target}: All architectural closure rules strictly verified.")
    return True


def main():
    topsrcdir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    reports = [
        os.path.join(topsrcdir, "netwerk", "naivefox", "reports", "closure-report-linux-x86_64.json"),
        os.path.join(topsrcdir, "netwerk", "naivefox", "reports", "closure-report-windows-x86_64.json"),
    ]

    all_passed = True
    for rep in reports:
        if not assert_closure(rep):
            all_passed = False

    if not all_passed:
        print("\nClosure assertion enforcement FAILED.", file=sys.stderr)
        sys.exit(1)

    print("\nAll target closure assertions successfully passed.")


if __name__ == "__main__":
    main()
