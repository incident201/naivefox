# NaiveFox prototype test report

Date: 2026-08-13

Environment: `Ubuntu24Dev`, x86-64, Firefox opt build

H2 baseline tag: `h2-prototype-v0.1`

Validated reference branch: `naivefox`; combined H2/H3 baseline tag:
`h2-h3-prototype-v0.2`

This is the committed acceptance record for the local prototype, the supplied
real Caddy deployment, the staged runtime, and the official NaiveProxy control
client. It intentionally contains no endpoint, username, password, proxy
authorization value, packet payload, or TLS key material.

## Build and focused automated tests

| Command | Result |
|---|---|
| `./mach build -j4 binaries` | PASS, 0 compiler warnings |
| `./mach gtest 'NaiveFox*'` plus `./mach gtest 'NaivePadding*'` | PASS, 49/49 tests in 9 suites |
| `./mach xpcshell-test netwerk/test/unit/test_proxyconnect.js netwerk/test/unit/test_proxyconnect_headers.js netwerk/test/unit/test_proxyconnect_https.js netwerk/test/unit/test_proxyconnect_raw.js netwerk/test/unit/test_proxyconnect_padding_header.js` | PASS, 5/5 tests |
| `git diff --check` | PASS |

The gtests cover strict config parsing, fragmented HTTP CONNECT and SOCKS5
parsing, padding negotiation, Variant 1
encoder/decoder boundaries, deterministic randomized round trips, truncation,
partial drains, entropy failure, CONNECT header-padding vectors, and secure
temporary-profile creation/removal without a home directory. The
xpcshell set covers existing proxy behavior plus raw HTTP/1.1 and HTTP/2
CONNECT, exact CONNECT-only padding headers, response metadata, and stream I/O.

## NaiveProxy-compatible config and local listeners

The user-facing config stage changed only `netwerk/naivefox/`; it did not
update the Firefox snapshot or add a Necko/Neqo patch. `SocksConnection` and
the new `HttpConnectConnection` delegate all upstream attempts, CONNECT
metadata, H2/H3/Auto policy, padding negotiation, transport barriers, and
duplex pumping to one shared `TunnelSession`.

| Gate | Result |
|---|---|
| Config parser gtests | PASS, string/array listeners and proxies, shared/one-to-one upstream mapping, H2/H3 URI mapping, default/explicit port, IPv4/IPv6, wildcard/non-loopback bind addresses, percent credentials, log modes, and strict failures |
| HTTP CONNECT parser gtests | PASS, arbitrary fragmentation, split CRLF, authorities, non-CONNECT, oversized headers, and early payload |
| `run-h2-config-tests.sh` | PASS, one process, 10 padded H2 tunnels, SOCKS5 + HTTP CONNECT, 3 MiB downloads, 2 MiB uploads, mixed concurrency |
| `run-h3-config-tests.sh` | PASS, the same workload over an H3-only UDP fixture, 10 padded H3 tunnels and no TCP fallback |
| `run-config-runtime-behavior-tests.sh` | PASS, absent log is silent, empty log is console-covered, file log is `0600`, persistent and no-home temporary profiles are `0700`, and a concrete non-loopback interface bind accepts traffic |
| `run-full-suite.sh` | PASS in 311.5 seconds, including all pre-existing H2/H3, Auto, robustness, and capture gates plus config mode |

The H2 and H3 config runs used two `0.0.0.0` listeners and a two-element
`proxy` array containing the same URI twice, matching NaiveProxy's
listener-by-index semantics. Both listening sockets were verified as wildcard
binds before traffic. A separate run bound SOCKS to the WSL instance's actual
non-loopback IPv4 address and connected through that address successfully.

The local HTTP listener returned 405 for an ordinary forward-proxy request and
did not emit its 200 response until the upstream CONNECT had succeeded. The
mixed phase ran two SOCKS and two HTTP tunnels concurrently. H2 reused one
established outer TCP connection; strict H3 succeeded against a UDP-only
listener where H2/TCP fallback could not satisfy the workload.

The supplied real Caddy was then tested with the newly staged package and
public CA validation. Each strict protocol run used one process, both wildcard
listeners, and two repeated per-listener proxy-array entries:

| Protocol/config scheme | Result |
|---|---|
| H2 / `https://` | PASS, 8/8 padded tunnels, two normal HTTPS pages, direct/proxied integrity match, four mixed concurrent requests |
| H3 / `quic://` | PASS, 8/8 padded tunnels, the same normal/integrity/concurrency workload, no H2 protocol selection |

The endpoint and credentials were supplied only through the private generated
config and were removed during cleanup. Neither appears in this report,
runtime output, or retained summaries.

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

