# Application-capacity transport experiment

This campaign is confined to an experimental branch and a separate Caddy
module repository. It does not change product defaults, the published matrix,
the stock-server contract, or the deferred export/publication state.

## History preflight and distinct hypothesis

Reviewed the complete all-ref carrier/finite/multiplexing history, the carrier
and finite sections of CAPTURE.md, corrected finite source in
`dbf22ddf2906`, and archived metadata for `9de8396c3b9573d7`,
`adf8c603b590072c`, and `25de70a0225eed54`.

- Ordinary GET changed the method of a long-lived raw carrier, not its graph.
- Finite read-through removed body-completion barriers but retained
  data-driven uploads and independently replenished receive windows.
- Budgeted finite responses reduced request count, but did not substitute
  useful data for a real application's fixed response capacity.
- Optimistic local acknowledgement and multiplexing alone are already tested
  families; neither is claimed as the new causal contribution.

The new premise is that a genuine application lifecycle grants capacity;
queued target bytes occupy that capacity and displace filler. No external
CONNECT exists in the candidate. Multiple logical streams share ordinary
origin GET/POST bodies. Necko/NSS/Neqo still own all external transport.
The assertion that nested CONNECT explains most residual is a hypothesis,
not an established decomposition of all earlier measurements.

## First prototype boundary

The first prototype uses the verified same-base Firefox browser as an actual
SPA transport worker, with a bounded loopback WebSocket IPC bridge offering
SOCKS5 and HTTP CONNECT locally. The WebSocket is local IPC only, never the
outer Caddy transport. The minimal NaiveFox binary is retained unchanged as a
contemporaneous CONNECT control. This deliberately tests an achievable
full-browser upper bound before porting the scheduler to the lean runtime;
results must not be described as measured improvements in the minimal binary.
The earlier full-process experiments kept CONNECT and therefore did not test
this protocol/capacity replacement mechanism.

## Preregistered application and comparison

The site is a small gallery/telemetry SPA: real HTML, CSS, deferred JavaScript,
four SVG images, API synchronization, finite event responses and finite media
chunks. The original lifecycle was two interactive rounds, eight download
rounds, and two interactive rounds. Each round obtains one upload slot and one
download slot, then updates its display through the browser animation event.
Upload slots are 4096 bytes; interactive responses 24576 bytes; download
responses 131072 bytes. Queue occupancy cannot add a slot, advance a round,
change its capacity, or postpone a partially filled response. After the active
job the app can enter an ordinary bounded idle-poll mode. A separate upload
state belongs to functional tests, not an implicit change to the primary run.
After the repeatability failure below, v1 explicitly freezes 16 rounds (two
interactive, twelve download, two interactive), identically in all SPA arms.
This grants 1,671,168 download-body bytes and 65,536 upload-body bytes per job;
the 73,728-byte static bootstrap remains additional overhead. The root is ASCII
to satisfy the existing minimal client's native-parser contract; this is not
a requirement of Firefox or of the new transport itself.

These are application-profile parameters, not packet-index targets. Record
empty/loaded capacity, useful bytes, filler, HTTP counts, latency, throughput,
memory bounds and idle cost. Fixed capacity necessarily imposes either spare
bandwidth or queueing; no universal size/link invariance is assumed.

Fresh same-base Firefox A/B controls run this same SPA against the same Caddy
build. Both current listener defaults and both candidate listeners run the
unchanged inner HTTPS/H2 browser_page workload at base 262144. The primary
views are packets 17--32 and Whole; 1--16, 1--32 and 250 ms are guardrails.
All arms use a fixed two-second capture window beginning at navigation dispatch
(not at useful-byte completion). Both the declared job and useful workload must
finish within it, with no process shutdown inside the capture. They share the same
isolated WSL namespace, MTU 1500, disabled offloads and cold profiles. Never
compare these scores numerically with the old static-page matrix as if the
reference distribution were unchanged.

