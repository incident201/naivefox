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

## Running the desktop product

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
    "quic://proxy.example:443"
  ],
  "host-resolver-rules": "MAP proxy.example 127.0.0.1",
  "extra-headers": "X-NaiveFox-Test: enabled\r\n",
  "no-post-quantum": false,
  "log": ""
}
```

- `listen` is one URI or a non-empty array. `socks://` serves SOCKS5 CONNECT;
  without userinfo it uses the normal no-auth method. SOCKS credentials are
  optional, percent-decoded, and checked with RFC 1929 username/password
  authentication; `http://` serves HTTP CONNECT only and does not accept
  listener credentials.
- `proxy` is one URI shared by all listeners, or an array whose length matches
  `listen`. `https://` is strict H2 over TLS/TCP and `quic://` is strict H3
  over QUIC. Credentials are optional, percent-decoded, and passed to
  Necko's proxy-auth path. Port 443 is used when omitted.
- `https://` requires H2 over TLS/TCP. `quic://` requires H3 over QUIC/UDP.
  Strict modes never silently fall back.
- Listener hosts must be numeric IPv4/IPv6; `localhost` maps to IPv4 loopback.
  An explicit nonzero port is required.
- `host-resolver-rules` accepts exactly one `MAP logical-host physical-host`
  rule. A matching upstream socket uses the physical host while TLS SNI and
  certificate validation retain the logical hostname.
- `extra-headers` is a CRLF-separated list added only to the outer upstream
  CONNECT request. Malformed, duplicate, or service headers such as `Host`,
  `Padding`, and `Proxy-Authorization` are rejected.
- `no-post-quantum` is a boolean, defaulting to `false`; when true it disables
  Firefox Kyber/ML-KEM TLS and HTTP/3 key shares before connecting.
- `log` absent disables runtime logging, `""` logs to the console, and a path
  appends to a mode-`0600` file.

Binding `0.0.0.0`, `::`, or a LAN address intentionally exposes a listener.
Ordinary forward-proxy HTTP requests return 405. Comma-separated proxy chains,
upstream HTTP/SOCKS proxies, and direct mode are not supported.

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

## Building and testing

The minimized product workflow builds and stages the runtime below the object
directory:

```bash
./netwerk/naivefox/tools/build-product.sh linux \
  --objdir "$PWD/../obj-naivefox-linux"

NAIVEFOX_OBJDIR="$PWD/../obj-naivefox-linux" \
./netwerk/naivefox/tools/verify-staged-runtime.sh \
  package/naivefox-linux-x86_64

./netwerk/naivefox/test/integration/run-full-suite.sh
```

Use the same entrypoint with `windows --bootstrap` for the Windows package.
The Android command is shown in the next section. Android packaging can be
checked without a device with
`run-android-embedded-tests.sh --check-only`; network acceptance requires an
online ARM64 API-26+ device or emulator.

## Android embedded runtime

Android ARM64 is a native SDK/runtime target for embedding the same NaiveFox
core in another process. Build and stage it with the common product entrypoint:

```bash
./netwerk/naivefox/tools/build-product.sh android \
  --objdir "$PWD/../obj-naivefox-android-aarch64"

NAIVEFOX_OBJDIR="$PWD/../obj-naivefox-android-aarch64" \
./netwerk/naivefox/tools/verify-staged-android-runtime.sh \
  "$PWD/../obj-naivefox-android-aarch64/package/naivefox-android-aarch64"
```

The build remains `--enable-project=netwerk/naivefox` and cross-compiles for
`aarch64-linux-android`. The staged, relocatable package is:

```text
naivefox-android-aarch64/
  include/NaiveFoxAPI.h
  lib/arm64-v8a/
    libxul.so
    dependent native and NSS libraries selected from the build output
  manifest.json
```

