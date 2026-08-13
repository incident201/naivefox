# NaiveFox upstream maintenance policy

NaiveFox is intentionally a thin downstream of Firefox.

The project must remain easy to synchronize with:

https://github.com/mozilla-firefox/firefox

This document records the branch policy and every modification made to an existing upstream Firefox file.

## Branch policy

Long-lived branches:

```text
main
    Clean Firefox upstream mirror.
    No NaiveFox development commits.

naivefox
    Firefox + NaiveFox project changes.
```

`main` should remain suitable for GitHub's normal **Sync fork** operation.

Local remotes should normally be:

```text
origin    https://github.com/incident201/naivefox.git
upstream  https://github.com/mozilla-firefox/firefox.git
```

## Updating from Mozilla

Preferred non-history-rewriting workflow:

```bash
git fetch upstream

git switch main
git merge --ff-only upstream/main
git push origin main

git switch naivefox
git merge main
git push origin naivefox
```

This creates explicit upstream-sync merges on the development branch and avoids force-pushing public history.

Feature branches may be rebased locally when appropriate.

Never merge `naivefox` back into `main`.

## Source placement rule

Almost all project code must live under:

```text
netwerk/naivefox/
```

This directory does not belong to upstream Firefox and therefore should rarely conflict during synchronization.

Do not move the project across unrelated Firefox directories for convenience.

## Existing upstream files

Editing an existing Firefox file is an exception.

Before doing so, the coding agent must establish:

1. no suitable existing API exists,
2. the change is actually required,
3. the patch can remain small,
4. normal Firefox behavior is unchanged,
5. the patch has a focused test,
6. the reason and exact files are recorded below.

Keep upstream hooks in separate commits when practical.

## Known likely integration points

These are **anticipated**, not pre-approved exact patches. Re-check the current source before editing.

### A. `netwerk/moz.build`

Likely purpose:

Include the new `netwerk/naivefox/` build directory.

Preferred character:

One isolated directory-registration change.

Expected conflict risk:

Very low.

### B. raw CONNECT callback without synthetic Upgrade/ALPN

Relevant current areas may include:

```text
netwerk/protocol/http/nsIHttpChannelInternal.idl
netwerk/protocol/http/HttpBaseChannel.cpp
netwerk/protocol/http/nsHttpConnection.cpp
```

Reason:

Existing CONNECT-only machinery must be examined carefully. Historically it requires an `HTTPUpgrade()` callback, and the upgrade protocol can be propagated into an `ALPN` header on the proxy CONNECT request.

NaiveFox must not emit a fake `ALPN: naivefox`, `ALPN: webrtc`, or similar wire marker.

Preferred solution:

First search for an existing raw CONNECT API that avoids this behavior.

If none exists, add the smallest clean internal hook that exposes the CONNECT tunnel without inventing a protocol token.

Do not decide the exact patch from this document alone.

### C. Naive `padding` request header in proxy CONNECT

Relevant current area:

```text
netwerk/protocol/http/nsHttpConnection.cpp
```

Reason:

Firefox's proxy CONNECT request is constructed separately from the ordinary origin request. Arbitrary normal request headers may not be copied into the CONNECT request.

Naive-compatible Caddy detects client padding capability from the presence of the `padding` CONNECT header.

Preferred solution:

Use an existing generic proxy-CONNECT-extra-header mechanism if one now exists.

If not, implement the smallest maintainable mechanism.

A project-specific one-line copy may produce fewer merge conflicts, while a generic API may be cleaner but touch more files. Choose based on current architecture and testability, and document the tradeoff.

Do not modify HTTP/2 framing/HPACK itself merely to add the header.

## Upstream behavior we should not patch speculatively

Do not modify these merely to imitate original Chromium NaiveProxy:

```text
Http2Session SETTINGS
RST_STREAM behavior
HPACK implementation
TLS cipher configuration
TLS extension order
Firefox connection pooling
Firefox preambles/background traffic
HTTP/2 priorities
socket parameters
```

If capture comparison later proves a NaiveFox-specific deviation caused by our integration, document and evaluate it separately.

The baseline goal is to preserve Firefox behavior, not transform Firefox into Chrome.

## Patch inventory

The agent must keep this section current.