The H3-stage core regression pass used a warning-free build, 33/33 project
gtests, and six sequential proxy-CONNECT xpcshell files: existing H1/H2
CONNECT, header handling, HTTPS proxying, raw H1/H2 takeover, CONNECT padding,
and the new raw H3 path all passed. The strict H3, IPv6 H3-proxy fallback, and
HTTP/2-over-H3-proxy Firefox tests also passed.
The subsequent config/listener compatibility stage expanded the warning-free
project gate to 48/48 gtests and reran the complete local H2/H3 integration
suite.

Two broader Mozilla tests expose frozen-snapshot limitations outside the
NaiveFox classic-CONNECT path. `test_http3_proxy.js` passes 37 assertions and
then its ordinary-channel three-concurrent helper times out all three channels;
`test_http3_with_proxy.js` fails its first CONNECT-UDP/MASQUE origin-H3 route
with `NS_ERROR_PROXY_CONNECTION_REFUSED`. Each failure was reproduced after a
controlled incremental build with every NaiveFox-modified H3/proxy Necko file
mechanically reverted to `h2-prototype-v0.1`, then the current patch was
restored and rebuilt. They are therefore recorded as base/environment defects,
not hidden as passes and not attributed to the connect-only prototype. The
project's required four concurrent classic CONNECT streams, 32 MiB transfers,
slow paths, and H3 capture multiplexing all pass.

Auto mode was tested in both raw and SOCKS modes. An H2-only fixture caused a
single strict H3 establishment timeout followed by H2 success. Against the
H3-only fixture, H3 success, invalid authentication, and denied target cases
ran alongside a TCP decoy bound to the same numeric proxy port; the decoy
accepted zero connections. This proves logical H3 failures do not create a
hidden H2 retry. The pure policy matrix also rejects fallback after CONNECT
codes 200, 403, 407, 502, and 504, after transport publication, after owner
cancellation, and after the one allowed retry has been consumed.

The extended H3 performance, passive/decrypted capture, staged-runtime, and
ten-minute real-server gates are reported below. All completed successfully.

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
netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64-config-compat
netwerk/naivefox/tools/verify-staged-runtime.sh \
  --fetch https://example.com/ naivefox-linux-x86_64-config-compat
```

Result: PASS. The verifier copied the package below `/tmp` and ran its root
`./naivefox` launcher; the native executable and GRE dependencies remained in
the package's `runtime/` directory. The redundant root `run-naivefox` alias was
removed: the staged root contains one user-facing `naivefox` launcher plus the
`runtime/` directory.

| Check | Result |
|---|---|
| Installed size | 378 MiB |
| Root launchers | One: `naivefox` |
| Copy to a fresh `/tmp` directory | PASS |
| Broken or absolute staged symlinks | None |
| `ldd` missing libraries or object-directory paths | None |
| External fresh profile and runtime smoke | PASS |
| Public HTTPS fetch from copied package | HTTP 200, 559-byte Example body |
| Config startup without home/XDG/profile variables | PASS, private mode-`0700` temporary profile and listening endpoint |
| No-argument adjacent `config.json` invocation | PASS, strict H2 |
| Positional config invocation | PASS, strict H3 |
| Strict H2 SOCKS + HTTP CONNECT/padding/integrity | PASS, 10 tunnels |
| Strict H3 SOCKS + HTTP CONNECT/padding/integrity | PASS, 10 tunnels, UDP-only fixture |
| Live process maps containing source/objdir paths | None |
| Real Caddy config workload from staged package | PASS over H2 and H3, recorded above |

The previous generated package was replaced only after the new staged copy had
passed the `/tmp` verification. Test profiles and credentials are not part of
the package.

The H3 verification used the same 378 MiB staged layout. Neqo is linked into
`libxul`, NSS/NSPR were already present, and networking remains in-process, so
H3 required no additional library, `plugin-container`, or second executable.
The copied package completed SOCKS and HTTP CONNECT targets, ten negotiated
padding tunnels per protocol, two 3 MiB download SHA-256 checks, two 2 MiB
upload byte-count/SHA-256 checks, and mixed frontend concurrency in both strict
H2 and strict H3 modes. No build-tree `LD_LIBRARY_PATH`, explicit profile CLI,
or credential environment variables were used by config mode. A separate
packaged-runtime invocation removed `HOME`, `XDG_STATE_HOME`,
`XDG_RUNTIME_DIR`, and `NAIVEFOX_PROFILE`; Gecko/NSS initialized and the local
listener became ready using the temporary-profile fallback.

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

The same 64 MiB/three-trial workload was then run in strict H3 mode:

```bash
NAIVEFOX_BENCHMARK_REFERENCE_BINARY=/tmp/naiveproxy-source-v150/src/out/Release/naive \
  netwerk/naivefox/test/integration/run-h3-throughput-benchmark.sh
