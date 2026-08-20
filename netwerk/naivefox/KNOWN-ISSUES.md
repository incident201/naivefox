# NaiveFox known issues and current constraints

Last reviewed: 2026-08-20, for the pre-export audit on `minimal`.

This document separates active architectural constraints, frozen Firefox
snapshot limitations, and non-reproducible observations from the acceptance
results in `TEST-REPORT.md`. None of the items below invalidates the completed
H2 or H3 classic-CONNECT prototype gates.

## Current pre-export status

The malformed SOCKS5 terminal-state/OOM issue is fixed. Unsupported commands,
bad address types, malformed versions, oversized same-write tails, and clients
that never read a reject now produce at most one bounded failure reply and stop
rearming input. The native Windows stress runner and Linux
`run-malformed-socks-tests.sh` both exercise this path; the next normal SOCKS
connection remains usable.

Windows file logging is also fixed and verified with relative, absolute, and
Unicode paths, append-after-restart, clean shutdown, and credential scans. The
Windows staged package passed five repeated local malformed-input/file-logging
smoke runs and a strict 600-second H3 soak (45/45 integrity requests, 45
padding/protocol records, no unexpected exit). A complete native Windows H2,
H3, and Auto workload matrix beyond this H3 soak remains a follow-up gate.

`minimal-source` publication is intentionally still blocked. One disposable
diagnostic tree now passes standalone configure, a full Linux build, and
runtime smoke, but it contains discovery residue and is not an accepted
artifact. Closure reports and provenance must be regenerated on the final
audited commit; then the fast manifest plan, one clean export, isolated Linux
acceptance, and Windows acceptance must pass.

Maintenance constraint: after any Firefox refresh or build-graph/Cargo/
generator change, regenerate both target build reports and the attested
configure report. Do not debug closure by repeatedly starting clean exports;
augment one diagnostic tree by missing input class and reserve clean export for
the final gate.

The previously reproducible Auto-suite startup abort on repeated H3 profile
launches is resolved. The lean preferences file adapter now forces the EOF
terminator expected by the Rust parser after an unknown-size read. An isolated
Auto run after the fix completed H3 preference, one bounded H2 establishment
fallback, logical H3 failures, and same-profile relaunches without a panic.

Capture comparisons now use the clean official Mozilla Firefox release fetched
by `tools/fetch-firefox-reference.sh`. The pinned NaiveFox Firefox snapshot is
kept as the other side; exact TLS/QUIC fingerprint equality is reported, not
required across release versions. Strict protocol, no-fallback, marker,
padding, and multiplexing assertions remain mandatory.

There are two capture meanings: same-base mode (an explicitly supplied Firefox
binary/library pair) is the strict minimalization regression gate; the default
pinned Firefox 154.0 release is the standalone/minimal-source diagnostic mode.
The committed `tools/firefox-reference-manifest` fixes its URL, version, and
archive digest; a moving `latest` URL is not accepted.

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
  closure is deliberately deferred. Source-closure diagnostics are green, but
  the one publishable clean export and isolated acceptance remain separate
  milestones; this is not an assertion that the runtime is unminimized.

Detailed evidence is in `TEST-REPORT.md`, wire comparisons are in
`H3-CAPTURE.md`, and every modified Firefox file is listed in `UPSTREAM.md`.
