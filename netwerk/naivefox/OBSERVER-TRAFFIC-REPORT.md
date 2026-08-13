# External-observer traffic comparison

Date: 2026-08-13

Environment: `Ubuntu24Dev`, x86-64, Firefox opt build, same loopback-only Caddy
TLS endpoint for both clients

This test asks what a passive network observer can see when ordinary Firefox
and NaiveFox use the same TLS front-end. It deliberately does **not** set
`SSLKEYLOGFILE`, decrypt TLS, inspect HTTP/2, or read CONNECT headers. The
separate internal-stack audit in [`CAPTURE.md`](CAPTURE.md) covers decrypted
Necko/NSS/H2 details.

Run it with:

```bash
netwerk/naivefox/test/integration/run-observer-comparison.sh
```

The ordinary Firefox reference makes one normal HTTPS GET for 4 MiB of
deterministic text. One NaiveFox process carries two 2 MiB target downloads as
two CONNECT streams. The total encrypted server-to-client application volume
is therefore closely matched while preserving each program's real application
semantics. Both binaries use the same build's `libxul` and NSS libraries.

## Visible handshake

| Passive-observer field | Result |
|---|---|
| Outer TCP streams | 1 for Firefox; 1 for NaiveFox |
| Ordered cipher suites | Exact match |
| Ordered extension types | Exact match |
| Supported TLS versions | Exact match |
| Supported groups | Exact match |
| Signature algorithms | Exact match |
| Key-share group identifiers | Exact match |
| Offered ALPN identifiers | Exact match |
| SNI | Same fixture front-end |
| Visible ServerHello version/cipher/group | Exact match |
| Canonical selected-field SHA-256 | `04003c95a03dfd9503508fb4c938b5b4a4616b7a385380dae659f63d94a97242` |

Random values, session identifiers, and key-exchange bytes are intentionally
excluded from equality because they must differ between independent secure
connections. The test compares the ordered configuration fields that form the
externally useful TLS fingerprint. This result follows from both programs using
Firefox NSS/PSM rather than NaiveFox synthesizing a Chrome-like ClientHello.

## Encrypted record aggregates

| Measure | Firefox GET | NaiveFox CONNECT traffic |
|---|---:|---:|
| Server TCP payload bytes | 4,210,997 | 4,213,567 |
| Server TLS records | 525 | 529 |
| Server TLS bytes | 4,208,372 | 4,210,922 |
| Server TLS length p10 | 26 | 26 |
| Server TLS length p50 | 1,210 | 1,592 |
| Server TLS length p90 | 16,401 | 16,401 |
| Server TLS length p95 | 16,401 | 16,401 |
| Server TLS length p99 | 16,401 | 16,401 |
| Client TLS records | 9 | 14 |
| Capture duration | 816 ms | 44 ms |
| 100 ms bursts | 2 | 1 |
| Teardown RST observations | 8 | 2 |

The bulk server-side record distribution is very close: identical p10 and
p90/p95/p99, with a different median caused by framing two padded CONNECT
streams instead of one GET. Client record counts, duration, burst count, and
test teardown differ because the application transactions differ and Firefox
also renders a screenshot. These are observable workload traits, not evidence
of an alternate TLS/H2 stack.

TLS 1.3 hides selected ALPN, HTTP methods, CONNECT authority, proxy
authorization, padding headers, HTTP/2 SETTINGS, and payload from this passive
test. A random request canary was absent from capture plaintext. Raw pcap,
profiles, screenshots, and logs were deleted after producing these aggregates.

The capture used WSL's synthetic `any` interface because `lo` produced no
packets in this environment. Duplicate local receive copies were excluded from
the aggregates. Loopback GRO/GSO and local scheduling can affect packet timing,
so TLS record lengths are the stronger result; repeat on a veth/physical link
before treating timing values as a deployment fingerprint.
