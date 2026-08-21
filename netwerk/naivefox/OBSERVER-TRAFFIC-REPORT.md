# External-observer traffic comparison

The quick observer comparison uses the downloaded, SHA-pinned latest Firefox
Nightly binary and does not build Firefox. A full same-base observer comparison
requires `NAIVEFOX_CAPTURE_MODE=same-base` and matching ordinary Firefox and
NaiveFox packages; that mode is an explicitly requested diagnostic, not a
merge or release gate. See the full policy in [`UPSTREAM.md`](UPSTREAM.md).

Date: 2026-08-20

Environment: `Ubuntu24Dev`, x86-64, strict loopback-only Caddy TLS endpoint.
The historical record used the clean Mozilla Firefox 154.0 archive. New quick
runs use the Nightly manifest; only same-base runs build ordinary Firefox and
NaiveFox from one Firefox base in isolated packages.

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
semantics. The two Firefox releases may differ in visible configuration; those
fields are reported as booleans rather than treated as exact-match gates.

## Visible handshake

| Passive-observer field | Result |
|---|---|
| Outer TCP streams | 2 for official Firefox (retry allowed); 1 for NaiveFox |
| Ordered cipher suites | Compared; visible ClientHello equality: no |
| Ordered extension types | Compared; independent NSS order/randomization is expected |
| Supported TLS versions | Compared; no replacement TLS stack |
| Supported groups | Compared; release drift is reported |
| Signature algorithms | Compared; release drift is reported |
| Key-share group identifiers | Compared; release drift is reported |
| Offered ALPN identifiers | Exact match |
| SNI | Same fixture front-end |
| Visible ServerHello version/cipher/group | Equality: yes in this run |
| Canonical selected-field SHA-256 | Per-side hashes are retained only in the ignored safe summary |

Random values, session identifiers, and key-exchange bytes are intentionally
excluded because they must differ between independent secure connections. The
test compares ordered configuration fields and records equality booleans; it
does not require an exact match between official Firefox 154 and the pinned
NaiveFox snapshot. This still proves that both paths use Firefox NSS/PSM rather
than NaiveFox synthesizing a Chrome-like ClientHello.

## Encrypted record aggregates

| Measure | Firefox GET | NaiveFox CONNECT traffic |
|---|---:|---:|
| Server TCP payload bytes | 4,213,955 | 4,213,127 |
| Server TLS records | 536 | 526 |
| Server TLS bytes | 4,211,275 | 4,210,497 |
| Server TLS length p10 | 26 | 26 |
| Server TLS length p50 | 281 | 3,872 |
| Server TLS length p90 | 16,401 | 16,401 |
| Server TLS length p95 | 16,401 | 16,401 |
| Server TLS length p99 | 16,401 | 16,401 |
| Client TLS records | 12 | 14 |
| Capture duration | 22 ms | 31 ms |
| 100 ms bursts | 1 | 1 |
| Teardown RST observations | 4 | 2 |

The bulk server-side record distribution is very close: identical p10 and
p90/p95/p99, with a different median caused by framing two padded CONNECT
streams instead of one GET. Client record counts, duration, burst count, and
test teardown differ because the application transactions differ and Firefox
also renders a screenshot. These are observable workload traits, not evidence
of an alternate TLS/H2 stack. Official Firefox made a normal retry against the
fixture (two TCP streams); NaiveFox retained one pooled outer TCP stream.

TLS 1.3 hides selected ALPN, HTTP methods, CONNECT authority, proxy
authorization, padding headers, HTTP/2 SETTINGS, and payload from this passive
test. A random request canary was absent from capture plaintext. Raw pcap,
profiles, screenshots, and logs were deleted after producing these aggregates.

The capture used WSL's synthetic `any` interface because `lo` produced no
packets in this environment. Duplicate local receive copies were excluded from
the aggregates. Loopback GRO/GSO and local scheduling can affect packet timing,
so TLS record lengths are the stronger result; repeat on a veth/physical link
before treating timing values as a deployment fingerprint.
