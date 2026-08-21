# Pre-Export Stabilization & Full Source-Closure Audit Report

This document records the definitive technical and architectural audit of the `minimal` branch prior to code tree export (`minimal-source`).

## Current gate status (2026-08-20)

The stability blockers that motivated this audit are closed: malformed
SOCKS5/HTTP terminal parsing is bounded, Windows wide-path file logging works,
and the native staged Windows package survived five repeated local stress runs
plus a 600-second strict-H3 soak. The soak completed 45/45 integrity-checked
requests, recorded 45 H3 and 45 padding events, sampled the process for 593
seconds, and ended with RSS 27.5 MiB (peak 28.3 MiB).

Source-closure discovery and standalone target validation are complete.
Early attempts incorrectly used clean copy operations as a dependency-discovery
loop. They produced a disposable, contaminated diagnostic tree and repeatedly
stopped on inputs that compiler/link reports cannot observe. That workflow is
retired. The diagnostic tree was augmented in place by input *classes* and
completed full Linux and Windows builds with the original Firefox checkout
hidden. Linux then passed the complete H2/H3/Auto/config suite and staged
runtime gate. Windows passed native runtime/config/logging/malformed-input
checks and short H2/H3/Auto integrity workloads. The diagnostic tree is not a
valid release export and will never be published.

The four build/closure reports plus Linux configure trace attest
`745d58bf7dcb44df0b8be87b39fb7d21d19383f9`; report-only snapshot
`bec198a62d422b1382315f335ef2965b429d9387` froze the original five-report set.
The target-specific Windows configure trace attests
`af716bf57f83ebdb377c0f34cd20995faf41b641`. The planner consumes all six
reports and at exporter checkpoint `4db1292e96ec` validates 25,549 files and 37
directory contracts. Publication is a clean deterministic snapshot, not a new
dependency-discovery run.

Capture comparison is outside the export gates. When explicitly requested,
both H2 and H3 runners compare ordinary Firefox and NaiveFox packages built
from the same Firefox base in isolated locations. The historical official
release capture remains diagnostic evidence only; the normal export workflow
does not build or fetch an ordinary Firefox browser package.

The Auto protocol gate had one reproducible startup abort while repeatedly
reusing the H3 fixture profile. The lean `MOZ_NAIVEFOX` preferences adapter was
passing an adopted exact-size buffer to the Rust parser without reasserting
its trailing EOF byte. NF-UPSTREAM-016 fixes that invariant; the minimal
binary was rebuilt and the isolated Auto matrix passed with no parser panic or
segmentation fault.

The cross-platform rebuild of `xul.dll` after NF-UPSTREAM-016 completed with
zero compiler warnings. The native Windows smoke was repeated against the
refreshed package: version/runtime startup, five SOCKS sessions, HTTP CONNECT,
malformed-input bounded stress, and relative/absolute/Unicode append logging
all passed.

The cheap closure pass is now complete. The browser-wide Glean metrics/pings
index is disabled for `MOZ_NAIVEFOX`; only the retained 23 metric schemas,
`netwerk/pings.yaml`, and the shared tag vocabulary are generated. The active
Cargo tree has no `firefox-on-glean`/`glean-core` runtime crates. Five direct
Rust dependencies proven unused were removed (`fluent-langneg`, `ipcclientcerts`,
`ipdl_utils`, `oblivious_http`, and `rusqlite`); `jsrust_shared` was deliberately
retained because removing it produced unresolved SpiderMonkey encoding symbols.
The remaining large closure is intentional: SpiderMonkey and ICU are recorded
as a future size milestone, not guessed away in this audit.
The older 366/379 counts came from Cargo's workspace-unified metadata and are
stale for this parent-only build. The reports distinguish the 271/287
runtime-reachable packages from the 311/325 source/build package closure.

---

## 1. Release & Baseline Provenance

- **Validated Firefox Base Commit:** `8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6`
- **Validated NaiveFox Baseline Commit:** `2a539d796d1a1d134ec64739c69b61f443132a3c` (historical full-tree baseline)
- **Standalone Diagnostic Source Commit:** `a020da3d5ba4`; standalone full build and runtime smoke passed from source content through this checkpoint.
- **Audited Minimal Evidence Source Commit:** `745d58bf7dcb44df0b8be87b39fb7d21d19383f9`.
- **Evidence Report Snapshot Commit:** `bec198a62d422b1382315f335ef2965b429d9387`; this direct report-only child contains the five configure/build/closure reports.
- **Validated exporter/code checkpoint:** `4db1292e96ec97fa39575e936e76608a711dbdb5`
- **Validated Minimal export source:** `b7f3b5bc67fcf155b569a0d0d2ad0f7f28cd45be`
- **Published Minimal Source Commit:** `31c1813e26cf652835dc73eaafef9f0fa84002f9`
  (independent root commit; tag `minimal-source-v0.1`).
