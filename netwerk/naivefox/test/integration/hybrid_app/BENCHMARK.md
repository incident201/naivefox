# Matched active-application benchmark

This is a new benchmark, not a continuation or relabeling of the idle-reference
results in `hybrid-ws-idle-diagnostic.json`. The old harness and datasets remain
available as explicitly superseded diagnostics. No old sample is eligible for
this study.

## Frozen workload and participants

A single Firefox application runs directly against the outer origin for
Firefox A/B, or through a selected NaiveFox listener against an inner origin.
Both origins serve identical HTML and assets: root 4096 bytes, stylesheet
12288 bytes, script 24576 bytes, and four SVG images of 8192 bytes each. The
shared script and canonical `manifest.json` define the workload. CSS/script
integrity attributes are not added to the root: the classic H3 native parser
contract forbids them.

After window load, the application performs 20 semantic POST/GET pairs at
`/app/api/bootstrap/{round}`: 40 ordinary HTTP requests carrying real catalog
and index records. These are not empty NFC cells and are not padded to native
carrier capacities. They are distinct from the actual native carrier's own
20 NFC GET/POST pairs, which remain an implementation cost only in the modes
that use them.

The application then opens exactly one WebSocket with subprotocol
`nfbench.app.v1` at `/api/realtime`. A test-only Caddy matcher routes that
subprotocol to the ordinary application backend. Native carrier subprotocol
`nfc1.hybrid.v1` continues to reach the real transport module.

After the backend accepts the application WS and before active work starts,
an independent kernel socket ownership check verifies routing: native inner-origin clients belong to the
Caddy proxy process, while Firefox owns the selected local-listener sockets.
Direct reference origin clients belong to Firefox. The snapshot may precede
the browser's open callback by the link propagation delay; that offset is
recorded rather than mistaken for a routing failure. Successful jobs alone
cannot conceal an active WS that bypassed the proxy.

The same manifest drives every participant:

1. Keep the application WS idle for 2000 ms.
2. Download 8 MiB.
3. Upload 1 MiB.
4. Start four 512-KiB downloads atomically on that same application WS.
5. Run four sequential 4-KiB echoes on the same WS.
6. Keep it idle for 2000 ms, then run one 4-KiB wake echo.
7. Verify every job and complete a clean normal WS close with code 1000.

The application protocol has 65536-byte data chunks, 524288-byte per-job
credit, at most four simultaneous jobs, and no data filler. Eleven jobs
transfer exactly 1069056 useful bytes client-to-server and 10506240 useful
bytes server-to-client. With the atomic `open_batch` operation, the expected
application WS message counts are 21/165 binary messages and 190/43 JSON
control messages in the client/server directions. Data generation, offsets,
chunk boundaries, credit rules and expected per-job SHA-256 digests come from
the same manifest. No candidate trace is replayed into the reference.

Every block contains Firefox A/B plus classic, no-connect and hybrid through
SOCKS and HTTP CONNECT: eight participants. Native configurations contain only
the row's selected local listener. All other configuration is common across
modes, including URI credentials, origin, trust and address mapping.

## Source and active-work admission

The application backend loads immutable asset buffers and records the bytes
and SHA-256 actually served. Its asset inventory is grouped by a hash of the
root's session cookie; semantic bootstrap binds the application's group so
that native carrier asset requests cannot substitute for the inner app's
assets. Both origins use the same backend routes and buffers. An independent
TLS preflight checks equal root/assets before fresh participant processes are
started. Root bytes and native root metadata still come from the real module.
No additional verification fetches are inserted into the browser workload.

A participant is accepted only when the browser and backend agree on the
manifest and script hashes, all six application asset responses, all 40
semantic API responses, all eleven job hashes and byte counts, the four-job
parallel barrier, message counts, and the normal single-WS close. Source,
manifest, script, backend, Caddy, native runtime and reference runtime hashes
are frozen before collection. The existing official Taskcluster proof binds
the Firefox runtime to the exact Git/Hg base and verified archive members.

An explicit negative gate must reject a page that merely opens an idle WS,
even if startup and the WS handshake succeed. Missing jobs, changed useful
bytes or directions, different concurrency/chunks/credits, extra connections,
corrupt data and premature close reject the participant and stop collection.

## Complete-session observation

A capture is active, with its own receive-side auxiliary UDP nonce observed
in that same file, before native/browser startup and common browser warmup.
The warmup uses a separate non-origin, non-target TLS health port; it cannot
traverse the selected proxy route. Immediately before actual navigation,
origin packets and origin requests must both still be zero. A warmed carrier
is rejected, never filtered away. It remains active through the whole
application lifecycle, normal app WS close, Firefox shutdown, native graceful
shutdown where applicable, and graceful Caddy/backend shutdown. Owned process
trees must be gone before the final capture drain. Both directional shaping
queues must have zero backlog, TCP termination must be observed on the receive
copy, and attributable outer traffic must remain quiet for at least three
configured RTTs (120 ms on the primary link). The final receive-side nonce is
followed by another wire/queue check, so a packet revealed after process exit
or after an earlier nonce restarts the drain condition. Observed drain bounds
are retained. Forced process kills and drain timeouts do not qualify.

Every captured TCP flow to the origin must have its beginning and terminal
FIN exchange or RST recorded. For encrypted QUIC, process termination plus the
observed capture drain is the termination evidence; no encrypted close frame
is inferred without decryption. The collector counts all attributable outer
origin traffic and excludes local proxy, inner-origin, backend, controller and
canary traffic. Attributable ICMP feedback is counted by its outer IP length,
with direction reversed from the quoted origin tuple; quoted UDP/TCP is not
counted again and ICMP does not create another physical connection. It does not crop Whole at two seconds or infer completion from
a quiet interval.

