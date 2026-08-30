# Local integration fixture

The fixture provides reproducible H2 and H3 testing without a real proxy
account. It builds pinned Caddy plus `forwardproxy@naive`, creates an isolated
per-run PKI and trusted/untrusted NSS profiles, starts deterministic HTTP/HTTPS
targets, and binds all fixture services to dynamically selected loopback ports.

Generated binaries, credentials, private keys, profiles, bodies, logs, and
captures live under `<objdir>/naivefox-fixture/`; none belongs in Git.

## Native classic/no-connect interoperability

The optional application transport has a separate fixture using the combined
Caddy module build. It creates private loopback PKI and configuration below
one existing product object directory, runs the rebuilt native executable, and
never starts a Firefox browser or rebuilds Caddy implicitly:

```bash
python3 netwerk/naivefox/test/integration/run-no-connect-tests.py \
  --objdir /absolute/path/to/warm-obj-naivefox-linux \
  --caddy /absolute/path/to/combined-caddy
```

Both transports use the same Caddy process for each protocol. The fixture checks
strict H2 and UDP-only H3, both local listeners, 1-MiB uploads/downloads,
backpressure, half-close, four parallel streams, idle wake, abrupt local
cancellation, bounded graceful shutdown, rejected application keys, exact target
allowlists and untrusted certificates. It records zero outer
CONNECT during the no-connect phase and then exercises classic CONNECT on that
same endpoint. Classic uses an explicit disabled preamble so this gate measures
transport interoperability, not the separate fronting-page contract.

Use `--protocol h2` or `--protocol h3` for a focused iteration. `--runtime`
selects an already-built Linux executable; omission uses `dist/bin/naivefox`
inside `--objdir`. Per-run configs, certificates and logs remain private;
`result.json` contains only sanitized gate results. Retained failed runs are
not passing evidence. Server build instructions and the maintained protocol
boundary are linked from [NO-CONNECT.md](../../NO-CONNECT.md).

Run the separate fail-closed HTTP-envelope gate with the same arguments:

```bash
python3 netwerk/naivefox/test/integration/run-no-connect-adversarial-tests.py \
  --objdir /absolute/path/to/warm-obj-naivefox-linux \
  --caddy /absolute/path/to/combined-caddy
```

It rejects mismatched profiles, appended/oversized cells, wrong capacities,
truncated filler, invalid sequence/reserved fields, redirects, HTTP authentication
prompts and cross-protocol fallback. The corrupt responses come from isolated
Caddy test routes; production server code is not modified. `--case` narrows a
run to one or more named cases, and `--protocol` selects H2 or H3. Each refusal
must reach a local connection failure without an outer CONNECT, redirect follow,
authentication retry or target open after rejected bootstrap.

The Windows adapter runs the same transfer and rejection matrix with the actual
staged Windows executable and Windows local clients:

```bash
python3 netwerk/naivefox/test/integration/run-no-connect-windows-tests.py \
  --objdir /absolute/path/to/warm-obj-naivefox-windows \
  --runtime /absolute/path/to/staged/naivefox.exe \
  --caddy /absolute/path/to/combined-linux-caddy \
  --windows-python '/mnt/c/absolute/path/to/python.exe'
```

Run the adapter as root in WSL. It creates its own isolated network namespace,
uses a loopback relay for the private Caddy/target fixture, and owns the Windows
client process through a kill-on-close Job Object. All fixture state remains
below the supplied object directory; no existing network-namespace wrapper is
needed. `--protocol h2` or `--protocol h3` narrows an iteration.

## Complete local gate

From the repository root, run:

```bash
./netwerk/naivefox/test/integration/run-full-suite.sh
```

The command runs H2, H3, Auto, config/listener, failure-path, padding,
integrity, backpressure, lifecycle, quick capture, and the structural passive
camouflage gate sequentially with the same NaiveFox binary. Strict H3 uses a
UDP-only Caddy proxy port with no TCP listener, and therefore cannot pass by
falling back to H2. The two-sample camouflage gate validates collection,
analysis, and sanitization; it never makes a statistical camouflage claim.

Protocol-only aggregate suites are:

```bash
./netwerk/naivefox/test/integration/run-local-suite.sh
./netwerk/naivefox/test/integration/run-h3-suite.sh
```

The H2 aggregate includes control, scoped trust, raw CONNECT, SOCKS, padding,
robustness, and H2 capture. The H3 aggregate includes raw CONNECT, SOCKS,
padding, robustness, Auto, and H3 capture. Config/runtime behavior is added by
`run-full-suite.sh`.

## Focused gates

Run the smallest relevant script while developing, then finish with the
applicable aggregate:

| Behavior | Commands |
|---|---|
| Fixture module, auth, ACL, TLS, profiles | `run-control-tests.sh` |
| Necko/NSS trust and hostname validation | `run-necko-tests.sh` |
| Temporary `SSL_CERT_FILE` trust and non-persistence | `run-ssl-cert-file-tests.sh` |
| Marker-free raw CONNECT | `run-raw-connect-tests.sh`, `run-h3-raw-connect-tests.sh` |
| SOCKS remote DNS and tunnel shutdown | `run-socks-tests.sh`, `run-h3-socks-tests.sh` |
| Padding negotiation and transfer integrity | `run-padded-tests.sh`, `run-h3-padded-tests.sh` |
| Backpressure, half-close, loss, concurrency | `run-robustness-tests.sh`, `run-h3-robustness-tests.sh` |
| Strict H3 and establishment-only H2 retry | `run-auto-protocol-tests.sh` |
| Simultaneous SOCKS/HTTP config listeners | `run-h2-config-tests.sh`, `run-h3-config-tests.sh` |
| Profile and logging policy | `run-config-runtime-behavior-tests.sh` |
| Malformed local requests | `run-malformed-socks-tests.sh` where present |
| Passive dataset/analyzer structure | `run-camouflage-self-tests.sh`, `run-camouflage-suite.sh --mode gate --protocol both` |

Robustness covers deterministic large uploads/downloads, bounded slow
producer/consumer behavior, request half-close followed by response, local and
target close, authentication/ACL/timeout failures, proxy loss, and concurrent
CONNECT streams. The concurrency checks require one pooled outer TCP connection
for H2 or one NaiveFox-owned UDP socket for H3.

## Staged runtime

After staging the Linux product, the verifier runs the package from outside the
source and object directories:

```bash
./netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64
./netwerk/naivefox/tools/verify-staged-runtime.sh naivefox-linux-x86_64
```

Focused config/padding runners accept a staged launcher through
`NAIVEFOX_RUNTIME`; the verifier also supplies `NAIVEFOX_EXPECT_RUNTIME_DIR` and
rejects source/object-directory mappings. It exercises both adjacent
no-argument `config.json` and an explicit positional config with inherited
loader and TLS-keylog variables removed.

Windows packages are checked with the staged Windows verifier and native
PowerShell/Python smoke/soak tooling on the `naivefox-full-source` branch. The native gate
must include valid CONNECT churn and repeated channel-stop/lifecycle activity;
a launch-only check is insufficient.

When the host has the supported naivefox-arm64-api27-raw AVD installed but no
device is running, append --start-emulator. The runner invokes
tools/start-android-emulator.sh, supplies the ARM64-safe -qemu -machine virt
launch override, waits for boot completion, and shuts down only the emulator
instance it started during cleanup.
On WSL the managed emulator and image are Linux-local, discovered under
`${XDG_DATA_HOME:-$HOME/.local/share}/naivefox/`; the launcher does not silently
fall back to a Windows installation. Run adb, the emulator and the fixture in
one isolated namespace. See [the managed emulator setup](../../MINIMAL.md).
Boot readiness requires an Android boot-completed property, not just a stopped
animation; the latter can precede clock and networking initialization.

The Android ARM64 embedded package has a test-only native harness and emulator
gate. It compiles the harness with NDK r29, relocates the package below
`/data/local/tmp`, loads `libxul.so` through the public C ABI, and verifies H2
and strict H3 through both local frontends:

```bash
NAIVEFOX_OBJDIR=/absolute/path/to/obj-naivefox-android-aarch64 \
./netwerk/naivefox/test/integration/run-android-embedded-tests.sh
```

H2 reaches the loopback fixture through `adb reverse`. H3 performs a UDP
preflight and reaches the same loopback-only fixture through the emulator host
alias `10.0.2.2`; set `NAIVEFOX_ANDROID_HOST_ALIAS` for an equivalent CI
network. The fixture certificate gains that IP SAN only for this test, and the
trusted CA remains confined to the pushed test profile. The gate checks
download/upload integrity, an active connection during cross-thread stop,
listener closure, runner return, and crash-free XPCOM shutdown. It fails when
no ARM64 API-26+ device is available. `--allow-skip-device` explicitly permits
a non-acceptance static run, while `--check-only` only builds and inspects the
harness.

## Real Caddy interoperability

