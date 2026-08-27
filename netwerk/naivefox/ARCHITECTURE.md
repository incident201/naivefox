# NaiveFox architecture

NaiveFox is a thin product layer around Firefox networking. Its central design
constraint is that the wire stack remains Firefox's stack:

```text
local client
  -> SOCKS5 or HTTP CONNECT parser
  -> TunnelSession
  -> Necko proxied channel
  -> NSS/PSM TLS + HTTP/2, or NSS/PSM + Neqo QUIC/HTTP/3
  -> classic CONNECT to forwardproxy@naive
  -> bounded DuplexPump + Naive Variant 1 codec
  -> local client
```

NaiveFox does not construct TLS handshakes, H2/H3 frames, HPACK/QPACK, stream
IDs, connection pools, or transport flow control.

## Components and ownership

`GeckoRuntime` initializes the headless XPCOM/Necko/PSM environment, profile,
preferences, and event loop. The small desktop executable and the Android
embedded runner are frontends over the same initializer and proxy core. The
Gecko-facing implementation remains inside `libxul` behind a controlled C ABI.

`Config` parses the strict JSON subset and produces one or more listener/proxy
pairs. `SocksServer` owns the local server sockets. Each accepted connection is
handled by either the bounded SOCKS5 state machine or the bounded HTTP CONNECT
parser; both pass the destination and any already-read payload to the same
tunnel backend.

`TunnelSession` owns the H2/H3/Auto attempt lifecycle, CONNECT metadata,
padding negotiation, and transition to one established tunnel. Attempt
callbacks carry an immutable generation so cancelled or superseded Auto
callbacks cannot publish stale streams.

`NeckoTunnel` creates the explicit proxy route and connect-only channel. Necko
owns route selection, authentication, connection reuse, and CONNECT transport.
The upgrade listener receives asynchronous input/output streams only after a
successful raw CONNECT.

`NaivePadding` and `PaddingNegotiation` implement the compatibility layer. The
codec is independent of Necko and local sockets. `DuplexPump` connects the
local byte stream to the tunnel streams with fixed-size buffers and async
backpressure.

## Threading and lifetime

Firefox's main and socket event targets both participate in tunnel setup and
shutdown. A `RefPtr` captured by a runnable may be retained and released on a
different thread from the original owner. Any object that crosses these event
targets, including `TunnelSession` and shared server state, therefore requires
thread-safe refcounting.

Atomic lifetime does not make object state generally thread-safe. State changes
remain confined to the documented owning event target or are dispatched there.
Queued callbacks must hold strong references, stale Auto generations must be
ignored, and teardown must be idempotent. A successful last reference release
must occur only after queued work has finished.

The embedded entrypoint is deliberately blocking. A host calls
`NaiveFoxRunEmbedded()` on one worker thread, which becomes the Gecko main/event
thread for that runtime, and may call `NaiveFoxRequestStop()` from another
thread. The stop request is atomically published and dispatched to the owning
event targets: listener sockets stop accepting, active connections and tunnel
requests are cancelled, the event loop drains and exits, and XPCOM shuts down
before the blocking call returns. Dispatch failure must complete the associated
connection bookkeeping rather than leave shutdown waiting indefinitely.

## Raw CONNECT contract

Firefox's upgrade callback is the existing way to receive tunnel streams, but a
normal non-empty upgrade token is reflected into wire headers. NaiveFox uses
this connect-only sequence:

```text
setConnectOnly(false)
HTTPUpgrade("", listener)
AsyncOpen(listener)
```

The downstream raw-CONNECT hook permits an empty protocol only on a
connect-only channel. It keeps the stream callback without emitting synthetic
`ALPN`, `Upgrade`, or `Connection` headers. The only intentional Naive marker is
the validated `padding` header inserted into the generated proxy CONNECT head.
CONNECT status and response headers are read through normal proxied-channel
metadata.

## H2 transport

H2 mode creates an HTTPS proxy route, disallows H3, and verifies negotiated
outer `h2`. Firefox's normal connection manager creates the TLS/TCP connection
and `Http2StreamTunnel`; NaiveFox receives only its byte streams.

The downstream H2 tunnel fixes preserve byte-stream semantics for raw CONNECT:

- consumed bytes advance normal H2 flow-control accounting;
- successful output close sends `END_STREAM` without closing input;
- buffered response bytes are delivered before graceful EOF;
- ordinary HTTP transactions keep their existing behavior.

## H3 transport

H3 mode uses Firefox's H3-proxy route:

```text
MASQUE-type proxy info
  -> HttpConnectionUDP
  -> Http3Session / Neqo / NSS
  -> regular CONNECT Http3StreamTunnel
  -> Http3TransportLayer byte streams
```

The route type selects an H3 proxy connection, but the request itself is
classic CONNECT. It does not use the MASQUE URI template, CONNECT-UDP,
WebTransport, or datagrams.

A strict proxy flag disables the normal H3 backup timer, MASQUE-to-HTTPS
restart conversion, and Happy Eyeballs TCP route for that transaction. The
channel's protocol metadata must report H3; integration tests additionally use
an H3-only UDP listener with no TCP listener at the proxy port.

