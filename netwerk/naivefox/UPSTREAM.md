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
Base commit: TO_BE_RECORDED_BY_AGENT
Last sync: TO_BE_RECORDED_BY_AGENT
```

### Patch NF-UPSTREAM-001

Status: planned

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

### Patch NF-UPSTREAM-002

Status: research required

Files:

```text
TO_BE_DETERMINED
```

Purpose:

Expose a raw successful HTTP proxy CONNECT tunnel without requiring an artificial NaiveFox-specific Upgrade/ALPN wire marker.

Before implementation, document the exact existing Firefox path and why it is insufficient.

Tests:

- focused raw CONNECT test,
- existing proxy CONNECT tests,
- wire/decrypted-header verification that no synthetic marker is sent.

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
