# NaiveFox local integration fixture

This fixture builds the pinned Naive-compatible Caddy, creates a scoped per-run
PKI and two isolated NSS profiles, starts deterministic HTTP and HTTPS targets,
and binds every listener to `127.0.0.1` on dynamically selected ports. Generated
binaries and run state live under the Firefox object directory in
`naivefox-fixture/`.

Run the complete M0.4 control suite from this directory:

```bash
./run-control-tests.sh
```

After building NaiveFox, run the M2.2 scoped NSS/Necko checks with:

```bash
./run-necko-tests.sh
```

This proves that the untrusted NSS profile rejects the proxy, the trusted
profile accepts it through a real Necko HTTPS channel, and hostname validation
still rejects the same certificate when the channel uses an IP address.

Run the M5 local SOCKS-to-Necko tunnel checks with:

```bash
./run-socks-tests.sh
```

The command starts a finite loopback SOCKS5 server, sends domain-name HTTP and
HTTPS requests through the Firefox/NSS HTTP/2 CONNECT path, validates the HTTPS
target with the scoped fixture CA, and verifies clean shutdown after multiple
sequential connections. Every curl invocation uses `--socks5-hostname` and
`--noproxy ''`; no application-side target DNS lookup or certificate bypass is
used.

Run the complete padded M8 interoperability suite with:

```bash
./run-padded-tests.sh
```

This is the single local acceptance command for Naive legacy padding Variant 1.
It requires successful request/response `padding` negotiation for every
CONNECT, checks HTTP and HTTPS, verifies a deterministic 3 MiB download and 2
MiB upload by byte count and SHA-256, repeats sequential connections, and keeps
all credentials and generated payloads inside the isolated fixture run state.

Run the M9 robustness and lifecycle suite with:

```bash
./run-robustness-tests.sh
```

It verifies bounded-memory 32 MiB download/upload backpressure, integrity,
local/target/proxy close paths, timeout, ACL and authentication failure,
application half-close, and four simultaneous padded CONNECT streams. While
the concurrent streams are active, `ss` must show exactly one established TCP
connection to the proxy, proving reuse of Firefox's outer H2 session.

The first run downloads the SHA-256-pinned Go toolchain when no matching Go is
already available, installs the pinned xcaddy, and builds Caddy with the exact
forwardproxy commit. Later runs reuse the validated tools. The suite verifies
the module and adapted config, loopback TLS listener with H1/H2 only, Basic Auth
success and rejection paths, ACL/port denial, ordinary certificate validation,
both NSS profile contents, all deterministic target behaviors, and cleanup.

For an interactive fixture lifecycle:

```bash
./start.sh
source "$(cat ../../../../obj-*/naivefox-fixture/active-run)/fixture.env"
./stop.sh
```

`start.sh` prints the exact generated environment-file path, so using that path
directly is preferable when more than one object directory exists. The file is
mode `0600` because it contains random per-run credentials. `stop.sh` removes
the run directory, including credentials, private keys, and profiles, and keeps
only sanitized diagnostics at `naivefox-fixture/last-diagnostics.txt`.

The control client uses `curl --proxy-cacert` for the HTTPS proxy and additionally
uses `--cacert` for the HTTPS target. The fixture never calls `caddy trust`, never
changes system or normal Firefox trust, and never disables certificate checks.

Pinned inputs are declared in `versions.env` with fully specified assignments:

- Caddy `v2.11.2`
- xcaddy `v0.4.6`
- module `github.com/caddyserver/forwardproxy` at fully pinned pseudo-version
  `v0.0.0-20250118002110-d62c80d3dd2c`, replaced by
  `github.com/klzgrad/forwardproxy` commit
  `d62c80d3dd2c706b6b87579844d2397bddd18317`
- Go `go1.25.12` for Linux x86-64, archive SHA-256
  `234828b7a89e0e303d2556310ee549fbcf253d28de937bac3da13d6294262ac1`
