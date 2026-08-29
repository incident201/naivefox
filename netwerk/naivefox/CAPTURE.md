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
Use `--scenario browser_page --browser-page-base-size BYTES` to scale all six
page assets coherently for that check. Omitting it preserves the established
262144-byte fixture; the sanitized metadata records either the explicit base
or `default_262144`.

Use `--network-one-way-delay-ms N` and `--network-rate-mbit N` only inside the
one-shot isolated namespace to test RTT and bandwidth robustness. The verified
loopback `netem` profile applies symmetrically to every participant. Shaped
captures use the receive copy so packet timestamps occur after netem rather
than at the pre-qdisc transmit tap; metadata records the profile and capture
copy policy.

The first 20-ms/20-Mbit plumbing smoke, `e97a1bae045f29d8` (seed
`20260828145`), successfully shaped and analyzed all four participants but
exposed that profile fields were written only for NaiveFox-only diagnostics.
It is retained as a failed metadata-validation attempt, not candidate evidence.
After fixing the ordinary metadata path, `162df2d1d421aa23` (seed
`20260828146`) again completed one H3/SOCKS block and records
`network_profile_active=1`, one-way delay 20 ms, rate 20 Mbit/s, one applied
protocol, and `capture_copy_policy=receive_after_netem`. Firefox packets 1--16
spanned about 148 ms and whole flows about 530--548 ms, confirming that the
stored receive-side timestamps include the emulated link. One block remains a
plumbing check, not a candidate comparison.

Harness validation `a045653efaa21665` (seed `20260828140`) completed one full
isolated same-base H3/SOCKS block at a 65536-byte base, including both Firefox
controls and the two NaiveFox arms. This is a successful end-to-end plumbing
check only; one block is explicitly insufficient for a residual inference.

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

Safe 30-block same-base acceptance artifact `7b5c70011f0fba08` (seed
`27082730`) compared explicit `off` with `document-start-overlap` using paired
Firefox A/B controls and inner HTTPS/H2. The document-start policy improved
packets 1--16 (`0.16459` to `0.13442`), packets 17--32 (`0.76117` to
`0.65828`), packets 1--32 (`0.26499` to `0.22720`), packets 1--64/128, and the
250 ms view (`0.14026` to `0.12081`). No whole-flow regression was detected
(`0.38926` to `0.38660`; paired CI included zero), but equivalence was not
proved. All 120 participants passed
the isolated-network mutation and capture-drop policy. This satisfies the
predeclared product gate for H3. The final six-block H2 resource-tree screen
then retained the lower distance for the same compact mechanism in every view
after packets 1--16. This initially promoted `document-start-overlap` for
explicit H2 and H3 upstreams; the later first-buffer H2 evidence below replaces
the H2 policy while retaining it for H3. Explicit `mode: off` remains the
control and opt-out.
Secondary `steady_after_32` and lifecycle point estimates favored `off`; the
steady paired interval crossed zero and lifecycle did not survive the report's
Holm correction (`p=0.136`). They remain regression monitors rather than a
reason to override the predeclared early/250 ms/whole acceptance views.

`tree-native-parser-document-start-overlap` keeps the same request-commit
barrier and lets the root continue in the background through the lean HTML5
speculative scanner and one native stylesheet preload. Decrypted admission is
fail-closed on one QUIC identity and one ClientHello, exact document and CSS
semantics, and the wire order `root GET < CONNECT < CSS GET < CSS 200/FIN`.
The mode deliberately does not require any packet position, elapsed time, or
asset-size-derived overlap. Six-block H2-inside-CONNECT screening improved
packets 17--32 from `0.62052` to `0.50648` and packets 1--32 from `0.22690` to
`0.20573`, but worsened the 250 ms and whole-flow views because the completed
stylesheet remains additional traffic. It is therefore not promoted by that
screen alone.

The same mode is available as an explicit H2-only experiment. Decrypted
same-base admission proves one TCP/TLS/H2 connection, equal semantic TLS and
SETTINGS, normal document/FromParser request semantics, and the wire order
`root GET < CONNECT < CSS GET`, followed by successful root and CSS
END_STREAM. Six-block direct-Firefox screening `fec6d5d295bcecf0` improved
packets 17--32 from `0.45721` to `0.43922` and packets 1--32 from `0.22483` to
`0.21018`, while worsening 250 ms from `0.13102` to `0.14840` and whole-flow
from `0.28498` to `0.31852`. The complete stylesheet added about 67 KiB of
server traffic by 250 ms, so this H2 arm is not a default candidate either.

The final bounded H2 fronting-page experiment extended that mechanism to one
same-origin stylesheet, classic deferred script, and image, all discovered by
the lean HTML5 speculative scanner and opened through native Necko preload
channels after early CONNECT admission. Two fresh decrypted runs proved one
outer TLS/H2 connection, one ClientHello, normal END_STREAM completion, exact
same-base request semantics, and `root GET -> CONNECT -> resource GETs`. The
isolated six-block paired screen is retained as `f244527d965b626e`. Relative to
`document-start-overlap`, the resource tree was tied for packets 1--16
(`0.06880` versus `0.06871`) but worse for packets 17--32 (`0.50135` versus
`0.45276`), packets 1--32 (`0.21964` versus `0.19245`), 250 ms (`0.19285`
versus `0.18929`), and whole flow (`0.49826` versus `0.48398`). It introduced
about 47 KiB of additional early server traffic and a new burst rather than
removing the residual. The tree is therefore rejected as a default;
`document-start-overlap` was kept as the compact H2 control until the later
first-buffer campaign.

H2 `tree-native-parser-document-start-navigation-stop` retains the same
client-to-target ownership predicate and scoped load-group cancellation.
Decrypted admission proves `root GET < CONNECT < CSS GET < CSS 200 <
RST_STREAM(CANCEL)`, no CSS END_STREAM, and one TCP/TLS/H2 connection. In the
six-block direct-Firefox screen `2f2b4c49e0b6edb7`, however, packets 17--32
changed only from `0.48108` to `0.47885`; packets 1--32 improved from `0.23117`
to `0.21944`, while packets 1--16, 250 ms, and whole-flow all worsened. Server
bytes by 250 ms were nearly identical for complete and canceled CSS, showing
that the response had already entered the H2/TLS/TCP send path before the
causal cancel. The H2 stop mode therefore remains diagnostic and is not a
default candidate.

A server-side H2 padding-phase control then tested whether the existing Naive
Variant 1 budget could fill part of the nested-handshake gap without adding
bytes. The opt-in Caddy path moved the same eight independently randomized
padding records from the first target reads to immediately after the first
positive client-to-target payload. It used valid zero-payload Variant 1
records, required no client decoder change, and left unmodified NaiveProxy
clients on the stock path. Six-block same-base artifact `2c4ef7e9e097254b`
compared both arms only with ordinary direct Firefox A/B. The shift improved
packets 1--16 (`0.14803` to `0.13831`) but worsened packets 17--32 (`0.58058`
to `0.61987`), packets 1--32 (`0.28510` to `0.29356`), and 250 ms (`0.12589`
to `0.12960`). It also compressed the first-32-packet phase from about 56.5 ms
to 32.9 ms instead of reproducing Firefox's roughly 53--56 ms phase. The
server patch and arm were removed. Legacy Variant 1 can contribute at most
`8 * 255 = 2040` padding bytes per direction; a larger cover protocol would
therefore add traffic rather than redistribute the existing budget. The
complete-stylesheet upper bound already shows that such additive early volume
trades a local 17--32 improvement for a 250 ms/whole-flow penalty.

The preceding H2 packet-index screens were collected with segmentation
offloads disabled but the Linux loopback MTU still at its default 65536 bytes.
Their SYNs advertised MSS 65495, and direct Firefox consequently exposed
5--8 KiB host-local TCP segments in packets 22--32.  Those results remain
useful lifecycle/byte-volume diagnostics, but their packet-index distances are
not physical-wire camouflage evidence and must not select a product default.
Controlled H2 screening now sets and verifies loopback MTU 1500 before any
endpoint starts and rejects an outer TCP segment with more than 1460 payload
bytes.  H2 product decisions require a fresh same-base baseline under this
policy.

The first fail-closed two-block gate under that policy is safe artifact
`00de90dccbdb2cec` (seed `2026082701`).  SYN MSS was 1460 for every participant
and no oversized outer TCP segment was admitted.  By packet 32 direct Firefox
had about 10.9 KiB of server transport payload rather than the earlier
55--62 KiB loopback-supersegment result; H2 `off` had about 6.4 KiB.  The main
remaining shape was temporal: direct Firefox reached packet 32 around
51--56 ms, while `off` reached it around 11 ms.  A bodyless
`document-start-overlap` control improved the isolated packets-17--32 distance
but worsened packets 1--16, packets 1--32, 250 ms, and whole flow.  With only
two blocks this is localization evidence, not inference or a product default.

A server-backpressure control then kept the ordinary 64-KiB declared
stylesheet but made only one HTTP/2 maximum-DATA-frame-sized source chunk
available before Gecko's existing causal navigation stop.  Six-block safe
artifact `4cf0e735fac6e9fc` (seed `2026082704`) confirmed the mechanism rather
than a product preset: packets 17--32 improved from `0.59298` for `off` to
`0.51864`, and packets 1--32 from `0.24220` to `0.22026`.  However packets
1--16 worsened from `0.08364` to `0.09202`, 250 ms from `0.07451` to
`0.08283`, and whole flow from `0.23120` to `0.25397`.  The bounded response
still added about 16 KiB of server traffic by 250 ms.  Therefore a fixed
one-frame response is not a default candidate.  It establishes only that
server-side backpressure can preserve the useful early stylesheet phase while
substantially reducing the complete-response penalty; a successor must derive
availability from real tunnel lifecycle events rather than a selected byte
count or timer.

Three subsequent two-block fail-closed controls rejected the remaining simple
H2 server-shaping candidates before any larger run.  In `b4e50d4c5b2f6e01`,
the stylesheet received a partial body only after the first successful
target-to-client CONNECT write on the same physical H2 connection.  This
event-derived release improved packets 17--32 only slightly, left packets
1--32 effectively unchanged, and retained a whole-flow penalty.  A separate
randomized 8--32 ms first-flight deferral (`360c141b0530a2b9`) likewise traded
a small packets-17--32 change for worse packets 1--16, 250 ms, and whole flow;
the timing range was not tuned further.  Stock `outer-session-gate`
(`e4a22a686496c9e1`) did not improve packets 17--32 or 1--32, despite better
250 ms/whole point estimates.  Finally, opt-in randomized fragmentation of
the existing first eight padded target records (`acfefa8613e22022`) added no
cover payload and improved both early slices descriptively, but its extra
H2/TLS framing sharply worsened 250 ms (`0.07142` to `0.15499`) and whole flow
(`0.26033` to `0.34173`).  All four server experiments were removed; the
pinned server binary and module source were restored byte-for-byte.  These
screens do not prove the H2 residual unfixable, but they rule out fixed cover,
first-event cover, cold-flight delay, gate-only establishment, and early
record fragmentation as acceptable defaults under the no-tail-regression
criterion.

A fresh same-base H2/inner-HTTPS-H2 barrier sweep then held the root request,
response body, MTU 1500 policy, and capture cutoff constant while moving only
the causal CONNECT admission point. Six-block `document-complete` screen
`fc337f9614b11061` (seed `2026082803`) improved packets 17--32 from `0.50779`
to `0.45518`, but worsened packets 1--16, 1--32, 250 ms, and whole flow.
Successful response-HEADERS admission in `9f502d43d980f6df` (seed
`2026082809`) improved packets 17--32 from `0.52789` to `0.46752` and packets
1--32 from `0.20916` to `0.20453`, but worsened packets 1--16 (`0.05244` to
`0.05673`), 250 ms (`0.09330` to `0.09638`), and whole flow (`0.28526` to
`0.29516`).

