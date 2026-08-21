# NaiveFox

NaiveFox is a headless Naive-compatible proxy client built on Firefox's real
networking stack. Necko supplies HTTP/2, HTTP/3, CONNECT, pooling, and flow
control; NSS/PSM supplies TLS and certificate validation; Neqo supplies QUIC.
NaiveFox adds local proxy listeners, transport selection, CONNECT orchestration,
Naive padding, bounded stream pumping, configuration, and packaging.

```text
application -> SOCKS5 or HTTP CONNECT -> NaiveFox
            -> Firefox Necko/NSS/Neqo -> H2 or H3 CONNECT
            -> Caddy forwardproxy@naive -> destination
```

The server is an unmodified Naive-compatible Caddy build. NaiveFox does not
ship another HTTP/TLS stack and does not synthesize a Firefox fingerprint.

## Running the product

The staged Linux package has one launcher at its root. With no argument it
reads `./config.json`; one positional argument selects another config:

```bash
./naivefox
./naivefox /absolute/path/to/config.json
```

The staged Windows package provides `run-naivefox.cmd` beside its runtime and
accepts the same optional config path.

The supported config is a strict NaiveProxy-compatible subset:

```json
{
  "listen": [
    "socks://127.0.0.1:1080",
    "http://127.0.0.1:8080"
  ],
  "proxy": [
    "https://user:password@proxy.example:443",
    "https://user:password@proxy.example:443"
  ],
  "log": ""
}
```

- `listen` is one URI or a non-empty array. `socks://` serves SOCKS5 CONNECT;
  `http://` serves HTTP CONNECT only.
- `proxy` is one URI shared by all listeners, or an array whose length matches
  `listen`. Multi-hop comma-separated proxy chains are rejected.
- `https://` requires H2 over TLS/TCP. `quic://` requires H3 over QUIC/UDP.
  Strict modes never silently fall back.
- Credentials are percent-decoded and passed to Necko's proxy-auth path. They
  are never written to normal logs.
- Listener hosts must be numeric IPv4/IPv6; `localhost` maps to IPv4 loopback.
  An explicit nonzero port is required.
- `log` absent disables runtime logging, `""` logs to the console, and a path
  appends to a mode-`0600` file.

Local listeners do not authenticate clients. Binding `0.0.0.0`, `::`, or a LAN
address intentionally exposes the listener and requires an appropriate host
firewall or trusted-network policy. Ordinary forward-proxy HTTP requests return
405.

A SOCKS client should delegate destination DNS to the proxy:

```bash
curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

Normal config mode uses a persistent profile under `XDG_STATE_HOME`, then
`$HOME/.local/state`, or the explicit `NAIVEFOX_PROFILE`. If no persistent
location is usable, it creates a private temporary profile and removes it after
an orderly shutdown. Certificate verification is never disabled.

Developer-only modes provide focused diagnostics:

```bash
naivefox --profile /path/to/profile --runtime-smoke
naivefox --profile /path/to/profile --fetch https://example.com/
naivefox --profile /path/to/profile \
  --raw-tunnel-smoke https://proxy.example:443 target.example:80
naivefox --profile /path/to/profile \
  --socks-listen 127.0.0.1:1080 \
  --proxy https://proxy.example:443 --protocol h2
