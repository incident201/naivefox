# NaiveFox

NaiveFox is an experimental headless proxy client built inside the Firefox/Gecko source tree.

The prototype reuses Firefox's **real networking stack** instead of manually imitating a browser fingerprint:

- **Necko** for HTTP networking, HTTP/2, and HTTP/3 behavior.
- **NSS/PSM** for TLS.
- Firefox's normal HTTP/2 and Neqo HTTP/3 implementations, connection
  management, HPACK/QPACK, TLS parameters, and related network behavior.
- A small NaiveFox-specific layer for the local SOCKS5 server, HTTP CONNECT tunnel orchestration, Naive padding compatibility, configuration, logging, and stream pumping.

The first target is **Linux x86_64 only**. Development uses Firefox's normal `mach` build system and a supported Linux build environment.

The tagged `h2-prototype-v0.1` baseline is preserved. The `feature/h3` stage
adds strict HTTP/3/QUIC through the same executable and project architecture;
there is no separate H3 client, SOCKS server, pool, or padding implementation.
The combined H2/H3 prototype is recorded by `h2-h3-prototype-v0.2`. Current
architectural constraints and non-blocking observations are centralized in
[`KNOWN-ISSUES.md`](KNOWN-ISSUES.md).

## Repository model

This repository is a fork of:

- Upstream Firefox: https://github.com/mozilla-firefox/firefox
- Project fork: https://github.com/incident201/naivefox

The long-lived branch model is:

```text
mozilla-firefox/firefox:main
             |
             v
incident201/naivefox:main       <- keep clean, mirror upstream
             |
             v
incident201/naivefox:naivefox   <- project development branch
```

Do not develop directly on `main`.

Project-specific code and documentation should live under:

```text
netwerk/naivefox/
```

Existing Firefox files should be modified only when no suitable existing internal API can solve the problem. Every upstream modification must be small, isolated, justified, tested, and documented in `UPSTREAM.md`.

The root Firefox `README.md` and root Firefox `AGENTS.md` are upstream files and must not be replaced by this project.

## Goal

The end-to-end prototype should provide a local SOCKS5 endpoint:

```text
Application
    |
    | SOCKS5 CONNECT
    v
NaiveFox
    |
    | Necko + NSS
    | HTTPS connection to proxy
    | --protocol h2: TLS/TCP, ALPN h2
    | --protocol h3: QUIC/UDP, ALPN h3
    | regular HTTP CONNECT target.example:443
    v
Existing Caddy + klzgrad/forwardproxy@naive
    |
    v
Target server
```

Example user-facing behavior:

```bash
export NAIVEFOX_PROXY_USER='user'
export NAIVEFOX_PROXY_PASS='pass'

./run-naivefox \
  --profile /path/to/writable-nss-profile \
  --socks-listen 127.0.0.1:1080 \
  --proxy https://proxy.example.com:443 \
  --protocol h3

curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

The prototype is successful when traffic flows through the user's existing Naive-compatible Caddy server, with the server unchanged.

## Server compatibility

Both the local integration fixture and the supplied real test server are expected to use a normal Naive-compatible Caddy build with the Naive fork of `forwardproxy`, equivalent to:

```bash
xcaddy build \
  --with github.com/caddyserver/forwardproxy=github.com/klzgrad/forwardproxy@naive
