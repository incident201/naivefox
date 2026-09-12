# NaiveFox integration verification

Tests exercise one current NaiveFox transport and coordinated client/server
builds. There are no classic, Auto, preamble, padding-version or migration
campaigns. The local frontends are SOCKS5 and HTTP CONNECT; the outer selections
are strict H2 and strict H3.

All generated packages, profiles, certificates, logs and captures belong under a
dedicated object-directory subtree. Reuse object directories for incremental
builds. Set TMPDIR and PYTHONPYCACHEPREFIX to that subtree. Credentials, decrypted
traffic and one-off reports must never enter source control.

## Correctness before measurement

Build the lean product with tools/build-product.sh. The Caddy binary must contain
the matching naivefox_transport module, without the classic forwardproxy module.
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
failure with no target connection and no fallback TCP packets. H2 must emit no
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
tests apply to H2; H3 uses streaming HTTP.

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
