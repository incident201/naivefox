# NaiveFox minimisation report

This report records measured changes on the `minimal` branch. The full
Firefox source tree and compiled Gecko dependency closure remain intact until
later phases prove that they can be reduced safely.

## Phase 1: staged runtime resources

Baseline source point: `2a539d796d1a1d134ec64739c69b61f443132a3c`.
The binary and shared libraries were not rebuilt or changed during this
phase. Successful copied-package H2 and H3 config workloads were traced with
`strace -ff -e trace=%file`; the trace contained 68 process files and observed
59 package files totalling 345,904,004 bytes. The helper
`tools/analyze-runtime-trace.py` emits a sorted, hashed JSON report for
repeatable audits.

The old package copied eight complete resource directories. The new package
copies only `tools/runtime-resources.manifest`, installs a minimal
`chrome.manifest`, and records every staged file, mode, byte count, and
SHA-256 in `runtime-manifest.json`. Verification recomputes that manifest
before and after copying the package to a fresh directory under `/tmp`.
Two independent staging runs from the same `dist/bin` produced byte-identical
runtime manifests.

| Measure | Baseline | Phase 1 | Change |
|---|---:|---:|---:|
| Package apparent size | 378 MiB | 331 MiB | -47 MiB |
| Exact package bytes | 388,995,134 | 345,903,270 | -43,091,864 (-11.08%) |
| Staged regular files | broad recursive resources | 46 | explicit allowlist |

The candidate manifest SHA-256 was
`2c315e41dc58f85fedcfeee062021098c74f47d4eb54e1bbe532a96d0998af99`.
It is evidence for this measured candidate rather than a permanent artifact
hash: rebuilding the executable legitimately changes the package manifest.

Largest remaining files are:

| File | Bytes |
|---|---:|
| `runtime/libxul.so` | 327,025,152 |
| `runtime/libgkcodecs.so` | 11,139,480 |
| `runtime/libmozsqlite3.so` | 1,666,840 |
| `runtime/libfreeblpriv3.so` | 1,208,232 |
| `runtime/libnss3.so` | 902,952 |
| `runtime/naivefox` | 796,192 |

`libxul.so` is approximately 94.5% of exact package bytes. Further material
reduction therefore requires build/link work; deleting more small runtime
resources cannot deliver the same order of improvement.

### Acceptance

The following gate passed from the 331 MiB candidate copied under `/tmp`, with
object-directory loader paths explicitly removed:

- deterministic manifest verification before and after the copy;
- `ldd` with no unresolved or build-tree dependency;
- Gecko runtime smoke and a public `https://example.com/` fetch;
- config startup with and without a persistent home/state directory;
- simultaneous SOCKS5 and HTTP CONNECT listeners over strict H2;
- the same config, padding, integrity, and concurrent workload over strict H3;
- Auto H3 success, single H2 establishment fallback, and no fallback after
  authentication or target failures;
- a final package manifest verification after all workloads.

No browser build rule, `libxul`, Necko, Neqo, NSS, PSM, or NSPR source was
changed in this phase.

## Phase 2.1: product infrastructure

`mozconfig-minimal` creates a separate `obj-naivefox-minimal` build with tests
enabled while disabling the Firefox crash reporter and updater. Both are
outside the NaiveFox runtime contract; profiles and fixture failure artifacts
remain sufficient for the current diagnostic workflow.

| Measure | Phase 1 package | Phase 2.1 package | Change |
|---|---:|---:|---:|
| Exact package bytes | 345,903,270 | 344,217,666 | -1,685,604 |
| Stripped `libxul.so` | 327,025,152 | 325,341,920 | -1,683,232 |
| Full object directory | 38 GiB reference | 21 GiB | -17 GiB |

The first cold build still compiled the complete browser, DOM, gfx, media,
WebRTC, and toolkit graphs and required approximately 71 minutes over two
invocations (the first was interrupted at the command timeout). This group is
therefore a safe infrastructure baseline, not meaningful graph minimisation.

Acceptance passed: 49/49 project gtests, six focused Firefox proxy-CONNECT
xpcshell files, the copied staged-runtime H2/H3/Auto gate, and the complete
local H2/H3/config/robustness/capture suite in 307.6 seconds. One preceding H2
robustness run transiently opened five pooled sockets; an immediate isolated
rerun and the final complete suite both restored the required single H2 outer
connection, so the strict gate was not weakened.

## Phase 2.2: WebRTC graph removal

