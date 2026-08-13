# NaiveFox prototype test report

Date: 2026-08-13

Environment: `Ubuntu24Dev`, x86-64, Firefox opt build

H2 baseline tag: `h2-prototype-v0.1`

Current branch: `feature/h3`

This is the committed acceptance record for the local prototype, the supplied
real Caddy deployment, the staged runtime, and the official NaiveProxy control
client. It intentionally contains no endpoint, username, password, proxy
authorization value, packet payload, or TLS key material.

## Build and focused automated tests

| Command | Result |
|---|---|
| `./mach build -j4 binaries` | PASS, 0 compiler warnings |
| `./mach gtest Naive*` | PASS, 30/30 tests in 6 suites |
| `./mach xpcshell-test netwerk/test/unit/test_proxyconnect.js netwerk/test/unit/test_proxyconnect_headers.js netwerk/test/unit/test_proxyconnect_https.js netwerk/test/unit/test_proxyconnect_raw.js netwerk/test/unit/test_proxyconnect_padding_header.js` | PASS, 5/5 tests |
| `git diff --check` | PASS |

The gtests cover fragmented SOCKS5 parsing, padding negotiation, Variant 1
encoder/decoder boundaries, deterministic randomized round trips, truncation,
partial drains, entropy failure, and CONNECT header-padding vectors. The
xpcshell set covers existing proxy behavior plus raw HTTP/1.1 and HTTP/2
CONNECT, exact CONNECT-only padding headers, response metadata, and stream I/O.

## HTTP/3 local prototype gate

The H3 work uses the same `naivefox` executable, SOCKS server, padding codec,
and pump as H2. The fixture is H3-only for strict tests: Caddy listens on UDP
and has no TCP listener on the proxy port, so hidden H2 fallback cannot satisfy
the workload.

| Command | Result |
|---|---|
| `./mach build -j4 binaries` | PASS, 0 compiler warnings |
| `./mach xpcshell-test netwerk/test/unit/test_proxyconnect_h3_raw.js` | PASS, 11/11 focused checks |
| `netwerk/naivefox/test/integration/run-h3-raw-connect-tests.sh` | PASS, CONNECT 200 plus deterministic 407 authentication failure |
| `netwerk/naivefox/test/integration/run-h3-padded-tests.sh` | PASS, six HTTP/HTTPS padded tunnels including 3 MiB download and 2 MiB upload |
| `netwerk/naivefox/test/integration/run-h3-robustness-tests.sh` | PASS |
| `./mach gtest 'NaiveFoxAutoFallback*'` | PASS, 3/3 policy tests |
| `netwerk/naivefox/test/integration/run-auto-protocol-tests.sh` | PASS |
| `netwerk/naivefox/test/integration/run-robustness-tests.sh --protocol h2` | PASS, H2 regression with the same workload |

The H3 robustness runner verifies 32 MiB slow download and upload integrity,
an RSS growth gate of at most 32 MiB after warm-up, local disconnect, target
early close, timeout, ACL rejection, request half-close followed by response,
invalid proxy authentication, forced proxy loss, and four simultaneous CONNECT
streams on one NaiveFox-owned UDP/QUIC socket. The invalid-authentication case
also regresses an upgrade lifecycle race: Necko can publish H3 tunnel streams
before the final channel failure, so `SocksConnection` now waits for transport,
CONNECT metadata, and successful channel completion before returning SOCKS
success.

The expected negative-path curl diagnostics are truncated response, timeout,
and rejected SOCKS target; the runner requires those failures before reporting
PASS. No credentials, response bodies, packet captures, or key material are
retained in this report.

Auto mode was tested in both raw and SOCKS modes. An H2-only fixture caused a
single strict H3 establishment timeout followed by H2 success. Against the
H3-only fixture, H3 success, invalid authentication, and denied target cases
ran alongside a TCP decoy bound to the same numeric proxy port; the decoy
accepted zero connections. This proves logical H3 failures do not create a
hidden H2 retry. The pure policy matrix also rejects fallback after CONNECT
codes 200, 403, 407, 502, and 504, after transport publication, after owner
cancellation, and after the one allowed retry has been consumed.

H3 performance comparison, passive/decrypted capture comparison, staged
runtime verification, and the ten-minute real-server soak remain mandatory
acceptance gates and are not claimed complete by this local result.

## Complete local integration gate

Command:

```bash
netwerk/naivefox/test/integration/run-local-suite.sh
```

Result: PASS. All seven constituent runners completed:

