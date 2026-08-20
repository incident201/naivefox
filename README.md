# NaiveFox

NaiveFox is a small Firefox/Necko-based NaiveProxy client. It provides local
SOCKS5 and HTTP `CONNECT` listeners and sends the resulting tunnels through
Firefox's native HTTP/2 or HTTP/3 (Neqo/QUIC) stack. Padding is negotiated with
the pinned Caddy `forwardproxy` server; NaiveFox does not implement a separate
HTTP/2, HTTP/3, QUIC, or TLS stack.

## Configuration

The normal user mode reads `./config.json`, or a path supplied as the first
argument. Start with the included `config.example.json`. A configuration
contains one or more local listeners and either one shared upstream proxy or
one proxy per listener:

```json
{
  "listen": [
    "socks://127.0.0.1:1080",
    "http://127.0.0.1:8080"
  ],
  "proxy": "https://user:password@proxy.example:443",
  "log": ""
}
```

Use `https://` for strict HTTP/2 or `quic://` for strict HTTP/3. The proxy
credentials are read from the URI, percent-decoded, and never written to the
runtime log. `listen` and `proxy` may each be a string or an array. A single
proxy is shared by all listeners; a proxy array must have the same length as
the listener array and is matched by index. HTTP listeners implement only
`CONNECT`; ordinary forward-proxy `GET` and `POST` requests are rejected.

The writable profile is created under `$XDG_STATE_HOME/naivefox/profile`, or
`$HOME/.local/state/naivefox/profile`. Set `NAIVEFOX_PROFILE` to override it.
When no home/state directory is available, NaiveFox uses a private temporary
profile and removes it during clean shutdown.

## Running

On a staged package, launch the product entry point beside `config.json`:

```sh
./naivefox
./naivefox /path/to/config.json
```

The package launcher sets only its runtime library path. It does not require a
Firefox checkout, an objdir, developer-only protocol flags, or proxy
credentials in the environment.

## Building from source

Install the normal Firefox build prerequisites, then run the bootstrap command
once if this machine has not been prepared for Mozilla builds:

```sh
./mach --no-interactive bootstrap \
  --application-choice "Firefox for Desktop" --no-system-changes
```

On Linux, build the lean NaiveFox product into an objdir outside the source
tree. Generate the product headers first, then build only the product binaries
and runtime metadata; a full Firefox browser build is not required:

```sh
export NAIVEFOX_OBJDIR="$PWD/../naivefox-objdir"
export MOZCONFIG="$PWD/netwerk/naivefox/mozconfig-minimal"
./mach configure
./mach build export
./mach build -j4 binaries
./mach build misc
```

The resulting executable is in `$NAIVEFOX_OBJDIR/dist/bin/naivefox`. For a
relocatable package, use `netwerk/naivefox/tools/stage-runtime.sh` and verify
it with `verify-staged-runtime.sh` before distributing it.

For Windows x86-64, run the same product-only `mach configure`, `export`,
`binaries`, and `misc` flow from MozillaBuild with `MOZCONFIG` set to
`netwerk/naivefox/mozconfig-windows-x86_64` and `NAIVEFOX_OBJDIR` set to an
external Windows objdir. Use PowerShell `$env:NAME = "value"` syntax rather
than the POSIX `export` commands above.

## Scope

NaiveFox intentionally supports TCP SOCKS5 `CONNECT` and HTTP `CONNECT` only.
It does not provide UDP ASSOCIATE, CONNECT-UDP/MASQUE, a full HTTP forward
proxy, proxy chains, TUN/TAP, or a GUI. A generated source snapshot contains a
curated technical document set under `docs/`, while full development history,
internal roadmaps, agent instructions, and export tooling remain only on the
full-tree `minimal` branch.