```

The runner completed in 121.5 seconds and moved approximately 10.81 GiB. All
download SHA-256 and upload byte-count/SHA-256 gates passed. NaiveFox recorded
68 H3 selections and 68 negotiated-padding tunnels, with neither H2 selection
nor raw fallback. The fixture's adapted proxy listener permitted exactly `h3`,
and the reference had a single `quic://` proxy, so H2 could not make the test
pass.

| Client | Sequential download | 4-parallel download | 8-parallel download | Sequential upload | 4-parallel upload | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| NaiveProxy v150 test build | 97.793 MiB/s | 92.743 MiB/s | 83.856 MiB/s | 80.123 MiB/s | 81.947 MiB/s | 20.8 MiB |
| NaiveFox | 82.152 MiB/s | 81.785 MiB/s | 87.969 MiB/s | 86.166 MiB/s | 99.732 MiB/s | 117.2 MiB |

Result: PASS. The published NaiveProxy binary rejects a locally trusted,
ephemeral fixture CA in its additional QUIC known-public-root check even after
normal certificate verification succeeds. The H3 reference was therefore
built from exact tag `v150.0.7871.63-1` at commit
`3ba967e2d36cc133a896e81a36257ad4c6ea20f4`, with a three-line test-only
exception for exact hostname `localhost` in
`net/quic/crypto/proof_verifier_chromium.cc`. The normal chain, name, date, and
signature checks remain active. The patch SHA-256 is
`fe09dd9100fe22fbe30ee39b81226397aca6c5ddf75b33003fdaa7946df83bec` and
the test binary SHA-256 is
`b837bc242b269d3e30f99fa4461b863bcb0046a9c1f13a2bf91ef75b4f4ad86b`.
No QUIC/HTTP/3/padding/data-path source was changed. Raw NetLog was removed and
the final runner does not generate one.

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

## Strict HTTP/3 ten-minute real-deployment soak

The final run used the fresh 378 MiB staged runtime, normal public certificate
validation, and explicit `--protocol h3`; neither `--insecure` nor an object
directory library path was used. A normal HTTPS page and immutable transfer
passed before the timed interval. One process then remained alive for exactly
600 seconds while 26 integrity-checked requests ran at seconds 0, 30, 60, 90,
120, 240, 270, 300, 330, 360, 480, 510, 540, and 570. The events at 60, 270,
330, and 510 used four parallel requests. This retains two 120-second idle
windows and a final 30-second idle interval.

| Measure | Result |
|---|---|
| Observation | 600.127 seconds |
| Attempts / HTTP 200 / SHA-256 matches | 26 / 26 / 26 |
| Timeouts | 0 |
| Total response bytes | 328,172 |
| Latency p50 / p95 / max | 0.239 / 0.399 / 0.466 seconds |
| Resource samples | 601 |
| RSS baseline / peak / final | 94,756 / 96,444 / 95,188 KiB |
| Final RSS delta | +432 KiB |
| FD baseline / peak / final | 39 / 39 / 39 |
| Threads baseline / peak / final | 27 / 27 / 24 |
| UDP socket epochs / maximum simultaneous | 3 / 1 |
| TCP proxy sockets | 0 |
| Outer protocol confirmations | 28 `h3`; 0 `h2` |
| Padding negotiation | 28 `yes`; 0 raw fallback |
| Functional/resource/sampling/transport/liveness gates | PASS / PASS / PASS / PASS / PASS |

Both requests following the long idle intervals succeeded, as did every
parallel wave. Neqo uses an unconnected UDP socket, so `/proc/net/udp` does not
contain a remote proxy port for that socket; the runner keeps that value as a
diagnostic instead of treating it as a gate. Strict transport proof combines
the observed UDP socket, zero TCP proxy sockets, and Necko's 28 actual `h3`
confirmations. Three UDP socket inode epochs indicate transparent transport
turnover without restarting NaiveFox.

The first staged preflight attempt timed out before an outer protocol was
selected. A bounded comparison immediately established H3 from the object
directory, and a second staged preflight also established H3; the complete
staged run above then passed. The cold-start timeout was observed once, was not
reproducible in the final preflight or soak, and is retained as a transient
external-network observation rather than hidden or counted as a protocol
fallback. See `KNOWN-ISSUES.md`.

