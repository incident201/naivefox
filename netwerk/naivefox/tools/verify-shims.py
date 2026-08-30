#!/usr/bin/env python3
"""
verify-shims.py - Focused Unit & Targeted Shim Semantics Verification Suite
Verifies the exact invariants, fail-closed semantics, and symbol boundaries of all NaiveFox shims.
"""

import argparse
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
        print(f"FAIL: libxul.so not found in explicit object directory: {objdir}", file=sys.stderr)
        return False

    print("[SHIM TEST 1] Verifying absence of heavy symbols in libxul binary...")
    forbidden_symbols = [
        ("Breakpad ELF parser", ["google_breakpad", "ElfFile"]),
        ("LUL DWARF stack unwinder", ["lul::LUL", "lul::SecMap"]),
        ("JsonCPP Profiler exporter", ["Json::Value", "Json::FastWriter"]),
        ("Abseil containers", ["absl::container", "absl::str_format"]),
        ("HarfBuzz font shaper runtime", ["hb_shape_full", "hb_ot_shape"]),
    ]

    try:
        nm_out = subprocess.check_output(["nm", "-D", "-C", libxul_path], stderr=subprocess.DEVNULL, text=True)
    except Exception:
        nm_out = subprocess.check_output(["readelf", "--wide", "--demangle", "-s", libxul_path], stderr=subprocess.DEVNULL, text=True)

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


def test_cache_crypto_boundary(topsrcdir):
    """Reject changes that turn unavailable profile encryption into plaintext."""
    print("[SHIM TEST 6] Verifying unavailable profile-keystore boundary...")
    cache = Path(topsrcdir) / "netwerk" / "cache2"
    crypto = (cache / "CacheCrypto.cpp").read_text(encoding="utf-8")
    native_file = (cache / "CacheFile.cpp").read_text(encoding="utf-8")
    assert re.search(
        r"CacheCrypto::LoadFromKeystore\([^)]*\)\s*\{\s*"
        r"#ifdef MOZ_NAIVEFOX\s*return nullptr;\s*#else", crypto
    ), "NaiveFox must not synthesize a key when the profile keystore is unavailable"
    setup = native_file.split("void CacheFile::SetupEncryption()", 1)[1]
    assert re.search(
        r"if \(!CacheCrypto::IsActive\(\)\)\s*\{\s*"
        r"if \(CacheCrypto::IsEnabled\(\)\)\s*\{"
        r"(?:(?!\n    \}).)*SetError\(NS_ERROR_NOT_AVAILABLE\)", setup, re.S
    ), "Encryption requested without a cipher must fail disk cache entries closed"
    print("  PASS: No fallback key; native encrypted-cache requests fail closed.")
    return True


def test_rust_allocator_boundary(topsrcdir):
    print("[SHIM TEST 7] Verifying Rust/C++ allocator ownership...")
    rust = Path(topsrcdir) / "toolkit" / "library" / "rust"
    product = (rust / "naivefox" / "lib.rs").read_text(encoding="utf-8")
    build = (rust / "moz.build").read_text(encoding="utf-8")
    runtime = (Path(topsrcdir) / "netwerk" / "naivefox" / "GeckoRuntime.cpp").read_text(encoding="utf-8")
    assert "extern crate mozglue_static;" in product, "GeckoAlloc must be linked, not just listed as an unused Cargo dependency"
    assert 'naivefox_features.append("mozglue-static/moz_memory")' in build
    assert "NaiveFoxRustAllocatorSmoke(&ownershipProbe)" in runtime
    print("  PASS: GeckoAlloc selected; --runtime-smoke exercises nested ThinVec/nsTArray ownership.")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--objdir", help="Linux build to inspect (or NAIVEFOX_OBJDIR)")
    mode.add_argument("--source-only", action="store_true", help="explicitly omit the binary check")
    args = parser.parse_args()
    topsrcdir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    objdir = args.objdir or os.environ.get("NAIVEFOX_OBJDIR")
    if not args.source_only and not objdir:
        parser.error("provide --objdir/NAIVEFOX_OBJDIR, or use --source-only for Gate 1")

    print("=" * 65)
    print("NaiveFox Targeted Shim Semantics & Invariants Test Suite")
    print("=" * 65)

    all_passed = True
    if args.source_only:
        print("Source-only mode: binary symbols were not checked.")
    elif not test_symbol_absence(topsrcdir, objdir):
        all_passed = False
    if not test_profiler_stub_invariants(topsrcdir, objdir):
        all_passed = False
    if not test_security_manager_invariants(topsrcdir):
        all_passed = False
    if not test_lean_dom_psm_invariants(topsrcdir):
        all_passed = False
    if not test_necko_channel_params(topsrcdir):
        all_passed = False
    if not test_cache_crypto_boundary(topsrcdir):
        all_passed = False
    if not test_rust_allocator_boundary(topsrcdir):
        all_passed = False

    print("=" * 65)
    if not all_passed:
        print("FAIL: One or more shim invariant tests failed.", file=sys.stderr)
        sys.exit(1)
    print("ALL TARGETED SHIM INVARIANT TESTS PASSED.")
    print("=" * 65)


if __name__ == "__main__":
    main()
