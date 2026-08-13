# NaiveFox implementation roadmap

This roadmap takes the repository from a clean Firefox fork to a working Linux HTTP/2 NaiveFox prototype.

Milestones are intentionally sequential. Each milestone must have a reproducible acceptance test before moving to the next one.

The coding agent should mark completed checklist items and record major discoveries as work proceeds.

## Phase 0 — establish the environment and baseline

### M0.1 Verify repository state

- [x] Confirm current branch is `naivefox`.
- [x] Confirm `main` is not being modified.
- [x] Confirm `origin` points to `incident201/naivefox`.
- [x] Add/verify `upstream` = `https://github.com/mozilla-firefox/firefox.git`.
- [x] Confirm the checkout is on a filesystem suitable for a large native Linux Firefox build.
- [x] Record current upstream/base commit SHA in `UPSTREAM.md`.
- [x] Read root `AGENTS.md` and all NaiveFox docs.

Acceptance:

```bash
git status
git branch --show-current
git remote -v
```

show a clean development branch and correct remotes.

### M0.2 Bootstrap Firefox build dependencies

- [x] Run `./mach bootstrap`.
- [x] Select full Firefox Desktop build.
- [x] Do not use Artifact Mode.
- [x] Let Mozilla tooling install the appropriate compiler/toolchain.
- [x] Record any required local environment changes.

Acceptance:

```bash
./mach --version
```

and normal build configuration commands work without missing-tool errors.

### M0.3 Clean baseline build

Before any implementation change:

- [x] Run a complete `./mach build`.
- [x] Save build output under `artifacts/`.
- [x] Record object directory and compiler.
- [x] Do not proceed if the untouched checkout fails to build.

Acceptance:

Firefox baseline build exits successfully.

---

### M0.4 Reproducible local Caddy fixture

Create the committed fixture sources under `netwerk/naivefox/test/integration/`. Put generated state under the Firefox object directory, not in the source tree.

- [x] Pin an exact tested Caddy version and immutable `klzgrad/forwardproxy` commit in fixture metadata/setup; do not resolve `@naive` or the latest Caddy on every run.
- [x] Build a dedicated fixture Caddy with that pinned module.
- [x] Verify `http.handlers.forward_proxy` is present and validate both the Caddyfile and adapted configuration.
- [x] Record xcaddy and Go versions in diagnostics without requiring an unnecessary installation when compatible tools already exist.
- [x] Use an explicitly TLS-enabled catch-all route without a request Host matcher, bound to a checked loopback high port.
- [x] Present an internal-PKI certificate valid for the proxy SNI hostname, normally `localhost`.
- [x] Disable HTTP/3 in the fixture; NaiveFox must negotiate `h2`.
- [x] Set `skip_install_trust`; never let Caddy attempt to install its CA globally.
- [x] Configure Basic Auth from generated per-run values and omit probe resistance.
- [x] Restrict both target ports and ACL rules to the fixture target, then deny everything else.
- [x] Create isolated Caddy XDG data/config directories.
- [x] Import only the fixture root CA into a dedicated NaiveFox NSS profile.
- [x] Create a second NSS profile without that CA.
- [x] Add deterministic target endpoints for small data, large data, upload/hash, delay, and early close.
- [x] Automate setup, readiness timeouts, execution, cleanup, and sanitized failure diagnostics.
- [x] Ensure setup and cleanup are idempotent and require neither `sudo` nor a system Caddy service.

Acceptance:

- the fixture starts with one documented command,
- the listener is loopback-only, TLS-enabled, and presents a certificate valid for the proxy hostname,
- the trusted NSS profile contains the fixture root and the untrusted profile does not,
- a control client using curl `--proxy-cacert` can authenticate through the proxy to the restricted local target,
- an HTTPS target control request additionally uses `--cacert` and validates normally,
- attempts to reach a non-fixture destination are denied,
- cleanup leaves no fixture child processes running.

Actual trusted/untrusted Necko connection tests begin in M2.2, after the headless networking runtime exists.