```

NaiveFox must interoperate with that existing server. Do not require a custom NaiveFox server. The command above describes compatibility; the committed local fixture must pin an exact tested Caddy version and immutable `forwardproxy` commit instead of resolving a moving branch on every run.

The user may provide a real test server address and credentials for final interoperability validation. Their absence must not block development because the repository must include a reproducible local Caddy fixture.

Never commit test credentials, proxy passwords, private keys, TLS keys, packet captures containing secrets, or generated NSS profile secrets.

## Integration test strategy

Testing has two distinct gates.

| Gate | Purpose |
|---|---|
| Local Caddy fixture | Reproducible development and regression testing without external credentials |
| Supplied real Caddy | Final interoperability validation against the user's deployment, DNS, public certificate, and production-like configuration |

The local fixture runs directly in the provided Linux build environment and uses:

- a dedicated Caddy binary built from pinned Caddy and `forwardproxy@naive` revisions,
- a loopback-only, explicitly TLS-enabled catch-all listener on an unprivileged port,
- an ACL and allowed-port list restricted to the local target,
- Basic Auth credentials generated outside the source tree,
- isolated Caddy internal-PKI state with `skip_install_trust`,
- a dedicated NSS profile containing only the fixture CA trust,
- a second untrusted NSS profile for the required certificate-failure test,
- a deterministic local HTTP/HTTPS target for integrity, upload, close, delay, and concurrency tests.

The fixture must not install its CA globally, modify a normal Firefox profile, disable certificate validation, start a system Caddy service, or expose an open proxy. Generated Caddy binaries, CA material, credentials, NSS databases, logs, and captures belong under the object directory and are not committed.

The automated runners cover certificate rejection/trust, strict `h2` and
strict `h3`, Basic Auth success and failure, raw CONNECT, SOCKS remote-hostname
semantics, padding negotiation, deterministic transfer hashes, concurrency,
backpressure, half-close, and shutdown paths. H3-only fixture mode exposes a
UDP listener with no TCP listener on the proxy port, so hidden H2 fallback
cannot make a strict H3 test pass. The supplied real server is tested only
after the local suite passes.

Run the bounded real-deployment gate with credentials supplied only through the
environment:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
netwerk/naivefox/test/integration/run-real-server-tests.sh
```

The default runner keeps one client process alive for two minutes, visits
several ordinary HTTPS pages, compares a direct and proxied GitHub archive by
SHA-256, and runs spaced four-request parallel waves. Generated bodies,
profiles, and client logs stay under the object directory; only a
credential-free summary is copied to the ignored `artifacts/` directory. Set
`NAIVEFOX_REAL_DURATION_SECONDS` to a bounded value from 30 through 300 when a
shorter or longer manual session is needed.

The strict H3 real-deployment soak is a separate ten-minute gate. It first
proves H3 and transfer integrity, then observes the same process for exactly
600 seconds with periodic small and parallel requests, two deliberate idle
windows, resource sampling, and a requirement that the proxy path uses UDP
without a TCP fallback:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
netwerk/naivefox/test/integration/run-real-server-h3-soak.sh
```

`netwerk/naivefox/tools/fetch-naiveproxy-reference.sh` downloads the pinned
official Linux client and verifies the GitHub release SHA-256. It is a
behavioral and performance reference, not a wire-shaping target.
NaiveFox deliberately does not copy Chromium-specific preambles or camouflage:
its TLS and HTTP/2 behavior must continue to come from Firefox Necko/NSS.
`run-reference-server-tests.sh` applies a separate, bounded long-lived workload
to that official client using a private generated config, so future comparisons
do not depend on fragile one-line invocations.

See `AGENTS.md` for fixture construction and trust procedure, `ROADMAP.md` for
milestone acceptance gates, and `TEST-REPORT.md` for the committed local,
real-deployment, reference-client, and packaged-runtime results.

## Non-goals

The first prototype does **not** need:

- Android support.
- Windows-native support.
- TUN/TAP support.
- Transparent proxying.
- UDP ASSOCIATE.
- SOCKS5 BIND.
- GUI.
- Browser UI.
- A single statically linked executable.
- Minimizing Firefox/libxul size.
- Matching Chromium.
- Copying Chromium-specific NaiveProxy camouflage patches.
- Full resistance to traffic analysis.
- Production hardening.

The H3 stage deliberately does not implement CONNECT-UDP, MASQUE,
WebTransport, UDP ASSOCIATE, a standalone Neqo client, or manual QUIC
fingerprint shaping.

## Why Firefox instead of Chromium

Original NaiveProxy reuses Chromium's networking stack. NaiveFox explores the same general idea with Firefox:

> Do not manually synthesize a Firefox TLS/HTTP fingerprint. Run the real Firefox networking implementation.

The outer network stack should therefore remain as close as practical to unmodified Firefox behavior.

Do not replace Necko HTTP/2 with nghttp2, curl, Boost.Beast, a custom HTTP/2 implementation, or another networking library.

Do not replace NSS with OpenSSL, BoringSSL, rustls, or another TLS implementation.

If a low-level Firefox internal change is required, prefer the smallest generic hook that allows NaiveFox to use the existing stack.

## High-level architecture

```text
NaiveFoxApp
|
+-- GeckoRuntime
|   +-- XPCOM startup/shutdown
|   +-- profile/runtime setup
|   +-- preferences needed for a headless networking process
|   +-- Necko / PSM / NSS initialization
|
+-- Config
|   +-- strict NaiveProxy-compatible JSON schema
|   +-- multiple loopback SOCKS5 / HTTP CONNECT listeners
|   +-- strict H2 (`https://`) or H3 (`quic://`) upstream
|   +-- persistent profile and logging policy
|
+-- LocalProxyServer
|   +-- one Gecko runtime and one Necko connection manager
|   +-- SocksConnection: parse SOCKS5 CONNECT and produce SOCKS replies
|   +-- HttpConnectConnection: parse HTTP CONNECT and produce HTTP replies
|   +-- preserve domain names for upstream resolution
|
+-- TunnelSession
|   +-- shared H2/H3/Auto attempt and fallback lifecycle
|   +-- CONNECT metadata and padding negotiation
|   +-- expose one established target tunnel to either frontend
|
+-- NeckoTunnel
|   +-- create an explicit HTTPS or QUIC proxy configuration
|   +-- require the configured Firefox H2 or H3 transport
|   +-- issue CONNECT host:port
|   +-- expose async tunnel input/output streams
|   +-- expose CONNECT status and response headers
|
+-- NaivePadding
|   +-- CONNECT header padding negotiation
|   +-- payload encoder
|   +-- payload decoder
|
+-- DuplexPump
    +-- local frontend -> Naive encode -> Necko tunnel output
    +-- Necko tunnel input -> Naive decode -> local frontend
    +-- bounded buffering and backpressure
    +-- shutdown/error propagation
