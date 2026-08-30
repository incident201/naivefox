# NaiveFox contributor instructions

Read the repository-root `AGENTS.md` first. These rules apply to
`netwerk/naivefox/` and to the few downstream Firefox files listed in
[`UPSTREAM-PATCHES.md`](UPSTREAM-PATCHES.md).

Before changing NaiveFox, read:

- [`README.md`](README.md) for product behavior;
- [`ARCHITECTURE.md`](ARCHITECTURE.md) for ownership and threading constraints;
- [`UPSTREAM.md`](UPSTREAM.md) for branch and refresh rules;
- [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md) for active limitations;
- [`test/integration/README.md`](test/integration/README.md) when changing runtime behavior.
- [`NO-CONNECT.md`](NO-CONNECT.md) when changing transport selection or the
  native application carrier.

## Repository discipline

- All NaiveFox networking, product, build-graph, packaging, shim, and export
  work belongs on `naivefox-full-source`.
- `firefox-upstream` is a fast-forward-only mirror of Mozilla Firefox.
- `naivefox-minimal-source` is generated; never edit its product tree or merge
  it back. Its `.github/workflows/` control-plane overlay is intentionally
  maintained directly so release automation can evolve independently.
- Preserve unrelated work in a dirty tree. Do not rewrite public history or push
  without authorization.
- Keep project code under `netwerk/naivefox/`. Modify an existing Firefox file
  only when project-only code cannot use an existing API.
- Every downstream Firefox change must be narrow, regression-tested, and added
  to `UPSTREAM-PATCHES.md` with a stable `NF-UPSTREAM-XXX` identifier.

## Architecture invariants

- Necko owns HTTP/2, HTTP/3, CONNECT, pooling, and flow control.
- NSS/PSM owns TLS and certificate validation; Neqo owns QUIC.
- Do not add another HTTP, TLS, or QUIC stack or manually generate protocol
  frames to imitate Firefox.
- A raw CONNECT must not emit a synthetic `ALPN`, `Upgrade`, or `Connection`
  marker. The Naive `padding` header is the intentional compatibility signal.
- SOCKS domain targets remain hostnames in classic CONNECT authority or
  no-connect OPEN frames; do not resolve them locally.
- Strict H2 and H3 must fail closed. Auto may retry H2 only after an H3
  establishment failure before CONNECT response or tunnel creation.
- The current product intentionally runs networking in one process. Do not
  enable the socket process without IPC-capable tunnel-stream takeover.
- Cross-thread-owned objects require thread-safe refcounting. Keep state
  mutation on its owning event target even when lifetime is thread-safe.
- Preserve bounded buffering, partial-I/O handling, async backpressure,
  half-close behavior, and shutdown propagation. Never assume socket reads map
  to H2/H3 frames or Naive records.
- Keep credentials, authorization headers, payloads, TLS secrets, profiles,
  captures, generated Caddy state, and logs out of Git and ordinary output.

## Configuration and protocol scope

The supported local frontends are SOCKS5 CONNECT and HTTP CONNECT. SOCKS BIND,
UDP ASSOCIATE, ordinary forward HTTP, CONNECT-UDP, MASQUE, WebTransport, TUN,
and GUI work are outside the current product.

Config parsing is strict. Preserve the documented string/array listener and
proxy mapping, percent-decoded upstream credentials, numeric IPv4/IPv6 binds,
and `https://` = H2 / `quic://` = H3 selection. SOCKS listeners may require
RFC 1929 username/password authentication when credentials are configured;
HTTP CONNECT listeners do not accept listener credentials. Wildcard or LAN
binding must remain an explicit operator choice.

Naive payload compatibility is legacy Variant 1: eight framed records per
direction followed by raw bytes. The streaming decoder must accept every
header/payload/padding split, coalesced records, and raw bytes following the
last framed record. Production padding must not use a deterministic RNG.

That padding contract applies to `classic`, the default transport. The opt-in
`no-connect` carrier uses bounded NFC1 application cells over Necko's ordinary
GET/POST channels and requires the separately maintained Caddy module. Preserve
the documented profile, ordered OPENs and cell sequences, credit only after
local delivery, HTTP completion checks, and per-stream half-close. Do not import
the experimental browser worker, DOM, JavaScript engine, or WSS bridge into the
lean runtime. Transport selection in JSON and the desktop CLI must agree.

## Build and test policy

Use Mozilla's `mach`, managed toolchains, source style, and ownership types.
Do not introduce CMake or a replacement build system. Use `searchfox-cli` for
upstream symbol research and narrow local `rg` searches for project code.

Before implementing or running a new residual experiment, search the current
documentation, retained artifact metadata, and the complete Git history for
both the exact proposal and causally equivalent mechanisms. Record the overlap
in the experiment notes. Do not repeat a closed experiment under a new name;
proceed only when the new proposal has a distinct, previously unmeasured causal
premise.

The normal two-stage cycle never builds the Firefox browser:

1. `upstream/main -> firefox-upstream -> naivefox-full-source`: source,
   inventory, and conflict review, followed by the minimized product build.
2. `naivefox-full-source -> naivefox-minimal-source`: export, isolated build,
   and acceptance checks.

An ordinary Firefox build is allowed only for an explicitly requested,
same-base capture comparison. See [`CAPTURE.md`](CAPTURE.md).

For changes on `naivefox-full-source`, use the product configuration and a full graph build
when build files or closure may have changed:

```bash
netwerk/naivefox/tools/build-product.sh linux \
  --objdir /absolute/path/to/obj-naivefox-linux
```

For focused C++ iteration, use the narrowest valid target, then finish with the
applicable full product gate. Run project gtests and the integration suites
described in [`test/integration/README.md`](test/integration/README.md). Changes
to downstream Necko/Neqo hooks also require their focused xpcshell regressions.
Do not run formatters over unrelated Firefox files.

Generated fixture state belongs below the object directory. The fixture must
remain loopback-only, use pinned Caddy and `forwardproxy@naive` inputs, install
trust only into isolated NSS profiles, never call `caddy trust`, never disable
certificate checks, and stop only processes that it started.

## Documentation

Keep active documentation short and durable:

- behavior and operator examples in `README.md`;
- design invariants in `ARCHITECTURE.md`;
- branch/process rules in `UPSTREAM.md`;
- downstream Firefox inventory in `UPSTREAM-PATCHES.md`;
- unresolved limitations only in `KNOWN-ISSUES.md`.

Do not copy mutable commit SHAs, dated status reports, command transcripts, or
one-off test results into multiple Markdown files. Release provenance belongs
in generated machine-readable evidence, `UPSTREAM-BASE`, commits, and annotated
tags. Historical reports remain available in Git history.