First admit functionality and collect a short H2 screen, then H3 and a fresh
replication if viable. The >=20% breaking-default threshold remains a minimum
for further work, not a promotion rule from a small screen. Add a matched
append-instead-of-replace ablation before attributing a gain to substitution;
the full browser and new application profile are separate possible causes.
No default promotion, large matrix or resource/link sweep for a weak result.

## Admission and site constraints

Before scoring: byte-exact transfers, sequence/replay rejection, auth and
target allowlist, bounded per-stream credits/queues, half-close, cancellation,
multiple concurrent targets, and normal HTTP completion. Require one outer
connection/ClientHello; the candidate must have zero CONNECT, successful
ordinary requests, identical declared response capacities for empty/loaded
queues, and actual useful bytes replacing filler. Capture drops, network
mutation, capacity overflow or workload not completed inside the declared job
invalidate the attempt; record the failure without cherry-picked recollection.

The experimental site requires the new `naivefox_transport` Caddy module,
same-origin API/assets, no HTTP content encoding of carrier bodies, a private
transport key, and an explicit target allowlist. No arbitrary existing site
is claimed to work. Ordinary visitors receive a functioning SPA, cannot open
target connections without authentication, and use the same public HTTP
surface. All secrets, profiles, payloads and pcaps remain outside Git.

## Results

Initial H2/SOCKS admission passed the original 12-round profile: zero outer
CONNECT, 31 ordinary HTTP/2 requests, 1,146,880 response-body bytes containing
665,738 useful bytes and 479,722 filler bytes; the rest is cell/frame overhead.
The unchanged inner browser_page completed in approximately 323 ms. This is
functional evidence, not a residual result.

Admission failures retained before this pass:

- Firefox startup rejected inherited WSLg runtime ownership; the driver now
  uses a campaign-owned runtime directory and an explicit cached geckodriver.
- dumpcap could not traverse a private non-root-owned ancestor after dropping
  capabilities; captures now use a private temporary staging directory.
- The new driver generated a 24-hex completion token, whereas the established
  target fixture requires exactly 32. The target returned an error page;
  both 12- and diagnostic 64-round attempts were invalid. Correcting the token
  admitted the original 12 rounds. The 64-round variant is not a scoring profile.
- Navigation can briefly return no script state; the driver handles that
  transition instead of treating it as a transport exception.

At this initial admission stage, no residual result was available yet.
Full-browser process/memory cost and idle behavior remained unqualified.

The first randomized two-superblock screen (`h2-screen2`) stopped in block 2:
the append arm delivered only 548,910 downstream target bytes before its
12-round job ended, leaving roughly 117 KiB of the workload. Both replacement
listeners and the first append run had passed. The complete attempted schedule,
including this failure, is retained; no aggregate score is issued for the
incomplete campaign. This is a real finite-capacity/lifecycle limitation, not
the earlier completion-token bug. V1's 16 rounds are fixed before a new screen;
this costs 524,288 more downstream and 16,384 more upstream bytes per empty job.

The first H3 screen (`h3-screen1`) is invalid as a default comparison even
though useful workloads completed: the root included a UTF-8 typographic
ellipsis, which the existing lean parser rejects before resource discovery.
The old default therefore fetched only the root and retried after about 1.5 s.
No improvement against that degraded control is claimed. The shared page is
now ASCII; the harness additionally requires all six default H3 resource GETs.
The previously undocumented ASCII restriction is added to FRONTING-PAGE.md.

### Initial completed screens (two blocks each, not inferential)

Each score uses that campaign's same-SPA Firefox A/B envelope, not the historical
static-site matrix. H2 `h2-screen3` used seed 202608302; corrected H3
`h3-screen2` used seed 202608304. All 28 samples passed isolated capture/drop
checks and single outer connection/ClientHello admission. The H3 control
fetched the full six-resource tree. H2 does not fetch that tree by design.

