# NaiveFox agent instructions

This file contains project-specific instructions for AI coding agents working in `netwerk/naivefox/`.

The repository root also contains Mozilla's upstream `AGENTS.md`. **Read and obey both files.** The root file covers Firefox-wide tooling and workflow; this file adds NaiveFox-specific constraints.

Before changing code, read:

1. `netwerk/naivefox/README.md`
2. `netwerk/naivefox/ROADMAP.md`
3. `netwerk/naivefox/UPSTREAM.md`
4. repository-root `AGENTS.md`

Do not assume access to any prior conversation about this project. These documents are the source of project intent.

## Mission

Build the Linux HTTP/2 NaiveFox prototype described in `README.md`.

The core principle is:

> Reuse Firefox's real Necko + NSS networking stack. Do not recreate, imitate, or replace it.

The target path is:

```text
local SOCKS5
    -> NaiveFox
    -> Necko
    -> NSS TLS
    -> HTTP/2 CONNECT
    -> existing Naive-compatible Caddy
    -> target
```

HTTP/3/Neqo is explicitly out of scope until the H2 prototype is complete.

## Autonomy

You are expected to work autonomously inside the provided Linux build environment.

You should:

- inspect the current repository state,
- bootstrap missing Firefox build dependencies,
- establish a clean baseline build,
- research current Firefox internals,
- implement milestones incrementally,
- build and run tests,
- diagnose failures,
- add targeted tests,
- update project documentation when discoveries invalidate an assumption.

Do not stop merely because an internal Firefox API differs from this document. Investigate the current source and adapt while preserving the architectural constraints.

Ask the user only when information cannot reasonably be discovered or inferred. Real proxy endpoint credentials are needed only for the final M8.3 interoperability gate; their absence must not block implementation or local M3-M9 validation.

## First actions in a fresh environment

Run these before modifying source:

```bash
pwd
git status
git branch --show-current
git remote -v
git log -1 --oneline
```

Confirm:

- the checkout is on the `naivefox` development branch,
- `origin` is the project fork,
- an `upstream` remote points to `https://github.com/mozilla-firefox/firefox.git` or can be added,
- the source lives on a native Linux filesystem, not `/mnt/c` or `/mnt/d`.

If `upstream` is missing:

```bash
git remote add upstream https://github.com/mozilla-firefox/firefox.git
```

Do not modify or commit to the `main` branch.

### Bootstrap

Follow the current Firefox Linux build documentation and the repository's tooling.

Start with the checkout's own tooling:

```bash
./mach bootstrap
```

Select a **full Firefox Desktop build**, not Artifact Mode.

If bootstrap needs basic Ubuntu packages, install only what is needed. Mozilla currently documents a Debian/Ubuntu base similar to:

```bash
sudo apt update
sudo apt install -y curl python3 python3-venv git make
```

Do not manually replace Firefox's compiler/toolchain with a random system GCC setup.

### Baseline build

Before any source changes, prove that the checkout builds:

```bash
mkdir -p artifacts
./mach build > artifacts/baseline-build.log 2>&1
```

Follow root `AGENTS.md` guidance for long-running commands and logs.

Record:

- source commit SHA,
- selected build configuration,
- successful build result,
- object directory,
- compiler/toolchain reported by the build.

If baseline Firefox does not build, diagnose the environment before touching NaiveFox code.

## Build philosophy

Use Firefox's build system.

Do not introduce:

- CMake as the primary build system,
- Meson,
- Bazel,
- a separate vendored HTTP/2 stack,
- a separate vendored TLS stack.

The project is expected to use `moz.build` and `mach`.

A likely initial declaration is:

```python
GeckoProgram("naivefox", linkage="dependent")
```

but verify current Firefox build conventions before committing it.

For initial integration, expect to add the new directory to `netwerk/moz.build`.

After the initial full build, use the narrowest valid build command that still verifies the affected C++ code. The root Firefox `AGENTS.md` currently documents `./mach build binaries` for C/C++/Rust-only changes. Use `./mach build` whenever in doubt or after build-system changes.

## Search and source research

Firefox is enormous.

Follow the root Firefox `AGENTS.md`:

- use `searchfox-cli` for upstream code research when available,
- use identifier-aware search for C++,
- narrow local `rg` searches to relevant directories,
- do not run blind whole-tree grep searches.

