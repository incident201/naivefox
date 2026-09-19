# Default HTTPS packet transport

https:// is the default NaiveFox delivery, both directly to an origin and
through a compatible CDN. Client and server are updated together; only the
current matching implementation is supported. wss:// explicitly selects
WebSocket and quic:// selects direct HTTP/3.

## Client configuration

The existing proxy URI selects the delivery adapter; no new JSON field is used.

~~~json
{
  "listen": "socks://127.0.0.1:1080",
  "proxy": "https://user~<64-hex-SPKI-SHA256>:password@proxy.example:443"
}
~~~

Replace the placeholder with the SHA-256 hash of the DER SubjectPublicKeyInfo
of the origin's dedicated inner-TLS certificate. Obtain it independently of any
intermediary, together with the credentials. Uppercase hexadecimal is accepted and
normalized. Missing, malformed pins or an empty username/password are rejected.

An Android wrapper can use server address proxy.example, username user~PIN and
the ordinary password, then serialize an https:// URI. The wrapper must use the
updated native client and preserve the username suffix; no new JSON field is
needed. NaiveFox separates the pin at the last tilde in
the decoded username before constructing AUTH; usernames containing a tilde
remain representable. URI-escape username/password normally.

For wss:// the final tilde suffix is removed from the decoded username and
ignored, without PIN validation; no inner TLS is selected. The suffix may be
omitted. The tilde suffix is reserved for these two schemes. quic:// keeps its
ordinary username semantics. Changing a pinned HTTPS URL to wss:// therefore
keeps the same authentication username.

## Security and identity

Outer HTTP uses native Necko H2, with normal NSS/PSM certificate verification
for the configured endpoint hostname. Inner TLS 1.3 uses existing NSS and the origin's Go TLS
implementation; there is no new HTTP/TLS library. It validates the configured
SPKI pin and certificate lifetime before sending AUTH. The server must retain
the dedicated private key outside the CDN and public site.

Credentials, destination OPEN frames, payload, credit and half-close are inside
inner TLS. Session tickets and early data are disabled. The TLS exporter uses label
EXPORTER-NaiveFox-packet and context naivefox/https. Its material
authenticates HTTP operations, routing identity, sequence/cursor fields, exact
ciphertext and acknowledgements. This secret never appears in a URL, cookie,
header or log. The visible random session ID is routing metadata, not a bearer
credential; sessions are not bound to a CDN source address or forwarded IP.

The public HTML/resources still complete before transport startup. The encrypted
HELLO confirms the site snapshot, packet selection and origin-issued session ID.
No destination is opened before that confirmation. The CDN can observe sizes,
timing and request metadata, and can deny or delay delivery.

## Delivery and recovery

Two finite setup POST exchanges carry the TLS handshake and the existing AUTH/
HELLO cells. They are idempotent after a lost response. WSS and QUIC keep
their twenty startup pairs. Packet startup needs its own short passive
comparison; an earlier direct-carrier result is not acceptance for this path.

Sustained upload consists of finite requests of at most 64 KiB, with at most
eight unacknowledged blocks. Retries retain the original ciphertext and are
bounded by a 30-second operation deadline. Only a verified cumulative response
acknowledgement advances the client window. A verified response also confirms
storage of its individual block, even when an earlier block is missing. Such
blocks remain in the bounded window until the cumulative acknowledgement
advances, but their bodies are not retransmitted. Missing or unverified receipts
still require retries. The server restores ordering and does not deliver a
duplicate block to TLS or the mux.

Each streamed GET item has a 64-bit sequence, 32-bit length, 32-byte MAC and at
most 64 KiB of TLS bytes. HTTP chunk boundaries have no semantic meaning.
Verify the complete item before feeding NSS. On EOF inside an item, discard
that incomplete item and reconnect from the next unconsumed sequence using the
same TLS/mux state. A monotonically increasing authenticated download generation
replaces the previous request.

The server retains at most 2 MiB and 128 output blocks per carrier; a full replay
queue blocks its TLS writer. Authenticated acknowledgement releases retention.
An unavailable resume cursor terminates the session explicitly. A connection
break never silently restarts a target TCP connection. The upload byte bound is
512 KiB, the reorder window eight and the recent receipt window sixteen. There
are at most 32 packet sessions, sixteen provisional sessions, 64 aggregate HTTP
handlers and twelve active handlers per packet session. The configured server
max_sessions can further reduce packet admission.

The packet adapter uses the common NFOX mux, 32 logical streams and 1-MiB
delivery credit per stream. Credit still follows actual local socket delivery.
Five-second heartbeats keep a quiet download active. Provisional setup expires
after 30 seconds; authenticated sessions expire after 90 seconds without fresh
authenticated client progress. Replayed old requests do not extend that lease.
The client bounds an interrupted download recovery to 30 seconds.

## Deployment and verification

Set packet_tls on the matching Caddy server and distribute the inner SPKI pin
with the credentials. The pin is mandatory even for a direct HTTPS connection.
Use [CDN.md](CDN.md) when an intermediary terminates the outer HTTPS connection.

Local gates cover both frontends, corruption/pin rejection, finite upload
retries, chunk fragmentation, interrupted download, bounded queues and WSS/QUIC
regressions. Complete native Windows/Android verification and short
speed/latency/five-window screens before a long campaign.

The client always completes each POST with a known body length. An intermediary
may remove Content-Length or reframe the finite origin request as HTTP/1.1
chunked. The origin still reads a bounded body to successful EOF before
accepting it; size and read deadlines reject unfinished or oversized uploads.

Packet upload cell selection accounts for framing and includes an 8-KiB
capacity, so a 4-KiB application write does not need a second POST merely for
its headers. The packet downlink batches ready ACK, CREDIT and payload for a
fixed interval of at most 2 ms; a full cell bypasses that wait. This prevents
small control writes from consuming the idle TCP congestion window before the
reply. The cell length and body are written to inner TLS together. Direct
carrier scheduling remains unchanged.

The configured max_sessions is shared across direct and packet admission.
The public unauthenticated visitor slot is transferred to packet setup when
possible. Expiry and module shutdown release reservations; setup after shutdown
is rejected.
