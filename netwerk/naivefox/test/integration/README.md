# NaiveFox integration verification

NaiveFox is a research project created entirely with AI. This covers all
project-specific code and documentation; upstream dependencies retain their
original authorship and licenses.

Tests exercise one current NaiveFox transport and coordinated client/server
builds. Test the current transport, without alternate wire versions or
migration campaigns. The local frontends are SOCKS5 and HTTP CONNECT; the outer selections
are default https:// packet delivery over H2, explicit wss:// WebSocket delivery
and quic:// native HTTP/3. Shared runtime selectors packet, h2 and h3 correspond
to those three deliveries; all is the default.

All generated packages, profiles, certificates, logs and captures belong under a
dedicated object-directory subtree. Reuse object directories for incremental
builds. Set TMPDIR and PYTHONPYCACHEPREFIX to that subtree. Credentials, decrypted
traffic and one-off reports must never enter source control.

## Correctness before measurement

Build the lean product with tools/build-product.sh. The Caddy binary must contain
the matching naivefox_transport module.
Use an isolated Linux network namespace for local fixtures. The
run-camouflage-isolated-network.sh wrapper verifies namespace isolation, sets
loopback MTU 1500 and disables segmentation offloads before any producer starts.

~~~sh
export NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1
unshare -n -- bash run-camouflage-isolated-network.sh \
  python3 run-transport-tests.py \
    --runtime /absolute/package/runtime/naivefox \
    --caddy /absolute/caddy \
    --objdir /absolute/objdir \
    --output /absolute/objdir/checks/runtime
~~~

The runtime gate covers both frontends, slow consumers, byte-exact upload and
download, request-first and response-first FIN, 40 simultaneously open streams,
cancellation followed by a fresh transfer, credential denial and untrusted TLS.
H3 also blocks UDP while TCP remains reachable and requires explicit local
failure with no target connection and no fallback TCP packets. HTTPS and WSS must emit no
UDP. Captures and aggregate server counters attest the actual carrier.

Use run-transport-cli-tests.py for the public command line and strict JSON
bounds. run-transport-codec-tests.sh runs the standalone codec tests against warm
NSS/NSPR outputs, with optional sanitizers. Full project gtests use
NAIVEFOX_ENABLE_TESTS=1 and MOZ_RUN_GTEST=1; restore tests-disabled configuration
before collecting release/export evidence.

The matching server runs go test ./... and go test -race ./.... Tests include
ordered H3 POST application, duplicate/replay/out-of-window rejection, canceled
head and queued requests, flow-control bounds, retirement and half-close.

Additional focused runners cover hostile startup envelopes
(run-transport-adversarial-tests.py), H2 WebSocket framing/control
(run-websocket-adversarial-tests.py, run-websocket-control-tests.py), explicit
host mapping (run-routing-tests.py), public site graphs (run-site-tests.py) and
non-loopback listener binding (run-listener-address-tests.py). WebSocket-specific
tests apply to WSS; HTTPS and QUIC use streaming HTTP.

## Matched application and five windows

run-matched-app-matrix.py compares the same complete ordinary HTTP application in
normal Firefox and through each NaiveFox frontend. http_app uses standard fetch
GET/POST requests and streaming body consumption, without NaiveFox cells, custom
padding or a simulated browser transport.

The workload retains a fixed public asset inventory, twenty catalog POST/GET
pairs, an 8-MiB download, a 1-MiB upload, four parallel 512-KiB downloads, four
small echo requests and an idle wake request. Client and server verify all
payload hashes, byte counts, actual resource consumption, bootstrap order and
normal completion. Per-job nextHopProtocol proves that the reference transfers
use native H3 for an H3 comparison. Socket ownership must match the declared
direct or proxied route before active work.

Each randomized block includes the two local frontends and common Firefox A/B
controls. Optional temporary before/after experiments use the same common
controls for both binary pairs. An archived binary is comparison evidence, not
a supported old transport or product selector.

The maintained windows are p1-16, p17-32, p1-32, the first 250 ms and Whole.
Their feature definitions and scoring are unchanged. See [../../CAPTURE.md](../../CAPTURE.md).

Start with a short functional screen and one block. Investigate failed samples,
route mismatches, isolated latency stalls or material distance shifts before
expanding collection. A few blocks are screening evidence; they are not the
30-block statistical gate. Never start a long campaign simply to average away
an unexplained implementation or fixture problem.

