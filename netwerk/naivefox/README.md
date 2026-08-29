# NaiveFox

NaiveFox is a headless Naive-compatible proxy client built on Firefox's real
networking stack. Necko supplies HTTP/2, HTTP/3, CONNECT, pooling, and flow
control; NSS/PSM supplies TLS and certificate validation; Neqo supplies QUIC.
NaiveFox adds local proxy listeners, transport selection, CONNECT orchestration,
Naive padding, bounded stream pumping, configuration, and packaging.

```text
application -> SOCKS5 or HTTP CONNECT -> NaiveFox
            -> Firefox Necko/NSS/Neqo -> H2 or H3 CONNECT
            -> Caddy forwardproxy@naive -> destination
```

The server is an unmodified Naive-compatible Caddy build. NaiveFox does not
ship another HTTP/TLS stack and does not synthesize a Firefox fingerprint.

## Running the desktop product

The staged Linux package has one launcher at its root. With no argument it
reads `./config.json`; one positional argument selects another config:

```bash
./naivefox
./naivefox /absolute/path/to/config.json
```

The staged Windows package provides `run-naivefox.cmd` beside its runtime and
accepts the same optional config path.

The supported config is a strict NaiveProxy-compatible subset:

```json
{
  "listen": [
    "socks://127.0.0.1:1080",
    "http://127.0.0.1:8080"
  ],
  "proxy": [
    "https://user:password@proxy.example:443",
    "quic://proxy.example:443"
  ],
  "insecure-concurrency": 2,
  "host-resolver-rules": "MAP proxy.example 127.0.0.1",
  "extra-headers": "X-NaiveFox-Test: enabled\r\n",
  "no-post-quantum": false,
  "max-connections": 0,
  "log": ""
}
```

- `listen` is one URI or a non-empty array. `socks://` serves SOCKS5 CONNECT;
  without userinfo it uses the normal no-auth method. SOCKS credentials are
  optional, percent-decoded, and checked with RFC 1929 username/password
  authentication; `http://` serves HTTP CONNECT only and does not accept
  listener credentials.
- `proxy` is one URI shared by all listeners, or an array whose length matches
  `listen`. `https://` is strict H2 over TLS/TCP and `quic://` is strict H3
  over QUIC. Upstream credentials are optional and percent-decoded; when
  userinfo is present it uses `username:password`, so either side may be
  empty (`user:`, `:password`, or `:@`). They are passed to Necko's
  proxy-auth path. Port 443 is used when omitted.
- `insecure-concurrency` accepts a positive JSON integer or a decimal string for
  NaiveProxy/Exclave config compatibility. NaiveFox validates the value and
  ignores it; connection pooling, concurrency, and tunnel lifecycle remain
  controlled by Firefox Necko.
- `max-connections` is an optional non-negative integer, defaulting to `0`
  (unbounded). A positive value closes the listeners after that many accepted
  local connections and exits after those connections drain; a peer that does
  not complete SOCKS parsing still consumes the bound. It is useful for bounded
  tests and orderly one-shot diagnostics.
- Strict H2/H3 modes never silently fall back.
- Listener hosts must be numeric IPv4/IPv6; `localhost` maps to IPv4 loopback.
  An explicit nonzero port is required.
- `host-resolver-rules` accepts exactly one `MAP logical-host physical-host`
  rule. A matching upstream socket uses the physical host while TLS SNI and
  certificate validation retain the logical hostname.
- `extra-headers` is a CRLF-separated list added only to the outer upstream
  CONNECT request. Malformed, duplicate, or service headers such as `Host`,
  `Padding`, and `Proxy-Authorization` are rejected.