Two response-body-start controls completed the causal scale. Releasing after the complete first
`OnDataAvailable` buffer in `9225f26bc7c3897b` (seed `2026082806`) improved
packets 17--32 (`0.60708` to `0.57265`), 250 ms, and whole flow, but worsened
packets 1--16 and left packets 1--32 slightly worse. Releasing immediately
after the first successfully read body byte in `063ec521aeb104dd` (seed
`2026082808`) improved packets 1--16 (`0.10928` to `0.09447`), packets 17--32
(`0.61839` to `0.56906`), and packets 1--32 (`0.26650` to `0.24909`), but
worsened 250 ms (`0.11467` to `0.12981`) and whole flow (`0.31970` to
`0.33364`). All artifacts passed the isolated-network, capture-drop,
inner-H2, normal-drain, and offload checks. Six blocks remain screening-only,
but the consistent tradeoff rejects all later document barriers under the
no-other-view-penalty rule for the original SOCKS ingress.

Changing the local ingress to native HTTP CONNECT exposed a better causal
boundary without changing the target workload or response sizes. Successful
response-HEADERS admission first reproduced as a candidate in six-block
artifacts `e25ca068dd5f6a2f` and `271cc85a33c50ba2`. A bounded response carrier
that remained open until target payload was observed was then rejected:
six-block artifact `59cb2995524f019d` worsened packets 17--32 (`0.41397`
versus `0.37410`), packets 1--32, 250 ms, and whole flow versus the simpler
HEADERS candidate. Native manual proxy preferences were also rejected in
two-block artifact `e839b0808bf6d33a`; they introduced a large server-byte
deficit and raised whole-flow distance to `0.48472` versus `0.29245` for PAC.
Both temporary mechanisms were removed.

The retained `document-first-buffer-overlap` mode releases only after the
entire first root-body buffer supplied by Necko has been read successfully.
This is a size-independent channel event, not a fixed byte threshold. A short
or failed read does not release CONNECT; a successful 2xx result and normal
root drain remain required, with bodyless completion using the terminal causal
fallback. Six-block artifact `306a249a46d33a5c` measured packets 17--32 at
`0.38953` versus `0.38985` for HEADERS admission and `0.44930` for the SOCKS
default. It also improved 250 ms (`0.08365` versus `0.09790`/`0.09415`) and
whole flow (`0.26186` versus `0.28139`/`0.28377`). Independent six-block
artifact `2b8dd75c4e682940` reproduced every target and cumulative win:
packets 17--32 were `0.41285` versus `0.43633`/`0.47148`, packets 1--32 were
`0.22472` versus `0.23290`/`0.24706`, 250 ms was `0.06474` versus
`0.08738`/`0.08567`, and whole flow was `0.27947` versus
`0.29050`/`0.29013`.

The first two-block workload check was inconclusive across SOCKS: `initial`
artifact `531316919b72bf69` retained the large packets-17--32 win (`0.52734`
versus `0.63849` for document-start), while `bulk_download_256k` artifact
`c301d169be40ee76` favored document-start at packets 17--32 and whole flow.
That initially limited the implicit promotion to HTTP-CONNECT-only H2
listeners. The broader SOCKS campaign below supersedes that provisional
restriction; these small runs remain screening evidence, not a 30-block
inference claim.

Two fresh independent six-block SOCKS/browser-page screens used the same
fail-closed PAC ingress and common same-base Firefox A/B controls. Artifact
`0e3d5fc56b0e06f5` measured first-buffer at `0.50157` for packets 17--32 and
`0.28531` for whole flow, versus `0.57141` and `0.30666` for document-start.
Replication `d98cf5d810045203` again improved packets 17--32 (`0.53648` versus
`0.58128`) and packets 1--32, while whole flow moved in the other direction
(`0.25715` versus `0.24842`). Combining the 12 sanitized paired blocks only as
a diagnostic, not as a predeclared inference test, ranked first-buffer best in
all five primary views: packets 1--16 `0.06904`, packets 17--32 `0.50936`,
packets 1--32 `0.22106`, 250 ms `0.07852`, and whole flow `0.25605`, versus
document-start `0.07038`, `0.56771`, `0.23474`, `0.08193`, and `0.25820`.

Three independently seeded two-block workload controls then addressed the
earlier bulk ambiguity. On `initial`, artifact `b8f33cd43e0a9722` favored
first-buffer at packets 17--32 (`0.73471` versus `0.74927`) and whole flow
(`0.45993` versus `0.47738`). On `bulk_download_256k`, artifact
`631ac031bb4498aa` favored it in every primary view, including packets 17--32
(`0.44125` versus `0.54880`) and whole flow (`0.29902` versus `0.32002`). On
`bidirectional_256k`, artifact `37207e981beba111` again improved packets
17--32 (`0.54020` versus `0.58278`) and whole flow (`0.35035` versus
`0.38637`). This repeated SOCKS evidence, together with the two HTTP-CONNECT
replications, established `document-first-buffer-overlap` as the H2 control.
The final task-boundary campaign below supersedes it for SOCKS-only listeners;
HTTP CONNECT and mixed listeners retain direct first-buffer admission. H3
retains `document-start-overlap`; any explicit preamble or gate value remains
authoritative.

The selected SOCKS policy then passed a fresh 30-block paired same-base run,
safe artifact `e1a89392d921b419` (seed `2026082832`). All 120 participants
passed the fail-closed network-mutation, MTU/offload, capture-drop, inner-H2,
and normal-drain admission policy. First-buffer ranked lower in all five fixed
views. Packets 17--32 improved from `0.47100` to `0.42560`; the paired
difference was `-0.04540`, CI95 `[-0.05907,-0.03310]`, Holm `p=0.0005`.
Packets 1--16 were `0.06300` versus `0.06409`, packets 1--32 `0.19473` versus
`0.20371`, 250 ms `0.08576` versus `0.08633`, and whole flow `0.25701` versus
`0.25735`. None of those four monitors detected a penalty; their
multiple-comparison-adjusted tests did not establish a difference. The report
supports this relative default ranking, not absolute Firefox
indistinguishability.

A native manual-SOCKS-profile control was also tested and removed. Two-block
artifact `7ffcb29b0724180e` improved whole flow to `0.32764` from `0.33475`
for document-start but worsened packets 17--32 to `0.52145` from `0.49642`.
The Firefox profile route is therefore not part of the product or harness.

`document-first-buffer-task-overlap` tests a distinct causal boundary without
a timer or byte threshold. Its first implementation suspended the root request
between the complete first body buffer and a next-main-thread-task barrier.
Lifecycle artifact `b2c6572423dc924e` and browser-page artifacts
`2bcfc478a6a47a5e`/`2a1d04de4af7389f` proved the mechanism and a strong
packets-17--32 reduction, but the root backpressure produced an unstable whole
flow and a bulk-download penalty.

The retained implementation removes `Suspend`/`Resume`. It queues the same
next-task barrier while allowing the channel to drain; if `OnStopRequest`
arrives synchronously after the first buffer, only terminal bookkeeping is
deferred until the queued barrier. Lifecycle artifact `7fa017e5724e7628`
proves SOCKS5 ingress, `first-data-buffer-task` admission, inner HTTPS/H2,
normal drain, and isolated/offload checks. Two-block bulk artifact
`8934faaa21a95889` ranked it best at 250 ms and whole flow. Independent
six-block browser artifact `e1b352731c553d6d` ranked it best at packets 1--16,
250 ms, and whole flow while retaining a packets-17--32 improvement versus
document-start. A fresh 12-block comparison `79e7587b8e64d9ee` then ranked it
better than direct first-buffer in all five fixed views.

The final 30-block paired SOCKS artifact `2834cb35aa391bb0` (seed
`2026082838`) admitted all 120 participants and supported paired inference.
Relative to direct first-buffer, task admission improved packets 17--32 from
`0.49063` to `0.48016`, packets 1--32 from `0.21280` to `0.21028`, 250 ms from
`0.08630` to `0.08036`, and whole flow from `0.26133` to `0.25077`. The whole
paired difference was `-0.01056`, CI95 `[-0.02075,-0.00043]`; it did not
survive correction across the five overlapping views (Holm `p=0.3095`).
Packets 1--16 moved from `0.06956` to `0.07328`, with a paired CI crossing
zero. Under the relaxed acceptance rule this is the better overall tradeoff:
it further reduces packets 17--32 and whole flow, with no detected early
penalty. Omitted-preamble SOCKS-only H2 therefore uses task admission;
HTTP-CONNECT-only and mixed listeners retain direct first-buffer admission,
and H3 retains document-start. Explicit configuration remains authoritative.

`tree-native-parser-document-start-navigation-stop` tests whether Firefox's
normal scoped load-group cancellation can retain that early server-heavy phase
without paying for the complete synthetic stylesheet. CONNECT is not a member
of the synthetic navigation load group. Cancellation is admitted only after
the CONNECT handoff, positive client-to-target tunnel application data, and a
successful stylesheet `OnStartRequest` with 2xx response headers. These two
asynchronous runtime predicates may arrive in either order. Decrypted admission
requires `root GET < CONNECT < CSS GET < CSS 200 < cancellation`, one QUIC
identity, one ClientHello, and the expected aborted stylesheet drain. It never
uses elapsed time, packet position, or delivered-byte count as a gate.

`tree-native-parser-document-start-response-stop` uses the same scoped load
group but waits for positive decoded target-to-client tunnel payload instead
of client-to-target activity. Runtime admission has two mutually exclusive
valid terminal branches: a causal abort with target activity, stop, expected
`NS_BINDING_ABORTED`, and no complete CSS drain; or natural completion with a
normal one-resource drain and no activity/stop/abort markers. Mixed branches
are rejected. Decrypted abort admission requires positive H3 DATA on the
CONNECT stream before CSS STOP_SENDING and RESET_STREAM, error `0x10c`, no CSS
FIN, and positive but partial CSS DATA relative to its declared body length.
Packet index, time, and QUIC reset final size are outcomes only because the
reset offset also includes HTTP/3 framing and HEADERS.

The first response-start trace placed the CSS GET at packet 18, CSS 200 at
packet 22, delivered the server burst through packet 58, and then emitted
STOP_SENDING/RESET_STREAM; the reset final size was about 51.8 KiB. Safe
six-block H2-inside-CONNECT screen `125ee98eabf85501` (seed `20260829`) found
the arm best at packets 1--32 (`0.20573` versus `0.20945` for complete CSS and
`0.23821` for document-start), but not at packets 17--32, 250 ms, or whole-flow.
Whole-flow remained `0.41881` versus `0.41220` for document-start. Upstream
audit confirms the tradeoff is fundamental: a normal FromParser stylesheet
drains to FIN, while canceling an active H3 load necessarily produces request
cancel/reset signaling. The arm remains experimental and does not change the
product default.

Safe six-block response-stop screen `9193f5a55f430bda` (seed `27082707`)
observed one abort and five natural completions. It improved packets 17--32 to
`0.57228` and packets 1--32 to `0.19807`, but `document-start-overlap` remained
better at 250 ms (`0.12532` versus `0.13297`) and whole-flow (`0.38923` versus
`0.39998`). The reset therefore was not a deterministic every-tunnel marker,
but the opportunistic policy also failed to remove the late stylesheet volume
reliably. This does not justify promotion or a 30-block confirmation.
The product keeps a working tunnel alive if the bounded background drain times
out, but the controlled harness rejects that third operational outcome because
it proves neither a causal abort nor a complete natural stylesheet lifecycle.

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

The H2 `document-start-overlap` diagnostic additionally requires the causal
runtime admission, successful result, normal drain, and CONNECT-established
markers to belong to one NaiveFox connection lifecycle. Decrypted wire
admission requires the document GET HEADERS before CONNECT, an HTTP 2xx
document response, and a normal H2 END_STREAM on that document stream. The
END_STREAM position relative to CONNECT is deliberately report-only: request
commit, rather than response completion or a selected frame position, releases
CONNECT in this mode.

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
the separate cold dataset. The harness restarts only the local Caddy process
between the warm and measured phases, on the same origin and port, while the
target server and its append-only journal remain alive. This deterministically
removes prior QUIC session state without a quiescence timeout. The cold control
gets the same Caddy reset immediately before measurement. Cross-dataset
warm/cold interpretation is therefore
descriptive causal screening, not paired or confirmatory inference.

The first fully fail-closed one-block same-base H3/inner-HTTPS admission run for
this diagnostic is retained as safe artifact `d26f91c82a29ceed` (seed `305`,
NaiveFox binary build ID `b1820e20442018465574f71995c69460`). Firefox A,
Firefox B, and NaiveFox
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

