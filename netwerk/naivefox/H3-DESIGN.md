# NaiveFox HTTP/3 transport design

## Scope and source baseline

This document describes the Firefox source snapshot tagged
`h2-prototype-v0.1`. Local source is authoritative; Searchfox was used only to
navigate related symbols. The H3 mode remains part of the existing `naivefox`
binary and reuses the existing SOCKS, padding, pump, and Gecko runtime code.

The intended data path is:

```text
SOCKS5 -> SocksConnection -> OpenNeckoTunnel
       -> Necko HTTP/3 proxy connection -> HttpConnectionUDP
       -> Http3Session -> Neqo/NSS -> QUIC/UDP
       -> regular CONNECT Http3StreamTunnel
       -> Http3TransportLayer async streams -> DuplexPump
```

No standalone Neqo API or alternative QUIC/TLS implementation is involved.

## Existing Firefox H3 proxy path

1. `nsIProtocolProxyService.newMASQUEProxyInfo()` creates a proxy info whose
   internal type is `masque`. In `nsHttpConnectionInfo` that type selects an
   HTTPS H3 proxy connection, the `h3` proxy NPN token, and CONNECT tunnelling.
   The MASQUE URI template is used by CONNECT-UDP, but is not used by the
   regular TCP CONNECT path described here.
2. `DnsAndConnectSocket` creates `HttpConnectionUDP` for the outer proxy
   route. `HttpConnectionUDP` owns `Http3Session`; `Http3Session` owns the Neqo
   connection and NSS/PSM security state.
3. For a normal proxied HTTP transaction,
   `nsHttpConnectionMgr::ProcessNewTransaction()` finds the wildcard H3 proxy
   connection and calls `HttpConnectionUDP::CreateTunnelStream()`. A non-H3
   target gets a small `Http3ConnectTransaction` plus
   `Http3Session::CreateTunnelStream(..., false)`. This is distinct from the
   CONNECT-UDP and WebTransport branches.
4. `Http3StreamTunnel::TryActivating()` calls
   `Http3Session::TryActivating("CONNECT", ...)`, which calls Neqo
   `Connect(authority, headers, ..., 3, false)`. This is a regular HTTP CONNECT
   request with `:method` and `:authority`, not extended CONNECT, CONNECT-UDP,
   or MASQUE datagrams.
5. After a successful CONNECT, `Http3StreamTunnel` creates a virtual inner
   `nsHttpConnection` over `Http3TransportLayer`. The normal
   `nsHttpConnection::HandleTunnelResponse()` and `CompleteUpgrade()` path
   invokes the existing `nsIHttpUpgradeListener` with async input and output
   streams.

The frozen Firefox tests already exercise this architecture in
`http3_proxy_common.js`, including regular CONNECT to HTTP/TLS echo servers,
large transfers, and concurrent tunnels. Those tests use
`HTTPUpgrade("webrtc", ...)`; NaiveFox instead requires an empty upgrade token
so no synthetic `ALPN`, `Upgrade`, or `Connection` marker is emitted.

## CONNECT headers and response metadata

The virtual inner `nsHttpConnection` generates the actual CONNECT request with
`nsHttpConnection::MakeConnectString()`. The downstream H2 prototype's
`setProxyConnectHeader()` stores validated extra headers on the request head,
and `MakeConnectString()` copies that sidecar into the generated CONNECT head.
The resulting flat CONNECT request is written through `Http3TransportLayer`;
`Http3StreamTunnel` parses it and passes its header block to Neqo. Therefore
the existing generic `padding` header hook is shared by H2 and H3; a separate
Naive-specific H3 header path is neither needed nor acceptable. A focused H3
test must still verify the request header on the wire.

Neqo converts H3 response headers into the normal HTTP response-head form.
`nsHttpConnection::HandleTunnelResponse()` stores a
`ProxyConnectResponseHead`, so the existing `nsIProxiedChannel`
`httpProxyConnectResponseCode` and `getHttpProxyResponseHeader()` APIs are also
shared. This is the source path used by `PaddingNegotiation` to read the
response `padding` header.

## Upgrade gating and raw stream hook

`HttpBaseChannel::Http3Allowed()` rejects a channel with an upgrade callback,
and `nsHttpChannel::ContinueOnBeforeConnect()` adds
`NS_HTTP_DISALLOW_HTTP3` for upgrade transactions. In this snapshot those
checks prevent origin H3 selection; the special outer `masque` proxy route can
still create an H3 tunnel, as shown by Firefox's existing connect-only H3
tests. They must not be removed globally.

The downstream raw CONNECT hook already permits this ordering:

```text
setConnectOnly(false)
HTTPUpgrade("", listener)
AsyncOpen(listener)
```

The H3 milestone will first add a focused regression for this exact empty-token
sequence. Core H3 internals will be changed only if that test reproduces a
failure which project code cannot fix.

## Strict selection and protocol proof