Validated on 2026-08-12 with
`netwerk/naivefox/test/integration/run-control-tests.sh`. The fixture pins Caddy
2.11.2, xcaddy 0.4.6, Go 1.25.12, and forwardproxy commit
`d62c80d3dd2c706b6b87579844d2397bddd18317`.

Do not call `caddy trust`, modify system trust, modify a normal Firefox profile, use `curl -k`, or commit generated CA/private-key/profile material.

---

## Phase 1 — add the NaiveFox executable without networking

### M1.1 Add build directory

Create the project subtree:

```text
netwerk/naivefox/
```

Documentation is already located there.

Add only the minimal build integration required for Firefox to descend into the directory.

Expected likely upstream touchpoint:

```text
netwerk/moz.build
```

Prefer an isolated addition such as:

```python
DIRS += ["naivefox"]
```

if valid in the current build system.

Document the exact upstream modification in `UPSTREAM.md`.

### M1.2 Minimal executable

Create the smallest Linux `naivefox` executable using Firefox's current in-tree pattern for Gecko-dependent programs.

Likely direction:

```python
GeckoProgram("naivefox", linkage="dependent")
```

but verify against current `build/gecko_templates.mozbuild` and small programs such as xpcshell.

The first executable may simply:

```text
start
print version/build information
exit 0
```

No networking yet.

Acceptance:

- [x] Firefox tree builds.
- [x] `naivefox` is produced in the object/runtime output.
- [x] `naivefox --version` or equivalent runs successfully.
- [x] No browser window appears.

---

## Phase 2 — headless Gecko/XPCOM/Necko runtime

### M2.1 Runtime initialization

Implement `GeckoRuntime`.

Responsibilities:

- initialize required Gecko/XPCOM runtime,
- initialize a usable profile/runtime environment if required,
- initialize Necko/PSM/NSS through supported Gecko startup paths,
- run the event loop needed for async networking,
- shut down cleanly.

Research current startup patterns rather than copying stale snippets.

Useful starting points:

```text
js/xpconnect/shell/xpcshell.cpp
toolkit/xre/
netwerk/
```

For the prototype, disabling the separate socket process is acceptable if necessary to make the networking objects live in the same process. Document exactly how.

Acceptance:

- [x] `naivefox` starts headlessly.
- [x] initializes networking runtime,
- [x] runs an event-loop smoke test,
- [x] shuts down with no assertion/crash.

### M2.2 Normal HTTPS sanity request

Before implementing proxy CONNECT, prove that the process can make an ordinary HTTPS request using Necko.

Requirements:

- use Necko channel APIs,
- use NSS/PSM TLS,
- do not call curl/libcurl,
- log HTTP status and a small bounded result,
- verify certificate validation works.

Acceptance:

A request to the fixture's HTTPS front-end health endpoint succeeds through Necko/NSS with the dedicated test profile. A public HTTPS endpoint may be used as an additional sanity check, but is not required for the reproducible suite.

Negative tests:

- the fixture's HTTPS front-end health endpoint must fail with the untrusted NSS profile,
- the same endpoint must succeed with the dedicated NSS profile containing the fixture CA,
- a deliberately invalid hostname must still fail.

No test may pass by disabling certificate verification or installing the fixture CA globally.

Completed on 2026-08-12. A public NSS/Necko request returned HTTP 200. Against
the local fixture, the untrusted profile failed, the scoped trusted profile
succeeded, and a trusted request using `127.0.0.1` instead of the certificate's
`localhost` DNS name failed hostname validation.

---

## Phase 3 — understand and expose raw HTTP/2 CONNECT

This is the most important Necko integration phase.

Do not implement SOCKS or Naive payload padding until a clean raw CONNECT tunnel works.

### M3.1 Reproduce Firefox's existing CONNECT path in tests

Study current:

```text
nsIHttpChannelInternal.idl
HttpBaseChannel.cpp
nsHttpConnection.cpp
Http2Session.cpp
Http2StreamTunnel.cpp
test_proxyconnect.js
test_proxyconnect_headers.js
```

Answer and document:

- how an explicit HTTPS proxy is attached to a channel,
- how CONNECT-only mode is requested,
- how HTTP/2 is selected,
- how the tunnel stream is created,
- how the successful tunnel input/output streams are surfaced,
- how CONNECT response code and headers are surfaced,
- how proxy auth participates.

