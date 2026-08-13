# NaiveFox prototype test report

Date: 2026-08-13

Environment: `Ubuntu24Dev`, x86-64, Firefox opt build

Branch: `naivefox`

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
| Real Caddy padded workload from staged package | PASS, recorded above |

The previous generated package was replaced only after the new staged copy had
passed the `/tmp` verification. Test profiles and credentials are not part of
the package.

## Data-retention result

Failed-run profiles, downloaded bodies, the credential-bearing Necko debug
trace, temporary official-client configs, and duplicate release downloads were
removed from the object directory after diagnosis. They are not recoverable.
Only ignored, credential-free summary files and the digest-verified official
reference package remain locally.
