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
