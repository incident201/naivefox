# NaiveFox transport

NaiveFox supports one current transport and coordinated client/server updates.
Explicit URI schemes select its delivery adapter; there is no version
negotiation or automatic migration fallback.
CONNECT is permitted when justified by the native protocol architecture.

See [ARCHITECTURE.md](ARCHITECTURE.md) for carrier ownership, H2/H3 selection,
bounded queues, delivery credit and lifecycle. The separately maintained
naivefox-transport repository documents the matching wire contract in
docs/PROTOCOL.md and the public site in docs/SITE.md.

The default https:// selection uses finite H2 POSTs and a resumable H2 GET
inside pinned origin TLS; see [HTTPS.md](HTTPS.md). wss:// uses native WSS
after public-site and twenty-pair H2 startup.
H3 uses the same startup followed by a persistent HTTP/3 GET and at most eight
concurrent finite POSTs. Its sustained data remains on Neqo QUIC. A four-byte
length prefix delimits downstream cells in the GET response.

WSS and QUIC use NFOX cells, the naivefox HELLO identity, bounded
stream credit and the same DATA/CREDIT/FIN/RESET rules. There is one current
subprotocol identity, not a list of supported versions.

HTTP startup identifies each GET by its canonical path and ?seq=N query.
The server retains each logical result for bounded idempotent intermediary
retries, without replaying input to the mux. This does not resume active streams
after an outer carrier fails; sustained H3 uploads still reject duplicates.

Each carrier has an isolated cookie store: at most 32 cookies and 16 KiB of
retained name/value/path/domain data, with 4096 bytes per Set-Cookie and 32 KiB
per response. Cookies are scoped to the configured HTTPS origin and their path,
respect Secure/expiry and domain validation, and update from individual HTTP
and WS response headers. They never enter the shared browser cookie service.
The reserved session cookie retains one validated root-path value per carrier.

Content-Length is optional. Fixed cells must still contain exactly their
expected bytes. Public resources remain streamed, with MIME, EOF and snapshot
validation. Carrier/site responses to a configured trusted proxy include no-transform.
WSS and QUIC routes retain their ordinary cache headers.

HTTPS packet delivery is the default for direct and compatible CDN endpoints.
Its PIN is mandatory. WSS ignores and strips an optional final username tilde
suffix; QUIC retains literal username semantics. No old scheme aliases or
wire negotiation are supported.

CDN deployment compatibility remains under validation. See [CDN.md](CDN.md)
for provider requirements, separately from the HTTPS transport contract.