The runner is
`netwerk/naivefox/test/integration/run-real-server-h3-soak.sh`. The endpoint
and credentials are intentionally absent from the report and client output.
Raw metrics, bodies, profiles, and sensitive logs are not retained after
aggregation.

## Strict HTTP/3 capture comparison

Command:

```bash
netwerk/naivefox/test/integration/run-h3-capture-comparison.sh
```

Result: PASS. The current runner completed all four workloads and all online
assertions with exit status zero as part of `run-full-suite.sh`. An earlier
development run exposed an over-strict exact-one-Firefox-connection assertion;
the final runner accepts a normal Firefox retry while still requiring one
NaiveFox QUIC connection for the two CONNECT streams.

The decrypted pass proved QUIC v1 with `h3`, matching semantic TLS
configuration, matching client transport parameters, and matching HTTP/3 and
QPACK settings for ordinary Firefox and NaiveFox from the same build family.
Ordinary Firefox sent one GET. NaiveFox sent classic CONNECT on stream IDs 0
and 4 over one outer QUIC connection. Both CONNECT requests and responses
carried the `padding` header name; no synthetic `alpn`, `upgrade`, or
`connection` header was present.

The passive no-keylog pass observed 1,762 Firefox and 1,845 NaiveFox UDP
datagrams for approximately 2 MiB server-to-client workloads. Server UDP bytes
were 2,167,712 and 2,166,305 respectively. Neither side established TCP or sent
TCP payload. Ordinary Firefox emitted two TCP SYN probes which the strict
UDP-only fixture rejected with RST and performed a second QUIC attempt;
NaiveFox emitted no TCP probe and kept both CONNECT streams on one QUIC
connection. No Version Negotiation packet occurred.

Raw pcaps, NSS keys, profiles, screenshots, bodies, and logs were deleted after
successful aggregation. The retained credential-free summary is under
`obj-x86_64-pc-linux-gnu/naivefox-fixture/h3-capture-safe/` and the complete
methodology and safe results are in [`H3-CAPTURE.md`](H3-CAPTURE.md).

## Minimal staged runtime gate

Phase 14.1 replaced recursive GRE resource staging with an explicit traced
allowlist. The package decreased from 388,995,134 bytes (378 MiB apparent) to
345,903,270 bytes (331 MiB apparent), an exact reduction of 43,091,864 bytes
or 11.08%, without rebuilding Gecko.

Command:

```bash
./netwerk/naivefox/tools/stage-runtime.sh \
  naivefox-linux-x86_64-min-runtime-v1
./netwerk/naivefox/tools/verify-staged-runtime.sh \
  --fetch https://example.com/ naivefox-linux-x86_64-min-runtime-v1
```

Result: PASS. The verifier copied the package below `/tmp`, removed inherited
loader/keylog state, checked its hashed manifest and ELF closure, ran runtime
smoke and a public HTTPS fetch, exercised temporary-profile startup without a
home, then passed simultaneous config-mode SOCKS5 and HTTP CONNECT workloads
over strict H2 and strict H3. It additionally passed Auto H3 preference,
single H2 establishment fallback, and the no-fallback authentication/target
error cases. The package manifest still matched after all workloads. See
`MINIMISATION-REPORT.md` for the measured closure and largest files.

## Minimal build baseline gate

The first separate `obj-naivefox-minimal` build kept tests enabled and disabled
only the updater and crash reporter. The stripped package was 344,217,666
bytes and its `libxul.so` was 325,341,920 bytes.

| Gate | Result |
|---|---|
| `MOZCONFIG=netwerk/naivefox/mozconfig-minimal ./mach build -j4` | PASS |
| Project gtests | PASS, 49/49 |
| Focused H1/H2/H3 proxy-CONNECT xpcshell tests | PASS, 6/6 |
| Copied staged package H2/H3/Auto verification | PASS |
| `run-full-suite.sh` | PASS, 307.6 seconds |

The complete suite included strict H2 and H3 raw, SOCKS, padding, integrity,
backpressure, lifecycle, multiplexing, Auto, simultaneous config listeners,
and both capture comparisons. An in-tree test environment ordering bug found
during this gate was fixed: all `env -u` options now precede the internal
`LD_LIBRARY_PATH` assignment. Failure output is retained only as a sanitized,
ignored diagnostic.

## WebRTC-free product build gate

Disabling WebRTC reduced build descriptors by 34.2%, from 21,487 to 14,142,
and reduced the staged package from 344,217,666 to 329,628,832 bytes. The
stripped `libxul.so` decreased by 14,584,176 bytes to 310,757,744 bytes.

