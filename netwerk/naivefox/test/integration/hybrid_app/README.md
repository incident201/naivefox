# Matched active application fixture

This is a test application, not a proxy or a production Caddy module. The same
Firefox runs the same checked-in JavaScript, manifest, semantic bootstrap and
WebSocket jobs directly at the outer origin or through NaiveFox at the inner
origin. The backend only generates and validates the manifest's fixed datasets;
it has no destination dialing or target configuration.

## Build and validation

Use the retained Go toolchain/cache and keep output and temporary files below
the task's existing object-directory subtree. In this directory:

```sh
python3 render-app.py --check
node --check app.js
node test-app.cjs
go test -race ./...
go build -trimpath -o /absolute/objdir/hybrid-ws/matched-app/backend/nfbench-app .
```

The small standalone module pins Gorilla WebSocket 1.5.3. It does not build
Firefox or add Go dependencies to the native product.

```sh
/absolute/objdir/hybrid-ws/matched-app/backend/nfbench-app \
  --listen 127.0.0.1:0 \
  --asset-dir /absolute/checkout/netwerk/naivefox/test/integration/hybrid_app \
  --ready /absolute/objdir/hybrid-ws/matched-app/private/ready.json \
  --stats /absolute/objdir/hybrid-ws/matched-app/private/stats.json
```

The private parent directory must already exist. The listener must be numeric
loopback; ready/statistics files are atomically replaced with mode 0600. The
ready file contains the port and SHA-256 of the embedded manifest bytes. Caddy
owns the client-facing certificate and TLS; do not disable verification.

## Identical origin routes

Configure these overlays identically on the outer and inner Caddy origins:

- Keep `/` on the existing transport module's 4096-byte root.
- Send `/assets/site.css`, `/assets/app.js` and `/assets/image-{1,2,3,4}.svg`
  to this backend.
- Send `/app/api/bootstrap/*` to this backend.
- Send `/api/realtime` to this backend **only** when
  `Sec-WebSocket-Protocol` equals `nfbench.app.v1`.

The native outer `nfc1.hybrid.v1` WebSocket and `/api/sync`, `/api/events/*` and
`/media/chunk/*` remain on the real transport module. They are separate from
the application workload. No production fault or benchmark hooks are needed.

The backend loads the six asset bodies once, before serving. CSS and the SVG
producer retain the existing fixture bodies padded to 12288 and 8192 bytes;
the shared generated script is exactly 24576 bytes. All four SVG paths use the
same producer. Subsequent file changes cannot change an active backend's
responses. There are no SRI attributes: the classic H3 root parser does not
permit them.

Actual completed asset writes, byte lengths and SHA-256 hashes are grouped in
`asset_groups` by the SHA-256 of the `app_session` cookie value; requests without
that cookie use `none`. API round zero binds its separate `nfbench_session`
cookie to that group. The application WebSocket requires its own six completed
assets and all forty semantic requests. Native outer cover assets under another
cookie cannot satisfy this gate. The raw cookie is never placed in statistics
or application responses. Cookie hashes remain private audit metadata.

## Semantic bootstrap and workload

After the ordinary load event, the script performs twenty ordered **POST/GET
pairs** at `/app/api/bootstrap/{0..19}`. POST carries the cursor, ascending
ordering, page size and manifest identity. Its response accepts that cursor;
round zero also returns the immutable six-asset inventory. GET returns sixty-four
catalog records with deterministic IDs, titles, group/revision fields and source
job references. Firefox parses, validates and stores all 1280 records.
Request and response hashes and byte lengths are recorded in `api`. There is
no padding or NFC1 filler in these application JSON messages.

The script then opens one ordinary WebSocket with subprotocol
`nfbench.app.v1`, waits 2000 ms, and executes:

| Stage | Job IDs | Useful payload |
| --- | --- | --- |
| Download | 1 | 8 MiB downstream |
| Upload | 2 | 1 MiB upstream |
| Parallel download | 3–6 | Four concurrent 512 KiB jobs |
| Small sequential echo | 7–10 | Four 4 KiB requests and responses |
| Idle and wake | 11 | Wait 2000 ms, then one 4 KiB echo |

All jobs reuse the same application WebSocket. Parallel jobs are registered
atomically by one `open_batch` command, before any of their ready/data messages.
The round-robin writer and per-job send timestamps provide an additional
overlap check. No per-small-request TLS or WebSocket establishment is performed.

The payload byte at offset `n` for job `id` is
`(id * 17 + n * 31 + (n >> 8)) & 255`. The manifest contains the exact size and
SHA-256 of every dataset. Application binary messages contain only a 16-byte
header and actual payload, never transport filler. Total useful bytes, derived
independently from the manifest, are 1,069,056 upstream and 10,506,240 downstream.

## Protocol and bounds

The 16-byte binary header is: type byte `1`, three reserved zero bytes, then
big-endian `id`, byte offset and payload length as unsigned 32-bit integers.
Payload is nonempty and at most 65536 bytes. Offsets, lengths, deterministic
payload content and final SHA-256 must match the manifest.

JSON controls are limited to 1024 bytes:

- Client `open`: `id`, `kind`, `bytes`; parallel instead uses
  `open_batch` with exactly `ids: [3,4,5,6]`.
- Server `ready`: `id`, `kind`, `bytes`, `credit: 524288`.
- Receiver `credit`: `id`, positive consumed/validated `bytes`.
- Client `fin` after upload/echo data: `id`, `bytes`, `sha256`.
- Server `complete`: `id`, `bytes`, `sha256`.
- Client `done` only after mandatory browser hash verification:
  `id`, `bytes`, `sha256`.

Credit stays at most 512 KiB per job. The backend holds at most four active jobs,
eight pending incoming messages, sixteen outgoing controls, one generated data
chunk and bounded echo buffers. It has one application writer; blocking writes
provide backpressure. Completed job IDs cannot be reused. At most eleven retired
credit counters allow bounded, valid late delivery credit without resurrecting
the job. Sessions and asset-cookie groups are also capped. Read and write
deadlines are thirty seconds.

The declared workload has 21 client and 165 server binary messages, and 190
client and 43 server JSON controls. A successful normal close requires all
eleven verified jobs, manifest-derived useful-byte totals, one atomic parallel
batch and a peak of four active jobs. Opening and idling on a WebSocket without
performing the workload cannot pass.

## Results and measurement boundary

Primary runs auto-start from navigation. `#hold` and `__NFB_RUN__` are plumbing
controls only and must not be used to schedule primary stages externally.
The browser publishes `__NFB_RESULT__` only after all verification and a clean
close code 1000. It records the manifest identity, declared asset inventory,
script hash, `performance.timeOrigin`, per-stage and per-job I/O timestamps,
verification timestamps, byte totals, digests and WebSocket counts. The I/O end
timestamp precedes WebCrypto hash verification; the next phase waits for that
verification. `__NFB_ERROR__` is terminal.

The asset metadata in the browser result is a declaration received from the
backend, not SRI. Actual immutable response-write hashes plus independent TLS
preflight and the source inventory provide the corresponding body-identity
evidence without extra browser fetches. Statistics distinguish those application
assets from native outer cover traffic.

This directory does not capture packets or compute residual/performance scores.
The measurement harness must use identical jobs and source bodies for every
participant, capture the declared complete lifecycle, and include every outer
TCP/QUIC flow. Component tests and a successful application run are functional
admission, not a camouflage or performance result.
