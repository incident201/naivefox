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