### M3.2 Eliminate synthetic Upgrade/ALPN marker

Known concern:

Current CONNECT-only plumbing is tied to `HTTPUpgrade()` and may copy the upgrade protocol into an `ALPN` CONNECT header.

The prototype must not send a fake `ALPN: webrtc`, `ALPN: naivefox`, or similar marker just to get a callback.

Investigate whether current Firefox already has another internal raw-connect path.

If not, implement the smallest maintainable Necko hook.

Preferred properties:

- generic enough to mean "raw proxy CONNECT stream",
- no Naive-specific behavior in core Necko unless unavoidable,
- no change to normal Firefox browsing behavior,
- no H3 work,
- covered by a focused test.

Any modification to existing Firefox files goes into `UPSTREAM.md`.

Acceptance:

- [x] successful H2 CONNECT yields async input/output streams,
- [x] packet/decrypted-header inspection confirms no synthetic project-specific Upgrade/ALPN header,
- [x] existing Firefox CONNECT tests still pass.

The current internal path attaches an explicit HTTPS `nsIProxyInfo`, with
resolve flags cloned to prefer HTTPS proxying and always use CONNECT, to an
HTTP target channel. The HTTP target scheme prevents Necko from adding target
TLS. `setConnectOnly(false)` followed by an empty `HTTPUpgrade()` requests raw
stream takeover. The empty value is never copied to request headers, and a
focused H2 proxy test inspects the CONNECT header block for absence of `ALPN`,
`Upgrade`, and `Connection` markers. A small `nsHttpConnection` guard is needed
so a first-use HTTPS proxy may finish its outer H2 bootstrap before the
connect-only transaction is restarted onto an H2 tunnel stream.

### M3.3 Hard-coded raw tunnel smoke test

Before SOCKS, use the mandatory local fixture:

```text
proxy = local Naive-compatible Caddy fixture
target = deterministic local target
```

Write known bytes through the CONNECT tunnel and verify the target-side response, or tunnel a simple request. Do not wait for supplied remote credentials.

Acceptance:

- [x] outer proxy TLS is handled by NSS,
- [x] negotiated protocol is HTTP/2,
- [x] CONNECT returns 200,
- [x] bytes can flow both directions,
- [x] close/error handling works.

Validated against the pinned local Caddy fixture with
`test/integration/run-raw-connect-tests.sh`. The client sent an HTTP request
over the returned raw streams, verified the deterministic response marker,
and closed cleanly.

---

## Phase 4 — proxy authentication

### M4.1 Integrate credentials

Use normal Necko proxy authentication where practical.

Support credentials supplied without committing them.

Suggested test environment variables:

```bash
NAIVEFOX_PROXY_USER
NAIVEFOX_PROXY_PASS
```

Acceptance:

- [x] valid credentials -> CONNECT 200,
- [x] invalid credentials -> clean failure,
- [x] missing required credentials -> clean failure,
- [x] no password appears in logs.

The same local raw-CONNECT runner verifies valid, invalid, and absent
credentials. The proxy authorization value is created in memory and is never
printed; both negative cases return CONNECT 407 and
`NS_ERROR_PROXY_AUTHENTICATION_FAILED`.

Run valid, invalid, and missing-credential paths against the local fixture. The supplied real server requires only the successful valid-credential interoperability path in M8.3.

---

## Phase 5 — local SOCKS5 server

### M5.1 SOCKS parser/state machine

Implement:

```text
client greeting
-> method selection
-> CONNECT request
-> target extraction
-> tunnel result
-> SOCKS reply
```

Support:

- IPv4,
- IPv6,
- domain names.

Only `CONNECT` is required.

Reject:

- BIND,
- UDP ASSOCIATE,
- unsupported auth methods.

Tests must fragment input at arbitrary boundaries.

### M5.2 Remote destination DNS

For a domain target:

```text
example.com:443
```

do not perform a local target DNS lookup.

Pass the domain name through as CONNECT authority.

Acceptance:

A test proves domain targets can be proxied without an application-side DNS resolution step.

### M5.3 SOCKS -> raw H2 CONNECT end to end

