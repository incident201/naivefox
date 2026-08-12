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

Commit: `NF04 expose raw HTTP CONNECT streams` (planned commit subject)

### Patch NF-UPSTREAM-003

Status: research required

Files:

```text
TO_BE_DETERMINED
```

Purpose:

Allow the Naive-compatible `padding` header to be present on the actual proxy CONNECT request.

Tests:

- proxy receives `padding`,
- normal CONNECT behavior unchanged,
- existing proxy tests pass.

NF-UPSTREAM-002 and NF-UPSTREAM-003 may become one small patch if the cleanest current Firefox design naturally solves both. If so, update this inventory rather than preserving artificial separation.

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