| Runner | Covered result |
|---|---|
| `run-control-tests.sh` | Pinned Caddy fixture lifecycle and access controls |
| `run-necko-tests.sh` | NSS trust rejection/success, hostname validation, public Necko fetch |
| `run-raw-connect-tests.sh` | Auth success/failure, outer ALPN `h2`, raw CONNECT streams |
| `run-socks-tests.sh` | SOCKS5 remote-hostname HTTP and HTTPS paths |
| `run-padded-tests.sh` | Negotiated Variant 1 traffic, multi-megabyte download/upload integrity, repeats |
| `run-robustness-tests.sh` | 32 MiB slow download/upload, bounded memory, half-close, early close, timeouts, proxy loss, seven simultaneous H2 streams on one outer TCP connection |
| `run-capture-comparison.sh` | Firefox/NaiveFox TLS and HTTP/2 capture comparison |

Expected negative-path `curl` diagnostics from the robustness runner were a
truncated response, a timeout, and a rejected SOCKS target. The runner reported
PASS after verifying those outcomes.

The detailed safe packet-capture comparison is recorded in
[`CAPTURE.md`](CAPTURE.md). Raw pcaps, NSS key logs, copied profiles, and process
logs were deleted after the sanitized aggregates were generated.

## Supplied real Caddy deployment

The credentials were supplied only through environment variables. Public CA
validation remained enabled; no fixture CA, `--insecure`, `-k`, or trust bypass
was used.

Command shape:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
NAIVEFOX_REAL_DURATION_SECONDS=120 \
netwerk/naivefox/test/integration/run-real-server-tests.sh
```

### Development runtime, 120-second session

One NaiveFox process remained alive for the whole session. The initial
sequential workload and six four-request parallel waves were separated by
20-second idle intervals.

| Measure | Result |
|---|---|
| Session length | 120 seconds |
| Sequential requests | 5/5 HTTP 200 |
| Parallel waves | 6 waves, 4 concurrent requests per wave, 24/24 HTTP 200 |
| Destinations | Example, Mozilla, IANA, GitHub HTML/raw/codeload |
| Proxied response bytes | 4,010,330 bytes total |
| Largest bounded artifact | Caddy source archive, 804,503 bytes |
| Archive integrity | Direct and proxied SHA-256 matched |
| Archive SHA-256 | `744474db518144ecccdba02ed59d451c695ccde19c5b6ec55c78c264077e4b2b` |
| SOCKS connections | 29 accepted and completed |
| Padding negotiation | 29/29 `yes`; no raw fallback |

Observed response sizes were stable: Example 559 bytes, IANA 10,499 bytes,
Mozilla 48,674 bytes, the pinned forwardproxy README 12,622 bytes, and GitHub
HTML approximately 426-462 KiB. No response or integrity check failed.

### Packaged runtime, 30-second session

The newly staged package, rather than the object-directory executable, was
then used against the same deployment.

| Measure | Result |
|---|---|
| Session length | 30 seconds |
| Sequential requests | 5/5 HTTP 200 |
| Parallel waves | 2 waves, 4 concurrent requests per wave, 8/8 HTTP 200 |
| Proxied response bytes | 2,222,494 bytes total |
| Archive integrity | Same direct/proxied SHA-256 match |
| SOCKS connections | 13 accepted and completed |
| Padding negotiation | 13/13 `yes`; no raw fallback |

### Real-deployment defect found and fixed

The first real run exposed a Firefox-specific routing defect for HSTS-preloaded
targets. The synthetic `http://authority/` URI exists only to carry a raw
CONNECT destination, but Necko upgraded it to a direct origin HTTPS channel for
hosts such as GitHub. `OpenNeckoTunnel` now sets `allowSTS = false` on that
non-navigational internal channel. Explicit HTTPS proxy routing is retained,
while outer TLS and HTTP/2 remain entirely Firefox Necko/NSS behavior.

The GitHub requests in both successful real sessions are the regression check
for this defect.

## Official NaiveProxy reference control

`fetch-naiveproxy-reference.sh` downloaded the official Linux x64 release from
GitHub and verified the release digest before extraction:

| Field | Value |
|---|---|
| Release | `v150.0.7871.63-1` |
| Binary version | `naive 150.0.7871.63` |
| Asset SHA-256 | `0c4f506ce66a7881892fd6932b542c53fc06ac2351987756096c61e753c687bf` |
| Source | `https://github.com/klzgrad/naiveproxy/releases/tag/v150.0.7871.63-1` |

The control runner used a private generated JSON config and one long-lived
official client process. Its 60-second result was PASS: the warm-up request and
three waves of four parallel requests all returned HTTP 200, including GitHub.

This control establishes server and workload compatibility. It is not a
fingerprint target: official NaiveProxy's Chromium-specific preambles and
camouflage remain deliberately out of scope. NaiveFox must look like Firefox
because its observable TLS and H2 stack is Firefox Necko/NSS.

## Staged runtime

Commands:

```bash
netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64-m83
netwerk/naivefox/tools/verify-staged-runtime.sh \
  --fetch https://example.com/ naivefox-linux-x86_64-m83
```

Result: PASS. The verified package was promoted to
`obj-x86_64-pc-linux-gnu/naivefox-linux-x86_64-final`.