Wire:

```text
curl
-> localhost SOCKS5
-> NaiveFox
-> raw Necko H2 CONNECT
-> proxy
-> target
```

Use:

```bash
curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

Acceptance:

- [x] HTTP and HTTPS target requests succeed through the local Caddy fixture,
- [x] curl validates the HTTPS target certificate end to end with the scoped fixture CA via `--cacert`, never `-k`,
- [x] a hostname request reaches Caddy unchanged as the CONNECT authority,
- [x] NaiveFox only sees opaque tunneled bytes after CONNECT,
- [x] multiple sequential requests work.

At this stage payload padding may still be disabled.

---

## Phase 6 — Naive CONNECT header padding negotiation

### M6.1 Verify current CONNECT header construction

Do not assume:

```cpp
SetRequestHeader("padding", ...)
```

automatically reaches the proxy CONNECT.

Trace the current source path.

Known Firefox behavior historically creates a separate CONNECT request head and selectively copies headers.

### M6.2 Send Naive `padding` header

Implement the smallest mechanism that gets the Naive-compatible `padding` header into the actual H2 CONNECT HEADERS.

Follow current NaiveProxy header-padding specification.

Do not change the server.

Do not add unrelated headers.

Add a focused test that inspects the proxy CONNECT headers.

### M6.3 Detect server padding capability

Use Firefox's existing CONNECT response metadata/header API where possible.

Expected conceptual API:

```text
nsIProxiedChannel
  -> CONNECT response code
  -> CONNECT response header lookup
```

Acceptance:

Against the local `forwardproxy@naive` fixture:

- [x] request includes `padding`,
- [x] CONNECT succeeds,
- [x] server response includes `padding`,
- [x] NaiveFox records `padding negotiated = true`.

Repeat successful negotiation against the supplied real server during M8.3.

Add a focused component test for negotiation fallback:

- [x] a successful CONNECT response containing `padding` enables payload padding,
- [x] a successful CONNECT response without `padding` leaves the tunnel in raw mode,
- [x] absence of padding capability is not treated as a protocol error.

A second non-Naive proxy fixture is not required.

---

## Phase 7 — Naive payload padding codec

Implement this as a standalone, heavily tested component before putting it in live traffic.

The first prototype implements legacy Naive padding Variant 1 used by the pinned `forwardproxy@naive` fixture. Newer padding-type variants are out of scope.

### M7.1 Encoder

For the first 8 logical padded records in each sending direction:

```text
u16 big-endian payload length
u8 padding length
payload
zero padding
```

Use the legacy Variant 1 specification and the pinned `forwardproxy@naive` implementation as the wire-format source of truth.

After the padded-record quota, send raw bytes.

Split payload chunks > 65535.

Inject RNG dependency so tests can be deterministic.

### M7.2 Decoder

Implement a streaming state machine.

It must support:

- incomplete 3-byte header,
- incomplete payload,
- incomplete padding,
- coalesced records,
- arbitrary I/O chunking,
- exact transition from last padded record to raw stream.

### M7.3 Codec tests

Required:

- [x] 1-byte payload,
- [x] 0-byte/edge behavior as defined by upstream,
- [x] padding 0,
- [x] padding 255,
- [x] payload 65535,
- [x] payload >65535 split,
- [x] every boundary fragmentation,
- [x] many coalesced records,
- [x] last padded record + raw tail in same input buffer,
- [x] malformed/truncated record,
- [x] deterministic round-trip randomized property test.

Acceptance:

Unit tests pass without real network access.

---

## Phase 8 — padded end-to-end tunnel

### M8.1 Integrate codec into `DuplexPump`

Path:

```text
SOCKS input
-> Naive encoder
-> Necko tunnel output