A separate route flag suppresses only the automatic PMTUD force normally
derived from outer-H3-proxy identity. `Http3Session` retains that identity for
TLS host selection and proxy session behavior, while passing a distinct PMTUD
input to Neqo. The global `network.http.http3.pmtud` preference remains
authoritative and can explicitly restore PMTUD on the same binary.

H3 raw tunnels preserve byte-stream behavior without copying H2 internals:

- async callbacks are published before a notification that may re-enter;
- successful connect-only output close uses Neqo's send-side FIN and keeps the
  receive side alive;
- the slow-consumer buffer is capped at 256 KiB and resumes reads after drain;
- received FIN and `STOP_SENDING` do not discard already-buffered response data;
- ordinary H3 requests, WebTransport, and CONNECT-UDP retain their normal
  lifecycle.

## Protocol selection

- H2: one strict HTTPS/H2 attempt; any other outer protocol is failure.
- H3: one strict QUIC/H3 attempt; no TCP/H2 traffic is permitted as fallback.
- Auto: one strict H3 attempt, then at most one new H2 attempt when H3 fails
  before CONNECT response or tunnel streams are observed.

Authentication failure, ACL/target rejection, any CONNECT response, and failure
after tunnel establishment are terminal in Auto. A bounded establishment timer
may classify a no-response/no-transport H3 attempt as eligible for retry.
Every attempt has its own padding value and generation; the local success reply
is withheld until the final attempt has valid protocol, CONNECT status, channel
completion, and streams.

## Padding and pumping

The proxy CONNECT carries a randomized Naive `padding` request header. Payload
padding activates only when the successful response also contains `padding`.
Legacy Variant 1 frames the first eight records in each direction:

```text
u16 big-endian payload length | u8 padding length | payload | zero padding
```

Payload chunks larger than 65535 bytes are split. Decoder state is independent
of transport read boundaries and handles split headers, split payload/padding,
coalesced records, and raw bytes following the final framed record.

The pump tolerates partial reads/writes and `WOULD_BLOCK`, uses asynchronous
callbacks, bounds memory, propagates EOF/errors, and avoids recursive busy
loops. Its buffers do not correspond to H2 DATA frames, H3 frames, QUIC
packets, or Naive records.

## Process and profile model

The current product deliberately disables Firefox's separate socket process.
Raw upgrade-connect stream takeover is not IPC-capable, while both H2 and H3
operate through the parent-process Necko stack. Enabling the socket process
requires a designed IPC stream handoff and lifecycle regressions; it must not be
turned on as a preference-only experiment.

One process may host several local listeners. They share the Gecko runtime,
Necko connection manager, TLS/QUIC stack, padding code, and tunnel backend.
Firefox, not NaiveFox, owns outer connection reuse.

There are two runtime-location frontends:

```text
desktop CLI -> discover executable/runtime location -> common Gecko initializer
embedded API -> caller-supplied runtime directory  -> common Gecko initializer
```

Desktop discovery continues to use the executable path (`/proc/self/exe` on
Unix and the module filename on Windows). Embedded Android must not use
`/proc/self/exe`, because it names the host application rather than the
NaiveFox runtime. Its caller supplies the absolute library directory, and the
embedded frontend sets `MOZ_ANDROID_LIBDIR` before any `BinaryPath`, Gecko, or
XPCOM work. No `JNIEnv`, Android `Context`, Java type, or GeckoView lifecycle is
part of the common initializer.

Android application sandboxes may deny the route-netlink monitor used by
Gecko's native network-link service. After that monitor reaches a terminal
failure, the embedded runtime follows the service's existing conservative
unknown-network policy and continues through the ordered main-thread and
socket-thread startup barriers. Linux still requires successful initial
netlink convergence, and a pending state, timeout, missing service, or failed
queue barrier remains fatal on every platform.

The instance contract is one NaiveFox runtime per process. Multiple listeners
belong to that one runtime; concurrent starts are rejected. After a successful
Gecko/XPCOM initialization and shutdown, another embedded start in the same
process is not supported because Gecko's process lifecycle is one-shot.

Gecko requires a writable profile. Config mode uses a unique private temporary
profile unless the operator explicitly supplies `NAIVEFOX_PROFILE`; the profile
choice never weakens NSS certificate or hostname verification. `SSL_CERT_FILE`
adds process-lifetime trust anchors through the NSS temporary certificate
context and never imports certificate or trust records into the profile. When
that explicit CA is not a built-in Firefox root, the common initializer also
temporarily disables Firefox's third-party-root H3 guard and restores the
original preference during shutdown. The environment option is applied by the
common desktop and embedded initializer.

The embedded frontend instead requires the host to provide an existing,
writable profile directory. It neither discovers desktop state directories nor
creates an Android application profile. Both frontends pass the chosen profile
to the same Gecko initialization path and use the same JSON parser, listeners,
sessions, transports, and shutdown machinery.

## Validation boundaries

The reproducible loopback fixture proves strict transport selection, scoped
trust, authentication failure, header negotiation, payload integrity,
backpressure, half-close, concurrency, connection reuse, and shutdown. A real
Caddy deployment is the second interoperability gate. Same-base Firefox capture
is an optional diagnostic, not a product build gate; see
[`CAPTURE.md`](CAPTURE.md).

All modifications outside `netwerk/naivefox/` are maintained in
`UPSTREAM-PATCHES.md` in the full maintenance checkout.