### Upstream base

```text
Base repository: https://github.com/mozilla-firefox/firefox
Base branch: main
Base commit: 8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6
Last sync: project branch state inspected 2026-08-12; upstream/main was 7 commits ahead
```

The NaiveFox work began at project commit
`7e26713ed7d05127188d2579d3c51afbe554db22`. Its merge base with the fetched
Mozilla `main` was `8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6`.

## Baseline build

On 2026-08-12 the untouched checkout was bootstrapped for a full Firefox
Desktop build and built successfully.

```text
Source commit: 7e26713ed7d05127188d2579d3c51afbe554db22
Object directory: /home/zubastik/src/naivefox/obj-x86_64-pc-linux-gnu
Build type: full Firefox Desktop, non-artifact
Build time: 42 minutes 55 seconds
Build log: artifacts/baseline-build.log (local, ignored)
```

Mozilla bootstrap used its managed Clang/Rust toolchains. The Ubuntu packages
`watchman` and `gh` were added to the development environment; `gh` is not
authenticated and is not required for local builds or tests.

The minimal Ubuntu image did not contain Firefox's GTK/X11 runtime libraries.
The normal GTK 3, X11, font, audio, D-Bus, and GLib runtime packages were
installed before executing the dependent NaiveFox binary. Development runs
set `LD_LIBRARY_PATH` to the build's `dist/bin` directory; Phase 11 will replace
that build-tree convention with a staged runtime layout.

### Patch NF-UPSTREAM-001

Status: implemented

Files:

```text
netwerk/moz.build
```

Purpose:

Register `netwerk/naivefox/` in the Firefox build.

Expected size:

Minimal.

Tests:

- full/build-system build,
- `naivefox` target produced.

Commit: `NF01 add NaiveFox build target`

### Patch NF-UPSTREAM-002

Status: implemented

Files:

```text
netwerk/protocol/http/nsIHttpChannelInternal.idl
netwerk/protocol/http/HttpBaseChannel.cpp
netwerk/protocol/http/nsHttpChannel.cpp
netwerk/protocol/http/nsHttpConnection.cpp
netwerk/test/unit/test_proxyconnect_raw.js
netwerk/test/unit/xpcshell.toml
```

Purpose:

Expose a raw successful HTTP proxy CONNECT tunnel without requiring an artificial NaiveFox-specific Upgrade/ALPN wire marker.

Why project-only code was insufficient:

Firefox exposes the successful CONNECT streams through `HTTPUpgrade()`, but
the API rejected an empty protocol. A non-empty protocol becomes both normal
Upgrade headers and an `ALPN` proxy-CONNECT header. In addition, a first-use
HTTPS proxy negotiating H2 reset the connect-only transaction, then closed the
outer connection before that transaction could be dispatched onto its H2
tunnel stream.

Implementation:

- allow an empty `HTTPUpgrade()` protocol only after `setConnectOnly()`;
- retain the upgrade callback/sticky transaction behavior without emitting
  `Upgrade` or `Connection` for the empty value;
- allow H2 for this raw connect-only case and continue to disallow H3;
- require a callback before opening every connect-only channel;
- do not take the connect-only early-close path while a fresh outer H2 proxy
  connection is completing its transaction restart.

Normal non-empty upgrade behavior and ordinary browsing channels are
unchanged.

Tests:

- focused raw CONNECT test,
- existing proxy CONNECT tests,
- wire/decrypted-header verification that no synthetic marker is sent.

The local Caddy integration additionally proves NSS TLS, outer H2, CONNECT
200, Basic Auth failure modes, and bidirectional C++ stream use.

Commit: `1fec4f92754c NF04 expose raw HTTP CONNECT streams`

### Patch NF-UPSTREAM-003

Status: implemented

Files:

```text
netwerk/protocol/http/nsIHttpChannelInternal.idl
netwerk/protocol/http/HttpBaseChannel.h
netwerk/protocol/http/HttpBaseChannel.cpp
netwerk/protocol/http/nsHttpRequestHead.h
netwerk/protocol/http/nsHttpRequestHead.cpp
netwerk/protocol/http/PHttpChannelParams.h
netwerk/protocol/http/nsHttpConnection.cpp
netwerk/test/unit/test_proxyconnect_padding_header.js
netwerk/test/unit/xpcshell.toml
```

