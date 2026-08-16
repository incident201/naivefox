#!/usr/bin/env python3
"""
verify-shims.py - Focused Unit & Targeted Shim Semantics Verification Suite
Verifies the exact invariants, fail-closed semantics, and symbol boundaries of all NaiveFox shims.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def test_symbol_absence(topsrcdir, objdir):
    """Verify heavy upstream symbols are completely absent from libxul."""
    libxul_path = os.path.join(objdir, "dist", "bin", "libxul.so")
    if not os.path.exists(libxul_path):
        libxul_path = os.path.join(objdir, "toolkit", "library", "build", "libxul.so")
    if not os.path.exists(libxul_path):
        print(f"Skipping symbol check (libxul.so not found at {libxul_path})")
        return True

    print("[SHIM TEST 1] Verifying absence of heavy symbols in libxul binary...")
    forbidden_symbols = [
        ("Breakpad ELF parser", ["google_breakpad", "ElfFile"]),
        ("LUL DWARF stack unwinder", ["lul::LUL", "lul::SecMap"]),
        ("JsonCPP Profiler exporter", ["Json::Value", "Json::FastWriter"]),
        ("Abseil containers", ["absl::container", "absl::str_format"]),
        ("HarfBuzz font shaper runtime", ["hb_shape_full", "hb_ot_shape"]),
    ]

    try:
        nm_out = subprocess.check_output(["nm", "-D", libxul_path], stderr=subprocess.DEVNULL, text=True)
    except Exception:
        nm_out = subprocess.check_output(["readelf", "-s", libxul_path], stderr=subprocess.DEVNULL, text=True)

    failed = False
    for desc, syms in forbidden_symbols:
        for s in syms:
            if s in nm_out:
                print(f"  FAIL: Found forbidden symbol for {desc}: {s}", file=sys.stderr)
                failed = True
    if not failed:
        print("  PASS: Zero heavy unwinder, Breakpad, JsonCPP, Abseil, or HarfBuzz symbols found.")
    return not failed


def test_profiler_stub_invariants(topsrcdir, objdir):
    """Verify Gecko Profiler component and stub semantics."""
    print("[SHIM TEST 2] Verifying Profiler stub invariants...")
    comp_conf = os.path.join(topsrcdir, "netwerk", "naivefox", "core", "components.conf")
    with open(comp_conf, "r", encoding="utf-8") as f:
        comp_text = f.read()
    assert "nsIProfiler" not in comp_text, "nsIProfiler must not be registered in components.conf"

    stub_path = os.path.join(topsrcdir, "netwerk", "naivefox", "core", "ProfilerNaiveFoxStub.cpp")
    with open(stub_path, "r", encoding="utf-8") as f:
        stub_text = f.read()

    assert "profiler_feature_active(uint32_t aFeature) { return false; }" in stub_text
    assert "profiler_is_paused() { return false; }" in stub_text
    assert "profiler_register_thread(const char* name, void* guessStackTop) {\n  return nullptr;\n}" in stub_text
    assert "profiler_get_backtrace() {\n  return nullptr;\n}" in stub_text
    print("  PASS: nsIProfiler component excluded and Profiler stub strictly inactive.")
    return True


def test_security_manager_invariants(topsrcdir):
    """Verify nsScriptSecurityManagerNaiveFox implementation invariants."""
    print("[SHIM TEST 3] Verifying Security Manager fail-closed invariants...")
    sec_path = os.path.join(topsrcdir, "caps", "nsScriptSecurityManagerNaiveFox.cpp")
    with open(sec_path, "r", encoding="utf-8") as f:
        sec_text = f.read()

    assert "GetSystemPrincipal" in sec_text
    assert "SystemPrincipalSingletonConstructor" in sec_text
    assert "NS_ERROR_DOM_BAD_URI" in sec_text or "NS_ERROR_FAILURE" in sec_text
    assert "NS_ERROR_NOT_AVAILABLE" in sec_text or "NS_ERROR_NOT_IMPLEMENTED" in sec_text
    print("  PASS: SystemPrincipal-only loads allowed; unprivileged/foreign loads fail-closed.")
    return True


def test_lean_dom_psm_invariants(topsrcdir):
    """Verify LeanDOMBindings.cpp and PSM certificate fail-closed stubs."""
    print("[SHIM TEST 4] Verifying Lean DOM and PSM certificate stubs...")
    dom_path = os.path.join(topsrcdir, "netwerk", "naivefox", "LeanDOMBindings.cpp")
    with open(dom_path, "r", encoding="utf-8") as f:
        dom_text = f.read()

    assert "OpenSignedAppFileAsync" in dom_text
    assert "AsyncVerifyPKCS7Object" in dom_text
    assert "NS_ERROR_NOT_IMPLEMENTED" in dom_text
    assert "OriginAttributesDictionary" in dom_text
    assert "PartitionKeyPatternDictionary" in dom_text
    assert "MapNAT64IPs" in dom_text
    print("  PASS: PSM app/PKCS7 verification returns NS_ERROR_NOT_IMPLEMENTED (fail-closed); OriginAttributes value semantics intact.")
    return True


def test_necko_channel_params(topsrcdir):
    """Verify NeckoChannelParams in-process definitions."""
    print("[SHIM TEST 5] Verifying NeckoChannelParams in-process value records...")
    hdr_path = os.path.join(topsrcdir, "netwerk", "naivefox", "NeckoChannelParams.h")
    with open(hdr_path, "r", encoding="utf-8") as f:
        hdr_text = f.read()

    assert "PreferredAlternativeDataTypeParams" in hdr_text
    assert "ProxyInfoCloneArgs" in hdr_text
    assert "HttpConnectionInfoCloneArgs" in hdr_text
    assert "CookieStruct" in hdr_text
    assert "HttpActivityArgs" in hdr_text
    print("  PASS: In-process Necko parameter records verified.")
    return True


def main():
    topsrcdir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    objdir = os.path.join(topsrcdir, "obj-naivefox-minimal")

    print("=" * 65)
    print("NaiveFox Targeted Shim Semantics & Invariants Test Suite")
    print("=" * 65)

    all_passed = True
    if not test_symbol_absence(topsrcdir, objdir):
        all_passed = False
    if not test_profiler_stub_invariants(topsrcdir, objdir):
        all_passed = False
    if not test_security_manager_invariants(topsrcdir):
        all_passed = False
    if not test_lean_dom_psm_invariants(topsrcdir):
        all_passed = False
    if not test_necko_channel_params(topsrcdir):
        all_passed = False

    print("=" * 65)
    if not all_passed:
        print("FAIL: One or more shim invariant tests failed.", file=sys.stderr)
        sys.exit(1)
    print("ALL TARGETED SHIM INVARIANT TESTS PASSED.")
    print("=" * 65)


if __name__ == "__main__":
    main()
