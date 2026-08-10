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

You are expected to work autonomously inside the provided Linux development environment.

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

Ask the user only when information cannot reasonably be discovered or inferred, such as real proxy endpoint credentials that have not yet been supplied.

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
- the source lives on a filesystem suitable for a large native Linux Firefox build.

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

If baseline Firefox does not build, diagnose the Linux build environment before touching NaiveFox code.

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

### 4. Caddy server remains unchanged

The supplied test server is the compatibility target.

Do not solve a client problem by changing the server protocol.

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

- a valid user/password reaches the supplied Caddy server successfully,
- invalid credentials fail cleanly,
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
- perform a real end-to-end SOCKS -> Caddy test,
- test large transfers,
- test concurrent connections.

When changing existing Necko CONNECT code, run existing proxy CONNECT tests, including the current equivalents of:

```text
netwerk/test/unit/test_proxyconnect.js
netwerk/test/unit/test_proxyconnect_headers.js
```

Discover the exact current test invocation with `./mach test --help` / repository tooling rather than guessing.

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