Purpose:

Provide a privileged, pre-open API for adding a validated header to the actual
proxy CONNECT request without adding it to the origin request.

Why project-only code was insufficient:

Firefox constructs a new request head for proxy CONNECT and selectively copies
headers into it. Setting a normal origin request header therefore cannot place
Naive's `padding` header on either the HTTP/1.1 or HTTP/2 CONNECT wire path.

Implementation and behavioral risk:

- store explicit CONNECT-only headers in a request-head sidecar that survives
  copy/move and socket-process serialization;
- copy the sidecar only in the common CONNECT construction path;
- reject authority, framing, hop-by-hop, proxy-authentication, and ALPN
  headers, as well as invalid tokens and values;
- leave normal requests and channels unchanged unless the new internal method
  is explicitly called.

Tests:

- validation rejects CR/LF injection and reserved headers;
- HTTP/1.1 and HTTP/2 proxies receive the exact `padding` value;
- CONNECT response `padding` is available through `nsIProxiedChannel`;
- existing raw and proxy CONNECT tests pass.

Commit: `da53c63336f5 NF06 add proxy CONNECT request headers`

### Patch NF-UPSTREAM-004

Status: implemented

Files:

```text
toolkit/library/libxul.symbols
```

Purpose:

Export the single C ABI entry point used by the small dependent `naivefox`
executable. The implementation remains inside `libxul`, where Firefox internal
Necko, PSM, preferences, event-loop, and shutdown APIs are available.

Why project-only code was insufficient:

Firefox intentionally hides all `libxul` symbols except its explicit export
list. Compiling the implementation directly into the executable would lose
`MOZILLA_INTERNAL_API` and cannot use the internal APIs required by this
project.

Behavioral risk:

One otherwise-unused symbol becomes visible. Firefox startup and browser
behavior are unchanged.

Tests:

- full binary build,
- `naivefox --runtime-smoke`,
- public HTTPS request through Necko/NSS,
- fixture trusted/untrusted/hostname certificate validation.

Commit: `NF02 initialize the headless Gecko runtime`

### Patch NF-UPSTREAM-005

Status: implemented

Files:

```text
netwerk/protocol/http/Http2Session.cpp
netwerk/protocol/http/Http2StreamTunnel.cpp
netwerk/protocol/http/Http2StreamTunnel.h
```

Purpose:

Make a raw HTTP/2 tunnel obey bounded-stream backpressure and byte-stream
half-close semantics during large and concurrent transfers.

Why project-only code was insufficient:

The tunnel callback could consume Firefox's internal slow-consumer buffer, but
`CallToWriteData()` always reported zero consumed bytes to `Http2Session`.
After roughly one receive window of data, no `WINDOW_UPDATE` was generated and
the tunnel deadlocked. In addition, the existing output-stream close path could
only cancel the whole H2 stream; it could not send an output `END_STREAM` while
leaving input open. Finally, a peer `RST_STREAM(NO_ERROR)` discarded bytes
already held in the slow-consumer buffer.

Implementation and behavioral risk:

- bound each input callback to the session's requested byte count and report
  the exact number consumed so normal H2 flow-control accounting advances;
- treat successful output-stream close as a tunnel output half-close and use
  the existing `mSendClosed` path to generate `END_STREAM`;
- for tunnels only, treat peer `RST_STREAM(NO_ERROR)` as graceful EOF after
  all already-buffered bytes have been delivered;
- leave ordinary HTTP transactions and non-successful tunnel resets unchanged.

This changes RST behavior only after a reproducible NaiveFox integration
failure: a 32 MiB response was first stalled at the receive-window boundary,
then truncated when Caddy closed the completed tunnel while bytes remained in
Firefox's bounded slow-consumer buffer. It is not a fingerprinting change.

Tests:

- deterministic 32 MiB download and upload with integrity checks,
- bounded-memory slow producer/consumer paths,
- application half-close and target/proxy disconnects,
- four simultaneous CONNECT streams on one H2 connection,
- focused raw/proxy CONNECT xpcshell regressions.

Commit: `a8ad15724cca NF09 harden H2 tunnel lifecycle`