```

Keep these responsibilities separate. In particular, `NaivePadding` should be testable without Necko or real sockets.

## SOCKS5 behavior

The first implementation should:

- Listen on loopback by default, e.g. `127.0.0.1:1080`.
- Support SOCKS5 `CONNECT`.
- Support destination address types:
  - IPv4.
  - IPv6.
  - Domain name.
- Reject unsupported commands with the correct SOCKS5 reply.
- Require no authentication on the local SOCKS endpoint for the prototype.
- Never listen on a non-loopback address by default.
- Avoid local DNS resolution when the client supplied a domain name.

For a SOCKS domain request such as:

```text
example.com:443
```

the CONNECT authority sent to the upstream proxy should remain:

```text
example.com:443
```

The upstream proxy should resolve the target name.

This is important both for proxy semantics and for avoiding local DNS leaks.

## Proxy configuration

Normal use follows the small NaiveProxy-compatible JSON subset below. With no
arguments, `naivefox` reads `./config.json`; one positional argument selects a
different file.

```json
{
  "listen": [
    "socks://127.0.0.1:1080",
    "http://127.0.0.1:8080"
  ],
  "proxy": "https://user:password@proxy.example:443",
  "log": ""
}
```

`listen` may be one string or a non-empty array. `socks://` provides SOCKS5
CONNECT; `http://` provides HTTP CONNECT only. Multiple listeners share one
process, Gecko runtime, Necko connection manager, upstream tunnel backend, and
padding implementation. Listener addresses are deliberately restricted to
`127.0.0.1`, `localhost`, or `[::1]`, and require an explicit nonzero port.
Ordinary forward-proxy GET/POST requests sent to the HTTP listener return 405.

The proxy URI contains percent-encoded credentials and selects a strict outer
transport:

- `https://user:password@host[:port]` requires H2;
- `quic://user:password@host[:port]` requires H3/QUIC without H2 fallback.

The default proxy port is 443. Username and password are decoded once, passed
to the existing Necko proxy-auth path, and never written to runtime logs.
Unknown or duplicate fields, malformed JSON, wrong types, unsupported schemes,
and unsafe endpoints are rejected rather than ignored.

