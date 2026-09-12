# NaiveFox

NaiveFox is a lean SOCKS5 and HTTP CONNECT client built on Firefox networking.
Necko owns HTTP and connection pooling, NSS/PSM owns TLS and certificate checks,
and Neqo owns QUIC. The runtime runs in one process without a browser, DOM
execution, JavaScript engine or GUI.

There is one NaiveFox transport. Client and server must be updated together.
Classic NaiveProxy, old clients/servers, transport selectors, alternate wire
versions and compatibility profiles are not supported. CONNECT is a protocol
mechanism, not an architectural prohibition.

## Configure and run

Run **naivefox [CONFIG_PATH]**; the default is config.json.
The other supported CLI forms are --help and --version.

~~~json
{
  "listen": "socks://127.0.0.1:1080",
  "proxy": "quic://username:password@proxy.example:443"
}
~~~

- https:// selects strict H2 startup followed by native WSS over TCP.
- quic:// selects strict H3 for startup and sustained application data.
  H3 does not switch to WSS or fall back to TCP.
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

Unknown or duplicate fields are errors. There are no transport/profile flags,
classic padding fields, preamble modes or experimental runtime switches.

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
        }
    }
}
~~~

The public HTML selects the startup stylesheet, script and image resources.
The client validates their MIME types, completion and snapshot identity.
The application carrier then sends twenty bounded POST/GET pairs with useful
data, followed by the protocol-specific sustained carrier:

| Outer selection | Sustained carrier |
| --- | --- |
| H2 | Native Necko WebSocket over TLS/TCP |
| H3 | Persistent HTTP/3 GET and at most eight concurrent finite HTTP/3 POSTs |

Both adapters carry the same current NaiveFox cells, multiplexed streams,
delivery credits, offsets and FIN/RESET semantics. A carrier hosts up to 32
logical streams; additional carriers allow more connections. Each stream has
512 KiB (H2) or 1 MiB (H3) of receive credit, returned only after local delivery.
H3 POST completion preserves cell order even when QUIC requests arrive out of
order. The downstream GET uses a four-byte cell-length prefix.

The H3 downstream stream is shared within a carrier. Logical streams do not
each obtain an independent QUIC stream. The bounded upload pipeline adds HTTP
request work; assess throughput and responsiveness with the maintained tests.

## Build and verification

Use tools/build-product.sh with an absolute object directory and an output
package directory. Reuse the object directory for incremental builds. All
profiles, captures, reports and temporary exports belong below a dedicated
artifact directory.

The product build excludes the browser. The generated minimal-source export
must retain that dependency boundary. See [ARCHITECTURE.md](ARCHITECTURE.md),
[test/integration/README.md](test/integration/README.md).
