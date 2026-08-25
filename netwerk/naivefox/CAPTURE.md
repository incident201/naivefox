# Firefox wire-behavior diagnostics

Capture comparison checks that NaiveFox continues to use Firefox's Necko,
NSS/PSM, and Neqo wire machinery without accidental project-specific markers.
It is diagnostic: a browser GET and padded proxy CONNECT are different
workloads, so packet timing and volume are not fingerprint-equality targets.

## Size-independent mechanism rule

Fixture asset sizes are diagnostic inputs, not camouflage parameters. A
candidate must not be selected by searching for a response size which happens
to improve a packet index, time window, or classifier score. Production code
may retain bounded byte budgets for safety, but it must derive scheduling,
headers, priorities, cache behavior, and stream lifecycle from normal Gecko
causes. Any topology candidate should remain directionally stable across a
small predeclared range of ordinary fixture sizes before promotion.

`document-overlap` is the size-independent control for document scheduling. It
admits CONNECT after successful 2xx document response HEADERS while the Necko
listener is still active, and then requires a normal document drain. Decrypted
admission requires one QUIC identity, one outer ClientHello, matching
`document-complete` request semantics and response `Content-Length`, and the
product lifecycle order `response-headers admission -> CONNECT -> normal
drain`. Whether server FIN happens before or after CONNECT is report-only: it
must never trigger sample replacement or fixture-size adjustment.

`document-start-overlap` moves the barrier earlier without using a main-loop
turn as a proxy for socket progress. The root channel's own
`NS_NET_STATUS_WAITING_FOR` event proves that Gecko committed the bodyless GET
to its H2/H3 stream path before CONNECT is admitted. The final response result
and normal drain are recorded separately. H3 decrypted admission requires GET
HEADERS to precede CONNECT HEADERS on the sole QUIC identity; failure rejects
the mechanism and never causes selective recapture. Response HEADERS and FIN
ordering remain outcomes rather than admission criteria.

## Modes and policy

The runners support two reference modes:

- `quick` (default) downloads the current official Firefox Nightly artifact
  named by the tooling manifest and uses that binary directly; it does not
  build Firefox. The manifest records the expected Nightly version and the
  SHA-256 of the last verified archive. Mozilla may republish a mutable
  `latest-mozilla-central` URL without changing the version string. When that
  happens, verify the downloaded binary still reports the manifest version and
  refresh only `archive_sha256` before rerunning the gate. This keeps the
  comparison against the current Nightly while retaining an explicit artifact
  integrity check.
- `same-base` uses caller-supplied Firefox and NaiveFox packages built from the
  same Firefox base. It is the only meaningful exact stack comparison and the
  only mode that may require a Firefox browser build.

An ordinary Firefox build is allowed only when the same-base diagnostic is
explicitly requested. It is outside the upstream, minimized-product, and export
gates and is never a merge or release prerequisite.

Run the H2, H3, and passive comparisons from the integration directory:

```bash
./run-capture-comparison.sh
./run-h2-capture-comparison.sh --arm root
./run-h3-capture-comparison.sh
./run-observer-comparison.sh
./run-camouflage-suite.sh --mode smoke --protocol both
```

For same-base mode, provide the reference paths required by the runner:

```bash
NAIVEFOX_CAPTURE_MODE=same-base \
NAIVEFOX_CAPTURE_REFERENCE_BIN=/path/to/firefox \
NAIVEFOX_CAPTURE_REFERENCE_LIBDIR=/path/to/firefox-package \
NAIVEFOX_CAPTURE_REFERENCE_OBJDIR=/path/to/firefox-objdir \
./run-capture-comparison.sh
```

The H3 runner accepts the same selection variables. Keep the Firefox and
NaiveFox packages and object directories isolated.

Create the ordinary Firefox reference once with the dedicated builder:

```bash
netwerk/naivefox/tools/build-firefox-same-base.sh --dry-run
netwerk/naivefox/tools/build-firefox-same-base.sh
netwerk/naivefox/tools/build-firefox-same-base.sh --verify
netwerk/naivefox/tools/build-firefox-same-base.sh --reuse
```

The builder derives the exact merge-base of the current NaiveFox revision and
`firefox-upstream`, checks it out as a detached pristine worktree, and performs
one optimized non-PGO browser build with four jobs by default. It runs
`mach package` and records the source revisions, full mozconfig, mozinfo,
toolchain and sccache identity, application and ELF build IDs, NSS key-log
define, and hashes of the package and capture runtime in a private manifest
below the reference object directory.