Logging is disabled when `log` is absent, goes to the console when it is an
empty string, and appends to a mode-`0600` file for any non-empty path. The
normal config mode creates a writable persistent Firefox/NSS profile at
`$XDG_STATE_HOME/naivefox/profile`, or
`$HOME/.local/state/naivefox/profile` when XDG state is unset. Set
`NAIVEFOX_PROFILE` to override that location. Developer/test CLI modes retain
their explicit `--profile`, `--protocol`, and environment-only credential
interfaces.

## HTTP/2 tunnel requirements

The upstream connection must be an HTTPS connection handled by Firefox's stack.

For the H2-only prototype:

- HTTP/3 must be disabled for the tunnel path.
- HTTP/2 must be allowed.
- The agent must verify that ALPN actually negotiates `h2`.
- The CONNECT request must be generated and transmitted through Firefox's HTTP/2 implementation.
- On successful CONNECT, NaiveFox needs asynchronous bidirectional streams representing the tunnel payload.
- The target TLS session belongs to the application using SOCKS, not to Necko. Necko must not automatically establish a second TLS connection to the CONNECT target.

Relevant Firefox implementation areas include:

```text
netwerk/protocol/http/nsIHttpChannelInternal.idl
netwerk/protocol/http/HttpBaseChannel.cpp
netwerk/protocol/http/nsHttpConnection.cpp
netwerk/protocol/http/Http2Session.cpp
netwerk/protocol/http/Http2StreamTunnel.cpp
netwerk/test/unit/test_proxyconnect.js
netwerk/test/unit/test_proxyconnect_headers.js
```

The exact internal API path must be verified against the current checkout before implementation.

### Important raw-CONNECT caveat

Current Firefox `setConnectOnly()` is tied to an `HTTPUpgrade()` listener. The existing implementation uses the upgrade protocol to generate an `ALPN` header in the CONNECT request.

NaiveFox must **not** ship a fake `ALPN: webrtc` or similar project-specific marker merely to obtain raw streams.

The agent must investigate the current code and implement the smallest maintainable solution that:

1. exposes the successful CONNECT tunnel streams,
2. does not add a synthetic protocol header to the wire,
3. preserves normal Firefox HTTP/2 behavior.

This may require a small internal Necko hook. Treat this as an expected engineering task, not a reason to bypass Necko.

## Naive padding

Naive padding is separate from Firefox/Chrome TLS fingerprinting.

Firefox's real stack handles the outer TLS and HTTP/2 behavior. Naive padding addresses observable length patterns created by tunneling another protocol inside HTTP/2 CONNECT.

The prototype should implement the Naive-compatible padding protocol used by current NaiveProxy and `forwardproxy@naive`.

Reference:

https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification

The first prototype targets legacy Naive padding Variant 1 as implemented by the pinned `forwardproxy@naive` fixture: eight padded records per direction followed by raw bytes. Newer NaiveProxy padding variants are not part of this prototype.

### CONNECT header negotiation

Naive-compatible clients put a `padding` header in the CONNECT request.

The server returns a `padding` header if it supports the padding protocol.

Payload padding is enabled only after server support has been established.

The request padding length used by current NaiveProxy is randomized in the range documented by upstream NaiveProxy. Do not invent a different wire protocol during the initial compatibility implementation.

Firefox normally constructs proxy CONNECT headers separately from the original request headers. The agent must verify the current source path and add only the minimal hook required to get the Naive `padding` header into the actual proxy CONNECT request.

Firefox already exposes CONNECT response metadata through `nsIProxiedChannel`, including the CONNECT status and response headers. Prefer using that existing machinery to detect the server's `padding` response header.

### Payload framing

Current upstream NaiveProxy documents padding for the first 8 reads/writes in each direction:

```text
+-------------------------+
| original_size_hi : u8   |
| original_size_lo : u8   |
| padding_size     : u8   |
+-------------------------+
| original payload        |
+-------------------------+
| zero padding            |
+-------------------------+
```

Where:

- `original_size` is a big-endian 16-bit length.
- `padding_size` is in `[0, 255]`.
- payload chunks larger than 65535 bytes must be split.
- only the first configured Naive padding records in each direction are framed this way; subsequent data is raw.

The decoder must be a true streaming decoder:

