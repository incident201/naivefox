# Implicit fronting-page contract

This file is the canonical operator-facing contract for the site served by the
upstream origin when `preamble` is omitted. It separates requirements needed
for a successful connection from the particular fixture profile whose passive
residuals were measured.

Changing an implicit policy, path, parser topology, accepted HTML attributes,
resource count, byte budget, cache policy, compression assumption, or fixture
response shape is incomplete until this file, the linked README and known
issue, the config/integration tests, and the four-row matrix in
[`CAPTURE.md`](CAPTURE.md#current-implicit-default-matrix) are updated in the
same logical change.

## Current implicit policies

| Outer protocol | Local listener | Policy | Origin requirement |
| --- | --- | --- | --- |
| H2 | SOCKS5 only | `document-first-buffer-task-overlap` | Non-empty successful document at `/`, at most 64 KiB |
| H2 | HTTP CONNECT or mixed | `document-first-buffer-overlap` | Non-empty successful document at `/`, at most 64 KiB |
| H3 | SOCKS5, HTTP CONNECT, or mixed | `tree-native-parser-resource-committed-overlap` | Exact six-resource page below, at most 384 KiB aggregate |

H2 does not discover or request resources referenced by the document. It
admits CONNECT after the first complete body buffer (on the next main-thread
task for SOCKS-only ingress) and still requires a normal successful root
drain. The HTML may therefore be the same page used by H3, but its CSS, script,
and images do not affect the H2 outer preamble.

The H2 64-KiB value is a functional safety ceiling, not a promise of identical
passive residuals for every document size. The canonical four-row dashboard
uses a 494-byte fixture root including its completion token. One-block endpoint
artifact `46354f735ce3d8a6` exercised the exact same page padded to 65,536 bytes:
packets 17--32 fell to `0.31378/0.14497` for SOCKS/HTTP, but 250 ms rose to
`0.15433/0.16218` and Whole rose to `0.33178/0.33533`. Thus padding the root is
not an optimization and was not promoted. Sites requiring the published
dashboard rather than only functional admission should keep the H2 root close
to the measured small-document profile; referenced resource sizes remain
irrelevant to the H2 preamble itself.

H3 fetches the root and all six resources through the selected outer route.
The 384-KiB limit covers the root and resource body bytes delivered by Necko
to the preamble listener; it is a safety ceiling, not a target. A mismatch or
failed/oversized response fails strict H3 closed. It does not silently switch
to a document-only request or fall back to H2.

## Exact H3 HTML topology

The supported and measured minimal form is:

```html
<!doctype html>
<html>
  <head>
    <link rel="stylesheet" href="/assets/site.css">
    <script defer src="/assets/site.js"></script>
  </head>
  <body>
    <img src="/assets/image-1.svg" alt="">
    <img src="/assets/image-2.svg" alt="">
    <img src="/assets/image-3.svg" alt="">
    <img src="/assets/image-4.svg" alt="">
  </body>
</html>
```

The lean parser requires:

- ASCII-only root HTML in the current implementation, including ordinary text;
  UTF-8 non-ASCII characters such as typographic punctuation are rejected before
  resource discovery (use ASCII text or HTML character references);
- exactly one stylesheet, one classic deferred script from the document head,
  and four images;
- six pairwise-distinct resource URLs on the same scheme, host, and port as
  the root;
- the stylesheet in the minimal `rel=stylesheet`/`href` form;
- the script with `src` and `defer`, without `async`, `type=module`, or another
  `type` value;
- simple image `src` URLs, without `srcset`, `sizes`, `picture`, preload, CORS,
  integrity, referrer-policy, media, nonce, or fetch-priority variations;
- no additional parser-discovered stylesheet, external script, image, or
  preload and no meta CSP or meta referrer policy.

A same-origin `<base>` can be parsed by the implementation, but it is outside
the measured minimal profile and should be avoided. Ordinary text and
non-resource markup are harmless. CSS and JavaScript bodies are downloaded as
cover resources; this lean preamble does not execute the script or construct a
rendered DOM, so secondary loads mentioned only inside those bodies do not
replace the six HTML-discovered requests.

Serve the root and all resources as deterministic successful `2xx` responses
which contain body data and complete normally. Use the appropriate content
types, avoid redirects and authentication challenges, and keep the aggregate
comfortably below 384 KiB. Body compression changes the delivered and wire
shape and has not been admitted as equivalent to the current fixture. Native
resource caching is enabled, but the canonical captures use fresh cold
profiles and do not rely on a warm cache hit.

## Profile behind the canonical default matrix

The canonical Caddy fixture rewrites `/` to its dense fronting page. Its outer
resource bodies are:

| Resource | Fixture body size |
| --- | ---: |
| Dense H3 root HTML | 620 bytes with the measurement completion token |
| Stylesheet | 12 KiB |
| Classic deferred script | 24 KiB |
| Image descriptors 1--3 | 8 KiB each |
| Image descriptor 4 | 34-byte fixture response |

The six resource bodies total about 60.0 KiB; including the small root, the
aggregate is about 60.4 KiB. The fourth fixture URL is intentionally recorded
as measurement provenance: it currently returns a tiny JSON body through an
`<img>` descriptor. That is not a recommendation to deploy an invalid image.
Replacing it with a small valid image is reasonable for a real fronting site,
and the coherent size campaign below did measure that shape. Its result was
mixed rather than equivalent to the historical dashboard, so the canonical
ten-block matrix continues to describe the historical response exactly.

The event-driven policy contains no configured resource size, fixed pause,
RTT, bandwidth, or packet-index threshold. It should remain functionally safe
for other body sizes under the aggregate limit, but identical residuals are
not guaranteed. Existing 64-KiB and 1-MiB `--browser-page-base-size` screens
scaled the **inner tunneled browser workload**, not these outer Caddy
resources. Slower-link screens did exercise the outer profile above, but kept
its resource sizes fixed.

The integration harness exposes the outer input independently as
`--outer-resource-unit-size`: CSS, JavaScript, and four valid SVG bodies use
`3/6/2/2/2/2` units while the root and inner tunneled workload remain fixed.

## Measured coherent size envelope

The 2026-08-29 coherent campaign kept the exact topology, URLs, MIME types,
inner workload, product binary, and policy fixed while scaling only the six
outer bodies. It successfully exercised 17, 68, and 272 KiB excluding the
small root on an unshaped isolated WSL link, plus the 17- and 272-KiB endpoints
at 20-ms one-way delay and 20 Mbit/s. Every run completed all 24 participants
with 24/24 cold proxy resets and network checks, and a live preflight verified
the served byte counts and MIME types. The table gives packets 17--32 / whole
point estimates; the
complete five-view table and safe artifact IDs are in
[`CAPTURE.md`](CAPTURE.md#predeclared-outer-resource-size-campaign).

| Link | Outer bodies excluding root | H3 SOCKS5 default | H3 HTTP CONNECT default |
| --- | ---: | ---: | ---: |
| unshaped | 17 KiB | 0.50255 / 0.50407 | 0.50641 / 0.50456 |
| unshaped | 68 KiB | 0.46957 / 0.42231 | 0.49428 / 0.42534 |
| unshaped | 272 KiB | 0.37350 / 0.37166 | 0.38149 / 0.35411 |
| 20-ms one-way, 20 Mbit/s | 17 KiB | 0.24379 / 0.40498 | 0.31374 / 0.40292 |
| 20-ms one-way, 20 Mbit/s | 272 KiB | 0.23608 / 0.30535 | 0.25181 / 0.30231 |

These four-block screens establish functional coverage over 17--272 KiB, not
passive equivalence. On the unshaped link every default view improved
monotonically as the bodies grew. On the shaped link, 272 KiB still improved
whole by about 0.10 and HTTP CONNECT packets 17--32 by about 0.062, but worsened
the 250-ms view by about 0.037 for SOCKS5 and 0.028 for HTTP CONNECT. Resource
size therefore materially affects the observed residual, and there is no
single size proven optimal independently of RTT, bandwidth, and observation
window. Links slower than 20 Mbit/s, one-way delays above 20 ms, compression,
and aggregates between or beyond the tested endpoints remain unmeasured.

For a neutral placeholder deployment, use the coherent nominal profile as the
conservative baseline: 12-KiB CSS, 24-KiB JavaScript, and four valid 8-KiB
images, 68 KiB excluding the small root. It is closest to the historical
fixture while using valid image responses. A real site whose natural six
resources total up to about 272 KiB remains inside the measured envelope and
showed better whole-flow point estimates; do not add artificial padding or
inflate resources merely to reproduce this laboratory result. The 17-KiB
profile also functioned correctly but had the worst unshaped whole result.
Always keep root plus resources comfortably below the hard 384-KiB aggregate
limit and preserve the exact six-resource topology and response requirements
above.
