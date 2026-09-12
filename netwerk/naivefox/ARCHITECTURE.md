# NaiveFox architecture

## One transport and native networking

NaiveFox has one current application transport and supports coordinated
client/server updates only. It does not negotiate legacy profiles or implement
classic NaiveProxy. The name of the transport is NaiveFox. Absence of CONNECT is
not an invariant; protocol choices are judged by correctness, performance,
maintainability and observable behavior.

The client uses Firefox Necko for HTTP, native WebSocket and connection pooling,
NSS/PSM for TLS and certificate verification, and Neqo for QUIC. Do not add a
second HTTP/TLS/QUIC stack or synthetic Firefox protocol frames. Networking stays
in the single lean process without browser execution, DOM loaders or JavaScript.

Config parses the strict product configuration. SocksServer owns listeners
and local protocol negotiation. TransportStream owns byte delivery, offsets,
credit and half-close. TransportCarrier owns startup, routing and multiplexing.
OriginChannel selects explicit strict native H2/H3 routes. TransportWebSocket
adapts the native WebSocket channel. TransportCodec owns the shared wire
contract, bounded upload ring and H3 cell-stream decoder. TransportSite parses
the public HTML resource graph without executing the site.

## Startup and sustained data

The client consumes the complete public document and selected resources, checks
MIME types and completion, and verifies their shared snapshot identity in HELLO.
Twenty serial POST/GET pairs bootstrap the application carrier and already carry
useful stream data. Keep the existing startup capacities and ordering unless
a separately validated change justifies altering them.

Strict H2 moves to native WSS/TCP. Strict H3 remains HTTP/3: one persistent GET
response carries downstream cells and at most eight finite POSTs carry upstream
cells. Every H3 byte continues through Neqo; no WSS or TCP fallback is allowed.

Finite POSTs use Necko's existing upload API. A live infinite upload would
otherwise be normalized into a complete storage stream before AsyncOpen and
would require additional length, restart and early-response lifecycle changes.
WebTransport adds another session/settings integration without a present need
for unreliable datagrams. The selected design keeps the native HTTP path small.

H3 POSTs may reach the server out of order. The server bounds both active request
bodies and its reorder map, applies sequences in order, and returns success only
after application. It never acknowledges a gap to free more pipeline slots.
Cancellation, gaps exceeding the deadline, malformed cells and transport failure
end the carrier. There is no application replay or version fallback.

The shared H3 GET is framed with a four-byte network-order cell length. Its
decoder accepts arbitrary buffer boundaries and coalesced cells, rejects invalid
capacities before allocation, and holds at most one bounded cell. The request
timeout becomes an activity deadline once streaming starts.

## Ownership and flow control

Carrier state lives on the main event target. Local listener callbacks execute
on the socket target; dispatch carries ownership explicitly. Cross-thread-owned
objects use thread-safe refcounting. Lifetime safety does not permit concurrent
state mutation.

Each carrier admits at most 32 logical streams; the client may create more
carriers. Stream receive credit is 512 KiB on H2 and 1 MiB on H3, returned only after local delivery.
Upload buffering uses a bounded ring; frame boundaries are independent of ring
wrap. Server read queues and HTTP request concurrency are bounded. Partial writes
and WOULD_BLOCK must preserve every unsent byte.

FIN closes one direction after buffered data is delivered; the opposite
direction remains usable. RESET terminates the stream. Stream offsets wrap
modulo 2^32. Cell sequence exhaustion ends the carrier instead of wrapping.
Remote domain targets remain hostnames in OPEN and are resolved by the server.

## Acceptance

Correctness and short mechanism checks precede long performance campaigns.
Verify native H3 UDP traffic with no TCP tail, sustained data, cancellation,
half-close, slow consumers, reordered POSTs and bounded memory. Compare speed,
latency and the established p1-16, p17-32, p1-32, 250 ms and Whole views using the
same reference, grouping and health checks. Only minor metric drift is acceptable;
a material regression requires investigation and a design change.

Preserve a known baseline outside the source tree. Temporary comparisons with
that baseline are evidence, not a supported legacy product mode. Keep temporary
builds, captures and exports together and reuse incremental build outputs.
