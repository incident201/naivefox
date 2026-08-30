# Application-capacity transport experiment

This campaign is confined to an experimental branch and a separate Caddy
module repository. It does not change product defaults, the published matrix,
the stock-server contract, or the deferred export/publication state.

The latest qualified lifecycle baseline is `continuous-v1`: startup followed
by ongoing interactive/download/upload/mixed states and economical idle. See
[continuous lifecycle](#continuous-lifecycle-preregistration) for its current
cost and residual evidence, and [active-speed diagnosis](#active-speed-diagnosis)
for the opt-in combined-exchange/short-lease trials. The finite profiles below
are retained experiment history, not instructions to terminate a live proxy
after its initial application job.

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

## Active-speed diagnosis

Before changing the next mechanism, history preflight checked the all-ref
duplex, pipeline, batching and multiplexing records, including `fac638485497`,
the existing `duplex-v1` cost failure and the prefix-delivery experiment above.
Duplex alone was already tried during the animation-gated startup; removing
the same HTTP boundary is not an untested startup hypothesis. Continuous
active leases have no paint barrier, however, and their time distribution has
not been measured. First instrument a separate H2 session (not a residual or
cost capture) to split fetch headers, response-body reads and local bridge IPC.
Do not infer a kernel/Firefox delay from total completion alone.

Session checks additionally record curl's own connection/TLS/first-byte/total
times. Existing `completion_ms` remains the observed subprocess completion
bound for historical comparisons; its 10-ms poll interval and browser-health
query are too coarse for precise small-transfer latency. Instrumentation is
explicitly opt-in, only after startup, with aggregate numeric timings and no
payloads or secret URLs in reported evidence.

The first trace (`continuous-h2-stage-profile`) had invalid IPC attribution:
its event listener ran after the application callback's microtasks. Retain
that trace but do not use its missing delivery counts. With the observer in
the capture phase (`continuous-h2-stage-profile2`), 1-MiB download took
120.236 ms by curl: upload/download fetch headers consumed 46/42 ms over
23/22 calls, body reads 5 ms, and take/deliver/pressure IPC 12/14/4 ms.
These rounded, instrumented aggregates can include work just after curl ends;
idle-request times spanning the preceding settle period are not stage latency.
They identify sequential HTTP turnaround as a large cost, not an unexplained
fixed Firefox pause or proof that IPC is free.

Next opt-in profile `continuous-sync` changes active leases only: a POST to
`/api/exchange/{interactive,download,upload,mixed}` receives the same fixed
8/64-KiB response directly, replacing POST-204 then GET. Initial 20-round
startup, four-slot lease size, upload/down capacities, pressure thresholds,
30-second idle poll and wake POST stay unchanged. No speculative parallel
request, extra padding budget, new delay or removal of flow control is added.
The prior animation-gated duplex failure is not overwritten. This tests the
newly measured active-stage barrier, while explicitly checking for additional
filler caused by turning slots faster. Admission first, then three randomized
H2 replacement-profile session pairs (seed 202608317), without instrumentation.
Only a substantial measured gain earns further protocol/residual checks.

All six sessions in `continuous-sync-h2-pairs` passed. Curl completion means
fell from 116.376 to 100.585 ms for 1 MiB (13.57% less time), and 217.034 to
165.671 ms for four parallel downloads (23.67%). Slow-upload time fell 3.90%,
but the 4-KiB wake grew from 22.419 to 23.927 ms (+6.72%). Complete-session wire
increased from 6,590,795 to 7,209,682 bytes (+9.39% against the previous
prototype, not against the native default). Combined exchanges are a measured
bulk-speed improvement with a traffic/small-latency regression, not a preferred
default. Do not run a full residual matrix for this tradeoff yet.

Scheduling deviation: that command omitted the intended seed argument and
therefore used the runner default 202608301, not preregistered 202608317.
Its saved randomized schedule is retained unchanged; there was no order
selection or rerun. Treat it as exploratory paired evidence.

The extra cost includes more underfilled upload leases; in the first pair
the baseline issued eight 128-KiB upload slots, the combined profile twelve.
Faster turnaround can cross state/credit/target-readiness boundaries sooner;
these counters do not isolate a unique cause. A four-slot upload commitment
reserves 512 KiB, twice one stream's 256-KiB credit window, and can waste a
large fixed tail even without a credit stall. Keep per-stream flow control;
investigate shorter fixed state leases rather than increasing queues or
switching to byte-exact response sizes. Verification passed seventeen JS tests,
nine harness tests and all four Go race-test packages; no Firefox build.

Frozen `continuous-sync-evidence` includes server `9fe57b4` and the first
combined-exchange trial; manifest SHA-256
`f8a65f55003571d8e7ca4e7eb05d1816a6da8b0ef92fdcf5402880597b88026b`.

History preflight for shorter activity leases also checked the all-ref credit
and finite-window records, specifically `a09db4ff155a`: that experiment changed
speculative initial concurrent receive requests, not the number of sequential
fixed-capacity slots committed before an active SPA state transition. Here
`continuous-sync2` retains combined exchanges and changes only four-slot active
leases to two slots, for every state. It halves a lease's potential empty tail
and upload commitment to 256 KiB, without changing the per-stream credit window,
body sizes, startup, idle timeout or any target-size-specific threshold.
The two-slot commitment remains fixed when pressure changes inside it.

First H2 functional admission; then two randomized H2 pairs against the same
binary's `continuous-v1` (seed 202608318). This is a speed/cost comparison, not
proof that every credit stall or arbitrary workload is solved. If this admits
useful speed without additional wire cost, check H3 and short canonical residuals.

`continuous-sync2-h2-session` passed all late, delayed, upload, concurrent and
post-idle work. Both pairs in `continuous-sync2-h2-pairs` (actual seed
202608318) also passed with exact bodies, live idle and one outer connection.

| H2 fixed-work stage, curl timing | continuous-v1 | continuous-sync2 | Time change |
| --- | ---: | ---: | ---: |
| 1-MiB download after idle | 134.351 ms | 103.708 ms | -22.81% |
| Four concurrent 512-KiB downloads | 249.779 ms | 182.362 ms | -26.99% |
| Target-throttled 1-MiB upload | 319.637 ms | 311.386 ms | -2.58% |
| 4-KiB post-idle request | 23.055 ms | 26.649 ms | +15.59% (+3.595 ms) |

Complete-session wire was 6,541,600 versus 6,859,046.5 bytes, +4.85% relative
to continuous-v1. This is less cost than the earlier four-slot combined
variant's +9.39%, but those are separate paired campaigns, not a direct
four-slot/two-slot latency comparison. Native controls were not rerun here;
do not describe +4.85% as the new total overhead over native or claim the
previous +32% surcharge was preserved. The stricter no-extra-wire gate is not
met, and small-request latency regresses, so the proposed H3/residual extension
is not run. These profiles remain opt-in tradeoffs; continuous-v1 remains the
qualified cross-protocol lifecycle baseline.

The new evidence supports reducing active HTTP turnaround as a useful bulk
speed direction. It does not establish a general latency fix. Before another
implementation, investigate the interactive-versus-bulk split, readiness at
response snapshot time, and bounded overlap of local delivery/HTTP work;
check their history first. Do not add fitted sleeps, silently enlarge credits,
reuse the old residual scores for a new profile, or trim failed work from
cost totals. The helper now rejects incomplete stage/precise-timing pairs and
retains historical polling observations separately from curl timings.

Final verification passed 160 capture/analysis tests, twelve focused Python
tests, eighteen JavaScript tests and all four Go race-test packages. Server
`1de273a` and matching binaries are preserved in `continuous-sync2-evidence`;
manifest SHA-256
`32cfaa086fe31e817c18f6ca68d8b2ac9caf016b03454bb68a4ea23cd813ff2e`.
Only the separate Caddy/bridge module was incrementally rebuilt. No full
Firefox build, minimal-source export, release workflow or default change was
performed. The source experiment branch is pushed in logical blocks; the
server remains a local repository with its committed history in the bundle.

## Download-lease coalescing

The next history preflight reviewed all-ref finite/carrier/batching commits,
the earlier v3 budgeted-response record (`7be2c317e82f`, `a09db4ff155a` and
artifact `25de70a0225eed54`), rejected timed server-read coalescing, and all
application cost/combined/short-lease results above. Fewer HTTP boundaries and
larger responses are not new in themselves. The unmeasured premise here is
coalescing a continuous download state's existing capacity commitment, while
leaving interactive, upload, mixed, idle and startup behavior at continuous-v1.
Earlier finite v3 changed data-driven response rotation; fixed startup profiles
changed total capacity and paint cadence. Neither measured this steady-state,
equal-budget lease substitution. No claim of a new camouflage mechanism is made.

Opt-in `continuous-bulk` replaces four 4-KiB POST/64-KiB GET pairs in a download
lease with one 16-KiB POST followed by one 256-KiB GET. The aggregate up/down
body budget remains 16/256 KiB per lease. There is no combined POST response,
wait-for-full-target-buffer, timer, speculative parallel request or credit
increase. The same coarse download-state decision grants the lease; its body
capacity is fixed even with an empty target queue. The larger full-body reader
may worsen delivery on a constrained link, and early snapshots may waste filler.
Those are explicit risks, not assumed-away benefits.

First test maximum-size framing, fixed budgets and full H2 session liveness.
Then two randomized H2 profile pairs against continuous-v1, seed 202608319.
A clear bulk-time reduction with no material total-wire or small-request penalty
earns targeted H3 and 10-Mbit/s pairs; residual checks only after these gates.
These are exploratory performance screens, not statistical/default promotion.

H2 functional admission and both seed-202608319 pairs passed. Curl means:
1 MiB 125.520 -> 106.189 ms (-15.40% time); four parallel 512-KiB downloads
211.276 -> 96.377 ms (-54.38%); slow upload 324.309 -> 317.767 ms (-2.02%);
small wake 21.612 -> 19.943 ms. Total wire 6,572,120.5 -> 6,743,066 bytes
(+2.60% versus continuous-v1). Thus equal per-lease budgets do not guarantee
equal total work: state transitions can still change the number of leases.
The large parallel improvement earns two H3 pairs (202608320) and one H2
10-Mbit/s pair (202608321), with unchanged binaries and no residual sweep yet.

The single-versus-parallel gap motivates a separate credit-state investigation.
After a 256-KiB response, one stream's remaining ready credit may fall below
the existing download threshold, even though queued target data remains. The
current server hint uses only sendable bytes, so it can return to interactive
before the next upload returns credits. Multiple streams have more aggregate
credit and may avoid this transition. This is a code-supported hypothesis,
not yet a measured attribution; do not increase the window to hide it.

Both H3 pairs (`continuous-bulk-h3-pairs`, seed 202608320) passed on one QUIC
connection with zero outer TCP. Curl means: single download 119.035 ->
116.828 ms (-1.85%); parallel downloads 210.764 -> 96.671 ms (-54.13%);
slow upload 321.333 -> 334.469 ms (+4.09%); small wake 21.031 -> 20.354 ms.
Wire 6,560,020 -> 6,653,805.5 bytes (+1.43%). One candidate upload includes
a 30.882-ms TLS timing outlier; it is retained, not removed from the mean.
The parallel improvement replicates; a general single-stream improvement does not.

The shared 10-Mbit/s H2 pair (`continuous-bulk-h2-10mbit-pair`, seed 202608321)
passed, but single download regressed: 1140.619 -> 1262.169 ms (+10.66%).
Parallel downloads improved 2195.232 -> 2043.784 ms (-6.90%); slow upload
1133.267 -> 1129.245 ms; small wake 61.567 -> 60.600 ms. Wire rose from
6,462,993 to 6,575,884 bytes (+1.75%). Some parallel candidate first-byte
times rose to about 320 ms from control's 60--76 ms: buffering a 256-KiB
response can delay useful delivery even when total completion improves.
This single exploratory pair is a regression signal, not a precise population
estimate. No new residual/default claim is admitted on these results.

Next, test bounded credit-state handoff independently of response streaming.
All-ref credit/backpressure history includes the old finite initial-credit
ablation and native H3 experiments, not this continuous SPA hint decision.
Expose queued bytes separately from currently credit-sendable bytes. Only an
opt-in profile may preserve a download hint when a bulk response just made
substantial useful progress, queued target data remains, but ready bytes have
fallen below the existing 32-KiB threshold. Never promote on backlog alone:
an unread local socket must not sustain empty 256-KiB responses indefinitely.
Count opportunities and promotions, retain the 256-KiB credit window, and
compare directly against continuous-bulk before comparing against v1.

The completed bulk block and matching server `c211251` binaries/history are
preserved in `continuous-bulk-evidence`, manifest SHA-256
`130ef0879353b423e1d386aabb026d3902cdc0c8e486eb01fd9f5549273e24b4`.

## Bounded credit-state handoff

Server `d02b4e1` adds `continuous-bulk-ready`. A bulk response must have sent
at least 128 KiB useful data before its remaining queued >=32 KiB can preserve
a download hint across low ready credit. Empty replies cannot sustain this
hint. The underlying receive window, per-stream credit enforcement, body
capacities and local receiver backpressure remain unchanged. The first unit
run caught the omitted frozen-budget entry for the new profile; after adding
it, 21 JavaScript tests, 14 focused Python tests and all four Go race packages
passed. No Firefox rebuild was required.

`continuous-bulk-ready-h2-pairs` (two randomized pairs, seed 202608322) compares
against continuous-bulk, not v1. All four sessions passed. All four observed
exactly three credit-handoff opportunities; only the candidate promoted them.
Curl single download means 121.482 -> 70.818 ms (-41.71% time); parallel
94.159 -> 99.459 ms (+5.63%); slow upload 319.018 -> 320.051 ms (+0.32%);
small wake 22.049 -> 19.661 ms. Wire 6,690,704 -> 6,714,406 bytes (+0.35%).
The two control runs and two candidate interventions support the
proposed cause for the single-stream gap; they do not establish every stall's
cause or guarantee a universal gain. Extend to two H3 pairs (202608323) and one
H2 10-Mbit/s pair (202608324) with identical binaries.

The runner now records explicit pair control/candidate/seed metadata and admits
non-v1 profile controls. Older campaigns retain their historical implicit v1
comparison. Incomplete or mislabeled pairs still fail closed; snapshots retain
the new comparison metadata.

Two H3 pairs (`continuous-bulk-ready-h3-pairs`, 202608323) passed. Single
download 107.912 -> 83.350 ms (-22.76%); parallel 98.632 -> 98.208 ms;
slow upload 319.903 -> 319.539 ms; small wake 19.214 -> 21.963 ms (+2.749 ms).
Wire 6,597,118 -> 6,685,747.5 bytes (+1.34%). Candidate single-download samples
100.329/66.371 ms show meaningful variability; do not present the mean as a
stable bound. One outer QUIC connection and no outer TCP were retained.

The shared 10-Mbit/s H2 pair (`continuous-bulk-ready-h2-10mbit-pair`, 202608324)
passed but did not improve speed: single 1266.567 -> 1311.304 ms (+3.53%);
parallel 2041.584 -> 2040.741 ms; slow upload 1127.760 -> 1145.743 ms;
small wake 65.292 -> 59.565 ms. Wire 6,632,979 -> 6,758,096 bytes (+1.89%).
The credit fix helps the unshaped single-stream implementation limit; it does
not solve the shaped-link penalty of committing/buffering large responses.
Keep both positive and negative evidence; proceed to the separate delivery
barrier experiment without promoting this profile.

Frozen matching `d02b4e1` evidence is `continuous-bulk-ready-evidence`, manifest
SHA-256 `c3249f082727260538fb2bb82a9b9e24e0b190664f660d9704aaaafd32813b23`.

## Frame-granular delivery preregistration

The next history preflight re-read finite read-through (`c75be29167da`,
`f124f1891c32`, `d587a084d5ff`), the staged-prefix experiment (`f804f175862d`)
and active IPC stage measurements above. Existing streaming waits for the
entire *used prefix*. If most of a 256-KiB cell is useful, that still holds the
first target bytes until nearly the whole response arrives. The distinct
premise is releasing complete logical frames while the remainder of useful
data, not just filler, is still arriving. It does not change outer capacities,
snapshot timing, request concurrency or target reads.

Implement a separate opt-in bulk-frames profile on the bounded-credit variant.
Only 256-KiB responses use incremental delivery. Retain a bounded decoder,
strict cell/frame sequence and length checks, full HTTP-body drain before the
next transaction, and explicit local IPC finalization only after valid EOF.
A malformed later frame or truncated filler closes the whole session; earlier
delivered bytes cannot be rolled back. Validate arbitrary splits, early data,
replay, cancellation, sink backpressure and decoder memory bounds first.
Extra local IPC can cost CPU/fast-link throughput; measure it rather than
assuming streaming is free. Compare directly against bulk-ready on the shaped
link that exposed the buffering problem, then unshaped H2/H3 if admitted.

Admission passed 24 JavaScript tests, 14 focused Python tests and all four Go
race packages. The incremental mode sends complete frames supplied by each
browser read with an awaited local sink; it introduces no batching timer or
queue-size-fitted read. Both JavaScript and bridge validate the prefix; the
bridge refuses mixed whole-cell/partial commands until finalization. Numeric
final-state counters distinguish frame deliveries with still-unread useful
bytes from the older filler-only early-prefix metric. Next: two randomized
10-Mbit/s H2 pairs against bulk-ready, seed 202608325.

`continuous-bulk-frames-h2-10mbit-pairs` passed all four full sessions. The
candidate recorded 119/134 deliveries while useful bytes remained unread,
145/160 total fragment IPCs including finalization, and about 2.80/2.82 MB
aggregate remaining-used-prefix bytes at first delivery per cell. This is
reader-side progress evidence, not additional bytes or a wire-in-flight count.
Curl means: single download 1185.605 -> 1155.670 ms (-2.52%); parallel
2046.702 -> 2035.026 ms (-0.57%); slow upload 1148.080 -> 1139.301 ms;
small wake 61.722 -> 62.854 ms. Wire 6,587,394 -> 6,759,519.5 bytes (+2.61%).
Control singles varied 1305.780/1065.430 ms, exceeding the mean gain, so this
is not a robust throughput improvement. One control parallel pair also had
~322-ms first-byte samples; candidate samples stayed near 61--82 ms, but this
was not consistent across all control transfers. Run two unshaped H2 pairs
(202608326) specifically to check the extra IPC cost, not a residual matrix.

## Selective bulk exchange preregistration

The unshaped frame screen also passed all four sessions: single download
72.935 -> 68.758 ms (-5.73%); parallel 96.078 -> 97.693 ms (+1.68%);
slow upload 320.175 -> 320.769 ms; small wake 18.499 -> 21.331 ms (+2.833 ms).
Wire 6,794,236 -> 6,853,684 bytes (+0.87%). No substantial fast-link regression,
but no decisive overall gain either. Retain frame delivery as an opt-in latency
building block; do not add H3/residual sweeps for this weak standalone result.
`continuous-bulk-frames-evidence` preserves matching server `871f9f1`, manifest
SHA-256 `94376b054b7d5342d58dd0701678ebe942fe97464c68eebbdddca9b68e541fff`.

Before another implementation, history preflight checked the all-ref pipeline
record (`fac638485497`), native/finite batching failures, `duplex-v1`, and the
continuous-sync/sync2 results. Combined exchanges are already measured: they
accelerated bulk but inflated upload tails and hurt short-request latency when
applied to every active state. The distinct proposed ablation combines only
the bulk download lease's 16-KiB POST and 256-KiB response. Interactive, upload,
mixed, startup and idle retain bulk-ready behavior; no frame streaming is
included, so its IPC cost is not confounded with HTTP turnaround.

The candidate uses one POST-200 `/api/sync/bulk` instead of POST-204 plus GET,
with exactly the same per-lease capacity. Compare directly against bulk-ready,
two randomized H2 pairs, seed 202608327. Request/body/auth/replay tests and
strict graph accounting precede performance. A positive result must retain
traffic and small-request behavior before H3/slow-link follow-up. This is a
selective composition of known mechanisms, not a newly invented carrier or
evidence that fewer HTTP requests alone guarantee better residuals.

Server `39f24a1` passed 25 JavaScript tests and all four Go race packages;
the focused Python admission suite passed. Both H2 pairs passed with no outer
CONNECT, one TCP connection and exact fixed bodies. Curl means: single
79.187 -> 57.847 ms (-26.95%); parallel 94.199 -> 81.714 ms (-13.25%);
slow upload 320.236 -> 320.147 ms; small wake 19.322 -> 20.687 ms (+1.365 ms).
Wire 6,870,202.5 -> 6,751,285.5 bytes (-1.73% versus bulk-ready). This earns
two H3 pairs (202608328), then one 10-Mbit/s H2 pair (202608329). If functional
admission holds, measure a separate H2 pair with 20-ms one-way outer delay and
a shared 50-Mbit/s ceiling (202608330) to expose serial-turnaround sensitivity.
These are targeted mechanism checks, not a full platform/resource matrix.

The runner now supports explicit one-way outer-port delay, still leaving local
IPC and targets unshaped, and refuses residual scoring on any shaped link.
It also checks and retains netem drop counters for every shaped sample. Review
found those counters had only been saved by the old finite timing-pair path,
not continuous session pairs; do not retroactively claim their absence for
the earlier continuous rate-only trials. Those retain byte-exact completion,
capture-drop and namespace checks, but lack that separate shaper-drop proof.
The new session gate also requires one outer flow rather than only reporting
the count. Fifteen focused Python tests cover delay construction and missing/
dropping-shaper rejection.

H3 `continuous-bulk-duplex-h3-pairs` passed both pairs: single download
81.556 -> 71.083 ms (-12.84%); parallel 99.141 -> 82.804 ms (-16.48%);
slow upload 331.959 -> 322.077 ms; small wake 24.498 -> 21.944 ms.
Wire 6,833,694.5 -> 6,650,729.5 bytes (-2.68%). The selective bulk result thus
replicates across protocols, unlike the earlier all-state combined profile.

H2 `continuous-bulk-duplex-h2-10mbit-pair` passed with zero netem drops:
single 1115.058 -> 1053.129 ms (-5.55%); parallel 2040.760 -> 2025.476 ms;
slow upload 1382.691 -> 1145.263 ms; small wake 60.887 -> 63.407 ms.
Wire 7,062,364 -> 6,472,511 bytes (-8.35%). The control's upload stage is
unusually slow and wire-heavy versus earlier controls. Retain it, but one
pair does not establish an upload improvement caused by a download-only change.

The 20-ms one-way/50-Mbit/s H2 pair (`continuous-bulk-duplex-h2-rtt40-pair`)
also passed with zero shaper drops. Single download 1468.789 -> 1046.475 ms
(-28.75%); parallel 1653.903 -> 1305.619 ms (-21.06%); slow upload
1406.953 -> 1452.350 ms (+3.23%); small wake 361.716 -> 315.171 ms.
Wire 6,542,301 -> 6,779,223 bytes (+3.62%). This exposes a real serial-HTTP
cost but is not an RTT-independent or free throughput improvement. Only one
delay/rate pair was tested. Matching server `39f24a1` evidence is frozen in
`continuous-bulk-duplex-evidence`, manifest SHA-256
`dd4bf745867681a79725d1faf9c55e3e79fb4a5e2d68d64a6f3c53d16f676aa3`.

## Per-state lease-length preregistration

History preflight revisited the all-ref finite initial-window ablation
(`a09db4ff155a`), continuous-sync2 and the selective-exchange evidence above.
Short leases are not new. The previous two-slot trial shortened every state
and combined every active exchange. It did not isolate the commitment of one
state while retaining the now-faster selective bulk path and original small
POST/GET exchanges. Test two independent profiles against bulk-duplex:

- `continuous-bulk-interactive1`: one interactive slot, all other leases unchanged.
  This can switch sooner when an inner TLS handshake produces download work,
  especially under RTT, but adds pressure-query boundaries and could spend
  larger cells prematurely.
- `continuous-bulk-upload1`: one upload slot, all other leases unchanged.
  This may remove up to three empty 128-KiB tail uploads; premature state
  transitions or credit timing could instead fragment the transfer.

Both retain fixed capacity per slot, the 32-KiB class threshold, bounded credits,
four-slot mixed state, same startup and idle. No data-sized body, fitted pause
or response wait is added. First independent two-pair H2 screens with seeds
202608331/202608332, then only the stronger candidate's H3/slow-link checks.
Do not combine these interventions before measuring them separately.

Server `2793377` passed 26 JavaScript tests, all four Go race packages and
15 focused Python tests. `continuous-bulk-interactive1-h2-pairs` passed both
pairs but is rejected as the next preferred candidate: wire 6,812,547.5 ->
7,420,479 bytes (+8.92%). Single download 63.400 -> 57.539 ms (-9.24%);
parallel 83.546 -> 83.879 ms; slow upload 317.810 -> 308.514 ms;
small wake 22.393 -> 18.588 ms. Both candidates used twelve large upload slots
instead of controls' eight, despite reducing interactive responses to 23/24
from 44. Earlier state transition spent more capacity elsewhere. No H3 or
residual follow-up for this unfavorable traffic tradeoff.

`continuous-bulk-upload1-h2-pairs` also passed both pairs but barely changed
the fixed workload: single 59.922 -> 59.720 ms; parallel 81.142 -> 81.341 ms;
slow upload 321.219 -> 317.181 ms; small wake 17.764 -> 19.747 ms.
Wire 6,888,809.5 -> 6,857,862 bytes (-0.45%). Critically, every arm consumed
exactly eight large upload slots. The proposed partial-tail saving was not
exercised by this nearly block-aligned 1-MiB upload. Do not infer that shorter
upload commitment helps, or that the tail mechanism is absent, from this test.
One narrowly targeted follow-up changes only the upload workload to 333,333
bytes for both arms, leaving all transport parameters unchanged: two H2 pairs,
seed 202608333. This is a causal boundary check, not a resource-size matrix.

## Local delivery acknowledgement preregistration

The partial-upload follow-up passed all four sessions and exercised the cause:
bulk-duplex used four 128-KiB uploads; upload1 used three. However total-session
wire still rose 5,892,770 -> 6,095,529.5 bytes (+3.44%), while upload completion
was unchanged (125.497 -> 125.525 ms). Single download varied adversely
61.261 -> 68.890 ms; parallel 82.014 -> 80.603 ms; small wake
19.678 -> 22.336 ms. Thus one upload tail slot was removed, but the full-session
cost did not improve in this small screen. Do not claim the upload change caused
all unrelated download variation, or promote this as an overall saving. Retain
both lease ablations without H3/residual expansion. Matching server `2793377`
is frozen in `continuous-state-leases-evidence`, manifest SHA-256
`956fd50d46362c3ad6756876deb8de81f9c52a16b979f460ccb50afea4dfc521`.

History preflight searched all-ref acknowledgement/IPC/optimistic records and
reviewed the rejected local SOCKS-success change (`8468a8adec14`) plus the SPA
stage trace. This proposal does not acknowledge a target connection earlier
or remove an outer handshake. It concerns the private loopback bridge's
per-response *delivery* ACK, which is followed by an already-required pressure
or upload-capacity request on the same ordered WebSocket.

An opt-in bulk-noack profile may defer that ACK to the next command's reply.
Only active/idle delivery changes; startup stays identical. Allow at most one
unacknowledged cell. The next pressure/take command is processed after delivery
and must finish before another response can be delivered, bounding local
buffering without a timer. Keep codec/stream sequencing and receive credit;
invalid delivery closes the socket and rejects the pending command. Never
speculate credit for bytes not written to the local application. Missing early
credit grants could spend an empty outer cell, so measure wire cost as well as
IPC latency. Compare against bulk-duplex with two H2 pairs, seed 202608334,
after deterministic ordering/fence tests and full-session admission.

Implementation admission passed 27 JavaScript tests, all four Go race packages
and 16 focused Python tests. A one-cell delivery fence is cleared only by an
awaited command reply; wake notifications cannot clear it. Profile definitions
were converted from fragile positional boolean lists to named inheritance.
A digest captured *before* that refactor freezes all twenty previously measured
profile JSON objects (`52e73811661919f765e75c444927a42abf28e383a50b58068f2b1dd9350372da`);
it still passes, so old controls' parameters were not silently changed.

`continuous-bulk-noack-h2-pairs` passed both pairs with 82/78 deferred
deliveries. Wire 6,890,371.5 -> 6,799,866.5 bytes (-1.31%); single download
78.787 -> 62.034 ms (-21.26%); parallel 94.040 -> 86.508 ms (-8.01%);
slow upload 319.860 -> 319.948 ms. Small wake regressed 19.434 -> 25.729 ms
(+6.296 ms). Candidate idle/wake activity increased (one run reached 22 polls
and 13 wake POSTs versus the usual ~9/5), consistent with requests observing
credit before local writers have returned it. This supports a timing/credit
interaction, not a proven attribution of every millisecond. No default or
residual promotion. Snapshot `continuous-bulk-noack-evidence`, server `7264a13`,
manifest SHA-256 `5a66026fb27f2140f96176b4e481330b3b212bfc40231bceb2c69dc0dc16c81e`.

One scoped follow-up, `continuous-bulk-noack-download`, defers only while the
SPA is in the bulk download state. Startup, idle, interactive, upload and mixed
retain their original delivery ACK. Same one-cell fence and body budgets.
This isolates the observed small-state penalty; it does not retest optimistic
SOCKS success. Two H2 pairs against bulk-duplex, seed 202608335. After that,
measure the best surviving profile's sustained throughput and fresh residuals,
rather than continuing tiny scheduler variants without a demonstrated cause.

The bulk-only ACK screen passed all four sessions with exactly thirteen deferred
deliveries per candidate. Single download 67.229 -> 65.719 ms (-2.25%);
parallel 90.283 -> 90.303 ms; slow upload 320.690 -> 324.677 ms;
small wake 23.710 -> 26.085 ms. Wire 6,863,867 -> 6,894,285 bytes (+0.44%).
The strong all-active ACK speed gain did not survive isolation to bulk, and
small latency is still not better. Keep both ACK profiles opt-in; no H3 or
residual sweep. Bulk-duplex remains the simpler strongest cross-protocol
candidate, while continuous-v1 is still the last residual-qualified lifecycle.

## Sustained-transfer diagnosis preregistration

Current 1-MiB screens include inner TLS startup and a small number of large
cells; control samples vary when a final lease changes. To distinguish these
from an established-transfer limit, expose one explicit 8-MiB single download
in the existing session workload, identically in all arms. Do not alter the
transport's capacities, thresholds or resource graph to fit it. Other stages
and the original 1-MiB upload stay fixed. This is one focused scaling check,
not a resource-size sweep or a replacement for canonical residual captures.

First a separate, non-cost H2 bulk-duplex stage profile with 8 MiB. Then two
H2 native-versus-bulk-duplex fixed-work pairs (seed 202608336) to measure the
remaining absolute gap and overhead. The runner records explicit workload sizes,
source revision and helper digests for new campaigns. Seventeen focused Python
tests pass; old campaigns keep their original workload and evidence.

The separate instrumented `continuous-bulk-duplex-h2-8m-stage` passed. Its
8-MiB download took 273.837 ms by curl. Rounded stage totals: upload fetch
headers 128 ms over 41 calls, ordinary download headers 12 ms/6 calls,
body reads 32 ms/41 calls, local delivery 58 ms/41, pressure 18 ms/37 and
take 20 ms/41. As before, counters can include work just after curl finishes;
idle fetch spans from the preceding settle are not stage latency. This points
to HTTP and local-IPC turnover rather than a mysterious fixed Firefox pause.
It does not prove all such time is removable or equate this laboratory
full-browser worker's limit with the transport architecture's global ceiling.

The uninstrumented two-pair `continuous-bulk-duplex-h2-8m-native-pairs` screen
passed all gates. Native -> replacement: 8-MiB completion 55.701 -> 304.994 ms;
parallel 17.099 -> 104.494 ms; slow upload 292.120 -> 324.687 ms;
small wake 6.764 -> 26.472 ms. Whole fixed-session IP wire grew
12,715,588.5 -> 15,125,210.5 bytes (+18.95%). On this fast isolated loopback,
8-MiB effective throughput is therefore 81.74% below native, despite the
lower percentage wire overhead than the earlier 1-MiB workload. These are
not WAN throughput predictions. Snapshot `continuous-selective-ack-sustained-evidence`,
server `37153fb`, manifest SHA-256
`c1695822abd95401e970d04872544db17dc3a1ff3a9d90062a5fef37b22cf3a9`.

## Bounded byte-window ablation preregistration

All-ref history preflight reviewed `6ce3fc4ddc3d`: its data-activated receive
credits meant outstanding finite HTTP GETs (one to four), not the logical
stream byte window. It worsened Whole and remains rejected. Current mux history
has the bounded hint change but no independent byte-window experiment.
Here one 256-KiB bulk body nearly exhausts the 256-KiB stream credit, although
the next exchange could otherwise continue bulk. Test exactly 512 KiB per
logical stream in both peers, as a separate profile derived from bulk-duplex.
Keep cell capacities, request concurrency, prefetch queue depth, state logic,
delivery ACK and body buffering unchanged. This is not the prior hint ablation:
it explicitly spends memory, up to 256 KiB more outstanding receive data per
stream/direction, or 8 MiB across 32 streams per peer, excluding existing
prefetch/in-flight overhead. No speculative delivery credit.

Prototype configuration pins the same window at bridge and Caddy; there is
no on-wire negotiation and mismatched peers are unsupported. Preserve the
old default and reject unsupported configured windows. First deterministic
credit/budget/overflow tests, then two H2 pairs against bulk-duplex with the
8-MiB workload (seed 202608337), to exercise the diagnosed sustained stage.
No residual or cross-protocol expansion unless the result warrants it.

First screen `continuous-bulk-window512-h2-8m-pairs`: all functional/wire gates
passed; 348.168 -> 336.276 ms single, 94.926 -> 87.301 ms parallel,
327.755 -> 337.547 ms slow upload, 33.001 -> 24.474 ms small wake;
wire 15,136,366.5 -> 15,157,253 bytes (+0.14%). Single samples span
297.486--375.066 ms, far beyond the 3.42% mean difference. A path-limited
all-ref Git history scan overlapped the final part and was stopped; these
timings are exploratory, not clean promotion evidence. Retain this run,
then repeat two pairs without a history scan/build/CPU benchmark in parallel
(`continuous-bulk-window512-h2-8m-clean-pairs`, seed 202608338).

Clean repeat passed: single 333.239 -> 290.052 ms (-12.96%), parallel
90.987 -> 85.117 ms (-6.45%), slow upload 326.313 -> 332.505 ms (+1.90%),
small wake 24.556 -> 23.953 ms. Wire 15,127,962.5 -> 15,188,476 bytes
(+0.40%). Single samples: control 345.358/321.119; candidate 286.882/293.221.
Useful sustained gain, not a universal ceiling or cross-protocol win. The
window remains an opt-in memory tradeoff. Snapshot `continuous-window512-evidence`,
server `9d5dc12`, manifest SHA-256
`97ec5bfc5f785d9bf848d662f5a8e63571645163093017fc4eb3966eac8a9aed`.

## Filler-generation CPU ablation preregistration

History preflight searched the journal and all-ref encoding/filler records;
the server cell encoder still has only its original implementation from
`63452c1`. It allocates random bytes for the entire fixed cell and then overwrites
the useful prefix, including all target data. Test generating randomness only
for the filler suffix after computing/validating `used`. Length, frame codec,
useful bytes and fresh cryptographic filler remain unchanged; no reused padding,
compression, weaker RNG or traffic-dependent outer capacity. First benchmark
the isolated encoder with empty, half-full and nearly-full 256-KiB cells, with
both paths in the same binary. Do not run this alongside network timings.
Only add a transport profile if saved CPU is large enough to be worth a screen.

The three-repeat Go microbenchmark (Ryzen 7 6800H, Go 1.25.12,
`encoder-filler-bench.txt`) retains one 262-KiB allocation per encoded cell.
Empty 256-KiB cell median 430.9 -> 435.0 microseconds (no useful gain);
128-KiB useful payload 431.6 -> 241.0 microseconds; 240-KiB useful
payload 425.6 -> 69.2 microseconds. The near-full saving is ~0.356 ms/cell,
not a claim of 84% end-to-end speedup. Add `continuous-bulk-filler` derived
from bulk-duplex (original 256-KiB window), enabling only suffix generation
at bridge/server encoders. Compare two H2 8-MiB pairs, seed 202608339.

`continuous-bulk-filler-h2-8m-pairs` passed functional/wire gates but **failed
speed**: single 271.891 -> 658.450 ms (+142.17%); parallel 86.964 -> 82.229 ms;
slow upload 345.357 -> 314.515 ms; small wake 21.305 -> 22.500 ms.
Wire 15,153,306.5 -> 15,394,648 bytes (+1.59%). Controls used 48/52 interactive
GETs and 41 bulk POSTs; both candidates used 132 interactive GETs and 38 bulk
POSTs. Faster encoding changes the moment `Pressure()` observes the producer;
extra short leases are consistent with a transient-queue state-hint problem,
not proof that random bytes are intrinsically necessary. Both bad runs remain
in evidence. No CPU-only default change. Snapshot `continuous-filler-evidence`,
server `7abf753`, manifest SHA-256
`02e2bf9ca8d5710837295cd129613ac72ed838ee7f53d77b419e4661beb2d103`.

## Progress-qualified handoff preregistration

Before the proposed two-transaction overlap test, directly test the timing
dependence exposed above. History preflight reviewed `d02b4e1` and the earlier
credit-handoff screen: the old predicate requires at least 32 KiB already
queued. The new independent `continuous-bulk-progress` derives from filler
(not the 512-KiB window or deferred ACK). After a bulk response containing at
least 128 KiB useful data, a still-readable logical stream permits one more
download hint even if the instantaneous queue is empty. A response below
that useful threshold cannot renew the promotion; observed EOF/reset cannot
justify it. Actual byte credit still controls transmission. No timer, wait
for full buffers, guessed delivery credit or larger cell.

This spends at most one extra empty 256-KiB probe after such productive progress,
with possible tail overhead; do not call it free. Add separate promotion
counters and deterministic empty/stalled/EOF tests. Compare two H2 8-MiB pairs
against the failed filler control (seed 202608340), then compare to the strong
original bulk-duplex before claiming a net improvement.

`continuous-bulk-progress-h2-8m-pairs` passed. Filler -> progress: single
758.165 -> 257.875 ms (-65.99%); parallel 85.980 -> 87.460 ms; slow upload
320.383 -> 332.304 ms; small wake 23.855 -> 21.944 ms. Wire
15,460,777.5 -> 15,740,392 bytes (+1.81%). Controls exposed 23/24 progress
opportunities and used 136/140 interactive GETs; candidates promoted 26/28
opportunities and used 48 interactive GETs. Bulk POSTs rose 38 -> 43.
This directly supports the transient-queue handoff mechanism, with a measured
tail/budget cost. The 66% recovery is against the broken fast-encoder control,
not against the strong original. Next two H2 8-MiB pairs against bulk-duplex,
seed 202608341; preserve both positive and negative controls.

Strong-control `continuous-bulk-progress-h2-8m-strong-pairs`: single
280.257 -> 257.494 ms (-8.12%), parallel 89.911 -> 83.887 ms (-6.70%),
slow upload 339.722 -> 332.155 ms, small wake 23.412 -> 27.454 ms
(+4.042 ms). Wire 15,116,067.5 -> 15,510,984.5 (+2.61%). The causal repair
works but the net gain is modest and not free; no H3/residual matrix or
default promotion. This also demonstrates why a faster microbenchmark alone
cannot justify changing a timing-sensitive transport. Snapshot
`continuous-progress-evidence`, server `114930d`, manifest SHA-256
`fac922a8fee1ccb73c0305aee7b5e906add6ee2a71af64e0ecaf154074dd1d54`.

## Bounded two-transaction overlap preregistration

History preflight re-read `fac638485497` (duplicate early CONNECT pipelining)
and `6ce3fc4ddc3d` (finite GET concurrency failed Whole), as well as the
single bulk-lease change `c211251`. Concurrency is not an untried general cure.
The distinct scope here is an established continuous SPA bulk phase, after
unchanged startup/idle, with two fixed application transactions per lease.
Compare a serial pair against a bounded overlapped pair with identical
2 x 16-KiB POST / 256-KiB response capacity and identical 512-KiB logical
window. Both derive from window512, without fast encoding, progress hints,
short-state leases or deferred ACK. Each pair spends twice the single-lease
budget and retains the documented window memory cost; a pair-only result
must later beat the original single lease before any promotion.

Do not dispatch the second POST before the first response headers: server
upload processing and response sequence assignment are then ordered. Prepare
the second local upload before starting delivery of the first, avoiding IPC
overlap, then overlap its HTTP wait with the first body's validation/delivery.
Deliver responses in order; finish/cancel both before the next state decision.
At most two fixed responses, no reorder queue, unbounded requests, raw outer
stream, retry or new timer. Test error cancellation, header ordering, exact
pair budget and IPC exclusivity, then two H2 8-MiB pairs, seed 202608342.

Admission passed 33 JS tests, all four Go race packages and 17 focused harness
tests. `continuous-bulk-pipeline-h2-8m-pairs` passed all four full sessions,
each with 21 paired bulk leases (42 POSTs); only candidates recorded 21 completed
overlapped pairs. These counters prove application scheduling, not simultaneous
packets on the wire. Serial -> pipeline: single 265.653 -> 237.809 ms (-10.48%),
parallel 84.413 -> 77.467 ms (-8.23%), slow upload 322.153 -> 318.721 ms,
small wake 23.595 -> 18.601 ms. Wire 15,412,336 -> 15,413,555 bytes (+0.008%).
Compare next against original bulk-duplex, two H2 8-MiB pairs, seed 202608343,
to include the pair-length and larger-window costs rather than hiding them
inside a weaker matched control.

`continuous-bulk-pipeline-h2-8m-strong-pairs` passed: bulk-duplex -> pipeline
single 261.850 -> 230.080 ms (-12.13%), parallel 87.368 -> 75.893 ms
(-13.13%), slow upload 319.974 -> 318.242 ms, small wake 20.310 -> 22.469 ms.
Wire 15,118,088 -> 15,403,160.5 bytes (+1.89%). This merits a focused H3
8-MiB two-pair check against the same strong control (seed 202608344), followed
by one H2 40-ms-RTT/shared-50-Mbit pair (seed 202608345) if admission holds.
No full resource matrix or default claim.

H3 `continuous-bulk-pipeline-h3-8m-strong-pairs` passed with one QUIC flow
and no outer TCP. Single 324.221 -> 257.453 ms (-20.59%; controls
296.176/352.265, candidates 251.543/263.362); parallel 92.012 -> 81.462 ms
(-11.47%), slow upload 325.930 -> 321.704 ms, small wake 24.617 -> 23.495 ms.
Wire 14,994,489 -> 15,256,638.5 bytes (+1.75%). Cross-protocol speed evidence
is positive, but these remain two-pair screens, not a residual qualification.

H2 `continuous-bulk-pipeline-h2-8m-rtt40-pair` passed with zero shaper drops:
single 3641.031 -> 3259.259 ms (-10.49%), parallel 1304.737 -> 1197.594 ms
(-8.21%), slow upload 1560.152 -> 1405.583 ms, small wake 360.803 -> 314.653 ms.
The latter stages include different observed inner-handshake turns in this
one pair; do not attribute all their difference to a bulk-only change.
Wire 15,067,111 -> 15,349,364 bytes (+1.87%). Snapshot
`continuous-pipeline-evidence`, server `fa778cb`, manifest SHA-256
`77fba374a3ef5888743b3cc0e59c2105a74b63c2df3ee0571c4ecfd42d06d620`.

## Idle event/heartbeat split preregistration

Separate next premise: current continuous idle always returns a 512-byte cell,
including empty 30-second timeouts. Such a response can carry only a small
fragment of the first inner TLS flight. History preflight reviewed continuous
idle/wake implementation and retained finite-lifecycle records; this is not
the rejected optimistic local ACK or a timeout before target delivery.

Test an opt-in profile based on original bulk-duplex: a genuine idle timeout
returns HTTP 204 with no cell, while an activity wake returns one fixed 8-KiB
cell, regardless of the exact target queue size. Leave the 30-second maximum
poll duration, POST wake, startup, bulk and all active leases unchanged.
No new wait, repeated rapid polling or per-byte body sizing. HTTP 204 does
not advance cell sequence; an event body still does. A wake/timeout race must
not lose work, and ordinary visitors use the same API semantics.

These are disjoint conditions: short fixed-work sessions isolate event size;
a separate 65-second idle run verifies zero-body heartbeat accounting/liveness.
Expect possible active-session filler growth from larger wake cells; measure
both it and idle wire cost. The scope is latency/idle cost, not a claimed
large-download acceleration or new residual winner. Implement after freezing
the pipeline evidence, with exact status/capacity/sequence/cancellation tests.

`continuous-bulk-idle-events` admission passed 36 JS tests, all four Go race
packages and 18 focused harness tests. Timeouts and events retain distinct
accounting; a 204 cannot consume cell sequence or carrier capacity. Work after
a timeout is observed by the next poll. First two H2 fixed-work pairs against
bulk-duplex with the original 1-MiB workload (seed 202608346), not the sustained
8-MiB variant. Then a separate real 65-second idle check; unit millisecond
timeouts are not substituted for the production 30-second poll behavior.
