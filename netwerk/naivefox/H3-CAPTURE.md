# HTTP/3 capture comparison

This report compares ordinary Firefox and NaiveFox from the same local build
family against the strict H3-only loopback Caddy fixture. Both processes load
the same `libxul` and NSS libraries. The comparison was recorded on revision
`5ef23ed3ab7d3480bc110f85120ffb92d4ebdcf7` with TShark 4.2.2.

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
| Outer QUIC connections | 1 | 1 |
| CONNECT request streams | n/a | 2, stream IDs 0 and 4 |
| TCP sessions / TCP payload | 0 / 0 bytes | 0 / 0 bytes |
| Server-side encrypted bytes | 2,164,740 | 2,167,427 |

The parsed TLS configuration is semantically equal: ClientHello length,
TLS-version offer, cipher suites, supported groups, signature algorithms, key
shares, SNI, and `h3` ALPN match. TLS extension order is deliberately not used
as an equality key. Firefox/NSS randomizes the order independently per
connection and sends GREASE values; the two captures therefore need not have
the same raw extension sequence even though they use the same configuration.

The client QUIC transport-parameter type vector and all named values decoded by
TShark match. Representative shared values are a 30,000 ms idle timeout,
25,165,824-byte initial connection credit, 12,582,912-byte local bidirectional
stream credit, 100 bidirectional and 100 unidirectional streams, active CID
limit 8, and maximum DATAGRAM frame size 65,535. Connection IDs themselves are
random and are not fingerprint equality inputs.

The client HTTP/3 SETTINGS blocks are equal. Both advertise QPACK maximum table
capacity 65,536 and 20 blocked streams; the full decoded setting ID vector is
also compared. Extended CONNECT and H3 DATAGRAM settings are present because
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
| UDP datagrams | 1,762 | 1,845 |
| Client UDP bytes | 13,655 | 17,396 |
| Server UDP bytes | 2,167,712 | 2,166,305 |
| Client UDP length p50 / p95 | 44 / 232 | 41 / 160 |
| Server UDP length p50 / p95 | 1,438 / 1,438 | 1,374 / 1,417 |
| Version Negotiation packets | 0 | 0 |
| Established TCP / TCP payload | 0 / 0 | 0 / 0 |

The passively visible semantic ClientHello configuration and client transport
parameters match. Packet-volume and timing equality is intentionally not
required: one browser GET and two padded CONNECT streams are different
workloads.

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
  20260813T113736Z-e3502bee/summary.txt
```

It contains hashes, counts, and protocol metadata only. It was scanned for
authorization names and credentials before the private capture material was
removed. The comparison proves that NaiveFox uses Firefox Necko/Neqo/NSS wire
machinery; it does not claim identical packet timing for unlike GET and CONNECT
workloads.