Early views use the existing strict chronological packet indices and window
aggregates. Future TLS-record ordinals or handshake summaries cannot leak into
p1--16 or p17--32. Whole contains the complete bounded session, including
startup, active work, both idle periods and teardown. It is not an arbitrary
WS lifetime.

The primary traffic result is complete-session outer IP bytes. Per-stage
throughput and latency use the same application's actual I/O start/end markers,
recorded before integrity hashing. Every stage also reports verification
completion, so hashing is not silently folded into only one cohort. Comparisons
use the matched direct Firefox controls and contemporary classic/no-connect
rows. There is no arbitrary-tail allocation of wire bytes to individual stages.
Any optional active-window subtotal must run from the first download send
through complete teardown and be labeled separately from total session cost.

## Primary link and sample plan

The primary campaign uses ten randomized paired blocks per H2/H3 startup
condition: 160 participants total. The outer link has 20 ms one-way delay
(40 ms RTT) and a separate 20 Mbit/s budget in each direction. Client-to-origin
and origin-to-client packets enter separate qdisc classes. Associated ICMP
quotes use the reverse directional class; the fixture validates the supported
IPv4 header layout instead of silently leaving feedback unclassified. Local proxy,
inner-origin, backend, geckodriver and capture-canary traffic are never shaped.

The logical hostname is fixed, with the same physical IPv4 endpoint for direct
Firefox and native MAP routing. Every Firefox role has the same documented
IPv4 resolver constraint. Captures must prove the selected address-family
coverage and absence of bypass. TLS, cryptographic and WebSocket feature
preferences are not changed for parity. The exact reference/inner-browser
preferences are retained for review.

Qdisc/filter topology, rates, delays, address-family coverage and zero qdisc
drops are admission conditions. The observer uses receive-side copies after
netem; missing receive-copy evidence is not replaced with transmit timestamps.
The namespace has MTU 1500, offloads disabled, and a mutation monitor.

Before the primary campaign, run complete unshaped correctness blocks and
controlled-link plumbing blocks for both protocols. These validate the method,
not a second claimed performance matrix or an optimization screen. Independent
review of the manifest, app/backend agreement, source/body inventory, negative
idle gate, network topology and complete-session boundary is required before
any capture. Pilot timing may revise the wall-time estimate, not the workload,
link, feature definition or sample-selection rule.

Ten blocks remain descriptive screening evidence below the 30-block paired
inference floor. All failures are retained, no participant is selectively
resampled, and no idle-reference or pre-fix dataset is spliced into the result.
All generated state belongs beneath the existing warm objdir's
`hybrid-ws/matched-app` subtree. The Firefox browser is reused, never rebuilt.

## Independent audit and publication

After collection stops successfully, run `audit-matched-app-results.py` against
the completed campaign directory. It independently recounts raw receive-side IP
bytes and directions, validates both endpoints' job and message inventories,
and recomputes traffic/rate ratios and residual means/medians. Its explicit PASS
is bound to the matrix, audit source and hashes of raw captures and sidecars.
A new audit replaces any stale PASS with an in-progress marker before checking.

`publish-matched-app-results.py --input CAMPAIGN --output DRAFT_JSON --section
DRAFT_MARKDOWN` then refuses anything except the complete 160-participant
primary and its matching audit. Draft outputs must be beneath the campaign's
parent directory and outside the campaign itself. Review those safe aggregates
before copying them into `test/integration/evidence/` and `CAPTURE.md`; never
publish raw captures, credentials, profiles or logs. Both scripts live in the
parent integration directory and require no rebuild of Firefox or NaiveFox.

## Initial implementation checks

The independent link check observed roughly 41 ms TCP/UDP RTT and less than
0.11 ms on the unshaped control port. Simultaneous UDP traffic measured
19.778/19.775 IP Mbit/s in the two independent directions. A shorter cold TCP
duplex check measured 12.22/18.10 Mbit/s despite overlapping transfers; TCP
congestion/ACK behavior is not a minimum application-goodput admission rule.
The configured qdisc rates and independently measured link capacity must not
be confused with throughput achieved by the application or proxy.

The first real Firefox correctness participant completed and verified the
entire shared application, its six decoded resources, all 40 semantic API
responses, all eleven jobs, exact useful bytes and a clean application WS
close. It was nevertheless rejected because the then-current native CLI
terminated on SIGINT rather than performing graceful runtime shutdown. The
failed pilot is retained; it supplies no comparative score or performance
result. The lifecycle gate is not relaxed to accept signal termination.

The first primary attempt stopped after 44 admitted H2 participants when
Marionette failed sandbox evaluation during the next browser's common health
navigation. That participant had no measured-origin requests, no carrier
opens and no application bootstrap or WS traffic. The entire incomplete
attempt is retained and is not pooled into the replacement campaign.
The controller had selected WebDriver `pageLoadStrategy=none`, which waits
for navigation to start, then evaluated readiness in a changing document.
It now requires the standard `normal` strategy and completed loads before
JavaScript postconditions. Health/about:blank navigation retains a 30-second
bound; actual application navigation uses the existing application timeout.
No script exception is caught or retried, no application error is masked,
and the in-page graph, useful payloads, native binaries and link are unchanged.
The replacement campaign is declared in advance with seed `2026083121`.