- it must handle a framing header split across multiple reads,
- payload split across multiple reads,
- padding split across multiple reads,
- multiple padded records received in one read,
- the final padded record and following raw bytes arriving in the same read.

Do not assume that HTTP/2 DATA frame boundaries or socket read boundaries preserve Naive record boundaries.

### Chromium-specific Naive changes are not automatically applicable

Do not blindly port:

- Chromium-specific RST_STREAM camouflage.
- Chromium-specific preambles.
- Chromium-specific HTTP/2 parameter patches.
- Chromium-specific Fast Open behavior.
- Changes whose purpose is specifically to make modified Chromium look like ordinary Chrome.

NaiveFox's outer stack is Firefox.

Any Firefox-specific camouflage change must be justified by measurements comparing NaiveFox against an ordinary Firefox build from the same source revision.

## Runtime and packaging model

The prototype is intentionally allowed to be large.

NaiveFox is expected to be built as a Gecko-dependent executable, conceptually similar to:

```python
GeckoProgram("naivefox", linkage="dependent")
```

The runtime will therefore not initially be a single static binary.

Development output will likely use Firefox build artifacts such as:

```text
naivefox
libxul.so
NSS/NSPR libraries
mozglue and other required Gecko runtime files
```

A normal Firefox build may also produce the Firefox browser executable in the object directory. That browser executable is not part of the NaiveFox product requirement.

The current prototype keeps the Gecko-facing implementation inside `libxul`
so it can use internal Necko and PSM APIs, while the small `naivefox` program
owns Firefox's bootstrap lifetime and calls the exported NaiveFox entry point.
This follows the dependent executable model without exposing internal XPCOM
types across the executable boundary.

The staged package has a user-facing launcher at its root and keeps the native
binary and GRE dependencies below `runtime/`:

```text
naivefox-linux-x86_64/
|-- naivefox
|-- run-naivefox
`-- runtime/
    |-- naivefox
    |-- libxul.so
    `-- ...
```

`run-naivefox` remains a compatibility alias. Normal packaged use requires no
build-tree loader variables, profile argument, or credential environment
variables:

```bash
cd naivefox-linux-x86_64
./naivefox
./naivefox /absolute/path/to/config.json
```

Runtime diagnostics are also available independently of SOCKS mode:

```bash
export LD_LIBRARY_PATH="$PWD/obj-x86_64-pc-linux-gnu/dist/bin"
obj-x86_64-pc-linux-gnu/dist/bin/naivefox \
  --profile /path/to/nss-profile --runtime-smoke
obj-x86_64-pc-linux-gnu/dist/bin/naivefox \
  --profile /path/to/nss-profile --fetch https://example.com/
obj-x86_64-pc-linux-gnu/dist/bin/naivefox \
  --profile /path/to/nss-profile \
  --raw-tunnel-smoke https://proxy.example:443 target.example:80
```

The development binary's legacy test-only SOCKS mode uses the same profile and
environment-only credentials:

```bash
obj-x86_64-pc-linux-gnu/dist/bin/naivefox \
  --profile /path/to/nss-profile \
  --socks-listen 127.0.0.1:1080 \
  --proxy https://proxy.example:443 \
  --protocol h2
```

`--protocol h2` is the compatibility-preserving default. `--protocol h3`
requires a Necko/Neqo HTTP/3 proxy connection and never silently falls back to
H2. `--protocol auto` first makes the same strict H3 attempt and retries once
with H2 only when transport establishment fails before any CONNECT response or
tunnel transport is observed. Authentication, ACL, target, CONNECT 200, and
established-tunnel failures never trigger fallback. Each successful tunnel
logs only `Outer protocol: h2` or `Outer protocol: h3`; credentials and proxy
authorization are never logged.

`--max-connections N` is an optional finite-run test control. Omitting it
keeps the loopback SOCKS listener running. The value counts total accepted
SOCKS connections over the lifetime of the process; it is not a parallel or
production concurrency limit. Normal long-lived use should omit the option.

The raw-tunnel diagnostic creates an explicit HTTPS proxy through Necko,
requires the selected outer protocol, and obtains regular CONNECT as
asynchronous Gecko streams. Credentials come only from
`NAIVEFOX_PROXY_USER` and `NAIVEFOX_PROXY_PASS`. The reproducible local
authentication and bidirectional stream tests are:

