# Pre-Export Stabilization & Full Source-Closure Audit Report

This document records the definitive technical and architectural audit of the `minimal` branch prior to code tree export (`minimal-source`).

## Current gate status (2026-08-20)

The source export is **not unlocked**. The stability blockers that motivated
this audit are now closed: malformed SOCKS5/HTTP terminal parsing is bounded,
Windows wide-path file logging works, and the native staged Windows package
survived five repeated local stress runs plus a 600-second strict-H3 soak.
The soak completed 45/45 integrity-checked requests, recorded 45 H3 and 45
padding events, sampled the process for 593 seconds, and ended with RSS 27.5
MiB (peak 28.3 MiB). The remaining gate is audit/provenance finalization and a
clean standalone export/build; `tools/export-minimal-source.sh` has not been
run.

The capture reference policy is also final: both H2 and H3 capture runners
fetch a clean official Mozilla Firefox release into ignored object storage via
`tools/fetch-firefox-reference.sh`. They do not require a full Firefox package
or source objdir to be present. Exact TLS/QUIC field equality against the
pinned NaiveFox snapshot is diagnostic only; protocol ownership and strict
transport assertions remain gates.

The Auto protocol gate had one reproducible startup abort while repeatedly
reusing the H3 fixture profile. The lean `MOZ_NAIVEFOX` preferences adapter was
passing an adopted exact-size buffer to the Rust parser without reasserting
its trailing EOF byte. NF-UPSTREAM-016 fixes that invariant; the minimal
binary was rebuilt and the isolated Auto matrix passed with no parser panic or
segmentation fault.

---

## 1. Release & Baseline Provenance

- **Validated Firefox Base Commit:** `8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6`
- **Validated NaiveFox Baseline Commit:** `2a539d796d1a1d134ec64739c69b61f443132a3c` (historical full-tree baseline)
- **Validated Minimal Audit Source Commit:** `3e395eb1aca9fc73af7bfeaa6076e929f80b8ff0`
- **Validated Minimal Report Commit:** report-only child of the audit source commit (exact SHA is recorded in the report provenance)
- **Validated Minimal Source Commit:** `NOT_CREATED`
- **Pre-Audit Graph Checkpoint Tag:** `minimal-graph-v0.1` (`60f2eede69da856daf2324fc90b2c2ab9cb86fd2`)

---

## 2. Multi-Target Link and Source Closure Summary

Audited with `netwerk/naivefox/tools/analyze-full-closure.py` and strictly validated with `netwerk/naivefox/tools/assert-closure.py`.

| Dimension | Linux x86_64 (`obj-naivefox-minimal`) | Windows x86_64 (`obj-naivefox-windows-x86_64`) |
|---|---|---|
| **Target Triple** | `x86_64-unknown-linux-gnu` | `x86_64-pc-windows-msvc` |
| **C/C++ Translation Units** | **545 TUs** (clean audited depfiles) | **468 TUs** (clean audited depfiles) |
| **Direct Link Objects** | **525 object files** (216.03 MB unstripped) | **536 object files** (202.62 MB unstripped) |
| **Main Binary Size** | 615.49 MB unstripped / **62.0 MB stripped** | **40.94 MB** (`xul.dll`) |
| **Headless Executable** | 1.05 MB (`naivefox`) | 11.0 KB (`naivefox.exe`) |
| **Static Libraries** | 3 archives (`js_static`, `gkrust`, `pure_virtual`) | 3 archives (`js_static.lib`, `gkrust.lib`, `pure_virtual.lib`) |
| **Reachable Rust Crates** | **366 packages** (filtered reachable from `gkrust`) | **379 packages** (filtered reachable from `gkrust`) |
| **Dynamic Dependencies** | **20 `DT_NEEDED`** (glibc, glib, dbus, nspr, nss, sqlite) | **22 DLL imports** (Win32 API, nspr, nss, sqlite) |
| **Desktop UI Libraries (GTK/X11)** | **0 libraries linked** | **0 libraries linked** |
| **Staged Runtime Package** | **18 files** (27.91 MB archive) | **21 files** (19.34 MB archive) |

### Closure Report Archives
- Linux: `netwerk/naivefox/reports/closure-report-linux-x86_64.json`
- Windows: `netwerk/naivefox/reports/closure-report-windows-x86_64.json`

---

## 3. 3-Tier Build Performance Breakdown

Benchmarked on 16-thread development workstation:

1. **Incremental Build (`./mach build binaries`):** **~2.85s**
2. **Clean objdir Build with Warm Compiler Cache (`sccache`):** **~36s**
3. **True Cold Clean Build without Cache (`SCCACHE_DISABLE=1`):** **1m 16.268s** (`real 1m16.268s`)

---

