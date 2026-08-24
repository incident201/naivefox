# Firefox wire-behavior diagnostics

Capture comparison checks that NaiveFox continues to use Firefox's Necko,
NSS/PSM, and Neqo wire machinery without accidental project-specific markers.
It is diagnostic: a browser GET and padded proxy CONNECT are different
workloads, so packet timing and volume are not fingerprint-equality targets.

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

For H3, compare:

- QUIC version and negotiated `h3`;
- semantic ClientHello and client transport parameters;
- H3/QPACK settings;
- classic CONNECT rather than CONNECT-UDP or extended CONNECT;
- multiple CONNECT streams on one QUIC connection;
- no established TCP fallback at the strict H3 endpoint;
- padding negotiation and absence of synthetic markers.

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
through its private SOCKS listener. H2 and H3 are separate datasets. Strict H3
rejects established TCP or TCP payload.

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
  GREEN/YELLOW/RED research classification.

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
./run-camouflage-suite.sh --mode smoke --protocol h2
./run-camouflage-suite.sh --mode standard --protocol both --seed 20260824
./run-camouflage-suite.sh --mode research --protocol h3
```

The default reference is the pinned current-Nightly artifact already used by
the quick capture diagnostics, so it is a version-drift experiment. For a
same-base experiment, set `NAIVEFOX_CAPTURE_MODE=same-base` and the three
`NAIVEFOX_CAPTURE_REFERENCE_*` paths shown above. Same-base is the primary
stack-parity mode but is never built implicitly or made an ordinary merge
prerequisite.

Successful runs leave only `metadata.txt`, `features.csv`, `metrics.json`, and
`summary.txt` below `<objdir>/naivefox-fixture/camouflage-safe/<run-id>/`.
Metadata includes platform, kernel, architecture, browser/product versions,
build identifiers, library hashes, capture-tool version, revision, mode, and
seed, making artifacts suitable for later regression history.

Current limitations are deliberate: localhost timing is useful only relative
to its Firefox baseline; smoke samples cannot support a camouflage conclusion;
current-Nightly differences may be version drift; platform fingerprints must
not be mixed; optional `tc netem`, persistent-profile and long-idle studies,
cross-version analytics, no-padding A/B, and a test-only preamble experiment
remain separate research extensions. No
production padding, preamble, or network behavior is changed by this suite.

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