Necko tunnel input
-> Naive decoder
-> SOCKS output
```

Only enable codec when CONNECT padding negotiation succeeded.

Keep send and receive padded-record counters independent.

### M8.2 Local Caddy interoperability suite

Run the functional padded fixture path with `padding negotiated = true`.

Test:

- [x] HTTP target through SOCKS,
- [x] HTTPS target through SOCKS with scoped `--cacert` validation and no verification bypass,
- [x] at least one deterministic multi-megabyte download,
- [x] deterministic upload with byte-count and hash verification,
- [x] multiple sequential connections.

Verify byte-for-byte integrity with hashes. The runner must use readiness timeouts, clean up all child processes on success or failure, and preserve only sanitized diagnostics.

Concurrency, backpressure, and forced close paths belong to Phase 9 and are not duplicated here.

Acceptance:

One documented command creates isolated fixture state, runs the functional M8 suite, and exits successfully without a remote proxy/server or supplied credentials. Initial dependency download or fixture-binary construction may still require network access.

### M8.3 Supplied real Caddy interoperability

After M8.2 passes, use the user's supplied server and credentials from a non-committed source.

Acceptance:

```bash
curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

works with `padding negotiated = true`.

Confirm one normal HTTPS request and one bounded integrity-checked transfer. Use the server's normal public certificate validation and do not carry local fixture CA overrides into this test. Concurrency, invalid credentials, and forced failure paths remain local-fixture tests unless diagnosing a real interoperability problem.

Status on 2026-08-13: **complete**. Credentials were supplied out of tree and
the public-CA deployment passed legacy Variant 1 negotiation with no trust
override. The acceptance run kept one client process alive for 120 seconds and
covered normal HTTPS pages, raw GitHub content, an integrity-checked Caddy
source archive, and six spaced waves of four parallel page/download requests.
All responses were HTTP 200, the direct/proxied archive SHA-256 matched, and all
29 resulting SOCKS connections negotiated padding.

The real deployment exposed one Firefox-specific integration bug: the
synthetic `http://authority/` URI used solely to construct CONNECT was upgraded
by HSTS for preloaded targets such as GitHub. The internal tunnel builder now
sets `allowSTS = false` on that non-navigational channel so explicit proxy
routing is retained. TLS, HTTP/2, and the observable outer wire behavior remain
implemented by Firefox Necko/NSS; no Chromium camouflage or preamble behavior
was copied.

Commands, bounded workload metrics, integrity hashes, and packaged-runtime
results are recorded in `TEST-REPORT.md`.

---

## Phase 9 — robustness and lifecycle

### M9.1 Backpressure

Prove bounded memory behavior when:

- local client sends faster than proxy accepts,
- proxy sends faster than local client consumes.

No unbounded append-only buffers.

### M9.2 Connection lifecycle

Test:

- local disconnect,
- proxy disconnect,
- target closes,
- failed CONNECT,
- auth failure,
- malformed padding,
- timeout,
- application half-close where supported/appropriate.

### M9.3 Concurrency

Run multiple simultaneous SOCKS CONNECT streams.

Verify whether Firefox reuses a single H2 proxy connection/multiplexes streams as expected.

Do not implement an independent connection pool unless Firefox's normal pooling cannot meet the requirement.

Acceptance:

- [x] a 32 MiB slow-consumer download and slow-target upload preserve byte
  integrity while NaiveFox's `VmRSS` delta stays below 32 MiB,
- [x] local disconnect, proxy disconnect, target early close, failed CONNECT,
  invalid authentication, timeout, and application half-close terminate
  without a crash or leaked finite server process,
- [x] malformed and truncated padding is rejected by the bounded codec tests,
- [x] four simultaneous SOCKS CONNECT streams complete through one established
  outer proxy TCP connection.

Completed locally with
`test/integration/run-robustness-tests.sh`. The test uses Firefox's normal H2
pooling; NaiveFox does not implement a separate connection pool.

---

## Phase 10 — Firefox behavior / capture validation

This phase validates the original reason for the project.

### M10.1 Reference Firefox capture

From the same source revision/build family, capture an ordinary Firefox HTTPS connection to the same proxy/front-end host if possible.

### M10.2 NaiveFox capture

Capture NaiveFox establishing the outer HTTPS/H2 proxy session.

Use developer TLS key logging if needed and available to inspect encrypted H2 contents in Wireshark.

### M10.3 Compare

Document:

- TLS ClientHello,
- ALPN,
- TLS extensions/cipher configuration,
- HTTP/2 SETTINGS,
- early WINDOW_UPDATE behavior,
- initial H2 frame ordering,
- connection reuse,
- any unexpected NaiveFox-only header or protocol marker.