Run real-deployment checks only after the local gate passes. Supply secrets
through the environment; never put them on the command line, in a committed
config, or in logs:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
./netwerk/naivefox/test/integration/run-real-server-tests.sh
```

Config-mode verification for a staged runtime is:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
NAIVEFOX_REAL_RUNTIME=/absolute/path/to/staged/naivefox \
./netwerk/naivefox/test/integration/run-real-server-config-tests.sh h2

# Replace h2 with h3 for a strict quic:// config.
```

Bounded stability gates are available as `run-real-server-soak.sh` and
`run-real-server-h3-soak.sh`. They preserve public certificate validation,
exercise integrity and parallel requests in one process, and retain only a
credential-free summary.

`../../tools/fetch-naiveproxy-reference.sh` downloads and verifies the pinned
official client. `run-reference-server-tests.sh` applies the comparable bounded
workload; this is a behavioral reference, not a target for copying
Chromium-specific wire shaping.

## Capture diagnostics

The capture commands are:

```bash
./run-capture-comparison.sh
./run-h3-capture-comparison.sh
./run-observer-comparison.sh
./run-camouflage-suite.sh --mode smoke --protocol both
```

Their default quick mode does not build Firefox. A full ordinary Firefox build
is permitted only for an explicitly requested same-base diagnostic and is never
part of the product gates. Requirements, environment variables, comparison
semantics, WSL packet handling, and sensitive-data rules are in
[`../../CAPTURE.md`](../../CAPTURE.md).

`run-firefox-repeat-navigation-diagnostic.sh` is a same-base, reference-only H3
control for separating first-navigation startup work from the normal Firefox
resource-discovery lifecycle. It prelaunches one Firefox instance and captures
eight sequential `browser_page` navigations by default in the same tab and
content process. `NAIVEFOX_REPEAT_NAVIGATION_COUNT` can raise the count up to 32
but cannot reduce it below eight. Every navigation gets a different random token
in the root URL and every asset URL.
The analyzer rejects cache reuse, conditional requests, non-200 resources,
changed request semantics or response sizes, a second QUIC identity or
ClientHello, network mutation, and ambiguous lifecycle mappings. Its safe
summary reports `root response HEADERS -> first CSS GET` and the parent/channel,
HTML5 parser, child-to-parent IPC, transaction-dispatch, H3 `AddStream`, and wire
sub-intervals for each navigation. It also reports steady-state spread, the
first-navigation delta, and Pearson correlations between total root-to-CSS time
and each exhaustive non-overlapping lifecycle component. The private capture,
key log, browser identity and Mozilla logs are deliberately retained under
`h3-captures`; the sanitized summary is written under the matching
`h3-capture-safe` directory. Run it only with
`NAIVEFOX_CAPTURE_MODE=same-base` and the same
`NAIVEFOX_CAPTURE_REFERENCE_*` inputs used by the other same-base diagnostics.

Quick capture downloads the pinned official Nightly/CI binary from the URL in
`../../tools/firefox-reference-manifest` and compares its observed behavior
with the same fixture run through NaiveFox. Prefer immutable Taskcluster URLs
and verify task revision routes and application metadata when refreshing the
manifest. The fetch script verifies the archive checksum before extraction or
execution, then verifies the Firefox version; a version string alone does not
establish same-base provenance.

Optional throughput scripts (`run-throughput-benchmark.sh` and
`run-h3-throughput-benchmark.sh`) produce local diagnostics; their point-in-time
numbers are not maintained in active documentation.

`run-h2-capture-comparison.sh --arm root` is the hardened same-base decrypted
H2 sequence check. It uses the isolated namespace and mutation monitor,
requires healthy loss-free capture, verifies one TCP/H2 connection, `h2` ALPN,
equal client SETTINGS, padding, and per-stream preamble GET/CONNECT ordering,
then exports only a sanitized event sequence with relative timing and a
summary. Its default keeps both reference and inner Firefox on an explicitly
recorded cold command-line start after capture begins. Select
`--browser-backend selenium` to pre-launch both browsers, wait for controller
readiness before capture, and navigate only after capture starts. The selected
startup cohort is recorded in safe metadata and the runner never silently
mixes the two contracts. The pre-launched option is restricted to the
`browser_page` reference; the fixed `fronting_page` resource-tree comparison
continues to use the command-line cohort.
Tree admission privately verifies the document plus same-origin CSS/JS
`Referer`, `Sec-Fetch-*`, exact document `Priority: u=0, i`, and naturally
computed resource `Priority: u=2` semantics;
the safe output contains only boolean results, never those header values.
`document-start-overlap` is also available in this H2 diagnostic. Its runtime
gate requires one admission/result/drain/CONNECT-established lifecycle on the
same NaiveFox connection. Its decrypted gate requires document GET before
CONNECT, HTTP 2xx, and normal H2 END_STREAM, but does not require END_STREAM to
fall on either side of CONNECT; request commit is the product admission cause.
`tree-native-parser-document-start-resource-tree` is the bounded H2-only
fronting-page treatment. Reference Firefox and the outer NaiveFox preamble use
the same `fronting_page`, while the browser behind SOCKS independently runs the
same block-wide `browser_page` workload. Private admission requires exactly one
root, stylesheet, classic deferred script, and image request in source order on
one TLS/H2 connection, with matching same-base Firefox request semantics and
normal END_STREAM completion. The three fixed fixture assets are inputs to a
normal small page, not packet-size targets.
The callback used by `tree-early-overlap` can release CONNECT while a resource
is live yet still lose the wire-level race to that resource's END_STREAM. The
decrypted check intentionally rejects such a run. Do not keep retrying until
it passes or use the arm for passive causal screening while this outcome is
nondeterministic, because that would select samples by their observed
scheduling.

`tree-root-overlap` tests a different and deterministic client-side cause. The
normal root parser starts the same CSS/JS channels, root completion is observed,
and CONNECT is admitted when at least one resource `AsyncOpen` succeeded,
without consulting resource response HEADERS or FIN. The operation remains
owned and drains after CONNECT. A zero-resource terminal tree falls through to
CONNECT immediately rather than waiting for a timeout. The runner requires the
safe `root_done=1 started_resources=2` marker and then a distinct normal
`drain=complete completed_resources=2` marker before ending capture or running
any passive analysis. The latter is emitted only when both opened assets have
response headers, HTTP 2xx, and successful completion. Thus a synchronous
failure to open the second fixture asset is rejected even though the generic
production barrier remains valid with one started asset. Its bounded watchdog
only invalidates the sample. H3 decrypted use
must select it together with `tree-complete` so the same run proves equal
request semantics and asset sizes.

That request-scheduling order does not guarantee wire overlap. H2/H3 response
FIN may already have won the transport race. Decrypted output reports the
observed order only; it never rejects `tree-root-overlap` for lacking
HEADERS/FIN overlap, and samples must not be selectively retried. Private
semantic validation still requires root completion before CONNECT, while the
production normal-completion marker accounts for every expected CSS/JS
response. Asset FIN is report-only because a fully consumed known-length H3
response may use `H3_REQUEST_CANCELLED`. H3 additionally requires its selected
request semantics and asset sizes to match the paired `tree-complete` arm
exactly. H2 uses the same fixture/config and validates expected request
semantics, but does not claim paired asset-size equality.

Two harness-only aliases isolate resource count without another product build.
`tree-complete-css` maps to production `tree-complete` and
`tree-root-overlap-css` maps to production `tree-root-overlap`; both set
`max-assets=1`, yielding root plus the first CSS request. The ordinary modes
remain at two assets. Passive validation requires exactly one started and one
successfully completed resource. H3 decrypted comparison requires the aliases
as a pair and proves one physical QUIC connection, equal root/CSS selected
request semantics and CSS `Content-Length`, and root FIN before CONNECT. Any
observed CSS FIN ordering relative to CONNECT in the overlap alias is
report-only.

`tree-warm-css-304` is a separate cache-mechanism diagnostic. It maps to
`tree-root-overlap` with one CSS resource and `cache-resources=true`. For every
participant the runner creates a fresh temporary profile, performs one private
warm navigation before capture, restarts Firefox/NaiveFox with that same
profile, and captures only the measured navigation. The profile is never
shared between participants or samples and is deleted with the private run.
Only Caddy is restarted between the warm and measured phases, reusing the same
origin, port, certificate, config, target server, and request journal. This
removes the previous server-side QUIC session deterministically. The separate
`tree-root-overlap-css` cold control receives the same pre-measure Caddy reset.
The target journals only CSS cache evidence and the runner fails closed unless
the warm phase is an unconditional 200 with a stable ETag and the measured
outer request is a Gecko-generated `If-None-Match` followed by 304. For
NaiveFox it additionally requires the fresh inner browser's CSS request to
remain an unconditional 200. No conditional header is injected by the
harness. The arm is limited to single-arm H3 gate/smoke diagnostics and cannot
be used for confirmation; `tree-root-overlap-css` is its cold control.
Persistent TLS token storage and H3 0-RTT are disabled for the two reused outer
profiles, and the runner rejects token-cache files, extra physical QUIC
identities, extra ClientHellos, or any measured 0-RTT packet. The network
mutation monitor spans warm-up, controlled shutdown, restart, and measured
capture. On failure the untrimmed pcap is preserved with private diagnostics,
while passive extraction begins at the first measured client Initial. Only a
server-only tail before that point is admissible; client traffic before it or a
different endpoint UDP 5-tuple after it is a hard failure, even when Wireshark
cannot decode that payload as QUIC. Exact
Referer/authority/Accept/fetch-metadata/Priority semantics of
the measured conditional CSS must match among the block's Firefox A, Firefox B,
and NaiveFox participants. Run the cold control separately under the same
fixture revision and build; warm/cold comparison remains descriptive rather
than a shared-reference paired result.

