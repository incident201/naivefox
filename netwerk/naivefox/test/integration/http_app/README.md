# Ordinary HTTP application workload

This real browser workload uses standard fetch GET/POST requests, streaming
download consumption and native HTTP flow control. It has no WebSocket,
NaiveFox cells, custom protocol padding or simulated Firefox transport.

It preserves the other benchmark's public asset sizes, twenty catalog bootstrap
pairs, deterministic payloads, stage order, download/upload sizes, four parallel
jobs and idle intervals. The four-request batch is synchronized at the backend
to ensure the declared overlap. Per-job hashes, byte counts, native
nextHopProtocol, actual asset consumption, bootstrap records and normal
start/end completion must all validate before a sample is admitted.

The five-window analysis remains unchanged. This workload measures real HTTP/3
against normal Firefox HTTP/3 bulk transfers. Keep the older WebSocket workload's
result separately: its TCP handshake features answer a different question and
must not be silently reclassified as passing.
