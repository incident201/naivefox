# Application carrier — stopped campaign / current results

Campaign stopped at the user's request. No further experiments, minimal export,
release workflow or default promotion are in progress.

Selected measured speed profile: `continuous-bulk-pipeline`. The native product
defaults and stock-Caddy compatibility remain unchanged. The experimental profile
requires the separate `naivefox_transport` Caddy module and a full Firefox worker;
these are not measured improvements to the minimal native executable.
The final server build is from `4738130`; the interactive-only variant in that
source is unit-tested but **unmeasured and not selected**.

## Full cold-page matrix

WSL Ubuntu24Dev, isolated network namespace, MTU 1500/offloads disabled;
Firefox base `0b76543aaeeeb2a5748ce2675ee36e7c94cb1125`, official same-base
Firefox 157.0a1, Caddy 2.11.2/Go 1.25.12. Four paired superblocks per protocol,
two Firefox references plus native/replacement SOCKS and HTTP arms per block:
48 admitted two-second cold-page captures. H2 seed 202608353; H3 seed 202608354.
Lower residual is better. Cells below are **candidate / contemporary native**.

| Protocol / listener | p1–16 | p17–32 | p1–32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| H2 SOCKS | 0.01844 / 0.03798 | 0.21365 / 0.40825 | 0.07816 / 0.18705 | 0.17924 / 0.20268 | 0.21638 / 0.37039 |
| H2 HTTP CONNECT | 0.02694 / 0.06895 | 0.33219 / 0.46837 | 0.12706 / 0.21763 | 0.17185 / 0.20665 | 0.21632 / 0.37517 |
| H3 SOCKS | 0.06649 / 0.07818 | 0.16618 / 0.19385 | 0.09312 / 0.11841 | 0.11388 / 0.14548 | 0.17201 / 0.32937 |
| H3 HTTP CONNECT | 0.08059 / 0.08607 | 0.20755 / 0.22414 | 0.10603 / 0.12862 | 0.10734 / 0.14955 | 0.19990 / 0.34481 |

| Protocol / listener | p17–32 reduction | Whole reduction | Extra outer traffic | Native → candidate page time | Effective page-rate loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| H2 SOCKS | 47.7% | 41.6% | +60.3% | 104.76 → 319.36 ms | 67.2% |
| H2 HTTP CONNECT | 29.1% | 42.3% | +60.1% | 109.01 → 310.18 ms | 64.9% |
| H3 SOCKS | 14.3% | 47.8% | +43.5% | 111.05 → 328.06 ms | 66.1% |
| H3 HTTP CONNECT | 7.4% | 42.0% | +45.1% | 114.84 → 308.60 ms | 62.8% |

Status is `INSUFFICIENT_FOR_INFERENCE`: four blocks, diagnostic gate mode,
not the required 30+ blocks for paired inference and not proof of indistinguishability.
Per-arm confidence intervals, all samples and native baselines are retained in
`analysis.json`; p17–32 has substantial uncertainty. The matrix primarily exercises
startup; it is not camouflage qualification for arbitrarily long bulk transfers.

## Separate mixed-session cost

Two native/replacement pairs per protocol; original workload: four initial
98,304-byte probes, a late 1-MiB download, delayed response, target-throttled
1-MiB upload, four parallel 512-KiB transfers across both local listeners, then
4-KiB wake. This is **not** the cold-page workload above. All payload/flow gates
passed. H2 seed 202608355; H3 retry seed 202608357. The first H3 run at seed
202608356 lost 47 capture packets and is retained as invalid, not averaged.
Only the capture buffer was increased to 32 MiB for the retry.

| Protocol | Extra complete-session traffic | 1-MiB native → candidate | 1-MiB effective-rate loss | Parallel native → candidate | 4-KiB wake native → candidate |
| --- | ---: | ---: | ---: | ---: | ---: |
| H2 | +44.5% | 13.09 → 70.68 ms | 81.5% | 17.57 → 79.01 ms | 7.05 → 25.58 ms |
| H3 | +42.2% | 13.44 → 64.25 ms | 79.1% | 18.53 → 84.35 ms | 7.20 → 26.80 ms |

Time growth = `100*(candidate_time/native_time - 1)`; effective fixed-work
speed loss = `100*(1-native_time/candidate_time)`. These are different percentages.
Fast isolated-loopback penalties are not predictions for an Internet connection.
The earlier ~32% overhead belonged to continuous-v1's mixed workload; the selected
faster pipeline spends more capacity. Full raw numeric summaries are in `matrix.json`.

## Application/site and implementation contract

- Dedicated module, same-origin real SPA, private key (at least 32 bytes), exact target allowlist; an arbitrary static site is not interchangeable.
- Root 4 KiB, CSS 12 KiB, JS 24 KiB, four SVGs 8 KiB each; 72-KiB static bootstrap. Root ASCII retains native-control compatibility.
- Startup: 20 fixed upload/response pairs, 80 KiB upload and 880 KiB download capacity; useful bytes displace filler. Carrier bodies are no-store and uncompressed.
- Ongoing interactive/upload/mixed four-slot leases; bulk is two finite 16-KiB POST / 256-KiB response exchanges, at most two responses in flight, ordered delivery and bounded 512-KiB per-stream credit. Maximum 32 logical streams; 4-GiB per-stream sequence limit remains.
- Idle stays one 30-second maximum long poll with 512-byte cells and explicit wake POST. The 204/8-KiB idle variant was not selected because its composed latency gain did not persist.
- Firefox owns outer TLS/H2/H3. Outer CONNECT is absent; loopback WSS is local IPC only. No production reconnection/key management/lean-runtime integration is claimed.

Compared with the preceding bulk-duplex profile, controlled 8-MiB screens showed
12.1% H2 / 20.6% H3 less completion time and 13.1% / 11.5% less parallel-transfer
time, with roughly 1.9% / 1.8% more wire bytes. This is why pipeline was selected;
those relative screens must not be confused with the native penalties above.
All positive, negative and unmeasured variants remain described in
[APPLICATION-CARRIER.md](APPLICATION-CARRIER.md).

## Retained workspace / cleanup

No new archives were created. Obsolete export/configure/transition trees, old
build logs, cumulative copied evidence/bundles, stale research binaries/caches,
raw captures, payload copies and temporary browser profiles were deleted.
Numeric results, features, schedules, capture-health logs, provenance and current
binaries remain in `/home/zubastik/naivefox-app-carrier-20260830.U0xyrg`
(about 101 MiB before the final documentation files).

Kept source repositories and current Linux/Windows/Android build roots/toolchains,
including `/home/zubastik/naivefox-refresh-20260830.fJHfmY`. The clean minimal
worktree moved to `/home/zubastik/src/naivefox-minimal-source`; its branch/commit
were not changed. The old reference worktree with four uncommitted C++ changes
was deliberately left untouched. Historical raw-capture and snapshot paths in
the journal may no longer exist; numeric results and Git history are retained.
Deleted raw files/logs are not recoverable here; generated exports/build outputs
can be recreated from source. WSL filesystem space was freed; the Windows VHDX
was not compacted. See the dated cleanup script for the guarded deletion scopes.

Recompute the matrix (no network experiment):

```sh
/home/zubastik/naivefox-refresh-20260830.fJHfmY/full-linux/camouflage-venv/bin/python \
  /home/zubastik/src/naivefox/netwerk/naivefox/test/application-carrier/matrix.py \
  /home/zubastik/naivefox-app-carrier-20260830.U0xyrg
```