| Protocol / arm | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| H2 default SOCKS | 0.10559 | 0.48819 | 0.22500 | 0.18465 | 0.35617 |
| H2 default HTTP | 0.11301 | 0.45346 | 0.22160 | 0.17955 | 0.34225 |
| H2 replace SOCKS | 0.10227 | 0.28549 | 0.16093 | 0.04727 | 0.18109 |
| H2 replace HTTP | 0.04628 | 0.23876 | 0.10244 | 0.06951 | 0.16111 |
| H2 append SOCKS | 0.07955 | 0.13877 | 0.09040 | 0.16064 | 0.23644 |
| H3 default SOCKS | 0.08534 | 0.26808 | 0.14163 | 0.14661 | 0.37350 |
| H3 default HTTP | 0.09436 | 0.30052 | 0.15352 | 0.15014 | 0.37102 |
| H3 replace SOCKS | 0.04394 | 0.03947 | 0.04439 | 0.07534 | 0.15069 |
| H3 replace HTTP | 0.05588 | 0.07533 | 0.07847 | 0.05473 | 0.14366 |
| H3 append SOCKS | 0.05224 | 0.25128 | 0.11832 | 0.10370 | 0.22225 |

The analyzer correctly reports `INSUFFICIENT_FOR_INFERENCE`; its intervals at
two blocks do not establish generalization. The append variant initially used
a root query parameter, so these are exploratory ablation observations, not a
clean attribution of early-packet changes. The next independently randomized
replication freezes identical outer `/` URLs and selects append only through
private server/bridge configuration. HTTP counts/capacities otherwise remain
unchanged. No minimum score or packet index is used to select samples.

Four simultaneous 98,304-byte downloads through mixed SOCKS/HTTP listeners
passed SHA-256 equality against direct fixture bodies over both H2 and H3.
They used four logical streams, zero outer CONNECTs, and the same 39 HTTP
requests / 1,671,168 response bytes as an empty v1 SPA job. Unit/race tests also
passed four concurrent 300,000-byte streams through local frontends, codec,
credit windows and half-close; 137 capture-harness/analysis tests passed.

The browser-worker cost is substantial: initial H2 inner completion was roughly
300 ms versus roughly 107 ms for CONNECT; H3 replacement roughly 260--280 ms
versus roughly 100--230 ms for an admitted default. A measured worker main
process accounts for approximately 250 MiB PSS, plus roughly 13 MiB for the
bridge, versus roughly 24 MiB for the native client. These are post-job process
snapshots, not peak-memory or CPU accounting. The fixed-capacity job spends
roughly one MiB on downstream filler for the canonical workload. It is not
qualified for other resource sizes, slow links, uploads, or long idle sessions.

### Measured cost of the initial completed screens

Means over two samples per arm, same canonical inner page. Wire bytes are
transmit-copy IP-level bytes in both outer directions, including protocol
overhead/ACKs but excluding loopback IPC. Effective page rate is fixed useful
work divided by observed completion time; this is not a sustained bulk-rate
benchmark. `costs.py` reproduces these calculations from retained numeric
features and completion results.

| Protocol/listener | Default -> replace completion | Effective page-rate drop | Default -> replace wire bytes | Wire growth |
| --- | ---: | ---: | ---: | ---: |
| H2 SOCKS | 108.16 -> 311.07 ms | 65.23% | 724,358.5 -> 1,941,993 | 168.10% |
| H2 HTTP | 106.72 -> 311.50 ms | 65.74% | 723,968 -> 1,943,297 | 168.42% |
| H3 SOCKS | 117.33 -> 277.24 ms | 57.68% | 799,581 -> 1,923,860.5 | 140.61% |
| H3 HTTP | 173.01 -> 261.43 ms | 33.82% | 799,447 -> 1,923,979.5 | 140.66% |

Thus current total traffic is about 2.4--2.7 times the default. The fixed
capacity and browser-worker impose real costs; the new protocol is not a free
speed improvement. H3 HTTP latency has a conspicuous slow control observation,
so its two-sample speed percentage is especially uncertain. Neither these
percentages nor the residual gains are claimed invariant under resource sizes,
slow links, long sessions or a future native implementation. The larger-scale
rate/idle cost remains unmeasured.

### Independent identical-URL replication

Two new complete blocks per protocol, seeds 202608305 / 202608306, directories
`h2-replication` / `h3-replication`. All 28 participants passed. These results
supersede the query-selected append variant for causal comparison, while the
earlier observations remain above. The small sample remains non-inferential.

