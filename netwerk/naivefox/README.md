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
- `preamble` is optional and defaults to `off`. `mode` remains the required
  default when the object is present; optional `h2-mode` and `h3-mode`
  override it only for that negotiated outer protocol. This allows Auto mode
  to choose a fresh policy on fallback instead of reusing the failed H3
  attempt's policy. Supported modes are `off`, `document-complete`,
  `document-carrier-dispatch`, `document-cold-winner-handoff`,
  `document-native-cache-open`, `document-native-channel-open`,
  `document-handshake-confirmed`, `document-overlap`,
  `document-start-overlap`, `tree-native-parser-document-start-overlap`,
  `tree-native-parser-document-start-navigation-stop`,
  `tree-native-parser-document-start-response-stop`,
  `tree-complete`, `tree-overlap`,
  `tree-early-overlap`, `tree-resource-native-cache-committed-overlap`, and
  `tree-root-overlap`; `root` and `tree` are
  compatibility aliases. `document-carrier-dispatch`,
  `document-cold-winner-handoff`, `document-native-cache-open`,
  `document-native-channel-open`, and
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
  `document-start-overlap` is a stricter request-scheduling experiment. Its
  root channel exposes the normal per-channel `WAITING_FOR` progress event
  only after the H2/H3 request stream has accepted and committed the GET. It
  then permits CONNECT while the response continues. Admission and final HTTP
  result are separate events; a normal 2xx root drain remains mandatory.
  `tree-native-parser-document-start-overlap` preserves that same early
  request-commit admission, then continues the root response through the lean
  HTML5 speculative scanner. Exactly one parser-discovered stylesheet opens
  through the native `FromParser` preload path while CONNECT and its tunneled
  workload are already active. It is H3-only, fail-closed, and does not add a
  timer, DOM, layout, graphics, JavaScript, or a second process. Screening
  shows a strong packets-17--32 improvement but a later volume penalty from
  the additional complete stylesheet, so it remains experimental rather than
  the default until that tradeoff is resolved.
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
  `document-native-channel-open` is the stricter cold H3 channel-lifecycle
  diagnostic. It keeps the system triggering principal, but requires Safe
  Browsing to classify the fixture URI through a non-system URI/codebase
  principal with matching origin attributes. It also requires a normal
  writable new cache2 entry and a genuine local-DB asynchronous classifier
  callback that suspends and resumes the channel. Missing services,
  `expectCallback=false`, synchronous completion, principal mismatch, cache
  reuse, and real-time classifier paths fail closed. Controlled runs use a
  fresh temporary profile; this mode does not introduce a persistent cache
  dependency.
  `tree-resource-native-cache-committed-overlap` is a screening-only H3 mode
  that keeps the root cache-inhibited, opens exactly one discovered resource
  through a normal writable Cache2 entry, and releases CONNECT only after an
  asynchronous new-entry callback and the resource's real
  `NS_NET_STATUS_WAITING_FOR` commit. Cache hits, synchronous callbacks,
  timeouts, and missing entries fail closed. It exists to test native resource
  scheduling with a fresh temporary profile; it is not a persistent-cache
  product policy or a recommended default.
  `cache-resources` is an opt-in diagnostic boolean, defaulting to `false`, and
  is accepted only when at least one effective protocol mode is a tree mode.
  It enables Gecko's ordinary HTTP cache path only for discovered resource
  channels; the root document remains cache-inhibited, as do direct requests,
  CONNECT, and every preamble under the default configuration. The cache lives
  in the run's selected profile. NaiveFox still creates a temporary profile by
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
