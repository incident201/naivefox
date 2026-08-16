# NaiveFox upstream maintenance policy

NaiveFox is a disciplined downstream of Firefox. The repository deliberately
keeps a full reference implementation and a full-source minimization branch
between Mozilla's source mirror and the generated compact product tree. This
separates Firefox refresh regressions, minimization regressions, and source
export defects.

Upstream repository:

https://github.com/mozilla-firefox/firefox

This document is the authority for long-lived branch direction, validated
base tracking, refresh gates, minimization policy, and every downstream change
to an existing Firefox file.

## Long-lived branch model

```text
mozilla-firefox/firefox:main
             |
             v
main         clean Firefox upstream mirror
             |
             v
naivefox     full Firefox + NaiveFox reference implementation
             |
             v
minimal      full Firefox + minimized build/runtime rules
             |
             | deterministic allowlist export
             v
minimal-source  compact standalone generated product tree
```

These four branches have different responsibilities and are not peers.

### `main`: upstream mirror only

`main` contains only commits reachable from `mozilla-firefox/firefox:main`.
It must never contain NaiveFox code, minimization changes, generated product
artifacts, or merge commits from another project branch. Update it only by
fast-forwarding from the explicit `upstream` remote:

```bash
git fetch upstream
git switch main
git merge --ff-only upstream/main
git push origin main
```

Never merge `naivefox` or `minimal` into `main`. Do not rely on GitHub
**Sync fork** after changing the default branch; the explicit command above is
the only supported mirror update workflow.

### `naivefox`: full reference and integration branch

`naivefox` retains the entire Firefox source tree plus the complete NaiveFox
reference implementation: H2, H3, Auto, config, SOCKS5 and HTTP CONNECT
listeners, Naive padding, every downstream Necko/Neqo hook, all Firefox-focused
regressions, integration fixtures, capture tooling, and staging scripts.

Do not minimize this branch. It is the mandatory first integration layer for
every new Firefox base and the control that distinguishes an upstream refresh
problem from a product-minimization problem.

### `minimal`: full-source minimization branch

`minimal` is the working branch for build/runtime/link dependency reduction.
It retains the complete upstream Firefox source tree so Mozilla changes can be
integrated with ordinary Git ancestry and conflict resolution. It is created
from the tagged, validated full-reference point recorded below. All
minimization work happens on `minimal` or on `feature/min-*` branches based on
it. Large upstream source directories are not deleted merely to reduce checkout
size.

`minimal` never receives Firefox commits directly from `main`. It receives a
new Firefox base only after that base has passed the full `naivefox` refresh
gate and then passed a second, minimization-specific refresh gate.

### `minimal-source`: generated standalone product branch

`minimal-source` contains only the explicit source and build dependency closure
needed to build NaiveFox independently. It is generated from an already
validated `minimal` checkout by `tools/export-minimal-source.sh`; it is never an
upstream integration branch or a source of hand-edited project changes.

Its history is independent of Firefox. The first published snapshot is an
orphan commit and later snapshots form a compact linear history whose parent is
only the previous generated snapshot. Every snapshot records the exact Firefox,
NaiveFox, and minimal commits plus the export-manifest version in
`UPSTREAM-BASE`.

Never merge `main`, `naivefox`, or `minimal` into `minimal-source`. Never merge
generated changes back from `minimal-source`; fix the source of truth in
`minimal` or an upper layer, validate it, and regenerate.

## Remotes, default branch, and merge direction

Local remotes are:

```text
origin    https://github.com/incident201/naivefox.git
upstream  https://github.com/mozilla-firefox/firefox.git
```

Allowed long-lived directions:

```text
upstream/main -> main
main          -> refresh/firefox-YYYYMMDD -> naivefox
naivefox      -> refresh/minimal-YYYYMMDD -> minimal
minimal       -> validated export snapshot -> minimal-source
```

Forbidden directions:

