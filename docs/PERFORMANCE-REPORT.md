# Local throughput comparison

Date: 2026-08-13

Environment: `Ubuntu24Dev`, x86-64, Firefox opt build, loopback-only pinned
Caddy/forwardproxy fixture

This benchmark compares NaiveFox with NaiveProxy `v150.0.7871.63-1`. Both
clients use one long-lived process, the same SOCKS interface, the same Caddy
listener, and the same deterministic HTTP target. It is a local
implementation/CPU/backpressure benchmark, not a claim about Internet
throughput. No credentials or packet payloads are retained in this report.

Run it with:

```bash
netwerk/naivefox/test/integration/run-throughput-benchmark.sh
```

That command preserves the original H2 default. The strict H3 run uses:

```bash
NAIVEFOX_BENCHMARK_REFERENCE_BINARY=/tmp/naiveproxy-source-v150/src/out/Release/naive \
  netwerk/naivefox/test/integration/run-h3-throughput-benchmark.sh
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

## HTTP/2 results

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

## HTTP/3 results

The identical workload was repeated against the fixture in strict `h3` mode.
The adapted Caddy listener advertised exactly `h3`; the reference client was
configured with a single `quic://` proxy and NaiveFox with `--protocol h3`.
NaiveFox logged 68 `Outer protocol: h3` confirmations, 68 successful padding
negotiations, no H2 selection, and no raw-padding fallback. Thus a TCP/H2
fallback could not satisfy this run.

| Client | Phase | Median MiB/s | Trials |
|---|---|---:|---:|
| direct | sequential download | 1,490.796 | 3 |
| direct | 8-parallel download | 1,257.120 | 3 |
| NaiveProxy v150 test build | sequential download | 97.793 | 3 |
| NaiveProxy v150 test build | 4-parallel download | 92.743 | 3 |
| NaiveProxy v150 test build | 8-parallel download | 83.856 | 3 |
| NaiveProxy v150 test build | sequential upload | 80.123 | 3 |
| NaiveProxy v150 test build | 4-parallel upload | 81.947 | 3 |
| NaiveFox | sequential download | 82.152 | 3 |
| NaiveFox | 4-parallel download | 81.785 | 3 |
| NaiveFox | 8-parallel download | 87.969 | 3 |
| NaiveFox | sequential upload | 86.166 | 3 |
| NaiveFox | 4-parallel upload | 99.732 | 3 |

Relative to the reference, NaiveFox was 16.0% slower for sequential download
and 11.8% slower for four parallel downloads. It was 4.9% faster for eight
parallel downloads, 7.5% faster for sequential upload, and 21.7% faster for
four parallel uploads. Peak resident memory was 20.8 MiB for the reference and
117.2 MiB for NaiveFox.

Both H3 clients were substantially slower than their H2 runs on this loopback
fixture. Depending on the phase, NaiveProxy H3 was 70.5--80.4% below its H2
median and NaiveFox H3 was 70.1--81.0% below its H2 median. Because the effect
appeared in two independent client stacks while the direct controls remained
above 1.2 GiB/s, these numbers characterize this local Caddy/QUIC setup; they
do not establish an Internet H3 capacity limit or a NaiveFox-specific
regression.

The published official binary could not be used unchanged with the ephemeral
private fixture CA. Its normal certificate verifier returned success, after
which Chromium's QUIC proof verifier rejected the chain solely because
`is_issued_by_known_root` was false (`ERR_QUIC_CERT_ROOT_NOT_KNOWN`). The H3
control therefore used a source-equivalent local test build from exact tag
`v150.0.7871.63-1`, commit
`3ba967e2d36cc133a896e81a36257ad4c6ea20f4`. Its only source change adds an
exact `hostname == "localhost"` allowance in
`net/quic/crypto/proof_verifier_chromium.cc`, after normal chain, name, date,
and signature verification. The three-line diff SHA-256 is
`fe09dd9100fe22fbe30ee39b81226397aca6c5ddf75b33003fdaa7946df83bec`;
the resulting `naive` SHA-256 is
`b837bc242b269d3e30f99fa4461b863bcb0046a9c1f13a2bf91ef75b4f4ad86b`.
This is not a bit-identical comparison with the published binary, but the
test-only trust exception does not alter QUIC, HTTP/3, padding, pooling, or
data-path code.

All H3 integrity checks passed. The ignored local artifacts are
`artifacts/h3-throughput-benchmark.tsv` and
`artifacts/h3-throughput-benchmark-summary.md`. The runner does not retain a
NetLog, client config, private CA, credentials, or payload bodies.