For NaiveFox-specific local code, ordinary `rg` inside `netwerk/naivefox` is fine.

Primary Necko areas for this project:

```text
netwerk/protocol/http/
netwerk/base/
netwerk/test/unit/
```

Useful known files:

```text
netwerk/protocol/http/nsIHttpChannelInternal.idl
netwerk/protocol/http/HttpBaseChannel.cpp
netwerk/protocol/http/nsHttpConnection.cpp
netwerk/protocol/http/Http2Session.cpp
netwerk/protocol/http/Http2StreamBase.cpp
netwerk/protocol/http/Http2StreamTunnel.cpp
netwerk/test/unit/test_proxyconnect.js
netwerk/test/unit/test_proxyconnect_headers.js
```

Use those as starting points, not as frozen implementation assumptions.

## Hard architectural constraints

### 1. Use real Necko HTTP/2

Do not manually write HTTP/2 frames.

Do not directly implement:

- SETTINGS,
- HEADERS,
- HPACK,
- flow control,
- stream IDs,
- TLS ALPN,
- connection pooling.

These are precisely the behaviors this project wants Firefox to own.

### 2. Use real NSS/PSM TLS

Do not replace the outer TLS connection with:

- OpenSSL,
- BoringSSL,
- curl,
- rustls,
- another TLS client.

### 3. H2 only for this phase

Disable or disallow HTTP/3 on the proxy channel as necessary.

Do not begin Neqo work.

Do not add QUIC support.

### 4. Caddy protocol remains unchanged

Both the reproducible local fixture and the supplied real server must use an unmodified Naive-compatible Caddy build with `forwardproxy@naive`.

Fixture configuration may change only to isolate the test, bind it safely to loopback, and expose deterministic assertions. Do not solve a client problem by changing the server module or wire protocol.

### 5. Keep upstream modifications tiny

Default location for project code:

```text
netwerk/naivefox/
```

Before editing an existing Firefox file:

1. search for an existing API,
2. inspect relevant tests,
3. determine whether the requirement can be implemented entirely in project code,
4. if not, design the smallest generic or narrowly scoped hook,
5. document the change in `UPSTREAM.md`,
6. add a test that proves why the hook is needed.

Do not refactor unrelated Necko code.

Do not reformat unrelated Firefox files.

Do not modify browser UI.

### 6. Do not leak a NaiveFox-specific wire marker

A prototype that works only by emitting an obviously artificial header such as:

```text
ALPN: naivefox
ALPN: webrtc
Upgrade: naivefox
```

is not acceptable.

Current Firefox `setConnectOnly()`/`HTTPUpgrade()` plumbing must be inspected carefully because the upgrade protocol may be reflected into the proxy CONNECT request.

Use the existing raw-connect machinery where possible, but if obtaining the stream callback requires a synthetic protocol marker, implement a small clean internal hook instead.

### 7. Preserve remote destination DNS

For SOCKS domain requests, do not resolve the destination hostname locally.

The proxy should receive the hostname in the CONNECT authority.

Resolving the proxy server itself locally through normal Necko behavior is expected.

## C++ and Firefox style

New C++ code should follow current Mozilla style and current required C++ standard.

Use Mozilla-provided formatting:

```bash
./mach format
```

New Mozilla source files should use the standard MPL 2.0 source header used by nearby Firefox files.

Follow root `AGENTS.md` comment guidance: comments should explain non-obvious behavior, not narrate straightforward code.

Prefer Mozilla types and ownership conventions where they make integration safer:

- `RefPtr`
- `nsCOMPtr`
- `nsCString`
- `nsresult`
- `UniquePtr`
- existing async stream interfaces

Do not mechanically replace standard C++ types when a standard type is clearer and accepted by surrounding code.

Avoid raw ownership unless dictated by an existing API.

## Eventing, I/O, and backpressure

The client must not use blocking I/O on Gecko's main thread.

Prefer event-driven integration with Firefox/XPCOM networking APIs.

The `DuplexPump` must:

- tolerate partial reads and writes,
- handle `WOULD_BLOCK`,
- register async callbacks correctly,
- bound memory usage,
- propagate EOF and errors,
- avoid busy loops,
- avoid recursive callback explosions,
- avoid a thread-per-byte-stream design unless there is a compelling reason.

Do not assume one local socket write equals one H2 DATA frame.

Do not assume one H2 read equals one Naive padding record.

## SOCKS5 implementation rules