### Patch NF-UPSTREAM-006

Status: implemented

Files:

```text
netwerk/base/nsIProxyInfo.idl
netwerk/protocol/http/ConnectionAttemptPool.cpp
netwerk/protocol/http/nsHttpConnectionInfo.h
netwerk/protocol/http/nsHttpTransaction.cpp
netwerk/test/unit/test_http3_proxy_strict.js
netwerk/test/unit/xpcshell.toml
```

Purpose:

Allow a privileged caller to require an HTTP/3 proxy without Necko opening or
switching to an HTTPS/H2 fallback route.

Why project-only code was insufficient:

An H3 proxy transaction creates a timed HTTPS backup connection by default,
and its generic restart path converts a `masque` proxy info into `https`.
Rejecting H2 only after the channel completes would still put fallback TCP
traffic on the wire and would not satisfy strict protocol selection.

Implementation and behavioral risk:

- add an opt-in proxy flag which is preserved by the existing proxy-info clone
  and IPC serialization;
- suppress the H3-proxy backup timer and the `masque` to `https` conversion
  only when that flag is present;
- explicitly disable Happy Eyeballs selection for a flagged transaction and
  reject that connection-attempt path defensively even if a caller supplied a
  preconfigured connection info;
- leave ordinary Firefox H3 fallback, origin H3, and unflagged proxy channels
  unchanged.

Tests:

- an unavailable UDP/H3 proxy with an available H2 proxy on the same port must
  fail instead of returning the H2 target response, with Happy Eyeballs enabled
  globally during the test;
- the existing H3 proxy fallback suite remains enabled for unflagged channels.

Commit: `a981e07b81ce NF-H3-03 require strict Necko H3 proxy selection`

### Patch NF-UPSTREAM-007

Status: implemented

Files:

```text
netwerk/protocol/http/Http3StreamTunnel.cpp
netwerk/test/http3server/src/main.rs
netwerk/test/unit/test_proxyconnect_h3_raw.js
netwerk/test/unit/xpcshell.toml
```

Purpose:

Make raw regular CONNECT over an HTTP/3 proxy reliably deliver its async input
and output streams, including through the main-thread-safe pipes used by a JS
upgrade listener.

Why project-only code was insufficient:

`InputStreamTunnel::AsyncWait()` and `OutputStreamTunnel::AsyncWait()` notified
the H3 session before storing the new callback. That notification can reenter
`Http3Session::SendData()` synchronously. The resulting callback-free
`ReadSegments()` iteration reported success without moving a byte and
immediately queued itself again, spinning the socket thread before the raw
tunnel consumer could run.

Implementation and behavioral risk:

- publish each async callback before notifying the H3 stream that input or
  output is wanted;
- preserve a callback consumed by a reentrant `OnSocketReady()` call instead
  of accidentally restoring it after notification;
- leave ordinary H3 transactions and callback-free waits unchanged.

The test-only H3 proxy response echoes a fixed `padding` marker when that
request header is present and rejects synthetic `ALPN`, `Upgrade`, or
`Connection` markers in the same request. It does not change production proxy
behavior.

Tests:

- focused empty-protocol raw H3 CONNECT obtains async streams, writes a known
  HTTP request, and verifies the deterministic tunneled response;
- request and response CONNECT `padding` metadata are checked;
- the outer channel is asserted to be HTTP/3 and no synthetic upgrade marker
  is accepted by the test proxy;
- the existing H3 proxy transfer tests through large-data coverage remain
  green before their pre-existing connection-refused timeout case.

Commit: `5d889d177561 NF-H3-04 expose raw HTTP/3 CONNECT streams`

### Patch NF-UPSTREAM-008

Status: implemented

Files:

```text
netwerk/protocol/http/Http3StreamTunnel.cpp
netwerk/protocol/http/Http3StreamTunnel.h
third_party/rust/neqo-http3/src/connection.rs
third_party/rust/neqo-http3/.cargo-checksum.json
netwerk/test/unit/test_proxyconnect_h3_raw.js
```

Purpose:

Give a raw regular HTTP/3 CONNECT tunnel byte-stream half-close semantics: a
successful close of its output stream sends QUIC FIN while its input stream
continues delivering the proxy response.

Why project-only code was insufficient:

