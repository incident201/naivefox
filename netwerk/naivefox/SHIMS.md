# Minimal compatibility shims

The `minimal` build keeps Firefox's Necko, NSS, and Neqo transport behavior but
does not compile the browser UI, content process, full DOM runtime, telemetry,
or profiler implementation. The shims below satisfy narrowly defined ABI or
type contracts for retained networking code.

A shim must be project-scoped, have a documented unsupported surface, fail
closed when it touches security or external protocols, and have a focused
check. It must not silently approximate browser behavior.

## Inventory

| Component | Supported contract | Deliberately unsupported / failure behavior | Refresh watchpoint |
|---|---|---|---|
| `core/ProfilerNaiveFoxStub.cpp` and `../protocol/http/happy_eyeballs_glue/src/profiler_noop.rs` | Profiler lifecycle, thread-registration, marker, and Windows ETW symbols required by retained callers; all are inactive no-ops. | Sampling, stack unwinding, capture buffers, profile JSON, and active markers. | Profiler public headers, ETW declarations, Rust marker FFI. |
| `LeanDOMBindings.cpp` | Value semantics for origin-attribute dictionaries plus the small headless Necko/PSM symbols used at startup. | JS reflection, interactive certificate UI, signed-app verification; unsupported security operations return an error. NAT64 mapping is pass-through. | `OriginAttributes.webidl`, `OriginAttributes.h`, PSM interfaces. |
| `../../caps/nsScriptSecurityManagerNaiveFox.cpp` | System-principal singleton and the URI checks required by in-process Necko channels. | Content, null, expanded, DocShell, addon, and domain-policy principals are unsupported. Externally reachable non-system loads fail closed; permissive JS wrapper/script ABI hooks must remain unreachable because the JS runtime is absent. | `nsIScriptSecurityManager.idl`, `BasePrincipal.h`, and closure reachability. |
| `NeckoChannelParams.h` and project IPDLH files | In-process value records needed by retained channel and stream code. | Cross-process actor serialization. | Upstream Necko channel and input-stream IPDL schemas. |
| `../../xpcom/base/MemoryReportingMinimal.cpp` and `../../xpcom/build/PoisonIOInterposerStub.cpp` | Startup/shutdown ABI for memory-reporting and I/O-interposer callers. | Memory reports and main-thread I/O poisoning; clean no-op lifecycle only. | XPCOM reporter/interposer declarations. |
| `../protocol/http/OpaqueResponseUtilsNaiveFox.cpp` | Headless channel content handling used by the proxy transport. | Browser ORB/CORB policy enforcement; no web-content execution exists in this product. | `OpaqueResponseUtils.h` and its Necko callers. |
| `../../toolkit/xre/AutoSQLiteLifetime.cpp` | SQLite initialization and shutdown needed by retained storage components. | Full XRE application lifecycle. | XRE/SQLite initialization ordering. |
| `../../js/xpconnect/src/XPCString.cpp` | The compact string conversion symbols retained XPCOM code links against. | General XPConnect/DOM JavaScript execution. | XPConnect public string APIs and callers. |
| `SpiderMonkeyCompat.cpp` | Small profiling/GC ABI symbols referenced by retained headers and XPCOM code when SpiderMonkey is not linked. | JavaScript execution, `js_static`, Wasm, GC heaps, realms, and script parsing. | `js/src/moz.build`, `js/src/frontend/Stencil.cpp`, JS public headers, and retained callers. |
| `nsContentUtils.h`, `StoragePrincipalHelper.h`, `ClientInfo.h`, `FeaturePolicy.h`, `NaiveFoxOriginTrials.h`, `NaiveFoxRFPTarget.h`, `Promise.h`, `ReferrerPolicyBinding.h`, `RequestBinding.h`, `ServiceWorkerDescriptor.h` | Header-only types and inert helpers needed to compile the selected Necko graph. | Browser DOM, ServiceWorker, origin-trial, permissions, and Promise runtime behavior. | Matching WebIDL, generated binding, DOM, and Necko headers. |
| `UnknownProtocolHandler.cpp` | Explicit rejection for schemes outside the retained protocol set. | External-app dispatch and UI prompts; returns unknown-protocol failure. | Protocol-handler interfaces and component registration. |
| Android native product fallbacks in `intl/locale/android`, `netwerk/system/android`, PSM, `mozglue`, and XPCOM | NDK/Bionic locale and API-level discovery, native filesystem/runtime behavior, conservative network availability hints, and logcat output needed by the headless runtime. | GeckoAppShell/JNI network notifications, Android local-network permission UI, Java enterprise-root import, Android keystore client-certificate discovery/signing, application metadata, and Java abort bridging. Security-sensitive Java-backed facilities are unavailable or use the normal non-Android NSS path; they are never treated as successful verification. | Android locale/JNI wrappers, NetworkLinkService and local-permission APIs, PSM enterprise-root/client-auth hooks, Android linker/bootstrap code, system-info, console, manifest, and local-file code. |