| Check | Result |
|---|---|
| Installed size | 378 MiB |
| Copy to a fresh `/tmp` directory | PASS |
| Broken or absolute staged symlinks | None |
| `ldd` missing libraries or object-directory paths | None |
| External fresh profile and runtime smoke | PASS |
| Public HTTPS fetch from copied package | HTTP 200, 559-byte Example body |
| Strict H2 SOCKS/padding/integrity from copied package | PASS |
| Strict H3 SOCKS/padding/integrity from copied package | PASS, UDP-only fixture |
| Live process maps containing source/objdir paths | None |
| Real Caddy padded workload from staged package | PASS, recorded above |

The previous generated package was replaced only after the new staged copy had
passed the `/tmp` verification. Test profiles and credentials are not part of
the package.

The H3 verification used the same 378 MiB staged layout. Neqo is linked into
`libxul`, NSS/NSPR were already present, and networking remains in-process, so
H3 required no additional library, `plugin-container`, or second executable.
The copied package completed HTTP and HTTPS SOCKS targets, six negotiated
padding tunnels per protocol, a 3 MiB download SHA-256 check, and a 2 MiB
upload byte-count/SHA-256 check in both strict H2 and strict H3 modes.

## Data-retention result

Failed-run profiles, downloaded bodies, the credential-bearing Necko debug
trace, temporary official-client configs, and duplicate release downloads were
removed from the object directory after diagnosis. They are not recoverable.
Only ignored, credential-free summary files and the digest-verified official
reference package remain locally.

## Extended throughput benchmark

The repeatable loopback benchmark moved approximately 10.81 GiB through the
deterministic target and compared the official NaiveProxy
`v150.0.7871.63-1` binary with NaiveFox. Every proxied 64 MiB integrity object
and every upload byte count/SHA-256 matched.

NaiveFox medians were 421.480 MiB/s sequential download, 431.364 MiB/s with
four parallel downloads, 412.577 MiB/s with eight parallel downloads,
288.093 MiB/s sequential upload, and 390.542 MiB/s with four parallel uploads.
Official-client medians for the same phases were 497.799, 342.281, 284.343,
325.700, and 349.085 MiB/s. Direct controls were 1,577.952 MiB/s sequential
and 1,289.432 MiB/s with eight parallel downloads.

Result: PASS. Full methodology, memory figures, relative comparisons, and
limitations are in [`PERFORMANCE-REPORT.md`](PERFORMANCE-REPORT.md).

## Passive external-observer comparison

The no-keylog capture compared ordinary Firefox and NaiveFox against the same
fixture TLS endpoint under a matched 4 MiB server-to-client workload. Ordered
visible ClientHello configuration fields and visible ServerHello choices were
exact matches. Each side used one outer TCP stream. Both had 16,401-byte p90,
p95, and p99 server TLS records; server TLS volume differed by only 2,550 bytes
because NaiveFox carried two padded CONNECT streams instead of one browser GET.
The plaintext request canary was absent.

Result: PASS. The exact safe aggregates, observable differences, TLS 1.3
visibility boundary, and WSL loopback caveat are in
[`OBSERVER-TRAFFIC-REPORT.md`](OBSERVER-TRAFFIC-REPORT.md).

## Ten-minute real-deployment soak

One packaged NaiveFox process ran for the entire test without
`--max-connections`. Fifteen small immutable GitHub objects were requested at
seconds 0, 30, 60, 90, 120, 240, 270, 300, 330, 360, 480, 510, 540, 570, and
600. The schedule intentionally includes two 120-second idle windows.

| Measure | Result |
|---|---|
| Attempts | 15 |
| HTTP 200 and SHA-256 matches | 15/15 |
| Timeouts | 0 |
| Total response bytes | 189,330 |
| Response SHA-256 | `3c9f0da4c8313e9c1e3a32935024abe1234215be1bd79cf01f78065359f3a5d2` |
| Latency p50 / p95 / max | 0.236 / 0.803 / 1.092 seconds |
| Liveness/resource samples | 601 |
| RSS baseline / peak / final | 92,988 / 93,580 / 93,264 KiB |
| Final RSS delta | +276 KiB |
| FD baseline / peak / final | 38 / 40 / 39 |
| Threads baseline / peak / final | 27 / 27 / 24 |
| Outer TCP epochs / reconnects | 2 / 1 |
| Maximum simultaneous outer TCP | 1 |
| Padding negotiation | 15/15 `yes`; no raw fallback |
| Functional/resource/sampling gates | PASS / PASS / PASS |

Both requests immediately following the long idle windows succeeded. The outer
connection turned over once during the session; Firefox/Necko reconnected
transparently while the same NaiveFox process and SOCKS listener remained
alive. This is expected resilience, not an application restart.

The soak runner is
`netwerk/naivefox/test/integration/run-real-server-soak.sh`. Credentials are
environment-only, the endpoint is intentionally anonymized here, raw metrics
are private, and only the credential-free aggregate is retained locally.
