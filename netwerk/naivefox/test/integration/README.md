# Local integration fixture

The fixture provides reproducible H2 and H3 testing without a real proxy
account. It builds pinned Caddy plus `forwardproxy@naive`, creates an isolated
per-run PKI and trusted/untrusted NSS profiles, starts deterministic HTTP/HTTPS
targets, and binds all fixture services to dynamically selected loopback ports.

Generated binaries, credentials, private keys, profiles, bodies, logs, and
captures live under `<objdir>/naivefox-fixture/`; none belongs in Git.

## Complete local gate

From the repository root, run:

```bash
./netwerk/naivefox/test/integration/run-full-suite.sh
```

The command runs H2, H3, Auto, config/listener, failure-path, padding,
integrity, backpressure, lifecycle, and quick capture checks sequentially with
the same NaiveFox binary. Strict H3 uses a UDP-only Caddy proxy port with no TCP
listener, and therefore cannot pass by falling back to H2.

Protocol-only aggregate suites are:

```bash
./netwerk/naivefox/test/integration/run-local-suite.sh
./netwerk/naivefox/test/integration/run-h3-suite.sh
```

The H2 aggregate includes control, scoped trust, raw CONNECT, SOCKS, padding,
robustness, and H2 capture. The H3 aggregate includes raw CONNECT, SOCKS,
padding, robustness, Auto, and H3 capture. Config/runtime behavior is added by
`run-full-suite.sh`.

## Focused gates

Run the smallest relevant script while developing, then finish with the
applicable aggregate:

| Behavior | Commands |
|---|---|
| Fixture module, auth, ACL, TLS, profiles | `run-control-tests.sh` |
| Necko/NSS trust and hostname validation | `run-necko-tests.sh` |
| Marker-free raw CONNECT | `run-raw-connect-tests.sh`, `run-h3-raw-connect-tests.sh` |
| SOCKS remote DNS and tunnel shutdown | `run-socks-tests.sh`, `run-h3-socks-tests.sh` |
| Padding negotiation and transfer integrity | `run-padded-tests.sh`, `run-h3-padded-tests.sh` |
| Backpressure, half-close, loss, concurrency | `run-robustness-tests.sh`, `run-h3-robustness-tests.sh` |
| Strict H3 and establishment-only H2 retry | `run-auto-protocol-tests.sh` |
| Simultaneous SOCKS/HTTP config listeners | `run-h2-config-tests.sh`, `run-h3-config-tests.sh` |
| Profile and logging policy | `run-config-runtime-behavior-tests.sh` |
| Malformed local requests | `run-malformed-socks-tests.sh` where present |

Robustness covers deterministic large uploads/downloads, bounded slow
producer/consumer behavior, request half-close followed by response, local and
target close, authentication/ACL/timeout failures, proxy loss, and concurrent
CONNECT streams. The concurrency checks require one pooled outer TCP connection
for H2 or one NaiveFox-owned UDP socket for H3.

## Staged runtime

After staging the Linux product, the verifier runs the package from outside the
source and object directories:

```bash
./netwerk/naivefox/tools/stage-runtime.sh naivefox-linux-x86_64
./netwerk/naivefox/tools/verify-staged-runtime.sh naivefox-linux-x86_64
```

Focused config/padding runners accept a staged launcher through
`NAIVEFOX_RUNTIME`; the verifier also supplies `NAIVEFOX_EXPECT_RUNTIME_DIR` and
rejects source/object-directory mappings. It exercises both adjacent
no-argument `config.json` and an explicit positional config with inherited
loader and TLS-keylog variables removed.

Windows packages are checked with the staged Windows verifier and native
PowerShell/Python smoke/soak tooling on the `naivefox-full-source` branch. The native gate
must include valid CONNECT churn and repeated channel-stop/lifecycle activity;
a launch-only check is insufficient.

When the host has the supported naivefox-arm64-api27-raw AVD installed but no
device is running, append --start-emulator. The runner invokes
tools/start-android-emulator.sh, supplies the ARM64-safe -qemu -machine virt
launch override, waits for boot completion, and shuts down only the emulator
instance it started during cleanup.

