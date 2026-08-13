# NaiveFox local integration fixture

This fixture builds the pinned Naive-compatible Caddy, creates a scoped per-run
PKI and two isolated NSS profiles, starts deterministic HTTP and HTTPS targets,
and binds every listener to `127.0.0.1` on dynamically selected ports. Generated
binaries and run state live under the Firefox object directory in
`naivefox-fixture/`.

Run every reproducible local integration gate sequentially with:

```bash
./run-local-suite.sh
```

The existing command remains the H2 suite. Run strict H3 alone, or both
protocol suites sequentially, with:

```bash
./run-h3-suite.sh
./run-full-suite.sh
```

Strict H3 runners use an H3-only Caddy listener on UDP with no TCP listener on
the proxy port. They require `Outer protocol: h3`, so H2 fallback cannot satisfy
the workload. Both suites execute the same `naivefox` binary and share the
SOCKS, CONNECT, padding, and pump implementation.

Run the NaiveProxy-style config and simultaneous local-listener gates with:

```bash
./run-h2-config-tests.sh
./run-h3-config-tests.sh
./run-config-runtime-behavior-tests.sh
```

Each protocol run starts one NaiveFox process with both a SOCKS5 listener and
an HTTP CONNECT-only listener. It verifies small HTTP/HTTPS targets, rejection
of ordinary forward HTTP, 3 MiB download and 2 MiB upload integrity through
both frontends, and mixed concurrency. The H2 phase requires one pooled outer
TCP connection; the H3 phase runs against the UDP-only fixture. The behavior
runner verifies disabled, console, and file logging plus automatic persistent
profile creation.

The staging verifier passes an external root `./naivefox` launcher through
`NAIVEFOX_RUNTIME`. It exercises both no-argument adjacent `config.json` and
positional-config invocation from a package copied below `/tmp`, then rejects
source/object-directory process mappings.

`run-padded-tests.sh` and `run-config-tests.sh` accept an absolute staged
launcher through `NAIVEFOX_RUNTIME` plus its native runtime directory through
`NAIVEFOX_EXPECT_RUNTIME_DIR`. The current staging verifier uses the stronger
config-mode runner for both protocols with inherited loader and TLS-keylog
variables removed, and rejects any live source/objdir mapping.

The supplied real Caddy can be checked with the same config frontend without
placing credentials on a command line or in a committed file:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
NAIVEFOX_REAL_RUNTIME=/absolute/path/to/staged/naivefox \
./run-real-server-config-tests.sh h2

# Repeat with h3; the private generated config uses quic://.
./run-real-server-config-tests.sh h3
```

The runner keeps normal public certificate validation enabled, checks normal
HTTPS and direct/proxied integrity, and mixes SOCKS with HTTP CONNECT requests.
Private configs are deleted on both success and failure; retained summaries do
not contain the endpoint or credentials.

The final capture step requires the restricted `dumpcap` capabilities
documented below and in `../../CAPTURE.md`.

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
./run-h3-socks-tests.sh
```

The command starts a finite loopback SOCKS5 server, sends domain-name HTTP and
HTTPS requests through the selected Firefox/NSS H2 or Firefox/Neqo/NSS H3
CONNECT path, validates the HTTPS target with the scoped fixture CA, and
verifies clean shutdown after multiple sequential connections. Every curl
invocation uses `--socks5-hostname` and `--noproxy ''`; no application-side
target DNS lookup or certificate bypass is used.

Run the complete padded M8 interoperability suite with:

```bash
./run-padded-tests.sh
./run-h3-padded-tests.sh
```

This is the single local acceptance command for Naive legacy padding Variant 1.
It requires successful request/response `padding` negotiation for every
CONNECT, checks HTTP and HTTPS, verifies a deterministic 3 MiB download and 2
MiB upload by byte count and SHA-256, repeats sequential connections, and keeps
all credentials and generated payloads inside the isolated fixture run state.

Run the M9 robustness and lifecycle suite with:

```bash
./run-robustness-tests.sh
./run-h3-robustness-tests.sh
```

It verifies bounded-memory 32 MiB download/upload backpressure, integrity,
local/target/proxy close paths, timeout, ACL and authentication failure,
application half-close, and four simultaneous padded CONNECT streams. While
the concurrent streams are active, `ss` must show exactly one established TCP
connection for H2 or one NaiveFox-owned UDP socket for H3, proving reuse of
Firefox's outer H2 session or Neqo's outer QUIC connection.

Run the bounded protocol-selection policy tests with:

```bash
./run-auto-protocol-tests.sh
```

Auto mode performs one strict H3 attempt and at most one H2 retry. The test
uses an H2-only endpoint for the allowed establishment fallback, then places a
TCP decoy beside the H3-only UDP fixture and requires zero decoy accepts for H3
success, authentication rejection, and target failure. Raw and SOCKS entry
points use the same policy.

Run the M10 Firefox/NaiveFox wire comparison with:

```bash
./run-capture-comparison.sh
```

The host must provide `dumpcap`/`tshark` and the standard restricted dumpcap
capture capabilities described in `../../CAPTURE.md`. Raw captures, NSS TLS
keys, copied profiles, screenshots, and process logs are private temporary
data under the object directory and are deleted on success. Only sanitized
ClientHello, ALPN, SETTINGS, early-frame, stream-reuse, and header-name
metadata is retained.

Run the strict HTTP/3 equivalent with:

```bash
./run-h3-capture-comparison.sh
```

It uses an H3-only UDP fixture and performs independent decrypted and passive
captures for ordinary Firefox and strict-H3 NaiveFox. It asserts QUIC without
an established TCP fallback, semantic ClientHello and transport-parameter
parity, equal H3/QPACK settings, ordinary Firefox GET versus classic CONNECT,
two CONNECT streams on one QUIC connection, negotiated `padding` headers, and
the absence of synthetic marker headers. Raw captures, keys, profiles, bodies,
screenshots, and logs are deleted after credential-free aggregates are written.
WSL loopback capture uses `any`, then retains only the cooked transmit copy so
duplicate packet numbers cannot disturb stateful QUIC dissection. Detailed
results are in `../../H3-CAPTURE.md`.

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