```bash
netwerk/naivefox/test/integration/run-raw-connect-tests.sh
netwerk/naivefox/test/integration/run-h3-raw-connect-tests.sh
```

For this internal path, `setConnectOnly(false)` is followed by
`HTTPUpgrade("", listener)`. An empty protocol is restricted to a connect-only
channel and means raw stream takeover: it emits neither an Upgrade header nor
the synthetic CONNECT `ALPN` header used by protocol upgrades. The explicit
proxy's resolve flags must carry both HTTPS-proxy preference and always-tunnel
semantics; the similarly named `nsIProxyInfo` connection flags are not a
substitute.

Single-process networking is the explicit architecture of the current Linux
prototype, not an accidental fallback. The socket process remains disabled,
and both `Http2StreamTunnel` and `Http3StreamTunnel` operate successfully in
the parent process. Necko owns HTTP and connection pooling, Neqo owns
QUIC/HTTP3, and PSM/NSS owns certificate verification and TLS. See
`H3-DESIGN.md` for the exact source path, `KNOWN-ISSUES.md` for the boundary of
this choice, and `UPSTREAM.md` for the minimal focused Firefox changes.

Packaging/minimization comes only after the network prototype works.

## Development environment

The prototype targets a supported **Linux x86_64** build environment.

The exact host arrangement is intentionally not part of the project architecture. Development may happen on a native Linux machine, virtual machine, container-like environment, subsystem, CI runner, or another suitable Linux environment.

Requirements:

- the source tree must be on a filesystem suitable for large native Linux builds,
- the environment must satisfy current Mozilla Firefox Linux build requirements,
- the agent must use Firefox's normal bootstrap and toolchain,
- the agent must prove a clean baseline build before modifying source.

Mozilla's current Linux build documentation:

https://firefox-source-docs.mozilla.org/setup/linux_build.html

Use a **full Firefox build**, not Artifact Mode, because NaiveFox modifies/links C++ backend code.

Use Mozilla's normal toolchain and `mach`; do not introduce CMake as the project build system.

## Source references

Primary upstream references:

- Firefox source:
  https://github.com/mozilla-firefox/firefox
- Firefox Linux build documentation:
  https://firefox-source-docs.mozilla.org/setup/linux_build.html
- Firefox C++ policy:
  https://firefox-source-docs.mozilla.org/code-quality/coding-style/using_cxx_in_firefox_code.html
- NaiveProxy:
  https://github.com/klzgrad/naiveproxy
- Naive padding specification:
  https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification
- Naive Caddy forward proxy:
  https://github.com/klzgrad/forwardproxy/tree/naive

Relevant current Firefox source paths:

- `build/gecko_templates.mozbuild`
- `js/xpconnect/shell/moz.build`
- `netwerk/moz.build`
- `netwerk/protocol/http/nsIHttpChannelInternal.idl`
- `netwerk/protocol/http/HttpBaseChannel.cpp`
- `netwerk/protocol/http/nsHttpConnection.cpp`
- `netwerk/protocol/http/Http2Session.cpp`
- `netwerk/protocol/http/Http2StreamTunnel.cpp`
- `netwerk/protocol/http/Http3Session.cpp`
- `netwerk/protocol/http/Http3StreamTunnel.cpp`
- `netwerk/protocol/http/Http3TransportLayer.cpp`
- `netwerk/test/unit/test_proxyconnect.js`
- `netwerk/test/unit/test_proxyconnect_headers.js`

Source code changes over time. Always verify current `main`; never copy line numbers or old assumptions blindly.

## Definition of the complete H2/H3 prototype

As of 2026-08-13, every local-fixture, codec, robustness, capture, staging, and
supplied-real-Caddy milestone is implemented and passes in the supported Linux
x86-64 environment. Extended local throughput, passive-observer, and
10-minute real-deployment stability tests also pass.

The H2 baseline remains defined by the list below. The H3 stage additionally
requires one `naivefox` executable to pass strict H2, strict H3, and bounded
Auto tests; use UDP/QUIC with no TCP fallback in strict H3; reuse the same
SOCKS, padding codec, and bounded duplex pump; multiplex concurrent regular
CONNECT streams on a Necko-owned H3 session; pass half-close, slow producer,
slow consumer, large-transfer, and proxy-loss tests; and run outside the
object directory from the same staged package.

