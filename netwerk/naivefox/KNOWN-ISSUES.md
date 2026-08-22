# Known issues and constraints

This file contains unresolved product limitations only. Completed milestones,
one-off observations, frozen-base test failures, and run results belong in Git
history or generated evidence.

## Single-process networking

NaiveFox disables Firefox's separate socket process. The current raw
upgrade-connect callback exposes asynchronous tunnel streams only in the parent
process; it has no IPC stream-takeover contract. Necko, Neqo, PSM, and NSS still
own the wire protocols and connection management.

Enabling the socket process requires an IPC-capable design plus cross-process
lifecycle, half-close, backpressure, and shutdown regressions. Do not enable it
only by changing preferences.

## Unauthenticated local listeners

SOCKS5 and HTTP CONNECT listeners do not authenticate local clients. Loopback
is the safe default, but explicit wildcard and LAN addresses are accepted for
NaiveProxy-compatible configuration. Operators exposing such an address must
provide host-firewall or trusted-network protection.

The HTTP frontend accepts CONNECT only; ordinary forward HTTP returns 405.

## Temporary profile cleanup

Gecko requires a writable filesystem profile. If neither an explicit profile
nor writable XDG/HOME state exists, NaiveFox creates a private temporary
profile. Orderly shutdown removes it, but an uncatchable process termination
may leave it for the runtime-directory or operating-system temporary-file
cleanup policy.

Use `NAIVEFOX_PROFILE` when NSS databases or other profile state must persist
across restarts.

The embedded API does not use this fallback. Its caller must provide an
existing writable profile directory and owns that directory's lifecycle.

## Embedded Gecko lifecycle is one-shot

`NaiveFoxRunEmbedded()` is blocking and supports one process-wide runtime.
Concurrent calls are rejected. `NaiveFoxRequestStop()` is thread-safe and
orders listener, active-session, event-loop, and XPCOM shutdown, but a new run
after a completed Gecko initialization and shutdown is not supported in the
same process. A downstream host that needs a fresh runtime must use a fresh
process.

## Android linker namespaces remain host-owned

The embedded `runtimePath` tells Gecko where its runtime is and establishes
`MOZ_ANDROID_LIBDIR`; it cannot make `libxul.so` or its `DT_NEEDED` closure
visible through an Android application linker namespace. The downstream host
must package the staged `lib/arm64-v8a` libraries together and load them from a
namespace that permits sibling dependency resolution. An AAR/JNI wrapper may
provide that policy later, but it is intentionally outside this repository.

The static package verifier and NDK harness build do not prove device loader,
network, or shutdown behavior. Acceptance still requires the Android embedded
runner on an online ARM64 API-26+ device or emulator. A host without an `adb`
device or KVM cannot claim that device gate.

## No Android VPN integration

The Android artifact is only an embeddable native local-proxy runtime. It has no
Java/Kotlin API, Android service or manifest, `VpnService`, TUN, tun2socks,
socket `protect()` callback, DNS routing, per-app routing, or VPN lifecycle.
Those capabilities require a downstream Android integration and are not
implicitly supplied by the SOCKS5/HTTP CONNECT listeners.

## Auto mode has no cross-connection memory

Developer `auto` mode makes a new strict H3 establishment decision for every
accepted SOCKS connection. It has no H3 failure cache, backoff, or shared
network-quality state. The only allowed fallback is one fresh H2 attempt after
pre-CONNECT H3 establishment failure.

Normal JSON config deliberately has no Auto scheme: `https://` is strict H2 and
`quic://` is strict H3.

## Product scope

- SOCKS BIND and UDP ASSOCIATE are not implemented.
- CONNECT-UDP, MASQUE, WebTransport, TUN/TAP, transparent proxying, and GUI work
  are not implemented.
- Comma-separated multi-hop proxy chains are rejected.
- The local reproducible Caddy fixture and exhaustive integration runners are
  Linux-oriented. Windows packages use cross-build, staged smoke, and native
  churn/soak verification rather than a second copy of the fixture stack.
- The staged runtime is a dependent Gecko package, not one static executable.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for intentional design boundaries and
[`test/integration/README.md`](test/integration/README.md) for current gates.
