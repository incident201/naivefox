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

## Profile behind the current measurements

The canonical Caddy fixture rewrites `/` to its dense fronting page. Its outer
resource bodies are:

| Resource | Fixture body size |
| --- | ---: |
| Root HTML | A few hundred bytes |
| Stylesheet | 12 KiB |
| Classic deferred script | 24 KiB |
| Image descriptors 1--3 | 8 KiB each |
| Image descriptor 4 | 34-byte fixture response |

The six resource bodies total about 60.0 KiB; including the small root, the
aggregate is about 60.4 KiB. The fourth fixture URL is intentionally recorded
as measurement provenance: it currently returns a tiny JSON body through an
`<img>` descriptor. That is not a recommendation to deploy an invalid image.
Replacing it with a small valid image is reasonable for a real fronting site,
but its passive result must be measured before it is claimed equivalent to the
dashboard.

The event-driven policy contains no configured resource size, fixed pause,
RTT, bandwidth, or packet-index threshold. It should remain functionally safe
for other body sizes under the aggregate limit, but identical residuals are
not guaranteed. Existing 64-KiB and 1-MiB `--browser-page-base-size` screens
scaled the **inner tunneled browser workload**, not these outer Caddy
resources. Slower-link screens did exercise the outer profile above, but kept
its resource sizes fixed.

Consequently, the closest known measured deployment profile is the topology
and approximate sizes above. A coherent outer-resource size matrix, including
a small valid fourth image and shaped/unshaped links, remains required before
the acceptable size range can be widened in this contract.
