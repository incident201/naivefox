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
    "https://user:password@proxy.example:443"
  ],
  "log": ""
}
```

- `listen` is one URI or a non-empty array. `socks://` serves SOCKS5 CONNECT;
  `http://` serves HTTP CONNECT only.
- `proxy` is one URI shared by all listeners, or an array whose length matches
  `listen`. Multi-hop comma-separated proxy chains are rejected.
- `https://` requires H2 over TLS/TCP. `quic://` requires H3 over QUIC/UDP.
  Strict modes never silently fall back.
- Credentials are percent-decoded and passed to Necko's proxy-auth path. They
  are never written to normal logs.
- Listener hosts must be numeric IPv4/IPv6; `localhost` maps to IPv4 loopback.
  An explicit nonzero port is required.
- `log` absent disables runtime logging, `""` logs to the console, and a path
  appends to a mode-`0600` file.

Local listeners do not authenticate clients. Binding `0.0.0.0`, `::`, or a LAN
address intentionally exposes the listener and requires an appropriate host
firewall or trusted-network policy. Ordinary forward-proxy HTTP requests return
405.

A SOCKS client should delegate destination DNS to the proxy:

```bash
curl --socks5-hostname 127.0.0.1:1080 https://example.com/
```

Normal config mode uses a persistent profile under `XDG_STATE_HOME`, then
`$HOME/.local/state`, or the explicit `NAIVEFOX_PROFILE`. If no persistent
location is usable, it creates a private temporary profile and removes it after
an orderly shutdown. Certificate verification is never disabled.

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
`Upgrade`, or `Connection` marker. A validated sidecar adds only the intentional
Naive `padding` header to the actual proxy CONNECT request.

H2 uses Firefox's TLS/TCP connection and `Http2StreamTunnel`. H3 uses a strict
MASQUE-type proxy route to create a regular classic CONNECT through
`Http3StreamTunnel`; it does not use CONNECT-UDP or a standalone Neqo client.
Connection pooling remains owned by Necko in both modes.

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
  --objdir "$PWD/../obj-naivefox-windows" \
  --bootstrap
```

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

An ordinary Firefox build is not a merge or release gate. It is allowed only
for an explicitly requested same-base capture comparison; see
[`CAPTURE.md`](CAPTURE.md).

## Repository workflow

```text
Mozilla main -> firefox-upstream -> naivefox-full-source -> generated naivefox-minimal-source
```

- `firefox-upstream` is a clean fast-forward-only Mozilla mirror.
- `naivefox-full-source` is the single complete working tree containing the
  NaiveFox implementation, minimization rules, and export tooling.
- `naivefox-minimal-source` is a generated standalone product snapshot and is
  never hand-edited. Its `.github/workflows/` control-plane files are the
  deliberate exception and may be maintained directly.

The refresh and export gates are defined in `UPSTREAM.md` in the full
maintenance checkout. In particular, commit SHAs and test transcripts
belong in generated evidence, commits, and annotated tags rather than being
copied into active Markdown.

Release automation is intentionally maintained as the control-plane overlay
`.github/workflows/release.yml` on `naivefox-minimal-source`. It is manual-only,
builds the targets selected by that branch's release workflow, and creates a
draft release without running the integration/Caddy suites.

## Security and data handling

Never commit or retain proxy passwords, authorization headers, TLS keys, local
CA private keys, NSS profiles, packet captures, request payloads, or generated
fixture state. Integration state lives under the object directory with private
permissions and is removed after successful runs. Real-server tests receive
their endpoint and credentials through environment variables and keep only a
credential-free summary.

## References

- [Firefox source](https://github.com/mozilla-firefox/firefox)
- [Firefox Linux build documentation](https://firefox-source-docs.mozilla.org/setup/linux_build.html)
- [NaiveProxy](https://github.com/klzgrad/naiveproxy)
- [Naive padding specification](https://github.com/klzgrad/naiveproxy/blob/master/README.md#padding-protocol-an-informal-specification)
- [Naive Caddy forward proxy](https://github.com/klzgrad/forwardproxy/tree/naive)
