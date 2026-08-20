# NaiveFox

NaiveFox is a small Firefox/Necko-based NaiveProxy client. It provides local
SOCKS5 and HTTP `CONNECT` listeners and sends the resulting tunnels through
Firefox's native HTTP/2 or HTTP/3 (Neqo/QUIC) stack. Padding is negotiated with
the pinned Caddy `forwardproxy` server; NaiveFox does not implement a separate
HTTP/2, HTTP/3, QUIC, or TLS stack.

## Configuration

The normal user mode reads `./config.json`, or a path supplied as the first
argument. A configuration contains one upstream proxy and one or more local
listeners:

```json
{
  "listen": [
    "socks://127.0.0.1:1080",
    "http://127.0.0.1:8080"
  ],
  "proxy": "https://user:password@example.com:443",
  "log": ""
}
```

Use `https://` for strict HTTP/2 or `quic://` for strict HTTP/3. The proxy
credentials are read from the URI, percent-decoded, and never written to the
runtime log. `listen` may also be a single string. HTTP listeners implement
only `CONNECT`; ordinary forward-proxy `GET` and `POST` requests are rejected.

The writable profile is created under `$XDG_STATE_HOME/naivefox/profile`, or
`$HOME/.local/state/naivefox/profile`. Set `NAIVEFOX_PROFILE` to override it.
When no home/state directory is available, NaiveFox uses a private temporary
profile and removes it during clean shutdown.

## Running

On a staged package, launch the wrapper or binary beside `config.json`:

```sh
./run-naivefox
# or
./naivefox /path/to/config.json
```

The package launcher sets only its runtime library path. It does not require a
Firefox checkout, an objdir, developer-only protocol flags, or proxy
credentials in the environment.

## Scope

NaiveFox intentionally supports TCP SOCKS5 `CONNECT` and HTTP `CONNECT` only.
It does not provide UDP ASSOCIATE, CONNECT-UDP/MASQUE, a full HTTP forward
proxy, proxy chains, TUN/TAP, or a GUI. The full Firefox-based reference tree,
upstream patch inventory, test reports, and reproducibility metadata are kept
under `docs/naivefox/` in generated source exports.