| Protocol / arm | 17--32 | Whole |
| --- | ---: | ---: |
| H2 default SOCKS | 0.38998 | 0.38001 |
| H2 default HTTP | 0.35961 | 0.38258 |
| H2 replace SOCKS | 0.11342 | 0.20262 |
| H2 replace HTTP | 0.20376 | 0.20417 |
| H2 append SOCKS | 0.11700 | 0.27992 |
| H3 default SOCKS | 0.33851 | 0.39290 |
| H3 default HTTP | 0.31308 | 0.38931 |
| H3 replace SOCKS | 0.27528 | 0.21481 |
| H3 replace HTTP | 0.17142 | 0.17600 |
| H3 append SOCKS | 0.23173 | 0.22008 |

Whole improved again (roughly 45--55% against defaults), but H3 SOCKS 17--32
is unstable: this replication improves only 18.7%, unlike the large first-screen
point gain. Do not claim the early shape is solved or promote this prototype.
Replacement versus append reduced Whole by 27.6% in H2 but only 2.4% in this
H3 repeat; removal of CONNECT/full-browser application topology and substitution
are not yet quantitatively separated for every protocol/view.

The repeat measured H2 SOCKS/HTTP completion at 277/294 ms against 107/107 ms,
and H3 at 319/265 ms against 126/117 ms. Effective fixed-page rates fall about
61/63% and 60/56%, respectively. Wire growth stays approximately +168% (H2)
and +141% (H3). The earlier H3 HTTP rate-loss estimate of 34% was driven by the
slow control observation; the repeat demonstrates why it must not be generalized.

### Next cost campaign preflight

Before implementation, re-read the finite source in `ab19acb41807`, corrected
read-through and budgeted records through `1807e1954dd4`, and the all-ref
carrier/finite history. Earlier finite transports separately replenished GETs
and data-driven POSTs, with no application-defined fixed filler substitution.
The next trials keep the new protocol's independently specified capacity slots:
smaller cells, and optional downstream cells in a POST response instead of a
separate following GET. Decoupling network turnover from animation callbacks
is an explicit application-scheduler axis. None is claimed as an untested
general finite-exchange idea; the distinct premise remains fixed-capacity
application traffic replacing filler. Functional capacity failures will be
recorded before any residual scoring. Start with H2/SOCKS only, not full matrices.

The first cost sweep freezes these discrete profiles before functional runs:

| Profile | Rounds | Media capacity | Downstream placement | Animation wait |
| --- | ---: | ---: | --- | --- |
| v1 control | 16 | 128 KiB | separate GET | every round |
| duplex-v1 | 16 | 128 KiB | POST response | every round |
| compact | 16 | 64 KiB | separate GET | every round |
| compact-sync | 16 | 64 KiB | POST response | every round |
| compact-sync20 | 20 | 64 KiB | POST response | every round |
| compact-fast20 | 20 | 64 KiB | POST response | every fourth round |

All retain two 24-KiB interactive rounds at each end and fixed 4-KiB uploads.
No queue occupancy changes slots, capacities or cadence. Network promises
already yield through actual HTTP/IPC completion; the fast profile only stops
requiring a rendered frame after every network operation. A static app-profile
object is embedded in the fixed-size script body for normal visitors and
carrier workers alike. Useful-work completion is mandatory; failed profiles
are not sent to residual scoring or silently extended until they pass.

First functional sweep (`cost-sweep1`, one attempt/profile): v1 and duplex-v1
completed; duplex used 23 rather than 39 requests but the same body budget.
Uniform 64-KiB `compact` and `compact-sync` exhausted their 16 slots at
648,829 and 510,528 delivered bytes and failed. `compact-sync20` completed with
1,146,880 downstream bytes but about 453 ms completion, worse than v1.
`compact-fast20` failed at only 379,664 bytes: turning slots faster spent them
before useful data was available. These are admission observations, not paired
performance estimates; the first v1 startup overlapped build finalization and
is not used as a speed baseline. No residual matrix is run for failed profiles.