The lean boundary in `netwerk/cache2/CacheCrypto.cpp` excludes only the
profile-keystore initialization and lookup. It returns no cipher and preserves
the requested encryption preference, so native CacheFile handling fails disk
entries closed when encryption was requested. It must never substitute a key,
disable that preference, or persist such entries as plaintext. Ordinary Cache2
and NSS operations are retained; `NaiveFoxCacheCrypto.*` and the shim invariant
check cover this unsupported boundary.

## Non-negotiable behavior

- TLS certificate and hostname verification stay in NSS/PSM and are never
  stubbed or disabled.
- H2, H3, CONNECT, Naive padding, proxy authentication, backpressure, and
  stream shutdown stay in the real network stack.
- Externally reachable principal or protocol operations outside the headless
  contract fail closed. ABI-only permissive JS hooks must remain unreachable,
  and closure checks must fail if a JS execution path becomes linked.
- Ordinary Firefox builds must remain unchanged; minimal guards are selected
  only by the NaiveFox project configuration.
- Android fallbacks must remain native and product-scoped. They must not pull
  Java, GeckoView, Gradle, mobile application, or widget application code into
  the measured Android closure, and they must not weaken NSS certificate,
  hostname, or client-auth failure behavior.
- A new unresolved symbol is not justification for a new stub. First determine
  whether the caller belongs in the minimal graph, then prefer a real small
  implementation, and document any remaining compatibility surface here.

## Verification

The product Rust root must explicitly link `mozglue-static` and retain its
`moz_memory` feature when the C++ graph enables `MOZ_MEMORY`. An unused Cargo
dependency does not select the global allocator. Mixing Rust's platform
allocator with Gecko's allocator can hang or corrupt memory when C++ destroys
Rust-owned `ThinVec`/`nsTArray` buffers; the Windows H3 certificate-chain path
exercises this boundary. `--runtime-smoke` now allocates, reallocates and frees
nested arrays across that ABI in both directions, and the static shim check
guards the dependency/feature wiring. A networking handshake-only test is not
a substitute for live H3 payload acceptance.

Run the static shim checks and the clean minimal build:

```bash
python3 netwerk/naivefox/tools/verify-shims.py --source-only
MOZCONFIG=netwerk/naivefox/mozconfig-minimal \
NAIVEFOX_OBJDIR=/absolute/path/to/obj-naivefox-linux \
./mach build -j4
python3 netwerk/naivefox/tools/verify-shims.py \
  --objdir /absolute/path/to/obj-naivefox-linux
```

Android fallback changes additionally require a clean
`mozconfig-android-aarch64` build, staged dependency/export verification, and
the static NDK harness check. An online ARM64 device/emulator H2/H3 run remains
the runtime acceptance gate; the static check is not a substitute.

Then run staged startup/shutdown and the networking suites relevant to the
changed shim. Security-manager, channel-parameter, profiler, binding, or IPDL
schema changes require focused compile/runtime checks in addition to the
ordinary H2/H3/Auto/config gates.

The closure reports must contain no `js_static`, SpiderMonkey frontend, Wasm,
or JavaScript execution objects. `SpiderMonkeyCompat.cpp` is a narrow ABI
boundary, not permission to reintroduce the JS engine.

During an upstream refresh, compare every inventory watchpoint before accepting
the merge. Update the shim and its focused test in the same source commit; do
not preserve obsolete fields or symbols solely because an older report named
them.