- **Export manifest SHA-256:** `04da2cd33beda6dca9727a5b681f7b0f2cc8f30b2417ceaa386ede43c5cdf140`
- **Pre-Audit Graph Checkpoint Tag:** historical checkpoint retained only in Git history; it is not part of current provenance.

---

## 2. Multi-Target Link and Source Closure Summary

Audited with `netwerk/naivefox/tools/analyze-full-closure.py` and strictly validated with `netwerk/naivefox/tools/assert-closure.py`.

The final report set is current export evidence. Runtime-reachable Rust closure
is 271/287 packages; the larger normal/build/proc-macro source closure is
311/325 packages. Linux and Windows reports share audited source `745d58bf`;
counts are measurements, not manifest contracts. Export policy consumes the
union of validated target-specific reports rather than hard-coded counts.

### Closure Report Archives
- Linux configure: `netwerk/naivefox/reports/configure-inputs-linux-x86_64.json`
- Windows configure: `netwerk/naivefox/reports/configure-inputs-windows-x86_64.json`
- Linux build inputs: `netwerk/naivefox/reports/build-inputs-linux-x86_64.json`
- Windows build inputs: `netwerk/naivefox/reports/build-inputs-windows-x86_64.json`
- Linux: `netwerk/naivefox/reports/closure-report-linux-x86_64.json`
- Windows: `netwerk/naivefox/reports/closure-report-windows-x86_64.json`

---

## 3. 3-Tier Build Performance Breakdown

Benchmarked on 16-thread development workstation:

1. **Incremental Build (`./mach build binaries`):** **~2.85s**
2. **Clean objdir Build with Warm Compiler Cache (`sccache`):** **~36s**
3. **True Cold Clean Build without Cache (`NAIVEFOX_DISABLE_SCCACHE=1`):** **1m 16.268s** (`real 1m16.268s`)

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
Automated NaiveFox product gates executed via live Caddy fixture
(`test/integration/run-full-suite.sh` and `tools/verify-staged-runtime.sh`)
exclude the optional ordinary-Firefox capture control:
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
- `[PASS]` standalone native H2 workload: 8/8 integrity requests, SOCKS5 +
  HTTP CONNECT, parallelism four, strict H2 and padding records
- `[PASS]` standalone native H3 workload: 8/8 integrity requests, SOCKS5 +
  HTTP CONNECT, parallelism four, strict H3/no H2 fallback and padding records
- `[PASS]` standalone native Auto workload: 8/8 integrity requests, H3 selected
- `[PASS]` Clean process termination and shutdown
- *Note:* no random crash recurred in the smoke, historical soak, or current
  H2/H3/Auto evidence, so no hypothetical profiler race is pursued without a
  new symbolized crash reproduction.

---

## 6. Known Limitations

1. **IPv6-Only NAT64 Environments:** The lean implementation of `NetworkConnectivityService::MapNAT64IPs` performs pass-through mapping. IPv6-only NAT64 synthesis environments are not yet validated or supported.
2. **Interactive UI Dialogs:** Interactive client certificate prompts, master password dialogs, and external protocol handlers fail-closed with explicit error codes.

---

## 7. Source-Export Requirements for `minimal-source`

1. **Boundary Definition:** DOM implementation and layout engines are excluded. Explicit minimal WebIDL, binding metadata, and code generator subsets are retained where required.
2. **Build-Time Dependency Inclusion:** The source export manifest must include all build-time generators, python actions, and dependency metadata even if they do not compile into the final runtime binary.
3. **Allowlist Integrity:** Do not hard-code object/crate counts. Generate the
   allowlist from the union of the validated Linux and Windows reports:
   `cxx_translation_units`, headers, Rust `source_paths`, Cargo manifests,
   generated inputs, and runtime resource sources. The counts in each report
   are measurements, not an export contract.
4. **Discovery Is Not Export:** Determine closure in one disposable diagnostic
   tree, updated in place. A clean export must never be restarted merely to
   discover another missing file.
5. **Evidence Union:** Use the attested configure trace, Linux/Windows backend
   and config-status inputs, all compiler/generated-action depfiles, generated
   Makefile prerequisites, target-filtered Cargo build closure, project files,
   bootstrap inputs, runtime resources, and licenses.
6. **One Clean Gate:** The physical exporter runs once after diagnostic build
   and `--plan-only` are green. That output is immutable and either passes the
   isolated acceptance gate or invalidates the manifest as a class, never by
   manual patching of generated source.