```

Developer CLI modes take proxy credentials from `NAIVEFOX_PROXY_USER` and
`NAIVEFOX_PROXY_PASS`. `--protocol h2` is the default; `h3` is strict H3;
`auto` performs one strict H3 attempt and permits one H2 retry only when H3
fails before a CONNECT response or tunnel transport exists.

## Supported behavior

- SOCKS5 CONNECT with IPv4, IPv6, and domain destinations; domain names remain
  unresolved in the upstream CONNECT authority.
- HTTP CONNECT local frontend, including bytes received with the request
  headers.
- Multiple listeners in one process, sharing one Gecko runtime and Necko
  connection manager.
- Strict H2 and strict H3 regular CONNECT, plus bounded developer Auto mode.
- Basic proxy authentication through Firefox's normal proxy-auth machinery.
- Naive `padding` request/response negotiation and legacy Variant 1 payload
  framing: eight padded records per direction, then raw bytes.
- Bounded async pumping, partial I/O, backpressure, half-close, connection reuse,
  and coordinated shutdown.
- Relocatable Linux x86-64 and Windows x86-64 packages from the minimized
  product graph.

NaiveFox deliberately does not implement SOCKS BIND or UDP ASSOCIATE,
CONNECT-UDP, MASQUE, WebTransport, transparent proxying, TUN/TAP, a GUI, or a
separate socket-process transport. See [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md).

## Architecture and wire compatibility

NaiveFox asks Firefox to create a normal proxied channel and takes over the
successful classic CONNECT as asynchronous byte streams. The empty raw-upgrade
token is restricted to connect-only channels and emits no synthetic `ALPN`,
`Upgrade`, or `Connection` marker. A validated sidecar adds only the intentional
Naive `padding` header to the actual proxy CONNECT request.

H2 uses Firefox's TLS/TCP connection and `Http2StreamTunnel`. H3 uses a strict
MASQUE-type proxy route to create a regular classic CONNECT through
`Http3StreamTunnel`; it does not use CONNECT-UDP or a standalone Neqo client.
Connection pooling remains owned by Necko in both modes.

The target's TLS session belongs to the application using the local proxy.
NaiveFox's outer TLS/QUIC session terminates at the upstream proxy.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for component, event-target, stream,
and fallback details. Downstream Firefox hooks are inventoried in
`UPSTREAM-PATCHES.md` in the full maintenance checkout.

## Building and testing

Development happens in the full Firefox source checkout, but the normal
product workflow builds only the minimized NaiveFox graph. It does not build a
Firefox browser:

```bash
MOZCONFIG=netwerk/naivefox/mozconfig-minimal ./mach build -j4
```

The Windows x86-64 cross-build uses the separate product mozconfig on the
`minimal` branch and Mozilla's clang-cl toolchain with the Visual Studio linker
and Windows SDK:

```bash
MOZCONFIG=netwerk/naivefox/mozconfig-windows-x86_64 \
NAIVEFOX_OBJDIR="$PWD/obj-naivefox-windows-x86_64" \
./mach build -j4
```

Run the reproducible local H2/H3/Auto/config/robustness gate with:

```bash
./netwerk/naivefox/test/integration/run-full-suite.sh
```

The fixture builds pinned Caddy and `forwardproxy@naive` inputs, binds only to
loopback, creates per-run credentials and PKI state, and trusts its CA only in
isolated NSS profiles. No real proxy account is required. Detailed focused and
real-deployment commands are in
[`test/integration/README.md`](test/integration/README.md).

Stage and verify the Linux package after a successful product build:

```bash
./netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64
./netwerk/naivefox/tools/verify-staged-runtime.sh naivefox-linux-x86_64
```

An ordinary Firefox build is not a merge or release gate. It is allowed only
for an explicitly requested same-base capture comparison; see
[`CAPTURE.md`](CAPTURE.md).

## Repository workflow

```text
Mozilla main -> main -> naivefox -> minimal -> generated minimal-source
```

- `main` is a clean fast-forward-only Mozilla mirror.
- `naivefox` is the complete full-source reference implementation.
- `minimal` contains the minimized build/runtime and export tooling.
- `minimal-source` is a generated standalone snapshot and is never hand-edited.

The three review gates and provenance rules are defined in `UPSTREAM.md` in the
full maintenance checkout. In particular, commit SHAs and test transcripts
belong in generated evidence, commits, and annotated tags rather than being
copied into active Markdown.

## Security and data handling

Never commit or retain proxy passwords, authorization headers, TLS keys, local
CA private keys, NSS profiles, packet captures, request payloads, or generated
fixture state. Integration state lives under the object directory with private
permissions and is removed after successful runs. Real-server tests receive
their endpoint and credentials through environment variables and keep only a
credential-free summary.

## References

- [Firefox source](https://github.com/mozilla-firefox/firefox)
- [Firefox Linux build documentation](https://firefox-source-docs.mozilla.org/setup/linux_build.html)
- [NaiveProxy](https://github.com/klzgrad/naiveproxy)
- [Naive padding specification](https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification)
- [Naive Caddy forward proxy](https://github.com/klzgrad/forwardproxy/tree/naive)