A later ten-block predecessor run was discarded in full when an old Caddy QUIC
flow continued after a measured client Initial. The deterministic Caddy reset
above closes that contamination path; the post-reset one-block gate is retained
as safe artifact `211be50a1f1af4bd` (seed `307`). The accepted ten-block warm
screen is `4a2b14495599e0a4` (seed `20260825307`), with the matching fresh-profile
cold control `6343c2cbe6c99bfd` (seed `20260825309`). All 30 participants in each
dataset passed the cache/transport/network admission invariants.

The result rejects cache persistence as a camouflage mechanism. Firefox versus
NaiveFox remained perfectly separable in the selected 1--16, 17--32, and 1--32
packet views in this small non-inferential screen. Removing the cold CSS body
did not correct the phase boundary: packet 16 still arrived around 8 ms for
NaiveFox versus roughly 25--28 ms for Firefox. Cold body packets had merely
made part of packets 22--32 look closer by signed byte size despite arriving
roughly 127 ms too early. With 304, that accidental byte-shape similarity
disappeared and the early CONNECT/control phase became more exposed. The 250 ms
aggregate improved modestly, but Firefox A/B noise also increased and whole-run
volume differences were essentially unchanged. The useful causal conclusion is
therefore limited: response-body volume affects the observed 17--32 shape, but
resource cache state does not repair the underlying topology/scheduling gap.
No product behavior should depend on warm or persistent cache state.

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
The passive H2 collector starts the outer Caddy listener in H2-only mode and
requires exactly one proxy-port TCP identity with exactly one client SYN and
one visible ClientHello. TLS 1.3 encrypts the server's selected ALPN, so the
passive gate does not mislabel ClientHello advertisement as selection: the
H2-only listener plus successful workload completion is the keyless ALPN
contract. Inner HTTPS/H2 uses a distinct port and cannot enter this identity.

`--h2-proxy-floor-superblocks` is a same-base passive causal design fixed to
the `browser_page` workload. Each randomized block contains direct Firefox A
and B, the same Firefox using its native authenticated HTTPS-proxy path, and
NaiveFox `off`. All browsers are Selenium-prelaunched before capture; a shared
wire completion token is reset locally before every participant, and every
capture ends at completion plus the same 250 ms tail. The native-proxy and
NaiveFox participants must also complete the same workload over the dedicated
inner HTTPS/H2 fixture. The ordinary H2 transport gate rejects any participant
with more than one outer TCP identity, SYN, or ClientHello.

The safe multi-arm output treats `firefox-proxied` as an analysis-only
candidate slot. Its distance from direct Firefox estimates the unavoidable
proxy architecture floor; `off` versus direct Firefox is the total NaiveFox
gap, and the paired difference between those two distances is the remaining
product-specific signal. The fixed views are packets 1--16, packets 17--32,
packets 1--32, the first 250 ms, and the whole flow. The internal `naivefox`
label for the native Firefox candidate is only a legacy analysis-schema slot,
not a claim about which executable produced it.

Six-block same-base artifact `bc9ee30b969b6318` first measured that floor
against ordinary direct Firefox, which remains the camouflage target.  It was
captured before the MTU-1500 policy and is retained only as lifecycle evidence;
its packet-index distances must not select H2 product behavior.

Fresh fail-closed six-block artifact `8f0c619bcd38385e` (seed `2026082712`)
repeated the control with loopback MTU 1500, SYN MSS 1460, no oversized outer
TCP segment, pre-launched same-base browsers, and inner HTTPS/H2.  Native
Firefox using its ordinary authenticated HTTPS-proxy path remained only
modestly closer to direct Firefox than NaiveFox `off`: distances were
`0.05923/0.46731/0.20420/0.10955/0.24196` versus
`0.06217/0.48243/0.21055/0.11034/0.26441` for packets 1--16, packets 17--32,
packets 1--32, 250 ms, and whole flow.  The paired native-proxy-minus-NaiveFox
interval crossed zero in every view.  This screening result does not prove
equivalence with six blocks, but it localizes most current H2 residual to the
shared nested `CONNECT + inner TLS/H2` architecture rather than a distinct
NaiveFox client fingerprint.  The control diagnoses that floor; it does not
redefine the camouflage target as Firefox behind a proxy.

A server-side response-coalescing candidate was then tested because stock
`forwardproxy` flushes every target TCP read. The candidate was backward
compatible and request-opt-in: unmodified NaiveProxy clients retained the
historical path, while the experiment coalesced adjacent target reads until a
one-millisecond idle window without adding or removing bytes. Cross-run data
looked favorable, but the authoritative randomized paired artifact
`01c1250aa7a7f14a` rejected it. Relative to direct Firefox, packets 17--32
improved only `0.5279 -> 0.5194`, packets 1--32 regressed
`0.2432 -> 0.2500`, and the server-byte deficit by packet 32 increased from
about 49.3 KiB to 50.0 KiB. The patch and experimental arm were therefore not
retained. This is evidence against server read-boundary batching as a useful
H2 camouflage mechanism, not evidence that the remaining H2 signal is
unfixable.

Two request-opt-in TLS-boundary controls then replaced arbitrary target TCP
read boundaries with semantic inner-TLS boundaries without changing Variant 1
payload or padding bytes. Full-record alignment split the initial inner server
flight into seven ordinary padded response records. In 30-block paired
artifact `e742cfd3a0c19fe9` (seed `2026082717`) this improved packets 17--32
from `0.54512` to `0.49152` and packets 1--32 from `0.22555` to `0.21010`,
but worsened whole flow from `0.22023` to `0.23771` (`p=0.0288`). A causal
flight cutoff stopped alignment when the client sent its next TLS flight, so
application DATA returned to stock streaming. The authoritative 30-block
artifact `9ce752e81ecd307b` (seed `2026082723`) reproduced the early benefit
(`0.53960 -> 0.48008` for packets 17--32 and `0.22238 -> 0.20419` for packets
1--32) but also the whole-flow regression (`0.22811 -> 0.24725`, `p=0.0063`).
Safe record sequences show why: the useful initial split itself shifts bulk
records by roughly seven visible TLS-record ordinals, so ending the policy
after the flight cannot remove its new fingerprint.

A final cleartext-only control aligned just TLS Handshake records and returned
to stock streaming at the first CCS or encrypted record. Six-block artifact
`64e7a03ff341ffe2` (seed `2026082729`) removed the large ordinal shift, but it
also removed the early benefit: packets 17--32 changed `0.54819 -> 0.55041`
and packets 1--32 `0.22286 -> 0.22699`. It did not qualify for a larger run.
The server module source, binary, and temporary client header hook were
restored byte-for-byte. Together these controls close deterministic inner-TLS
record alignment as a default H2 mechanism under the no-tail-regression rule:
the multi-record split is the cause of both its local improvement and its
later distinguishability.

A final client-side diagnostic reduced only the existing Variant 1 upstream
padding range from 0--255 to 0--63 bytes, without adding cover traffic or
changing the stock server. Strict decrypted artifact
`20260827T111901Z-af8eee26` admitted the H2-only runtime hook and ordinary
CONNECT lifecycle. Six-block paired artifact `7bd5dd64a07e662f` (seed
`2026082731`) found a local packets 1--16 improvement (`0.06600 -> 0.05847`),
but packets 17--32 (`0.55279 -> 0.58610`), packets 1--32
(`0.23201 -> 0.24500`), 250 ms (`0.06341 -> 0.07417`), and whole flow
(`0.24158 -> 0.25524`) all moved away from direct Firefox. The diagnostic
hook was removed without a larger run. Reducing random client padding only
reorders the early nested-TLS phase; it does not repair the remaining H2
application-phase shape.

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

A subsequent exact-path decrypted diagnostic mapped the early phase boundary
for Firefox and `document-complete`. Both used the same full scenario/query
path and a 494-byte root response. Their packet 9 H3 control/SETTINGS and QPACK
encoder/decoder stream initialization matched semantically, with no dynamic
QPACK instructions. NaiveFox then emitted document GET HEADERS in outer packet
10, before receiving its server post-handshake bundle in packet 11. Firefox
first received the post-handshake transport work (`HANDSHAKE_DONE`, session
ticket/token, and ACK), with its navigation GET appearing only later; the exact
GET index varied across controlled traces while this ordering remained stable.
Packets 13--18 were consequently different lifecycle phases: Firefox still
contained PMTUD/ACK/navigation work while NaiveFox had already completed the
document and opened CONNECT. This rules out a post-document drain fence as the
primary repair for the earliest split and motivates an H3-only causal control
which queues the document transaction normally but releases its HEADERS only
after QUIC handshake confirmation. Packet positions remain outcomes, never the
barrier definition.

A six-block same-base H3/SOCKS screen of the bounded post-confirmation dwell
and six-resource native-parser arm is retained as safe artifact
`f07dc0cb1e282738` (seed `20260828118`). Descriptively, the candidate measured
0.13428 for packets 1--16, 0.19702 for packets 17--32, 0.12997 for packets
1--32, 0.15636 for the first 250 ms, and 0.35166 for the whole-flow view. The
shared `document-start-overlap` control measured 0.65307 for packets 17--32
and 0.44082 for the whole flow. This is the strongest controlled fixture result
so far, but it is not evidence for a product default: the settling interval is
fixed, the page has one fixed six-resource shape, and the namespace has stable
localhost latency and bandwidth. A real candidate must retain its advantage
when object sizes, response pacing, RTT, and available bandwidth vary. Those
conditions must remain experimental inputs, not values inferred from packet
indices in this artifact.

The complete follow-up screen is recorded below. Distances are ordered as
packets 1--16 / packets 17--32 / packets 1--32 / first 250 ms / whole flow.
One- and two-block rows are diagnostics only; their purpose is to reject ideas
cheaply and preserve negative results, not compare tiny differences across
independent seeds.