Initial scope:

- SOCKS version 5 only.
- No-auth local method.
- CONNECT only.
- IPv4, IPv6, and domain targets.
- loopback bind by default.

Reject unsupported methods/commands correctly.

Do not resolve SOCKS domain targets locally.

Write protocol parsing as explicit bounded state machines. Validate lengths before consuming data.

Add tests for fragmented SOCKS handshakes and requests.

## Naive padding implementation rules

Treat padding as a wire-compatibility protocol, not approximate obfuscation.

Reference current upstream NaiveProxy before implementation:

https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification

Also inspect current NaiveProxy source implementation if the README leaves ambiguity.

### Header padding

The actual proxy CONNECT must carry the Naive `padding` header.

Do not merely set a normal origin request header and assume Firefox copies it into CONNECT. Verify on the wire or in a dedicated proxy test.

The CONNECT response must be checked for the server `padding` header using existing Firefox CONNECT response APIs where possible.

Only enable payload padding after compatibility is established.

### Payload padding

Implement encoder and decoder as independent state machines.

Required tests include:

- empty/very small payload,
- padding size 0,
- padding size 255,
- original payload length 65535,
- split payload larger than 65535,
- every framing field split at every possible input boundary,
- multiple records coalesced in one input buffer,
- transition from padded records to raw mode in the same input buffer,
- malformed/truncated input,
- connection close mid-record.

Random padding generation must be appropriate for the upstream protocol. Do not use deterministic production padding. Tests may inject a deterministic RNG.

Do not add Chromium-specific RST_STREAM camouflage in this phase.

## Proxy authentication

Use normal Firefox proxy authentication behavior if practical.

Investigate current:

- `nsIProxyInfo`,
- proxy username/password handling,
- `Proxy-Authorization`,
- CONNECT authentication retry behavior.

Acceptance criteria:

- a valid user/password reaches the local Caddy fixture successfully,
- invalid and missing credentials fail cleanly,
- the same behavior is confirmed against the supplied real Caddy server when credentials are available,
- credentials are never written to logs.

Do not hardcode credentials.

## Headless Gecko runtime

Do not launch a browser UI just to access Necko.

The project should have its own executable and initialize enough Gecko/XPCOM runtime for networking.

Use current in-tree executable startup patterns as references, especially small Gecko-dependent programs such as `xpcshell`.

Known useful references include:

```text
js/xpconnect/shell/moz.build
js/xpconnect/shell/xpcshell.cpp
toolkit/xre/
```

For the first prototype, it is acceptable to disable the separate Firefox socket process if doing so materially simplifies correct in-process networking integration. If used, document the mechanism and rationale. Do not assume it is a permanent product requirement.

## Testing rules

Every milestone in `ROADMAP.md` has explicit acceptance criteria. Do not advance a milestone because the code merely compiles.

At minimum:

- build after each structural change,
- add unit/component tests for protocol state machines,
- run targeted Firefox networking tests relevant to changed upstream code,
- run `./mach test --auto` when appropriate,
- run end-to-end tests against the reproducible local Caddy fixture,
- confirm final interoperability against the supplied real Caddy server,
- test large transfers,
- test concurrent connections.

When changing existing Necko CONNECT code, run existing proxy CONNECT tests, including the current equivalents of:

```text
netwerk/test/unit/test_proxyconnect.js
netwerk/test/unit/test_proxyconnect_headers.js
```

Discover the exact current test invocation with `./mach test --help` / repository tooling rather than guessing.

## Reproducible local Caddy integration fixture

The project must provide a self-contained local integration fixture inside the provided Linux build environment. Missing remote proxy credentials are not a blocker for M3-M9: implement and validate against the local fixture first. The supplied real server is a second interoperability gate, not the everyday development dependency.

Commit the fixture source under a structure similar to:

```text
netwerk/naivefox/test/integration/
├── README.md
├── Caddyfile.template
├── setup-fixture.sh
├── start-fixture.sh
├── stop-fixture.sh
├── run-e2e.sh
└── target_server.py
```

The exact filenames may follow current Mozilla test conventions, but setup, execution, and cleanup must be automated. Do not require a manually configured system service.

### Generated state and isolation

Keep generated state outside the source tree, preferably under the Firefox object directory:

```text
<objdir>/naivefox-fixture/
├── bin/
├── caddy-data/
├── caddy-config/
├── nss-profile/
├── nss-profile-untrusted/
├── run/
└── logs/
```