Next preregistered profiles `staged` / `staged-fast` have 18 slots: four 8-KiB,
two 32-KiB, ten 64-KiB, two 8-KiB. Total down capacity is 770,048 bytes, with
the same 4-KiB uplink per round. They retain separate POST/GET transactions;
the fast variant waits for animation only every second round. This moves
capacity from poorly utilized startup/tail slots into the middle, without
using queue occupancy or proxy packet indices to choose slot size.

`staged` completed one admission with 665,866 useful / 102,690 filler bytes,
but completion slowed to about 431 ms. `staged-fast` delivered only 608,740
bytes before exhausting the same job and failed. The following `staged-fast20`
trial explicitly adds two 64-KiB middle slots before the two brief tail slots:
20 rounds, 901,120 downstream bytes, animation every second round. This is a
declared capacity/speed tradeoff following a recorded failure, not a retry of
the original insufficient profile. Fixed profiles remain workload-limited;
none establishes a production scheduler for arbitrary resource sizes.

`staged-fast20` passed H2/SOCKS admission (278.840 ms useful completion),
four simultaneous mixed-listener hash-checked downloads, and all eight samples
of the two-block lean H2 screen `staged-fast20-screen`, seed 202608307.
The screen uses two Firefox controls, default SOCKS and replacement SOCKS only.
Mean useful completion was 290.602 ms against default 95.823 ms. Mean outer
wire bytes were 1,146,260.5 against 722,667: +58.62%, versus v1's approximately
+168%. Relative to the earlier v1 H2 replication, total bytes fell about 41%;
this cross-campaign accounting is not a paired latency improvement. Effective
page rate remains 67.03% below the contemporary default.

| H2 SOCKS, compact screen | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default | 0.07550 | 0.53341 | 0.22463 | 0.18242 | 0.34928 |
| staged-fast20 replacement | 0.03574 | 0.25208 | 0.10079 | 0.09142 | 0.16205 |

The screening point gains are 52.74% (17--32) and 53.60% (Whole), with much
less filler. Status remains `INSUFFICIENT_FOR_INFERENCE`. The reference profile
changed with the application, so these absolute residuals cannot rank v1 and
staged across different screens. This profile is a cost candidate, not a
default, universal workload budget or established speed improvement.

### Prefix-delivery cost hypothesis

History preflight re-read the corrected finite read-through record (including
the negative Whole results in `dbf22ddf2906` / `c75be29167da`) and the retained
metadata identified above. Read-through itself is not new. The distinct test
here removes a local `response.arrayBuffer()` barrier while retaining the
new application's fixed-capacity replacement protocol. No slot replenishment,
queue-dependent sizing, new delay, GET/POST boundary or target-read batching
is introduced. Compare `staged-fast20` with `staged-stream20`: identical slots,
uploads and animation cadence. Only the latter validates and delivers the used
prefix before draining the response's filler. The next slot still waits for
successful full response completion. A short/truncated/malformed body aborts
the session even if a valid prefix was already delivered.

Add deterministic split-boundary, prefix-before-EOF, full-drain, truncation and
backpressure tests before admission. Record actual early-prefix deliveries;
do not infer benefit merely from enabling a flag. First H2 functional admission
and a short paired timing check; no full matrix. A targeted slower-link test is
appropriate only to isolate this barrier, not to claim general link robustness.

Eight deterministic JavaScript tests and the complete Go race suite passed.
H2 admission at full loopback speed completed, but recorded zero early useful
prefixes: this condition does not exercise the supposed advantage. The targeted
`prefix-10mbit` check then ran three randomized pairs, seed 202608308. A separate
queue discipline rate-limited only the outer port to 10 Mbit/s in both directions;
local IPC and target links stayed unshaped. All six workloads completed with
zero queue drops and identical fixed body budgets. This timing-only test does
not use transmit-side captures for shaped-link residual scoring.