Expected difference:

NaiveFox issues CONNECT, while ordinary browsing issues normal requests. Application-level request differences are unavoidable.

Failure:

If NaiveFox accidentally bypasses Firefox behavior by using another TLS/H2 stack, the milestone fails.

Do not add speculative Chromium camouflage patches.

If a measurable Firefox-specific anomaly is caused by our integration, document it first, then propose the smallest fix.

Acceptance:

- [x] ordinary Firefox and NaiveFox were captured from the same local build
  family against the same fixture TLS front-end,
- [x] both selected `h2`, and their ordered ClientHello fields and client H2
  SETTINGS matched,
- [x] two NaiveFox CONNECT requests used distinct stream IDs on one outer TCP
  connection,
- [x] Naive `padding` was present in both directions and no synthetic
  `ALPN`, `Upgrade`, or `Connection` header marker was present,
- [x] only sanitized metadata was retained; pcap files, NSS key logs, copied
  profiles, screenshots, and raw logs were deleted after the successful run.

The reproducible procedure and 2026-08-12 comparison record are in
`CAPTURE.md`; `test/integration/run-capture-comparison.sh` performs the capture,
safe extraction, assertions, and sensitive-data cleanup.

The strict HTTP/3 equivalent is complete. Ordinary Firefox and NaiveFox from
the same build family both used QUIC v1 and `h3`; semantic TLS configuration,
client QUIC transport parameters, and HTTP/3/QPACK settings matched. Two
classic CONNECT request streams shared one NaiveFox QUIC connection, padding
was visible in both header directions, and no synthetic marker header existed.
The independent passive pass used no key log and established no TCP session.
See `H3-CAPTURE.md` and
`test/integration/run-h3-capture-comparison.sh`; raw pcaps, keys, profiles,
bodies, screenshots, and logs were deleted after aggregation.

---

## Phase 11 — prototype packaging

Only after functional and capture milestones pass.

### M11.1 Determine runtime dependencies

Identify the minimum practical runtime set around the built `naivefox` executable.

Expected model:

```text
naivefox
libxul.so
NSS/NSPR
mozglue
other required Gecko runtime files/resources
```

A single static executable is not required.

The current minimum-practical staged runtime is 378 MiB after stripping debug
sections. It contains the dependent Gecko/NSS libraries plus dereferenced GRE
resources; GTK, GLib, X11/Wayland, font, audio, C++ runtime, and libc remain
normal system dependencies.

### M11.2 Create repeatable staging command/script

Produce a staging directory or archive such as:

```text
naivefox-linux-x86_64/
```

Do not accidentally include:

- test credentials,
- build logs,
- packet captures with secrets,
- full source tree.

Acceptance:

A fresh compatible Linux environment can run the staged prototype with its required runtime files.

The repeatable staging and verification commands are:

```bash
./netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64
./netwerk/naivefox/tools/verify-staged-runtime.sh naivefox-linux-x86_64
```

Verification copies the package under `/tmp`, uses a separate writable profile,
checks `ldd` for build-tree or unresolved dependencies, and runs the headless
runtime smoke test without inheriting build-tree loader paths.

---

## Phase 12 — HTTP/3/QUIC through Necko and Neqo

This phase starts from the immutable `h2-prototype-v0.1` tag on
`feature/h3`. It does not update the Firefox snapshot or introduce a second
client architecture.

### M12.1 Document and select the transport

- [x] Record the current Firefox H3 HTTPS-proxy, classic CONNECT,
  `Http3StreamTunnel`, Neqo, header, and socket-process paths in
  `H3-DESIGN.md`.
- [x] Add `--protocol h2|h3|auto` to the existing executable and pass the
  selection through the existing runner, SOCKS server, connection, and tunnel
  layers.
- [x] Preserve `h2` as the default.
- [x] Log only the selected outer protocol, never credentials or
  `Proxy-Authorization`.
- [x] Make strict H3 suppress the timed backup, restart conversion, and Happy
  Eyeballs TCP route while leaving ordinary Firefox fallback unchanged.