| Safe artifact | Variant | Blocks | Distances |
| --- | --- | ---: | --- |
| `d403f00e1eaa227e` | first 16-ms post-confirmation dwell | 1 | 0.18711 / 0.23813 / 0.16551 / 0.19828 / 0.37031 |
| `23f0f7cb5a58b9f3` | hold images for stylesheet/script response headers | 1 | 0.22357 / 0.34407 / 0.23429 / 0.21802 / 0.41994 |
| `472e78a791458b2d` | 15-ms post-confirmation dwell | 1 | 0.16362 / 0.30478 / 0.17779 / 0.20493 / 0.45740 |
| `dbdf4f41d5460ae8` | 17-ms post-confirmation dwell | 1 | unavailable / 0.47632 / 0.29546 / 0.33291 / 0.50266 |
| `d747406b98716d1c` | 16-ms dwell plus forced candidate PMTUD | 1 | 0.19568 / 0.24770 / 0.21073 / 0.23474 / 0.43461 |
| `cdee6e1b67e8d6e0` | image response-tailing class | 1 | 0.17274 / 0.17441 / 0.16196 / 0.18151 / 0.47484 |
| `2dd5b3b7dde3920a` | release images at parser finish | 1 | 0.21567 / 0.28686 / 0.18901 / 0.22479 / 0.44302 |
| `7819f87eab73fd56` | preconstruct and synchronously activate all channels | 1 | 0.24242 / 0.26010 / 0.22714 / 0.31901 / 0.44565 |
| `8acfa2e9bf69fd25` | preconstruct channels; CSS/script then image task | 2 | 0.19958 / 0.21088 / 0.17982 / 0.22551 / 0.39621 |
| `353ec240b34c1cc1` | preceding two-phase activation with 12-ms dwell | 1 | 0.17917 / 0.48634 / 0.21403 / 0.22754 / 0.46054 |
| `96e4f0433451f926` | exact Firefox User-Agent override | 1 | 0.20197 / 0.27632 / 0.18694 / 0.24588 / 0.44651 |
| `54edcd1cb261bd6b` | activate all prepared resources on next main turn | 1 | 0.23997 / 0.17685 / 0.21067 / 0.24860 / 0.43406 |
| `1e3021030d540730` | sequentially open all resources at parser finish | 1 | 0.25506 / 0.34635 / 0.23902 / 0.24322 / 0.45066 |
| `dcb8ca36bf574d7a` | confirmation only, then 16-ms resource delay | 1 | 0.13137 / 0.21756 / 0.15600 / 0.20159 / 0.39685 |
| `86c38709c99b66a0` | split delay: 8 ms before root and 8 ms before resources | 1 | 0.17666 / 0.51109 / 0.26344 / 0.24243 / 0.50742 |
| `fe08089dde0ac15c` | one socket-thread turn after handshake confirmation | 1 | 0.26040 / 0.62362 / 0.34047 / 0.32277 / 0.53048 |
| `6c1da178f259ec37` | confirmation-gated six-resource arm, 65536-byte page base | 4 | 0.10039 / 0.33793 / 0.15398 / 0.16820 / 0.41965 |
| `02da1771dd44e616` | confirmation-gated six-resource arm, 1048576-byte page base | 4 | 0.08761 / 0.42522 / 0.17234 / 0.16295 / 0.38674 |
| `99b99423b650ff11` | explicit 16-ms dwell, 65536-byte page base | 4 | 0.18995 / 0.13934 / 0.15949 / 0.19980 / 0.37883 |
| `4f29ad91fa7f29f6` | explicit 16-ms dwell, 1048576-byte page base | 4 | 0.14023 / 0.18591 / 0.13661 / 0.16593 / 0.36313 |
| `a8977a8f77e3e129` | explicit 16-ms dwell, 20-ms one-way delay and 20 Mbit/s | 4 | 0.19669 / 0.55600 / 0.24601 / 0.16671 / 0.44047 |
| `54ea85af8f8ade5a` | cancel resource bodies on first tunneled application bytes, 20-ms one-way delay and 20 Mbit/s | 1 | 0.34306 / 0.63746 / 0.37520 / 0.30147 / 0.51192 |
| `4f6a2d5f9d5d7f7e` | cancel each resource at response headers, confirmation gate, 20-ms one-way delay and 20 Mbit/s | 1 | 0.15433 / 0.51371 / 0.24360 / 0.18631 / 0.45597 |
| `b269891ca820c21c` | cancel all resources after all request HEADERS are sent, confirmation gate, shaped link | 1 | 0.28176 / 0.49624 / 0.32068 / 0.22380 / 0.55319 |
| `79bd0652b13f9da6` | cancel all resources on first `RECEIVING_FROM`, confirmation gate, shaped link | 1 | 0.29122 / 0.53469 / 0.32290 / 0.19148 / 0.54446 |
| `3ac531854f16583d` | preceding response stop plus first post-confirmation transmitted-packet gate, shaped link | 1 | 0.17466 / 0.61024 / 0.27430 / 0.14788 / 0.49282 |
| `783fb4014bc2c827` | ordinary pre-confirmation root scheduling, shaped link | 1 | 0.22187 / 0.30808 / 0.23385 / 0.20438 / 0.40084 |
| `9041afc666c4d27a` | ordinary pre-confirmation root scheduling, shaped link | 4 | 0.21975 / 0.41939 / 0.24150 / 0.20281 / 0.37755 |
| `98ff5b58e8acabbf` | ordinary pre-confirmation root scheduling, unshaped localhost | 4 | 0.13015 / 0.42408 / 0.19173 / 0.18586 / 0.40104 |
| `52eb19521edabbc2` | prepare four images, open them together on the next main-thread turn, shaped link | 1 | 0.10745 / 0.24945 / 0.15401 / 0.18001 / 0.36747 |
| `3db870baccdd8047` | preceding next-turn image scheduling, shaped link | 4 | 0.14180 / 0.42302 / 0.20045 / 0.19663 / 0.35352 |
| `69ee3fd2559c47e1` | preceding next-turn image scheduling, unshaped localhost | 4 | 0.11254 / 0.43472 / 0.17955 / 0.17351 / 0.41992 |
| `f9a240071240a55b` | open images in `2+1+1` successive main-thread turns, shaped link | 1 | 0.19743 / 0.37870 / 0.22054 / 0.20749 / 0.38823 |
| `f5cfa9eff387313d` | next-turn image scheduling plus exact Firefox application UA token, shaped link | 1 | 0.12563 / 0.62333 / 0.24182 / 0.17464 / 0.39800 |
| `906de419f0b1605d` | release CONNECT after stylesheet and deferred-script response HEADERS, shaped link | 1 | 0.09888 / 0.53957 / 0.19785 / 0.19506 / 0.37942 |
| `e0780953cbebd8a4` | release CONNECT as soon as all six request transactions commit, shaped link | 1 | 0.22115 / 0.69741 / 0.32680 / 0.22386 / 0.47965 |
| `9c0b28cfd8163df8` | defer first-resource-HEADERS CONNECT admission by one main-thread task, shaped link | 1 | 0.10124 / 0.25761 / 0.15044 / 0.17865 / 0.36383 |
| `5247083ce674bb8c` | preceding task-deferred admission, shaped link | 4 | 0.18055 / 0.42214 / 0.21098 / 0.16720 / 0.38589 |
| `e003c4bcdeae430f` | release CONNECT after the first consumed resource body buffer, shaped link | 1 | 0.22977 / 0.35266 / 0.23174 / 0.20103 / 0.38039 |
| `390cc24ccb6ef8c9` | preceding first-resource-body-buffer admission, shaped link | 4 | 0.11745 / 0.29471 / 0.15293 / 0.16835 / 0.34363 |
| `4e4edd7c53b91735` | preceding first-resource-body-buffer admission, unshaped localhost | 4 | 0.10945 / 0.44214 / 0.18431 / 0.17123 / 0.38250 |
| `c9643a1e000b3c2c` | preceding first-resource-body-buffer admission, 65536-byte page base, shaped link | 1 | 0.20808 / 0.72418 / 0.32068 / 0.22956 / 0.45906 |
| `8925499c80a97688` | preceding first-resource-body-buffer admission, 1048576-byte page base, shaped link | 1 | 0.21060 / 0.41138 / 0.24301 / 0.19447 / 0.42297 |
| `47adda8f2a4b7783` | preceding first-resource-body-buffer admission, 65536-byte page base, shaped link | 4 | 0.08814 / 0.33185 / 0.14079 / 0.13174 / 0.31845 |
| `07c422cacf441cd1` | preceding first-resource-body-buffer admission, 1048576-byte page base, shaped link | 4 | 0.14312 / 0.39571 / 0.18047 / 0.15179 / 0.36178 |
| `d62dc8f4ca95d6e1` | preceding first-resource-body-buffer admission, 50-ms one-way delay and 5 Mbit/s | 1 | 0.15058 / 0.38089 / 0.19917 / 0.10000 / 0.29532 |
| `4606cb015d86e68a` | preceding first-resource-body-buffer admission, 50-ms one-way delay and 5 Mbit/s | 4 | 0.09429 / 0.42424 / 0.16478 / 0.07301 / 0.31681 |
| `d25a8910b028a6d5` | release CONNECT after the deferred-script body buffer, shaped link | 1 | 0.22782 / 0.46535 / 0.25761 / 0.22191 / 0.40645 |
| `4a893926cca282f9` | release deferred images and CONNECT on the first resource body buffer, shaped link | 1 | 0.26798 / 0.51989 / 0.32041 / 0.32007 / 0.47166 |
| `ee1d57bb7b7817f0` | give CONNECT document-equivalent H3 priority `u=0, i`, shaped link | 1 | 0.27480 / 0.62412 / 0.33962 / 0.30322 / 0.43876 |
| `c276f92a62ecafdd` | open two images next turn and two on first resource body buffer, shaped link | 1 | 0.29429 / 0.44772 / 0.31302 / 0.28711 / 0.45058 |
| `e26fad3d3d44af3c` | open all six resources together next turn, then admit on first body buffer, shaped link | 1 | 0.22284 / 0.71720 / 0.32656 / 0.24533 / 0.49797 |
| `dcbd84d7270ad4d2` | admit after first body buffers from two distinct resources, shaped link | 1 | 0.12912 / 0.29712 / 0.17971 / 0.18160 / 0.39582 |
| `e37b8b45df2c72f7` | cross-process stylesheet rendezvous before resource activation, shaped link | 1 | 0.10459 / 0.23626 / 0.14855 / 0.19669 / 0.36922 |
| `a0ead86d96ae38c9` | preceding cross-process resource activation, shaped link | 4 | 0.12889 / 0.32005 / 0.16100 / 0.15227 / 0.34415 |
| `cdf15c6b3cc11142` | request one-byte ranges for the four image-cover responses, shaped link | 1 | 0.20619 / 0.47585 / 0.23629 / 0.20118 / 0.42462 |
| `bfd151c6ae8b307f` | keep CONNECT admission, but delay local SOCKS success until a second resource progresses, shaped link | 1 | 0.12217 / 0.56947 / 0.24059 / 0.21827 / 0.38174 |
| `6f3abdd953241f36` | promote CONNECT locally from generic `u=4` to default-wire `u=3`, shaped link | 1 | 0.28571 / 0.67590 / 0.35053 / 0.27983 / 0.44583 |
| `516a52ecdf7325fe` | request a one-byte range only for the largest, third image-cover response, shaped link | 1 | 0.20167 / 0.20686 / 0.21134 / 0.26134 / 0.38629 |
| `3ccc3e566c17bf55` | preceding selective third-image Range request, shaped link | 4 | 0.13225 / 0.38692 / 0.17637 / 0.14720 / 0.36567 |
| `b4404c4a0ed5af8f` | delay image activation by half the measured root request-to-response interval, shaped link | 1 | 0.11576 / 0.43308 / 0.18672 / 0.17680 / 0.37198 |
| `c3e2674327356bfc` | release all prepared images from the main-thread idle queue, shaped link | 1 | 0.24089 / 0.66662 / 0.30821 / 0.22581 / 0.46435 |
| `78cbd9ef5ace8048` | open each prepared image after the preceding image request commits, shaped link | 1 | 0.30343 / 0.37128 / 0.30186 / 0.28342 / 0.45118 |
| `ef003b01f019fe12` | open three images next turn and the final image on the following main-thread turn, shaped link | 1 | 0.30779 / 0.58293 / 0.35812 / 0.31232 / 0.44871 |
| `d1c08c6718fa278f` | admit CONNECT after the first successful image response HEADERS, shaped link | 1 | 0.11972 / 0.38830 / 0.17140 / 0.18762 / 0.38223 |
| `e386025177b9432c` | make only the first preamble-owned H3 CONNECT stream incremental at generic urgency, shaped link | 1 | 0.12355 / 0.42295 / 0.17830 / 0.16060 / 0.35493 |
| `f9c4c26050a2988a` | bound H3 duplex-pump reads to Gecko's 4096-byte default segment, shaped link | 1 | 0.23503 / 0.36697 / 0.25550 / 0.24040 / 0.41194 |
| `3500eb894d951e57` | demote only the first preamble-owned H3 CONNECT from generic `u=4` to `u=5`, shaped link | 1 | 0.25290 / 0.49920 / 0.28958 / 0.22799 / 0.47932 |
| `7770ec81beea4b8a` | release prepared images after CSS and script request commits, shaped link | 1 | 0.28935 / 0.39986 / 0.29283 / 0.29333 / 0.42113 |
| `c3d1643c1f966245` | enable standard PLPMTUD on the page-mode H3 proxy route, shaped link | 1 | 0.31425 / 0.55231 / 0.35320 / 0.29369 / 0.52470 |
| `5720930db5b46be2` | open the prepared script one main-thread turn after CSS, then images on the following task, shaped link | 1 | 0.12781 / 0.38475 / 0.19378 / 0.19891 / 0.35858 |
| `a6de9bd760c43a2e` | root-rendezvous native parser, one CSS control, shaped link | 1 | 0.20613 / 0.46121 / 0.25115 / 0.21464 / 0.48865 |
| `a6de9bd760c43a2e` | separate activation-process native parser, one CSS control, shaped link | 1 | 0.09027 / 0.47334 / 0.18639 / 0.16738 / 0.44881 |
| `a6de9bd760c43a2e` | full-process native parser rendezvous, one CSS control, shaped link | 1 | 0.20120 / 0.45172 / 0.24339 / 0.20945 / 0.49445 |

None of the rejected rows improved the early and whole-flow views together.
They are not timing constants to carry into production. The fixed dwell
remains an opt-in research mechanism while event-driven or measured-network
alternatives are evaluated.

