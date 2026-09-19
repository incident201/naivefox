# NaiveFox

NaiveFox is a lean SOCKS5 and HTTP CONNECT client built on Firefox networking.
Necko owns HTTP and connection pooling, NSS/PSM owns TLS and certificate checks,
and Neqo owns QUIC. The runtime runs in one process without a browser, DOM
execution, JavaScript engine or GUI.

There is one NaiveFox transport. Client and server must be updated together.
Only the current matching client/server pair is supported. Explicit URI schemes select delivery; there are no alternate wire versions,
compatibility profiles or automatic fallbacks. CONNECT is a protocol
mechanism, not an architectural prohibition.

## Configure and run

Run **naivefox [CONFIG_PATH]**; the default is config.json.
The other supported CLI forms are --help and --version.

~~~json
{
  "listen": "socks://127.0.0.1:1080",
  "proxy": "https://username~PIN:password@proxy.example:443"
}
~~~

Replace PIN with the independently obtained 64-hex SHA-256 SPKI fingerprint
of the origin's inner-TLS certificate.

- https:// is the default packet transport: finite H2 POST uploads, resumable
  streaming GET downloads and inner TLS 1.3 to a pinned origin. It works directly
  or through a compatible CDN; the PIN is mandatory in both cases.
- wss:// explicitly selects native WebSocket after H2 startup. An optional
  username~PIN suffix is stripped and ignored, without PIN validation.
- quic:// selects strict H3 for startup and sustained application data.
  H3 does not switch to WSS or fall back to TCP.
- See [HTTPS.md](HTTPS.md) for PIN configuration, security and recovery.
- listen accepts a string or array of numeric IPv4/IPv6 endpoints.
  Use http://127.0.0.1:8080 for a local HTTP CONNECT listener.
- proxy accepts a string or array. One upstream is shared; otherwise upstream
  and listener arrays must have the same length and map by index.
- Credentials in proxy URIs are percent decoded. They authenticate the NaiveFox
  session. Domain destinations remain hostnames until the server resolves them.
- SOCKS listener credentials enable username/password authentication. HTTP
  listener credentials are rejected.
- host-resolver-rules accepts one MAP logical-host numeric-address rule for
  the outer server. It preserves the logical TLS identity and destination DNS.
- no-post-quantum is an optional boolean; the default preserves Firefox TLS.
- max-connections is an optional bounded connection count for finite runs.
- Logging is disabled when log is omitted. An empty string enables console
  logging; a nonempty string names a log file.

Unknown or duplicate fields are errors. The configuration selects H2 or H3
for the same application protocol and URI-selected delivery adapters; it has no
old wire-version modes.

By default each CLI run creates and removes an isolated temporary NSS profile.
NAIVEFOX_PROFILE explicitly selects a persistent profile.
SSL_CERT_FILE adds the supplied CA certificates to the isolated runtime trust
store without disabling certificate validation.

## Server and application carrier

Use the separate Caddy module in naivefox-transport, with an HTTPS application
directory and one set of credentials/access rules:

~~~caddyfile
proxy.example {
    route {
        naivefox_transport {
            application_root /srv/naivefox-site
            basic_auth username password
            packet_tls /etc/naivefox/inner.crt /etc/naivefox/inner.key
        }
    }
}
~~~

The public HTML selects the startup stylesheet, script and image resources.
The client validates their MIME types, completion and snapshot identity.
The default HTTPS adapter uses two finite inner-TLS/AUTH setup exchanges.
WSS and QUIC use twenty bounded POST/GET pairs with useful data.
Each selection continues with its sustained carrier:

| URI selection | Sustained carrier |
| --- | --- |
| https:// (default) | Resumable H2 GET and finite H2 POSTs, with pinned inner TLS |
| wss:// | Native Necko WebSocket over TLS/TCP |
| quic:// | Persistent HTTP/3 GET and at most eight concurrent finite HTTP/3 POSTs |

All adapters carry the same current NaiveFox cells, multiplexed streams,
delivery credits, offsets and FIN/RESET semantics. A carrier hosts up to 32
logical streams; additional carriers allow more connections. Each stream has
512 KiB (WSS) or 1 MiB (HTTPS and QUIC) of receive credit, returned only
after local delivery.
H3 POST completion preserves cell order even when QUIC requests arrive out of
order. The downstream GET uses a four-byte cell-length prefix.

The H3 downstream stream is shared within a carrier. Logical streams do not
each obtain an independent QUIC stream. The bounded upload pipeline adds HTTP
request work; assess throughput and responsiveness with the maintained tests.

## Direct and CDN deployment

HTTPS packet delivery is the default regardless of whether a CDN is used.
The inner origin pin and packet_tls are required for this delivery. Generate
the dedicated identity as described in the server's docs/HTTPS.md.

CDN compatibility is a separate deployment concern. Provider-specific
production acceptance remains incomplete; short live and local checks are not
a support guarantee. See [CDN.md](CDN.md) and [TRANSPORT.md](TRANSPORT.md).

## Build and verification

Use tools/build-product.sh with an absolute object directory and an output
package directory. Reuse the object directory for incremental builds. All
profiles, captures, reports and temporary exports belong below a dedicated
artifact directory.

The product build excludes the browser. The generated minimal-source export
must retain that dependency boundary. See [ARCHITECTURE.md](ARCHITECTURE.md),
[test/integration/README.md](test/integration/README.md).
