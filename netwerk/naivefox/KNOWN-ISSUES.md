# Known issues and constraints

This file contains unresolved product limitations only. Completed milestones,
one-off observations, frozen-base test failures, and run results belong in Git
history or generated evidence.

## Single-process networking

NaiveFox intentionally keeps networking in one process. The native application
carrier has no cross-process ownership/lifecycle design. Necko, Neqo, PSM and
NSS still own networking and encryption. Do not enable the socket process by
changing preferences alone.

## Local listener exposure

HTTP CONNECT listeners do not authenticate local clients. SOCKS5 listeners can
require RFC 1929 username/password authentication when credentials are present
in the `listen` URI. Loopback is the safe default, but explicit wildcard and
LAN addresses are accepted when explicitly configured. Operators
exposing such an address must provide host-firewall or trusted-network
protection.

The HTTP frontend accepts CONNECT only; ordinary forward HTTP returns 405.

## Temporary profile cleanup

Gecko requires a writable filesystem profile. Config mode creates a private
temporary profile by default; orderly shutdown removes it, but an uncatchable
process termination may leave it for the runtime-directory or operating-system
temporary-file cleanup policy.

Use `NAIVEFOX_PROFILE` when NSS databases or other profile state must persist
across restarts. `SSL_CERT_FILE` trust anchors are deliberately process-local
and do not persist in either profile mode.

The embedded API does not use this fallback. Its caller must provide an
existing writable profile directory and owns that directory's lifecycle.

## Profile-keystore cache encryption

The lean runtime does not include Firefox's profile keystore. Ordinary Cache2
resource caching remains available, but explicitly enabling
`browser.cache.disk.encryption.enabled` cannot load a cache cipher. Native
cache handling rejects affected disk entries instead of persisting plaintext.
This does not change NSS transport encryption or certificate validation.

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

## Carrier boundaries

The matching NaiveFox Caddy module is required. There is no compatibility with
classic NaiveProxy or an arbitrary static website. Client and server must be
updated together; version negotiation, session resumption and transparent replay
after an outer-session failure are not supported.

The client consumes only supported directly declared HTML resources. It does
not execute scripts, follow CSS imports or emulate a browser. Large sites add
startup traffic, latency and server snapshot memory; there is no fixed site-size
budget. Client resource caching remains disabled.

H2 uses WSS/TCP after startup. H3 uses HTTP/3 throughout, but its shared downstream
GET does not give each logical destination an independent QUIC stream. The H2
adapter keeps 512 KiB of stream credit and H3 keeps 1 MiB. Long credit turnaround can limit
single-stream throughput; the H3 upload pipeline is also bounded to eight requests.

Short controlled-link results apply only to their recorded application, network
and binaries. They do not establish passive traffic indistinguishability across
arbitrary networks. See [TRANSPORT.md](TRANSPORT.md).

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