The first predeclared size matrix exposed a provenance mismatch rather than a
fixed-dwell robustness result. Safe artifact `f07dc0cb1e282738` used dirty
revision `3d005bd87721599c99b1ad5c20516243e5961e51` and libxul digest
`8fe40a8763fa79e11ccf2ba9a0864288aa2c9c25ae515b36410d362211f3d312`.
The two size artifacts used the later committed binary
`59ec40bb48fe587f445c924600c3dbf4698ef414e9c5d04cb98e4ec78d5ab6b0`,
whose six-resource arm waits for handshake confirmation but has no dwell; its
16-ms setter belongs to the separate `document-handshake-confirmed` mode.
Therefore the 0.33793/0.41965 and 0.42522/0.38674 pairs are retained as valid
confirmation-gate measurements, but they neither validate nor reject the
fixed-dwell candidate. Fixed-dwell size testing must first rebuild an
explicitly identified arm and record its binary digest. Results from different
mechanisms must not be compared as a robustness matrix merely because the arm
label was reused during an uncommitted experiment.

The corrected fixed-dwell size screen rebuilt the six-resource arm explicitly
with `SetProxyPreambleHandshakeDwell(16)`. Both new artifacts identify NaiveFox
build ID `fa86f083ff437943123733a262ce02ff` and libxul digest
`eb462bba4c0ffe345bbc5961b5a9d90b70b55ae65b3850dc7ad51715b9c181b7`.
At a 65536-byte page base, packets 17--32 measured 0.13934 and whole flow
0.37883; at 1048576 bytes they measured 0.18591 and 0.36313. Both sizes retain
the main advantage over `document-start-overlap`, whose corresponding values
were 0.63512/0.45239 and 0.60016/0.45256. The tradeoff is consistent and
limited but real: candidate packets 1--16 exceeded control by 0.05848 and
0.05072, while 250 ms exceeded it by 0.01797 and 0.00784. Thus object size
alone does not invalidate the dwell, but these four-block diagnostics do not
answer slower-link or RTT robustness and do not promote the timer to default.

The predeclared shaped-link run rejects the fixed dwell as a general default.
Artifact `a8977a8f77e3e129` uses the same identified 16-ms binary at the default
page size, with a verified 20-ms one-way delay, 20-Mbit/s loopback rate, and
receive-side timestamps. Packets 17--32 regressed to 0.55600, only slightly
below the `document-start-overlap` control's 0.60207, and whole flow regressed
to 0.44047 versus 0.45630. Packets 1--16, 1--32, and 250 ms were effectively
near the control rather than retaining the localhost advantage. Firefox A/B
whole durations remained close (means 541.7 and 547.6 ms), so this is not
explained by a broken control envelope. The opt-in fixed dwell remains a useful
laboratory oracle, but a production successor must derive release from actual
transport progress or native scheduling under the current path conditions.

An event-driven attempt to stop all still-active outer resource requests on
the first positive client-to-target tunnel application bytes is also rejected.
One-block shaped diagnostic `54ea85af8f8ade5a` used the same 20-ms one-way,
20-Mbit/s profile. It regressed packets 17--32 to 0.63746 and whole flow to
0.51192, while whole server bytes still exceeded the Firefox midpoint by
724090 bytes. By the time the inner application signal reached the main
thread, the localhost origin and H3 stack had already queued the large cover
bodies ahead of `netem`; cancelling their channels could not withdraw those
datagrams. The result rules out late channel cancellation, not causal
handoff generally. A viable successor must bound or suspend response-body
delivery before QUIC accepts the excess bytes, then use observed transport or
application progress to release or retire that bounded cover.

Three earlier resource-stop boundaries and one transport gate refine that
negative result. Stopping each channel at `OnStartRequest` in
`4f6a2d5f9d5d7f7e` was still too late: whole server bytes exceeded the Firefox
midpoint by 723282 bytes. Stopping all six channels as soon as H3 reported all
request HEADERS sent in `b269891ca820c21c` reduced the excess over the matched
`document-start-overlap` arm from roughly 73 KiB to 1231 bytes, but packets
17--32 remained 0.49624 and whole flow regressed to 0.55319. Delaying that stop
until the first resource `NS_NET_STATUS_RECEIVING_FROM` in
`79bd0652b13f9da6` admitted one path-controlled congestion flight, about
42.8 KiB beyond the control, without improving the phase: packets 17--32 were
0.53469 and whole flow 0.54446. These are one-block diagnostics, but the large
volume and phase effects are sufficient to reject the three boundaries.

Artifact `3ac531854f16583d` also replaced the wall-clock dwell with a causal
transport gate: the root transaction remained queued until Neqo's transmitted
packet counter proved that one packet had been emitted after handshake
confirmation. Combined with the first-response stop, this improved packets
1--16 and 250 ms descriptively, but packets 17--32 regressed to 0.61024 and
whole flow measured 0.49282. Thus neither a generic next socket turn nor an
actually transmitted post-confirmation packet reproduces the localhost
16-ms result on the shaped path. No timer, byte threshold, cancellation, or
transport-gate code from these diagnostics is retained.

Removing the six-resource arm's handshake-confirmation gate is retained as the
next production-oriented candidate. Packet-sequence inspection showed that on
the shaped path direct Firefox sent its navigation request near 46 ms, before
transport confirmation, while the gated preamble did not send its root request
until roughly 104 ms. The gate therefore introduced an RTT-dependent phase
error that the localhost 16-ms dwell had hidden. The replacement uses ordinary
H3 transaction scheduling; it contains no timer, RTT estimate, packet-count
barrier, body-size rule, or response cancellation.

The one-block shaped screen `783fb4014bc2c827` measured 0.30808 for packets
17--32 and 0.40084 whole. Four-block follow-up `9041afc666c4d27a` corrected the
optimistic early estimate but retained the material advantage: 0.41939
[`0.38538`, `0.47197`] for packets 17--32 versus control 0.58280, and 0.37755
[`0.36902`, `0.39015`] whole versus control 0.44734. The 250-ms view paid a
0.02960 descriptive penalty. Unshaped artifact `98ff5b58e8acabbf` measured
0.42408 packets 17--32 and 0.40104 whole, so it gives up the fixed dwell's
fixture-specific localhost advantage while avoiding its shaped-link collapse.
Both four-block runs identify NaiveFox build ID
`22e819b8198cdbefef03759293a2b6d1` and libxul digest
`10233fa0a10e43bb6f993d84d9b5d01205ee1f5597d43dac00876abf9628a284`.
This candidate is retained because its scheduling rule generalizes across RTT;
its roughly 0.42 packets-17--32 residual remains the next optimization target,
not an acceptable endpoint.

The next experiment split native parser discovery from image-channel
activation without using elapsed time or response progress. Stylesheet and
script channels still open in the parser callback; the four image channels are
prepared there and opened together by the next ordinary main-thread task. The
one-block shaped diagnostic `52eb19521edabbc2` was optimistic. Four-block
artifact `3db870baccdd8047` measured 0.42302 for packets 17--32, statistically
unchanged from the ordinary-scheduling candidate's 0.41939, but improved
packets 1--16 from 0.21975 to 0.14180 and whole flow from 0.37755 to 0.35352.
The same binary on unshaped localhost in `69ee3fd2559c47e1` measured 0.43472
for packets 17--32 and 0.41992 whole, small descriptive regressions from
0.42408 and 0.40104. All three artifacts identify NaiveFox build ID
`7304222bbd34555aef8b8526c6fc70f9` and libxul digest
`04f77b4941775bb7d3c3a7a093e5e8a9ea1096de6f80e0c4c214e633178731d5`.
The split is retained as an experimental scheduling primitive because it
materially reduces shaped whole-flow residual without encoding fixture
latency, bandwidth, or resource size. It is not promoted to default by these
screens: packets 17--32 remain unresolved, and the localhost whole-flow cost
needs to be recovered. Follow-ups should vary the image release batches on
ordinary event-loop turns and reject any schedule that only moves the shaped
whole-flow score.

The first such follow-up is rejected. It opened two images on the first
deferred main-thread turn and one image on each of the next two turns, matching
the `2+1+1` grouping seen in one decrypted Firefox trace without adding a
timer. The first attempted capture, private failure artifact
`8e5e218c073f4b6f`, stopped after its first candidate because the validator
incorrectly required every deferred image open to precede even stylesheet and
script `WAITING_FOR` callbacks. The log proved the intended `2+1+1` execution;
the validator was temporarily corrected to require causal ordering per stream,
and the capture was rerun. Safe one-block artifact `f9a240071240a55b`
measured 0.37870 for packets 17--32 and 0.38823 whole. This did not improve
both target views over the one-turn diagnostic's 0.24945/0.36747, so no
four-block confirmation was spent. The binary identified build ID
`7477b35b421bf7f30eaa264106fa625c` and libxul digest
`9a1a314167aeaaa2cec3277efa671335c80605a35bf237ae1cbbbe968368c43c`.
The batching code and its temporary validator relaxation were removed; the
single next-turn image release remains the working candidate.

Exact User-Agent parity does not repair the remaining phase. Decrypted traces
showed that every candidate GET used the otherwise native
`Mozilla/... Gecko/20100101 /156.0a1` string because the lean product has no
application UA name, while same-base Firefox used the corresponding
`Firefox/156.0` token. This affects every QPACK/HEADERS block independently of
RTT and resource size, so it was combined with the retained next-turn image
schedule. One-block shaped artifact `f5cfa9eff387313d` regressed packets
17--32 to 0.62333 and whole flow to 0.39800; the earlier isolated UA screen's
low packets-17--32 value therefore did not compose with the robust scheduler.
The binary identified build ID `acb2989186801906f1b1936d4fc9c4da` and libxul
digest `a220cfc201cc62361a82322b8570bf5b5ffae0f96bfc6c7bddf8f9901fb06701`.
The temporary HTTP-handler override was removed. `Alt-Used` and
`Sec-Fetch-User` remain untouched as well: the former must come from an actual
Alt-Svc mapping and the latter from a genuine user navigation, so fabricating
either would change semantics rather than recover native scheduling.

Waiting for both blocking-resource response HEADERS is also rejected. The
causal rationale came from the decrypted reference, where image requests
followed the stylesheet and deferred-script response HEADERS. The candidate
kept the existing next-turn image activation but delayed CONNECT admission
until both stream 1 and stream 2 had a successful response start. This uses no
body progress or fixed delay, but one-block shaped artifact
`906de419f0b1605d` regressed packets 17--32 to 0.53957 and whole flow to
0.37942. The binary identified build ID `064783d96e998ba682750149381d4d92`
and libxul digest
`79c52c4ab62be2e8033694b8d241fd5b394410133aa69fc8c3c9317e2da3afc8`.
Additional response waiting therefore moves CONNECT in the wrong direction on
the shaped path and would impose an avoidable slow-server penalty. The
blocking-HEADERS condition and its temporary lifecycle label were removed.

Releasing at the opposite edge, before any resource response HEADERS, is
rejected more strongly. The six native channels and their real H3 transactions
were still required, but CONNECT was admitted as soon as all request
transactions reported `NS_NET_STATUS_WAITING_FOR`. This rule has no
slow-server wait and no body-size input. Nevertheless one-block shaped
artifact `e0780953cbebd8a4` regressed packets 17--32 to 0.69741 and whole flow
to 0.47965. The binary identified build ID
`8072c68d5db43c67e0af4af1fb3cb879` and libxul digest
`a109abf36fe551224012910accbb91c64a254a77f8f7b393df0a4ce83d922d26`.
Together with the two-HEADERS result, this brackets the useful admission
boundary at the first resource response HEADERS: moving CONNECT by a whole
network event in either direction is harmful. The all-request-committed label
and release were removed; later work should vary task scheduling at the first
HEADERS boundary rather than add another network wait.

Deferring that first-HEADERS boundary by one ordinary main-thread task did not
survive replication. One-block shaped artifact `9c0b28cfd8163df8` looked
promising at 0.25761 for packets 17--32 and 0.36383 whole, so it was expanded
to the predeclared four-block screen. Artifact `5247083ce674bb8c` measured
0.42214 [`0.37848`, `0.45709`] for packets 17--32 and 0.38589
[`0.36954`, `0.40224`] whole, versus 0.59751 and 0.46798 for its matched
control. Relative to the retained next-turn image scheduler's four-block
0.42302/0.35352, the task handoff did not change the target packet window and
regressed whole flow; packets 1--16 also regressed from 0.14180 to 0.18055.
Only the first-250-ms view improved, from 0.19663 to 0.16720. Both artifacts
identify NaiveFox build ID `a354ee4827c7cb896758774641abf777` and libxul
digest
`ab98ac290569de1224f862ffc8c5a688c24b6ba544bbb8806b01eb3eb3a235d5`.
The task deferral, temporary lifecycle label, and validator allowance were
removed. The result also demonstrates why single-block improvements are used
only to decide whether a replication is worth collecting.