`--disable-webrtc` removes PeerConnection, SCTP, SRTP, WebRTC media and
transport code. NaiveFox classic CONNECT uses Necko's HTTP tunnel paths and
does not use WebRTC APIs or a synthetic WebRTC upgrade token.

| Measure | Phase 2.1 | Phase 2.2 | Change |
|---|---:|---:|---:|
| Build descriptors | 21,487 | 14,142 | -7,345 (-34.2%) |
| Backend files | 5,933 | 4,389 | -1,544 (-26.0%) |
| Exact package bytes | 344,217,666 | 329,628,832 | -14,588,834 |
| Stripped `libxul.so` | 325,341,920 | 310,757,744 | -14,584,176 |

Relative to the Phase 1 runtime-only result, the package is smaller by
16,274,438 bytes. `mach build -j4 binaries` completed with zero project
warnings after removal of an already-unused local usage helper.

Acceptance passed: 49/49 project gtests, six focused Firefox classic CONNECT
tests including raw H3, the copied staged package H2/H3/Auto gate with public
TLS fetch, and `run-full-suite.sh` in 308.3 seconds. H2 and H3 capture
comparisons, multiplexing, half-close, backpressure, integrity, simultaneous
SOCKS/HTTP config listeners, and strict no-fallback assertions all remained
green. No Firefox networking source was changed.

## Phase 2.3: accessibility graph removal

`--disable-accessibility` removes Gecko accessibility implementations. It
does not remove GTK's system ATK dependency, so its runtime effect is smaller
than its pre-change object-input total suggested.

| Measure | Phase 2.2 | Phase 2.3 | Change |
|---|---:|---:|---:|
| Build descriptors | 14,142 | 13,918 | -224 (-1.6%) |
| Backend files | 4,389 | 4,355 | -34 |
| Exact package bytes | 329,628,832 | 327,756,137 | -1,872,695 |
| Stripped `libxul.so` | 310,757,744 | 308,885,048 | -1,872,696 |

The one-time global-define rebuild took 51 minutes 41 seconds, which is too
expensive to justify serial experimentation with many more small configure
flags. Relative to Phase 1, the package is now smaller by 18,147,133 bytes.

Acceptance passed: warning-free `binaries`, 49/49 project gtests, six focused
Firefox CONNECT tests, copied-package H2/H3/Auto verification, and the full
H2/H3/config/robustness/capture suite in 305.2 seconds. This group is retained
as a proven reduction, but further work should target an explicit lean
application/link graph rather than repeatedly toggling small global features.

## Phase 2.4: DOM/GFX implementation graph exclusion

This checkpoint is the first large build-graph reduction. The full Firefox
source checkout remains present and no upstream source directories were
deleted, but the lean NaiveFox application no longer compiles implementation
sources from `dom/` or `gfx/`. The networking, NSS/PSM, Neqo, NSPR, XPCOM, and
required IPC paths remain in the build.

The validation was performed from the empty `obj-naivefox-cold` directory with
`MOZCONFIG=netwerk/naivefox/mozconfig-minimal`. The clean build initially
exposed one missing generated IPDL input (`RandomAccessStreamParams.ipdlh`);
the minimal project-owned IPDL definition was added, configuration was
regenerated, and the same empty object directory was resumed to a successful
`gmake -j4` completion. This was not an incremental build against the earlier
product object directory.

| Measure | Result |
|---|---:|
| Clean object directory | `obj-naivefox-cold`, 4.0 GiB |
| `libxul.so` (opt/debug build output) | 638 MiB |
| `naivefox` | 5.2 MiB |
| Cold runtime smoke | PASS |
| Compiled implementation sources under `dom/` | 0 |
| Compiled implementation sources under `gfx/` | 0 |

The closure audit found no `gfx` object subtree and no object/archive/shared
library outputs under the cold `dom` subtree. The only remaining `dom`
artifacts are generated binding metadata needed by the common build machinery;
they are not compiled DOM implementation sources. The audit also found zero
`/dom/` or `/gfx/` implementation includes in generated unified C/C++ sources
and zero matching compiler source operands across the complete clean-build
logs.

This is a build-graph checkpoint, not yet a final package-size measurement:
the 638 MiB `libxul.so` value is an unstripped development artifact and is not
directly comparable with the stripped staged-package numbers above. Staged
runtime measurement and further link-closure work are deliberately deferred
until this checkpoint is reviewed.

## Phase 2.4 acceptance: lean staged runtime

