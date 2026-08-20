# NaiveFox Shims, Stubs & Lean Compatibility Layer Audit

This document is the authoritative technical reference and security audit for all compatibility shims, stubs, and lean replacements implemented in NaiveFox. These components decouple the headless network/proxy runtime from large browser-oriented subsystems (DOM, Layout, Graphics, DevTools, Profiler, Addon Policies, WebRTC, Multi-process IPDL).

---

## 1. Inventory & Classification Matrix

| Shim / Compatibility Component | Target Subsystem Replaced | Primary Purpose | Risk Rating | Classification |
|---|---|---|---|---|
| [`netwerk/naivefox/core/ProfilerNaiveFoxStub.cpp`](core/ProfilerNaiveFoxStub.cpp) | Gecko Profiler, LUL DWARF unwinder, Breakpad | Zero-overhead no-op profiler lifecycle & ETW stubs | **Low** | Case 2 (Unreachable / No-op ABI) |
| [`netwerk/protocol/http/happy_eyeballs_glue/src/profiler_noop.rs`](../protocol/http/happy_eyeballs_glue/src/profiler_noop.rs) | Rust Gecko Profiler FFI | No-op Rust profiler marker bindings | **Low** | Case 2 (No-op compatibility) |
| [`netwerk/naivefox/LeanDOMBindings.cpp`](LeanDOMBindings.cpp) | DOM Bindings, JSRuntime & PSM UI | `OriginAttributes` dictionary semantics & headless PSM stubs | **Contained** | Case 1 (Minimal impl) & Case 3 (Fail-closed) |
| [`caps/nsScriptSecurityManagerNaiveFox.cpp`](../../caps/nsScriptSecurityManagerNaiveFox.cpp) | Browser Script Security Manager | SystemPrincipal-only security manager for proxy pipeline | **Contained** | Case 1 (Minimal impl) & Case 3 (Fail-closed) |
| [`netwerk/naivefox/NeckoChannelParams.h`](NeckoChannelParams.h) | Multi-process IPDL channel serialization | In-process parameter passing value records | **Low** | Case 1 (Real in-process minimal impl) |
| [`xpcom/base/MemoryReportingMinimal.cpp`](../../xpcom/base/MemoryReportingMinimal.cpp) | Full Memory Reporter Manager | Compact memory reporter stubs for XPCOM | **Low** | Case 2 (No-op compatibility) |
| [`xpcom/build/PoisonIOInterposerStub.cpp`](../../xpcom/build/PoisonIOInterposerStub.cpp) | Poison IO Interposer / Main thread I/O traps | No-op I/O poison interposition | **Low** | Case 2 (No-op compatibility) |
| [`netwerk/protocol/http/OpaqueResponseUtilsNaiveFox.cpp`](../protocol/http/OpaqueResponseUtilsNaiveFox.cpp) | Full ORB/CORB Validator | Headless opaque response handling for Necko channels | **Low** | Case 1 (Minimal impl) |
| [`toolkit/xre/AutoSQLiteLifetime.cpp`](../../toolkit/xre/AutoSQLiteLifetime.cpp) | Toolkit XRE AppRunner | Minimal SQLite thread init & shutdown | **Low** | Case 1 (Real minimal impl) |
| [`js/xpconnect/src/XPCString.cpp`](../../js/xpconnect/src/XPCString.cpp) | XPConnect DOM JS Conversion | Compact JS-to-XPCOM string conversions | **Low** | Case 1 (Real minimal impl) |
| **Minimal DOM & Security Headers Group** (see Sec. 2.7) | Browser DOM / ServiceWorker / Permissions | Compile-time type definitions for Necko headers | **Low** | Case 1 (Header-only type stubs) |
| [`netwerk/naivefox/UnknownProtocolHandler.cpp`](UnknownProtocolHandler.cpp) | External protocol handler dialogs | Fail-closed unknown scheme rejection | **Low** | Case 3 (Fail-closed) |

---

## 2. Detailed Technical Audit Per Subsystem

### 2.1. Gecko Profiler Stub (`ProfilerNaiveFoxStub.cpp` & `profiler_noop.rs`)

- **Files:** [`netwerk/naivefox/core/ProfilerNaiveFoxStub.cpp`](core/ProfilerNaiveFoxStub.cpp), [`netwerk/protocol/http/happy_eyeballs_glue/src/profiler_noop.rs`](../protocol/http/happy_eyeballs_glue/src/profiler_noop.rs)
- **Replaced Upstream Subsystem:** Gecko Profiler (`tools/profiler`), LUL (Lightweight Unwind Library DWARF stack unwinder), Breakpad ELF parser, JSON profile serialization (`toolkit/components/jsoncpp`).
- **Required Symbols / API:**
  - `profiler_init()`, `profiler_shutdown()`, `profiler_start()`, `profiler_stop()`, `profiler_pause()`, `profiler_resume()`.
  - `profiler_register_thread()`, `profiler_unregister_thread()`, `profiler_get_core_buffer()`, `AddMarkerToBuffer()`.
  - Windows ETW provider handles: `ETW::gETWCollectionMask`, `kFirefoxTraceLoggingProvider`, `ETW::Init()`, `ETW::Shutdown()`.