For a response-volume causal control, set
`NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE` and
`NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE` before the suite. Defaults are 64 KiB
and 128 KiB; the fixture accepts only 1 KiB through 4 MiB and records the exact
values in sanitized metadata. This changes the controlled server response
profile without changing the production preamble mode, request classes,
priorities, or resource count. Sizes must be chosen before capture from a
declared content profile, never from packet indices.

`root-pmtud-control` is a separate H3-only harness arm for the PMTUD policy.
It maps to the same production `document-complete` preamble as `root`, while
only its NaiveFox profile sets `network.http.http3.pmtud=true`. The ordinary
`root` profile leaves the preference unset. Multi-arm passive screening thus
compares both policies with one binary and the normal socket. The paired H3
decrypted check requires one physical QUIC connection, one complete root GET,
and equal selected request semantics and response `Content-Length`; it records
no wire PMTUD claim.

The dedicated H3 comparison uses the identical inner HTTPS URL for every
NaiveFox arm, while the direct reference loads the same path and query on the
outer fixture. Firefox must exit successfully and produce a nonempty screenshot
in every cohort. Every preamble GET must have a successful response and
`Content-Length`, while the root must have an observed FIN before CONNECT.
Asset FIN is not a universal application-completion predicate: upstream
Firefox may close a fully consumed, known-length H3 response with
`H3_REQUEST_CANCELLED`. Root semantics and size are checked for every arm and
against `tree-complete` whenever it is selected.
`tree-overlap`, `tree-early-overlap`, and `tree-root-overlap` are admitted only
beside `tree-complete`, including exact asset-size parity. Only
`tree-root-overlap` has an existing deterministic normal-drain runtime marker;
the other overlapping modes therefore cannot be selectively retried when an
available decrypted ordering predicate is absent. Asset FIN is not treated as
a universal proof of application completion. Until exact H3 DATA-byte
accounting is implemented, `tree-early-overlap` and `tree-overlap` are not
admissible for a strict whole-volume conclusion; `tree-root-overlap` supplies
the deterministic all-resource drain marker required for that comparison.

`run-h2-connect-priority-comparison.sh` is a separate same-base causal
diagnostic for the first SOCKS tunnel. It compares an ordinary top-level HTTPS
navigation through the fixture's existing authenticated H2 forward proxy with
NaiveFox default behavior and the opt-in
`diagnostic-first-socks-tunnel-urgent-start` behavior. A private privileged
Marionette controller installs an exact-target `nsIProtocolProxyChannelFilter`
before navigation. The filter constructs Firefox's normal HTTPS proxy info and
supplies the fixture Basic header preemptively, so the production Caddy route
is unchanged and a 407 retry cannot contaminate ordering. All three browsers
are ready before capture and navigate only after capture begins.

Admission requires one physical TCP/H2 identity and one outer ClientHello per
cohort, equal same-base TLS and SETTINGS semantics, no 407, successful classic
CONNECT, the expected Naive padding distinction, and non-empty scheduling
evidence. The first-CONNECT packet is accepted only with exactly one relevant
HEADERS occurrence/method/stream and exactly one priority-flag, dependency, and
weight field set; coalesced extra HEADERS or PRIORITY evidence fails closed.
Both fresh NaiveFox logs must have zero diagnostic markers before capture.
After workload, default must still have zero and the diagnostic arm exactly one
`Connection 1` H2 applied marker before the first CONNECT-established log
evidence. Safe output exposes only pass/fail booleans for that check. Private
decrypted H2 fields compare the first CONNECT's native scheduling signature and
observe whether a `Priority` header name is present.
A valid capture produces one of three mechanism verdicts: `native-match` when
the urgent arm alone matches Firefox and has compatible Priority-header
presence; `wire-null` when all three scheduling signatures and header-presence
states match; or `native-mismatch` for every other valid relationship. Negative
verdicts are results, not infrastructure failures, and successful cleanup
deletes their raw data. Only the verdict, equality/presence booleans, header
names, relative ordering, and source/build hashes enter safe output; no header
value, credential, authority, port, profile, browser-driver log, NSS secret, or
capture survives a valid run. This diagnostic is not a passive classifier and
must not be included in camouflage datasets.

The current same-base Caddy H2 run is `wire-null`: both NaiveFox variants match
proxied Firefox's observable CONNECT scheduling and all three omit a CONNECT
`Priority` header. Consequently the opt-in setting is research instrumentation,
not a camouflage recommendation, and it must not be promoted to a default or
screened passively without a different peer or tunnel-adapter implementation.

In same-base mode, `run-h3-capture-comparison.sh --compare-arms` performs a
private decrypted sequence audit of `off`, `gate`, `root` (the
`document-complete` alias), `tree-complete`, and `tree-overlap`. The
experimental `tree-early-overlap` arm is available only by explicitly
selecting it together with `tree-complete`. Repeat `--compare-arm ARM` to
select a candidate set. The audit drives the
same browser-page document and resources for the reference, preamble, and inner
HTTPS workload. Sanitized output retains request ordering, observed stream-FIN
ordering, and whether a response header or observed FIN followed CONNECT; it
never retains header values or key material. A transient private CSV contains
only GET header blocks so the complete/overlap root, stylesheet, and script can
be checked for equal selected request values and order. For
`tree-early-overlap`, another private extract checks that its two response
`Content-Length` values equal `tree-complete`. Both are deleted with the
capture staging directory; safe output records only equality booleans such as
`tree_request_semantics_match=yes`,
`tree_early_overlap_request_semantics_match=yes`,
`tree_early_overlap_asset_sizes_match=yes`, and
`tree_expected_request_semantics=yes`. The latter requires a navigation-style
root request and same-origin stylesheet/script subresource semantics, including
referers exactly equal to the root URL computed from its private pseudo-header
values, without exporting any of those values.

The controlled browser page serves a 64 KiB stylesheet and a 128 KiB script.
The root document plus those two discovered assets stays below the tree
preamble's 256 KiB aggregate limit. These ordinary resource-sized bodies are
shared by direct Firefox, the preamble traversal, and the inner browser
workload. Their causal purpose is to keep normal resource response streams
alive long enough for `tree-overlap` or `tree-early-overlap` to overlap CONNECT
on loopback; their
contents and sizes are fixed workload parameters, not fitted packet patterns.
The origin and Caddy route intentionally apply no `Content-Encoding`; enabling
gzip or zstd would collapse the repeated fixture body on the wire and invalidate
this overlap workload.

## Passive camouflage dataset

`run-camouflage-suite.sh` builds separate H2 and H3 datasets that actively try
to classify Firefox versus NaiveFox. It reuses the normal fixture, browser
reference download, private capture staging, profile handling, strict-H3
checks, sanitization, and cleanup. Seeded complete blocks collect two
independent Firefox cohorts and one NaiveFox cohort through the same endpoint,
Caddy, certificate, IP, port, namespace, and interface.

All cohorts use the selected reference Firefox to execute the controlled page.
The NaiveFox cohort configures that browser to use the sample's private SOCKS
or HTTP CONNECT listener. Its target uses HTTPS inside CONNECT by default; the
fixture CA is trusted only by the isolated test profile. `--inner-transport
http` selects a separate cleartext diagnostic dataset. Forced Alt-Svc applies
only to the direct H3 reference browser; the proxied browser leaves origin H3
disabled while NaiveFox independently owns the selected outer H2 or H3
transport. A fail-closed PAC sends only the exact loopback workload authority
to the sample listener and every non-loopback hostname to a dead local proxy.
Other loopback ports remain direct so Selenium's local remote-control channel
cannot create a pre-capture NaiveFox flow; the isolated namespace prevents
those local control connections from escaping to the host network. A Selenium
controller is preferred for H2 when available. Same-base H3
multi-arm screening requires Selenium and launches Firefox before capture;
the command-line backend is rejected because its readiness marker precedes
Firefox process startup. Direct H3 reference participants first complete an
uncaptured HTTPS warmup on the distinct `127.0.0.1` origin and return to
`about:blank`. This initializes the same browser process and Alt-Svc storage
without warming the measured `localhost` origin or contacting the measured H3
proxy port. The command-line backend remains only the dependency-free H2
fallback. The target records the browser's completion POST in a private file;
the controller watches that file without adding an out-of-band network flow.
The runner stops and validates `dumpcap` before shutting down the browser or
NaiveFox, rejects capture drops, and rejects H2/H3 flows whose client SYN or
Initial was not captured.
For passive H2 datasets it starts the outer Caddy listener with only `h2`
enabled and requires exactly one proxy-port TCP identity, one client SYN, and
one visible ClientHello. Because TLS 1.3 encrypts the server-selected ALPN,
successful workload completion against that H2-only listener is the keyless
selection contract; the runner does not confuse ClientHello advertisement
with negotiated ALPN. The optional inner H2 origin uses a different port and
is excluded from the outer identity.

