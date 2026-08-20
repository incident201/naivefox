# HTTP/3 capture comparison

This report compares a clean official Mozilla Firefox release with NaiveFox
against the strict H3-only loopback Caddy fixture. The reference is downloaded
by `tools/fetch-firefox-reference.sh`, not assumed to exist in an objdir. The
audited archive is Mozilla Firefox 154.0 (`firefox-154.0.tar.xz`, SHA-256
`7665cd49ab13417270748325838e565136adbc76d41bbd76fb24d15a0cc7792b`) and the
NaiveFox side is the pinned Firefox snapshot built by this checkout. Their
TLS/QUIC configuration can legitimately differ across Firefox releases; the
gate reports that difference while requiring Firefox-owned Neqo/Necko/NSS,
strict UDP/QUIC, classic CONNECT, padding, and multiplexing.

The reproducible runner is:

```bash
netwerk/naivefox/test/integration/run-h3-capture-comparison.sh
```

It makes four independent captures: ordinary Firefox and NaiveFox with an NSS
key log for internal protocol inspection, then ordinary Firefox and NaiveFox
again with `SSLKEYLOGFILE` explicitly removed. NaiveFox is always started with
`--protocol h3`. Caddy exposes only UDP on the proxy port. The ordinary Firefox
profile forces its testing Alt-Svc mapping to the same H3 endpoint.

## Decrypted internal comparison

Result: PASS.

| Property | Ordinary Firefox | NaiveFox |
|---|---:|---:|
| Application request | one HTTP/3 `GET` | two classic HTTP/3 `CONNECT` requests |
| QUIC version | v1 (`0x00000001`) | v1 (`0x00000001`) |
| Negotiated application protocol | `h3` | `h3` |
| Outer QUIC connections | one successful capture (Firefox may retry) | 1 |
| CONNECT request streams | n/a | 2, stream IDs 0 and 4 |
| TCP sessions / TCP payload | 0 / 0 bytes | 0 / 0 bytes |
| Server-side encrypted bytes | 2,163,143 | 2,166,626 |

The parsed TLS configuration is compared field-by-field. With the clean
Firefox 154 reference, the semantic configuration and transport-parameter
equality booleans are expected to be `no` against the pinned NaiveFox snapshot;
this is version drift, not evidence of a replacement QUIC/TLS stack. TLS
extension order is never used as an equality gate: Firefox/NSS randomizes it
independently and sends GREASE values.

The client QUIC transport-parameter type vector and all named values decoded by
TShark are retained as comparison output. The clean release and pinned
snapshot report different semantic values in this run; this is expected
cross-release drift. Connection IDs themselves are random and are not
fingerprint equality inputs.

The client HTTP/3 SETTINGS blocks in the audited run were equal. Both advertise
QPACK maximum table capacity 65,536 and 20 blocked streams; the full decoded
setting ID vector is compared. Extended CONNECT and H3 DATAGRAM settings are present because
they are normal Firefox HTTP/3 capabilities; the NaiveFox request under test is
still classic `CONNECT`, not CONNECT-UDP, MASQUE, WebTransport, or an extended
CONNECT protocol.

Both CONNECT streams carried a `padding` request header and received a
`padding` response header. Decrypted header-name inspection found no synthetic
`alpn`, `upgrade`, or `connection` request header. Header values, proxy
authorization, and credentials are never copied to the safe output.

## Passive observer comparison

Result: PASS.

The passive pass does not create or supply an NSS key log. It retains only
packet direction, UDP length, QUIC version/long-header types, CID lengths, and
coarse handshake ordering. QUIC Initial protection is publicly derivable, so
the passive parser can also see the ClientHello and client transport parameters;
HTTP/3 request headers and 1-RTT plaintext remain unavailable.

| Aggregate | Ordinary Firefox GET | NaiveFox two CONNECT tunnels |
|---|---:|---:|
| UDP datagrams | 1,945 | 1,741 |
| Client UDP bytes | recorded in ignored safe summary | recorded in ignored safe summary |
| Server UDP bytes | 2,187,585 | 2,167,729 |
| Client UDP length p50 / p95 | recorded in ignored safe summary | recorded in ignored safe summary |
| Server UDP length p50 / p95 | recorded in ignored safe summary | recorded in ignored safe summary |
| Version Negotiation packets | 0 | 0 |
| Established TCP / TCP payload | 0 / 0 | 0 / 0 |

The passively visible semantic ClientHello configuration and client transport
parameters are recorded as comparison booleans, not a cross-release equality
gate. Packet-volume and timing equality is intentionally not required: one
browser GET and two padded CONNECT streams are different workloads.

In this sample ordinary Firefox performed two QUIC attempts and sent two TCP
SYN probes. The H3-only fixture immediately answered the probes with RST; no TCP
handshake or TCP application bytes existed. NaiveFox used one QUIC connection
and made no TCP probe. This is recorded rather than hidden, and does not satisfy
or imitate H2 fallback.

## Capture hygiene and limitations

WSL's `any` capture interface exposes both cooked transmit and receive copies
of a loopback packet. The runner retains only `sll.pkttype == 4` (the transmit
copy) before QUIC dissection. This avoids duplicate packet numbers corrupting
Wireshark's stateful key-phase and QPACK tracking. The safe passive aggregates
then count every retained UDP datagram; they are not deduplicated by packet
length.

After all assertions and aggregation, raw pcaps, NSS key logs, copied profiles,
screenshots, response bodies, and process logs are deleted. The retained local
summary is:

```text
obj-x86_64-pc-linux-gnu/naivefox-fixture/h3-capture-safe/
  20260820T044117Z-d078844c/summary.txt
```

It contains hashes, counts, and protocol metadata only. It was scanned for
authorization names and credentials before the private capture material was
removed. The comparison proves that NaiveFox uses Firefox Necko/Neqo/NSS wire
machinery; it does not claim identical packet timing or cross-release TLS
fingerprints for unlike GET and CONNECT workloads.