The reference is reusable by later NaiveFox revisions that retain the same
Firefox merge-base. A completed manifest is verified instead of rebuilding;
an exact prepared manifest may resume an interrupted build. The builder never
deletes or clobbers an existing worktree or object directory and refuses any
unrecognized directory. `--reuse` prints the verified paths expected by the
capture runners. Override the external paths only with `--worktree` and
`--objdir`; use `--sccache off` when compiler caching is not wanted.

## Capture prerequisites

The host needs `dumpcap` and `tshark` plus loopback capture permission. Grant
only the normal Wireshark capabilities if required:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip "$(command -v dumpcap)"
```

The fixture binds to loopback. H2 capture filters the proxy TCP port. H3 capture
filters UDP and TCP at the strict H3 proxy port so both the intended QUIC flow
and any forbidden TCP fallback are visible.

On WSL, the `any` interface can expose cooked transmit and receive copies of a
loopback packet. Before stateful QUIC dissection, retain the transmit copy
(`sll.pkttype == 4`) so duplicate packet numbers do not corrupt Wireshark's key
phase or QPACK tracking.

WSL's packaged `dumpcap` can be denied access when its output path is below the
object directory, even with the required capabilities. The capture runners
automatically use a private staging directory below `/tmp` and move completed
captures into the private object-directory diagnostics tree. No manual
permission change is needed, and successful runs still delete raw captures and
key logs. When the runner is invoked as root, it also supplies a private
`XDG_RUNTIME_DIR` so the downloaded headless Firefox can start without using the
interactive user's WSLg runtime directory.

## Decrypted internal audit

Independent private NSS key logs allow `tshark` to inspect encrypted protocol
state without replacing the client TLS/H2/H3 stack.

For H2, compare:

- same endpoint/SNI and selected `h2`;
- semantic ClientHello ciphers, extensions, groups, signatures, and versions;
- HTTP/2 SETTINGS and early SETTINGS/WINDOW_UPDATE/HEADERS ordering;
- multiple CONNECT stream IDs on one outer connection;
- `padding` request/response header names;
- absence of synthetic `alpn`, `upgrade`, and `connection` request headers.

`run-h2-capture-comparison.sh` is the hardened same-base sequence admission
for camouflage arms. It always re-executes inside the private capture network,
starts a route-netlink mutation monitor before each participant, disables and
verifies namespace-local loopback offloads, rejects dumpcap drops or early
exit, and requires direct Firefox and NaiveFox to use one physical TCP/H2
connection. Private key logs drive ALPN, client SETTINGS, and per-stream
GET/CONNECT/response/END_STREAM reconstruction. Safe output retains only
header names, relative timing and ordering, stream indices, equality booleans, and build
identity; header values, endpoint values, credentials, profiles, captures, and
keys are deleted after success. Both cohorts deliberately use a command-line
cold Firefox start after capture begins, and that backend/start state is
recorded so warm Selenium and cold command-line runs cannot be mixed silently.
For tree arms, private admission also requires native HTTPS document fetch
metadata with exact `Priority: u=0, i`, plus same-origin stylesheet/script
`Referer`, fetch metadata, and `Priority: u=2`; only pass/fail booleans enter
the safe summary.

`run-h2-connect-priority-comparison.sh` isolates the scheduling cause of the
first SOCKS tunnel. It uses the same authenticated Caddy listener for an
ordinary proxied same-base Firefox navigation, NaiveFox default, and the
explicit first-tunnel UrgentStart diagnostic. Firefox receives a private
exact-target channel filter through a system-access Marionette session; the
filter carries preemptive proxy authorization in memory, never in a profile or
command line. The decrypted gate rejects a 407, multiple outer TCP/ClientHello
identities, TLS/SETTINGS mismatch, or missing/ambiguous scheduling evidence.
The exact first-CONNECT packet must contain one relevant HEADERS occurrence,
method, and stream plus one priority-flag/dependency/weight field set; an extra
coalesced HEADERS or PRIORITY occurrence invalidates admission. Before capture,
both fresh NaiveFox processes must have no applied marker. After workload,
default must still have none, while the diagnostic must have exactly one safe
`Connection 1` H2 applied marker ordered before its first CONNECT-established
evidence. Only the resulting validation booleans are exported. A valid run
classifies the mechanism as `native-match`, `wire-null`, or `native-mismatch`;
the latter two remain successful experiments and delete their private raw
material normally. Safe evidence records only that verdict,
Priority-header-presence booleans, scheduling equality, header names/order, and
source/build hashes. It never feeds a passive classifier.
The current same-base Caddy H2 control produced `mechanism_verdict=wire-null`:
default NaiveFox, the UrgentStart diagnostic, and proxied Firefox had equal
observable scheduling, and none carried a CONNECT `Priority` header. Do not
promote this diagnostic to a camouflage default or spend passive samples on
it; it remains available only to test another H2 peer or a future Gecko tunnel
adapter change.

The preamble callback barrier and observed wire ordering are deliberately
separate claims. On a fast loopback run, Necko can receive a resource response,
release `tree-early-overlap`, and still schedule the CONNECT HEADERS after the
resource END_STREAM. The decrypted validator rejects that trace. Do not
selectively recapture only passing traces or use this arm for passive causal
screening until wire-level overlap is deterministic; doing so would condition
the dataset on the scheduling outcome being measured.

`tree-root-overlap` is a separate production-shaped scheduling arm and does
not change any older mode. It discovers and opens the same resource channels
while parsing the root, then admits CONNECT once the root has completed and at
least one resource channel was successfully started. It does not wait for a
resource response callback. A safe product marker records only
`root_done=1`, the non-zero started-resource count, protocol, and admission
kind; sample validation requires exactly one such marker before passive
analysis. The controlled fixture requires exactly two started resources. It
also waits for a separate safe `drain=complete completed_resources=2` marker,
emitted only after both opened assets received response headers, HTTP 2xx, and
a successful `OnStopRequest`, while every preamble stream stopped normally. A
bounded watchdog invalidates the sample and never substitutes for that
completion. The H3 decrypted
comparison requires `tree-root-overlap` to be
selected with `tree-complete` so private request and response-size parity is
proved in the same run. If the tree contains no successfully started resource, terminal
operation completion releases CONNECT immediately instead of waiting for the
preamble timeout.

This is a client scheduling intervention, not a promise that QUIC/H2 frames
will appear in the same order. Resource response HEADERS or FIN may precede
CONNECT on a fast path. Decrypted diagnostics report observed overlap, but do
not use it for admission, retry a failed ordering, or filter passive samples by
that outcome. They still require a completed root before CONNECT and validate
the production normal-completion marker for every expected CSS/JS response.
Observed asset FIN is report-only because a fully consumed known-length H3
response may use `H3_REQUEST_CANCELLED` instead. In the paired H3
diagnostic, `tree-complete` and `tree-root-overlap` must retain identical
root/CSS/JS request semantics and response sizes. H2 uses the same fixed
fixture/config and validates expected request semantics, but does not claim a
paired asset-size proof.

Resource-cache experiments use the orthogonal `cache-resources` preamble
setting. The default is off. When enabled for a tree/resource mode, only CSS,
JS, and other discovered resource channels enter Gecko's native HTTP cache;
the root navigation stays cache-inhibited. This is a mechanism diagnostic for
separating resource topology from cold response-body volume. It is not a
persistent-profile design: normal NaiveFox temporary-profile startup remains
valid, and product behavior must not depend on state surviving a process run.

The passive harness exposes that question only as `tree-warm-css-304`. It maps
to `tree-root-overlap`, selects one CSS resource, and enables
`cache-resources`. Every reference or NaiveFox participant receives a newly
created profile which is reused only for its private warm→measure pair and then
deleted. The measured passive window begins at its publicly observable client
Initial. The complete pcap is preserved with other private diagnostics when a
run fails; a server-only tail from the closed warm 5-tuple may precede the
Initial and is excluded from classifier features. Any client traffic before
that Initial, or any other endpoint UDP 5-tuple after it (regardless of whether
its payload decodes as QUIC), invalidates the sample.
The fixture serves the stable CSS
with `ETag` and `Cache-Control: no-cache`; admission fails unless Gecko first
receives an unconditional 200 and later generates `If-None-Match` which earns
a 304. The NaiveFox inner measurement browser remains fresh and must still
receive its own cold 200. This arm is H3 gate/smoke diagnostic evidence only:
it cannot enter a shared superblock or a confirmatory/research run, and it is
never a proposal for persistent `NAIVEFOX_PROFILE` state. The unchanged
`tree-root-overlap-css` arm remains the cold control.
The warm arm disables persistent TLS token storage and H3 0-RTT in both outer
profiles, rejects any `ssl_tokens_cache*` file, and requires one measured QUIC
identity, one ClientHello, and no 0-RTT packet. Its network-mutation monitor
runs continuously from before warm-up through the measured capture cutoff.
Firefox A/B and NaiveFox receive condition-specific warm controls; a fresh
`tree-root-overlap-css` run under the same fixture revision and binary build is
the separate cold dataset. Cross-dataset warm/cold interpretation is therefore
descriptive causal screening, not paired or confirmatory inference.

The first fully fail-closed one-block same-base H3/inner-HTTPS admission run for
this diagnostic is retained as safe artifact `d26f91c82a29ceed` (seed `305`, NaiveFox binary
build ID `b1820e20442018465574f71995c69460`). Firefox A, Firefox B, and NaiveFox
each produced an unconditional warm 200 followed by a measured native Gecko
conditional request and 304 with identical selected outer request semantics.
The fresh inner Firefox behind NaiveFox independently received 200. Every
measured participant had one QUIC identity, one ClientHello, no 0-RTT, no TLS
token persistence, and no route/address/link mutation. This is admission
evidence only; one block contains no classifier inference. The predecessor
private run `da04d2419e6c9715` discovered two delayed server-only datagrams from
the closed warm 5-tuple before the measured client Initial. The harness now
preserves that full pcap on failure and defines the passive sample origin at
the first client Initial, while rejecting any client traffic before it or
stale flow continuing afterward.

`tree-complete-css` and `tree-root-overlap-css` are harness-only one-asset
controls. They map to the unchanged production `tree-complete` and
`tree-root-overlap` modes with `max-assets=1`, so the parsed root opens only the
first stylesheet and never the later script. Passive admission requires exact
`started_resources=1` and `completed_resources=1` lifecycle evidence. The H3
decrypted diagnostic admits `tree-root-overlap-css` only beside
`tree-complete-css`; it proves one QUIC identity, identical root/CSS selected
request semantics and response size, and root FIN before CONNECT. Any observed
CSS FIN ordering relative to CONNECT remains report-only and is never a
resampling criterion.

Controlled causal screens may set
`NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE` and
`NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE` before starting the fixture. The
defaults remain 64 KiB and 128 KiB. Both values are bounded and recorded in
safe metadata. Vary them only as a declared response-profile condition while
keeping the normal CSS/Script channels and orchestration unchanged; never
select them from observed packet positions.

`root-pmtud-control` is an H3-only, same-binary harness control. It uses the
same production `document-complete` configuration and root workload as `root`,
but only the NaiveFox participant profile explicitly sets
`network.http.http3.pmtud=true`. Passive screening can therefore compare the
new default route with the former forced-PMTUD behavior without changing the
socket, product binary, or preamble semantics. Decrypted admission proves one
complete root GET with equal selected request semantics and response size; it
does not claim PMTUD wire equivalence. That conclusion requires the passive
capture itself.

The dedicated H3 diagnostic gives every NaiveFox arm the same inner HTTPS URL;
the direct reference uses the same page path and query on the outer fixture.
It rejects any Firefox nonzero exit or empty screenshot and requires
successful response headers and `Content-Length` for every preamble GET plus
an observed root FIN before CONNECT. Application completion at a known response
length may make upstream Firefox end an H3 fetch with
`H3_REQUEST_CANCELLED` instead of waiting for the peer FIN, so asset FIN is not
used as a universal completion predicate. The diagnostic checks every root
against Firefox navigation semantics and uses
`tree-complete` as the required root/resource-size control for all two-resource
overlap modes. `tree-early-overlap` and `tree-overlap` have no existing private
normal-drain completion marker, so their available decrypted ordering
predicates must pass directly; a missing predicate is not repaired with a wait
heuristic or selective resampling. Until exact H3 DATA-byte accounting is
available, do not use those two arms for a strict whole-volume conclusion: one
background asset can complete by known `Content-Length` without an observed
FIN. `tree-root-overlap` has the deterministic all-resource drain marker used
for that comparison.

For H3, compare:

- QUIC version and negotiated `h3`;
- semantic ClientHello and client transport parameters;
- H3/QPACK settings;
- classic CONNECT rather than CONNECT-UDP or extended CONNECT;
- multiple CONNECT streams on one QUIC connection;
- no established TCP fallback at the strict H3 endpoint;
- padding negotiation and absence of synthetic markers.

Same-base `run-h3-capture-comparison.sh --compare-arms` additionally audits
document/tree preamble ordering against the browser-page root and resources.
The experimental `tree-early-overlap` arm is opt-in and must be selected
together with `tree-complete`.
Use repeated `--compare-arm` options to restrict the candidate set. Its safe
event table reports only method/status sequencing, connection/stream indices,
packet positions, and observable response-header or stream-FIN overlap; raw
headers, values, captures, and NSS keys remain private. For tree causal
validation, a transient private GET-only extract compares selected root/CSS/JS
request values and ordering between complete and overlap. For
`tree-early-overlap`, a second private-only extract proves that the two asset
`Content-Length` values also equal `tree-complete`; all values are deleted with
the private capture directory. The safe summary retains only booleans:
`tree_request_semantics_match=yes` for complete/overlap equality,
`tree_early_overlap_request_semantics_match=yes` and
`tree_early_overlap_asset_sizes_match=yes` for the experimental arm, and
`tree_expected_request_semantics=yes` after the root has navigation/site-none
semantics and CSS/JS have same-origin no-cors subresource semantics with
referers exactly equal to the root URL computed from its private pseudo-header
values.

The browser-page fixture uses a 64 KiB stylesheet and a 128 KiB script, with
the root document and both assets remaining below the configured 256 KiB tree
budget. Direct Firefox, preamble traversal, and the inner browser load all use
those same URLs and response bodies. This controlled size increase is intended
to keep ordinary resource streams alive when `tree-overlap` or
`tree-early-overlap` starts CONNECT on loopback; it is not tuned to reproduce
any packet sequence. Neither the origin
nor Caddy applies `Content-Encoding` to these fixture assets: adding gzip or
zstd would invalidate the intended on-wire stream lifetime.

Do not require equality for connection IDs, random values, GREASE values, or
TLS extension order. NSS may independently randomize extension order. In quick
mode, record version-dependent differences instead of presenting them as a
same-source failure.

## Passive observer audit

The passive runner explicitly removes `SSLKEYLOGFILE`. It may retain only
packet direction, transport length, QUIC long-header/version metadata, coarse
handshake ordering, and other facts visible without private keys. QUIC Initial
protection is publicly derivable, so semantic ClientHello and transport
parameters may still be inspected; HTTP/3 headers and 1-RTT plaintext may not.

Packet counts, lengths, timing, and TCP probes are recorded, not normalized or
treated as equality requirements. Any established TCP transport or TCP payload
in a strict-H3 NaiveFox case is a failure.

## Statistical camouflage experiment

`run-camouflage-suite.sh` attacks the project assumption from the point of
view of a passive traffic classifier. It does not prove indistinguishability.
It attempts to distinguish ordinary Firefox from NaiveFox using selected
externally observable features, reports the measured classifier advantage, and
compares that advantage with an independent Firefox-versus-Firefox baseline.

The collector uses seeded complete blocks for three cohorts: Firefox A,
Firefox B, and NaiveFox. Each sample gets a fresh, symmetric trusted profile.
All cohorts run on the same host and network namespace, use the same capture
interface, endpoint IP and port, certificate, target, Caddy process, and
controlled Firefox page workload. The NaiveFox browser reaches the target
through its private SOCKS listener. HTTPS/TLS inside CONNECT is the default
NaiveFox workload; `--inner-transport http` produces a separate cleartext
diagnostic dataset. Forced Alt-Svc applies only to direct H3 reference traffic,
not to the SOCKS browser. The SOCKS browser uses a fail-closed PAC: exact
loopback destinations use the sample's SOCKS5 port, while every other hostname
goes to a dead local proxy with no `DIRECT` fallback. This keeps Mozilla
background requests out of the captured NaiveFox flow. H2 and H3 are separate
datasets. Strict H3 rejects
established TCP or TCP payload.

The browser controller remains alive until after the primary capture has
stopped. After receiving the browser's completion POST, the target writes a
private completion file which the controller watches without generating a
second network flow. Browser or NaiveFox process shutdown is therefore not part
of the primary sample.
`dumpcap` readiness uses its runtime marker rather than pcap header creation.
Every sample rejects nonzero capture drops, an H2 flow missing its opening
client SYN and ClientHello, or an H3 flow missing its opening client Initial.

Controlled workloads cover a cold small operation, a browser page with CSS,
JavaScript, images, and an API response, warm sequential requests, burst and
concurrent requests, bulk download and upload, bidirectional transfer, and an
idle-then-resume lifecycle. The runner keeps request counts and byte budgets
comparable where the protocol roles allow it; payload equality is not assumed.

The safe feature extractor uses only information available on the wire:

- signed direction and normalized IP/TCP-payload or UDP-datagram lengths for
  the first 128 packets, plus 16/32/64/128-packet aggregates;
- inter-packet timing, response delay, bursts, idle gaps, direction n-grams and
  run lengths, and byte/packet windows through two seconds;
- visible TLS-over-TCP record boundaries and lengths;
- TCP SYN options, FIN/RST/reconnect behavior, and actual retransmission or
  out-of-order observations;
- public TLS ClientHello capability sets with GREASE values normalized;
- for QUIC, version, Initial sizes and ordering, CID lengths, Retry/version
  negotiation, public ClientHello and transport parameters, packet phases, and
  observable TCP probe attempts at the strict-H3 endpoint.

It deliberately excludes labels and harness artifacts, filenames, process
data, source/destination ports, absolute timestamps, profile paths, credentials,
queries, decrypted H2/H3 headers and frames, CONNECT authority, plaintext,
private NSS secrets, and NaiveFox logs. H2/H3 decryption remains available only
to the separate wire-parity diagnostics.

The analyzer is dependency-free Python. It fits an L2-regularized logistic
classifier after train-only standardization and feature screening. Splits are
grouped by experiment block when that metadata is present, otherwise by
session, and stratified by workload; the two Firefox cohorts form the
experimental baseline. The primary metric is orientation-fixed ROC AUC with
NaiveFox, or Firefox B in the baseline, always treated as the positive class.
AUC is calculated within each outer fold and pair-weighted across folds, so
uncalibrated scores from different models are never pooled. The report retains
`D = max(AUC, 1 - AUC)` only as a diagnostic and never uses it for a verdict.

Empty steady or lifecycle phases have explicit observable presence indicators.
The general diagnostic confidence interval uses a conditional clustered
bootstrap of held-out groups within their outer folds. Research verdicts do not
use that fixed-model interval: for both the target comparison and its Firefox
negative control, the three primary views additionally resample complete
workload blocks and refit the entire grouped-CV pipeline, including feature
screening and standardization. The workload/block-stratified permutation test
also refits that pipeline after reshuffling session labels. Expensive
permutation refits run only for Firefox-versus-NaiveFox in the three
predeclared verdict views: initial 32 packets, steady state after 32 packets,
and lifecycle. Secondary reports explicitly record why refit inference was not
run.
The report also includes an outer-fold threshold learned only from training
data, confusion metrics, standardized coefficient importance, and
leave-one-workload-out generalization. Whole-flow, initial packet windows,
initial time windows, steady state, and lifecycle views are reported
separately. Raw metrics are retained so policy thresholds can be changed
without recapturing traffic.

Run levels are:

- `gate`: two samples per cohort and protocol; structural collection, analysis,
  and sanitization only;
- `smoke`: ten samples per cohort and protocol; end-to-end validation only;
- `standard`: sixty samples per cohort and protocol; preliminary statistics;
- `research`: 240 samples per cohort and protocol; the only level that emits a
  GREEN/YELLOW/RED research classification. This is a hard minimum: the runner
  and non-screening analyzer reject a smaller research dataset.

Gate and smoke results are always `INCONCLUSIVE`, regardless of their point
estimate. Research policy currently treats a sufficiently narrow, validated
refit-bootstrap interval contained within `0.40--0.60`, a healthy Firefox
negative control whose interval contains chance, and no more than 0.05 excess
absolute advantage over that control as GREEN. Reverse-oriented, confounded,
or uncertain evidence is YELLOW. Stable `AUC >= 0.70` is RED only when its
refit lower bound is at least 0.60 and the full-refit permutation test also has
`p <= 0.05`. These are project policy thresholds, not a security proof.

Examples:

```bash
./run-camouflage-self-tests.sh
./run-camouflage-suite.sh --mode gate --protocol both
./run-camouflage-suite.sh --mode gate --protocol both --inner-transport http
./run-camouflage-suite.sh --mode smoke --protocol h2
./run-camouflage-suite.sh --mode standard --protocol both --seed 20260824
./run-camouflage-suite.sh --mode research --protocol h3
```

The same explicit seed reproduces schedule order for HTTPS-primary and
HTTP-diagnostic runs, but separately collected samples are not statistically
paired. For arm comparison, `--multi-arm-superblocks` requires same-base mode
and collects Firefox A, Firefox B, and NaiveFox `off`, `gate`, and `root` once
per seeded randomized block. The sanitized full dataset records
`experiment_block` and `naivefox_arm`, then materializes one arm-specific
dataset per arm using the same two contemporaneous Firefox controls. In that
mode `--samples-per-cohort` is the number of five-member blocks per protocol;
it cannot be combined with `--naivefox-arm`.

The default remains that bounded five-member design. A deliberate screening
run may instead select arms with `--multi-arm-arms`; for the first preamble
shape screen use
`gate,root,tree-complete,tree-overlap` (`root` is document-complete). A causal
follow-up may replace `gate` with the opt-in `tree-early-overlap` control while
keeping the root, CSS, JavaScript, and asset sizes identical. Add
`--multi-arm-views initial_packets_16,packets_17_32,initial_packets_32` to
report both the cumulative early views and the non-overlapping 17--32 packet
slice. `packets_17_32` contains only passive per-packet (and, for H2,
per-record) sequence features from those positions; it does not repeat the
handshake or first-16 aggregates. That creates randomized six-member blocks: the four
selected modes are all paired against the same Firefox A/B controls. The
analyzer emits every selected pair and its Holm family contains only the
protocols and views in that report. This opt-in design is screening-only and
cannot produce an absolute verdict.

The first isolated same-base H3/inner-HTTPS smoke for
`root,tree-complete,tree-root-overlap` is retained as safe artifact
`183164d35decbb0f` (seed `24082420`). It used ten paired blocks and therefore
supports neither relative nor absolute inference. Descriptively,
`tree-root-overlap` was closest to the shared Firefox A/B
controls for packets 17--32 (0.53163 versus 0.56326 for `tree-complete` and
0.64406 for `root`) and for packets 1--32 (0.20633 versus 0.22243 and
0.23118). `root` remained closest for packets 1--16 and for the whole-flow
view. Together with the paired decrypted trace, which observed CONNECT at
packet 19 while both resource responses were still active, this supports the
root-complete/start-resources admission mechanism but does not support making
it the default. The remaining early signal is concentrated around packets 12,
14, and 17, while the whole-flow penalty remains dominated by tree response
volume. A follow-up should vary the number of naturally opened resources with
the same admission rule before adding delays or packet-size targets.

The first ten-block same-base H3/inner-HTTPS screen of
`document-complete,document-overlap,document-start-overlap` is retained as safe
artifact `c7ef700ffa3c42ae` (seed `20260825`, revision `2ab204178e4b`). All 50
participants passed the isolated-network mutation monitor and physical-QUIC
checks. This is scheduling evidence only: the reference used the measured
scenario query while the outer preamble still used the shorter fixed document
path. Relative to the common Firefox A/B envelope, request-committed overlap
was worse in every selected view: 0.13721 versus 0.08776/0.08299 for packets
1--16, 0.63527 versus 0.61094/0.61219 for packets 17--32, and 0.23219 versus
0.20479/0.20280 for packets 1--32. Its new packet-13 client phase opposed the
Firefox server phase. The two older document modes remained descriptively
indistinguishable from one another. Consequently none of these modes warrants
a 30-block confirmation from this run.

Future paired captures pass the exact same scenario path and block-scoped wire
token to the direct reference, the candidate outer preamble, and the inner
browser. The local completion file is still removed before every participant,
so reusing a wire token cannot satisfy the next controller from a stale marker.
Safe metadata records this token/reset policy, the fixed
`browser-done + 250 ms` cutoff, and fail-closed preamble-drain admission. This
removes request-target and root-body differences without tuning a response
size or copying decrypted fields into the passive classifier.

Each paired view also retains diagnostic-only top passive features and mean
signed packet-size sequences. The sequence records the Firefox A/B mean
absolute disagreement (the local noise floor) and each arm's mean delta from
the matched Firefox midpoint. These aggregates help distinguish a reduced
13--20 signal from a divergence merely shifted after packet 16. They never
enter the arm distance, bootstrap, sign-flip tests, or Holm family, and contain
no decrypted fields, HTTP semantics, stream identifiers, endpoints, or raw
timestamps.

Tree results collected before the decrypted H3 profile split are invalid.
Those runs injected `network.http.http3.alt-svc-mapping-for-testing` into the
NaiveFox process even though its outer H3 route was already explicit. The root
GET could use the proxy connection while the resource GETs used the newly
ready test mapping and a second QUIC connection. In particular, private
capture `20260824T123959Z-725a1454` diagnoses harness contamination; it is not
product evidence and must not be used in passive or decrypted conclusions.
The direct same-base Firefox profile still needs the test mapping, while every
NaiveFox arm profile must omit it.

Controlled same-base screening must also exclude host network mutation as an
experimental variable. WSL mirrored networking can project host VPN
route/address churn into Linux, where Gecko correctly emits
`network:link-status-changed`, runs `VerifyTraffic`, and marks an active H3
session `DontReuse`. This is real Firefox network-change behavior, not a pool
reuse defect and not something production code may suppress. Use
`NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1` to run the complete localhost experiment
inside a stable private Linux network namespace. Independently of that mode,
the harness rejects every sample that observes a link/address/route mutation
after its sample monitor starts. The monitor is active before NaiveFox startup
or reference-browser startup, fails closed if its own process or netlink parser
fails, and drains queued events before confirming the sample boundary closed.

H3 packet-shape screening inside the private namespace also disables loopback
GRO, GSO, TSO, UDP segmentation, and GSO-list aggregation. The harness rejects
any proxy UDP frame whose captured UDP length exceeds 1500 bytes. Without this
step, Linux can expose one host-side UDP GSO superframe as a 13--20 KiB
"packet" even though it is segmented into ordinary QUIC datagrams before the
wire. Private run `909bdbd9c1d68824` was collected before this invariant was
added. Its arm rankings and packet-13--20 sequence are offload-contaminated and
must not be cited as wire-level camouflage evidence.

The first passive multi-arm attempt after that fix, private run
`5f45fb110cc57517`, exposed the same contamination in a second harness entry
point and was stopped after two samples. Its shell profile helper had inferred
"direct H3 reference" from the absence of a SOCKS port, which also describes
the private profile owned by the NaiveFox process. That entire run, including
its gate sample, is invalid and must be discarded. Profile creation now
requires an explicit `reference`, `naivefox`, or `socks-browser` role and
fails before capture if a NaiveFox/SOCKS profile contains either forced test
mapping preference or a pre-existing `AlternateServices.bin`. Only the direct
H3 reference role may contain the mapping.

The default reference is the pinned current-Nightly artifact already used by
the quick capture diagnostics, so it is a version-drift experiment. For a
same-base experiment, set `NAIVEFOX_CAPTURE_MODE=same-base` and the three
`NAIVEFOX_CAPTURE_REFERENCE_*` paths shown above. Same-base is the primary
stack-parity mode but is never built implicitly or made an ordinary merge
prerequisite.

Successful single-arm runs leave only `metadata.txt`, `features.csv`,
`metrics.json`, and `summary.txt` below the sanitized run directory. Multi-arm
runs instead retain `metadata.txt`, `features-superblocks.csv`,
`arm-comparison.{json,txt}`, and
`arms/<arm>/{features.csv,metrics.json,summary.txt}`. The paired arm report is a
model-free, within-`experiment_block` relative ranking. For every protocol and
passive feature view it measures bounded featurewise excess outside the common
Firefox A/B envelope, with feature scales derived only from Firefox control
disagreement. It reports workload-stratified paired-block bootstrap intervals,
paired sign-flip tests, and a Holm family-wise correction across eligible
comparisons. Arm labels are never classifier inputs and no arm-dependent
feature screening is performed.

Arm-specific classifier reports from a multi-arm run are explicitly
screening-only, even when 240 blocks were collected in `research` mode. They
retain descriptive metrics and refit uncertainty, but record
`supports_absolute_verdict=false`; all classifications and the overall
conclusion remain `INCONCLUSIVE`. Select a candidate with the paired report,
then preregister it and collect a fresh single-arm confirmation such as
`--mode research --naivefox-arm root`. Experimental
`tree-complete`, `tree-early-overlap`, `tree-root-overlap`,
`tree-warm-css-304`, and `tree-overlap` single-arm runs remain
screening-only, and
the fixed default superblock deliberately excludes them to bound collection
cost. Screening rows must not be reused as
confirmatory evidence.

This ranking is not an absolute camouflage verdict: the same Firefox A/B rows
define the envelope, so there is no independent third Firefox null observation.
Gate and smoke mode, fewer than 30 paired blocks per protocol, or any workload
with only one block are explicitly insufficient for paired inference. The
distance also gives equal weight to correlated engineered features and its
bootstrap intervals are conditional on the observed Firefox-only scales. These
limitations are recorded in both paired output files rather than left as an
interpretation convention.
Metadata includes platform, kernel, architecture, browser/product versions,
build identifiers, library hashes, capture-tool version, revision, mode, and
seed, making artifacts suitable for later regression history.

Current limitations are deliberate: localhost timing is useful only relative
to its Firefox baseline; smoke samples cannot support a camouflage conclusion;
current-Nightly differences may be version drift; platform fingerprints must
not be mixed; optional `tc netem`, persistent-profile and long-idle studies,
cross-version analytics and no-padding A/B remain separate research
extensions. Multi-arm superblocks provide the screening collection design but
cannot support an absolute camouflage conclusion; that requires a fresh,
preregistered single-arm research run. The suite selects explicit runtime
configuration but never mutates production defaults.

## Sensitive data handling

Raw packet captures, NSS key logs, copied profiles, screenshots, bodies, and
process logs are sensitive. Runners create them with private permissions below
the ignored object-directory fixture state. On success, they retain only safe
aggregates and delete private inputs. On failure, they print the private path
for local diagnosis; those files must never be committed or shared blindly.

Safe summaries may contain protocol identifiers, setting values, frame types,
stream identifiers, packet counts/length aggregates, hashes of build artifacts,
and header names. They must not contain credentials, `Proxy-Authorization`,
header values, TLS secrets, DATA payload, target bodies, or private profile
material.