```text
naivefox -> main
minimal  -> main
minimal  -> naivefox
main     -> minimal directly
main     -> minimal-source
naivefox -> minimal-source directly
minimal-source -> minimal
minimal-source -> naivefox
```

Feature branch bases:

```text
feature/network-*  from naivefox, for shared/reference functionality
feature/min-*      from minimal, for minimization-only changes
```

The project-facing GitHub default branch is `naivefox` until the standalone
export has passed its clean-build and acceptance gates. Once `minimal-source`
is stable, change the default branch to `minimal-source`. `main` is a service
mirror, while `naivefox` and `minimal` are developer branches; none should
represent the compact product tree to ordinary users.

Protect all four long-lived branches against force-push and deletion where
repository settings permit it. Require review or the relevant validation gate
for refresh merges; branch protection must not make `main` accept non-upstream
commits.

## Validated base tracking

The following values are immutable inputs to the first minimization milestone,
not aliases for a moving `main`:

```text
Validated Firefox base commit: 8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6
Validated NaiveFox commit: 2a539d796d1a1d134ec64739c69b61f443132a3c
Validated Minimal base commit: 2a539d796d1a1d134ec64739c69b61f443132a3c
Validated Minimal Source commit: NOT_CREATED
Pre-minimization baseline tag: pre-minimization-v0.3
```

The `minimal` branch was initialized from that tagged NaiveFox line and then
fast-forwarded through the validated config/profile corrections above; it has
no minimization-only commit at this baseline. Every release or significant
milestone must record concrete Firefox, NaiveFox, minimal, and generated-source
SHAs. Phrases such as "current Firefox main" are not valid release provenance.

## Two-gate Firefox refresh workflow

There is no supported `main -> minimal` workflow. Every Firefox refresh must
pass the following gates in order.

### Gate 1: Firefox -> `naivefox`

First fast-forward `main` as described above. Then create a dated temporary
branch from the last validated `naivefox`:

```bash
git switch naivefox
git switch -c refresh/firefox-YYYYMMDD
git merge main
```

Resolve conflicts on the refresh branch. Before merging it into `naivefox`:

1. inspect every downstream file in the patch inventory below, even if Git did
   not report a textual conflict;
2. build NaiveFox with the refreshed full Firefox tree;
3. run all project gtests;
4. run the focused Firefox CONNECT/H2/H3/Necko/Neqo regression set associated
   with the active patches;
5. run the complete H2/H3/Auto/config integration suite;
6. stage and verify the runtime outside the object directory;
7. rerun capture sanity/comparison when TLS, H2, H3, Neqo, NSS, PSM, or relevant
   network preferences changed;
8. run the bounded supplied-real-Caddy gate when networking behavior changed
   materially.

Only after every applicable check passes may the refresh branch be merged into
`naivefox`. Update the three concrete validated SHA fields in this document as
part of that milestone. If Firefox now exposes an equivalent supported API or
fix, remove the downstream patch instead of carrying it forward by inertia.

### Gate 2: `naivefox` -> `minimal`

Only after Gate 1 is complete may a second dated refresh branch be created from
the last validated `minimal`:

```bash
git switch minimal
git switch -c refresh/minimal-YYYYMMDD
git merge naivefox
```

Before merging it into `minimal`, verify the minimal build/runtime, package
manifest, H2, H3, Auto, SOCKS5, HTTP CONNECT, config mode, padding,
integrity/concurrency, size regression, and staged runtime outside the build
tree. Add focused checks for any dependency or packaging area touched while
resolving conflicts.

If Gate 1 passes but Gate 2 fails, classify the failure as a minimization
integration defect until evidence proves otherwise. Do not weaken `naivefox`
or blame the Firefox refresh merely to make the minimized branch pass.

### Gate 3: validated `minimal` -> `minimal-source`

Only after Gate 2 passes may the allowlist export be regenerated. The export
gate must:

1. start from a clean, validated `minimal` checkout;
2. create an empty export directory and copy only manifest entries;
3. validate licenses, traceability, forbidden paths, stale manifest entries,
   and absence of credentials, profiles, logs, captures, `.git`, and objdirs;