## 4. Architectural Boundaries & Shims Audit

Detailed in [`netwerk/naivefox/SHIMS.md`](SHIMS.md) and verified by [`netwerk/naivefox/tools/verify-shims.py`](tools/verify-shims.py):

- **Gecko Profiler:** Replaced by no-op compatibility stub [`netwerk/naivefox/core/ProfilerNaiveFoxStub.cpp`](core/ProfilerNaiveFoxStub.cpp) and `profiler_noop.rs`. DWARF unwinder (LUL), Breakpad ELF parser, and `jsoncpp` are completely eliminated from `libxul`.
- **Lean DOM Bindings:** [`netwerk/naivefox/LeanDOMBindings.cpp`](LeanDOMBindings.cpp) handles `OriginAttributes` and partition keys for network isolation. PSM interactive UI dialogs and PKCS7/App verify return `NS_ERROR_NOT_IMPLEMENTED` (fail-closed).
- **Security Manager:** [`caps/nsScriptSecurityManagerNaiveFox.cpp`](../../caps/nsScriptSecurityManagerNaiveFox.cpp) implements trusted SystemPrincipal security checks. Non-SystemPrincipal loads fail-closed (`NS_ERROR_DOM_BAD_URI`).
- **In-Process Necko Parameters:** [`netwerk/naivefox/NeckoChannelParams.h`](NeckoChannelParams.h) implements lean in-process parameter passing, removing multi-process IPDL channel serialization.
- **HarfBuzz Upstream:** `gfx/harfbuzz/src/moz.build` is 100% pristine untouched Mozilla code; 28 HarfBuzz public headers are exported directly in `netwerk/naivefox/app.mozbuild`.

---

## 5. Verification Status

### Linux x86_64 Status: Fully Verified
Automated test gates executed via live Caddy fixture (`test/integration/run-full-suite.sh` and `tools/verify-staged-runtime.sh`):
- `[PASS]` SOCKS5 listener (IPv4/IPv6, TCP CONNECT, domain names)
- `[PASS]` HTTP CONNECT listener
- `[PASS]` Multiplexed H2 proxying with padding negotiation
- `[PASS]` Multiplexed H3 proxying with 0-RTT/1-RTT QUIC
- `[PASS]` Auto-protocol mode with H3 preference and transparent bounded H2 fallback
- `[PASS]` Robustness, backpressure, connection hang recovery, and soak
- `[PASS]` Staged package verification outside objdir (`verify-staged-runtime.sh`)

### Windows x86_64 Status: Build & Local Runtime Verified
Automated smoke test executed via `tools/verify-staged-windows-smoke.py`:
- `[PASS]` `--version` check (`NaiveFox 0.3.0-dev`)
- `[PASS]` `--runtime-smoke` headless event-loop lifecycle
- `[PASS]` `config.json` loading, parsing, and error validation
- `[PASS]` Dynamic port SOCKS5 listener startup & handshake (`0x05 0x00`)
- `[PASS]` 5 consecutive client SOCKS5 sessions
- `[PASS]` Dynamic port HTTP CONNECT listener startup & request validation
- `[PASS]` malformed SOCKS/HTTP bounded stress, including 2 MiB same-write tail,
  200 non-reading rejects, and post-stress normal handshake
- `[PASS]` relative, absolute, and Unicode file logging with append/restart and
  credential scan
- `[PASS]` five repeated native Windows smoke iterations without unexpected exit
- `[PASS]` 600-second strict H3 real-Caddy soak: 45/45 integrity requests,
  45/45 H3 and padding records, 593 resource samples
- `[PASS]` Clean process termination and shutdown
- *Note:* strict H3 networking is now covered by the native soak. A separate
  full native H2/Auto matrix and crash-dump-backed long churn run remain
  follow-up evidence; no random crash recurred in the repeated smoke/soak gate.

---

## 6. Known Limitations

1. **IPv6-Only NAT64 Environments:** The lean implementation of `NetworkConnectivityService::MapNAT64IPs` performs pass-through mapping. IPv6-only NAT64 synthesis environments are not yet validated or supported.
2. **Interactive UI Dialogs:** Interactive client certificate prompts, master password dialogs, and external protocol handlers fail-closed with explicit error codes.

---

## 7. Source-Export Requirements for `minimal-source`

1. **Boundary Definition:** DOM implementation and layout engines are excluded. Explicit minimal WebIDL, binding metadata, and code generator subsets are retained where required.
2. **Build-Time Dependency Inclusion:** The source export manifest must include all build-time generators, python actions, and dependency metadata even if they do not compile into the final runtime binary.
3. **Allowlist Integrity:** Do not generate export allowlists solely from the 525 direct object files. Retain all 934 C/C++ source units, 396 reachable Rust crates, and active code generators.
