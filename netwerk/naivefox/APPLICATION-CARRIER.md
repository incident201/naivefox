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

No measured residual result yet. Full-browser process/memory cost and idle
behavior remain unqualified; this is an experimental upper-bound prototype.

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