4. copy the export to a clean location with no access to the full Firefox
   source or original object directory;
5. bootstrap, configure, and build NaiveFox from that export alone;
6. run the required H2/H3/Auto/config/SOCKS/HTTP CONNECT and staged-runtime
   acceptance gates;
7. publish a new compact snapshot only after every check passes.

If `minimal` passes but the export fails, the defect belongs to the source
manifest/export tooling. It must not be worked around by editing
`minimal-source` manually.

## Refresh cadence

Do not continuously chase Firefox `main` in product branches. The clean mirror
may be synchronized frequently without rebuilding NaiveFox, but propagation to
`naivefox` and `minimal` is an explicit, scheduled refresh milestone. Typical
triggers are:

- security fixes in NSS, PSM, Necko, or Neqo;
- an upstream networking fix required by NaiveFox;
- a meaningful Firefox TLS/H2/H3 wire-behavior change;
- preparation of a NaiveFox release;
- a planned periodic base update.

Between refresh milestones, releases may intentionally remain on the concrete
validated Firefox SHA recorded above.

## Minimization policy relative to upstream

> The goal of minimization is to reduce the build/runtime dependency closure,
> not to reduce the Git checkout.

Prefer leaving upstream source in the repository while changing NaiveFox build
configuration so unused code and resources are not built or staged:

```text
source remains in repository
        |
        v
NaiveFox build configuration stops building it
        |
        v
code/resources do not enter the runtime package
```

Do not delete large upstream directories such as `dom/`, `gfx/`, or media
trees merely because the minimized client does not use them. Physical source
deletion is permitted only after measurements prove a material benefit that
build-time exclusion cannot provide and a separate review accepts the cost to
future Mozilla merges. Deletion must never be the first minimization tool.

The compact checkout is produced separately and only after closure is known:

```text
runtime allowlist -> build graph -> link closure -> source manifest -> export
```