## Reference workload matters

The earlier application used WebSocket/TCP even after HTTP/3 page loading.
Its Whole view therefore contains a separate TCP TLS handshake and TCP record
features. A QUIC-only NaiveFox carrier intentionally lacks those features.

Keep that historical result when diagnosing the transition. Do not silently
label it as passing, remove unfavorable features, introduce dummy TCP traffic
or compare numbers across different workloads as if they were paired. The
ordinary HTTP fixture provides a protocol-matched H3 reference. Compare old and
new binaries against the same reference workload and record the workload hash.

## Packaging and platforms

tools/verify-staged-runtime.sh checks a relocated Linux package, its libraries,
strict CLI and bounded runtime lifecycle. Supply --caddy for the network gate.
NAIVEFOX_VERIFY_WORK_DIR places its temporary files beneath the chosen objdir.

Windows and Android use the same current configuration and three-argument
NaiveFoxRunEmbedded API. Their platform runners adapt process ownership and
host/device routing to the shared fixture; they do not select another transport.
Cross-compilation and static package checks do not establish device runtime
behavior. Record native device/host verification separately.

Export minimal source only with current build/configuration/link evidence.
The normal product and exported product must remain free of browser execution,
DOM loaders, JavaScript engine and retired activation-process dependencies.

## CDN deployment fixture

Build test/integration/cdn_proxy/main.go with the pinned Go toolchain and keep
the binary outside the source tree. run-cdn-tests.py uses it as a separate
TLS-terminating H2 edge with H1/H2 origin startup and H1 WebSocket. It verifies
changing trusted proxy IPs, independent and rotated/path-scoped cookies,
missing Content-Length, split reads, identical startup replay, lifecycle,
idle heartbeat and fresh sessions after edge restart. Hostile status, encoding,
MIME, snapshot and body-length cases must fail before opening a target.

The Windows and Android runtime runners accept --cdn-proxy with --protocol h2
to exercise their normal native workloads through the same replaying edge.
These local tests do not establish real-CDN compatibility. Provider-specific production acceptance remains incomplete. HTTPS packet
delivery is the default independently of CDN use.


## Default HTTPS packet delivery verification

run-packet-tests.py exercises the native https:// URI, independently pinned inner
TLS, both local frontends, streaming integrity, half-close, 40 streams, idle
heartbeat and rejection of a wrong pin, credentials or edge CA. With
--cdn-proxy it injects lost finite responses, reordered uploads, arbitrary body
fragments, download truncation and complete outer TCP connection resets,
against H1 or H2 origins, including chunked origin request reframing. WebSocket upgrades
are disabled in this fixture. The native Windows and Android runners accept
--protocol packet with the same fault-injecting proxy.

The client and Go TLS implementations are tested against each other; the server
packet unit suite additionally covers queue bounds, receipt expiry, stale
generation/cursor rejection, replay ownership, request cancellation, MAC
binding and old-replay inactivity expiry.

For a short matched H2 screen, --include-packet adds default HTTPS packet HTTP/SOCKS arms beside
WSS HTTP/SOCKS in each randomized block, sharing the same Firefox A/B
controls. Window definitions, feature extraction, workload and health gates
are unchanged. Carrier health checks distinguish the explicitly selected path.
A short block is diagnostic evidence, not a 30-block acceptance claim.


run-packet-receipt-tests.py holds a large upload block while later blocks reach
the origin, then loses a head response and one later receipt. Both frontends
must preserve payload integrity, retry missing receipts, keep the eight-slot
cumulative window and avoid resending blocks whose storage was already
confirmed. Run it with --cdn-proxy against both H1 and H2 origins.


run-http2-upload-backpressure-tests.py is a short direct HTTPS regression.
Inside a private network namespace it limits TCP send buffers, shapes only the
outer carrier, and alternates an 8-MiB download with a 1-MiB upload through both
frontends. Payloads must remain exact and uploads must finish before the request
retry deadline. This is a queue-ownership test, not a five-window or peak-speed
benchmark. Optional --diagnostics retains private native HTTP/2 logs and TLS
keys below the output directory; never publish those files.

The NaiveFoxHttp2Upload gtest forces repeated frame-commitment refusal at the
end of an input stream. It checks that blocked writes consume no source bytes,
then verifies a single complete DATA/END_STREAM frame after the queue drains.