Buffered completion averaged 1,074.466 ms, prefix delivery 1,040.491 ms: 3.16%
less time / 3.27% more effective fixed-page rate. Each streaming run recorded
2--3 useful prefixes delivered with 64--112 KiB of aggregate filler still
unconsumed by the reader. This is consumption/IPC evidence, not a measurement
of how many bytes were still in flight on the wire. Three pairs do not establish
a robust speed gain; the total application job was not shorter and traffic
capacity did not decrease. Four simultaneous mixed-listener hash probes also
passed over H3 at 10 Mbit/s. Keep this as an opt-in building block, not a claimed
solution to the main speed deficit, and do not run its own residual matrix.

The more substantial cost candidate remains `staged-fast20`, without prefix
delivery. Its H2 screen earned one lean H3 check (two blocks, seed 202608309)
with unchanged inner workload and no link shaping. This is a targeted second
protocol admission, not the full listener/resource/link matrix.

That H3 screen stopped at its first replacement (`sample-001`). All seven
inner page/resource requests returned 200 and the inner document was complete,
but no completion beacon reached the target. The bridge/server delivered
665,695 downstream bytes and only 3,142 upstream bytes; successful canonical
runs send roughly 3,400 upstream bytes including the final beacon. The fixed
20-round application had no upload opportunity after its final render callback.
Do not score this incomplete screen or generalize the H2 candidate to H3.

History preflight for a terminal application confirmation checked the all-ref
carrier/finite commit history, current completion/tail notes and this retained
failure. Earlier finite streams flushed/replenished their data-driven windows;
they did not join the new SPA's final render with another fixed-capacity API
transaction. The new `staged-commit20` hypothesis retains the 20-round job and
adds one `/api/action` POST/response after its existing final animation callback.
Each direction is exactly 4096 bytes, regardless of queue state. No extra
timer, retry-until-complete, target-size threshold or changed media slot is
introduced. This gives late application acknowledgements a transport slot at
about 0.8% more body budget, not proof of general long-session liveness.
Functional admission comes first; if admitted, preregister lean H2/H3 screens
with seeds 202608310/202608311 rather than extending the failed job in place.

`staged-commit20` passed one H3 admission at 282.860 ms, but the first
replacement in its H3 screen failed again (`sample-003`): 665,700 downstream
bytes delivered, 3,222 upstream, inner document complete, no final beacon.
The action transaction itself completed normally. A single terminal slot does
not guarantee transport liveness after the application job. The screen is
incomplete/unscored; the planned H2 screen is cancelled rather than spending
more captures on a profile that has already failed this gate. Retain the
profile and deterministic final-render/confirmation tests as a negative result,
not as the repair or a recommended operating mode.

The next structural requirement is an ongoing interactive/idle lifecycle, not
more finite tail slots fitted to this beacon. Any continuation must retain
bounded queues, multiplexing, fixed capacities within each application state,
an explicit idle traffic budget and state-transition evidence. The reduced-cost
H2 screen is promising but does not solve this general-liveness limitation;
v1 and all current product defaults remain unchanged.

Harness follow-up: retain per-sample rather than cumulative inner HTTP status
counts; require exact method/path multiplicities as well as byte budgets; and
wait for both useful completion and the SPA inside the existing fixed capture
window. Previously the polling loop stopped at SPA completion even if the
last delivered bytes might still produce a target acknowledgement. Waiting
does not add a slot, retry, timer in the application, or change the two-second
capture bound. The two failed screens above remain unscored, not retrospectively
reclassified. Their missing upstream beacon bytes still indicate a finite-slot
limitation, but the stricter observation separates that from a bookkeeping race.

The corrected two-second drain audit (`commit-h3-drain-audit`) still failed:
663,271 downstream bytes, 3,141 upstream, inner document still interactive.
Thus merely waiting for the late marker does not repair the finite lifecycle.
Final checks for this block passed 160 capture/analysis tests, five focused
carrier harness tests, eleven JavaScript tests and all four Go race-test
packages in the isolated namespace. No minimized/full Firefox rebuild was needed.

## Continuous lifecycle preregistration