The fixture must:

- use unprivileged loopback ports selected or checked at runtime,
- run Caddy and the target as ordinary child processes,
- keep PID files and install cleanup traps,
- stop only processes it started,
- leave an already installed/system Caddy untouched,
- never require `sudo` or a global firewall change,
- never place generated binaries, CA keys, credentials, logs, or packet captures in git,
- use restrictive permissions for generated secrets,
- sanitize or avoid logs that could contain proxy authorization or tunneled payload.

Repeated setup and cleanup must be idempotent. A failed test must still tear down child processes. The runner should print the selected non-secret ports and paths, but never generated credentials.

### Real Naive-compatible Caddy

Build a dedicated fixture binary with the real module:

```bash
xcaddy build \
  --with github.com/caddyserver/forwardproxy=github.com/klzgrad/forwardproxy@naive
```

Verify the result before running tests:

```bash
./caddy list-modules | rg '^http\.handlers\.forward_proxy$'
./caddy validate --config Caddyfile
```

Record or pin the resolved Caddy, xcaddy, Go, and `forwardproxy@naive` revisions so a later agent can reproduce the fixture. Cache the binary in generated state, never in the repository.

The Caddy configuration must:

- bind the proxy only to loopback on a high port,
- use an HTTPS site address that includes the wildcard site label required by `forward_proxy`, while an explicit Caddy `bind` still restricts the listener to `127.0.0.1` and optionally `::1`,
- set Caddy's `skip_install_trust` global option so startup never attempts a system trust-store change,
- use `tls internal` for a certificate valid for the configured proxy hostname, normally `localhost`,
- enable Basic Auth with per-run credentials supplied outside the committed Caddyfile,
- omit probe resistance in the primary deterministic auth fixture so missing/invalid credentials have an unambiguous result; test probe resistance against the supplied server or an optional second fixture mode,
- allow CONNECT only to the fixture target host/ports and deny everything else,
- never become a general-purpose or externally reachable open proxy,
- serve an ordinary non-proxy response on the front-end for a health check.

The client under test must still require HTTP/2 and prove that the outer NSS connection negotiated `h2`; merely reaching Caddy over HTTP/1.1 does not pass.

### Local CA and NSS trust

Use Caddy's isolated internal PKI or an equivalently scripted local CA. Point Caddy's XDG data/config directories at the generated fixture state so the CA root and private key never mix with the user's normal Caddy state.

Trust the generated root only in a dedicated NSS profile used by NaiveFox tests. Use the NSS `certutil` from the platform package, conceptually:

```bash
certutil -N --empty-password -d "sql:$NSS_PROFILE"
certutil -A -d "sql:$NSS_PROFILE" \
  -n "NaiveFox local fixture CA" -t "C,," -i "$CADDY_ROOT_CA"
certutil -L -d "sql:$NSS_PROFILE"
```

Adapt command details to the current NSS tooling when necessary, and make NaiveFox explicitly use that profile.

Do not:

- call `caddy trust`,
- install the root in the operating-system trust store,
- modify the user's normal Firefox profile,
- disable certificate verification,
- use `curl -k`, an NSS bad-certificate override, or an "accept all certificates" callback as the passing path.

Maintain a second fresh NSS profile without the root CA. The same proxy connection must fail there with an untrusted-issuer error. This negative test proves the passing result comes from scoped trust rather than disabled validation. Use a proxy hostname matching the certificate SAN and SNI; do not hide hostname errors with an IP address.

### Deterministic target

Run a small loopback-only target service controlled by the fixture. It should provide deterministic endpoints for:

- a small known response,
- a multi-megabyte body generated from a stable pattern,
- an upload that returns the received byte count and hash,
- a delayed response for backpressure tests,
- an intentional early close for lifecycle tests.

If an HTTPS target is required, terminate its TLS with a separate local Caddy site or another scripted server certificate from the same test CA. The application using SOCKS must validate the target certificate normally; outer proxy TLS trust and inner target TLS trust are separate assertions.

Use a hostname target in at least one SOCKS test and verify that the CONNECT authority received by the proxy retains that hostname. Unit/component coverage must additionally prove NaiveFox did not resolve the SOCKS domain before creating CONNECT.

### Required local end-to-end sequence

`run-e2e.sh` or its equivalent must perform, in order:

1. build or locate the pinned fixture Caddy and verify the module,
2. create isolated state, per-run credentials, CA, trusted NSS profile, and untrusted NSS profile,
3. start the target and Caddy and wait for explicit readiness with a timeout,
4. prove the untrusted NSS profile rejects the proxy certificate,
5. use a control request with an explicit CA file, never `-k`, to prove Caddy auth/ACL wiring independently of NaiveFox,
6. prove the trusted NSS profile connects through Necko/NSS and negotiates `h2`,
7. test valid, invalid, and missing proxy credentials without exposing them in output,
8. run the hard-coded raw CONNECT bidirectional smoke test,
9. run SOCKS5 HTTP and HTTPS target requests with `curl --socks5-hostname`,
10. assert CONNECT header padding negotiation and then padded payload operation,
11. compare hashes for the deterministic large download and upload,
12. run repeated and concurrent connections,
13. exercise target close, proxy close, and client close paths,
14. stop all fixture processes and preserve only sanitized diagnostics on failure.

Where a milestone has not implemented a later feature yet, the runner may select the applicable subset, but the final local suite must execute the complete sequence with one documented command.

A local pass is required before using the real server. Final real-server validation must use credentials supplied outside git and must not depend on local CA overrides.

## Packet capture / fingerprint validation

The project's goal is to use real Firefox behavior, so "it connects" is not enough.

After the end-to-end path works, capture and compare:

```text
ordinary Firefox from the same source revision
vs.
NaiveFox from the same source revision
```

At minimum inspect:

- TLS ClientHello structure,
- ALPN,
- cipher/extension behavior visible in the handshake,
- HTTP/2 SETTINGS,
- initial WINDOW_UPDATE behavior,
- connection reuse behavior,
- ordering of early H2 frames,
- unexpected custom headers or protocol markers.

When possible, connect ordinary Firefox and NaiveFox to the same front-end host to make the outer comparison meaningful.

NaiveFox is not required to produce identical application request traffic to a browsing session. Differences inherent to `CONNECT` are expected. The purpose is to detect accidental deviations caused by our integration.

Document findings rather than adding camouflage patches speculatively.

## Logging

Logs should be useful for engineering while avoiding secrets.

Useful fields:

- connection id,
- SOCKS target host/port,
- proxy host/port,
- tunnel state,
- HTTP version negotiated,
- CONNECT status,
- padding negotiated yes/no,
- byte counters,
- close/error reason.

Never log:

- proxy passwords,
- complete `Proxy-Authorization`,
- TLS secrets,
- arbitrary tunneled application payload.

Verbose payload dumps must not exist in normal builds.

## Error handling

Fail explicitly and locally.

Examples:

- no HTTP/2 negotiated -> clear connection failure in H2-only mode,
- CONNECT status != 200 -> return appropriate SOCKS failure,
- proxy auth failure -> clear failure,
- malformed Naive record -> close tunnel with diagnostic,
- upstream EOF -> propagate close,
- local EOF -> stop corresponding direction and clean up.

Do not silently fall back to a different networking stack.

## Git and commit discipline

Work on `naivefox`, not `main`.

Keep commits milestone-oriented and reviewable.

Preferred history shape:

```text
NF01 docs / build integration
NF02 headless Gecko runtime
NF03 Necko HTTPS sanity request
NF04 raw H2 CONNECT plumbing
NF05 SOCKS5 server
NF06 CONNECT padding negotiation
NF07 Naive payload codec
NF08 end-to-end padded proxy
NF09 robustness / capture validation
NF10 packaging prototype
```

If an existing Firefox file must be changed, prefer a dedicated commit for that upstream hook instead of mixing it into large NaiveFox feature commits.

Do not force-push `main`.

Do not mass-rebase or rewrite public project history unless the user asks.

## Updating documentation

Update these files as part of engineering work:

- `README.md` when architecture or supported behavior changes.
- `ROADMAP.md` when a milestone is completed or materially redesigned.
- `UPSTREAM.md` whenever an existing Firefox file is modified.

If current Firefox source disproves a statement in these docs, fix the documentation in the same change.

## Stop conditions

Do not declare the prototype complete until the definition in `README.md` and final roadmap acceptance criteria are met.

Do not start HTTP/3 merely because H2 is working.

Do not optimize binary size before correctness and interoperability are demonstrated.
