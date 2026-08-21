# Firefox wire-behavior diagnostics

Capture comparison checks that NaiveFox continues to use Firefox's Necko,
NSS/PSM, and Neqo wire machinery without accidental project-specific markers.
It is diagnostic: a browser GET and padded proxy CONNECT are different
workloads, so packet timing and volume are not fingerprint-equality targets.

## Modes and policy

The runners support two reference modes:

- `quick` (default) downloads the SHA-256-pinned Firefox reference declared by
  the tooling. It does not build Firefox and may be used in a lightweight
  product suite. Version differences are reported rather than treated as exact
  parity.
- `same-base` uses caller-supplied Firefox and NaiveFox packages built from the
  same Firefox base. It is the only meaningful exact stack comparison and the
  only mode that may require a Firefox browser build.

An ordinary Firefox build is allowed only when the same-base diagnostic is
explicitly requested. It is outside the upstream, minimized-product, and export
gates and is never a merge or release prerequisite.

Run the H2, H3, and passive comparisons from the integration directory:

```bash
./run-capture-comparison.sh
./run-h3-capture-comparison.sh
./run-observer-comparison.sh
```

For same-base mode, provide the reference paths required by the runner:

```bash
NAIVEFOX_CAPTURE_MODE=same-base \
NAIVEFOX_CAPTURE_REFERENCE_BIN=/path/to/firefox \
NAIVEFOX_CAPTURE_REFERENCE_LIBDIR=/path/to/firefox-package \
NAIVEFOX_CAPTURE_REFERENCE_OBJDIR=/path/to/firefox-objdir \
./run-capture-comparison.sh
```

The H3 runner accepts the same selection variables. Keep the Firefox and
NaiveFox packages and object directories isolated.

## Capture prerequisites

The host needs `dumpcap` and `tshark` plus loopback capture permission. Grant
only the normal Wireshark capabilities if required:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip "$(command -v dumpcap)"
```

The fixture binds to loopback. H2 capture filters the proxy TCP port. H3 capture
filters UDP and TCP at the strict H3 proxy port so both the intended QUIC flow
and any forbidden TCP fallback are visible.

On WSL, the `any` interface can expose cooked transmit and receive copies of a
loopback packet. Before stateful QUIC dissection, retain the transmit copy
(`sll.pkttype == 4`) so duplicate packet numbers do not corrupt Wireshark's key
phase or QPACK tracking.

## Decrypted internal audit

Independent private NSS key logs allow `tshark` to inspect encrypted protocol
state without replacing the client TLS/H2/H3 stack.

For H2, compare:

- same endpoint/SNI and selected `h2`;
- semantic ClientHello ciphers, extensions, groups, signatures, and versions;
- HTTP/2 SETTINGS and early SETTINGS/WINDOW_UPDATE/HEADERS ordering;
- multiple CONNECT stream IDs on one outer connection;
- `padding` request/response header names;
- absence of synthetic `alpn`, `upgrade`, and `connection` request headers.

For H3, compare:

- QUIC version and negotiated `h3`;
- semantic ClientHello and client transport parameters;
- H3/QPACK settings;
- classic CONNECT rather than CONNECT-UDP or extended CONNECT;
- multiple CONNECT streams on one QUIC connection;
- no established TCP fallback at the strict H3 endpoint;
- padding negotiation and absence of synthetic markers.

Do not require equality for connection IDs, random values, GREASE values, or
TLS extension order. NSS may independently randomize extension order. In quick
mode, record version-dependent differences instead of presenting them as a
same-source failure.

## Passive observer audit

The passive runner explicitly removes `SSLKEYLOGFILE`. It may retain only
packet direction, transport length, QUIC long-header/version metadata, coarse
handshake ordering, and other facts visible without private keys. QUIC Initial
protection is publicly derivable, so semantic ClientHello and transport
parameters may still be inspected; HTTP/3 headers and 1-RTT plaintext may not.

Packet counts, lengths, timing, and TCP probes are recorded, not normalized or
treated as equality requirements. Any established TCP transport or TCP payload
in a strict-H3 NaiveFox case is a failure.

## Sensitive data handling

Raw packet captures, NSS key logs, copied profiles, screenshots, bodies, and
process logs are sensitive. Runners create them with private permissions below
the ignored object-directory fixture state. On success, they retain only safe
aggregates and delete private inputs. On failure, they print the private path
for local diagnosis; those files must never be committed or shared blindly.

Safe summaries may contain protocol identifiers, setting values, frame types,
stream identifiers, packet counts/length aggregates, hashes of build artifacts,
and header names. They must not contain credentials, `Proxy-Authorization`,
header values, TLS secrets, DATA payload, target bodies, or private profile
material.