- `preamble` is optional. When it is omitted, an explicit H2 upstream behind
  SOCKS-only listeners uses the promoted `document-first-buffer-task-overlap`
  policy. HTTP-CONNECT-only and mixed listeners use
  `document-first-buffer-overlap`. An explicit H3 upstream behind SOCKS-only
  listeners uses the promoted
  `tree-native-parser-resource-committed-overlap` policy with path `/`, exactly
  six parser-discovered resources, ordinary resource caching, and a 384 KiB
  aggregate safety budget. HTTP-CONNECT-only and mixed H3 listeners retain
  `document-start-overlap` with path `/` and a 64 KiB document budget. The
  canonical H2/H3 by SOCKS5/HTTP-CONNECT residual matrix is maintained in
  [Current implicit-default matrix](CAPTURE.md#current-implicit-default-matrix);
  all four rows must be regenerated together whenever the implicit policy or
  its measurement contract changes. The
  implicit cold-route gate applies to the selected H2 or H3 upstream, so one
  established outer session does not repeat the synthetic page for every
  tunnel. An
  explicit `{"preamble":{"mode":"off"}}` is the complete opt-out, and an
  explicit `outer-session-gate` value remains authoritative: `false` runs the
  implicit protocol-specific document on every tunnel, while `true` retains
  the existing global gate semantics. An older H3 gate-only config must now
  add explicit `mode: off` to keep sending no document GET. The 64 KiB value is
  a safety cap, not a target response size. `mode` is still required when the
  preamble object is present; optional `h2-mode` and `h3-mode` override it only
  for that negotiated outer protocol. This allows Auto mode to choose a fresh
  policy on fallback instead of reusing the failed H3 attempt's policy.
  Supported modes are `off`, `document-complete`,
  `document-carrier-dispatch`, `document-cold-winner-handoff`,
  `document-native-cache-open`,
  `document-handshake-confirmed`, `document-overlap`,
  `document-first-buffer-overlap`,
  `document-first-buffer-task-overlap`,
  `document-start-overlap`, `tree-native-parser-document-start-overlap`,
  `tree-native-parser-document-start-resource-tree`,
  `tree-native-parser-resource-committed-overlap`,
  `tree-native-parser-document-start-navigation-stop`,
  `tree-native-parser-document-start-response-stop`,
  `tree-complete`, `tree-overlap`,
  `tree-early-overlap`, `tree-resource-native-cache-committed-overlap`, and
  `tree-root-overlap`; `root` and `tree` are
  compatibility aliases. `document-carrier-dispatch`,
  `document-cold-winner-handoff`, `document-native-cache-open`,
  `document-handshake-confirmed` are H3-only causal diagnostics and therefore
  must be selected explicitly through `h3-mode`; the resolved H2 mode must
  remain a different supported mode. Active
  modes share one absolute origin-form `path` and bounded `max-bytes` budget.
  `max-assets` is allowed when at least one effective protocol mode is a tree
  mode and is ignored by a document-only effective mode. Protocol overrides
  are explicit policy, not an automatic camouflage verdict. For example, an
  experimental split policy can keep H2 on a document request while using the
  two-resource root-overlap mode for H3:

  ```json
  {
    "mode": "document-complete",
    "h3-mode": "tree-root-overlap",
    "path": "/",
    "max-assets": 2,
    "max-bytes": 262144
  }
  ```
  `document-overlap` is an experimental scheduling control: after a successful
  2xx response HEADERS event it permits CONNECT while the normal document
  channel continues to completion. It never discovers assets and requires
  `max-assets=0`. Physical HEADERS/DATA/FIN overlap is deliberately not a
  success criterion because that would make the policy depend on response size
  and packetization. The current screening evidence does not make this mode a
  recommended default.
  `document-first-buffer-overlap` admits CONNECT only after the complete first
  body buffer delivered by Necko has been consumed successfully. The event is
  a channel-delivery boundary, not a byte count or fixture-size threshold; a
  short or failed read cannot release CONNECT, and normal 2xx document drain
  remains mandatory. Two independent six-block H2/inner-H2 screens with an
  HTTP CONNECT local frontend (`306a249a46d33a5c` and
  `2b8dd75c4e682940`) reproduced lower packets-17--32, 1--32, 250 ms, and
  whole-flow distances than both response-HEADERS admission and the former
  SOCKS default. Two independent six-block SOCKS screens
  (`0e3d5fc56b0e06f5` and `d98cf5d810045203`) then reproduced the
  packets-17--32 and 1--32 improvement; their combined diagnostic ranked
  first-buffer best in every view. Fresh initial, bulk-download, and
  bidirectional SOCKS controls (`b8f33cd43e0a9722`, `631ac031bb4498aa`, and
  `37207e981beba111`) each favored first-buffer over document-start at packets
  17--32 and whole flow. This established direct first-buffer as the H2
  control and retained policy for HTTP CONNECT and mixed listeners. Fresh
  30-block paired SOCKS artifact `e1a89392d921b419`
  then ranked it lower in all five fixed views: packets 17--32 improved from
  `0.47100` to `0.42560` (paired CI95 `[-0.05907,-0.03310]`, Holm
  `p=0.0005`), while whole flow was effectively tied at `0.25701` versus
  `0.25735`. Explicit preamble policy remains authoritative.
  `document-first-buffer-task-overlap` queues CONNECT admission to the next
  main-thread task after the first complete body buffer. The root channel is
  not suspended; if its terminal callback arrives synchronously, only terminal
  bookkeeping is deferred until the queued barrier, so normal network drain is
  never backpressured. In fresh 30-block paired SOCKS artifact
  `2834cb35aa391bb0`, task admission improved packets 17--32 from `0.49063` to
  `0.48016`, packets 1--32 from `0.21280` to `0.21028`, 250 ms from `0.08630`
  to `0.08036`, and whole flow from `0.26133` to `0.25077` relative to direct
  first-buffer. Packets 1--16 moved from `0.06956` to `0.07328`; its CI crossed
  zero. This promotes task admission for SOCKS-only H2 while leaving the
  ingress-tested direct policy on HTTP CONNECT and mixed listeners.
  `document-start-overlap` is a stricter request-scheduling experiment. Its
  root channel exposes the normal per-channel `WAITING_FOR` progress event
  only after the H2/H3 request stream has accepted and committed the GET. It
  then permits CONNECT while the response continues. Admission and final HTTP
  result are separate events; a normal 2xx root drain remains mandatory.
  Same-base 30-block acceptance artifact `7b5c70011f0fba08` compared explicit
  `off` and this mode against paired Firefox A/B controls with inner HTTPS/H2.
  It improved packets 1--16 (`0.16459` to `0.13442`), packets 17--32
  (`0.76117` to `0.65828`), packets 1--32 (`0.26499` to `0.22720`), and the
  250 ms view (`0.14026` to `0.12081`); no whole-flow regression was detected
  (`0.38926` to `0.38660`, with a paired interval crossing zero). It remains
  the H3 policy for HTTP CONNECT and mixed listeners. A final
  six-block H2 screen against the bounded resource-tree candidate retained the
  lower distance for this mode in packets 17--32, packets 1--32, 250 ms, and
  whole-flow views, while packets 1--16 were effectively tied.
  `tree-native-parser-resource-committed-overlap` is the promoted SOCKS-only
  H3 policy. Its lean parser accepts exactly one same-origin stylesheet, one
  classic deferred script, and four images. CSS and script open from the
  parser callback; the four prepared images open together on the next ordinary
  main-thread turn. CONNECT is admitted only after all six native H3 resource
  transactions have committed and one complete valid resource body buffer has
  been consumed. This boundary contains no fixed pause, byte threshold, packet
  count, resource-size target, RTT, or bandwidth value. It fails closed if the
  page does not meet the exact bounded resource contract or any required
  request or response fails. Four-block shaped artifact `390cc24ccb6ef8c9`
  measured `0.11745/0.29471/0.15293/0.16835/0.34363` for packets 1--16,
  packets 17--32, packets 1--32, 250 ms, and whole flow. Separate four-block
  runs retained the improvement with 64 KiB and 1 MiB page bases and at 50 ms
  one-way delay with 5 Mbit/s bandwidth. Unshaped localhost retained the
  whole-flow gain. These robustness checks promote the event-driven mode for
  SOCKS-only H3; explicit preamble configuration remains authoritative.
  `tree-native-parser-document-start-overlap` preserves that same early
  request-commit admission, then continues the root response through the lean
  HTML5 speculative scanner. Exactly one parser-discovered stylesheet opens
  through the native `FromParser` preload path while CONNECT and its tunneled
  workload are already active. It is fail-closed and does not add a
  timer, DOM, layout, graphics, JavaScript, or a second process. Screening
  shows a strong packets-17--32 improvement but a later volume penalty from
  the additional complete stylesheet, so it remains experimental rather than
  the default until that tradeoff is resolved.
  `tree-native-parser-document-start-resource-tree` is the final bounded H2
  fronting-page experiment. It preserves early document-start admission and
  then accepts, in source order, one same-origin stylesheet, one classic
  deferred script, and one image from the lean HTML5 speculative scanner. Each
  resource uses a native Necko preload channel with upstream referrer, Fetch
  Metadata, priority, image `Accept`, Cache2, and normal stream completion. The
  fixture uses a fixed small page (12 KiB CSS, 24 KiB JS, and 8 KiB SVG); these
  are semantic fixture sizes, not packet-index targets. A fresh decrypted run
  proved `root GET -> CONNECT -> resource GETs` on one H2 TLS connection with
  request semantics matching same-base Firefox. The final paired screen still
  made packets 17--32, packets 1--32, 250 ms, and whole flow worse than
  `document-start-overlap`: the resource burst moved the residual and added
  roughly 47 KiB of early server traffic. The mode remains available for
  controlled research but is rejected as a product default.
  `tree-native-parser-document-start-navigation-stop` tests the corresponding
  upstream cancellation tradeoff. The synthetic root and stylesheet share a
  scoped load group which excludes CONNECT. After CONNECT is admitted, positive
  client-to-target tunnel data is observed, and the stylesheet has received
  successful 2xx response headers, the scoped synthetic navigation is stopped
  with the normal `NS_BINDING_ABORTED` load-group path. This preserves a real
  early stylesheet response burst but necessarily emits H3 request-cancel
  signaling when the response has not reached FIN. Six-block screening improved
  packets 1--32, but remained worse than `document-start-overlap` at 250 ms and
  whole-flow. The mode is therefore a negative product experiment and is not a
  recommended default.
  `tree-native-parser-document-start-response-stop` moves that cancellation
  predicate from client-to-target data to the first positive decoded
  target-to-client tunnel payload. If the stylesheet is still active, the same
  scoped load group issues a normal `NS_BINDING_ABORTED`; if it has already
  finished, natural completion is a valid product outcome and the tunnel is
  never failed. Safe metadata records abort and natural-completion counts
  separately. A bounded background-drain timeout also leaves the working
  tunnel intact, but controlled captures reject it as incomplete lifecycle
  evidence. In six-block same-base H3 screening only one of six samples
  canceled while five completed naturally. The arm was best at packets 17--32
  and 1--32, but remained worse than `document-start-overlap` at 250 ms and
  whole-flow. It therefore remains an experimental negative product result,
  not a default.
  `document-carrier-dispatch` uses one request-less Gecko
  `SpeculativeTransaction` to establish the first cold outer H3 session. The
  real document remains pending until the carrier's normal zero-byte
  `ReadSegments`/`Close` lifecycle completes, then returns through the ordinary
  connection-manager dispatch onto that same session. The carrier has an
  explicit one-connection limit so profiles may continue to disable general
  speculative preconnects.
  The mode does not enable Happy Eyeballs, use transaction swapping, wait for
  QUIC confirmation, or change proxy fallback policy. If normal dispatch does
  not select the carrier-established connection, the transaction fails closed.
  Same-base screening found this drain fence worse than `document-complete` in
  every measured view, so it remains a negative diagnostic rather than a
  recommended default.
  `document-cold-winner-handoff` is a narrower H3-only reconstruction of the
  ordinary cold Firefox winner lifecycle. The real document first enters the
  normal pending queue; one request-less proxy-aware H3 carrier owns
  establishment while the connection remains `IsRacing`, and the existing
  asynchronous activation callback dispatches that exact document onto the
  exact winner before publishing it. It does not start a speculative
  preconnect, use the Rust address race, enable 0-RTT, wait for confirmation,
  swap transactions, or change proxy fallback. Every failure is terminal for
  this single candidate and releases the real transaction without feeding an
  artificial result into the Rust race machine. Same-base decrypted and
  passive screening left the first GET at packet 10 and did not improve the
  overall distance, so this remains a falsified causal diagnostic rather than
  a default.
  `document-native-cache-open` is a cold H3-only diagnostic that restores the
  native asynchronous cache2 phase removed by the lean preamble shortcut. It
  preserves `INHIBIT_CACHING`, requires an `OPEN_READONLY` miss before normal
  network dispatch, and never writes the response cache. Synchronous callbacks,
  hits, timeouts, and other cache outcomes fail before the document GET.
  The falsified `document-native-channel-open` diagnostic was retired from
  product configuration. Its real local Safe Browsing DB path did not improve
  the passive screen and pulled protobuf plus Abseil into the lean link graph;
  retaining that browser subsystem would violate the minimal-runtime boundary.
  `tree-resource-native-cache-committed-overlap` is a screening-only H3 mode
  that keeps the root cache-inhibited, opens exactly one discovered resource
  through a normal writable Cache2 entry, and releases CONNECT only after an
  asynchronous new-entry callback and the resource's real
  `NS_NET_STATUS_WAITING_FOR` commit. Cache hits, synchronous callbacks,
  timeouts, and missing entries fail closed. It exists to test native resource
  scheduling with a fresh temporary profile; it is not a persistent-cache
  product policy or a recommended default.
  `cache-resources` is an explicit-config diagnostic boolean, defaulting to
  `false`, and is accepted only when at least one effective protocol mode is a
  tree mode. The promoted implicit SOCKS-only H3 policy enables it for its six
  resource channels.
  It enables Gecko's ordinary HTTP cache path only for discovered resource
  channels; the root document remains cache-inhibited, as do direct requests,
  CONNECT, and document-only preambles. The cache lives in the run's selected
  profile. NaiveFox still creates a temporary profile by
  default, so this mechanism is useful for controlled repeated loads within a
  process and does not introduce a persistent-profile product dependency.
- `no-post-quantum` is a boolean, defaulting to `false`; when true it disables
  Firefox Kyber/ML-KEM TLS and HTTP/3 key shares before connecting.
- `log` absent disables runtime logging, `""` logs to the console, and a path
  appends to a mode-`0600` file.
- `SSL_CERT_FILE` is an environment variable, not a JSON field. When set to an
  absolute PEM path, its certificates become additional TLS trust anchors for
  the current run only. The normal Firefox/NSS roots and certificate checks
  remain active. An empty, relative, unreadable, or malformed file fails
  startup. For example:

  ```bash
  SSL_CERT_FILE=/absolute/path/private-root.pem ./naivefox config.json
  ```

  The same process environment is honored by the Android embedded entry point;
  its caller-provided profile remains host-owned. The CA certificates are
  trusted through NSS's temporary certificate context and are not imported as
  persistent profile certificates. When a configured CA is not a built-in
  Firefox root, NaiveFox temporarily permits strict H3 to use that trust
  anchor and restores Firefox's original preference during shutdown.

Binding `0.0.0.0`, `::`, or a LAN address intentionally exposes a listener.
Ordinary forward-proxy HTTP requests return 405. Comma-separated proxy chains,
upstream HTTP/SOCKS proxies, and direct mode are not supported.

A SOCKS client should delegate destination DNS to the proxy:

```bash
curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

Normal config mode creates a private temporary profile for every run and removes
it after an orderly shutdown. Set `NAIVEFOX_PROFILE` explicitly when NSS
databases or other profile state must persist across restarts. Existing profiles
under `XDG_STATE_HOME` or `$HOME/.local/state` are not selected implicitly.
Certificate verification is never disabled.

Developer-only modes provide focused diagnostics:

```bash
naivefox --profile /path/to/profile --runtime-smoke
naivefox --profile /path/to/profile --fetch https://example.com/
naivefox --profile /path/to/profile \
  --raw-tunnel-smoke https://proxy.example:443 target.example:80
naivefox --profile /path/to/profile \
  --socks-listen 127.0.0.1:1080 \
  --proxy https://proxy.example:443 --protocol h2
```

Developer CLI modes take proxy credentials from `NAIVEFOX_PROXY_USER` and
`NAIVEFOX_PROXY_PASS`. `--protocol h2` is the default; `h3` is strict H3;
`auto` performs one strict H3 attempt and permits one H2 retry only when H3
fails before a CONNECT response or tunnel transport exists.

## Android embedded runtime

Android ARM64 is a native SDK/runtime target for embedding the same NaiveFox
core in another process. Build and stage it with the common product entrypoint:

```bash
./netwerk/naivefox/tools/build-product.sh android \
  --objdir "$PWD/../obj-naivefox-android-aarch64"

NAIVEFOX_OBJDIR="$PWD/../obj-naivefox-android-aarch64" \
./netwerk/naivefox/tools/verify-staged-android-runtime.sh \
  "$PWD/../obj-naivefox-android-aarch64/package/naivefox-android-aarch64"
```

The build remains `--enable-project=netwerk/naivefox` and cross-compiles for
`aarch64-linux-android`. The staged, relocatable package is:

```text
naivefox-android-aarch64/
  include/NaiveFoxAPI.h
  lib/arm64-v8a/
    libxul.so
    dependent native and NSS libraries selected from the build output
  manifest.json
```

The manifest records the target, minimum Android API, runtime path, exact file
hashes, Android system dependencies, and four exported `NaiveFox*` symbols.
The host must make `lib/arm64-v8a` and its dependent libraries visible in its
Android linker namespace. It then includes `NaiveFoxAPI.h`, starts the blocking
runner on one host worker thread, and stops it from another thread:

```c
int status = NaiveFoxRunEmbedded(config_json, writable_profile_dir,
                                 runtime_lib_dir);

/* From another host thread while the call above is running. */
NaiveFoxRequestStop();
```

`config_json` is the same UTF-8 JSON contract documented above, not a file
name. `writable_profile_dir` must be an existing writable directory, and
`runtime_lib_dir` is the absolute `lib/arm64-v8a` directory containing
`libxul.so`. `NaiveFoxVersion()` returns the product version. Return values are
the `NaiveFoxStatus` constants in the public header. The first successful Gecko
startup consumes the process-wide runtime; restarting it in the same process is
not supported.

This target contains no JNI or Java/Kotlin API, Gradle project, GeckoView,
Firefox Android application, Android manifest, UI, service, `VpnService`, TUN,
or tun2socks layer. Those are downstream integration concerns; the public
boundary here is the small native C ABI plus the local SOCKS5/HTTP CONNECT
listeners.

## Supported behavior

- SOCKS5 CONNECT with IPv4, IPv6, and domain destinations; domain names remain
  unresolved in the upstream CONNECT authority.
- HTTP CONNECT local frontend, including bytes received with the request
  headers.
- Multiple listeners in one process, sharing one Gecko runtime and Necko
  connection manager.
- Strict H2 and strict H3 regular CONNECT, plus bounded developer Auto mode.
- Basic proxy authentication through Firefox's normal proxy-auth machinery.
- RFC 1929 authentication for configured SOCKS listeners.
- Upstream host mapping, custom CONNECT headers, and the no-post-quantum TLS
  preference.
- Additive process-local CA trust from an absolute PEM path in `SSL_CERT_FILE`.
- Naive `padding` request/response negotiation and legacy Variant 1 payload
  framing: eight padded records per direction, then raw bytes.
- Bounded async pumping, partial I/O, backpressure, half-close, connection reuse,
  and coordinated shutdown.
- Relocatable Linux x86-64 and Windows x86-64 packages, plus an Android ARM64
  embedded native runtime, from the minimized product graph.

NaiveFox deliberately does not implement SOCKS BIND or UDP ASSOCIATE,
CONNECT-UDP, MASQUE, WebTransport, transparent proxying, TUN/TAP, a GUI, or a
separate socket-process transport. See [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md).

## Architecture and wire compatibility

NaiveFox asks Firefox to create a normal proxied channel and takes over the
successful classic CONNECT as asynchronous byte streams. The empty raw-upgrade
token is restricted to connect-only channels and emits no synthetic `ALPN`,
`Upgrade`, or `Connection` marker. A validated sidecar adds the intentional
Naive `padding` header and configured extra headers to the actual proxy CONNECT
request.

H2 uses Firefox's TLS/TCP connection and `Http2StreamTunnel`. H3 uses a strict
MASQUE-type proxy route to create a regular classic CONNECT through
`Http3StreamTunnel`; it does not use CONNECT-UDP or a standalone Neqo client.
Connection pooling remains owned by Necko in both modes.

NaiveFox's H3 route suppresses only Firefox's automatic PMTUD force for an
outer H3 proxy connection. The normal `network.http.http3.pmtud` preference
still applies, so an explicit global enable retains Firefox's existing PMTUD
behavior. This does not change H3 proxy identity, TLS, pooling, CONNECT, or
strict fallback behavior.

The target's TLS session belongs to the application using the local proxy.
NaiveFox's outer TLS/QUIC session terminates at the upstream proxy.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for component, event-target, stream,
and fallback details. Downstream Firefox hooks are inventoried in
`UPSTREAM-PATCHES.md` in the full maintenance checkout.

## Building and testing

Development happens in the full Firefox source checkout, but the normal
product workflow builds only the minimized NaiveFox graph. It does not build a
Firefox browser:

```bash
./netwerk/naivefox/tools/build-product.sh linux \
  --objdir "$PWD/../obj-naivefox-linux"
```

The same entrypoint selects the Windows x86-64 mozconfig, external object
directory, staging script, and (under WSL) the portable Wine paths/prefix:

```bash
./netwerk/naivefox/tools/build-product.sh windows \
  --objdir "$PWD/../obj-naivefox-windows"
```

The product build command intentionally omits `--bootstrap`, so it also works
from a generated git-less minimal-source export. Run `--bootstrap` only from
the full Firefox Git checkout when Mozilla build dependencies need to be
installed; bootstrapping is not part of an export build.

For a local WSL/Windows ARM64 AVD, pass --start-emulator to the same runner;
it owns the QEMU virt launch workaround and cleans up the emulator it starts.

The Android ARM64 command and package verifier are documented in
[Android embedded runtime](#android-embedded-runtime). A static NDK harness
check, which does not require a device, is available after staging:

```bash
./netwerk/naivefox/test/integration/run-android-embedded-tests.sh \
  --package "$PWD/../obj-naivefox-android-aarch64/package/naivefox-android-aarch64" \
  --check-only
```

Together, the verifier and `--check-only` prove the package manifest,
dependency/export metadata, AArch64 harness construction, and ELF inspection
only. Android acceptance still requires an online ARM64 API-26+ device or
emulator and the same runner without `--check-only`, so a host with no `adb`
device or KVM must not report the H2/H3 device gate as passed.

Run the reproducible local H2/H3/Auto/config/robustness gate with:

```bash
./netwerk/naivefox/test/integration/run-full-suite.sh
```

The fixture builds pinned Caddy and `forwardproxy@naive` inputs, binds only to
loopback, creates per-run credentials and PKI state, and trusts its CA only in
isolated NSS profiles. No real proxy account is required. Detailed focused and
real-deployment commands are in
[`test/integration/README.md`](test/integration/README.md).

The entrypoint stages the package below the object directory. Verify the Linux
package after a successful product build:

```bash
NAIVEFOX_OBJDIR="$PWD/../obj-naivefox-linux" \
./netwerk/naivefox/tools/verify-staged-runtime.sh \
  package/naivefox-linux-x86_64
```

The entrypoint disables the local sccache daemon by default so a build does
not depend on stale daemon state or silently change its configure inputs. Set
`NAIVEFOX_USE_SCCACHE=1` only when the daemon has been deliberately configured
for this checkout.

The product graph disables SpiderMonkey's ECMAScript `Intl` API and does not
build ICU4C. The retained locale parser, IDNA/Unicode properties, and text
segmentation use the existing ICU4X Rust data and Necko's normal helpers. This
keeps the networking/runtime behavior needed by NaiveFox while avoiding the
browser's formatting, collation, and other ICU4C-only components; code that
requires `DateTimeFormat`, `Collator`, or the JavaScript `Intl` API is outside
the product scope.

An ordinary Firefox build is not a merge or release gate. It is allowed only
for an explicitly requested same-base capture comparison; see
[`CAPTURE.md`](CAPTURE.md).

## References

- [Firefox source](https://github.com/mozilla-firefox/firefox)
- [Firefox Linux build documentation](https://firefox-source-docs.mozilla.org/setup/linux_build.html)
- [NaiveProxy](https://github.com/klzgrad/naiveproxy)
- [Naive padding specification](https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification)
- [Naive Caddy forward proxy](https://github.com/klzgrad/forwardproxy/tree/naive)