The cold object directory was linked after the parent-only Necko fixes were
validated. The project-owned static component manifest restores the lean
script-security manager; `RequestContextService` uses the native parent PID;
the browser dictionary/cache paths and DOM-only channel overloads remain out
of the lean graph. This preserves the network client without bringing DOM or
GFX implementation code back into the build.

The explicit staged package
`obj-naivefox-cold/naivefox-linux-x86_64-cold-milestone2` is 90,755,038 bytes
(about 87 MiB), including an 81 MiB stripped `libxul.so`. The package was
copied outside the object directory and passed runtime smoke, public HTTPS
fetch, profile/no-home checks, strict H2/H3 config workloads, and Auto.

The final isolated H2 and H3 suites passed all raw CONNECT, SOCKS, padding,
large-transfer integrity, backpressure, lifecycle, multiplexing, Auto,
configuration, robustness, and capture checks. Capture uses an explicitly
separate full Firefox baseline and per-runtime library paths; no Firefox
browser binary is added to the lean staged package. A single sequential run
hit a transient libpref parser abort at the start of a second H3 capture pass;
fresh per-pass profiles fixed the environmental race and the independent H3
suite passed without weakening any gate.


## Phase 2.5: GTK3 and Desktop UI Linkage Exclusion

Phase 2.5 decouples all desktop UI / X11 / GTK3 / Cairo / Pango / ATK dynamic
shared library dependencies from the headless NaiveFox runtime on Linux.

### Dependency Closure and Symbol Audit

Symbol audit of `libxul.so` demonstrated that no Cairo, Pango, ATK, X11, or XCB
functions are called by the headless application. Only two GTK calls
(`gdk_event_handler_set` and `gtk_main_do_event`) existed inside the unused
`MessagePumpForUI` destructor in `ipc/chromium/src/base/message_pump_glib.cc`.
By guarding these calls under `MOZ_NAIVEFOX` and updating
`toolkit/library/moz.build` to link only `GLIB_LIBS` instead of `MOZ_GTK3_LIBS`,
`MOZ_X11_LIBS`, and `MOZ_PANGO_LIBS`, 16 desktop GUI libraries were eliminated
from `DT_NEEDED`.

| Dynamic Shared Libraries (`DT_NEEDED`) | Before Phase 2.5 | Phase 2.5 | Change |
|---|---:|---:|---:|
| System, NSPR, NSS, SQLite, GLib | 19 | 20 | +1 (explicit GLib) |
| Desktop UI (GTK3, GDK, Cairo, Pango, ATK, X11, XCB) | 16 | 0 | -16 (100% removed) |
| Total `DT_NEEDED` entries | 35 | 20 | -15 (-42.8%) |

Eliminated libraries: `libgtk-3.so.0`, `libgdk-3.so.0`, `libgdk_pixbuf-2.0.so.0`,
`libgio-2.0.so.0`, `libcairo.so.2`, `libcairo-gobject.so.2`, `libpango-1.0.so.0`,
`libpangocairo-1.0.so.0`, `libatk-1.0.so.0`, `libX11.so.6`, `libX11-xcb.so.1`,
`libXext.so.6`, `libXfixes.so.3`, `libXrandr.so.2`, `libxcb.so.1`, `libxcb-shm.so.0`.

### Link Inputs and Staged Package

- **Link input count**: 609 object files (305,209,024 bytes unstripped)
- **`libxul.so` stripped**: 86,802,016 bytes (~82.8 MiB)
- **Staged runtime package**: 93,153,920 bytes (~88.8 MiB) across 15 files
- **Incremental compile time**: 4.5 seconds

### Acceptance

- `readelf -d` and `analyze-link-closure.py` verification;
- Staged runtime verification outside build tree (`verify-staged-runtime.sh`);
- H2, H3, Auto, SOCKS5, HTTP CONNECT, padding, robustness, and lifecycle suites green.


## Phase 2.6: HarfBuzz Font Shaper Object Exclusion

Phase 2.6 excluded HarfBuzz complex text shaping translation units from the `libxul` link graph.

### Analysis & Implementation

Symbol audit confirmed that `libxul.so` never calls any HarfBuzz shaper functions (`hb_shape`, `hb_font_*`, `hb_buffer_*`). The only usage in the headless tree is enum types (`hb_unicode_general_category_t`) in `nsUnicodeProperties.h`. By guarding `UNIFIED_SOURCES` in `gfx/harfbuzz/src/moz.build` under `if not CONFIG["MOZ_NAIVEFOX"]:`, the large font shaper object (`Unified_cpp_gfx_harfbuzz_src0.o`, 43.4 MB unstripped) was excluded from the link closure.