Waiting for the first successfully consumed resource body buffer is retained
as the stronger causal candidate. All six request transactions must still be
committed first, and the barrier is armed only after `OnDataAvailable` has
consumed one complete positive resource buffer. The rule has no elapsed-time
constant, byte threshold, packet count, response-size assumption, or wait for
the complete resource. It follows actual response progress, while failures,
short reads, empty responses, and response HEADERS without body data do not
release CONNECT.

One-block shaped artifact `e003c4bcdeae430f` measured 0.35266 for packets
17--32 and 0.38039 whole. Four-block replication `390cc24ccb6ef8c9` improved
all five requested views: 0.11745 [`0.07902`, `0.15589`] for packets 1--16,
0.29471 [`0.25981`, `0.32960`] for packets 17--32, 0.15293
[`0.11978`, `0.18609`] for packets 1--32, 0.16835
[`0.13830`, `0.19840`] for the first 250 ms, and 0.34363
[`0.32339`, `0.36381`] whole. Its matched control measured
0.19749/0.54571/0.24224/0.17254/0.43664. Relative to the retained next-turn
image scheduler's earlier four-block 0.42302/0.35352, the new boundary reduces
the target packets-17--32 residual materially and also lowers whole-flow
residual. Both new artifacts identify NaiveFox build ID
`89a4a90ceb64afd12b532dbe7ee67913` and libxul digest
`eb4ed5f1dd05c40d1ab17a4419f5ca238ef620854149b501ff0a40bc4b32dcb4`.
The mechanism and fail-closed lifecycle validation are retained for
cross-size and unshaped robustness screens; these gate-sized runs do not yet
promote it to the product default.

Unshaped localhost artifact `4e4edd7c53b91735` used the identical binary and
measured 0.44214 [`0.37933`, `0.50496`] for packets 17--32 and 0.38250
[`0.37084`, `0.39415`] whole. The target packet window is descriptively close
to the prior next-turn scheduler's localhost 0.43472, while whole flow improves
materially from 0.41992 and also improves over ordinary scheduling's 0.40104.
Packets 1--16 were effectively tied with the matched control
(0.10945 versus 0.10739); the first 250 ms paid a limited 0.01567 descriptive
cost. The causal body-buffer boundary therefore retains its shaped-link gain
without repeating the previous scheduler's localhost whole-flow regression.
Resource-size and slower-link screens remain required because the first body
callback depends on actual server and path progress even though it contains no
fixed time or byte threshold.

The first one-block size screens are mixed and remain diagnostic. At a
65536-byte page base, artifact `c9643a1e000b3c2c` measured 0.72418 for packets
17--32 and 0.45906 whole; at 1048576 bytes, artifact `8925499c80a97688`
measured 0.41138 and 0.42297. In both cases the candidate still improved all
five views over its matched `document-start-overlap` control, whose target
pairs were respectively 0.88117/0.58286 and 0.58080/0.53749. The high absolute
residual at 65536 bytes is not acceptable evidence of size robustness, while
the simultaneous control movement makes a single-block rejection equally
unsafe. The smaller-size condition is therefore selected for four-block
replication before changing the mechanism.

That replication rejects the apparent small-resource failure rather than the
mechanism. Four-block artifact `47adda8f2a4b7783` measured 0.33185
[`0.23825`, `0.41572`] for packets 17--32 and 0.31845
[`0.28458`, `0.34555`] whole at the same 65536-byte page base. All five views
improved over the matched control: 0.08814/0.33185/0.14079/0.13174/0.31845
versus 0.17985/0.55323/0.23522/0.16931/0.43635. The earlier 0.72418 one-block
target was therefore an unstable Firefox-envelope diagnostic, not evidence
that a small response deterministically releases CONNECT at the wrong phase.
Large-resource and slower-link replication are still needed.

The large-resource replication also retains the candidate. Four-block
artifact `07c422cacf441cd1` measured 0.39571 [`0.29175`, `0.49966`] for packets
17--32 and 0.36178 [`0.33620`, `0.38808`] whole at the 1048576-byte page base.
All five views again improved over the matched control:
0.14312/0.39571/0.18047/0.15179/0.36178 versus
0.20947/0.63845/0.26754/0.16318/0.48312. The target residual is higher than
the default-size 0.29471/0.34363 and small-size 0.33185/0.31845, but it does
not collapse as resource duration grows because admission waits only for the
first delivered buffer. Together the two four-block size endpoints reject a
fixed object-size dependency; lower bandwidth and higher RTT remain a separate
robustness axis.

The slower-link screen retains the causal candidate where the fixed dwell had
failed. One-block artifact `d62dc8f4ca95d6e1` at 50-ms one-way delay and
5 Mbit/s measured 0.38089 for packets 17--32 and 0.29532 whole. Four-block
replication `4606cb015d86e68a` measured 0.42424
[`0.26067`, `0.58780`] and 0.31681 [`0.28349`, `0.35695`], versus matched
control 0.73615/0.48809. The wider packets-17--32 interval records real
slow-path variability, but its upper endpoint remains below 0.6 and the mean
retains a large improvement. All other views also improve:
0.09429/0.16478/0.07301 for packets 1--16, packets 1--32, and 250 ms, versus
control 0.21167/0.29908/0.18354. These runs use the same build ID and libxul
digest as the default-size, localhost, and size screens. The event-driven
boundary therefore generalizes across the tested RTT/rate range without
encoding either value; broader network and server-TTFB coverage remains future
validation rather than a reason to add a fixed pause.

Releasing CONNECT after only the first successfully consumed resource byte is
rejected after replication. This moved the barrier inside the first positive
`OnDataAvailable` callback: one byte was read successfully, CONNECT was
admitted, and only then was the rest of that callback drained. It therefore
tested whether the Necko callback size itself caused the remaining target
distance, without adding a timer, waiting for a complete response, or requiring
a server-dependent body size. One-block shaped artifact `d21d819300a31433`
measured 0.24021/0.46981/0.29011/0.24730/0.47484. Four-block replication
`178f43ceda873438` measured 0.16215 [`0.12115`, `0.18980`] for packets 1--16,
0.30412 [`0.24145`, `0.33866`] for packets 17--32, 0.17515 [`0.13674`,
`0.20065`] for packets 1--32, 0.15015 [`0.13084`, `0.16092`] for the first
250 ms, and 0.34798 [`0.32732`, `0.36864`] whole. The target pair does not
improve the retained complete-buffer candidate's replicated 0.29471/0.34363,
while packets 1--16 and 1--32 regress from 0.11745/0.15293. Both artifacts
identify NaiveFox build ID `3464761a0c1b92b78d8459f1f1232e16` and libxul
digest
`3c7df4963e6efe05b7a883485db9c89707704b46bfa75d2c84fcbb9d96902560`.
The byte-level state and lifecycle labels were removed; admission again
follows successful consumption of the complete first Necko resource buffer.

Releasing after the first successful bounded input-stream read is also
rejected. The resource drain loop requested at most its existing 4096-byte
scratch capacity, accepted any positive short read, logged the actual returned
length, and admitted CONNECT before draining the rest of the callback. Thus it
tested an intermediate input-stream boundary without requiring a 4096-byte
body or waiting for resource completion. One-block shaped artifact
`5e675ae5652db1fb` measured 0.16059/0.43240/0.21810/0.17386/0.38263. Neither
packets 17--32 nor whole improves the retained complete-buffer candidate's
replicated 0.29471/0.34363, and the early packet views are also higher. The
binary identified build ID `7d0dc259cb8d106445836d2d7df4e24e` and libxul
digest
`d8be3d8949b82fbcd612715d26a481f3b0a9a69de0621424cb74a3fd2bcd88e3`.
The read-level state, byte diagnostic, and validator markers were removed.
Together with the replicated byte-level result, this rejects callback-drain
granularity as the next optimization axis; the complete first buffer remains
the simpler and slightly stronger boundary.

Selecting the deferred script's first body buffer instead of the first ready
resource is rejected. The semantic stream-2 rule avoided a timer and byte
threshold, but one-block shaped artifact `d25a8910b028a6d5` regressed packets
17--32 to 0.46535 and whole flow to 0.40645; the first 250 ms also regressed to
0.22191, above its matched control's 0.20256. This is worse in both target
views than the first-ready-resource candidate's one-block 0.35266/0.38039 and
replicated 0.29471/0.34363. The binary identified build ID
`28ab359279fe717eeb2c33df8d60556e` and libxul digest
`536b1ee21f409a357bbec3b3cd20ec83a85da4995146ebf5e3069671f557425a`.
The stream-specific condition and lifecycle labels were removed; admission
again follows whichever valid resource delivers body data first.

Using that same first body event to open the four prepared images is also
rejected. This attempted to reproduce the decrypted Firefox ordering in which
image GETs followed blocking-resource response progress, while retaining real
channels and avoiding a time or size constant. Instead one-block shaped
artifact `4a893926cca282f9` regressed packets 17--32 to 0.51989 and whole flow
to 0.47166; packets 1--16, 1--32, and 250 ms also rose to
0.26798/0.32041/0.32007. Its matched control measured 0.64491/0.52655 for the
two target views, so the arm still differed from the baseline control but was
clearly worse than retained first-body admission with next-turn images. The
binary identified build ID `bf1fbfed66c17fe744a7d4c0266e8669` and libxul
digest
`a338ef2b94b96c24b16994aa3097c608555289d07d46f33a0170c28886e84a32`.
The body-triggered image release and validator ordering were removed; image
activation again occurs on its independent ordinary main-thread turn.

Giving the tunneled CONNECT document-equivalent H3 priority is rejected.
Upstream Gecko maps an ordinary `TYPE_OTHER` transaction to urgency 4, while
the direct Firefox navigation in the decrypted trace carried `priority: u=0,
i`. The experiment combined `UrgentStart`, `PRIORITY_HIGHEST`, and incremental
delivery only for the exact six-resource H3 body-buffer arm, producing that
same priority value without a timer. One-block shaped artifact
`ee1d57bb7b7817f0` regressed packets 17--32 to 0.62412 and whole flow to
0.43876; the other views rose to 0.27480/0.33962/0.30322. The binary identified
build ID `cc5666f97b91aa87a297258769c0e408` and libxul digest
`e84a1d875957baf7234fe4e3329d1d11b988523c6e9baa83789c21cfc13df45e`.
CONNECT carries a document but also competes with the outer cover streams, so
the direct document priority over-promotes it. The new parameter, class flags,
and priority marker were removed; ordinary CONNECT priority is retained.

Splitting image activation `2+2` across two causal events is rejected. Streams
3--4 opened on the existing next-main-turn boundary, while streams 5--6 stayed
prepared until the first valid resource body buffer; only then did they open
and commit before CONNECT admission. This avoided every timer, byte threshold,
and complete-resource wait, but one-block shaped artifact
`c276f92a62ecafdd` measured 0.44772 for packets 17--32 and 0.45058 whole,
worse than the retained candidate's replicated 0.29471/0.34363. Its packets
1--16 and first-250-ms distances, 0.29429 and 0.28711, also exceeded the
matched control's 0.26596 and 0.20538. The experimental binary identified
build ID `2150abeda012a918bdb326ffa2ad35d0` and libxul digest
`13eac5dee0318aa619fb9d0a9c54a4a2b07441e1298e0792719cebe9d8a7e15a`.
The partial body-triggered image release and its validator ordering were
removed. The result also rejects treating the incomplete decrypted trace's
apparent image grouping as sufficient causal evidence.