At the user's request, the next implementation is `continuous-v1`. History
preflight checked all-ref carrier/finite/idle polling, wake, state and budget
commit records and the retained finite-tail failures. The old finite adapters
already replenished data-driven windows; merely keeping them alive is not the
new premise. Here useful bytes still replace filler in fixed application-state
capacities, with bounded multiplexing and no outer CONNECT.

Keep `staged-fast20`'s initial application job as a matched starting point, then
continue indefinitely. Interactive, download, upload and mixed activity grant
four-transaction leases with fixed capacities within a lease: 4/8 KiB up/down,
4/64 KiB, 128/8 KiB and 128/64 KiB, respectively. Coarse queue pressure selects
state only at lease boundaries. After the initial job, display painting no
longer blocks network turnover. This does not promise identical total traffic
for empty and loaded sessions: state transitions intentionally reflect activity.

Idle uses one ordinary GET `/api/events/idle`, a fixed 512-byte response held
for at most 30 seconds, ending sooner on server work. A local IPC notification
wakes a pending idle poll with a normal fixed 4-KiB POST; it must not wait for
the timeout. No outer WebSocket, background rapid polling, unbounded response,
or hard-coded delay before useful transfer is added. At rest the carrier-body
budget is 512 bytes per 30 seconds (61,440 bytes/hour), excluding HTTP/TLS/IP
overhead; actual idle wire cost must be measured rather than equated to this
body-only bound. This bounded long poll is a normal application request, not
a replacement long-lived raw tunnel. Session expiry and cancellation must
account for the in-flight poll.

Admission must cover late server bytes, wake after an idle interval, sequential
and concurrent local connections, larger downloads and uploads, per-stream
credits, cancellation and bounded state. The initial app-complete marker no
longer terminates transport. A fixed-window residual capture may end with one
valid idle poll pending; completed active exchanges must retain exact capacity
accounting. Normal Firefox controls execute the same initial application and
idle behavior, without replaying a candidate's observed state trace. First
qualify liveness and idle cost, then short paired residual/timing measurements.

The first continuous H3 canonical admission completed, then remained alive in
idle; twelve dynamic slots were needed after the initial job. Those extra
327,680 response bytes are real cost, not hidden behind the initial marker.
The H2 four-connection hash probe passed. The first extended H2 session admitted
a 1-MiB download after idle but stopped on the delayed-response digest: the
strict inner H2 fixture only reverse-proxies `/camouflage*`, so `/delay`
returned an empty response instead of exercising target delay. Add explicit
`/camouflage/delay` and `/camouflage/slow-upload` aliases to the existing fixture
handlers; do not interpret that invalid route test as transport success/failure.

Corrected extended sessions passed on H2 and H3: initial four mixed-listener
downloads, a 1-MiB download after idle, a genuinely delayed 1.5-second server
response while the application was idle, a hash-checked 1-MiB slow upload,
four concurrent 512-KiB downloads, then another small connection. Twelve
logical streams reused each running browser transport. All bytes matched;
active cells retained their declared capacities, and one idle poll cancelled
normally when the worker shut down.

H3 additionally stayed idle for 65.256 seconds: 2,649 outer IP-level bytes in
15 packets, with two new application poll starts and no active state changes.
That short observation extrapolates to about 146 kB/hour (not a measured hour
or a universal link-independent bound). A fresh 4-KiB download then completed
in about 29 ms, without waiting for the 30-second idle timeout. Artifact:
`continuous-h3-session-idle`. H2 artifact: `continuous-h2-session2`.

Next comparisons freeze this implementation: two randomized fixed-work session
pairs per protocol (seeds 202608312/202608313), then lean two-block canonical
residual screens (202608314/202608315). The session control is the unchanged
native client with both local listener types. Identical curl workloads compare
warmed-session download/upload timing, with complete-session wire accounting;
these are not browser-page completion metrics. A single H2 pair at outer
10 Mbit/s (202608316) checks whether the cost/rate result changes when the link,
rather than loopback processing, is limiting. This is not a full size/link matrix.

