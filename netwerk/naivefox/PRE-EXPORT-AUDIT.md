# Pre-Export Stabilization & Full Source-Closure Audit Report

This document records the definitive technical and architectural audit of the `minimal` branch prior to code tree export (`minimal-source`).

---

## 1. Release & Baseline Provenance

- **Validated Firefox Base Commit:** `8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6`
- **Validated NaiveFox Baseline Commit:** `2a539d796d1a1d134ec64739c69b61f443132a3c`
- **Validated Minimal Base Commit:** `8e2d123c9a61`
- **Validated Minimal Source Commit:** `NOT_CREATED`
- **Pre-Audit Graph Checkpoint Tag:** `minimal-graph-v0.1` (`60f2eede69da856daf2324fc90b2c2ab9cb86fd2`)

---

## 2. Multi-Target Link and Source Closure Summary

Audited with `netwerk/naivefox/tools/analyze-full-closure.py` and strictly validated with `netwerk/naivefox/tools/assert-closure.py`.

| Dimension | Linux x86_64 (`obj-naivefox-minimal`) | Windows x86_64 (`obj-naivefox-windows-x86_64`) |
|---|---|---|
| **Target Triple** | `x86_64-unknown-linux-gnu` | `x86_64-pc-windows-msvc` |
| **C/C++ Translation Units** | **934 TUs** (parsed from compiler `.deps`) | **957 TUs** (parsed from compiler `.deps`) |
| **Direct Link Objects** | **525 object files** (216.03 MB unstripped) | **536 object files** (202.62 MB unstripped) |
| **Main Binary Size** | 615.49 MB unstripped / **62.0 MB stripped** | **40.94 MB** (`xul.dll`) |
| **Headless Executable** | 1.05 MB (`naivefox`) | 11.0 KB (`naivefox.exe`) |
| **Static Libraries** | 3 archives (`js_static`, `gkrust`, `pure_virtual`) | 3 archives (`js_static.lib`, `gkrust.lib`, `pure_virtual.lib`) |
| **Reachable Rust Crates** | **396 packages** (filtered reachable from `gkrust`) | **396 packages** (filtered reachable from `gkrust`) |
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
- `[PASS]` Clean process termination and shutdown
- *Note:* Windows build, launch, config parsing, local listener handshake and shutdown are fully verified. End-to-end H2/H3 networking against live upstream proxy is tracked separately.

---

## 6. Known Limitations

1. **IPv6-Only NAT64 Environments:** The lean implementation of `NetworkConnectivityService::MapNAT64IPs` performs pass-through mapping. IPv6-only NAT64 synthesis environments are not yet validated or supported.
2. **Interactive UI Dialogs:** Interactive client certificate prompts, master password dialogs, and external protocol handlers fail-closed with explicit error codes.

---

## 7. Source-Export Requirements for `minimal-source`

1. **Boundary Definition:** DOM implementation and layout engines are excluded. Explicit minimal WebIDL, binding metadata, and code generator subsets are retained where required.
2. **Build-Time Dependency Inclusion:** The source export manifest must include all build-time generators, python actions, and dependency metadata even if they do not compile into the final runtime binary.
3. **Allowlist Integrity:** Do not generate export allowlists solely from the 525 direct object files. Retain all 934 C/C++ source units, 396 reachable Rust crates, and active code generators.
