# CDN deployment

CDN is an optional deployment of the default [HTTPS packet transport](HTTPS.md).
Use https://user~PIN:password@host:443 for both direct and CDN endpoints.
There is no CDN-specific client scheme or automatic fallback.

## Required provider behavior

- Client-facing HTTPS with HTTP/2 and a valid edge certificate.
- Authenticated HTTPS to the origin; origin HTTP/1.1 or H2 is sufficient.
  Set the origin port to 443 and preserve the expected Host/SNI. An HTTP origin
  that redirects back to the public HTTPS URL can create a redirect loop.
- Completed binary POST bodies and incrementally forwarded GET responses.
  WebSocket and streaming request bodies are unnecessary.
- No cache for /api/packet and /api/packet/*; preserve methods, query strings,
  request headers and body bytes. Disable transformations and browser challenges.
  Varying cache keys by cookies or query strings does not disable caching.
- Route a session to the same origin process. Edge source addresses may change;
  packet authentication is independent of source and forwarded IPs.

A provider that buffers an entire live GET cannot serve this transport.
no-store, no-transform and X-Accel-Buffering headers are hints; verify their
actual handling. Public-site snapshot validation rejects transformed resources.
The origin private key stays outside the CDN and application_root.

## Acceptance boundary

Provider-specific production acceptance remains incomplete. Local fixtures and
short live probes do not establish long-term streaming, loss recovery, all-edge
behavior or performance. Validate prefix delivery, idle/lifetime limits,
request-size limits, cache bypass and routing for each deployment.
The shared HTTP/2 TCP connection can still stall after packet loss.