Combining all-resource next-turn activation with first-body-buffer CONNECT
admission is rejected. The stylesheet, deferred script, and four images were
all prepared during parser output and opened together on one ordinary main
thread turn; CONNECT still waited for the first valid resource body buffer.
Although each boundary was event-driven, their composition produced the worst
recent target result: one-block shaped artifact `e26fad3d3d44af3c` measured
0.71720 for packets 17--32 and 0.49797 whole, versus the retained candidate's
replicated 0.29471/0.34363. Packets 1--16, packets 1--32, and 250 ms were also
0.22284/0.32656/0.24533. The binary identified build ID
`d0b5461df5032118d8d786feb737dae2` and libxul digest
`51010bd3f4df3fb69c4d0f4c15b48fbf623d60d9ac6d5e5e80f228770079f2bb`.
The all-resource deferral and generalized validator markers were removed;
stylesheet/script activation remains in the parser callback and only images
use the following main-thread turn.

Waiting for first body buffers from two distinct resources is rejected. The
boundary counted at most one successfully consumed buffer per response, so it
did not depend on elapsed time, buffer length, or full resource completion and
remained reachable for both small and large resources. One-block shaped
artifact `dcbd84d7270ad4d2` measured 0.29712 for packets 17--32, effectively
the retained four-block candidate's 0.29471, but whole flow regressed from
0.34363 to 0.39582. Its other views were
0.12912/0.17971/0.18160. The binary identified build ID
`1086f63b07794dd90ff8f74b69d36489` and libxul digest
`5e240d15097355e6bd0a129171d9988f327d167332ff0993b87626281f01b541`.
The per-stream progress accounting and second-resource lifecycle markers were
removed; the first valid resource body buffer again admits CONNECT.

A cross-process parser-cadence rendezvous is rejected after replication. This
experiment mirrored the root response into the existing persistent native
activation child while the parent lean parser independently discovered all six
resources. CSS/script channels stayed prepared until the child reported its
stylesheet descriptor and that URL matched the parent's CSS exactly; images
then opened on the following main-thread turn. CONNECT retained the first-body
boundary. Thus the new ordering used actual parser and cross-process IPC work,
not a timer or resource-size threshold. The first attempt, private failure
artifact `983af062e23d11a3`, failed closed before the process route started
because the page mode's parent parser target was rejected by a CSS-only process
precondition. It timed out and produced no admissible distances; the
precondition was corrected before measurement.

One-block shaped artifact `e37b8b45df2c72f7` looked promising at packets
17--32, measuring 0.23626, but whole flow rose to 0.36922 and 250 ms to
0.19669. The predeclared four-block replication `a0ead86d96ae38c9` did not
retain that target gain: packets 17--32 measured 0.32005 [`0.26996`,
`0.37014`], worse than the retained candidate's 0.29471 [`0.25981`,
`0.32960`]. Whole flow was effectively unchanged at 0.34415 [`0.31509`,
`0.37297`] versus retained 0.34363 [`0.32339`, `0.36381`]. The replicated
other views were 0.12889/0.16100/0.15227 for packets 1--16, packets 1--32,
and 250 ms. Both valid artifacts identify build ID
`ae852c7c0e6698d875e921452f25b346` and libxul digest
`6a6d141cb374a0694e2650aef0d03c70bf9891ce16c12ac89788b39d02e40971`.
The extra process startup, root mirroring, rendezvous state, and generalized
validator lifecycle were removed; their complexity did not improve either
replicated target view.

Removing duplicated image-cover bodies is rejected in both tested forms. A
first attempt changed the four image requests to standard `HEAD` while leaving
CSS/script as `GET`, so first-body admission remained reachable. Private
failure artifact `4a11c4f36f4af40c` showed all six transactions committed and
CONNECT succeeded, but the H3 HEAD streams never delivered the terminal Necko
callbacks required for a normal six-resource drain before the fixed cutoff.
The harness therefore failed closed and published no distances; weakening the
drain contract to accept those hanging channels was not considered.

The follow-up retained `GET` and requested `Range: bytes=0-0`, which completed
normally and would fall back to full bodies on an origin that ignored Range.
Despite removing almost all image response payload without a timer or a
resource-size-dependent completion rule, one-block shaped artifact
`cdf15c6b3cc11142` regressed packets 17--32 to 0.47585 and whole flow to
0.42462. Packets 1--16, packets 1--32, and 250 ms also rose to
0.20619/0.23629/0.20118. The binary identified build ID
`a485895d17c1e5964fdbd488362870c3` and libxul digest
`944e7468380d2be8bac89df91da8ee0a3d7b07adc0f082ff30415a091d19a21a`.
The HEAD/Range mutations and their validator markers were removed. The result
shows that retained whole-flow behavior depends on the natural multiplexed
cover-body shape, not merely eliminating duplicated bytes.

Separating outer CONNECT readiness from local SOCKS success is rejected. The
retained first resource body buffer still opened CONNECT, so the natural root,
six cover requests, and CONNECT transaction were unchanged. `TunnelReady`
instead waited until a second distinct successful cover response had made
body progress; a normally completed empty response also counted, avoiding a
fixed byte minimum and preserving reachability for zero-length resources. This
causal gate follows network pacing instead of wall-clock time, but one-block
shaped artifact `bfd151c6ae8b307f` regressed packets 17--32 to 0.56947. Whole
flow was 0.38174 and the remaining views were
0.12217/0.24059/0.21827. The binary identified build ID
`09690181c93c9a2530add74f51d1cc01` and libxul digest
`d11ecf6711f0577cb58b269155b35e3f5a26d728f966eaf2a847791485328024`.
The secondary preamble callback and SOCKS admission state were removed. This
result shows that holding the inner browser after CONNECT reorders the target
window much more destructively than moving CONNECT itself to the same second
resource boundary.

A moderate H3 CONNECT priority is also rejected. Gecko maps a generic channel
to urgency 4 and the `Unblocked` class to urgency 3. Because non-incremental
urgency 3 is the HTTP Priority default, this experiment changed local H3
scheduling without adding a Priority request field, unlike the earlier
document-equivalent urgent experiment. One-block shaped artifact
`6f3abdd953241f36` nevertheless measured 0.67590 for packets 17--32 and
0.44583 whole; its other views were 0.28571/0.35053/0.27983. The binary
identified build ID `5dfb91f3521b088e904b9b97976fce6f` and libxul digest
`6122c5a00c58b7a7e0e8900764bb0bc8a4b323913eb6c33315d9d7132d11ca9b`.
The `Unblocked` class assignment was removed. Together with the rejected
urgent CONNECT arm, this rules out simple CONNECT promotion as the next
default direction.

Selectively reducing only the largest, third image cover is rejected after
replication. This kept the first two image bodies and final API image intact,
while requesting `Range: bytes=0-0` only on stream 5; an origin ignoring Range
still returned its full representation. One-block shaped artifact
`516a52ecdf7325fe` initially looked promising at 0.20686 for packets 17--32,
with 0.38629 whole. The predeclared four-block artifact
`3ccc3e566c17bf55` did not reproduce it: packets 17--32 measured 0.38692
[`0.32087`, `0.45108`] and whole flow 0.36567 [`0.32993`, `0.39984`], both
worse than retained 0.29471/0.34363. Its other views were
0.13225/0.17637/0.14720. Both artifacts identify build ID
`faf694cd8abf0bee3cd5a2881496842e` and libxul digest
`4c332156e983765b277039439ddf57b4b498b871f90bb3b3cf0e3008b7c9c96d`.
Server-byte diagnostics remained close to the full-body retained arm, so the
fixture ignored the range and the single-block movement came only from the
extra request-header/QPACK shape. The selective header was removed; neither
wholesale nor one-stream Range requests improve the replicated target views.

Delaying image activation by half the measured root request-to-response
interval is rejected. This replaced a fixed dwell with a per-connection
`nsITimedChannel` observation and fell back to ordinary next-turn activation
when timing was unavailable. Private failure artifact `67ac2a4d35b104a7`
first exposed that the measured half-flight was 62 ms on the nominal
20-ms-one-way profile; CSS/script therefore committed before the delayed image
opens, and the original strict lifecycle validator correctly refused that new
ordering. A temporary validator accepted only the explicit positive H3 timing
marker and the exact split order (blocking commits, four image opens, four
image commits), without weakening count or drain checks. The resulting
one-block shaped artifact `b4404c4a0ed5af8f` measured 0.43308 for packets
17--32 and 0.37198 whole, with other views
0.11576/0.18672/0.17680. Its binary identified build ID
`269e859d236cac7d6f240bbf54740b79` and libxul digest
`fd77fac46590b5df6257c0543463c9fc2f5e28cef4d71e9845148c1a05e3c8fd`.
The timing state and temporary validator branch were removed. The measured
interval includes connection/queue work and is not an RTT estimator; dividing
it by another fitted constant would recreate the fixture-specific timing
problem this experiment was meant to avoid.

Main-thread idle-queue image activation is rejected. All four images remained
prepared together, but their release runnable used Gecko's
`EventQueuePriority::Idle` instead of an ordinary next-turn dispatch. This
introduced no timer, byte threshold, or resource completion dependency, yet
one-block shaped artifact `c3e2674327356bfc` regressed packets 17--32 to
0.66662 and whole flow to 0.46435. Packets 1--16, packets 1--32, and 250 ms
were also 0.24089/0.30821/0.22581. The binary identified build ID
`628395e375ae5a29ee6179e4e11c555e` and libxul digest
`cc8dc4c497fcdf48ff6ec2a7b23e6bafd0352e13b5060d06ceebf763adf381fb`.
The idle dispatch and its temporary exact lifecycle marker were removed.
Yielding behind normal network callbacks is much coarser than ordinary parser
cadence and is not suitable as a product scheduling cause.

Sequential image activation at request-commit boundaries is rejected. The
first prepared image still opened on the ordinary next main-thread turn, but
each remaining image opened only after the preceding image emitted Necko's
`WAITING_FOR` request-commit status. This was a causal chain with no timer,
response-size threshold, body wait, or bandwidth estimate, and the strict
temporary lifecycle validator required the exact open/commit alternation.
One-block shaped artifact `78cbd9ef5ace8048` measured 0.37128 for packets
17--32 and 0.45118 whole; packets 1--16, packets 1--32, and 250 ms also rose
to 0.30343/0.30186/0.28342. Its binary identified build ID
`0f7fc9070bdf29f71a3f2819d3aac833` and libxul digest
`c05045cac8f79043c6776a07ee752c3cf663972e87d8848f49796beb349ea8dd`.
The request-commit chain and temporary validator ordering were removed. A
transaction commit is deterministic but too coarse as a parser-cadence proxy:
serializing all four image submissions harms both the target window and the
whole-flow view.

Opening three images on the ordinary next turn and the final image on one
following main-thread turn is rejected. This approximated separate image-load
tasks without a timer or network/body dependency, while keeping the split much
smaller than the earlier `2+1+1` experiment. The strict temporary validator
required exactly streams 3--5 with `next-main-turn` cause and stream 6 with
`following-main-turn` cause. One-block shaped artifact `ef003b01f019fe12`
regressed packets 17--32 to 0.58293 and whole flow to 0.44871; its other views
were 0.30779/0.35812/0.31232. The binary identified build ID
`832d32cad6dad72e455ff6244008e97f` and libxul digest
`deb6c282de08da7094482573a9f1c3a01247517e7616b26a9340602ecc29fa35`.
The second dispatch and temporary validator marker were removed. Together
with the `2+1+1`, idle-queue, and request-commit results, this shows that even
a one-image event-loop split is too coarse for the desired H3 burst shape.

CONNECT admission after the first successful image response HEADERS is
rejected. The gate still required all six request commits, then used the first
2xx image response start as a causal, body-size-independent boundary. The
temporary validator required that exact image-header observation between all
commits and CONNECT admission. One-block shaped artifact
`d1c08c6718fa278f` kept packets 1--16 low at 0.11972, but packets 17--32 rose
to 0.38830 and whole flow was 0.38223; the remaining views were
0.17140/0.18762. The binary identified build ID
`17baed0a323625250d73534cc06beb36` and libxul digest
`f71697b265a4486de74890ee7c3d41b0b49a1dcbbeaf05ea9fa33f27b5eb0cd2`.
The image-header state and temporary validator marker were removed. Moving
admission from first body progress to a specific response class does not
improve the target window.