An H3 proxy info alone is not strict. `nsHttpTransaction` starts an H3 backup
timer (100 ms by default), and both the backup/restart paths can call
`CreateConnectUDPFallbackConnInfo()`, which changes the proxy type from
`masque` to `https`. Consequently `--protocol h3` needs a narrow per-channel
or per-transaction prohibition on H3-proxy fallback. Disabling the timer pref
alone is insufficient because restart fallback remains possible.

The implemented explicit-mode behavior is:

- `h2`: HTTPS proxy info, H3 disallowed, negotiated outer protocol must be
  `h2`;
- `h3`: MASQUE/H3 proxy info, H3 enabled, all TCP/H2 proxy fallback disabled
  for that transaction, negotiated outer protocol must be `h3`;
- `auto`: remains pending. It will prefer the H3 proxy route and permit one H2
  retry only for outer transport/protocol establishment failures. A 407,
  CONNECT/ACL rejection, bad target, or failure after tunnel establishment
  must not trigger fallback.

Strict H3 uses the opt-in `DISABLE_HTTP3_PROXY_FALLBACK` proxy flag. The flag
suppresses the H3 backup timer, the `masque` to `https` restart conversion, and
the Happy Eyeballs route. Consequently strict tests cannot create fallback TCP
traffic before the application checks the negotiated protocol.

The upgrade callback's H3 virtual transport does not expose the outer QUIC TLS
socket control, so the H2-only `GetNegotiatedNPN()` check cannot be reused.
The channel's `protocolVersion` reports `h3` from the CONNECT response and is
the project-level selection signal. Strict integration tests additionally use
an H3-only Caddy listener and prove UDP/QUIC traffic with no TCP flow to the
proxy port. Decrypted capture is the strongest check: ALPN `h3`, a regular
CONNECT header block, and multiple CONNECT stream IDs on one QUIC connection.

## Socket process

H3/Neqo works in the parent process through `HttpConnectionUDP`; it does not
require the socket process. Conversely, the current raw upgrade completion
path explicitly rejects upgrade-connect operation when networking runs in the
socket process. The first prototype therefore keeps the socket process
disabled, enables the H3 pref in `GeckoRuntime`, and avoids new IPC or a second
executable. Socket-process support is a later upstreamability concern, not a
reason to bypass Necko.

## Streams, backpressure, and lifecycle

`Http3TransportLayer` implements `nsISocketTransport` over
`Http3StreamTunnel` and supplies the async input/output streams consumed by the
existing `DuplexPump`. Stream callbacks run on the socket thread. The H3 tunnel
uses Neqo stream flow control and a bounded `SimpleBuffer` when the consumer
would block; it does not expose QUIC packet or H3 frame boundaries to the
padding codec.

This differs from the downstream H2 tunnel implementation, so the H2 flow
control, END_STREAM, half-close, and reset patches were not copied. Focused H3
tests first reproduced two independent failures:

- successful output close cancelled both QUIC stream directions instead of
  sending request FIN and retaining the response direction;
- a slow consumer allowed the tunnel `SimpleBuffer` to grow and then lost the
  unread response when Caddy sent `STOP_SENDING(H3_REQUEST_CANCELLED)` after
  target completion.

The narrow H3 fixes use Neqo's existing send-side close for classic CONNECT,
retain the opposite handler only for classic CONNECT rather than WebTransport
or CONNECT-UDP, cap the tunnel slow-consumer buffer at 256 KiB, retain received
FIN until buffered bytes are drained, and treat tunnel `STOP_SENDING` as a
send-direction event. Ordinary HTTP/3 transaction behavior is unchanged.

Memory acceptance remains the project invariant: two fixed 64 KiB pump
buffers per direction plus bounded codec state, with Necko/Neqo flow control
providing backpressure. Connection pooling remains owned by Necko; NaiveFox
will not add its own QUIC pool.

## Fixture and verification plan

Pinned Caddy 2.11.2 and `forwardproxy` revision `d62c80d3...` accept
`ProtoMajor == 3` regular CONNECT and route H2/H3 through the same
`dualStream`, including the same response header and Variant 1 payload
padding. This source support still requires a live regression test.

The deterministic fixture modes are:

- `h2`: the existing TCP listener with protocols `h1 h2`;
- `h3`: an H3-only TLS listener on UDP, with no TCP listener on that port.

H3 readiness is established by the Caddy UDP listener and then by the Necko
raw smoke; the system curl is not treated as an H3 oracle. Strict success
requires the raw marker, `Outer protocol: h3`, observed QUIC/UDP, and no TCP
fallback. Padding tests require request and response `padding`, eight framed
records per sending direction followed by raw bytes, and raw fallback when the
response marker is absent.

The live fixture now passes strict raw CONNECT authentication, shared Variant
1 padding, multi-megabyte SOCKS transfers, and the complete 32 MiB robustness
workload in H3-only mode. Four simultaneous CONNECT streams were observed on
one NaiveFox-owned UDP/QUIC socket, while the same workload remains green in
H2 mode. Each Firefox-core change is isolated, backed by a focused xpcshell
regression, and recorded in `UPSTREAM.md`.