- [x] Make Auto retry H2 exactly once only before any CONNECT response or
  tunnel transport is observed.

### M12.2 Raw regular CONNECT over H3

- [x] Use Firefox `masque` proxy metadata only to select the existing HTTPS
  H3 proxy transport; send classic HTTP CONNECT, not CONNECT-UDP or MASQUE.
- [x] Obtain async bidirectional streams through `Http3StreamTunnel` with an
  empty raw-upgrade token.
- [x] Verify CONNECT 200, known marker I/O, response metadata, and absence of
  synthetic `ALPN`, `Upgrade`, and `Connection` headers.
- [x] Verify request and response `padding` header names through the common
  CONNECT-header path.
- [x] Cover strict failure without H2 fallback.

### M12.3 Shared SOCKS, padding, and lifecycle

- [x] Reuse `Socks5Parser`, `SocksServer`, `SocksConnection`, `DuplexPump`,
  `HeaderPadding`, `PaddingNegotiation`, and the Variant 1 codec unchanged at
  the protocol boundary.
- [x] Verify HTTP and HTTPS targets, remote hostnames, the first eight framed
  records in each direction, and raw traffic afterward.
- [x] Verify 32 MiB slow download/upload integrity and bounded resident-memory
  growth.
- [x] Verify local disconnect, target early close, proxy loss, timeout,
  authentication/ACL failures, and response after client half-close.
- [x] Verify concurrent classic CONNECT streams share one Necko-owned QUIC
  socket; do not add a project connection pool.
- [x] Keep the full H2 workload green.

### M12.4 Deterministic fixture and suites

- [x] Add isolated fixture modes: H2 uses TCP `h1 h2`; strict H3 uses an
  H3-only UDP listener and no TCP listener on the same proxy port.
- [x] Add separate raw, SOCKS, padding, robustness, Auto, H3 aggregate, and
  H2+H3 aggregate runners.
- [x] Pass focused H3 xpcshell tests, project gtests, strict H3 local tests,
  the equivalent H2 regression workload, and a warning-free binary build.
- [x] Verify the same staged runtime outside the object directory in strict H2
  and strict H3 modes; Neqo remains inside `libxul` and needs no second binary.

### M12.5 Measurement and deployment gates

- [x] Benchmark strict H3 NaiveFox against the pinned official NaiveProxy with
  integrity-checked sequential and parallel local transfers.
- [x] Compare decrypted Firefox/NaiveFox H3 internals and separate passive
  observer-visible QUIC traffic; retain only sanitized aggregates.
- [x] Complete an exactly 600-second strict-H3 soak against the supplied real
  Caddy with periodic small/parallel loads and idle windows.
- [x] Record commands, results, limitations, memory, runtime size, and all
  Firefox modifications in the project Markdown reports.

---

## Phase 13 — NaiveProxy-style config and local frontends

This phase remains on the validated Firefox snapshot. It adds user-facing
configuration and local protocol adapters without changing Firefox or Neqo.

### M13.1 Strict config, logging, and profile lifecycle

- [x] Parse a bounded strict JSON object without SpiderMonkey or a new
  dependency; accept `listen` as one string or an array.
- [x] Accept numeric IPv4/IPv6 `socks://` and `http://` bind addresses,
  including wildcard, loopback, and specific LAN addresses; reject malformed,
  unknown, duplicate, wrongly typed, or unsupported input.
- [x] Match NaiveProxy's `proxy` string/array mapping: one URI is shared,
  while two or more URIs map one-to-one to listeners in the same order.
- [x] Map credential-bearing `https://` to strict H2 and `quic://` to strict H3,
  including default port 443, IPv4/IPv6, and percent decoding.
- [x] Implement disabled, console, and mode-`0600` file logging without
  exposing credentials.
- [x] Resolve and create the persistent XDG/HOME profile, with
  `NAIVEFOX_PROFILE` as an explicit override.

### M13.2 Shared tunnel session and HTTP CONNECT frontend

- [x] Extract H2/H3/Auto attempts, CONNECT metadata, padding negotiation,
  transport barriers, and `DuplexPump` ownership from `SocksConnection` into
  one reusable `TunnelSession`.