An incremental H3 CONNECT at unchanged generic urgency is rejected. The first
preamble-owned tunnel set Gecko's incremental class-of-service bit without
promoting urgency; this produced the standard `u=4, i` Priority signal and
allowed Neqo's incremental scheduling, while later SOCKS connections retained
their normal behavior. The first private run `0e250cd494490c3f` failed closed:
the implementation initially applied the bit to a second non-preamble SOCKS
connection too, and the exact validator rejected two markers. After scoping it
to the preamble operation generation, one-block shaped artifact
`e386025177b9432c` measured 0.42295 for packets 17--32 and 0.35493 whole; the
other views were 0.12355/0.17830/0.16060. Its binary identified build ID
`006056bd843c9e546c26872cab533982` and libxul digest
`a3233c9126b1adb1941c0b25665faf7385a6adce331e6d688d631c07d54685f9`.
The CONNECT API flag, generation scope, and exact marker were removed. Whole
flow remained close to retained, but DATA interleaving and the additional
Priority signal worsened the target packet sequence substantially.

Bounding H3 duplex-pump reads to Gecko's default 4096-byte network segment is
rejected. Ordinary H3 transactions ask for that quantum, whereas the product
pump historically reads up to 64 KiB; the experiment applied the upstream
runtime segment value symmetrically in both tunnel directions without timers,
Priority changes, or resource-size assumptions. One-block shaped artifact
`f9c4c26050a2988a` measured 0.36697 for packets 17--32 and 0.41194 whole,
with other views 0.23503/0.25550/0.24040. Its binary identified build ID
`c7d69296800b54f9e5f0d1b36fe5965b` and libxul digest
`658ac4fb91a913157197094b681c6df40d325779c7f9d36e35066f8cdb27e9d8`.
The pump quantum and diagnostic marker were removed. Sequential small writes
remain eligible for aggregation inside Neqo, so matching the ordinary read
size does not reproduce ordinary multi-stream packetization.

Demoting the first preamble-owned H3 CONNECT by one scheduler step is
rejected. The experiment left the stream non-incremental and changed only its
generic Gecko priority from `u=4` to `u=5`, so the already-committed cover
transactions could win H3 scheduling without any timer, RTT, throughput, or
resource-size constant. A strict temporary marker required exactly one such
CONNECT on the owning preamble generation and rejected the priority on every
later SOCKS connection. The first private capture
`7c50b7798ccafd5c` failed closed because the initial validator assumed that
the marker logged before the synchronously delivered preamble result; the
actual `AsyncOpen` callback logged the result before the call returned. After
correcting that diagnostic ordering, one-block shaped artifact
`3500eb894d951e57` measured 0.49920 for packets 17--32 and 0.47932 whole, with
other views 0.25290/0.28958/0.22799. Its binary identified build ID
`187868e93104742a431cd46725894c6c` and libxul digest
`bb8627aac5326cc30ae838e4c09aaf3a2d0c3c5748c09a5261cf712d6a1e3312`.
The priority flag and exact validator marker were removed. Both one-step
promotion and one-step demotion of CONNECT now regress the target window, so
further urgency-only tuning is not a useful direction.

Holding the four prepared image channels until both blocking requests reach
`NS_NET_STATUS_WAITING_FOR` is rejected. Decrypted packet inspection motivated
the experiment: direct Firefox's packets 17--32 contained the stylesheet and
deferred-script GETs followed by stylesheet response data, while the retained
candidate had already placed its four image GETs in that window. The new gate
depended only on CSS/script request commitment, then opened all images on the
next ordinary main-thread task; it did not wait for a response, body bytes,
elapsed time, RTT, or bandwidth. The strict temporary validator required both
blocking commits before the task and all four image commits afterwards.
One-block shaped artifact `7770ec81beea4b8a` nevertheless measured 0.39986
for packets 17--32 and 0.42113 whole, with other views
0.28935/0.29283/0.29333. Its binary identified build ID
`6163349c5fa7afdb18ac1a857435cb79` and libxul digest
`179fb8677622398e29caf982acc7f4fafb4731afa3c10f4921e9d2021f778661`.
Moving the image HEADERS out of the target window alone left mismatched ACK
and response-flight positions and worsened the 250-ms view. The request gate,
task, and temporary lifecycle validation were removed.

Forcing standard PLPMTUD on the page-mode outer H3 route is rejected.
Decrypted inspection showed that direct Firefox's server-side response flight
had advanced from 1280-byte QUIC packets to larger path-validated datagrams by
packet 18, while the retained preamble's dense response flight stayed at 1280
until roughly packet 92. The experiment removed the route's PMTUD opt-out for
both preamble and CONNECT, keeping one proxy identity and asking Neqo's normal
PLPMTUD algorithm to discover the actual path; it did not prescribe an MTU,
packet size, delay, resource size, or bandwidth. One-block shaped artifact
`c3d1643c1f966245` regressed packets 17--32 to 0.55231 and whole flow to
0.52470, with other views 0.31425/0.35320/0.29369. Its binary identified build
ID `7bbd9e285252c729dad1a826d134cd8e` and libxul digest
`36384253d67b6738ff033c195b2b9e5bf3369f96651aff2f02ab9fd2944c74fe`.
The additional probe and ACK transitions outweighed any later DATA-size
alignment. The route flag and exact validator marker were removed; production
must not force path probing merely to reproduce this fixture's packet size.

Deferring only the prepared script by one main-thread turn is rejected. The
stylesheet opened immediately, the script opened from the first queued task,
and the four already prepared images opened from the existing following task.
This introduced no timer, response gate, packet counter, resource-size check,
or link-rate dependency. One-block shaped artifact `5720930db5b46be2`
measured 0.12781/0.38475/0.19378/0.19891/0.35858: the whole-flow result stayed
near the retained candidate, but packets 17--32 were materially worse than the
retained four-block replication's 0.29471. The measured binary identified build
ID `9482377fbe41a310f96d94869eaccb8a` and libxul digest
`1694ba3b3513757d37cc2fb0c7f33e1426b8b048a7d45b0756c3c5d3f8bb8df6`.
The extra script task and its lifecycle marker were removed. A single
event-loop split is therefore insufficient to reproduce native parser/resource
scheduling and is not retained.

The existing separate-process native-parser paths were screened before trying
to generalize their IPC protocol from one stylesheet to the full six-resource
tree. In shaped paired artifact `a6de9bd760c43a2e`, the root-rendezvous,
activation-process, and full-process arms respectively measured packets 17--32
at 0.46121, 0.47334, and 0.45172, with whole-flow distances 0.48865, 0.44881,
and 0.49445. All three used the real streamed HTML body, native speculative
parser output, and event/IPC rendezvous rather than a time delay, but all were
materially worse than the retained six-resource candidate's replicated
0.29471/0.34363. The measured binary identified build ID
`9482377fbe41a310f96d94869eaccb8a` and libxul digest
`1694ba3b3513757d37cc2fb0c7f33e1426b8b048a7d45b0756c3c5d3f8bb8df6`;
the earlier deferred-script branch in that binary is scoped to the
resource-committed page mode and was inactive for these three controls. The
process boundary by itself therefore does not supply useful browser-like H3
pacing, so the larger full-tree IPC generalization was not implemented.

Publishing the complete six-resource lean-parser descriptor stream through the
full activation process is rejected in its all-at-once form. This follow-up
filtered non-network document-context descriptors in the child, transferred
the CSS, parser-blocking script, and four image descriptors with their native
fields over IPDL, opened each native parent channel as its paired process event
arrived, and admitted CONNECT only after the parser finished and all six
requests committed. It introduced no timer, RTT, packet-count, response-size,
or bandwidth dependency, and the strict validator proved six distinct ordered
descriptor identities and clean process/channel completion. One-block shaped
artifact `9448a2a3eb7ce638` nevertheless measured
0.24504/0.67761/0.31918/0.24062/0.47613. The full tree improved the 250-ms view
relative to the two process controls in that block, but its packets 17--32
distance was worst and whole remained high because all resource requests were
front-loaded. The measured binary identified build ID
`d80db43f07d97bdd93a9bc1e7a882b79` and libxul digest
`77724d918db91ab7b1ac849f350ef3105b86c1c9f8f0c4f3e40066dc13886fa2`.
The descriptor transport is useful diagnostic scaffolding, but immediate
publication of every discovered image is not a candidate default.

Releasing all four prepared images only after the parser-blocking script's
successful native `OnStop` is also rejected as a candidate default. This was a
causal response gate: slower transport or a larger script naturally postponed
the image wave, while no elapsed-time, RTT, packet, or byte threshold was
consulted. The validator accepted either ordering between final preamble drain
and target establishment so that small resources cannot invalidate a healthy
sample, but required script actor completion before the exact four image opens.
One-block shaped artifact `576d37b44e6dbb09` improved the eager full-process
result to 0.21196/0.33680/0.22121/0.19824/0.44306. Packets 17--32 were still
worse than the retained page candidate's four-block 0.29471 and whole remained
well above its 0.34363. Diagnostic signed packet sizes showed the four image
responses becoming another dense flight around packets 23--24 instead of the
two later direct-reference groups. The measured binary identified build ID
`4f6cbbaf6282135c2c7c87a4d7745688` and libxul digest
`04e91ec7013735bb76cea355159585262cb9219638ff02cae0c457b72daee21f`.
Script completion is a useful adaptive boundary, but one four-image wave is
still too coarse.

Splitting the images into two causal waves is rejected after direct paired
replication. The first two prepared images opened after successful script
`OnStop`; the second two opened only after both first-wave channels received
successful response headers. Thus body sizes could not accelerate the second
wave, while slower RTT or server response naturally delayed it, and no timer,
packet index, byte count, or fixture-specific resource length was used. In the
four-block shaped artifact `07829ee5bb0b5ad3`, this arm measured
0.13976/0.43070/0.18764/0.14583/0.39706. The retained page arm in the same
randomized superblocks measured 0.13185/0.37573/0.16462/0.13457/0.35488 and
ranked closer in every view. The two-wave binary identified build ID
`43873d9f2e5168673d6b87baa5a98ec0` and libxul digest
`a564f1152f5da8e7b221dd37c7dcc9dea3f5b351bdf0d29c2048bdc44814d8e5`.
All four samples passed strict process identity, descriptor-order, two-wave,
request-commit, and terminal-drain validation, so the rejection is numerical
rather than a lifecycle failure. The full-tree IPC and wave scheduler are
removed rather than retained as dormant product complexity.

A fresh retained-candidate decrypted capture did not pass strict admission and
must not be treated as wire evidence. Private artifact
`20260828T220612Z-2af3b849` logged all six request commits, first resource body
progress on the deferred-script stream, successful CONNECT admission, and a
normal six-resource drain. The pcap decoder, however, could not assign one
coalesced response HEADERS occurrence unambiguously to every asset stream, so
`h3_decrypted_arm_summary.py` failed closed with missing asset response-header
evidence. Manual private inspection suggests the next hypothesis only: the
candidate issued CSS/script and all four image GETs in a dense early cluster,
whereas the direct reference issued its image GETs later in two groups after
blocking-resource response progress. No result from this failed trace enters
the passive table or validates a production change.

Strict decrypted artifact `20260826T051112Z-deaf291f` admits the H3-only
`tree-resource-committed-overlap-css` experiment. It uses the same root and
64-KiB stylesheet as `tree-complete-css`, but releases CONNECT only after
Gecko reports `NS_NET_STATUS_WAITING_FOR` for that stylesheet. The trace proves
one QUIC identity and ClientHello, root completion before the resource commit,
CSS GET before CONNECT, CSS response after CONNECT, and identical HTTP
semantics/content length in the paired control. Two-block passive artifact
`c1bd74f7b299c8a1` found a descriptive improvement for packets 17--32 and
1--32, but a regression for packets 1--16 and the first 250 ms. It is a causal
screen, not evidence for tuning asset volume or selecting a product default.

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
after its sample monitor starts. Fixture cold reset and namespace convergence
finish before this measurement boundary; the monitor is then active before
NaiveFox startup or reference-browser startup, fails closed if its own process
or netlink parser fails, and drains queued events before confirming the sample
boundary closed.

Packet-shape screening inside the private namespace sets loopback MTU 1500 and
disables GRO, GSO, TSO, UDP segmentation, and GSO-list aggregation. H2 rejects
outer TCP payload segments larger than 1460 bytes. H3 rejects
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
`tree-complete`, `tree-early-overlap`, `tree-resource-committed-overlap-css`, `tree-root-overlap`,
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
