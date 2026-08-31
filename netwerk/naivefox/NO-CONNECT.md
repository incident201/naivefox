# Native no-connect transport

`classic` remains the default transport. It uses ordinary Naive-compatible
CONNECT with Caddy `forwardproxy@naive`. `no-connect` is an explicit opt-in to
an application protocol carried by ordinary HTTPS GET/POST requests. The same
lean NaiveFox executable supports both; `https://` and `quic://` independently
select strict H2 and H3.

`no-connect-hybrid` is a separate experimental opt-in. Its finite startup uses
the selected H2 or H3 route, then a native Firefox WebSocket over TLS/TCP takes
over the same NFC1 session. This is explicitly a mixed protocol policy for an
H3 startup, not WebSocket over QUIC or an implicit fallback. The original
`no-connect` mode retains its strict H2/H3 finite-exchange behavior.

The server implementation is maintained in the separate
[naivefox-transport repository](https://github.com/incident201/naivefox-transport).
Its registered Caddy module is `http.handlers.naivefox_transport`.

## Configuration and server ownership

```json
{
  "listen": ["socks://127.0.0.1:1080", "http://127.0.0.1:8080"],
  "proxy": "quic://user:password@proxy.example:443"
}
```

Launch this private file with `./naivefox /absolute/path/to/config.json` to
use classic by default. Select no-connect with
`./naivefox /absolute/path/to/config.json --transport no-connect`, and switch
back with `./naivefox --transport=classic /absolute/path/to/config.json`.
The option may precede or follow the path; omission of the path reads
`./config.json`. JSON `"transport":"no-connect"` selects the same mode.
Credentials never select a transport implicitly.

Both modes authenticate using the same percent-decoded username and password
from `proxy`. No-connect carries their Basic authentication value inside a
TLS-protected application AUTH frame; classic uses normal proxy authentication.
There is no separate client key, server key or no-connect target allowlist.
Keep credentials in the private config, not command-line authentication flags,
query parameters or logs. JSON values do not expand environment variables.

The removed `no-connect-key` field is rejected with a migration error, even
when classic is selected. Delete that field and retain the existing forward-proxy
credentials in the URI. Upgrade older key-based servers together with clients;
the native carrier rejects servers that do not advertise the shared Basic
authentication contract.

Valid classic preambles, extra CONNECT headers, outer-session gates and classic
diagnostic settings are inactive when no-connect is selected. Their values are
still parsed strictly, and switching back to classic reapplies the unchanged
configuration. No-connect never sends those extra CONNECT headers or emits an
implicit classic preamble. Local SOCKS authentication, listener mapping,
`host-resolver-rules`, certificate trust and `max-connections` keep their usual
meaning. Missing or explicitly empty URI credentials retain classic parsing
semantics; authentication acceptance belongs to the server. There is no
automatic downgrade to classic.

Build Caddy with both modules using the server repository's
[build and configuration instructions](https://github.com/incident201/naivefox-transport#readme)
and [combined Caddyfile example](https://github.com/incident201/naivefox-transport/blob/main/examples/Caddyfile).
Configure `forward_proxy` authentication and its normal `hosts`, `ports` and
`acl` policy once. The transport module uses that same forward-proxy authority
for authentication and destination access, while preserving valid TLS
certificates and the `continuous-bulk-pipeline` profile. An arbitrary static
website or a Caddy binary with only forwardproxy cannot serve no-connect.

The module owns the server SPA/assets and protocol endpoints. Native clients
consume that HTTP contract without executing or rendering the SPA. Match the
module and client authentication contract and profile during upgrades.

## Exact port boundary

The experimental branch contained a full Firefox SPA worker controlled by a
Python capture harness and a Go loopback WSS bridge. Those components were
measurement infrastructure and a prototype frontend; they are not runtime
dependencies of native no-connect.

| Retained in the native product | Kept outside the native product |
| --- | --- |
| NFC1 cells and stream open/data/FIN/reset/credit/authentication messages | Firefox browser process, WebDriver and Selenium |
| Bounded sequence validation, credits, queues and stream lifecycle | SpiderMonkey, script execution, DOM, canvas, layout and GFX |
| Selected `continuous-bulk-pipeline` startup/active/idle protocol state | Historical profile variants, animation scheduling and benchmark corpus |
| Ordinary native Necko HTTP channels, NSS/PSM trust and NSS random filler | Local WSS bridge, a second HTTP/TLS/QUIC stack and manual wire framing |
| Existing SOCKS5/HTTP CONNECT frontends and shared headless runtime | Full-source Firefox browser build or browser package |

Application cells sit inside normal HTTP bodies. Necko still owns H2/H3
streams, pooling, flow control and packetization; NSS/PSM and Neqo still own
TLS and QUIC. The client never turns a local SOCKS target hostname into a local
DNS query. The authenticated server applies the shared forward-proxy access policy
before opening the target connection.

The protocol preserves finite declared body capacities, ordinary HTTP
completion, ordered application cells and per-stream credit. A partial response
or invalid body cannot be accepted as successful completion. Target errors,
invalid authentication, malformed cells, unexpected sequence offsets and
transport failure must release the local stream with an error rather than replaying data
or falling back to another transport.

## Selected protocol and limits

The selected profile is `continuous-bulk-pipeline`. It retains a fixed
20-pair startup with 4-KiB upload cells and staged response capacities,
four-slot interactive/upload/mixed leases, and bounded bulk exchanges. Bulk
uses 16-KiB uploads and 256-KiB responses, with at most two ordered responses
in flight. Idle uses one finite 512-byte long-poll response, held for at most
30 seconds, with an explicit upload to wake local activity.

Each carrier multiplexes up to 32 logical streams; additional connections can
use additional carriers, so 32 is not a client-wide connection limit.
Per-stream receive credit stays bounded at 512 KiB. Byte offsets wrap modulo
2^32, with exact expected-offset checks and unchanged credit accounting; they
do not impose a fixed stream-size limit or stop a transfer at 4 GiB.
Useful payload displaces cryptographic filler within granted capacity. This
can cost extra traffic and latency; the selected profile is not a throughput
or indistinguishability guarantee.

Session resumption, transparent reconnect/replay and automatic credential rotation
are outside the current contract. Normal application retry after a reported
connection failure remains the local client's responsibility.

## Experimental hybrid lifecycle

Select JSON `"transport":"no-connect-hybrid"` or
`--transport no-connect-hybrid`. The matching server must advertise
`X-App-Realtime: websocket-v1`; older servers fail before authentication or
target opening. The existing proxy credentials and shared destination policy
remain authoritative. No additional secret, target allowlist, or origin HTTP
Authorization header is introduced.

The milestone is successful root completion, completion of all six assets,
then completion and validation of all 20 startup POST/GET pairs. There is no
transition timer or packet-count predicate. Proxy data may occupy the existing
startup cells. Only after that complete graph does the client open
`/api/realtime` with subprotocol `nfc1.hybrid.v1`. HTTP carrier exchanges cannot
resume after the switch, and a failed WebSocket terminates its logical streams
without reconnect or replay. Additional local activity reuses the open socket.

The WebSocket uses Firefox's existing native implementation, including TLS,
handshake validation, masking, fragmentation, control frames and shutdown. The
current Firefox source does not support WebSocket over HTTP/3. The hybrid
therefore explicitly opens an HTTP/1.1 WSS connection to the same authority
after either H2 or H3 startup. The server must permit TCP/TLS on that endpoint;
a UDP-only server cannot serve this mode. Capture metrics must include both
the startup connection and WSS, including the second TLS handshake.

NFC1 cell and stream sequences continue across the transition. Each direction
uses only 512-byte control/idle, 64-KiB active, and 256-KiB bulk messages. Useful
frames displace cryptographic filler. An active message uses 64 KiB, or
256 KiB when at least 128 KiB is queued. A 2-ms application scheduling turn
coalesces partial payloads and new OPENs. A full 256-KiB queue or pure stream
control proceeds on the next event turn without that delay; newly ready
capacity can replace a pending partial-payload timer. The startup transition
does not depend on these timers.
Idle application heartbeats occur every 25
seconds. Client queueing is limited to one unacknowledged native WebSocket
message, in addition to the existing per-stream buffers and 512-KiB credits.
Native receive dispatch is bounded by 32 callbacks and 2 MiB total payload;
the peer-driven PONG queue is capped at 32. Overflow fails the carrier.
Unsolicited WebSocket compression is rejected before data processing starts.
A missing server message for 75 seconds closes the carrier. Hybrid local
upload buffering is bounded at 256 KiB per stream so bulk messages can fill
without growing the 512-KiB credit window.

The WS-only NFC1 `ACK` frame has kind 8, stream zero, empty payload and a
sequence equal to the last fully processed uplink cell. It is cumulative and
confirms local FIN retirement; socket-write acknowledgement alone does not.
Future, decreasing, malformed or HTTP-carried ACKs fail closed. Delivery-based
per-stream CREDIT remains unchanged, and empty heartbeats do not cause ACK
loops. A carrier still serves at most 32 concurrent logical streams.

The ordinary anonymous server SPA can exercise the same realtime lifecycle
without proxy credentials, but cannot open targets or send logical-stream
frames. This is a browser control, not an authentication bypass.

## Verification and evidence

Use the existing warm minimized product object directories. New source files
may regenerate the incremental product backend; they do not justify a cold
Firefox build or another object directory. See the
[full-source build runbook](https://github.com/incident201/naivefox/blob/naivefox-full-source/netwerk/naivefox/MINIMAL.md).

Check CLI syntax, JSON precedence and mode validation without starting the
network runtime:

```bash
python3 netwerk/naivefox/test/integration/run-transport-cli-tests.py \
  --binary /absolute/path/to/warm-obj-naivefox-linux/dist/bin/naivefox \
  --work-dir /absolute/path/to/warm-obj-naivefox-linux/no-connect/cli
```

Add `--caddy /absolute/path/to/combined-caddy` to that command for an active
H2/H3 check of one unchanged config containing a single proxy URI credential
pair. It starts classic by default, selects no-connect through the CLI, then
selects classic again. Both local listeners must work; no-connect must emit no
outer CONNECT and must not forward classic-only extra headers.

Acceptance covers both transports against one Caddy process, H2 and H3, and
SOCKS5 and HTTP CONNECT listeners. No-connect additionally needs authentication
and shared access-policy failures, invalid/truncated cells, sequence/credit bounds,
byte-exact uploads/downloads, concurrency, slow consumers, half-close, idle
wake, cancellation and shutdown. Server request observations must show zero
outer CONNECT for no-connect and successful CONNECT for classic. Do not disable
TLS certificate checks to make a fixture pass.

Inspect actual linker inputs, dependent `libxul` libraries and runtime package
contents after rebuilding, alongside shim checks. The closure must still omit
`js_static`, JavaScript execution, full DOM, layout, GFX, browser worker and
ICU4C; project-only source additions are not sufficient evidence by themselves.

Historical browser-worker residual, latency and throughput results remain
experimental evidence for that worker. Native functional and lean-build gates
do not transfer those measurements to the native client or establish
browser-equivalent scheduling. Any new performance or traffic-shape claim needs
its own measured native run under a stated comparison contract.