- **Known Callers:** NSPR thread hooks, XPCOM thread initialization, pref observers, network marker macros (`PROFILER_MARKER`).
- **Supported Semantics:**
  - Thread registration is a clean no-op returning `nullptr`.
  - Buffer access returns a static inactive `ProfileChunkedBuffer`.
  - ETW mask is statically zero (all markers rejected early before formatting).
- **Unsupported Semantics:** Stack sampling, active profile capturing, JSON trace exporting.
- **Fail-Closed / Error Behavior:** `profiler_start()` returns a resolved generic promise to avoid crashing any async promise listener, but leaves features inactive (`profiler_feature_active` returns `false`).
- **Risk Rating:** **Low**. The stub satisfies link-time symbol references without allocating buffers or running background sampling threads.
- **Upstream Files to Watch:** `tools/profiler/public/GeckoProfiler.h`, `tools/profiler/public/ETWTools.h`.
- **Targeted Tests:** `netwerk/naivefox/tools/verify-shims.py` (Test 1 & Test 2).

---

### 2.2. Lean DOM Bindings & PSM Stubs (`LeanDOMBindings.cpp`)

- **File:** [`netwerk/naivefox/LeanDOMBindings.cpp`](LeanDOMBindings.cpp)
- **Replaced Upstream Subsystem:** Generated DOM bindings (`dom/bindings`), full `CycleCollectedJSRuntime`, interactive PSM UI dialogs (`ShowProtectedAuthDialog`), and DOM Promise FFI.
- **Required Symbols / API:**
  - `dom::OriginAttributesDictionary` copy, compare, and assign operators.
  - `dom::PartitionKeyPatternDictionary`, `dom::OriginAttributesPatternDictionary`.
  - `NetworkConnectivityService::MapNAT64IPs()`, `GetSingleton()`.
  - `CycleCollectedJSRuntime` stubs (`AreGCGrayBitsValid`, `TraverseRoots`).
  - PSM certificate stubs: `nsNSSCertificateDB::OpenSignedAppFileAsync`, `AsyncVerifyPKCS7Object`.
- **Known Callers:** Necko cookie storage (`nsICookieJarSettings`), Necko origin attributes computation, PSM certificate database initialization.
- **Supported Semantics:** Full value-semantic handling of `OriginAttributes` and partition keys for network request isolation and cookie jars.
- **Unsupported Semantics:** DOM JS object reflection of origin attributes dictionaries (`Init` returns `false`). Interactive auth dialogs and app package signature verification.
- **NAT64 Limitation Notice:** The lean implementation of `NetworkConnectivityService::MapNAT64IPs` performs a pass-through returning the original record set. **IPv6-only NAT64 synthesis environments are not yet validated or supported.**
- **Fail-Closed / Error Behavior:** PSM interactive/app certificate verification APIs return `NS_ERROR_NOT_IMPLEMENTED` (fail-closed).
- **Risk Rating:** **Contained**. Schema changes in `OriginAttributes.webidl` require matching field updates in `LeanDOMBindings.cpp`.
- **Upstream Files to Watch:** `dom/bindings/parser/WebIDL.py`, `netwerk/base/OriginAttributes.h`, `dom/webidl/OriginAttributes.webidl`.
- **Targeted Tests:** `netwerk/naivefox/tools/verify-shims.py` (Test 4), `verify-staged-runtime.sh`.

---

### 2.3. Script Security Manager (`nsScriptSecurityManagerNaiveFox.cpp`)

- **File:** [`caps/nsScriptSecurityManagerNaiveFox.cpp`](../../caps/nsScriptSecurityManagerNaiveFox.cpp)
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
- **Unsupported Semantics:** Content principals, Null principals, Expanded principals, DocShell principals, domain policies, and file URI allowlists.
- **Fail-Closed / Error Behavior:** Non-SystemPrincipal loads return `NS_ERROR_DOM_BAD_URI` (fail-closed). Principal creation from untrusted origins or JSON returns `NS_ERROR_NOT_AVAILABLE`.
- **Risk Rating:** **Contained**. The security manager strictly permits only SystemPrincipal requests; any untrusted web content execution is prevented by design.
- **Upstream Files to Watch:** `caps/nsIScriptSecurityManager.idl`, `caps/BasePrincipal.h`.
- **Targeted Tests:** `netwerk/naivefox/tools/verify-shims.py` (Test 3), integration suites.

---

### 2.4. In-Process Necko Channel Parameters (`NeckoChannelParams.h`)

- **File:** [`netwerk/naivefox/NeckoChannelParams.h`](NeckoChannelParams.h)
- **Replaced Upstream Subsystem:** Multi-process IPDL channel parameter serialization (`ipc/glue/NeckoChannelParams.h`).
- **Required Symbols / API:**
  - `PreferredAlternativeDataTypeParams`, `ProxyInfoCloneArgs`, `HttpConnectionInfoCloneArgs`, `CookieStruct`, `CookieStructTable`, `HttpActivityArgs`.