| Gate | Result |
|---|---|
| `mach build -j4 binaries` | PASS, zero project warnings |
| Project gtests | PASS, 49/49 |
| Focused H1/H2/H3 proxy-CONNECT xpcshell tests | PASS, 6/6 |
| Copied staged package H2/H3/Auto verification | PASS |
| `run-full-suite.sh` | PASS, 308.3 seconds |

The suite proves that regular Necko H2/H3 CONNECT, strict fallback policy,
padding, SOCKS/HTTP listeners, large transfers, lifecycle, pooling and capture
behavior do not depend on WebRTC. No Necko, Neqo, NSS, or PSM implementation
was modified for this build reduction.

## Accessibility-free product build gate

Disabling Gecko accessibility reduced build descriptors from 14,142 to
13,918 and reduced the staged package to 327,756,137 bytes. The stripped
`libxul.so` is 308,885,048 bytes. GTK continues to provide the system ATK
dependency; this group targets Gecko implementation code, not the toolkit ABI.

| Gate | Result |
|---|---|
| `mach build -j4 binaries` | PASS, zero project warnings |
| Project gtests | PASS, 49/49 |
| Focused H1/H2/H3 proxy-CONNECT xpcshell tests | PASS, 6/6 |
| Copied staged package H2/H3/Auto verification | PASS |
| `run-full-suite.sh` | PASS, 305.2 seconds |

All strict transport, padding, integrity, listener, lifecycle, pooling and
capture assertions remained unchanged and passed.

## DOM/GFX-free cold build checkpoint

The current `minimal` branch was rebuilt from an empty
`obj-naivefox-cold` directory with the lean NaiveFox mozconfig. The full
`gmake -j4` build completed successfully after one missing generated IPDL
input was supplied by the project lean closure. The final cold output passed
the runtime smoke check:

```text
NaiveFox completed successfully
```

The source-closure audit found zero compiled implementation sources from
`dom/` and zero from `gfx/`. It checked the generated unified C/C++ sources,
all compiler source operands in the clean-build logs, and object outputs under
the corresponding object-directory subtrees. `dom/bindings` still contains
generated metadata required by the build, while `gfx` has no object-directory
subtree; neither represents compiled DOM/GFX implementation code.

The cold object directory is 4.0 GiB; its opt/debug `libxul.so` is 638 MiB and
`naivefox` is 5.2 MiB. These are development-build figures, not stripped
package measurements. No staged package, documentation refresh, or additional
minimization phase was run after this checkpoint.

## DOM/GFX-free staged-runtime milestone

The reviewed lean cold build was linked again after restoring the parent-only
Necko channel path needed by the standalone client. The lean runtime now
registers its script-security manager explicitly, obtains a request-context
PID without the browser XRE service, bypasses the browser-only dictionary and
HTTP cache components, and keeps DOM-only `NS_NewChannel` overloads out of the
application graph. No DOM or GFX implementation source was reintroduced.

Staging and verification:

```bash
./netwerk/naivefox/tools/stage-runtime.sh \
  naivefox-linux-x86_64-cold-milestone2
./netwerk/naivefox/tools/verify-staged-runtime.sh \
  naivefox-linux-x86_64-cold-milestone2
```

Result: PASS. The package is 90,755,038 bytes (about 87 MiB) and contains an
81 MiB stripped `libxul.so`. Verification copied it below `/tmp`, checked the
manifest and ELF closure, ran runtime smoke and public `https://example.com/`,
tested persistent and temporary profiles (including no `HOME`/XDG state), and
passed staged config-mode H2, H3, and Auto workloads outside the object tree.

The functional gates were rerun from the same cold binary:

| Gate | Result |
|---|---|
| H2 local suite (`run-local-suite.sh`) | PASS |
| H2 config and runtime-profile tests | PASS |
| H3 local suite (`run-h3-suite.sh`) | PASS |
| H3 config, padding, robustness and Auto | PASS |
| Firefox-vs-NaiveFox H2 capture | PASS |
| Firefox-vs-NaiveFox strict H3/QUIC capture | PASS |

Capture used the separate full Firefox baseline from the pre-minimization
object directory, with its own `libxul`/NSS path; the lean NaiveFox process used
only the cold staged libraries. H3 capture proved UDP/QUIC and HTTP/3 without
TCP fallback. Raw pcaps, key logs, profiles and bodies were deleted after
sanitization; only aggregate reports remain under the ignored fixture state.

One sequential full-suite attempt observed a transient libpref parser abort at
the start of the second H3 capture pass after the first pass had completed.
Fresh per-pass profiles and separate library paths were added; the isolated
H2 and H3 suites, including both capture passes, then passed. This remains a
diagnostic transient, not an accepted failure or a weakened test gate.