The H3 tunnel output stream previously mapped every close to
`CancelFetch()`, which resets both directions. Reusing the normal Neqo
send-side close exposed a second issue: classic CONNECT and true extended
CONNECT share `Http3StreamType::ExtendedConnect`, and Neqo removed the receive
handler before checking whether the stream actually belonged to a
WebTransport or CONNECT-UDP session. The server's response remained visible
on the QUIC wire but could no longer produce `DataReadable`.

Implementation and behavioral risk:

- map only a successful **connect-only** tunnel output close to Neqo's existing
  `stream_close_send()` path; failed closes retain full-stream cancellation;
- retain the opposite stream handler when closing one side of classic
  CONNECT, while preserving coupled lifecycle for actual extended-connect
  sessions;
- update the vendored Cargo checksum for the changed Neqo source;
- leave ordinary HTTP requests, WebTransport, CONNECT-UDP, and full tunnel
  cancellation unchanged.

The `NS_HTTP_CONNECT_ONLY` scope is intentional. Ordinary Firefox HTTP
channels that happen to traverse an H3 proxy also use `Http3StreamTunnel`, but
retain their pre-existing coupled transport lifecycle.

Tests:

- focused raw H3 xpcshell test closes the request send-side before reading and
  still receives the deterministic response;
- the strict local Caddy H3-only runner verifies request FIN, delayed response,
  response FIN, marker integrity, and authentication failure over UDP/QUIC;
- full `gkrust`, `libxul`, and NaiveFox binary builds pass.

Commit: `43aa7e8a09ff NF-H3-05 preserve classic CONNECT input after H3 half-close`

Scope correction:
`f0da0115d59c NF-H3-15 scope tunnel lifecycle changes to raw CONNECT`

### Patch NF-UPSTREAM-009

Status: implemented

Files:

```text
netwerk/protocol/http/Http3Session.cpp
netwerk/protocol/http/Http3StreamTunnel.cpp
netwerk/protocol/http/Http3StreamTunnel.h
netwerk/test/unit/test_proxyconnect_h3_raw.js
```

Purpose:

Bound raw HTTP/3 tunnel buffering under a slow consumer and preserve the
receive direction when the CONNECT peer sends `STOP_SENDING` after completing
its response.

Why project-only code was insufficient:

The application pump already bounded its own buffers, but the H3 tunnel could
continue draining Neqo into an unbounded `SimpleBuffer`. During a 32 MiB slow
download Caddy then sent `STOP_SENDING(H3_REQUEST_CANCELLED)` after target
completion. The generic session path treated that send-direction signal as a
full request-stream cancellation and discarded response bytes which the slow
consumer had not yet read. The public tunnel input stream also returned an
error from `Available()` instead of its buffered byte count.

Implementation and behavioral risk:

- cap only a connect-only tunnel's slow-consumer buffer at 256 KiB and resume
  Neqo reads after the application drains it;
- for connect-only tunnels, retain a received FIN until all bytes already
  buffered by the tunnel have reached the consumer;
- treat `STOP_SENDING` on a connect-only regular CONNECT tunnel as closing
  only its sending direction, while leaving the receive direction available;
- report the current tunnel buffer size through `Available()`;
- leave ordinary H3 requests, WebTransport, CONNECT-UDP, and non-tunnel reset
  handling unchanged.

The final scope guard uses the transaction's `NS_HTTP_CONNECT_ONLY` cap. A
control build with these H3 tunnel changes fully reverted reproduced the
frozen snapshot's ordinary-channel concurrent H3-proxy timeout identically;
the scope guard nevertheless ensures NaiveFox lifecycle and buffering policy
cannot alter normal Firefox proxy channels.

The change is deliberately H3-specific and does not copy the H2 flow-control
implementation. Neqo remains responsible for QUIC stream and connection flow
control.

Tests:

- focused raw H3 xpcshell regression slowly drains a 512 KiB response in 4 KiB
  callbacks after output half-close and verifies every byte;
- deterministic 32 MiB slow download and upload integrity checks;
- bounded-RSS gate after a warm-up tunnel;
- local disconnect, response-after-half-close, target early close, timeout,
  ACL denial, proxy loss, and four concurrent H3 CONNECT streams on one QUIC
  connection;