Use `--h2-proxy-floor-superblocks` for the fixed same-base `browser_page`
proxy-floor experiment. Every randomized block contains direct Firefox A/B,
native-proxied Firefox, and NaiveFox `off`. Each participant uses a fresh
profile and a Selenium browser that is ready before capture; navigation starts
after capture readiness, the block shares one wire completion token while its
local marker is reset before every participant, and all captures stop at
completion plus 250 ms. Native-proxied Firefox and NaiveFox must independently
pass the fixture's inner HTTPS/H2 access-log validator. No sample is retried
because of its observed packet sequence.

The existing multi-arm analysis reports `firefox-proxied` and `off` against
the common direct Firefox A/B noise floor for packets 1--16, 17--32, 1--32,
the first 250 ms, and the whole flow. `firefox-proxied` is an analysis-only
candidate arm; its internal `label=naivefox` value is merely the legacy
candidate slot required by the feature schema.

`--h2-request-timing` adds a separate, sanitized outer/inner request timeline
to one canonical H2 diagnostic. It requires isolated gate/smoke `browser_page`
superblocks, inner HTTPS/H2, pre-launched Selenium, and exactly the two current
H2 listener arms. Resource-size, preload, padding, and shaped-link variants are
not admitted. With the usual same-base binary environment already set:

```bash
NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1 ./run-camouflage-suite.sh \
  --mode gate --protocol h2 --inner-transport https-h2 \
  --scenario browser_page --samples-per-cohort 1 --seed 2026083074 \
  --multi-arm-arms document-first-buffer-task-overlap,document-first-buffer-http-connect \
  --multi-arm-views packets_17_32,whole --h2-request-timing
```

`h2-request-lifecycle/*.json` contains fixed event labels, relative intervals,
handler durations, and response byte counts only. Per-sample access-log offsets
exclude earlier participants; missing requests, unexplained duplicates, wrong
protocols, unexpected authorities, invalid timestamps, or ambiguous navigation
identities fail closed. The canonical fourth image returns 34-byte JSON and
may be fetched twice by Firefox; one such extra image attempt and one empty
favicon request are explicitly counted and retained as separate events.
Other duplicates, a third API attempt, or changed API MIME/size are rejected.
CONNECT records are read after browser/product shutdown, outside
the primary capture. Caddy writes an access record when its handler ends, so
request start is estimated as `log timestamp - handler duration`. These are
coarse millisecond-scale server intervals, not exact wire or Necko timestamps.
They never enter passive feature CSVs, distance calculation, or inference.
Private access-log slices are deleted with the capture after success and
retained only in the private diagnostic directory on failure. Product, Caddy,
page contents, and capture cutoff remain unchanged.

The controlled workloads are cold initial, browser-page navigation, warm
sequential, burst/concurrent streams, bulk download, bulk upload,
bidirectional, and idle/resume. Features include only passive-visible packet or
datagram direction, lengths, timing and bursts, TLS records, public handshake
capabilities, TCP SYN/lifecycle/recovery state, and QUIC Initial/CID/phase plus
strict-endpoint TCP probe state. Ports, process information, absolute
timestamps, paths, labels, HTTP
plaintext and decrypted protocol state are rejected from classifier input.

The dependency-free analyzer uses grouped workload-stratified cross-validation
with train-only preprocessing, a regularized logistic classifier, a separate
Firefox-A-versus-Firefox-B baseline, conditional diagnostic intervals, full
pipeline refit bootstrap for research verdicts, permutation tests, coefficient
importance, and leave-one-workload-out checks.
It reports whole, initial packet/time, steady-state, and lifecycle views as raw
JSON plus a human-readable summary. Orientation-fixed AUC drives the verdict.
`D = max(AUC, 1 - AUC)` remains a diagnostic that makes an inverted classifier
visible without changing policy.

```bash
./run-camouflage-self-tests.sh
./run-camouflage-suite.sh --mode gate --protocol both
./run-camouflage-suite.sh --mode gate --protocol both --inner-transport http
./run-camouflage-suite.sh --mode smoke --protocol both
./run-camouflage-suite.sh --mode standard --protocol both
./run-camouflage-suite.sh --mode research --protocol both
```

Gate uses two, smoke ten, standard sixty, and research 240 samples per cohort
and protocol. Override with `--samples-per-cohort` only for a deliberate local
experiment; research mode has a hard minimum of 240 and rejects smaller
overrides. Gate and smoke are always `INCONCLUSIVE`; only a non-screening
research run with at least 240 samples in every cohort and protocol applies the
documented GREEN/YELLOW/RED policy. Default quick-reference runs measure
drift against pinned current Nightly. Set `NAIVEFOX_CAPTURE_MODE=same-base` and
the `NAIVEFOX_CAPTURE_REFERENCE_*` paths described in `../../CAPTURE.md` for an
explicit same-source experiment; no Firefox build is started automatically.
Run HTTPS and HTTP with the same explicit seed when comparing the primary and
diagnostic inner transports; each invocation produces its own dataset and
records `inner_transport` in metadata.

`--scenario NAME` repeats one controlled workload across the requested blocks.
It is useful for private lifecycle diagnostics such as stressing `sequential`
connection reuse; the selected scenario is recorded in sanitized metadata and
does not relax any capture-health or sample-count rule.

For a predeclared inner browser-workload resource-size robustness check, combine
`--scenario browser_page` with `--browser-page-base-size BYTES` (65536 through
4194304).
The base scales the six ordinary page assets in the same 1/4, 1/2, 1,
1/64 profile as the default 262144-byte fixture. Omitting the option preserves
the established fixture exactly. The selected base is recorded in sanitized
metadata; it is a diagnostic input and must not be tuned to a packet window.
When a dense fronting-page arm replaces the outer scenario, this option still
scales the inner tunneled workload only. The fixed outer origin profile and
the completed independent outer-size campaign are documented in
[`../../FRONTING-PAGE.md`](../../FRONTING-PAGE.md).