The prototype is complete when all of the following are demonstrated on Linux x86_64:

1. A clean upstream Firefox checkout can be bootstrapped and built.
2. `naivefox` builds as part of the Firefox tree.
3. It starts headlessly and initializes the required Gecko networking runtime.
4. It can perform a normal HTTPS request using Necko/NSS as a sanity test.
5. It can establish an HTTPS connection to the local Caddy fixture through scoped NSS trust and negotiate HTTP/2.
6. It can issue a raw HTTP/2 CONNECT without an artificial NaiveFox-specific ALPN/Upgrade marker.
7. It exposes the CONNECT tunnel as async bidirectional streams.
8. It serves a local SOCKS5 endpoint.
9. `curl --socks5-hostname ...` can fetch deterministic HTTP and HTTPS targets through the local Caddy fixture.
10. Proxy authentication works.
11. The Naive CONNECT `padding` header is sent.
12. The server `padding` response header is detected.
13. Naive payload padding is encoded/decoded compatibly.
14. End-to-end traffic works with payload padding enabled.
15. Large transfers and multiple concurrent SOCKS connections work without corruption or unbounded buffering.
16. Tests cover the padding codec and critical SOCKS/tunnel state transitions.
17. A packet-capture comparison documents how the outer TLS/H2 setup compares with ordinary Firefox from the same revision.
18. The complete local integration suite passes from one documented command.
19. The same core path is confirmed against the supplied real Caddy server.
20. All changes to existing Firefox files are documented in `UPSTREAM.md`.
21. The prototype runtime can be staged and run outside the build tree on a compatible Linux system.
22. `naivefox` reads a strict NaiveProxy-compatible `config.json` without
    requiring developer CLI flags or credential environment variables.
23. One process can serve SOCKS5 and HTTP CONNECT listeners simultaneously,
    with both frontends using the same `TunnelSession`, Necko pool, padding
    negotiation, and bounded duplex pump.
24. Config `https://` and `quic://` upstreams pass local, staged, and supplied
    real-Caddy tests as strict H2 and strict H3 respectively.

See `ROADMAP.md` for the required implementation order.

Run the complete local integration gate with:

```bash
./netwerk/naivefox/test/integration/run-local-suite.sh
./netwerk/naivefox/test/integration/run-h3-suite.sh
./netwerk/naivefox/test/integration/run-full-suite.sh
```

The capture phase requires the restricted `dumpcap` capabilities documented
in `CAPTURE.md`. The H3-specific decrypted and no-keylog comparison is in
`H3-CAPTURE.md` and is reproduced by
`test/integration/run-h3-capture-comparison.sh`. The commands build or reuse
the pinned fixture dependencies, run local functional and failure-path suites
sequentially, and delete sensitive run material after every successful phase.

Additional repeatable test entry points are:

```bash
./netwerk/naivefox/test/integration/run-throughput-benchmark.sh
./netwerk/naivefox/test/integration/run-h3-throughput-benchmark.sh
./netwerk/naivefox/test/integration/run-observer-comparison.sh
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
./netwerk/naivefox/test/integration/run-real-server-soak.sh
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
./netwerk/naivefox/test/integration/run-real-server-h3-soak.sh
```

The committed outcomes and limitations are recorded in
[`PERFORMANCE-REPORT.md`](PERFORMANCE-REPORT.md),
[`OBSERVER-TRAFFIC-REPORT.md`](OBSERVER-TRAFFIC-REPORT.md), and
[`TEST-REPORT.md`](TEST-REPORT.md).

After a successful build, create and verify the relocatable prototype runtime
with:

```bash
./netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64
./netwerk/naivefox/tools/verify-staged-runtime.sh naivefox-linux-x86_64
```

The package is created below the configured object directory and deliberately
contains no NSS profile, fixture credentials, logs, TLS key logs, or packet
captures. Config mode creates or reuses its writable state profile
automatically; only developer/test modes require an explicit `--profile`.