- **Known Callers:** `nsHttpChannel`, `nsHttpConnectionInfo`, `nsCookieService`.
- **Supported Semantics:** In-process parameter passing across thread boundaries.
- **Unsupported Semantics:** Cross-process serialized IPC messages.
- **Fail-Closed / Error Behavior:** In-process operations retain exact field contents without IPC marshalling overhead.
- **Risk Rating:** **Low**. Requires updating type definitions if upstream Necko introduces new connection clone arguments.
- **Upstream Files to Watch:** `netwerk/ipc/NeckoChannelParams.ipdlh`, `netwerk/base/nsHttpConnectionInfo.h`.
- **Targeted Tests:** `netwerk/naivefox/tools/verify-shims.py` (Test 5).

---

### 2.5. Memory Reporting & IO Interposer (`MemoryReportingMinimal.cpp` & `PoisonIOInterposerStub.cpp`)

- **Files:** [`xpcom/base/MemoryReportingMinimal.cpp`](../../xpcom/base/MemoryReportingMinimal.cpp), [`xpcom/build/PoisonIOInterposerStub.cpp`](../../xpcom/build/PoisonIOInterposerStub.cpp)
- **Replaced Upstream Subsystem:** `nsMemoryReporterManager.cpp`, `PoisonIOInterposer.cpp`.
- **Required Symbols / API:**
  - `nsMemoryReporterManager` initialization / minimization stubs.
  - `mozilla::InitPoisonIOInterposer()`, `mozilla::ClearPoisonIOInterposer()`.
- **Supported Semantics:** Clean no-op lifecycle management during startup and shutdown.
- **Risk Rating:** **Low**. Pure no-op stubs.
- **Targeted Tests:** Incremental builds, shutdown clean verification.

---

### 2.6. Opaque Response Blocking (`OpaqueResponseUtilsNaiveFox.cpp`)

- **File:** [`netwerk/protocol/http/OpaqueResponseUtilsNaiveFox.cpp`](../protocol/http/OpaqueResponseUtilsNaiveFox.cpp)
- **Replaced Upstream Subsystem:** Browser CORB/ORB validator (`OpaqueResponseUtils.cpp`).
- **Required Symbols / API:** `OpaqueResponseUtils` content sniffers.
- **Supported Semantics:** Transparent headless data pass-through for proxy tunneling.
- **Risk Rating:** **Low**.
- **Targeted Tests:** Integration H2/H3 proxy suites.

---

### 2.7. Minimal DOM & Security Header Compatibility Group

The following header-only compatibility types satisfy include requirements in Necko and XPCOM without compiling the corresponding DOM engines:

1. **[`netwerk/naivefox/nsContentUtils.h`](nsContentUtils.h):** Compact helper definitions (`StoragePrincipalHelper`, thread safety checks).
2. **[`netwerk/naivefox/StoragePrincipalHelper.h`](StoragePrincipalHelper.h):** Principal storage helpers for cookie jars.
3. **[`netwerk/naivefox/ClientInfo.h`](ClientInfo.h):** Value record for ClientInfo parameters.
4. **[`netwerk/naivefox/FeaturePolicy.h`](FeaturePolicy.h):** Permission and feature policy type definitions.
5. **[`netwerk/naivefox/NaiveFoxOriginTrials.h`](NaiveFoxOriginTrials.h) & [`NaiveFoxRFPTarget.h`](NaiveFoxRFPTarget.h):** Origin trials and resist-fingerprinting targets.
6. **[`netwerk/naivefox/Promise.h`](Promise.h), [`netwerk/naivefox/ReferrerPolicyBinding.h`](ReferrerPolicyBinding.h), [`netwerk/naivefox/RequestBinding.h`](RequestBinding.h):** Minimal binding type definitions.
7. **[`netwerk/naivefox/ServiceWorkerDescriptor.h`](ServiceWorkerDescriptor.h):** ServiceWorker descriptor stubs.
8. **[`netwerk/naivefox/UnknownProtocolHandler.cpp`](UnknownProtocolHandler.cpp):** Returns `NS_ERROR_UNKNOWN_PROTOCOL` for unsupported schemes (fail-closed).

- **Risk Rating:** **Low**. All headers are self-contained type definitions.

---

## 3. Red-Zone Schema Audit Watchlist

When refreshing upstream Firefox, the following upstream schema definitions must be audited for field or ABI drift:
1. `dom/webidl/OriginAttributes.webidl` $\rightarrow$ Compare with `LeanDOMBindings.cpp`.
2. `caps/nsIScriptSecurityManager.idl` $\rightarrow$ Compare with `nsScriptSecurityManagerNaiveFox.cpp`.
3. `netwerk/ipc/NeckoChannelParams.ipdlh` $\rightarrow$ Compare with `NeckoChannelParams.h`.
4. `tools/profiler/public/GeckoProfiler.h` $\rightarrow$ Compare with `ProfilerNaiveFoxStub.cpp`.