- the same full robustness workload passes in H2 mode.

Commit: `17ca8b746802 NF-H3-09 bound H3 tunnel backpressure and receive lifecycle`

Scope correction:
`f0da0115d59c NF-H3-15 scope tunnel lifecycle changes to raw CONNECT`

## Project-only config and local-listener stage

Status: implemented; no new upstream Firefox modification

Existing Firefox files changed by this stage: none.

Project files:

```text
netwerk/naivefox/Config.cpp
netwerk/naivefox/Config.h
netwerk/naivefox/HttpConnectParser.cpp
netwerk/naivefox/HttpConnectParser.h
netwerk/naivefox/RuntimeLogging.cpp
netwerk/naivefox/RuntimeLogging.h
netwerk/naivefox/TunnelSession.cpp
netwerk/naivefox/TunnelSession.h
netwerk/naivefox/NaiveFox.cpp
netwerk/naivefox/NaiveFoxRunner.cpp
netwerk/naivefox/SocksServer.cpp
netwerk/naivefox/SocksServer.h
netwerk/naivefox/core/moz.build
netwerk/naivefox/test/gtest/TestConfig.cpp
netwerk/naivefox/test/gtest/TestHttpConnectParser.cpp
netwerk/naivefox/test/gtest/moz.build
netwerk/naivefox/test/integration/run-config-tests.sh
netwerk/naivefox/test/integration/run-h2-config-tests.sh
netwerk/naivefox/test/integration/run-h3-config-tests.sh
netwerk/naivefox/test/integration/run-config-runtime-behavior-tests.sh
netwerk/naivefox/test/integration/run-real-server-config-tests.sh
netwerk/naivefox/test/integration/run-full-suite.sh
netwerk/naivefox/tools/stage-runtime.sh
netwerk/naivefox/tools/verify-staged-runtime.sh
```

Purpose:

Add a strict NaiveProxy-compatible JSON subset, automatic persistent profile,
runtime logging policy, simultaneous local SOCKS5 and HTTP CONNECT-only
listeners, and packaged no-argument/positional config invocation.

Why no Firefox patch was needed:

Both local frontends terminate only their small local protocols. The extracted
project `TunnelSession` owns the already-tested `OpenNeckoTunnel` path,
strict H2/H3/Auto policy, CONNECT metadata, Naive padding negotiation,
transport lifecycle, and bounded `DuplexPump`. Multiple listeners initialize
one `GeckoRuntime` and naturally share Firefox's existing Necko connection
manager and pooling. No new CONNECT header, stream, IPC, QUIC, or TLS behavior
was required.

Behavioral risk:

The refactor moves existing SOCKS tunnel state into a reusable project class.
The complete pre-existing H2/H3 raw, padded, robustness, Auto, and capture
suites were rerun to cover lifecycle and backpressure. HTTP parsing is bounded
at 16 KiB and preserves post-header bytes as initial tunnel payload. Listener
binding is intentionally loopback-only.

Tests:

- warning-free `./mach build -j4 binaries`;
- 45/45 `Naive*` gtests;
- complete local H2/H3 suite, including prior robustness and capture gates;
- strict H2 and H3 config workloads with simultaneous SOCKS5 and HTTP CONNECT;
- disabled/console/file logging and automatic profile checks;
- copied staged package with adjacent no-argument and positional config;
- supplied real Caddy over both strict protocols with public CA validation.

Commits:

- `29275f3d3f03 NF-CONFIG-01 add config-driven local proxy frontends`
- `5a9cabd981ba NF-CONFIG-02 verify config mode locally and staged`

## Rules for future upstream changes

When adding another upstream patch, append:

```text
### Patch NF-UPSTREAM-XXX

Status:
Files:
Purpose:
Why project-only code was insufficient:
Behavioral risk:
Tests:
Commit:
Notes for future sync:
```

During every Mozilla synchronization:

1. merge/fast-forward upstream into clean `main`,
2. merge `main` into `naivefox`,
3. inspect conflicts specifically around this inventory,
4. rebuild,
5. rerun tests for each still-active upstream patch,
6. delete downstream patches that have become unnecessary because Firefox gained an upstream API.

The best downstream patch is one we can eventually remove.