The Android ARM64 embedded package has a test-only native harness and emulator
gate. It compiles the harness with NDK r29, relocates the package below
`/data/local/tmp`, loads `libxul.so` through the public C ABI, and verifies H2
and strict H3 through both local frontends:

```bash
NAIVEFOX_OBJDIR=/absolute/path/to/obj-naivefox-android-aarch64 \
./netwerk/naivefox/test/integration/run-android-embedded-tests.sh
```

H2 reaches the loopback fixture through `adb reverse`. H3 performs a UDP
preflight and reaches the same loopback-only fixture through the emulator host
alias `10.0.2.2`; set `NAIVEFOX_ANDROID_HOST_ALIAS` for an equivalent CI
network. The fixture certificate gains that IP SAN only for this test, and the
trusted CA remains confined to the pushed test profile. The gate checks
download/upload integrity, an active connection during cross-thread stop,
listener closure, runner return, and crash-free XPCOM shutdown. It fails when
no ARM64 API-26+ device is available. `--allow-skip-device` explicitly permits
a non-acceptance static run, while `--check-only` only builds and inspects the
harness.

## Real Caddy interoperability

Run real-deployment checks only after the local gate passes. Supply secrets
through the environment; never put them on the command line, in a committed
config, or in logs:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
./netwerk/naivefox/test/integration/run-real-server-tests.sh
```

Config-mode verification for a staged runtime is:

```bash
NAIVEFOX_REAL_PROXY_URL=https://proxy.example:443 \
NAIVEFOX_REAL_PROXY_USER=user \
NAIVEFOX_REAL_PROXY_PASS=secret \
NAIVEFOX_REAL_RUNTIME=/absolute/path/to/staged/naivefox \
./netwerk/naivefox/test/integration/run-real-server-config-tests.sh h2

# Replace h2 with h3 for a strict quic:// config.
```

Bounded stability gates are available as `run-real-server-soak.sh` and
`run-real-server-h3-soak.sh`. They preserve public certificate validation,
exercise integrity and parallel requests in one process, and retain only a
credential-free summary.

`../../tools/fetch-naiveproxy-reference.sh` downloads and verifies the pinned
official client. `run-reference-server-tests.sh` applies the comparable bounded
workload; this is a behavioral reference, not a target for copying
Chromium-specific wire shaping.

## Capture diagnostics

The capture commands are:

```bash
./run-capture-comparison.sh
./run-h3-capture-comparison.sh
./run-observer-comparison.sh
```

Their default quick mode does not build Firefox. A full ordinary Firefox build
is permitted only for an explicitly requested same-base diagnostic and is never
part of the product gates. Requirements, environment variables, comparison
semantics, WSL packet handling, and sensitive-data rules are in
[`../../CAPTURE.md`](../../CAPTURE.md).

Optional throughput scripts (`run-throughput-benchmark.sh` and
`run-h3-throughput-benchmark.sh`) produce local diagnostics; their point-in-time
numbers are not maintained in active documentation.

## Interactive fixture lifecycle

For manual debugging:

```bash
./start.sh
run_dir=$(<../../../../obj-*/naivefox-fixture/active-run)
source "$run_dir/fixture.env"
./stop.sh
```

When more than one object directory exists, use the exact active-run path
printed by `start.sh` instead of the glob. The environment file is mode `0600`
because it contains per-run credentials. `stop.sh` stops only fixture-owned
processes, removes private run state, and may retain sanitized diagnostics.

The control client uses `curl --proxy-cacert` for outer proxy TLS and `--cacert`
for an HTTPS target. The fixture never invokes `caddy trust`, modifies system or
normal Firefox trust, installs a service, uses `-k`, accepts arbitrary
certificates, or exposes an open proxy.

## Pinned inputs and first run

`versions.env` is the source of truth for exact Caddy, xcaddy, Go archive/hash,
and immutable `forwardproxy@naive` revisions. The first run may download the
pinned Go toolchain and build the fixture Caddy; later runs reuse only validated
artifacts. Setup verifies the module list, adapted Caddy configuration,
loopback listener, protocol mode, authentication, ACL, certificates, and both
NSS profile contents before product traffic is accepted.

Every runner installs cleanup traps. Successful runs delete sensitive state;
failed runs print its private ignored path for diagnosis.
