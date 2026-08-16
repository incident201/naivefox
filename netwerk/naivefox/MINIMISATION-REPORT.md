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
