# Minimal-only Firefox patch inventory

This is the refresh checklist for changes carried only by `minimal`. The
shared NaiveFox integration patches are catalogued in `UPSTREAM-PATCHES.md`.
Do not record transient commit SHAs or measured results here; reports and Git
history provide that evidence.

For every Firefox refresh, inspect each listed area even when Git reports no
textual conflict. Prefer an upstream-supported lean path when one becomes
available, and remove the downstream guard instead of preserving it by habit.

| ID | Minimal contract | Primary refresh watchpoints | Required focused gates |
|---|---|---|---|
| `NF-UPSTREAM-010` | Keep real parent-process Necko channel creation while excluding browser loading-node, cache, and dictionary services. | `netwerk/base/nsNetUtil.cpp`, `RequestContextService.cpp`, `netwerk/protocol/http/nsHttpChannel.cpp`, `nsHttpHandler.cpp` | Direct HTTPS fetch; H2/H3/Auto CONNECT; profile and staged startup. |
| `NF-UPSTREAM-011` | Supply only the Windows file, DNS, event-loop, IPC, locale, shutdown, and packaging glue needed by the single-process app. | `mozconfig-windows-x86_64`, `app.mozbuild`, Windows branches under `ipc`, `mozglue`, `netwerk`, `security`, and `xpcom` | Cross-build; PE/package audit; native Windows runtime, H2/H3, config, lifecycle churn, and Unicode logging. |
| `NF-UPSTREAM-012` | Remove GTK/GDK/Cairo/Pango/ATK/X11 linkage from the headless Linux library while retaining the GLib event loop. | `toolkit/library/moz.build`, `ipc/chromium/src/base/message_pump_glib.cc`, `caps/BasePrincipal.*` | Dynamic dependency audit; staged runtime; H2/H3/Auto and listener suites. |
| `NF-UPSTREAM-013` | Export HarfBuzz public types without compiling the font-shaping implementation. | `app.mozbuild`; HarfBuzz public header names used by Unicode code | Link-input audit; Unicode/network startup smoke. |
| `NF-UPSTREAM-014` | Exclude unused Abseil and profiler unwinder/Breakpad implementation edges. | `caps/moz.build`, `toolkit/library/moz.build`, profiler declarations, `app.mozbuild` | Link-closure assertions; startup/shutdown; network integration. |
| `NF-UPSTREAM-015` | Replace the full profiler and JsonCPP dependency path with the project compatibility ABI. | `core/ProfilerNaiveFoxStub.cpp`, `core/moz.build`, profiler public ABI and Rust marker bindings | `tools/verify-shims.py`; link-closure assertions; startup/shutdown. |
| `NF-UPSTREAM-016` | Preserve the trailing EOF byte required by the Rust preferences parser on the lean unknown-size stream path. | `modules/libpref/Preferences.cpp` and parser buffer ownership changes | Persistent, temporary, absent-home, repeated-startup, and config preference tests on Linux and Windows. |
| `NF-UPSTREAM-017` | Generate only retained Glean schemas and own the target-correct Rust feature closure; product telemetry remains disabled. | Glean mozbuild/parser inputs, Necko/Neqo/NSS Cargo manifests, `Cargo.lock` | Linux/Windows Cargo closure, generated-header audit, H2/H3/Auto networking. |
| `NF-UPSTREAM-018` | Use an isolated NaiveFox Rust workspace and product ping root rather than Firefox-wide incidental feature ownership. | root and toolkit Rust manifests, `config/makefiles/rust.mk`, mozbuild emitter, Glean parser | Deterministic metadata on both targets; clean standalone build; closure report consistency. |
| `NF-UPSTREAM-019` | Declare Winsock support at the Neqo crate that directly consumes it. | `netwerk/socket/neqo_glue/Cargo.toml` and upstream `winapi`/socket feature changes | Windows Cargo resolution and cross-link; native H3/UDP acceptance. |
| `NF-UPSTREAM-020` | Export SpiderMonkey public headers needed by retained types while suppressing the JS engine and satisfying only the narrow compatibility ABI. | `js/src/moz.build`, `js/src/frontend/Stencil.cpp`, `SpiderMonkeyCompat.cpp`, JS public headers | Closure contains no `js_static`, frontend, Wasm, or execution objects; startup and networking suites pass. |
| `NF-UPSTREAM-021` | Let the test-enabled NaiveFox graph use Mozilla gtest without linking browser FOG/XRE startup. Ordinary Firefox gtest remains unchanged. | `testing/gtest/mozilla/GTestRunner.cpp`, `NaiveFoxRunner.cpp`, `core/moz.build` | `mach gtest 'NaiveFoxTunnelSessionLifecycle.*'` on the test-enabled minimal graph. |

Project-owned shims are reviewed separately in `SHIMS.md`. Source-export
discovery and report mechanics are maintenance tooling, not Firefox patches;
their reproducible workflow is in `MINIMAL.md`.
