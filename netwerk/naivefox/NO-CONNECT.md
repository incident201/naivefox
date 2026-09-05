# Native no-connect transport

NaiveFox has two transports: `classic` (default) and `no-connect`.
Classic remains ordinary Naive-compatible CONNECT. No-connect uses a fixed
ordinary HTTP startup followed by a persistent shaped native WebSocket.

## Configuration

Select JSON `"transport": "no-connect"` or pass
`--transport no-connect`. `--transport classic` selects classic.
JSON, desktop CLI and the fourth embedded API argument accept exactly these
two names. Omission preserves classic/default JSON selection.

The previous finite HTTP implementation and the hybrid/asymmetric selectors
are retired. Upgrade the client and server together; old selector names,
startup profiles and WebSocket subprotocols are not aliases.

Both transports use the same percent-decoded credentials from the proxy URI
and the same server forward-proxy authentication and destination policy.
No-connect sends Basic authentication inside the TLS-protected NFC1 AUTH frame,
never an origin Authorization header. There is no extra key or allowlist.
Valid classic-only options are inactive in no-connect; malformed fields remain
configuration errors. SOCKS target hostnames are sent to the server unchanged.

The proxy URI selects the startup protocol:
`https://` uses strict H2 and `quic://` uses strict H3.
After either startup, no-connect opens HTTP/1.1 WSS over TLS/TCP to the same
authority. H3 no-connect therefore requires both UDP and TCP access.
Classic H3 retains its strict QUIC-only behavior. There is no transport fallback.
An H2 origin that negotiates H1 may receive the initial root metadata GET;
the client refuses that response before authentication, API work or target
opening. A mismatched protocol never becomes an accepted carrier.

## Server and application

Use the matching
[naivefox-transport module](https://github.com/incident201/naivefox-transport).
The supported wire profile is `native-stream-v1`, advertised with
`X-App-Profile`, `X-App-Auth: basic` and `X-App-Realtime: websocket-v1`.
Omit the Caddy `profile` option or set it to `native-stream-v1`.

Configure the nested `forward_proxy` credentials and access policy once for
both transports. Keep the hostless `:443` site address alongside the named
proxy hostname so classic destination-authority CONNECT requests reach it.

An absolute `application_root` must name a complete seven-file application:
`index.html`, `assets/site.css`, `assets/app.js` and
`assets/image-{1,2,3,4}.svg`. The served capacities are 4096, 12288, 24576
and 8192 bytes per image. The server validates two stable source snapshots,
pads them once, then serves memory. The application remains ordinary external
operator content; the client does not execute JavaScript. No browser worker,
local WSS bridge, DOM or additional network stack is part of the product.

## Lifecycle and bounds

The client completes the root, all six assets, and twenty ordered startup
POST/GET pairs before opening `/api/realtime` with `nfc1.stream.v1`.
Startup uploads are 4096 bytes. Responses use four 8192-byte slots, two
32768-byte slots, twelve 65536-byte slots, and two final 8192-byte slots.
Proxy frames may displace filler during startup.

The transition depends on complete, validated HTTP responses. It does not
depend on a timer, packet number or transferred-byte threshold. Active HTTP
leases, bulk pipelines and idle long polls no longer exist. HTTP carrier work
cannot resume after the WebSocket transition.

NFC1 sequences and logical streams continue across the transition. Each
WebSocket binary message contains one complete cell, with zero reserved
header bytes. Text, compression, malformed cells, sequence errors and credit
violations fail the carrier. Every unused byte is fresh cryptographic filler.
Firefox owns WebSocket framing, TLS, HTTP and QUIC.

Message capacity depends only on locally sendable payload within stream credit:

| Direction | No payload | Small payload | Medium grant | Large grant |
| --- | ---: | ---: | ---: | ---: |
| Client to server | 512 B; OPEN uses 4 KiB | 4 KiB | 16 KiB at 8 KiB ready | 128 KiB at 64 KiB ready |
| Server to client | 512 B | 8 KiB | 64 KiB at 32 KiB ready | 256 KiB at 128 KiB ready |

Partial payload and OPEN retain a 2-ms coalescing turn. A full selected
capacity or pure control dispatches immediately. Capacity is rechecked after
coalescing. There are no peer-pressure hints and no delayed credit grants.
The server encodes each response once.

Per-stream receive credit is 512 KiB; local upload buffering is at most
256 KiB. Credit is returned only after delivery to the local consumer.
Stream byte offsets wrap modulo 2^32 without a 4-GiB transfer ceiling.
Each carrier holds at most 32 simultaneous streams, with additional carriers
available beyond that limit. One warm carrier is retained per route.

Only one native WebSocket application message may await a write completion.
PING/PONG payload completions (at most 125 bytes) do not consume this NFC1
budget; application sends are at least 512 bytes. Native receive dispatch is
bounded by 32 callbacks and 2 MiB, and the peer-driven PONG queue by 32.

Idle heartbeats use 512-byte cells after 25 seconds. A missing peer message
for 75 seconds closes the carrier. Empty heartbeats do not cause ACK loops.
A cumulative WS-only ACK (kind 8, stream zero, empty body) confirms processing
of the last uplink cell and local FIN retirement. It does not replace delivery
credit. Future, decreasing, malformed and HTTP-carried ACKs fail closed.

Half-close, cancellation and shutdown remain per-stream and bounded.
WebSocket failure releases all affected streams. There is no reconnect,
transparent replay or session resumption.

## Verification

Reuse the product object directories and perform incremental builds. The
Linux, native Windows and Android runners in
[test/integration/README.md](test/integration/README.md) exercise both transports,
both local frontends, H2/H3 startup, integrity, authentication/policy refusal,
concurrency, slow consumers, half-close, idle, cancellation and shutdown.

No-connect must emit zero outer CONNECT requests and reach exactly its
documented startup/WS lifecycle. Classic must retain successful CONNECT.
Capture accounting includes the startup connection and WSS, their handshakes
and complete teardown. Historical measurements keep their original transport
labels and source identities in [CAPTURE.md](CAPTURE.md); they are not aliases
in current configuration.
