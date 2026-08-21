# NaiveFox known issues and current constraints

Last reviewed: 2026-08-21, for the no-SpiderMonkey checkpoint on `minimal`.

## No-SpiderMonkey checkpoint constraints (2026-08-21)

The clean no-SpiderMonkey Linux and Windows graphs remove the `js/src`
runtime `DIRS`/`FINAL_LIBRARY` and URLPattern glue, but retain public/generated
JS ABI headers and the project-owned `SpiderMonkeyCompat.cpp` shims. They also
retain classic ICU and the ICU4X encoding, locale, and segmenter support; this
is intentionally not a no-Intl configuration.

Linux acceptance in `/home/zubastik/obj-naivefox-no-sm-linux-final` completed
the full `mach build -j4` in 5:00 with 114 unused browser/JS-only warnings and
no errors, then passed staged runtime smoke and config SOCKS/HTTP CONNECT H2,
H3, and Auto H3/fallback. Windows acceptance in
`/home/zubastik/obj-naivefox-no-sm-windows-final` produced
`x86_64-pc-windows-msvc` `xul.dll` and `naivefox.exe`; bundled Wine passed
`--help` only after `WINEPREFIX`, `WINELOADER`, and `WINESERVER` were set
explicitly.

Neither objdir contains `js/src` `.o`, `.obj`, or `.a`, `libjs_static.a`, or
Wasm objects, and neither build-output `dependentlibs.list` contains a
`js`/`mozjs`/`wasm` entry.

The current staged Windows package at
`D:\naivefox\naivefox-windows-x86_64-no-sm-final` also passed host-native
acceptance. The pinned Caddy fixture remained in WSL while Windows NaiveFox ran
natively. The `verify-staged-windows-smoke.py` runner passed version/runtime smoke,
dynamic SOCKS5 and HTTP CONNECT, malformed stress, and Unicode file logging;
strict H2 and H3 each passed SOCKS5 and HTTP CONNECT fetches with padding, and
CLI Auto passed H3 preference and H2 fallback.

## Current refresh/export result

The controlled upstream refresh and one clean export are complete. The audited
source is `a533411d0c4940f3063d1d01313a3e4fc24ba53d`, the final report/export
evidence snapshot is `a7251b0ea7fd530d1d23fada8e3217514aa1399c`, and the
Firefox base is `17e93ad5d3261e20104c7f6f2ec867ecc138ca1`. The export has
25,558 files and 37 directory contracts; each generated `UPSTREAM-BASE`
records its exact manifest SHA-256.

The exported source was configured, generated, built with the lean NaiveFox
`binaries` target, staged, and tested outside the original checkout and
objdir. H2, H3, Auto, config, malformed-SOCKS, padding/integrity, and finite
robustness gates passed. A full Firefox browser build is intentionally out of
scope and is not required for this product gate. The validated snapshot is
published on `minimal-source` at
`0df131ea63ae0d2dc1bbefb9e811fcd038168f70` as `minimal-source-v0.2`.

This document separates active architectural constraints, frozen Firefox
snapshot limitations, and non-reproducible observations from the acceptance
results in `TEST-REPORT.md`. None of the items below invalidates the completed
H2 or H3 classic-CONNECT prototype gates.

## Bundled sccache server is optional

The bundled `sccache` 0.17.0 can hang the first `clang -E` configure probe in
Ubuntu24Dev when the local server at `127.0.0.1:4226` is missing or stale. A
bounded probe and explicit `sccache --start-server` are documented in
`UPSTREAM.md`. If the probe still times out, rerun configure/build in the same
objdir with `NAIVEFOX_DISABLE_SCCACHE=1`. This is an operational cache issue;
it does not justify changing source, repeating the upstream merge, or making a
new object directory, and it is never a product or release failure.

## Current pre-export status

The malformed SOCKS5 terminal-state/OOM issue is fixed. Unsupported commands,
bad address types, malformed versions, oversized same-write tails, and clients
that never read a reject now produce at most one bounded failure reply and stop
rearming input. The native Windows stress runner and Linux
`run-malformed-socks-tests.sh` both exercise this path; the next normal SOCKS
connection remains usable.

Windows file logging is also fixed and verified with relative, absolute, and
Unicode paths, append-after-restart, clean shutdown, and credential scans. The
standalone Windows package passes malformed-input/file-logging smoke plus
short native H2, H3, and Auto workloads. Each protocol workload completed
8/8 integrity-checked requests with parallelism four; strict H2/H3 used both
SOCKS5 and HTTP CONNECT listeners, padding was negotiated for every tunnel,
and Auto selected H3. The historical 600-second H3 soak remains valid earlier
stability evidence but was not repeated for this source-publication gate.

Standalone closure validation is complete. The disposable diagnostic tree is
still contaminated discovery state and is never published, but it has now
passed the lean Linux and Windows product evidence gates with the original
Firefox checkout hidden.
An independent clean Linux export passed the full H2/H3/Auto/config suite and
staged-runtime verification; the native Windows package passed the short
matrix above. Publication uses one newly generated clean manifest snapshot and
an orphan history. GitHub's default branch is now `minimal-source`; fixes must
still be made in `minimal` or an upper layer and regenerated, never edited in
the product branch.

Maintenance constraint: after any Firefox refresh or build-graph/Cargo/
generator change, regenerate both target build reports and both attested
configure reports. Do not debug closure by repeatedly starting clean exports;
augment one diagnostic tree by missing input *class*, follow Windows resource
includes recursively, and reserve clean export for the publication gate.