- **Link input count**: 608 files (down from 609)
- **Unstripped link inputs**: 261,856,704 bytes (down from 305,209,024 bytes, **-43.35 MB / -14.2%**)
- **`gfx` group**: Reduced to 0 files / 0 bytes.


## Phase 2.7: Google Abseil Elimination & Profiler Unwinder Trimming

Phase 2.7 eliminated the unused Google Abseil C++ library and trimmed unnecessary DWARF stack unwinder (LUL) and Breakpad ELF parser components from `tools/profiler`.

### Analysis & Implementation

1. **Abseil Elimination**: `config/external/abseil-cpp` generated 75 object files under `third_party/abseil-cpp`. Symbol inspection of `libxul.so` demonstrated 0 unresolved or required Abseil symbols. Removed `abseil-cpp` from `netwerk/naivefox/app.mozbuild` and `toolkit/library/moz.build`.
2. **Gecko Profiler Unwinder Trimming**: Guarded LUL DWARF unwinder, Breakpad ELF parser, CPU frequency sampling, and PowerCounter objects in `tools/profiler/moz.build` and `tools/profiler/core/platform.cpp` under `MOZ_NAIVEFOX`.

### Link Inputs and Link Closure

| Component Group | Before Phase 2.6 | Phase 2.6 | Phase 2.7 | Cumulative Delta |
|---|---:|---:|---:|---:|
| `netwerk` | 61 files (89.3 MB) | 61 files (89.3 MB) | 61 files (89.3 MB) | 0 |
| `config` (ICU, SQLite, NSPR) | 369 files (47.4 MB) | 369 files (47.4 MB) | 369 files (47.4 MB) | 0 |
| `xpcom` | 28 files (33.9 MB) | 28 files (33.9 MB) | 28 files (33.9 MB) | 0 |
| `modules` (libpref, brotli, jar, zlib) | 23 files (16.3 MB) | 23 files (16.3 MB) | 23 files (16.3 MB) | 0 |
| `tools` (profiler) | 8 files (18.8 MB) | 8 files (18.8 MB) | 2 files (15.5 MB) | -6 files (-3.3 MB) |
| `third_party` (abseil, zstd) | 77 files (16.0 MB) | 77 files (16.0 MB) | 2 files (1.2 MB) | -75 files (-14.8 MB) |
| `ipc` | 11 files (13.8 MB) | 11 files (13.8 MB) | 11 files (13.8 MB) | 0 |
| `security` (PSM, mozpkix, CT) | 17 files (12.7 MB) | 17 files (12.7 MB) | 17 files (12.7 MB) | 0 |
| `storage` | 4 files (6.0 MB) | 4 files (6.0 MB) | 4 files (6.0 MB) | 0 |
| `intl` | 5 files (3.0 MB) | 5 files (3.0 MB) | 5 files (3.0 MB) | 0 |
| `toolkit` | 2 files (2.7 MB) | 2 files (2.7 MB) | 2 files (2.7 MB) | 0 |
| `caps` | 2 files (1.1 MB) | 2 files (1.1 MB) | 2 files (1.1 MB) | 0 |
| `chrome` | 1 file (0.9 MB) | 1 file (0.9 MB) | 1 file (0.9 MB) | 0 |
| `gfx` (HarfBuzz) | 1 file (43.4 MB) | 0 files (0 MB) | 0 files (0 MB) | -1 file (-43.4 MB) |
| **Total Link Inputs** | **609 files (305.2 MB)** | **608 files (261.9 MB)** | **527 files (243.8 MB)** | **-82 files (-61.4 MB / -20.1%)** |

- **Unstripped `libxul.so` size**: 655,196,824 bytes (down from 691.1 MB, **-35.9 MB**)
- **Incremental compile/link time**: **1.7 seconds**

### Phase 2.8: Gecko Profiler & JsonCPP Elimination
- **Profiler engine replaced**: Replaced heavy Gecko Profiler source files with `tools/profiler/core/ProfilerNaiveFoxStub.cpp` providing lightweight no-op stubs.
- **JsonCPP eliminated**: `toolkit/components/jsoncpp` completely removed from `libxul` link closure (**0 files / 0 MB** in `toolkit`).
- **Closure impact**: Link closure reduced to **525 files / 216.04 MB** (unstripped).
- **Binary size**: `libxul.so` unstripped size reduced to **616.0 MB**, stripped size **62 MB**.
- **Build time**: Clean incremental build completes in **2.85 seconds**.