Use `--outer-resource-unit-size BYTES` only with a dense H3 fronting-page arm
to scale the actual outer resources independently of the inner workload. The
accepted unit is 1024 through 22000 bytes. CSS, JavaScript, and each of four
valid SVG image bodies then use `3/6/2/2/2/2` units respectively, so their
aggregate excluding the small root is exactly 17 units and remains below the
384-KiB product budget. Omitting the option preserves the historical measured
fixture exactly, including its 34-byte JSON fourth image response. Sanitized
metadata records the selected profile, each body size, and the aggregate.
Before network shaping or capture begins, the runner downloads all six bodies
from the isolated target and fails closed unless their actual byte counts and
MIME types match that metadata; the number of validated protocol fixtures is
also recorded. The predeclared 17/68/272-KiB unshaped screen and shaped
17/272-KiB endpoint screen, including safe artifact IDs and all five default
views, are recorded in
[`../../CAPTURE.md`](../../CAPTURE.md#predeclared-outer-resource-size-campaign).
Those four-block results are descriptive robustness evidence, not an
acceptable-size equivalence claim or a reason to tune fixture bytes after
observing a packet window.

For an isolated link-robustness check, add `--network-one-way-delay-ms N`,
`--network-rate-mbit N`, or both. Network shaping is rejected unless
`NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1`; after the fixture is ready, the runner
installs and verifies one loopback `netem` qdisc before any participant runs.
The same profile applies to both Firefox controls and every NaiveFox arm. A
shaped capture keeps the receive-side cooked-packet copy because its timestamp
is after netem; the usual transmit copy is tapped before shaping. Requested
values, application count, and copy policy are recorded in sanitized metadata.

To diagnose a NaiveFox lifecycle race without collecting flaky direct-Firefox
reference samples, set `NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY=1`. This
private mode is restricted to gate/smoke, requires exactly one explicit
`--scenario` and one explicit `--naivefox-arm`, and rejects multi-arm designs.
It collects only the requested number of NaiveFox samples while retaining the
strict per-sample transport and arm validator:

```bash
NAIVEFOX_CAPTURE_DIAGNOSTIC_NAIVEFOX_ONLY=1 \
NAIVEFOX_CAPTURE_PRIVATE_H3_KEYLOG=1 \
./run-camouflage-suite.sh --mode smoke --protocol h3 \
  --scenario sequential --naivefox-arm tree-complete \
  --samples-per-cohort 10
```

No classifier, comparison, or verdict is run in this mode. A successful run
deletes pcaps, extracted features, logs, and key logs, leaving only a sanitized
summary with sample counts, arm, scenario, and protocol. A failed run preserves
the private directory for lifecycle analysis.

For a private H3 lifecycle diagnosis only, set
`NAIVEFOX_CAPTURE_PRIVATE_H3_KEYLOG=1` in gate/smoke mode. The NaiveFox process
then writes TLS secrets beside that sample's raw pcap; normal passive runs
still unset `SSLKEYLOGFILE`. Key logs never enter feature extraction or safe
output, are deleted after success, and survive only with failed private
diagnostics so GOAWAY/CONNECTION_CLOSE/RESET state can be checked.

For controlled same-base experiments on a host whose WSL mirrored networking
inherits VPN or adapter churn, set `NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1`. The
runner re-executes itself in a private Linux network namespace containing only
loopback and a stable TEST-NET dummy default route. Caddy, NaiveFox, both
Firefox participants, and packet capture all remain in that namespace; the
Windows host, its VPN, and other WSL processes are untouched. This mode is
restricted to same-base runs so it cannot unexpectedly block reference
downloads.

Every sample completes its fixture cold reset and namespace convergence first,
then starts a route-netlink mutation monitor before either the reference
browser or the NaiveFox process. Any link, address, or route add/delete from
that measurement boundary through the end of capture invalidates the sample.
Stopping uses a drain-and-confirm handshake so an event already queued
by the kernel cannot be lost at the right boundary. An early monitor exit,
truncated/error netlink input, missing completion confirmation, or non-empty
event log fails closed. The private log stores only event type,
sequence/process metadata, flags, and elapsed time; it never records interface
names, addresses, gateways, or routes. A stable isolated namespace should
therefore produce an empty mutation log for every sample.

When a failed sample contains both `capture.pcapng` and `naivefox.keys`, inspect
only the original outer connection before the second physical ClientHello with:

```bash
python3 ./analyze-private-h3-lifecycle.py \
  --pcap PRIVATE_SAMPLE/capture.pcapng \
  --keylog PRIVATE_SAMPLE/naivefox.keys \
  --proxy-port OUTER_PROXY_PORT
```

The private-only report includes H3 GOAWAY, QUIC CONNECTION_CLOSE,
RESET_STREAM, and STOP_SENDING positions. It deliberately omits headers,
request targets, connection IDs, and secrets. It refuses to infer that GOAWAY
was absent unless H3 frames from the first connection were actually decrypted.

`--protocol h2 --scenario browser_page --document-body-size BYTES` is a
gate/smoke-only site-envelope diagnostic. It pads the exact shared outer,
direct-reference, and tunneled HTML response body to 1024--65536 bytes without
changing its resource URLs or completion behavior. Omitting the option keeps
the ordinary fixture body. Sanitized metadata records the selected size; this
option never changes a product or Caddy default.

`--naivefox-arm off|gate|root|root-pmtud-control|document-complete|document-carrier-dispatch|document-cold-winner-handoff|document-native-cache-open|document-handshake-confirmed|document-overlap|document-start-overlap|tree-complete|tree-complete-css|tree-early-overlap|tree-resource-committed-overlap-css|tree-resource-native-cache-committed-overlap|tree-native-parser-preload-overlap-css|tree-native-parser-document-start-overlap-css|tree-native-parser-document-start-resource-tree|tree-native-parser-document-start-navigation-stop-css|tree-native-parser-document-start-response-stop-css|tree-native-parser-document-handoff-overlap-css|tree-native-parser-retarget-overlap-css|tree-native-parser-ipc-rendezvous-overlap-css|tree-native-parser-root-rendezvous-overlap-css|tree-native-parser-process-overlap-css|tree-native-parser-full-process-overlap-css|tree-root-overlap|tree-root-overlap-css|tree-warm-css-304|tree-overlap`
selects a separate one-binary NaiveFox arm. All use the same config-mode startup
path. `off` disables the outer-session gate and preamble. `gate` enables the
gate without a preamble. `root` is the short alias for `document-complete` and
adds one bounded document GET before CONNECT. The harness always emits these
fields explicitly. Thus `off` remains a true
control even though a successfully parsed product config which omits
`preamble` now selects the documented protocol- and listener-specific implicit
default. The harness never depends on that omission during arm screening.
The tree modes also fetch two resources from that browser page;
`tree-complete` waits for them, while
`tree-overlap` may overlap their completion with CONNECT.
`tree-resource-committed-overlap-css` is an H3-only, one-resource causal arm.
`tree-native-parser-document-handoff-overlap-css` keeps the existing native
HTML5 speculative-preload arm as a control and adds the upstream-style
document-consumer handoff before parser feeding. Its fail-closed admission
requires each handoff phase exactly once, one physical QUIC connection and
ClientHello, and the same root/CSS semantics and asset length as both
`tree-complete-css` and `tree-native-parser-preload-overlap-css`; timing and
packet indices are outcomes only. The first parser feed is explicitly
`main-copy-dispatch`; delivery retargeting is not part of this arm.
`tree-native-parser-retarget-overlap-css` is the separate H3-only retarget
experiment. It requires verified delivery to the HTML5 parser thread before
the replacement listener is installed and the channel resumes, direct
retargeted `OnDataAvailable`, and rejects fallback to the main-thread copy
path. A parent-side `nsHttpChannel` returns physical `OnStopRequest` to the
main thread, so the parser finish is then dispatched to the parser target as
in the upstream HTML5 parser fallback; direct `OnDataFinished` is part of the
later `HttpChannelChild` boundary. Its decrypted comparison requires the
complete CSS, native-preload, and document-handoff controls; timing and packet
indices remain outcomes rather than admission criteria.
`tree-native-parser-ipc-rendezvous-overlap-css` is the H3-only treatment that
keeps the retarget control's root/parser path and inserts the lean native-style
activation rendezvous before the existing parent-side channel `AsyncOpen`.
Admission requires one request identity across request registration, primary
open, the independent background leg, both receives, and successful release.
The two receive orders may race, but release must follow both. Failure,
cancellation, callback failure, missing/duplicate phases, direct fallback, a
second QUIC connection, or a second outer ClientHello invalidates the sample.
The arm retains one CSS asset, a fresh profile, and ordinary writable Cache2;
the existing Cache2 lifecycle begins after rendezvous release and is not
emulated by another hop.
`tree-native-parser-root-rendezvous-overlap-css` is the H3-only root
replacement treatment. It keeps the same root and CSS requests and the same
native parser/style semantics, but suspends the already-open physical root
channel while a request-scoped replacement primary actor links to that exact
channel, asynchronous redirect verification resolves, and the independent
request-scoped background actor becomes ready. Only then does it publish the
replacement consumer, forward the stored `OnStartRequest`, and resume the
physical root. The existing real `RetargetDeliveryTo` step remains downstream
of Resume and is not an admission barrier. No second root channel is opened.
Admission requires the primary/background actor pairs to be created, linked,
and destroyed for this request; `same_channel=1`; the redirect-verification
queue/run/callback/resolve phases; verification and setup completion before
forward/Resume; and the prior retarget, parser, stylesheet, one-QUIC-identity,
and one-ClientHello contracts. Timing and packet positions remain outcomes.
It releases CONNECT only after the root has completed and Gecko has emitted
`NS_NET_STATUS_WAITING_FOR` for the stylesheet request. It therefore proves
that the resource transaction was committed without conditioning admission on
response HEADERS, body size, packet number, or elapsed time. A terminal drain
fallback is reported separately and is invalid for passive admission.
`tree-resource-native-cache-committed-overlap` keeps the same one-resource
admission rule but restores the resource channel's ordinary writable Cache2
open. The root remains cache-inhibited. Admission additionally requires an
asynchronous callback for a new entry and therefore a fresh temporary profile;
cache reuse, a synchronous callback, or a timeout invalidates the sample. This
is a scheduling diagnostic, not a proposal to depend on persistent cache.
`document-overlap` uses the identical single document request but releases
CONNECT after accepted 2xx response HEADERS while the root listener is still
active. Its normal root drain is mandatory. Root FIN ordering is report-only,
so this arm cannot be selected or resampled according to response size or
whether physical overlap happened to appear.
`document-handshake-confirmed` is an H3-only causal diagnostic. It lets the
ordinary document transaction establish the cold outer session, but retains
that first marked preamble transaction until Neqo reports transport
`Confirmed`. Decrypted admission requires one physical QUIC connection, one
ClientHello, H3 control/QPACK initialization before server HANDSHAKE_DONE,
transport confirmation before GET HEADERS, unchanged document semantics and
response size, and document FIN before CONNECT. This arm tests whether
pre-confirmation preamble activation causes the early split; it is not a claim
that ordinary Firefox explicitly waits for HANDSHAKE_DONE.
`document-carrier-dispatch` is the product-oriented H3 lifecycle experiment.
A request-less Gecko `SpeculativeTransaction` owns establishment of one cold
H3 proxy connection while the real document remains an inactive ordinary
`nsHttpTransaction`. The document stays in the normal pending queue through
`ConnectionConnected` and is released only after the carrier's normal
zero-byte `ReadSegments -> Close`; the callback posts ordinary
connection-manager processing, which must activate it with normal
`Http3Session::AddStream` on the carrier-established connection. Its explicit
single-carrier limit is independent of the profile setting that disables
general speculative preconnects. It uses no Happy Eyeballs flag or race, no
`Confirmed` gate, no `SwapTransaction`, and no proxy-fallback change.
Decrypted admission rejects a second physical QUIC
identity or ClientHello, any unexpected HTTP request or client bidi stream,
missing/failed carrier read or completion, null/reused lifecycle identities, a
document stream-id mismatch, dispatch before carrier completion, any
confirmation-gate wait/release, changed document request/response semantics,
or CONNECT before document FIN.

`document-cold-winner-handoff` reconstructs the later cold Firefox lifecycle
at `MakeNewConnection`, after the real document is pending. It suppresses only
that channel's earlier speculative preconnect, starts one request-less H3
proxy carrier, keeps its connection racing and unpublished through
`ConnectionConnected`, then uses the normal posted activation callback and
exact pending-transaction dispatch before publishing the winner. It bypasses
the Rust multi-candidate race and has a single-candidate terminal failure path;
there is no retry, 0-RTT, confirmation barrier, transaction swap, dummy HTTP
request, timer, or fallback-policy change. Decrypted admission proves one QUIC
identity and ClientHello, identical document semantics, the complete ordered
ownership lifecycle, no carrier request, and GET/200/FIN before CONNECT.

The H3 early-scheduling results below that used the command-line browser
backend are superseded. That backend wrote its ready marker without launching
Firefox, so direct Firefox process startup and its busy main-thread queue were
inside capture, while NaiveFox was already running in a separate process.
Those artifacts remain useful for decrypted lifecycle admission and negative
mechanism checks, but their Firefox GET packet index, Initial-to-GET timing,
and passive early-window rankings are not valid same-state camouflage
comparisons. New H3 multi-arm results must use the pre-launched Selenium
contract above; no old sample is silently reused or resampled.

Accepted same-base decrypted artifact `20260825T164347Z-53b8e44f` falsified
the intended scheduling hypothesis: Firefox emitted its first GET at packet
18 / 33.301 ms, while both `document-complete` and the exact cold winner path
emitted it at packet 10 (5.548 and 4.851 ms). The ten-block H3/inner-HTTPS
screen is retained as safe artifact `c95c4bce98e1c840` (seed `25082502`). The
cold winner was only nominally closer on packets 1--16 (0.08792 versus
0.08959) and the first 250 ms (0.13444 versus 0.13814), but was worse on
packets 17--32 (0.72760 versus 0.72547), packets 1--32 (0.23630 versus
0.23606), and whole flow (0.41899 versus 0.41459). Smoke mode is insufficient
for inference; together with the unchanged GET position it rules out moving
this mechanism toward the default and directs the next diagnostic to socket-
thread event ordering around carrier close, activation callback, `RecvData`,
`ResumeSend`, and Neqo output.

The isolated ten-block same-base H3/inner-HTTPS paired screen is retained as
safe artifact `c63340cad667a8c4` (seed `25082502`). The carrier drain fence was
worse than `document-complete` in every selected view: packets 1--16
`0.10395` versus `0.07986`, packets 17--32 `0.72861` versus `0.71992`, packets
1--32 `0.24091` versus `0.22342`, the first 250 ms `0.10758` versus `0.09208`,
and whole-flow `0.37200` versus `0.35077`. Decrypted traces also kept the first
GET at packet 10 while same-base Firefox placed it around packets 15--17. This
is screening evidence, but it falsifies carrier drain alone as a default
camouflage mechanism; a follow-up must preserve Firefox's provisional racing
connection and winner-handoff semantics instead of adding another delay.
`document-native-cache-open` restores the native channel sequence
`SpeculativeConnect -> asynchronous cache2 read-only miss -> TriggerNetwork`.
It uses only the temporary profile, retains `INHIBIT_CACHING`, and rejects a
synchronous callback, cache hit, timeout, or any result other than the cold
read-only miss. Accepted decrypted run `20260825T154313Z-ec3f2b9e` showed GET
at packet 10 for both complete and native-cache arms (5.029 ms and 5.368 ms),
while Firefox emitted its first GET at packet 15 (27.621 ms). The paired
10-block H3/inner-HTTPS smoke `7c168177d3fa6928` (seed `25082503`) ranked the
native arm worse on packets 1--16, packets 1--32, 250 ms, and whole, with only
a small 17--32 improvement. Together with unchanged GET ordering this closes
the cache-open-only hypothesis; the arm is not a default candidate.
`document-native-channel-open` restores the larger native cold document
lifecycle: generic request-less speculative H3 establishment, normal writable
cache2 open, and genuine local Safe Browsing classification. Admission requires
`TYPE_DOCUMENT`, a system triggering principal, a distinct non-system URI
principal matching the exact fixture URI and origin attributes, an available
URL-classifier DB, `Classify()==NS_OK`, `expectCallback=true`, a positive
suspend count, a successful asynchronous clean callback and Resume, and a new
writable cache entry before network trigger. The real-time/global-cache/google5
paths are disabled in both controlled Firefox and NaiveFox profiles. Every
reference participant and NaiveFox sample gets a separate fresh profile, and
the runner rejects a pre-existing `cache2` directory rather than deleting or
reusing it. Any fail-open classifier path makes the sample invalid.
Accepted pre-launched same-base decrypted artifact
`20260825T182825Z-918ced97` replaced the earlier cold-start interpretation:
Firefox emitted its first GET at packet 11 / 5.000 ms,
`document-complete` at packet 10 / 5.821 ms, and
`document-native-channel-open` at packet 11 / 5.362 ms. All three used one
physical QUIC identity and one ClientHello, and the native arm passed its full
cache/principal/classifier/Suspend/Resume admission. Thus the old Firefox
packet 15--18 delay was browser-startup contamination, not a required Necko
transport phase. A two-block fail-closed passive control
`7b3a8e636ebd6f16` ranked the native arm closer in packets 1--16, packets
1--32, and the first 250 ms, while whole-flow distance was effectively tied;
it was explicitly insufficient for inference. The subsequent ten-block
pre-launched screen `3f3c464064e0d510` (seed `84622`) reversed that hint and
ranked `document-complete` closer in every selected view: packets 1--16
`0.11904` versus `0.13597`, packets 17--32 `0.60661` versus `0.61389`, packets
1--32 `0.20744` versus `0.21865`, the first 250 ms `0.13268` versus `0.13675`,
and whole `0.37567` versus `0.38481`. This remains screening-only evidence,
but it does not justify a 30-block inference run or moving the native channel
lifecycle toward the default. The arm remains a fail-closed diagnostic.

Review then narrowed its synthetic URI principal to the classifier's single
local `Classify()` call; the general lean channel-principal API remains
system-owned. Lean shutdown also explicitly unregisters the classifier
feature's preference callbacks before XPCOM teardown. Post-fix strict artifact
`20260825T185749Z-cb8bbaf2` passed the complete admission contract with all
three first GETs at packet 10 (Firefox 3.991 ms, complete 4.634 ms, native
4.335 ms).

`document-start-overlap` uses the same root request but waits for the root
channel's `NS_NET_STATUS_WAITING_FOR` event, which follows H2/H3 request-stream
commit, before releasing CONNECT. It does not infer socket ordering from
`AsyncOpen` or a main-loop delay. The final HTTP result and root drain are
separate lifecycle evidence. Decrypted H3 admission requires root GET HEADERS
before CONNECT HEADERS, one QUIC identity, and request/response-size parity
with both document controls; it never requires response HEADERS or FIN before
CONNECT.
The final isolated six-block H2/inner-HTTPS screen is retained as safe artifact
`f244527d965b626e`. Compared with `document-start-overlap`, the bounded resource
tree was effectively tied for packets 1--16 (`0.06880` versus `0.06871`) but
worse for packets 17--32 (`0.50135` versus `0.45276`), packets 1--32 (`0.21964`
versus `0.19245`), the first 250 ms (`0.19285` versus `0.18929`), and whole
flow (`0.49826` versus `0.48398`). It added about 47 KiB of server traffic in
the first 250 ms. Six blocks remain screening evidence rather than an
inferential verdict, but the consistent direction and identified volume/burst
mechanism reject the resource tree as a product default. The smaller
`document-start-overlap` lifecycle is therefore the promoted H2 policy; the
resource tree remains an explicit fail-closed research arm.
`tree-native-parser-document-start-overlap-css` retains that exact early
admission while the root continues through the lightweight HTML5 speculative
scanner and opens one native `FromParser` stylesheet in the background. The
sample validator requires one physical H3 connection, one outer ClientHello,
one successful parser descriptor/channel/drain, and forbids the older late
parser barrier. The private decrypted validator additionally proves `root GET
< CONNECT < CSS GET` and the CSS 200/FIN lifecycle.
`tree-native-parser-document-start-navigation-stop-css` adds a scoped
Firefox-style navigation stop after that CSS response has begun. Its validator
requires the CONNECT handoff, a positive client-to-target tunneled read, CSS
request commit, successful CSS 2xx response start, and only then the expected
`NS_BINDING_ABORTED` stylesheet stop and complete root/preamble drain. The
tunnel activity and CSS response may occur in either order; any missing or
non-2xx response, premature stop, unexpected abort, or failed one-shot dispatch
rejects the sample. Decrypted admission requires CSS 200 HEADERS before the H3
STOP_SENDING/RESET_STREAM but deliberately does not require CSS FIN. Screening
shows that this exchanges complete duplicate volume for a repeatable cancel
pattern and does not improve 250 ms or whole-flow over document-start, so it is
not a default candidate.
`tree-native-parser-document-start-response-stop-css` moves the same scoped
stop to the first positive decoded target-to-client tunnel payload. The
runtime validator accepts exactly one of two outcomes: an active stylesheet is
aborted after response activity, or the stylesheet completes naturally before
that activity and no stop is attempted. Safe metadata counts those branches
separately. Strict decrypted admission for the abort branch requires CONNECT
DATA before CSS STOP_SENDING/RESET_STREAM, error `0x10c`, no CSS FIN, and a
positive but partial CSS DATA body; packet number, elapsed time, and QUIC reset
final size remain outcomes. A nonfatal background-drain timeout is valid
product behavior but invalid controlled evidence because it proves neither
terminal branch. Six-block same-base screening `9193f5a55f430bda` produced one abort
and five natural completions. It improved packets 17--32 and 1--32, but remained
worse than `document-start-overlap` at 250 ms and whole-flow, so it is not a
default candidate or a reason to tune another cancellation point.
`tree-early-overlap` completes the root first, then releases CONNECT only after
at least one resource response has begun while leaving that same CSS or JS
stream unfinished at the callback boundary. Necko can nevertheless serialize
CONNECT after the resource END_STREAM; the decrypted audit rejects that
outcome and the arm must not be selectively resampled. The audit also requires
exactly the same root/CSS/JS request semantics and asset sizes as
`tree-complete`. Reusing an explicit
seed across separate invocations reproduces schedule order, but does not make
independently captured samples statistically paired:

`tree-root-overlap` also completes the root first, but its barrier depends only
on at least one successfully started resource channel. Consequently its causal
state is stable across fast and slow servers while actual asset/CONNECT wire
overlap remains a report-only outcome. The capture is retained only after all
expected response streams have a FIN; root FIN must precede CONNECT.

```bash
./run-camouflage-suite.sh --mode gate --protocol both --seed 12345 --naivefox-arm off
./run-camouflage-suite.sh --mode gate --protocol both --seed 12345 --naivefox-arm gate
./run-camouflage-suite.sh --mode gate --protocol both --seed 12345 --naivefox-arm root
```

Discard all tree results produced before the H3 diagnostic profile split.
Those runs mistakenly installed the direct-Firefox
`alt-svc-mapping-for-testing` preferences in NaiveFox too, so document and
resource GETs could be routed over different physical QUIC connections. The
preserved private diagnostic `20260824T123959Z-725a1454` demonstrates that
harness artifact and is not valid product evidence. The current diagnostic
keeps the forced mapping only in the direct Firefox reference profile and
requires exactly one physical outer QUIC connection shared by every preamble
GET and CONNECT.

Config-arm files and their percent-encoded proxy credentials remain under the
private capture directory. Sanitized metadata records only `naivefox_arm`.
The runner rejects `off` or `gate` if any preamble result is logged, rejects a
document/tree arm unless exactly one successful result is logged, and requires
every gated capture to contain exactly one physical outer connection. Tree arms
are opt-in screening diagnostics even in `research` mode. They are not added to
the large default superblock: its five-member `off`/`gate`/`root` design keeps
collection cost bounded and remains screening-only. `--multi-arm-arms` can opt
a deliberate screening run into a different arm list without increasing every
routine run. `tree-native-parser-resource-committed-page-http-connect` is the
H3-only ingress-control alias for the six-resource page arm. It preserves the
same preamble mode, path, cache policy, limits, and lifecycle validation while
selecting the local HTTP CONNECT listener; a multi-arm run must include the
matched `document-start-http-connect` control. The operator-facing exact HTML
and response contract is maintained in
[`../../FRONTING-PAGE.md`](../../FRONTING-PAGE.md).

The HTTP CONNECT ingress can also be combined with the existing causal
document barriers on either H2 or H3. `document-overlap-http-connect` waits
for accepted response HEADERS, while `document-first-buffer-http-connect`
waits for the first complete body buffer. The
`document-start-task-http-connect`, `document-headers-task-http-connect`, and
`document-first-buffer-task-http-connect` aliases preserve the corresponding
product modes but add exactly one ordinary main-thread turn after their native
Necko event. These are listener-selection aliases only: they do not add a
timer, byte threshold, packet-index condition, or new production mode. They
remain opt-in screening arms and do not alter implicit defaults.

Profiles have explicit participant roles. Direct H3 Firefox alone receives
the local test Alt-Svc mapping; the NaiveFox process profile enables the real
H3 stack without that mapping, and the workload browser uses only its
exact-authority SOCKS or HTTP proxy PAC. Non-loopback traffic remains
fail-closed and namespace-local browser control ports remain direct. The runner
validates those generated profiles before
capture and rejects inherited `AlternateServices.bin` state. Private run
`5f45fb110cc57517` predated this role separation and stopped after exposing a
second resumed QUIC route; all of its samples are invalid harness evidence.

Comparative inference between arms requires randomized multi-arm superblocks
with shared contemporaneous controls. Enable that design in same-base mode:

```bash
NAIVEFOX_CAPTURE_MODE=same-base \
NAIVEFOX_CAPTURE_REFERENCE_BIN=/path/to/firefox \
NAIVEFOX_CAPTURE_REFERENCE_OBJDIR=/path/to/objdir \
./run-camouflage-suite.sh --mode standard --protocol both --seed 12345 \
  --multi-arm-superblocks
```

Each seeded superblock captures Firefox A, Firefox B, NaiveFox `off`, `gate`,
and `root` in randomized order against one scenario. The Firefox controls are
captured once and shared by all three arm comparisons. `samples-per-cohort`
means superblocks per protocol in this mode. The sanitized
`features-superblocks.csv` records both `experiment_block` and `naivefox_arm`;
`arms/<arm>/features.csv` selects the common controls and that arm, with its own
`metrics.json` and `summary.txt`. The runner also writes
`arm-comparison.json` and `arm-comparison.txt`. This paired report ranks
`off`, `gate`, and `root` inside each protocol and passive feature view using
the same Firefox A/B controls from each `experiment_block`; it does not train a
classifier or select features from the arm labels. Its bounded distance is the
featurewise excess outside the matched Firefox A/B envelope, calibrated only
from Firefox control disagreement. Paired block bootstrap intervals and
within-block sign-flip tests are reported, with Holm correction across all
eligible protocol/view/arm-pair tests.

For the first preamble-shape screen, collect the four product modes against one
common Firefox A/B pair in every randomized six-member block and restrict the
paired report to the earliest packet windows:

```bash
NAIVEFOX_CAPTURE_MODE=same-base \
NAIVEFOX_CAPTURE_REFERENCE_BIN=/path/to/firefox \
NAIVEFOX_CAPTURE_REFERENCE_OBJDIR=/path/to/objdir \
./run-camouflage-suite.sh --mode standard --protocol both --seed 12345 \
  --multi-arm-arms gate,root,tree-complete,tree-overlap \
  --multi-arm-views initial_packets_16,packets_17_32,initial_packets_32
```

Here `root` is the document-complete mode. Every selected arm is compared with
the same controls inside its block; the paired analyzer infers the selected
arms, emits every selected arm pair, and applies Holm correction across only
the protocol/view/pair family actually reported. This remains candidate
screening, never an absolute camouflage verdict.

For the scheduling-only follow-up, replace `gate` with
`tree-early-overlap`. The early arm uses exactly the same root, stylesheet,
script, and response sizes as the two existing tree arms; only the CONNECT
admission point changes.

An isolated ten-block same-base H3/inner-HTTPS smoke of
`root,tree-complete,tree-root-overlap` is retained as safe artifact
`183164d35decbb0f` (seed `24082420`). Descriptively, it found the new arm closest on packets
17--32 (0.53163 versus 0.56326 and 0.64406) and packets 1--32 (0.20633 versus
0.22243 and 0.23118). `root` remained closest on packets 1--16 and the whole
flow. These are screening distances and support neither relative inference nor
an absolute camouflage verdict. The paired decrypted run independently proved equal
request semantics and asset sizes and observed real resource/CONNECT overlap
without selecting samples on that outcome. The next low-cost screen should
hold the admission rule fixed and vary only how many discovered resources are
opened, rather than tune packet sizes or add sleeps.

An isolated ten-block same-base H3/inner-HTTPS paired screen of
`document-complete,document-handshake-confirmed` is retained as safe artifact
`89f06084e323ff50` (seed `25082501`). Two independent decrypted admissions
moved the first preamble GET from packet 10 to packet 12, after transport
confirmation, while the same-base Firefox GET appeared at packet 14 or 15.
The passive paired result nevertheless rejected a bare confirmation barrier as
a camouflage improvement: its distance was worse on packets 1--16 (0.11245
versus 0.06598), packets 1--32 (0.22852 versus 0.19602), the first 250 ms
(0.14197 versus 0.12561), and the whole flow (0.36925 versus 0.35642).
Packets 17--32 improved only marginally (0.57514 versus 0.58116). This is a
screening result, but it shows that reproducing only the confirmation boundary
creates an artificial sequence; any production candidate should instead
investigate the ordinary Firefox connection-winner/adoption lifecycle.

The H3-only `tree-resource-committed-overlap-css` arm uses the same root and
64-KiB stylesheet as `tree-complete-css`, but releases CONNECT after Gecko has
committed the CSS request rather than after its response. Strict decrypted
artifact `20260826T051112Z-deaf291f` proved one QUIC identity and ClientHello,
root completion before CSS commit, CSS GET before CONNECT, and CSS response
after CONNECT, with identical request semantics and content length in the
control arm. Two-block passive artifact `c1bd74f7b299c8a1` is screening-only:
the committed arm was closest for packets 17--32 (0.43774) and packets 1--32
(0.22508), but worse for packets 1--16 (0.16204) and the first 250 ms
(0.22409). This supports a real scheduling effect without supporting asset-size
tuning or making the mechanism the default.

A corrected ten-block same-base screen, safe artifact `324d583facd28300`
(seed `84625`), confirmed that the committed arm removes the old packet-25/26
phase boundary rather than merely moving it. Relative to `document-complete`,
its model-free distance improved on packets 17--32 (`0.34961` versus
`0.48280`), packets 1--32 (`0.13689` versus `0.18094`), and whole flow
(`0.35140` versus `0.38407`), but regressed in the first 250 ms (`0.13604`
versus `0.12857`). Its CSS response also formed an early dense server burst at
about 7 ms, with roughly 82.7 KiB excess server wire volume in the first
250 ms. The arm is therefore a useful causal diagnostic but not a default.

`tree-resource-native-cache-committed-overlap` tested whether the stylesheet's
ordinary asynchronous Cache2 open naturally supplies the missing scheduling
phase. Strict same-base artifact `20260826T062323Z-e9a146a3` proved one QUIC
identity and ClientHello, a new writable cache entry, identical root/CSS
semantics and 65536-byte CSS content length, CSS GET before CONNECT, and CSS
response completion after CONNECT. The outcome was negative: CSS remained at
packet 16 and CONNECT at packet 17 / 7.140 ms. A subsequent paired collection
was rejected fail closed after a sequential native-cache sample timed out
before admission, so no incomplete statistics were retained. This mode remains
diagnostic and must not be promoted or expanded as a cache-dependent policy.

Fresh symmetric private lifecycle artifact `20260826T063809Z-a069beb8` also
closed the Safe Browsing latency hypothesis. The pre-launched reference had
initialized its URL-classifier service during warm-up; for the measured target
document `StartInternal` returned `expectCallback=false`, so the channel was
not suspended and its real transaction was created 0.292 ms after classifier
start. The admitted NaiveFox `document-native-channel-open` channel did perform
the genuine local-DB path: `Classify(expectCallback=true) -> Suspend`, clean
callback after 1.276 ms, `Resume` 0.002 ms later, and transaction dispatch
0.019 ms after that. H3 connected only later, so classification did not retain
an already-ready session. Firefox and NaiveFox document GETs were correspondingly
close (4.882 ms and 5.560 ms). The remaining phase appears after the document:
Firefox's first resource GET was at 29.277 ms, while a document-only NaiveFox
arm had already released CONNECT near 7 ms. Further work must target ordinary
resource discovery/activation rather than extend classifier or cache barriers.

Controlled H3 packet-shape screening must run with
`NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1`. The private namespace disables loopback
GRO/GSO/TSO and UDP segmentation offloads, and the runner rejects captured
proxy UDP frames larger than 1500 bytes. Older run `909bdbd9c1d68824` predates
this check and contains 13--20 KiB UDP GSO superframes. It is invalid as
wire-level packet evidence and must not be used to rank the preamble arms.

The three views answer different mechanism questions: `initial_packets_16`
is the earliest cumulative window, `packets_17_32` isolates the next sixteen
positions without repeating handshake features, and `initial_packets_32`
checks the combined early shape. For each view, `arm-comparison.json` retains
diagnostic top passive features plus aggregate signed transport/wire-size
sequences. The Firefox A/B mean absolute difference is written beside each
arm's mean delta from the matched Firefox midpoint, so a small run can reveal
whether a packet-13--20 feature shrank or only moved later. These summaries are
not used for arm ranking or inference and contain no decrypted/header/stream,
endpoint, or raw timestamp data.

The selected arm-specific classifier reports are candidate-screening diagnostics,
including when the collection mode is `research`. Their JSON records
`screening_only=true`, every classification is `INCONCLUSIVE`, and they cannot
emit an absolute GREEN/RED verdict. After choosing an arm, preregister the
configuration and seed policy, then collect a fresh single-arm confirmation;
for example:

```bash
NAIVEFOX_CAPTURE_MODE=same-base \
NAIVEFOX_CAPTURE_REFERENCE_BIN=/path/to/firefox \
NAIVEFOX_CAPTURE_REFERENCE_OBJDIR=/path/to/objdir \
./run-camouflage-suite.sh --mode research --protocol both --seed 67890 \
  --naivefox-arm root
```

Do not reuse the multi-arm screening samples for that confirmation.

The paired report is deliberately relative. Because Firefox A/B define the
control envelope, there is no independent third Firefox observation that could
serve as an absolute null. It can rank arms but cannot establish absolute
indistinguishability. Gate and smoke runs, fewer than 30 blocks per protocol,
or workloads represented by only one block are marked
`INSUFFICIENT_FOR_INFERENCE` even though diagnostic point estimates are still
written. Feature views overlap heavily and use equal feature weighting, so
even research-sized rankings remain conditional on this declared passive
distance. This mode requires same-base Firefox and is mutually exclusive with
`--naivefox-arm`. The seed determines collection order, but it does not turn
gate/smoke sample counts into research-grade evidence.

Successful single-arm runs retain only `metadata.txt`, `features.csv`,
`metrics.json`, and `summary.txt`. Multi-arm runs retain `metadata.txt`,
`features-superblocks.csv`, `arm-comparison.{json,txt}`, and the selected
sanitized `arms/<arm>/` result sets under
`<objdir>/naivefox-fixture/camouflage-safe/<run-id>/`.
Capture files, profiles, bodies, credentials, screenshots, and logs remain
private and are deleted on success. See `../../CAPTURE.md` for the threat model,
feature schema, interpretation policy, limitations, and same-base procedure.

## Interactive fixture lifecycle

For manual debugging:

```bash
./start.sh
run_dir=$(<../../../../obj-*/naivefox-fixture/active-run)
source "$run_dir/fixture.env"
./stop.sh
```

When more than one object directory exists, use the exact active-run path
printed by `start.sh` instead of the glob. The environment file is mode `0600`
because it contains per-run credentials. `stop.sh` stops only fixture-owned
processes, removes private run state, and may retain sanitized diagnostics.

The control client uses `curl --proxy-cacert` for outer proxy TLS and `--cacert`
for an HTTPS target. The fixture never invokes `caddy trust`, modifies system or
normal Firefox trust, installs a service, uses `-k`, accepts arbitrary
certificates, or exposes an open proxy.

## Pinned inputs and first run

`versions.env` is the source of truth for exact Caddy, xcaddy, Go archive/hash,
and immutable `forwardproxy@naive` revisions. The first run may download the
pinned Go toolchain and build the fixture Caddy; later runs reuse only validated
artifacts. Setup verifies the module list, adapted Caddy configuration,
loopback listener, protocol mode, authentication, ACL, certificates, and both
NSS profile contents before product traffic is accepted.

Every runner installs cleanup traps. Successful runs delete sensitive state;
failed runs print its private ignored path for diagnosis.