The source manifest is allowlist-based: the exporter starts from an empty
directory and copies explicit inputs. A blacklist workflow that copies Firefox
and deletes apparently unused directories is forbidden.

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
Upstream repository: https://github.com/mozilla-firefox/firefox
Upstream branch: main
Validated Firefox base commit: 8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6
Validated NaiveFox commit: 92d25f965b8cd98dc9be88c4892a00ea2c8030b7
Validated Minimal base commit: 92d25f965b8cd98dc9be88c4892a00ea2c8030b7
Pre-minimization baseline tag: pre-minimization-v0.3
```

The NaiveFox work began at project commit
`7e26713ed7d05127188d2579d3c51afbe554db22`. Its merge base with the fetched
Mozilla `main` was `8d4f297e7481f71d5b3fad7fb84aa8e2f600b4c6`.
The remote `main` mirror and the first validated NaiveFox base share that exact
Firefox commit. No upstream fetch or base update was performed while adopting
the three-branch policy.

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

Why project-only code was insufficient:

Firefox does not discover new top-level networking subdirectories
automatically; the upstream `netwerk/moz.build` traversal must register the
project directory.

Behavioral risk:

One isolated build-directory registration. Normal Firefox source selection and
runtime behavior are unchanged.

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

Behavioral risk:

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

Implementation:

- store explicit CONNECT-only headers in a request-head sidecar that survives
  copy/move and socket-process serialization;
- copy the sidecar only in the common CONNECT construction path;
- reject authority, framing, hop-by-hop, proxy-authentication, and ALPN
  headers, as well as invalid tokens and values;
- leave normal requests and channels unchanged unless the new internal method
  is explicitly called.

Behavioral risk:

The sidecar is serialized with the request head, but is copied only into the
separate proxy CONNECT head. Validation excludes authority, framing,
hop-by-hop, authentication, and ALPN fields; callers that do not invoke the new
internal method are unchanged.

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

Implementation:

- bound each input callback to the session's requested byte count and report
  the exact number consumed so normal H2 flow-control accounting advances;
- treat successful output-stream close as a tunnel output half-close and use
  the existing `mSendClosed` path to generate `END_STREAM`;
- for tunnels only, treat peer `RST_STREAM(NO_ERROR)` as graceful EOF after
  all already-buffered bytes have been delivered;
- leave ordinary HTTP transactions and non-successful tunnel resets unchanged.

Behavioral risk:

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

Implementation:

- add an opt-in proxy flag which is preserved by the existing proxy-info clone
  and IPC serialization;
- suppress the H3-proxy backup timer and the `masque` to `https` conversion
  only when that flag is present;
- explicitly disable Happy Eyeballs selection for a flagged transaction and
  reject that connection-attempt path defensively even if a caller supplied a
  preconfigured connection info;
- leave ordinary Firefox H3 fallback, origin H3, and unflagged proxy channels
  unchanged.

Behavioral risk:

Only transactions carrying the new privileged strict-proxy flag lose Firefox's
normal H3 backup/restart/Happy-Eyeballs routes. Unflagged Firefox traffic keeps
its existing fallback behavior.

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

Implementation:

- publish each async callback before notifying the H3 stream that input or
  output is wanted;
- preserve a callback consumed by a reentrant `OnSocketReady()` call instead
  of accidentally restoring it after notification;
- leave ordinary H3 transactions and callback-free waits unchanged.

Behavioral risk:

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

Implementation:

- map only a successful **connect-only** tunnel output close to Neqo's existing
  `stream_close_send()` path; failed closes retain full-stream cancellation;
- retain the opposite stream handler when closing one side of classic
  CONNECT, while preserving coupled lifecycle for actual extended-connect
  sessions;
- update the vendored Cargo checksum for the changed Neqo source;
- leave ordinary HTTP requests, WebTransport, CONNECT-UDP, and full tunnel
  cancellation unchanged.

Behavioral risk:

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

Implementation:

- cap only a connect-only tunnel's slow-consumer buffer at 256 KiB and resume
  Neqo reads after the application drains it;
- for connect-only tunnels, retain a received FIN until all bytes already
  buffered by the tunnel have reached the consumer;
- treat `STOP_SENDING` on a connect-only regular CONNECT tunnel as closing
  only its sending direction, while leaving the receive direction available;
- report the current tunnel buffer size through `Available()`;
- leave ordinary H3 requests, WebTransport, CONNECT-UDP, and non-tunnel reset
  handling unchanged.

Behavioral risk:

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

Add a strict NaiveProxy-compatible JSON subset, automatic persistent or
temporary profile lifecycle, runtime logging policy, simultaneous local
SOCKS5 and HTTP CONNECT-only
listeners, string/array per-listener upstream mapping, explicit IPv4/IPv6 bind
addresses, and packaged no-argument/positional config invocation.

The compatibility correction was derived from
`net/tools/naive/naive_config.cc` and `naive_proxy_bin.cc` at NaiveProxy tag
`v150.0.7871.63-1`: one parsed proxy is shared by all listeners, while two or
more proxy-array entries must match the listener count and are selected by
listener index. Comma-separated multi-hop chains are a separate NaiveProxy
feature and remain explicitly outside this project's current scope.

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
at 16 KiB and preserves post-header bytes as initial tunnel payload. Configured
numeric IPv4/IPv6 addresses are passed to `nsIServerSocket::InitWithAddress`,
so wildcard and LAN binds behave like NaiveProxy instead of being rewritten to
loopback. Local listener authentication is still absent and external exposure
is therefore an operator security decision documented in `README.md` and
`KNOWN-ISSUES.md`.

An explicit `NAIVEFOX_PROFILE` remains strict and persistent. Automatic
XDG/HOME state preserves the previous behavior when writable; only its absent
or unusable case now falls back to an atomically created mode-`0700` temporary
directory. This changes no PSM/NSS preference or trust behavior and requires no
Firefox hook.

Tests:

- warning-free `./mach build -j4 binaries`;
- 49/49 project and padding gtests;
- complete local H2/H3 suite, including prior robustness and capture gates;
- strict H2 and H3 config workloads with simultaneous wildcard-bound SOCKS5
  and HTTP CONNECT listeners and repeated per-listener proxy-array entries;
- a concrete non-loopback interface bind and successful client connection;
- disabled/console/file logging, persistent-profile checks, and real
  config-mode startup without HOME/XDG/profile variables using a temporary
  mode-`0700` profile;
- copied staged package with adjacent no-argument and positional config;
- supplied real Caddy over both strict protocols with public CA validation.

Commits:

- `29275f3d3f03 NF-CONFIG-01 add config-driven local proxy frontends`
- `5a9cabd981ba NF-CONFIG-02 verify config mode locally and staged`
- `92d25f965b8c NF-CONFIG-03 match NaiveProxy listener and proxy-array semantics`
- `3e3ab3ddd466 NF-CONFIG-05 support users without home directories`

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

1. fast-forward only the clean `main` mirror from `upstream/main`;
2. create `refresh/firefox-YYYYMMDD` from the validated `naivefox`;
3. merge `main` there and inspect every inventory file, not only conflicts;
4. pass the complete Gate 1 build, test, staging, and conditional capture/real
   deployment checks;
5. merge the validated refresh into `naivefox` and update the exact SHA record;
6. create `refresh/minimal-YYYYMMDD` from the validated `minimal`;
7. merge only the newly validated `naivefox` there and pass the complete Gate 2
   minimal build, feature, package, size, and staged-runtime checks;
8. remove downstream patches that became unnecessary because Firefox gained an
   equivalent supported API or fix.

Never bypass either refresh branch with `main -> minimal`, and never propagate
a failure backward from `minimal` into the already validated reference branch
without evidence that the full reference is also affected.

The best downstream patch is one we can eventually remove.

## Lean DOM/GFX-free runtime gate

The following downstream files were required to make the validated lean
application usable after excluding the browser implementation graph. They are
kept as a separate refresh inventory and must be rechecked on every Firefox
base update.

### Patch NF-UPSTREAM-010

Status: implemented in the lean staged-runtime milestone

Files:

```text
netwerk/base/nsNetUtil.cpp
netwerk/base/RequestContextService.cpp
netwerk/protocol/http/nsHttpChannel.cpp
netwerk/protocol/http/nsHttpHandler.cpp
```

Purpose:

- retain the parent-only `NS_NewChannelInternal` path used by direct Necko
  fetches and the NaiveFox carrier channel;
- exclude DOM-only `NS_NewChannel` overloads and loading-node classification
  from the lean translation unit;
- obtain request-context identity without the browser-only XRE runtime
  service;
- skip optional browser dictionary and HTTP cache initialization while keeping
  network channel transport active.

Why project-only code was insufficient:

The project creates real Necko channels, but the previous lean guards returned
`NS_ERROR_NOT_AVAILABLE` or reached services whose registrations belong to the
browser component graph. Reimplementing channel creation or adding a second
network stack would violate the architecture; the minimal compatible change
is to preserve Firefox's parent path and remove only browser-only branches.

Behavioral risk:

Lean NaiveFox channels are intentionally uncached and do not expose document
loading-node overloads. Ordinary Firefox builds remain unchanged by the
`MOZ_NAIVEFOX` guards. Future Firefox refreshes must verify cache-independent
fetch, H2/H3 CONNECT, profile startup, and component registration.

Tests:

- cold lean link and direct HTTPS fetch (`example.com`, HTTP 200);
- staged runtime smoke, public fetch, persistent/temporary/no-home profiles;
- H2 and H3 raw, SOCKS, padding, robustness, Auto, config, and capture suites;
- full Firefox baseline capture uses separate libraries and remains outside
  the lean package.

Commit: `daf76d468b89 min: validate lean staged runtime`

Notes for future sync: if the lean component graph gains a supported parent
cache/XRE registration, remove the corresponding workaround rather than
retaining it by inertia.