- [x] Keep SOCKS parsing/replies in `SocksConnection`; add a bounded streaming
  HTTP CONNECT parser and frontend with 200 only after upstream success.
- [x] Preserve bytes received after the HTTP header terminator as initial
  tunnel payload; reject non-CONNECT HTTP methods with 405.
- [x] Serve multiple SOCKS5 and HTTP CONNECT listeners in one process and leave
  outer pooling to the shared Necko connection manager.

### M13.3 Local, real, and packaged acceptance

- [x] Pass 48/48 project gtests, including config and HTTP parser fragmentation,
  authority, size, early-payload, type, scheme, and credential cases.
- [x] Pass mixed concurrent SOCKS5 and HTTP CONNECT workloads over local strict
  H2 and H3 fixtures with negotiated padding and transfer integrity.
- [x] Pass the unchanged H2/H3 raw, padding, robustness, Auto, and capture
  regression suites.
- [x] Pass supplied real-Caddy config workloads over both `https://` and
  `quic://` using public certificate validation and a staged runtime, including
  wildcard listeners and repeated per-listener proxy-array entries.
- [x] Verify `./naivefox` with adjacent `config.json` and positional config from
  a copied package outside the object directory, with no objdir mappings.

---

# Final prototype acceptance suite

The H2 prototype is complete only when this full sequence can be reproduced:

1. Fresh supported Linux build environment.
2. `./mach bootstrap`.
3. clean build.
4. reproducible loopback-only Caddy fixture starts with isolated state.
5. the untrusted fixture NSS profile rejects the proxy certificate.
6. the dedicated trusted NSS profile validates it without global trust changes or verification bypasses.
7. `naivefox` starts headlessly.
8. Necko HTTPS sanity request succeeds.
9. outer proxy connection negotiates H2 via NSS.
10. raw CONNECT tunnel has no synthetic NaiveFox ALPN/Upgrade marker.
11. proxy authentication success, invalid, and missing-credential paths behave correctly.
12. local SOCKS5 CONNECT works.
13. destination domain is passed to proxy without local resolution.
14. `curl --socks5-hostname` works through the local Caddy fixture for HTTP and HTTPS targets.
15. CONNECT `padding` negotiation succeeds with Naive-compatible Caddy.
16. payload codec tests pass.
17. the complete padded local end-to-end suite passes from one documented command.
18. large download/upload integrity tests pass.
19. repeated and concurrent connection tests pass.
20. close/error lifecycle tests pass.
21. supplied real Caddy interoperability passes with normal public certificate validation.
22. existing touched Firefox CONNECT tests pass.
23. capture comparison is documented.
24. all upstream Firefox modifications are listed in `UPSTREAM.md`.
25. prototype runtime can be staged outside the build tree.
26. strict NaiveProxy-style config mode works without developer flags.
27. simultaneous SOCKS5 and HTTP CONNECT listeners share one tunnel backend.
28. config-mode strict H2 and H3 pass local, real, and staged acceptance.
29. configured wildcard and specific non-loopback listener addresses bind and
    accept clients without an implicit loopback policy.
30. `proxy` string/array behavior matches NaiveProxy's shared and one-to-one
    listener mapping, and the package exposes only one root launcher.

Final prototype status on 2026-08-13: all items 1-30 pass. In particular, the
supplied real Caddy passed normal public-certificate validation, the H2
interoperability workload and ten-minute H2 soak, and the strict-H3
preflight plus exactly 600-second H3 soak without hidden H2 fallback. Commands,
integrity gates, load schedules, resource measurements, and sanitized results
are recorded in `TEST-REPORT.md`. Current architectural constraints and
non-blocking observations are recorded in `KNOWN-ISSUES.md`.

Run all reproducible local H2 and H3 integration gates sequentially with:

```bash
./netwerk/naivefox/test/integration/run-full-suite.sh
```

The H2 acceptance point is preserved by the `h2-prototype-v0.1` tag. The
user-approved HTTP/3/Neqo continuation is tracked separately in Phase 12 and
must not weaken any item in this H2 suite. Native Windows, Android, and size
reduction remain future work.
