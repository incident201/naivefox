# Native no-connect transport

NaiveFox has two transports: `classic` (default) and `no-connect`.
Classic remains ordinary Naive-compatible CONNECT. No-connect uses an HTML-derived public bootstrap and ordered
HTTP startup followed by a persistent shaped native WebSocket.

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
The only supported wire contract is the current `native-stream-v2`.
Upgrade client and server together; earlier implementations are not supported.
Omit the Caddy `profile` option or set it to `native-stream-v2`.

Public GET/HEAD responses have no `X-App-*` headers. Root GET sets an ordinary
Secure/HttpOnly `session` cookie bound to the client IP. Public resources do not
advertise transport capabilities or snapshot metadata. Anonymous carrier and
WebSocket requests use normal site fallback, including empty NFC1 cells,
malformed uploads and invalid credentials.

Configure the nested `forward_proxy` credentials and access policy once for
both transports. Keep the hostless `:443` site address alongside the named
proxy hostname so classic destination-authority CONNECT requests reach it.

An absolute `application_root` contains the complete public site. The server
derives its startup inventory from the actual UTF-8 `index.html`, validates
stable file reads and serves an immutable memory snapshot at actual sizes.
There are no compulsory asset names, image counts, manifest files or padding.

The client loads every distinct supported directly declared stylesheet,
classic deferred script, eager image, image preload and favicon. Compatible
duplicate URLs are loaded once within that carrier. Navigation links,
CSS dependencies, JavaScript requests and dynamically inserted content are not
followed. A root-only page is valid. Unsupported automatic network declarations
fail explicitly; this is a defined first-level contract, not a full browser.

See the server's [site requirements](https://github.com/incident201/naivefox-transport/blob/main/docs/SITE.md)
for supported markup, URL/MIME rules, update behavior and size recommendations.
There is no fixed total byte budget or resource-count maximum. Large directly
declared files increase startup cost on every new carrier and server snapshot
memory. Keep the initial page lightweight; the old 72-KiB bootstrap is a
reference point, not a required size.

Client caching remains disabled. Public bodies are streamed through fixed-size
I/O buffers, with at most six resource GETs active at once. Parser/URL metadata
still scales with document structure and resource count. Ordinary deadlines,
backpressure, checked lengths and allocation-failure handling remain necessary.
The client validates complete responses and MIME families while hashing each
public body with NSS SHA-256. It computes an ordered snapshot digest from the
document digest, resource URLs, kinds, MIME types and body digests. The first
POST carries AUTH alone. The first authenticated GET contains exactly one HELLO
confirming `native-stream-v2` and the server snapshot digest. Both are checked
before OPEN. A mixed snapshot fails without reload/retry. Bodies remain streamed
through bounded buffers, retaining one digest and MIME value per resource.

The client uses Gecko's DOM-free HTML tokenizer/tree builder. No JavaScript
execution, full DOM, style processing, image decoder, browser worker, local WSS
bridge or additional network stack is introduced. Upgrade server and client
together whenever the contract changes. TLS certificate and hostname validation
remain mandatory before credentials are sent.

## Lifecycle and bounds

The client completes the root, all selected resources, and twenty ordered startup
POST/GET pairs before opening `/api/realtime` with `nfc1.stream.v1`.
Startup uploads are 4096 bytes. Responses use four 8192-byte slots, two
32768-byte slots, twelve 65536-byte slots, and two final 8192-byte slots.
The first pair authenticates and confirms the contract. Proxy frames may
displace filler from the second pair onward. The twenty pairs contribute
960 KiB of body capacity per new carrier in addition to the site resources.
The public site script is not required to implement this carrier protocol.

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
| Server to client | 512 B | 8 KiB | 64 KiB when data and framing fill the grant | 256 KiB when data and framing fill the grant |

Partial payload and OPEN retain a 2-ms coalescing turn. An already-full
selected capacity or pure control dispatches immediately. During server
coalescing, data and credit notifications recheck maximum-cell fullness;
the deadline never restarts. Capacity accounts for DATA/control framing and
reserves the optional ACK header so returned full-cell credit retains its tier.
Smaller tiers keep their aggregation deadline.

A credit-limited remainder smaller than an 8-KiB cell's DATA budget does not
start a standalone DATA cell when more data is queued. It can still accompany
other sendable data. Short final tails remain sendable when their available
credit covers the backlog, and controls/FIN do not wait for DATA credit. This avoids repeatedly padding a tiny window
remainder into a full cell. There are no peer-pressure hints or delayed receive
credit grants. The server encodes each response once, generating random filler
only for its unused suffix.

Per-stream receive credit is 512 KiB; local upload buffering is at most
256 KiB, using a ring buffer without moving the remaining upload on each
DATA frame. Credit is returned only after delivery to the local consumer.
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
