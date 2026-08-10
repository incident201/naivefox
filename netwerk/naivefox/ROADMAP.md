# NaiveFox implementation roadmap

This roadmap takes the repository from a clean Firefox fork to a working Linux HTTP/2 NaiveFox prototype.

Milestones are intentionally sequential. Each milestone must have a reproducible acceptance test before moving to the next one.

The coding agent should mark completed checklist items and record major discoveries as work proceeds.

## Phase 0 — establish the environment and baseline

### M0.1 Verify repository state

- [ ] Confirm current branch is `naivefox`.
- [ ] Confirm `main` is not being modified.
- [ ] Confirm `origin` points to `incident201/naivefox`.
- [ ] Add/verify `upstream` = `https://github.com/mozilla-firefox/firefox.git`.
- [ ] Confirm checkout is on native WSL Linux storage.
- [ ] Record current upstream/base commit SHA in `UPSTREAM.md`.
- [ ] Read root `AGENTS.md` and all NaiveFox docs.

Acceptance:

```bash
git status
git branch --show-current
git remote -v
```

show a clean development branch and correct remotes.

### M0.2 Bootstrap Firefox build dependencies

- [ ] Run `./mach bootstrap`.
- [ ] Select full Firefox Desktop build.
- [ ] Do not use Artifact Mode.
- [ ] Let Mozilla tooling install the appropriate compiler/toolchain.
- [ ] Record any required local environment changes.

Acceptance:

```bash
./mach --version
```

and normal build configuration commands work without missing-tool errors.

### M0.3 Clean baseline build

Before any implementation change:

- [ ] Run a complete `./mach build`.
- [ ] Save build output under `artifacts/`.
- [ ] Record object directory and compiler.
- [ ] Do not proceed if the untouched checkout fails to build.

Acceptance:

Firefox baseline build exits successfully.

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

- [ ] Firefox tree builds.
- [ ] `naivefox` is produced in the object/runtime output.
- [ ] `naivefox --version` or equivalent runs successfully.
- [ ] No browser window appears.

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

- [ ] `naivefox` starts headlessly.
- [ ] initializes networking runtime,
- [ ] runs an event-loop smoke test,
- [ ] shuts down with no assertion/crash.

### M2.2 Normal HTTPS sanity request

Before implementing proxy CONNECT, prove that the process can make an ordinary HTTPS request using Necko.

Requirements:

- use Necko channel APIs,
- use NSS/PSM TLS,
- do not call curl/libcurl,
- log HTTP status and a small bounded result,
- verify certificate validation works.

Acceptance:

A request to a public HTTPS endpoint succeeds through Necko/NSS.

Negative test:

A deliberately invalid/untrusted TLS endpoint must fail certificate validation unless an explicit test trust setup is used.

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

- [ ] successful H2 CONNECT yields async input/output streams,
- [ ] packet/decrypted-header inspection confirms no synthetic project-specific Upgrade/ALPN header,
- [ ] existing Firefox CONNECT tests still pass.

### M3.3 Hard-coded raw tunnel smoke test

Before SOCKS, hard-code:

```text
proxy = supplied or local test proxy
target = known HTTP/TLS server
```

Write known bytes through the CONNECT tunnel and verify the target-side response, or tunnel a simple request.

Acceptance:

- [ ] outer proxy TLS is handled by NSS,
- [ ] negotiated protocol is HTTP/2,
- [ ] CONNECT returns 200,
- [ ] bytes can flow both directions,
- [ ] close/error handling works.

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

- [ ] valid credentials -> CONNECT 200,
- [ ] invalid credentials -> clean failure,
- [ ] missing required credentials -> clean failure,
- [ ] no password appears in logs.

If the provided Caddy server uses Basic auth, verify the actual behavior against it.

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

- [ ] HTTPS request succeeds through supplied Caddy,
- [ ] curl sees the target certificate/end-to-end TLS normally,
- [ ] NaiveFox only sees opaque tunneled bytes after CONNECT,
- [ ] multiple sequential requests work.

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

Against the supplied `forwardproxy@naive` server:

- [ ] request includes `padding`,
- [ ] CONNECT succeeds,
- [ ] server response includes `padding`,
- [ ] NaiveFox records `padding negotiated = true`.

Against a regular HTTP/2 proxy without Naive padding:

- [ ] CONNECT can still work,
- [ ] no server padding header,
- [ ] payload padding remains disabled.

---

## Phase 7 — Naive payload padding codec

Implement this as a standalone, heavily tested component before putting it in live traffic.

### M7.1 Encoder

For the first 8 logical padded records in each sending direction:

```text
u16 big-endian payload length
u8 padding length
payload
zero padding
```

Use current upstream NaiveProxy as source of truth.

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

- [ ] 1-byte payload,
- [ ] 0-byte/edge behavior as defined by upstream,
- [ ] padding 0,
- [ ] padding 255,
- [ ] payload 65535,
- [ ] payload >65535 split,
- [ ] every boundary fragmentation,
- [ ] many coalesced records,
- [ ] last padded record + raw tail in same input buffer,
- [ ] malformed/truncated record,
- [ ] deterministic round-trip randomized property test.

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

### M8.2 Real Caddy interoperability

Use the user's supplied server.

Acceptance:

```bash
curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

works with `padding negotiated = true`.

Also test:

- [ ] HTTP target through SOCKS,
- [ ] HTTPS target through SOCKS,
- [ ] at least one multi-megabyte download,
- [ ] upload if practical,
- [ ] repeated connections,
- [ ] concurrent connections,
- [ ] connection close during transfer.

Verify byte-for-byte integrity with hashes for large deterministic test payloads.

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

---

# Final prototype acceptance suite

The H2 prototype is complete only when this full sequence can be reproduced:

1. Fresh supported Ubuntu/WSL environment.
2. `./mach bootstrap`.
3. clean build.
4. `naivefox` starts headlessly.
5. Necko HTTPS sanity request succeeds.
6. outer proxy connection negotiates H2 via NSS.
7. raw CONNECT tunnel has no synthetic NaiveFox ALPN/Upgrade marker.
8. proxy authentication succeeds.
9. local SOCKS5 CONNECT works.
10. destination domain is passed to proxy without local resolution.
11. `curl --socks5-hostname` works through Caddy.
12. CONNECT `padding` negotiation succeeds with Naive-compatible Caddy.
13. payload codec tests pass.
14. padded end-to-end traffic succeeds.
15. large transfer integrity test passes.
16. concurrent connection test passes.
17. existing touched Firefox CONNECT tests pass.
18. capture comparison is documented.
19. all upstream Firefox modifications are listed in `UPSTREAM.md`.
20. prototype runtime can be staged outside the build tree.

After this point, and only after user approval, future work may consider HTTP/3/Neqo, native Windows, Android, or size reduction.
