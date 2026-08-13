# Local throughput comparison

Date: 2026-08-13

Environment: `Ubuntu24Dev`, x86-64, Firefox opt build, loopback-only pinned
Caddy/forwardproxy fixture

This benchmark compares NaiveFox with the official Linux x64 NaiveProxy
`v150.0.7871.63-1` client. Both clients use one long-lived process, the same
SOCKS interface, the same HTTPS Caddy listener, and the same deterministic
HTTP target. It is a local implementation/CPU/backpressure benchmark, not a
claim about Internet throughput. No credentials or packet payloads are
retained in this report.

Run it with:

```bash
netwerk/naivefox/test/integration/run-throughput-benchmark.sh
```

## Workload

Every measured object is 64 MiB. Each row below is the median of three trials.
Within each client trial the runner performs four sequential downloads, four
parallel downloads, eight parallel downloads, two sequential uploads, and four
parallel uploads. Upload responses must report the exact byte count and
SHA-256. A separate 64 MiB proxied download must match the direct target's
SHA-256 before measurements are accepted.

Each proxy client transferred 4.25 GiB; the direct target controls transferred
2.3125 GiB. The complete run therefore moved approximately 10.81 GiB through
the loopback target. Both clients accepted 68 finite SOCKS connections during
the test. Neither client was configured with a production concurrency cap.

## Results

| Client | Phase | Median MiB/s | Trials |
|---|---|---:|---:|
| direct | sequential download | 1,577.952 | 3 |
| direct | 8-parallel download | 1,289.432 | 3 |
| official NaiveProxy | sequential download | 497.799 | 3 |
| official NaiveProxy | 4-parallel download | 342.281 | 3 |
| official NaiveProxy | 8-parallel download | 284.343 | 3 |
| official NaiveProxy | sequential upload | 325.700 | 3 |
| official NaiveProxy | 4-parallel upload | 349.085 | 3 |
| NaiveFox | sequential download | 421.480 | 3 |
| NaiveFox | 4-parallel download | 431.364 | 3 |
| NaiveFox | 8-parallel download | 412.577 | 3 |
| NaiveFox | sequential upload | 288.093 | 3 |
| NaiveFox | 4-parallel upload | 390.542 | 3 |

Relative to the official client, NaiveFox was 15.3% slower for sequential
download and 11.5% slower for sequential upload. It was 26.0% faster for four
parallel downloads, 45.1% faster for eight parallel downloads, and 11.9%
faster for four parallel uploads. The direct controls were substantially faster
than either proxy path, so the deterministic target was not the throughput
ceiling.

Peak resident memory was 13.3 MiB for the official client and 139.2 MiB for
NaiveFox. The latter includes the intentionally reused Gecko/Necko/NSS runtime
and is expected to be materially larger than Chromium's minimized standalone
Naive client.

All integrity checks passed. The raw three-trial TSV is kept only as an ignored
local artifact. Trial order was direct, official client, then NaiveFox; it was
not randomized, and one direct 8-parallel trial showed scheduler variance.
Use the medians for regression comparisons and rerun on dedicated hardware
before drawing capacity-planning conclusions.
