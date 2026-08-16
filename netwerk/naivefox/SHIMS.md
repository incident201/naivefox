# NaiveFox Shims, Stubs & Lean Implementations Audit

This document audits every project-specific shim, stub, and lean replacement implemented in NaiveFox to decouple the headless network/proxy runtime from large browser-oriented subsystems (DOM, Layout, Graphics, DevTools, Profiler, Addon Policy).

---

## 1. Summary of Project Shims

| Shim File | Target Subsystem Replaced | Primary Purpose | Classification |
|---|---|---|---|
| [`netwerk/naivefox/core/ProfilerNaiveFoxStub.cpp`](file:///home/zubastik/src/naivefox/netwerk/naivefox/core/ProfilerNaiveFoxStub.cpp) | Gecko Profiler & Breakpad / LUL | Zero-overhead no-op profiler lifecycle, markers & ETW | **2** (Unreachable / No-op ABI) |
| [`netwerk/naivefox/LeanDOMBindings.cpp`](file:///home/zubastik/src/naivefox/netwerk/naivefox/LeanDOMBindings.cpp) | Full DOM Bindings, JSRuntime & PSM Dialogs | Non-JS `OriginAttributes` dictionary and headless stubs | **1 & 3** (Minimal impl & Fail-closed) |
| [`caps/nsScriptSecurityManagerNaiveFox.cpp`](file:///home/zubastik/src/naivefox/caps/nsScriptSecurityManagerNaiveFox.cpp) | Browser Script Security & Content Principals | SystemPrincipal-only security manager for headless proxy | **1 & 3** (Minimal impl & Fail-closed) |
| [`xpcom/build/PoisonIOInterposerStub.cpp`](file:///home/zubastik/src/naivefox/xpcom/build/PoisonIOInterposerStub.cpp) | Poison IO Interposer / Main thread I/O checks | No-op I/O poison interposition | **2** (No-op compatibility) |
| [`toolkit/xre/AutoSQLiteLifetime.cpp`](file:///home/zubastik/src/naivefox/toolkit/xre/AutoSQLiteLifetime.cpp) | Full Toolkit XRE AppRunner | Minimal SQLite thread initialization / shutdown | **1** (Real minimal impl) |
| [`js/xpconnect/src/XPCString.cpp`](file:///home/zubastik/src/naivefox/js/xpconnect/src/XPCString.cpp) | Full XPConnect DOM JS conversion | Compact JS-to-XPCOM string conversions | **1** (Real minimal impl) |
| [`ipc/glue/NeckoChannelParams.h`](file:///home/zubastik/src/naivefox/ipc/glue/NeckoChannelParams.h) | Multi-process IPDL channel serialization | In-process parameter passing types | **1** (Real minimal impl) |

---

## 2. Detailed Audit Per Component

### 2.1. `ProfilerNaiveFoxStub.cpp`

- **File:** `netwerk/naivefox/core/ProfilerNaiveFoxStub.cpp`
- **Replaced Upstream Subsystem:** Gecko Profiler (`tools/profiler`), LUL (Lightweight Unwind Library DWARF stack unwinder), Breakpad ELF parser, JSON profile serialization (`toolkit/components/jsoncpp`).
- **Required Symbols / API:**
  - `profiler_init()`, `profiler_shutdown()`
  - `profiler_start()`, `profiler_stop()`, `profiler_pause()`, `profiler_resume()`
  - `profiler_register_thread()`, `profiler_unregister_thread()`
  - `profiler_get_core_buffer()`, `AddMarkerToBuffer()`
  - Windows ETW provider handles: `ETW::gETWCollectionMask`, `kFirefoxTraceLoggingProvider`, `ETW::Init()`, `ETW::Shutdown()`
- **Known Callers:** NSPR thread hooks, XPCOM thread initialization, pref observers, network marker macros (`PROFILER_MARKER`).
- **Supported Semantics:**
  - Thread registration is a clean no-op returning `nullptr`.
  - Buffer access returns a static inactive `ProfileChunkedBuffer`.
  - ETW mask is statically zero (all markers rejected early before formatting).
- **Unsupported Semantics:**
  - Stack sampling, active profile capturing, JSON trace exporting.
- **Failure Behavior:**
  - `profiler_start()` returns a resolved generic promise to avoid crashing any async promise listener, but leaves features inactive (`profiler_feature_active` returns `false`).
- **Classification:** **Case 2** (Compatibility symbols with proved inactive state).
- **Upstream Source to Watch on Refresh:** `tools/profiler/public/GeckoProfiler.h`, `tools/profiler/public/ETWTools.h`.
- **Tests:** Headless smoke test, staged runtime integration suite.

---

### 2.2. `LeanDOMBindings.cpp`

- **File:** `netwerk/naivefox/LeanDOMBindings.cpp`
- **Replaced Upstream Subsystem:** Generated DOM bindings (`dom/bindings`), full `CycleCollectedJSRuntime`, interactive PSM UI dialogs (`ShowProtectedAuthDialog`), and DOM Promise FFI.
- **Required Symbols / API:**
  - `dom::OriginAttributesDictionary` copy, compare, and assign operators.
  - `dom::PartitionKeyPatternDictionary`, `dom::OriginAttributesPatternDictionary`.
  - `NetworkConnectivityService::MapNAT64IPs()`, `GetSingleton()`.
  - `CycleCollectedJSRuntime` stubs (`AreGCGrayBitsValid`, `TraverseRoots`).
  - PSM certificate stubs: `nsNSSCertificateDB::OpenSignedAppFileAsync`, `AsyncVerifyPKCS7Object`.
- **Known Callers:** Necko cookie storage (`nsICookieJarSettings`), Necko origin attributes computation, PSM certificate database init.
- **Supported Semantics:**
  - Full value-semantic handling of `OriginAttributes` and partition keys for network request isolation and cookie jars.
  - NAT64 IPv4-to-IPv6 mapping pass-through (`MapNAT64IPs` returns addrefed original RRSet).
- **Unsupported Semantics:**
  - DOM JS object reflection of origin attributes dictionaries (`Init` returns `false`).
  - App signature verification and interactive auth dialogs.
- **Failure Behavior:**
  - PSM interactive/app certificate verification APIs return `NS_ERROR_NOT_IMPLEMENTED` (fail-closed).
- **Classification:** **Case 1** for `OriginAttributes` & NAT64; **Case 3** for PKCS7/App verify (fail-closed).
- **Upstream Source to Watch on Refresh:** `dom/bindings/parser/WebIDL.py`, `netwerk/base/OriginAttributes.h`.
- **Tests:** `verify-staged-runtime.sh` (cookie handling and origin isolation), H2/H3 proxy suites.

---

### 2.3. `nsScriptSecurityManagerNaiveFox.cpp`

- **File:** `caps/nsScriptSecurityManagerNaiveFox.cpp`
- **Replaced Upstream Subsystem:** Browser script security manager (`caps/nsScriptSecurityManager.cpp`), DocShell content principal factory, addon content security policies.
- **Required Symbols / API:**
  - `nsIScriptSecurityManager` interface.
  - `GetSystemPrincipal()`, `SystemPrincipalSingletonConstructor()`.
  - `CheckLoadURIWithPrincipal()`, `CheckLoadURIStrWithPrincipal()`.
  - `GetChannelResultPrincipal()`, `GetChannelURIPrincipal()`.
- **Known Callers:** Necko channel creation, socket transport service, PSM TLS verification channel bindings.
- **Supported Semantics:**
  - SystemPrincipal singleton creation and lifetime management.
  - URI equality comparisons (`SecurityCompareURIs`, `IsHttpOrHttpsAndCrossOrigin`).
  - SystemPrincipal loads succeed immediately (`CheckLoadURIWithPrincipal` returns `NS_OK` for SystemPrincipal).
- **Unsupported Semantics:**
  - Content principals, Null principals, Expanded principals, DocShell principals.
  - Domain policies and file URI allowlists.
- **Failure Behavior:**
  - Non-SystemPrincipal loads return `NS_ERROR_DOM_BAD_URI` (fail-closed).
  - Principal creation from untrusted origins or JSON returns `NS_ERROR_NOT_AVAILABLE`.
- **Classification:** **Case 1** (Minimal headless SystemPrincipal implementation) & **Case 3** (Fail-closed on unprivileged principals).
- **Upstream Source to Watch on Refresh:** `caps/nsIScriptSecurityManager.idl`, `caps/BasePrincipal.h`.
- **Tests:** Full proxy regression suite (SOCKS5, HTTP CONNECT, H2, H3).

---

### 2.4. `PoisonIOInterposerStub.cpp`

- **File:** `xpcom/build/PoisonIOInterposerStub.cpp`
- **Replaced Upstream Subsystem:** `xpcom/build/PoisonIOInterposer.cpp` (debug assertions on main-thread blocking file I/O).
- **Required Symbols / API:**
  - `mozilla::InitPoisonIOInterposer()`, `mozilla::ClearPoisonIOInterposer()`
  - `mozilla::PoisonIOInterposer::Register()`, `Unregister()`
- **Known Callers:** XPCOM shutdown hooks.
- **Supported Semantics:**
  - No-op initialization and deregistration.
- **Unsupported Semantics:**
  - Thread I/O trap interception.
- **Failure Behavior:** No-op (no side effects).
- **Classification:** **Case 2** (Compatibility symbol).
- **Upstream Source to Watch on Refresh:** `xpcom/build/PoisonIOInterposer.h`.
- **Tests:** Incremental build and shutdown clean verification.

---

### 2.5. `NeckoChannelParams.h` & IPC In-Process Stubs

- **File:** `ipc/glue/NeckoChannelParams.h`
- **Replaced Upstream Subsystem:** Multi-process IPDL channel parameter serialization.
- **Required Symbols / API:**
  - Core Necko request headers, load info parameters, and cookie header structures.
- **Supported Semantics:** In-process parameter passing across thread boundaries.
- **Unsupported Semantics:** Cross-process serialized IPC messages.
- **Classification:** **Case 1** (Real minimal in-process implementation).

---

## 3. Strict Closure Assertions

The following assertions are enforced on the build and link graph:
1. **No heavy Gecko Profiler objects:** LUL DWARF unwinder, Breakpad ELF parser, CPU counters are excluded.
2. **No JsonCPP:** `toolkit/components/jsoncpp` is completely absent from `USE_LIBS` and link closure.
3. **No Google Abseil:** `config/external/abseil-cpp` is completely absent from link closure.
4. **No HarfBuzz implementation objects:** `gfx/harfbuzz` compiles 0 translation units into `libxul`.
5. **No GTK/X11/Cairo/Pango/ATK `DT_NEEDED`:** `libxul.so` links zero graphical system libraries.