The previously reproducible Auto-suite startup abort on repeated H3 profile
launches is resolved. The lean preferences file adapter now forces the EOF
terminator expected by the Rust parser after an unknown-size read. An isolated
Auto run after the fix completed H3 preference, one bounded H2 establishment
fallback, logical H3 failures, and same-profile relaunches without a panic.

Capture comparison is an isolated diagnostic only. When explicitly requested,
ordinary Firefox and NaiveFox must be built from the same Firefox base and
compared with the capture runners. It is not a minimization, merge, release, or
routine-change gate. The historical official-release record remains evidence
only; no ordinary Firefox package is built by the normal upstream/minimal or
minimal-source workflow.

## Single-process networking

Single-process networking is the deliberate architecture of the current Linux
prototype. `GeckoRuntime` disables Firefox's separate socket process, so the
existing raw CONNECT upgrade callback, `Http2StreamTunnel`,
`Http3StreamTunnel`, Necko connection management, Neqo, PSM, and NSS all run in
the parent process.

This is not a replacement network stack and does not change wire ownership:
Firefox still owns HTTP/2, HTTP/3, QUIC, TLS, connection pooling, and protocol
flow control. The current upgrade-connect completion path does not support
publishing its asynchronous tunnel streams across the socket-process boundary.
Enabling the socket process later therefore requires an IPC-compatible stream
takeover design and focused lifecycle tests. The prototype does not silently
enable an incomplete IPC path.

## Profile storage without a home directory

Normal config mode prefers persistent state under `XDG_STATE_HOME` or `HOME`.
Unlike NaiveProxy's direct Chromium `URLRequestContext`, the current Gecko
bootstrap still requires a filesystem profile directory for XPCOM/PSM
services. This is an implementation requirement, not a requirement that the
state remain persistent.

If no automatic persistent location is usable, NaiveFox creates an isolated
mode-`0700` profile under `XDG_RUNTIME_DIR`, the platform temporary directory,
or finally `/tmp`. This permits service accounts with no home directory to run
without changing NSS verification behavior. The directory is removed on
orderly C++ runtime teardown; an uncatchable termination may leave it for the
runtime-directory or system temporary-file cleanup policy. Set
`NAIVEFOX_PROFILE` to a managed writable directory when certificate database or
other profile state must persist across restarts.

## Frozen-snapshot Mozilla test limitations

Two broader Mozilla tests fail outside NaiveFox's classic CONNECT path on the
pinned Firefox snapshot:

- `test_http3_proxy.js` passes its first 37 assertions, then the ordinary
  channel three-concurrent helper times out all three channels with
  `NS_ERROR_NET_TIMEOUT`.
- `test_http3_with_proxy.js` fails its first CONNECT-UDP/MASQUE origin-H3 route
  with `NS_ERROR_PROXY_CONNECTION_REFUSED`. CONNECT-UDP and MASQUE are outside
  this prototype's scope.

Both failures were reproduced after mechanically reverting every
NaiveFox-modified H3/proxy Necko file to `h2-prototype-v0.1`, rebuilding, and
rerunning the tests. They are frozen-base/environment limitations, not
regressions introduced by NaiveFox. The relevant strict-H3, fallback,
HTTP/2-over-H3-proxy, raw classic CONNECT, four-stream multiplexing, slow-path,
and large-transfer tests pass.

## Observed cold-start H3 timeout

One staged-runtime strict-H3 preflight timed out before the client selected an
outer protocol. An immediate object-directory comparison succeeded, the next
staged preflight succeeded, and the same staged runtime subsequently completed
the full 600-second strict-H3 soak: 26 of 26 integrity-checked probes passed,
including parallel waves and two 120-second idle windows, with no H2 fallback.

The cold-start event was therefore observed but not reproducible in the final
soak. It is retained as a transient external-network observation. Strict H3
continues to fail closed: a future establishment failure cannot silently use
H2, and the soak runner records such a failure rather than masking it.

## Prototype scope constraints

- Linux x86-64 is the supported prototype platform.
- H2 remains the developer CLI default. In normal config mode, `https://`
  selects strict H2 and `quic://` selects strict H3; config mode deliberately
  has no `auto` scheme.
- `auto` does not maintain a cross-connection H3 failure cache or backoff; each
  new SOCKS connection makes its own strict H3 establishment decision before
  the one permitted establishment-only H2 retry.
- CONNECT-UDP, MASQUE, WebTransport, SOCKS UDP ASSOCIATE, TUN/TAP, GUI work, and
  platform ports are intentionally outside this prototype.
- Config listeners accept explicit numeric IPv4/IPv6 addresses, including
  wildcard and LAN binds, and are currently unauthenticated. Exposing
  `0.0.0.0`, `::`, or a LAN address must therefore be an intentional operator
  choice protected by host firewall or trusted-network policy. The HTTP
  frontend is CONNECT-only; ordinary forward HTTP, listener authentication,
  and UDP ASSOCIATE are not implemented.
- `proxy` accepts NaiveProxy-compatible string or array mapping. Comma-separated
  multi-hop proxy chains remain outside the current scope and fail explicitly.
- The verified runtime/build closure is minimized through the current
  DOM/GFX/WebRTC/UI/profiler boundary. The remaining SpiderMonkey and ICU
  closure is deliberately deferred. Source-closure diagnostics, the clean
  export, and isolated acceptance are complete; this is not an assertion that
  the remaining runtime closure is fully minimized.

Detailed evidence is in `TEST-REPORT.md`, wire comparisons are in
`H3-CAPTURE.md`, and every modified Firefox file is listed in `UPSTREAM.md`.
