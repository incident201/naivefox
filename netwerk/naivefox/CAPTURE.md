# Capture and comparison method

The purpose is to compare observable traffic and application performance against
a real Firefox build from the same upstream base. Protocol implementations stay
in Firefox Necko/NSS/Neqo. The measurement harness must never construct fake
Firefox packets, hide an origin connection or omit unfavorable lifecycle traffic.

## Reference and workload

Verify the official Firefox artifact against its task metadata and file hashes.
Record the client and Caddy binaries, runtime manifests, server source snapshot,
workload manifest/assets, harness files, network configuration and randomized
schedule. Freeze those inputs for the complete block.

Use the same semantic workload for direct Firefox and both local proxy
frontends. The ordinary HTTP application fixture records native
nextHopProtocol for every job: direct H3 reference jobs must remain on H3.
The proxied browser uses the declared inner route; NaiveFox's outer carrier
selection is validated independently.

The older WebSocket application is historical evidence, not an interchangeable
HTTP/3 reference. Its sustained traffic uses TCP. In that workload a QUIC-only
carrier differs in TCP handshake, TLS-record and lifecycle features by design.
Retain adverse results and state this scope explicitly. Never convert a
workload change into an apparent improvement by mixing datasets.

## Observer unit and health

One sample includes all encrypted flows to the measured outer origin, including
public-site loading, startup, useful data, idle periods, carrier transitions and
normal teardown. Merge TCP and QUIC chronologically. Count every physical
connection and relevant ICMP feedback rather than selecting a favorable flow.

Use a fresh isolated network namespace, loopback MTU 1500 and disabled
segmentation/receive offloads. Verify shaping rules before and after collection.
Record producer identities, actual endpoint socket owners, readiness and clean
exit. A network mutation, capture drop, missing start, incomplete payload, wrong
route or forced producer kill invalidates the sample.

Whole extends through normal application completion and producer shutdown,
empty shaping queues, TCP terminal evidence where TCP exists, and final capture
drain. Do not truncate at the application's result marker. Logs, TLS secrets and
decrypted diagnostics are private evidence and must not enter passive features.

## Five fixed windows

| View | Scope |
| --- | --- |
| p1-16 | First 16 observed packets |
| p17-32 | Packets 17 through 32 |
| p1-32 | First 32 packets |
| 250ms | Traffic within 250 ms of the observer origin |
| Whole | The complete session and teardown |

Early views use only packet-index or time-window features available within that
window. They must not include future handshake completion, later TLS record
ordinals, later connection counts or information recovered by decryption.

The Whole view includes the complete passive feature set, including all
connections, handshakes, TLS records, QUIC behavior and lifecycle aggregates.
Changing the carrier does not justify deleting its newly unfavorable features.

## Paired scoring

Randomized superblocks share Firefox A/B controls across all candidates and
frontends. For each feature, the two controls define a midpoint and paired
radius. A candidate's excess outside that interval is normalized using the
Firefox-only 75th-percentile paired radius and bounded to the existing [0,1]
range. The view score is the mean feature contribution; lower is closer.

Keep the existing tolerance, normalization cap, feature selection, block
grouping and statistical implementation in the analysis modules. Diagnostic
decomposition may explain a score by feature family, but must not feed back
into feature selection, weights or acceptance arithmetic.

Application performance uses verified useful bytes and I/O completion markers.
Hashing and control-channel time must not be passed off as payload transfer.
Report latency and throughput separately from passive distance.

## Screening and acceptance

Read current documentation, retained evidence and complete Git history before
repeating an experiment. Record any equivalent prior mechanism and the distinct
causal premise of new work.

Run correctness and route checks first. Start performance work with short
screens and one block, then investigate material changes before increasing
sample count. A single block cannot establish a stable regression or a
confidence interval; repeated blocks remain screening evidence below the
documented statistical gate.

Keep only changes with acceptable performance and no material unexplained
distance regression in the intended workload. Do not launch a 120-run campaign
to diagnose an already visible mechanism. Historical campaign reports and
retired experimental modes remain in Git history and their retained artifacts;
they are not supported product configurations.