The manifest records the target, minimum Android API, runtime path, exact file
hashes, Android system dependencies, and four exported `NaiveFox*` symbols.
The host must make `lib/arm64-v8a` and its dependent libraries visible in its
Android linker namespace. It then includes `NaiveFoxAPI.h`, starts the blocking
runner on one host worker thread, and stops it from another thread:

```c
int status = NaiveFoxRunEmbedded(config_json, writable_profile_dir,
                                 runtime_lib_dir);

/* From another host thread while the call above is running. */
NaiveFoxRequestStop();
```

`config_json` is the same UTF-8 JSON contract documented above, not a file
name. `writable_profile_dir` must be an existing writable directory, and
`runtime_lib_dir` is the absolute `lib/arm64-v8a` directory containing
`libxul.so`. `NaiveFoxVersion()` returns the product version. Return values are
the `NaiveFoxStatus` constants in the public header. The first successful Gecko
startup consumes the process-wide runtime; restarting it in the same process is
not supported.

This target contains no JNI or Java/Kotlin API, Gradle project, GeckoView,
Firefox Android application, Android manifest, UI, service, `VpnService`, TUN,
or tun2socks layer. Those are downstream integration concerns; the public
boundary here is the small native C ABI plus the local SOCKS5/HTTP CONNECT
listeners.

## Supported behavior

- SOCKS5 CONNECT with IPv4, IPv6, and domain destinations; domain names remain
  unresolved in the upstream CONNECT authority.
- HTTP CONNECT local frontend, including bytes received with the request
  headers.
- Multiple listeners in one process, sharing one Gecko runtime and Necko
  connection manager.
- Strict H2 and strict H3 regular CONNECT, plus bounded developer Auto mode.
- Basic proxy authentication through Firefox's normal proxy-auth machinery.
- RFC 1929 authentication for configured SOCKS listeners.
- Upstream host mapping, custom CONNECT headers, and the no-post-quantum TLS
  preference.
- Naive `padding` request/response negotiation and legacy Variant 1 payload
  framing: eight padded records per direction, then raw bytes.
- Bounded async pumping, partial I/O, backpressure, half-close, connection reuse,
  and coordinated shutdown.
- Relocatable Linux x86-64 and Windows x86-64 packages, plus an Android ARM64
  embedded native runtime, from the minimized product graph.

NaiveFox deliberately does not implement SOCKS BIND or UDP ASSOCIATE,
CONNECT-UDP, MASQUE, WebTransport, transparent proxying, TUN/TAP, a GUI, or a
separate socket-process transport. See [`KNOWN-ISSUES.md`](netwerk/naivefox/KNOWN-ISSUES.md).

## Architecture and wire compatibility

NaiveFox asks Firefox to create a normal proxied channel and takes over the
successful classic CONNECT as asynchronous byte streams. The empty raw-upgrade
token is restricted to connect-only channels and emits no synthetic `ALPN`,
`Upgrade`, or `Connection` marker. A validated sidecar adds the intentional
Naive `padding` header and configured extra headers to the actual proxy CONNECT
request.

H2 uses Firefox's TLS/TCP connection and `Http2StreamTunnel`. H3 uses a strict
MASQUE-type proxy route to create a regular classic CONNECT through
`Http3StreamTunnel`; it does not use CONNECT-UDP or a standalone Neqo client.
Connection pooling remains owned by Necko in both modes.

The target's TLS session belongs to the application using the local proxy.
NaiveFox's outer TLS/QUIC session terminates at the upstream proxy.

See [`ARCHITECTURE.md`](netwerk/naivefox/ARCHITECTURE.md) for component, event-target, stream,
and fallback details.

## References

- [Firefox source](https://github.com/mozilla-firefox/firefox)
- [Firefox Linux build documentation](https://firefox-source-docs.mozilla.org/setup/linux_build.html)
- [NaiveProxy](https://github.com/klzgrad/naiveproxy)
- [Naive padding specification](https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification)
- [Naive Caddy forward proxy](https://github.com/klzgrad/forwardproxy/tree/naive)