The first two H2 session pairs completed with exact bodies and single outer
connections. Mean total wire grew from 4,968,428 to 6,558,423 bytes (+32.00%).
However, 1-MiB download time grew from 20.813 to 143.474 ms (85.49% lower
effective rate), and four parallel 512-KiB downloads from 31.837 to 255.127 ms
(87.52% lower aggregate effective rate). The intentionally slow 1-MiB upload
grew from 304.755 to 343.731 ms; its 11.34% rate loss is not an unrestricted
upload-throughput result because the target deliberately pauses per read.
The protocol is live and economical at rest, but active transport throughput
is not solved. `session_costs.py` reproduces these paired-session calculations.

H3's two session pairs likewise passed. Wire grew from 4,985,291.5 to 6,588,756
bytes (+32.16%). The 1-MiB download grew from 20.793 to 159.564 ms (86.97% rate
loss); concurrent downloads from 32.049 to 243.590 ms (86.84%). Slow-upload
completion grew from 309.629 to 347.494 ms. A derived connection audit corrected
an accounting field that had counted an empty dissector connection value as a
second flow: every full H3 session has one real QUIC identity and zero TCP
packets. Primary result files are unchanged and their wire totals remain valid.
The idle-only capture has no Initial or TCP packets; without a handshake in
that capture the dissector does not assign a connection number. Do not count
that missing field as another connection.

Under one shared 10-Mbit/s outer-port rate limit, the H2 fixed-work pair used
4,930,688 default versus 6,512,717 replacement wire bytes (+32.09%). Its 1-MiB
download was 918.207 versus 1,158.506 ms (20.74% lower effective rate), parallel
downloads 1,819.143 versus 2,196.301 ms (17.17%), and the slow upload 945.010
versus 1,159.156 ms (18.47%). Small post-idle work still paid a latency cost:
4 KiB took 20.761 versus 81.432 ms. This one paired link condition is not a
general network guarantee. It shows why the loopback throughput penalty and
the loss on a bandwidth-limited link must be reported separately.

Both preregistered lean residual screens completed all eight samples, with
one outer connection per sample and zero TCP in H3. These are two-block
diagnostic screens, `INSUFFICIENT_FOR_INFERENCE`, not a default promotion.

| Continuous screen, SOCKS | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| H2 default | 0.08975 | 0.46738 | 0.21209 | 0.21354 | 0.37620 |
| H2 replacement | 0.05204 | 0.30473 | 0.12605 | 0.10050 | 0.21085 |
| H3 default | 0.11015 | 0.33619 | 0.17327 | 0.15875 | 0.34739 |
| H3 replacement | 0.11602 | 0.20926 | 0.14830 | 0.18394 | 0.21306 |

The 17--32/Whole point improvements are about 34.8%/44.0% on H2 and
37.8%/38.7% on H3. H3 1--16 and 250 ms are worse, not silently waived.
Early H3 results remain noisy. Artifacts: `continuous-h2-residual` and
`continuous-h3-residual`. These compare the canonical inner browser page
against independent normal visitors of the current application, not an old
static-site dashboard or a replay of the loaded worker's state transitions.

Canonical-page wire accounting is separate from the larger session above:
H2 default/replacement means are 724,179.5/1,174,797.5 bytes (+62.22%), with
117.309/293.213 ms useful completion; H3 is 799,368/1,175,091 bytes (+47.00%),
with 116.328/329.797 ms. Thus +32% is a measured mixed-session result, not a
universal traffic surcharge. The startup budget is less amortized on small
workloads. Long sessions, economical idle and valid late work are now admitted;
startup cost, active speed, memory, reconnect/resumption and native integration
remain experimental limitations.

Continuous baseline verification passed 160 capture/analysis tests, eight
focused harness tests, fifteen JavaScript tests and all four Go race-test
packages. Frozen evidence `continuous-v1-evidence` includes matching binaries
and the server repository bundle through `d444393`; manifest SHA-256 is
`80dce75db0bb295e82474d3a360b38136ce8f46601357a8c1805c8a3ddee1559`.
The server repository still has no remote; this bundle is local preservation,
not a server push. Product defaults and Firefox binaries are unchanged.
