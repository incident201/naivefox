# NaiveFox contributor instructions

Read the repository-root AGENTS.md first, then README.md, ARCHITECTURE.md,
UPSTREAM.md, KNOWN-ISSUES.md and TRANSPORT.md. For runtime work also read
test/integration/README.md.

## Repository discipline

- Product, networking, packaging, build graph, shims and export changes belong
  on naivefox-full-source.
- firefox-upstream is a fast-forward-only Mozilla mirror.
- naivefox-minimal-source is generated. Do not edit its product tree or merge it
  back. Its workflow control-plane overlay may be maintained independently.
- Preserve unrelated changes. Do not push or rewrite public history without
  authorization.
- Keep project code under netwerk/naivefox. Existing Firefox files may change
  only when a project-only implementation cannot use the available APIs.
- Inventory every downstream Firefox change in UPSTREAM-PATCHES.md with a
  stable NF-UPSTREAM identifier and focused regression coverage.
- Credentials, payloads, TLS secrets, generated profiles/captures, Caddy state
  and reports stay outside Git and ordinary output.

## Architecture

There is one current NaiveFox transport, with coordinated client/server updates.
Classic NaiveProxy, transport selectors, legacy profiles, alternate versions,
migration fallbacks and experimental production modes are out of scope.
CONNECT absence is not an architectural requirement.

Necko owns HTTP, native WebSocket and pooling. NSS/PSM owns TLS and certificate
checks. Neqo owns QUIC. Do not add another HTTP/TLS/QUIC stack, generate fake
Firefox wire frames, or import browser execution, DOM loaders or JavaScript.

Strict H2 uses native HTTP startup and WSS/TCP. Strict H3 keeps all startup and
sustained carrier traffic on HTTP/3; TCP fallback is forbidden. The current H3
adapter uses a persistent downstream GET and at most eight finite upload POSTs.
Keep application sequencing, bounded reorder retention and delivery credit
consistent between client and server.

SOCKS domain destinations remain hostnames in OPEN and are resolved remotely.
Preserve bounded buffering, partial I/O, async backpressure, independent
half-close, reset and shutdown. Do not equate socket reads with HTTP messages or
application frames. Cross-thread ownership requires thread-safe refcounting;
state mutation stays on its owning event target.

The product intentionally runs networking in one process. Enabling additional
processes needs a supported lifecycle design, not just preference changes.

## Product behavior

The local frontends are SOCKS5 CONNECT and HTTP CONNECT. Configuration is strict:
preserve string/array listener and upstream mapping, URI credential decoding,
numeric IPv4/IPv6 binds, SOCKS username/password auth and explicit LAN binding.
No Auto protocol or classic configuration is supported.

Keep the complete public HTML-selected resource bootstrap, snapshot identity,
twenty ordered startup pairs, cache inhibition and streamed public-body
consumption. Site size and resource count are operator choices. Keep one current
cell contract, 32 streams per carrier, additional carriers as needed and bounded
per-stream credit. Offsets wrap modulo 2^32 without a 4-GiB transfer cap.
Return CREDIT only after local delivery.

The Caddy module owns its authentication and destination policy. It does not
load or delegate to a classic forward-proxy implementation.

## Build and testing

Use mach and managed toolchains, not a replacement build system. Use
searchfox-cli for upstream symbol research and narrow local searches for
downstream changes.

Before proposing or running a residual experiment, search current documentation,
retained artifact metadata and complete Git history for the exact proposal and
causally equivalent mechanisms. Record overlap and the distinct causal premise;
do not repeat a closed experiment under a new name.

Reuse incremental object directories. Put temporary packages, profiles, test
outputs, captures and minimal exports under a dedicated artifact catalog.
When build files or dependencies change, run the full product graph through
tools/build-product.sh with an absolute objdir. Do not build the Firefox browser
unless explicitly requested for a same-base reference comparison.

The source cycle is upstream/main -> firefox-upstream -> naivefox-full-source,
followed by minimized product build and review. The export cycle is
naivefox-full-source -> generated naivefox-minimal-source, with isolated build
and lean closure validation. Run applicable focused gtests/integration tests.
Downstream networking hooks need focused lifecycle regressions.

Complete runtime tests on Linux, Windows and Android and the five-window
comparisons before exporting minimal source. A successful cross-build is not a
runtime test. Minimal-source export is the final step after those checks.

Correctness, native H3 proof and short performance screens precede long capture
campaigns. Keep the established p1-16, p17-32, p1-32, 250ms and Whole analysis
method and health gates. Minor drift is acceptable; material degradation is not.
An old binary retained as comparison evidence is not a legacy product mode.

Fixtures use pinned Caddy/module inputs, isolated loopback network namespaces and
NSS profiles. Never run caddy trust or disable certificate validation. Stop only
processes created by the fixture.

## Documentation

Keep active docs short and durable. Behavior goes in README.md, design in
ARCHITECTURE.md, process in UPSTREAM.md, downstream inventory in
UPSTREAM-PATCHES.md and unresolved limitations in KNOWN-ISSUES.md.
Do not duplicate dated reports, command transcripts or mutable commit SHAs.
Keep release provenance in generated evidence, UPSTREAM-BASE, commits and tags.
