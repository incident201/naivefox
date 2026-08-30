# Firefox wire-behavior diagnostics

Capture comparison checks that NaiveFox continues to use Firefox's Necko,
NSS/PSM, and Neqo wire machinery without accidental project-specific markers.
It is diagnostic: a browser GET and padded proxy CONNECT are different
workloads, so packet timing and volume are not fingerprint-equality targets.

## Size-independent mechanism rule

Fixture asset sizes are diagnostic inputs, not camouflage parameters. A
candidate must not be selected by searching for a response size which happens
to improve a packet index, time window, or classifier score. Production code
may retain bounded byte budgets for safety, but it must derive scheduling,
headers, priorities, cache behavior, and stream lifecycle from normal Gecko
causes. Any topology candidate should remain directionally stable across a
small predeclared range of ordinary fixture sizes before promotion.
Use `--scenario browser_page --browser-page-base-size BYTES` to scale all six
page assets coherently for that check. Omitting it preserves the established
262144-byte fixture; the sanitized metadata records either the explicit base
or `default_262144`. For a dense H3 fronting-page arm, this option scales the
inner tunneled `browser_page`; the outer Caddy page remains the fixed profile
documented in [`FRONTING-PAGE.md`](FRONTING-PAGE.md). It is therefore not an
outer fronting-resource size sweep.

Use `--network-one-way-delay-ms N` and `--network-rate-mbit N` only inside the
one-shot isolated namespace to test RTT and bandwidth robustness. The verified
loopback `netem` profile applies symmetrically to every participant. Shaped
captures use the receive copy so packet timestamps occur after netem rather
than at the pre-qdisc transmit tap; metadata records the profile and capture
copy policy.

## Experiment-history preflight

Before implementing a new candidate, search this file, `README.md`,
`FRONTING-PAGE.md`, and the integration harness for both the proposed name
and causal synonyms. Then search Git history with message grep and pickaxe
(`git log --grep`, `-S`, and `-G`) at the intended code boundary. Compare
the causal mechanism rather than only arm names: a renamed timer, task hop,
response gate, parser/process topology, cache condition, framing rule, or
server read policy is still a duplicate if it waits on or changes the same
event.

If a causal analog already has an admitted artifact, do not reimplement or
rebuild it. Cite the existing commit/artifact in the research notes and move
to another hypothesis. A new screen proceeds only after this preflight finds
no prior analog; its successful or failed result is then added here so the
same search closes the loop for later work.

This preflight is mandatory for every implementation attempt, including a
follow-on variation of the most recently tested experiment. A different arm
name, protocol branch, task boundary, or number of resources does not make an
idea new when the changed code still controls the same causal event. Record a
rejected premise here even when timing evidence makes implementation and
capture unnecessary.

Do not begin a product edit, fixture fork, or harness arm until that exact and
causal-history search is complete. This is a per-idea gate, not a one-time
review for an extended research session.

## Predeclared outer-resource size campaign

The first actual outer-resource matrix is fixed before collection. It uses the
same promoted product binary and one H3 same-base superblock containing
Firefox A/B, SOCKS5 `document-start-overlap`, SOCKS5 six-resource tree, HTTP
CONNECT `document-start-overlap`, and HTTP CONNECT six-resource tree. The
inner `browser_page` remains at its default 262144-byte base. Every run uses a
fresh-profile, unshaped isolated WSL namespace unless a shaped row explicitly
says otherwise, seed `2026082971`, four complete blocks, and the five dashboard
views. These rows are descriptive screens below the 30-block inference floor.

The fixed profiles are:

| Profile | CSS | JavaScript | Images | Resource bytes excluding root | Purpose |
| --- | ---: | ---: | ---: | ---: | --- |
| exact current | 12 KiB | 24 KiB | 8 KiB x3 plus 34-byte JSON | 61,474 | Reproduce the currently measured fixture |
| coherent small, unit 1024 | 3 KiB | 6 KiB | 2 KiB x4 valid SVG | 17 KiB | Small-resource endpoint |
| coherent nominal, unit 4096 | 12 KiB | 24 KiB | 8 KiB x4 valid SVG | 68 KiB | Isolate replacement of the fourth response |
| coherent large, unit 16384 | 48 KiB | 96 KiB | 32 KiB x4 valid SVG | 272 KiB | Four-times nominal endpoint below the 384-KiB cap |

Comparisons among the three coherent rows isolate body-size scaling at fixed
topology, URLs, MIME types, inner workload, and product policy. Exact current
versus coherent nominal separately exposes the historical fourth-response
shape change and must not be described as a pure size comparison. A secondary
endpoint screen uses only coherent small and coherent large at 20-ms one-way
delay and 20 Mbit/s, seed `2026082972`, and four blocks. No default promotion
or acceptable-size guarantee will be inferred from these screening runs. Each
fixture first passes a live byte-count and MIME preflight outside the capture;
declared sizes alone are not accepted as evidence that the intended profile
was served.

The campaign completed on 2026-08-29. All six runs used the promoted product
binary (`naivefox_binary_build_id=929cd10f5dc1a5a286ba4d09ccc0c9c0`,
libxul SHA-256
`9df4dfb2b2b45475931a0155a8e336f5a8dcc1bb377e1d724eaa33785db7e39c`),
inner HTTPS/H2, the default 262144-byte inner workload, cold profiles, and 24
successful proxy resets and network-mutation checks. Every coherent-profile
artifact records a passed live size/MIME preflight. Values below are ordered
as packets 1--16 / packets 17--32 / packets 1--32 / first 250 ms / whole.

| Link | Outer profile | Safe artifact | SOCKS5 six-resource default | HTTP CONNECT six-resource default |
| --- | --- | --- | --- | --- |
| unshaped | exact current, 61,474 B | `27cd617551df09e1` | 0.11371 / 0.48209 / 0.18401 / 0.15601 / 0.42831 | 0.10430 / 0.48934 / 0.18104 / 0.16029 / 0.43444 |
| unshaped | coherent 17 KiB | `797c93e5e5de5b77` | 0.13154 / 0.50255 / 0.19260 / 0.17128 / 0.50407 | 0.12961 / 0.50641 / 0.19508 / 0.18269 / 0.50456 |
| unshaped | coherent 68 KiB | `6cd4be128b6cdaef` | 0.10774 / 0.46957 / 0.18568 / 0.16721 / 0.42231 | 0.10576 / 0.49428 / 0.19396 / 0.16441 / 0.42534 |
| unshaped | coherent 272 KiB | `7eeda921f5be8f0e` | 0.08663 / 0.37350 / 0.15555 / 0.14808 / 0.37166 | 0.08330 / 0.38149 / 0.14741 / 0.12599 / 0.35411 |
| 20-ms one-way, 20 Mbit/s | coherent 17 KiB | `6a9ff85817ad6a41` | 0.09260 / 0.24379 / 0.12282 / 0.07891 / 0.40498 | 0.09482 / 0.31374 / 0.13610 / 0.08995 / 0.40292 |
| 20-ms one-way, 20 Mbit/s | coherent 272 KiB | `1a6aeb8439779ae7` | 0.10323 / 0.23608 / 0.13335 / 0.11601 / 0.30535 | 0.09898 / 0.25181 / 0.13569 / 0.11801 / 0.30231 |

The unshaped coherent series is monotonic in every displayed default view:
larger outer bodies produced lower residuals. From 17 to 272 KiB, SOCKS5
changed by -0.04491 / -0.12905 / -0.03705 / -0.02320 / -0.13241 and HTTP
CONNECT by -0.04631 / -0.12492 / -0.04767 / -0.05670 / -0.15045. Under the
shaped link, the same endpoint comparison is mixed: SOCKS5 changed by
+0.01063 / -0.00771 / +0.01053 / +0.03710 / -0.09963 and HTTP CONNECT by
+0.00416 / -0.06193 / -0.00041 / +0.02806 / -0.10061. Thus a larger natural
page strongly improved whole-flow point estimates and improved HTTP CONNECT
packets 17--32 in both link conditions, but it did not dominate the small
page in the shaped 250-ms view.

The corresponding document-start causal controls support a scheduling effect
rather than only dilution by more outer bytes. Their packets 17--32 / whole
values were: exact current SOCKS5 0.61855 / 0.46008 and HTTP CONNECT 0.63062 /
0.43776; coherent 17 KiB unshaped 0.54084 / 0.51579 and 0.55695 / 0.49752;
coherent 68 KiB unshaped 0.64363 / 0.46323 and 0.65259 / 0.43256; coherent
272 KiB unshaped 0.56182 / 0.41738 and 0.57931 / 0.37375; coherent 17 KiB
shaped 0.52894 / 0.46855 and 0.51583 / 0.45591; coherent 272 KiB shaped
0.48775 / 0.40952 and 0.46932 / 0.39760. The six-resource defaults were lower
than their listener-matched controls in packets 17--32 for every row and in
whole for every row except the four-block unshaped 17-KiB HTTP point estimate
(0.50456 versus 0.49752).

This is evidence that outer resource size materially affects passive residuals,
not permission to tune a production site to one fixture. Four blocks are
descriptive, their bootstrap intervals remain broad, and the four-block exact
repeat does not reproduce the canonical ten-block point estimates exactly.
The coherent 17--272-KiB range is now functionally exercised, including both
endpoints on a slower link, but it is not an equivalence interval or a new
default measurement matrix. The canonical four-row table below remains
unchanged. The exact-current and coherent-nominal comparison is mixed and also
changes the fourth response from tiny JSON to a valid SVG, so it is not used
as a size effect.

Two one-block plumbing artifacts preceded the matrix. `1f49e855f3652c2c`
successfully exercised the coherent 68-KiB fixture before the live preflight
was added. `15c737ffa606b880` then exercised coherent 17 KiB with the new
preflight and passed. Neither is candidate evidence and neither is substituted
for a complete four-block row.

## Current implicit-default matrix

This is the canonical current-default residual dashboard. Lower is closer to
the same Firefox A/B controls in the same randomized block. The columns are
packets 1--16, packets 17--32, packets 1--32, the first 250 ms, and the whole
flow. Harness-only arm names in parentheses select the listener while keeping
the listed product policy and budgets.

| Outer | Local ingress | Effective omitted-preamble policy | Safe artifact | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| H2 | SOCKS5 | `document-first-buffer-task-overlap` | `e026109117dd8141` | 0.08824 | 0.42884 | 0.19744 | 0.09826 | 0.27527 |
| H2 | HTTP CONNECT | `document-first-buffer-overlap` (`document-first-buffer-http-connect`) | `e026109117dd8141` | 0.08222 | 0.42859 | 0.19466 | 0.09400 | 0.25955 |
| H3 | SOCKS5 | `tree-native-parser-resource-committed-overlap`, six cached resources, 384 KiB | `8bc66f8738d559b2` | 0.07392 | 0.39623 | 0.13837 | 0.15011 | 0.39149 |
| H3 | HTTP CONNECT | `tree-native-parser-resource-committed-overlap`, six cached resources, 384 KiB (`tree-native-parser-resource-committed-page-http-connect`) | `8bc66f8738d559b2` | 0.06375 | 0.40952 | 0.13753 | 0.14458 | 0.39012 |

Both artifacts use seed `2026082968`, ten complete same-base smoke blocks,
the 262144-byte `browser_page`, inner HTTPS/H2, Selenium, an unshaped isolated
WSL namespace, loopback MTU 1500, disabled offloads, and fresh Firefox profiles.
The reference browser was installed from the canonical upstream base as a CI
artifact; no full Firefox build was performed. H2 contains Firefox A/B and the
two defaults (40 participants). H3 contains Firefox A/B, both defaults, and the
required SOCKS5 and HTTP-CONNECT `document-start-overlap` causal controls (60
participants). All capture-drop,
network-mutation, transport-origin, inner-H2, and preamble-drain checks passed.
Ten smoke blocks provide a uniform descriptive dashboard; they are below the
30-block minimum and do not make a new paired-inference or absolute
indistinguishability claim. Bootstrap intervals and diagnostics remain in each
safe artifact's `arm-comparison.txt`. Both identify source revision
`aa4bd846af3a76170aee845ce7f2a356f6c768ee`, NaiveFox build ID
`929cd10f5dc1a5a286ba4d09ccc0c9c0`, and libxul digest
`9df4dfb2b2b45475931a0155a8e336f5a8dcc1bb377e1d724eaa33785db7e39c`.

This table is the single source of current residual numbers. A change to an
implicit default, published runtime, fixture, capture policy, or residual view
is incomplete until all four rows are rerun together under one declared
measurement contract and this table is updated in the same logical change.
Never splice a row from a different seed, reference, network profile, or
workload into the current matrix; prior results remain in the chronological
evidence below and in Git history.

The campaign also retained its failed attempts. Two quick-reference H3 gates
failed in direct Firefox with `connectionFailure` before NaiveFox ran, so they
produced no candidate data and were rejected. The exact same-base reference
was then installed without building Firefox. Three initial same-base H3 gates
were rejected because the HTTP PAC proxied Selenium's local remote-control
port, creating the sole outer QUIC identity before capture and therefore
failing the client-Initial origin check. A 100-ms capture-readiness delay did
not change that result and was removed. Restricting the PAC to the exact
workload authority, keeping other namespace-local control ports direct, and
keeping every non-loopback host fail-closed fixed the cause. One-block H2
artifact `e8e45098f22f8a09` and H3 artifact `96c46e62fc03a3c7` then passed
before the ten-block runs above.

The first 20-ms/20-Mbit plumbing smoke, `e97a1bae045f29d8` (seed
`20260828145`), successfully shaped and analyzed all four participants but
exposed that profile fields were written only for NaiveFox-only diagnostics.
It is retained as a failed metadata-validation attempt, not candidate evidence.
After fixing the ordinary metadata path, `162df2d1d421aa23` (seed
`20260828146`) again completed one H3/SOCKS block and records
`network_profile_active=1`, one-way delay 20 ms, rate 20 Mbit/s, one applied
protocol, and `capture_copy_policy=receive_after_netem`. Firefox packets 1--16
spanned about 148 ms and whole flows about 530--548 ms, confirming that the
stored receive-side timestamps include the emulated link. One block remains a
plumbing check, not a candidate comparison.

Harness validation `a045653efaa21665` (seed `20260828140`) completed one full
isolated same-base H3/SOCKS block at a 65536-byte base, including both Firefox
controls and the two NaiveFox arms. This is a successful end-to-end plumbing
check only; one block is explicitly insufficient for a residual inference.

`document-overlap` is the size-independent control for document scheduling. It
admits CONNECT after successful 2xx document response HEADERS while the Necko
listener is still active, and then requires a normal document drain. Decrypted
admission requires one QUIC identity, one outer ClientHello, matching
`document-complete` request semantics and response `Content-Length`, and the
product lifecycle order `response-headers admission -> CONNECT -> normal
drain`. Whether server FIN happens before or after CONNECT is report-only: it
must never trigger sample replacement or fixture-size adjustment.

`document-start-overlap` moves the barrier earlier without using a main-loop
turn as a proxy for socket progress. The root channel's own
`NS_NET_STATUS_WAITING_FOR` event proves that Gecko committed the bodyless GET
to its H2/H3 stream path before CONNECT is admitted. The final response result
and normal drain are recorded separately. H3 decrypted admission requires GET
HEADERS to precede CONNECT HEADERS on the sole QUIC identity; failure rejects
the mechanism and never causes selective recapture. Response HEADERS and FIN
ordering remain outcomes rather than admission criteria.

Safe 30-block same-base acceptance artifact `7b5c70011f0fba08` (seed
`27082730`) compared explicit `off` with `document-start-overlap` using paired
Firefox A/B controls and inner HTTPS/H2. The document-start policy improved
packets 1--16 (`0.16459` to `0.13442`), packets 17--32 (`0.76117` to
`0.65828`), packets 1--32 (`0.26499` to `0.22720`), packets 1--64/128, and the
250 ms view (`0.14026` to `0.12081`). No whole-flow regression was detected
(`0.38926` to `0.38660`; paired CI included zero), but equivalence was not
proved. All 120 participants passed
the isolated-network mutation and capture-drop policy. This satisfies the
predeclared product gate for H3. The final six-block H2 resource-tree screen
then retained the lower distance for the same compact mechanism in every view
after packets 1--16. This initially promoted `document-start-overlap` for
explicit H2 and H3 upstreams; the later first-buffer H2 evidence below replaces
the H2 policy while retaining it for H3. Explicit `mode: off` remains the
control and opt-out.
Secondary `steady_after_32` and lifecycle point estimates favored `off`; the
steady paired interval crossed zero and lifecycle did not survive the report's
Holm correction (`p=0.136`). They remain regression monitors rather than a
reason to override the predeclared early/250 ms/whole acceptance views.

`tree-native-parser-document-start-overlap` keeps the same request-commit
barrier and lets the root continue in the background through the lean HTML5
speculative scanner and one native stylesheet preload. Decrypted admission is
fail-closed on one QUIC identity and one ClientHello, exact document and CSS
semantics, and the wire order `root GET < CONNECT < CSS GET < CSS 200/FIN`.
The mode deliberately does not require any packet position, elapsed time, or
asset-size-derived overlap. Six-block H2-inside-CONNECT screening improved
packets 17--32 from `0.62052` to `0.50648` and packets 1--32 from `0.22690` to
`0.20573`, but worsened the 250 ms and whole-flow views because the completed
stylesheet remains additional traffic. It is therefore not promoted by that
screen alone.

The same mode is available as an explicit H2-only experiment. Decrypted
same-base admission proves one TCP/TLS/H2 connection, equal semantic TLS and
SETTINGS, normal document/FromParser request semantics, and the wire order
`root GET < CONNECT < CSS GET`, followed by successful root and CSS
END_STREAM. Six-block direct-Firefox screening `fec6d5d295bcecf0` improved
packets 17--32 from `0.45721` to `0.43922` and packets 1--32 from `0.22483` to
`0.21018`, while worsening 250 ms from `0.13102` to `0.14840` and whole-flow
from `0.28498` to `0.31852`. The complete stylesheet added about 67 KiB of
server traffic by 250 ms, so this H2 arm is not a default candidate either.

The final bounded H2 fronting-page experiment extended that mechanism to one
same-origin stylesheet, classic deferred script, and image, all discovered by
the lean HTML5 speculative scanner and opened through native Necko preload
channels after early CONNECT admission. Two fresh decrypted runs proved one
outer TLS/H2 connection, one ClientHello, normal END_STREAM completion, exact
same-base request semantics, and `root GET -> CONNECT -> resource GETs`. The
isolated six-block paired screen is retained as `f244527d965b626e`. Relative to
`document-start-overlap`, the resource tree was tied for packets 1--16
(`0.06880` versus `0.06871`) but worse for packets 17--32 (`0.50135` versus
`0.45276`), packets 1--32 (`0.21964` versus `0.19245`), 250 ms (`0.19285`
versus `0.18929`), and whole flow (`0.49826` versus `0.48398`). It introduced
about 47 KiB of additional early server traffic and a new burst rather than
removing the residual. The tree is therefore rejected as a default;
`document-start-overlap` was kept as the compact H2 control until the later
first-buffer campaign.

H2 `tree-native-parser-document-start-navigation-stop` retains the same
client-to-target ownership predicate and scoped load-group cancellation.
Decrypted admission proves `root GET < CONNECT < CSS GET < CSS 200 <
RST_STREAM(CANCEL)`, no CSS END_STREAM, and one TCP/TLS/H2 connection. In the
six-block direct-Firefox screen `2f2b4c49e0b6edb7`, however, packets 17--32
changed only from `0.48108` to `0.47885`; packets 1--32 improved from `0.23117`
to `0.21944`, while packets 1--16, 250 ms, and whole-flow all worsened. Server
bytes by 250 ms were nearly identical for complete and canceled CSS, showing
that the response had already entered the H2/TLS/TCP send path before the
causal cancel. The H2 stop mode therefore remains diagnostic and is not a
default candidate.

A server-side H2 padding-phase control then tested whether the existing Naive
Variant 1 budget could fill part of the nested-handshake gap without adding
bytes. The opt-in Caddy path moved the same eight independently randomized
padding records from the first target reads to immediately after the first
positive client-to-target payload. It used valid zero-payload Variant 1
records, required no client decoder change, and left unmodified NaiveProxy
clients on the stock path. Six-block same-base artifact `2c4ef7e9e097254b`
compared both arms only with ordinary direct Firefox A/B. The shift improved
packets 1--16 (`0.14803` to `0.13831`) but worsened packets 17--32 (`0.58058`
to `0.61987`), packets 1--32 (`0.28510` to `0.29356`), and 250 ms (`0.12589`
to `0.12960`). It also compressed the first-32-packet phase from about 56.5 ms
to 32.9 ms instead of reproducing Firefox's roughly 53--56 ms phase. The
server patch and arm were removed. Legacy Variant 1 can contribute at most
`8 * 255 = 2040` padding bytes per direction; a larger cover protocol would
therefore add traffic rather than redistribute the existing budget. The
complete-stylesheet upper bound already shows that such additive early volume
trades a local 17--32 improvement for a 250 ms/whole-flow penalty.

The preceding H2 packet-index screens were collected with segmentation
offloads disabled but the Linux loopback MTU still at its default 65536 bytes.
Their SYNs advertised MSS 65495, and direct Firefox consequently exposed
5--8 KiB host-local TCP segments in packets 22--32.  Those results remain
useful lifecycle/byte-volume diagnostics, but their packet-index distances are
not physical-wire camouflage evidence and must not select a product default.
Controlled H2 screening now sets and verifies loopback MTU 1500 before any
endpoint starts and rejects an outer TCP segment with more than 1460 payload
bytes.  H2 product decisions require a fresh same-base baseline under this
policy.

The first fail-closed two-block gate under that policy is safe artifact
`00de90dccbdb2cec` (seed `2026082701`).  SYN MSS was 1460 for every participant
and no oversized outer TCP segment was admitted.  By packet 32 direct Firefox
had about 10.9 KiB of server transport payload rather than the earlier
55--62 KiB loopback-supersegment result; H2 `off` had about 6.4 KiB.  The main
remaining shape was temporal: direct Firefox reached packet 32 around
51--56 ms, while `off` reached it around 11 ms.  A bodyless
`document-start-overlap` control improved the isolated packets-17--32 distance
but worsened packets 1--16, packets 1--32, 250 ms, and whole flow.  With only
two blocks this is localization evidence, not inference or a product default.

A server-backpressure control then kept the ordinary 64-KiB declared
stylesheet but made only one HTTP/2 maximum-DATA-frame-sized source chunk
available before Gecko's existing causal navigation stop.  Six-block safe
artifact `4cf0e735fac6e9fc` (seed `2026082704`) confirmed the mechanism rather
than a product preset: packets 17--32 improved from `0.59298` for `off` to
`0.51864`, and packets 1--32 from `0.24220` to `0.22026`.  However packets
1--16 worsened from `0.08364` to `0.09202`, 250 ms from `0.07451` to
`0.08283`, and whole flow from `0.23120` to `0.25397`.  The bounded response
still added about 16 KiB of server traffic by 250 ms.  Therefore a fixed
one-frame response is not a default candidate.  It establishes only that
server-side backpressure can preserve the useful early stylesheet phase while
substantially reducing the complete-response penalty; a successor must derive
availability from real tunnel lifecycle events rather than a selected byte
count or timer.

Three subsequent two-block fail-closed controls rejected the remaining simple
H2 server-shaping candidates before any larger run.  In `b4e50d4c5b2f6e01`,
the stylesheet received a partial body only after the first successful
target-to-client CONNECT write on the same physical H2 connection.  This
event-derived release improved packets 17--32 only slightly, left packets
1--32 effectively unchanged, and retained a whole-flow penalty.  A separate
randomized 8--32 ms first-flight deferral (`360c141b0530a2b9`) likewise traded
a small packets-17--32 change for worse packets 1--16, 250 ms, and whole flow;
the timing range was not tuned further.  Stock `outer-session-gate`
(`e4a22a686496c9e1`) did not improve packets 17--32 or 1--32, despite better
250 ms/whole point estimates.  Finally, opt-in randomized fragmentation of
the existing first eight padded target records (`acfefa8613e22022`) added no
cover payload and improved both early slices descriptively, but its extra
H2/TLS framing sharply worsened 250 ms (`0.07142` to `0.15499`) and whole flow
(`0.26033` to `0.34173`).  All four server experiments were removed; the
pinned server binary and module source were restored byte-for-byte.  These
screens do not prove the H2 residual unfixable, but they rule out fixed cover,
first-event cover, cold-flight delay, gate-only establishment, and early
record fragmentation as acceptable defaults under the no-tail-regression
criterion.

A fresh same-base H2/inner-HTTPS-H2 barrier sweep then held the root request,
response body, MTU 1500 policy, and capture cutoff constant while moving only
the causal CONNECT admission point. Six-block `document-complete` screen
`fc337f9614b11061` (seed `2026082803`) improved packets 17--32 from `0.50779`
to `0.45518`, but worsened packets 1--16, 1--32, 250 ms, and whole flow.
Successful response-HEADERS admission in `9f502d43d980f6df` (seed
`2026082809`) improved packets 17--32 from `0.52789` to `0.46752` and packets
1--32 from `0.20916` to `0.20453`, but worsened packets 1--16 (`0.05244` to
`0.05673`), 250 ms (`0.09330` to `0.09638`), and whole flow (`0.28526` to
`0.29516`).

Two response-body-start controls completed the causal scale. Releasing after the complete first
`OnDataAvailable` buffer in `9225f26bc7c3897b` (seed `2026082806`) improved
packets 17--32 (`0.60708` to `0.57265`), 250 ms, and whole flow, but worsened
packets 1--16 and left packets 1--32 slightly worse. Releasing immediately
after the first successfully read body byte in `063ec521aeb104dd` (seed
`2026082808`) improved packets 1--16 (`0.10928` to `0.09447`), packets 17--32
(`0.61839` to `0.56906`), and packets 1--32 (`0.26650` to `0.24909`), but
worsened 250 ms (`0.11467` to `0.12981`) and whole flow (`0.31970` to
`0.33364`). All artifacts passed the isolated-network, capture-drop,
inner-H2, normal-drain, and offload checks. Six blocks remain screening-only,
but the consistent tradeoff rejects all later document barriers under the
no-other-view-penalty rule for the original SOCKS ingress.

Changing the local ingress to native HTTP CONNECT exposed a better causal
boundary without changing the target workload or response sizes. Successful
response-HEADERS admission first reproduced as a candidate in six-block
artifacts `e25ca068dd5f6a2f` and `271cc85a33c50ba2`. A bounded response carrier
that remained open until target payload was observed was then rejected:
six-block artifact `59cb2995524f019d` worsened packets 17--32 (`0.41397`
versus `0.37410`), packets 1--32, 250 ms, and whole flow versus the simpler
HEADERS candidate. Native manual proxy preferences were also rejected in
two-block artifact `e839b0808bf6d33a`; they introduced a large server-byte
deficit and raised whole-flow distance to `0.48472` versus `0.29245` for PAC.
Both temporary mechanisms were removed.

The retained `document-first-buffer-overlap` mode releases only after the
entire first root-body buffer supplied by Necko has been read successfully.
This is a size-independent channel event, not a fixed byte threshold. A short
or failed read does not release CONNECT; a successful 2xx result and normal
root drain remain required, with bodyless completion using the terminal causal
fallback. Six-block artifact `306a249a46d33a5c` measured packets 17--32 at
`0.38953` versus `0.38985` for HEADERS admission and `0.44930` for the SOCKS
default. It also improved 250 ms (`0.08365` versus `0.09790`/`0.09415`) and
whole flow (`0.26186` versus `0.28139`/`0.28377`). Independent six-block
artifact `2b8dd75c4e682940` reproduced every target and cumulative win:
packets 17--32 were `0.41285` versus `0.43633`/`0.47148`, packets 1--32 were
`0.22472` versus `0.23290`/`0.24706`, 250 ms was `0.06474` versus
`0.08738`/`0.08567`, and whole flow was `0.27947` versus
`0.29050`/`0.29013`.

The first two-block workload check was inconclusive across SOCKS: `initial`
artifact `531316919b72bf69` retained the large packets-17--32 win (`0.52734`
versus `0.63849` for document-start), while `bulk_download_256k` artifact
`c301d169be40ee76` favored document-start at packets 17--32 and whole flow.
That initially limited the implicit promotion to HTTP-CONNECT-only H2
listeners. The broader SOCKS campaign below supersedes that provisional
restriction; these small runs remain screening evidence, not a 30-block
inference claim.

Two fresh independent six-block SOCKS/browser-page screens used the same
fail-closed PAC ingress and common same-base Firefox A/B controls. Artifact
`0e3d5fc56b0e06f5` measured first-buffer at `0.50157` for packets 17--32 and
`0.28531` for whole flow, versus `0.57141` and `0.30666` for document-start.
Replication `d98cf5d810045203` again improved packets 17--32 (`0.53648` versus
`0.58128`) and packets 1--32, while whole flow moved in the other direction
(`0.25715` versus `0.24842`). Combining the 12 sanitized paired blocks only as
a diagnostic, not as a predeclared inference test, ranked first-buffer best in
all five primary views: packets 1--16 `0.06904`, packets 17--32 `0.50936`,
packets 1--32 `0.22106`, 250 ms `0.07852`, and whole flow `0.25605`, versus
document-start `0.07038`, `0.56771`, `0.23474`, `0.08193`, and `0.25820`.

Three independently seeded two-block workload controls then addressed the
earlier bulk ambiguity. On `initial`, artifact `b8f33cd43e0a9722` favored
first-buffer at packets 17--32 (`0.73471` versus `0.74927`) and whole flow
(`0.45993` versus `0.47738`). On `bulk_download_256k`, artifact
`631ac031bb4498aa` favored it in every primary view, including packets 17--32
(`0.44125` versus `0.54880`) and whole flow (`0.29902` versus `0.32002`). On
`bidirectional_256k`, artifact `37207e981beba111` again improved packets
17--32 (`0.54020` versus `0.58278`) and whole flow (`0.35035` versus
`0.38637`). This repeated SOCKS evidence, together with the two HTTP-CONNECT
replications, established `document-first-buffer-overlap` as the H2 control.
The final task-boundary campaign below supersedes it for SOCKS-only listeners;
HTTP CONNECT and mixed listeners retain direct first-buffer admission. H3
retains `document-start-overlap`; any explicit preamble or gate value remains
authoritative.

The selected SOCKS policy then passed a fresh 30-block paired same-base run,
safe artifact `e1a89392d921b419` (seed `2026082832`). All 120 participants
passed the fail-closed network-mutation, MTU/offload, capture-drop, inner-H2,
and normal-drain admission policy. First-buffer ranked lower in all five fixed
views. Packets 17--32 improved from `0.47100` to `0.42560`; the paired
difference was `-0.04540`, CI95 `[-0.05907,-0.03310]`, Holm `p=0.0005`.
Packets 1--16 were `0.06300` versus `0.06409`, packets 1--32 `0.19473` versus
`0.20371`, 250 ms `0.08576` versus `0.08633`, and whole flow `0.25701` versus
`0.25735`. None of those four monitors detected a penalty; their
multiple-comparison-adjusted tests did not establish a difference. The report
supports this relative default ranking, not absolute Firefox
indistinguishability.

A native manual-SOCKS-profile control was also tested and removed. Two-block
artifact `7ffcb29b0724180e` improved whole flow to `0.32764` from `0.33475`
for document-start but worsened packets 17--32 to `0.52145` from `0.49642`.
The Firefox profile route is therefore not part of the product or harness.

`document-first-buffer-task-overlap` tests a distinct causal boundary without
a timer or byte threshold. Its first implementation suspended the root request
between the complete first body buffer and a next-main-thread-task barrier.
Lifecycle artifact `b2c6572423dc924e` and browser-page artifacts
`2bcfc478a6a47a5e`/`2a1d04de4af7389f` proved the mechanism and a strong
packets-17--32 reduction, but the root backpressure produced an unstable whole
flow and a bulk-download penalty.

The retained implementation removes `Suspend`/`Resume`. It queues the same
next-task barrier while allowing the channel to drain; if `OnStopRequest`
arrives synchronously after the first buffer, only terminal bookkeeping is
deferred until the queued barrier. Lifecycle artifact `7fa017e5724e7628`
proves SOCKS5 ingress, `first-data-buffer-task` admission, inner HTTPS/H2,
normal drain, and isolated/offload checks. Two-block bulk artifact
`8934faaa21a95889` ranked it best at 250 ms and whole flow. Independent
six-block browser artifact `e1b352731c553d6d` ranked it best at packets 1--16,
250 ms, and whole flow while retaining a packets-17--32 improvement versus
document-start. A fresh 12-block comparison `79e7587b8e64d9ee` then ranked it
better than direct first-buffer in all five fixed views.

The final 30-block paired SOCKS artifact `2834cb35aa391bb0` (seed
`2026082838`) admitted all 120 participants and supported paired inference.
Relative to direct first-buffer, task admission improved packets 17--32 from
`0.49063` to `0.48016`, packets 1--32 from `0.21280` to `0.21028`, 250 ms from
`0.08630` to `0.08036`, and whole flow from `0.26133` to `0.25077`. The whole
paired difference was `-0.01056`, CI95 `[-0.02075,-0.00043]`; it did not
survive correction across the five overlapping views (Holm `p=0.3095`).
Packets 1--16 moved from `0.06956` to `0.07328`, with a paired CI crossing
zero. Under the relaxed acceptance rule this is the better overall tradeoff:
it further reduces packets 17--32 and whole flow, with no detected early
penalty. Omitted-preamble SOCKS-only H2 therefore uses task admission;
HTTP-CONNECT-only and mixed listeners retain direct first-buffer admission,
while the later H3 campaign determines its protocol-specific default below.
Explicit configuration remains authoritative.

`tree-native-parser-document-start-navigation-stop` tests whether Firefox's
normal scoped load-group cancellation can retain that early server-heavy phase
without paying for the complete synthetic stylesheet. CONNECT is not a member
of the synthetic navigation load group. Cancellation is admitted only after
the CONNECT handoff, positive client-to-target tunnel application data, and a
successful stylesheet `OnStartRequest` with 2xx response headers. These two
asynchronous runtime predicates may arrive in either order. Decrypted admission
requires `root GET < CONNECT < CSS GET < CSS 200 < cancellation`, one QUIC
identity, one ClientHello, and the expected aborted stylesheet drain. It never
uses elapsed time, packet position, or delivered-byte count as a gate.

`tree-native-parser-document-start-response-stop` uses the same scoped load
group but waits for positive decoded target-to-client tunnel payload instead
of client-to-target activity. Runtime admission has two mutually exclusive
valid terminal branches: a causal abort with target activity, stop, expected
`NS_BINDING_ABORTED`, and no complete CSS drain; or natural completion with a
normal one-resource drain and no activity/stop/abort markers. Mixed branches
are rejected. Decrypted abort admission requires positive H3 DATA on the
CONNECT stream before CSS STOP_SENDING and RESET_STREAM, error `0x10c`, no CSS
FIN, and positive but partial CSS DATA relative to its declared body length.
Packet index, time, and QUIC reset final size are outcomes only because the
reset offset also includes HTTP/3 framing and HEADERS.

The first response-start trace placed the CSS GET at packet 18, CSS 200 at
packet 22, delivered the server burst through packet 58, and then emitted
STOP_SENDING/RESET_STREAM; the reset final size was about 51.8 KiB. Safe
six-block H2-inside-CONNECT screen `125ee98eabf85501` (seed `20260829`) found
the arm best at packets 1--32 (`0.20573` versus `0.20945` for complete CSS and
`0.23821` for document-start), but not at packets 17--32, 250 ms, or whole-flow.
Whole-flow remained `0.41881` versus `0.41220` for document-start. Upstream
audit confirms the tradeoff is fundamental: a normal FromParser stylesheet
drains to FIN, while canceling an active H3 load necessarily produces request
cancel/reset signaling. The arm remains experimental and does not change the
product default.

Safe six-block response-stop screen `9193f5a55f430bda` (seed `27082707`)
observed one abort and five natural completions. It improved packets 17--32 to
`0.57228` and packets 1--32 to `0.19807`, but `document-start-overlap` remained
better at 250 ms (`0.12532` versus `0.13297`) and whole-flow (`0.38923` versus
`0.39998`). The reset therefore was not a deterministic every-tunnel marker,
but the opportunistic policy also failed to remove the late stylesheet volume
reliably. This does not justify promotion or a 30-block confirmation.
The product keeps a working tunnel alive if the bounded background drain times
out, but the controlled harness rejects that third operational outcome because
it proves neither a causal abort nor a complete natural stylesheet lifecycle.

## Modes and policy

Every proposed experiment has a mandatory history gate before implementation.
Search this document and the other NaiveFox Markdown, the product and capture
harness, and the complete Git history (including pickaxe/regex searches for
removed implementations). The search must cover both the proposed name and
the causal wire/lifecycle mechanism: renaming an earlier delay, boundary,
carrier, padding, scheduling, cache/classifier, process-topology, or
multiplexing experiment does not make it new. Record either the specific
difference which makes the mechanism new or the earlier evidence which rejects
it before writing product, Caddy, or harness code. If the same causal variable
was already tested, do not implement it again unless new evidence identifies a
materially different condition and the preflight records that distinction.

The runners support two reference modes:

- `quick` (default) downloads the current official Firefox Nightly artifact
  named by the tooling manifest and uses that binary directly; it does not
  build Firefox. The manifest records the expected Nightly version and the
  SHA-256 of the last verified archive. Mozilla may republish a mutable
  `latest-mozilla-central` URL without changing the version string. When that
  happens, verify the downloaded binary still reports the manifest version and
  refresh only `archive_sha256` before rerunning the gate. This keeps the
  comparison against the current Nightly while retaining an explicit artifact
  integrity check.
- `same-base` uses caller-supplied Firefox and NaiveFox packages built from the
  same Firefox base. It is the only meaningful exact stack comparison and the
  only mode that may require a Firefox browser build.

An ordinary Firefox build is allowed only when the same-base diagnostic is
explicitly requested. It is outside the upstream, minimized-product, and export
gates and is never a merge or release prerequisite.

Run the H2, H3, and passive comparisons from the integration directory:

```bash
./run-capture-comparison.sh
./run-h2-capture-comparison.sh --arm root
./run-h3-capture-comparison.sh
./run-observer-comparison.sh
./run-camouflage-suite.sh --mode smoke --protocol both
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

Create the ordinary Firefox reference once with the dedicated builder:

```bash
netwerk/naivefox/tools/build-firefox-same-base.sh --dry-run
netwerk/naivefox/tools/build-firefox-same-base.sh
netwerk/naivefox/tools/build-firefox-same-base.sh --verify
netwerk/naivefox/tools/build-firefox-same-base.sh --reuse
```

The builder derives the exact merge-base of the current NaiveFox revision and
`firefox-upstream`, checks it out as a detached pristine worktree, and performs
one optimized non-PGO browser build with four jobs by default. It runs
`mach package` and records the source revisions, full mozconfig, mozinfo,
toolchain and sccache identity, application and ELF build IDs, NSS key-log
define, and hashes of the package and capture runtime in a private manifest
below the reference object directory.

The reference is reusable by later NaiveFox revisions that retain the same
Firefox merge-base. A completed manifest is verified instead of rebuilding;
an exact prepared manifest may resume an interrupted build. The builder never
deletes or clobbers an existing worktree or object directory and refuses any
unrecognized directory. `--reuse` prints the verified paths expected by the
capture runners. Override the external paths only with `--worktree` and
`--objdir`; use `--sccache off` when compiler caching is not wanted.

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

WSL's packaged `dumpcap` can be denied access when its output path is below the
object directory, even with the required capabilities. The capture runners
automatically use a private staging directory below `/tmp` and move completed
captures into the private object-directory diagnostics tree. No manual
permission change is needed, and successful runs still delete raw captures and
key logs. When the runner is invoked as root, it also supplies a private
`XDG_RUNTIME_DIR` so the downloaded headless Firefox can start without using the
interactive user's WSLg runtime directory.

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

`run-h2-capture-comparison.sh` is the hardened same-base sequence admission
for camouflage arms. It always re-executes inside the private capture network,
starts a route-netlink mutation monitor before each participant, disables and
verifies namespace-local loopback offloads, rejects dumpcap drops or early
exit, and requires direct Firefox and NaiveFox to use one physical TCP/H2
connection. Private key logs drive ALPN, client SETTINGS, and per-stream
GET/CONNECT/response/END_STREAM reconstruction. Safe output retains only
header names, relative timing and ordering, stream indices, equality booleans, and build
identity; header values, endpoint values, credentials, profiles, captures, and
keys are deleted after success. Both cohorts deliberately use a command-line
cold Firefox start after capture begins, and that backend/start state is
recorded so warm Selenium and cold command-line runs cannot be mixed silently.
For tree arms, private admission also requires native HTTPS document fetch
metadata with exact `Priority: u=0, i`, plus same-origin stylesheet/script
`Referer`, fetch metadata, and `Priority: u=2`; only pass/fail booleans enter
the safe summary.

The H2 `document-start-overlap` diagnostic additionally requires the causal
runtime admission, successful result, normal drain, and CONNECT-established
markers to belong to one NaiveFox connection lifecycle. Decrypted wire
admission requires the document GET HEADERS before CONNECT, an HTTP 2xx
document response, and a normal H2 END_STREAM on that document stream. The
END_STREAM position relative to CONNECT is deliberately report-only: request
commit, rather than response completion or a selected frame position, releases
CONNECT in this mode.

`run-h2-connect-priority-comparison.sh` isolates the scheduling cause of the
first SOCKS tunnel. It uses the same authenticated Caddy listener for an
ordinary proxied same-base Firefox navigation, NaiveFox default, and the
explicit first-tunnel UrgentStart diagnostic. Firefox receives a private
exact-target channel filter through a system-access Marionette session; the
filter carries preemptive proxy authorization in memory, never in a profile or
command line. The decrypted gate rejects a 407, multiple outer TCP/ClientHello
identities, TLS/SETTINGS mismatch, or missing/ambiguous scheduling evidence.
The exact first-CONNECT packet must contain one relevant HEADERS occurrence,
method, and stream plus one priority-flag/dependency/weight field set; an extra
coalesced HEADERS or PRIORITY occurrence invalidates admission. Before capture,
both fresh NaiveFox processes must have no applied marker. After workload,
default must still have none, while the diagnostic must have exactly one safe
`Connection 1` H2 applied marker ordered before its first CONNECT-established
evidence. Only the resulting validation booleans are exported. A valid run
classifies the mechanism as `native-match`, `wire-null`, or `native-mismatch`;
the latter two remain successful experiments and delete their private raw
material normally. Safe evidence records only that verdict,
Priority-header-presence booleans, scheduling equality, header names/order, and
source/build hashes. It never feeds a passive classifier.
The current same-base Caddy H2 control produced `mechanism_verdict=wire-null`:
default NaiveFox, the UrgentStart diagnostic, and proxied Firefox had equal
observable scheduling, and none carried a CONNECT `Priority` header. Do not
promote this diagnostic to a camouflage default or spend passive samples on
it; it remains available only to test another H2 peer or a future Gecko tunnel
adapter change.

The preamble callback barrier and observed wire ordering are deliberately
separate claims. On a fast loopback run, Necko can receive a resource response,
release `tree-early-overlap`, and still schedule the CONNECT HEADERS after the
resource END_STREAM. The decrypted validator rejects that trace. Do not
selectively recapture only passing traces or use this arm for passive causal
screening until wire-level overlap is deterministic; doing so would condition
the dataset on the scheduling outcome being measured.

`tree-root-overlap` is a separate production-shaped scheduling arm and does
not change any older mode. It discovers and opens the same resource channels
while parsing the root, then admits CONNECT once the root has completed and at
least one resource channel was successfully started. It does not wait for a
resource response callback. A safe product marker records only
`root_done=1`, the non-zero started-resource count, protocol, and admission
kind; sample validation requires exactly one such marker before passive
analysis. The controlled fixture requires exactly two started resources. It
also waits for a separate safe `drain=complete completed_resources=2` marker,
emitted only after both opened assets received response headers, HTTP 2xx, and
a successful `OnStopRequest`, while every preamble stream stopped normally. A
bounded watchdog invalidates the sample and never substitutes for that
completion. The H3 decrypted
comparison requires `tree-root-overlap` to be
selected with `tree-complete` so private request and response-size parity is
proved in the same run. If the tree contains no successfully started resource, terminal
operation completion releases CONNECT immediately instead of waiting for the
preamble timeout.

This is a client scheduling intervention, not a promise that QUIC/H2 frames
will appear in the same order. Resource response HEADERS or FIN may precede
CONNECT on a fast path. Decrypted diagnostics report observed overlap, but do
not use it for admission, retry a failed ordering, or filter passive samples by
that outcome. They still require a completed root before CONNECT and validate
the production normal-completion marker for every expected CSS/JS response.
Observed asset FIN is report-only because a fully consumed known-length H3
response may use `H3_REQUEST_CANCELLED` instead. In the paired H3
diagnostic, `tree-complete` and `tree-root-overlap` must retain identical
root/CSS/JS request semantics and response sizes. H2 uses the same fixed
fixture/config and validates expected request semantics, but does not claim a
paired asset-size proof.

Resource-cache experiments use the orthogonal `cache-resources` preamble
setting. The default is off. When enabled for a tree/resource mode, only CSS,
JS, and other discovered resource channels enter Gecko's native HTTP cache;
the root navigation stays cache-inhibited. This is a mechanism diagnostic for
separating resource topology from cold response-body volume. It is not a
persistent-profile design: normal NaiveFox temporary-profile startup remains
valid, and product behavior must not depend on state surviving a process run.

The passive harness exposes that question only as `tree-warm-css-304`. It maps
to `tree-root-overlap`, selects one CSS resource, and enables
`cache-resources`. Every reference or NaiveFox participant receives a newly
created profile which is reused only for its private warm→measure pair and then
deleted. The measured passive window begins at its publicly observable client
Initial. The complete pcap is preserved with other private diagnostics when a
run fails; a server-only tail from the closed warm 5-tuple may precede the
Initial and is excluded from classifier features. Any client traffic before
that Initial, or any other endpoint UDP 5-tuple after it (regardless of whether
its payload decodes as QUIC), invalidates the sample.
The fixture serves the stable CSS
with `ETag` and `Cache-Control: no-cache`; admission fails unless Gecko first
receives an unconditional 200 and later generates `If-None-Match` which earns
a 304. The NaiveFox inner measurement browser remains fresh and must still
receive its own cold 200. This arm is H3 gate/smoke diagnostic evidence only:
it cannot enter a shared superblock or a confirmatory/research run, and it is
never a proposal for persistent `NAIVEFOX_PROFILE` state. The unchanged
`tree-root-overlap-css` arm remains the cold control.
The warm arm disables persistent TLS token storage and H3 0-RTT in both outer
profiles, rejects any `ssl_tokens_cache*` file, and requires one measured QUIC
identity, one ClientHello, and no 0-RTT packet. Its network-mutation monitor
runs continuously from before warm-up through the measured capture cutoff.
Firefox A/B and NaiveFox receive condition-specific warm controls; a fresh
`tree-root-overlap-css` run under the same fixture revision and binary build is
the separate cold dataset. The harness restarts only the local Caddy process
between the warm and measured phases, on the same origin and port, while the
target server and its append-only journal remain alive. This deterministically
removes prior QUIC session state without a quiescence timeout. The cold control
gets the same Caddy reset immediately before measurement. Cross-dataset
warm/cold interpretation is therefore
descriptive causal screening, not paired or confirmatory inference.

The first fully fail-closed one-block same-base H3/inner-HTTPS admission run for
this diagnostic is retained as safe artifact `d26f91c82a29ceed` (seed `305`,
NaiveFox binary build ID `b1820e20442018465574f71995c69460`). Firefox A,
Firefox B, and NaiveFox
each produced an unconditional warm 200 followed by a measured native Gecko
conditional request and 304 with identical selected outer request semantics.
The fresh inner Firefox behind NaiveFox independently received 200. Every
measured participant had one QUIC identity, one ClientHello, no 0-RTT, no TLS
token persistence, and no route/address/link mutation. This is admission
evidence only; one block contains no classifier inference. The predecessor
private run `da04d2419e6c9715` discovered two delayed server-only datagrams from
the closed warm 5-tuple before the measured client Initial. The harness now
preserves that full pcap on failure and defines the passive sample origin at
the first client Initial, while rejecting any client traffic before it or
stale flow continuing afterward.

A later ten-block predecessor run was discarded in full when an old Caddy QUIC
flow continued after a measured client Initial. The deterministic Caddy reset
above closes that contamination path; the post-reset one-block gate is retained
as safe artifact `211be50a1f1af4bd` (seed `307`). The accepted ten-block warm
screen is `4a2b14495599e0a4` (seed `20260825307`), with the matching fresh-profile
cold control `6343c2cbe6c99bfd` (seed `20260825309`). All 30 participants in each
dataset passed the cache/transport/network admission invariants.

The result rejects cache persistence as a camouflage mechanism. Firefox versus
NaiveFox remained perfectly separable in the selected 1--16, 17--32, and 1--32
packet views in this small non-inferential screen. Removing the cold CSS body
did not correct the phase boundary: packet 16 still arrived around 8 ms for
NaiveFox versus roughly 25--28 ms for Firefox. Cold body packets had merely
made part of packets 22--32 look closer by signed byte size despite arriving
roughly 127 ms too early. With 304, that accidental byte-shape similarity
disappeared and the early CONNECT/control phase became more exposed. The 250 ms
aggregate improved modestly, but Firefox A/B noise also increased and whole-run
volume differences were essentially unchanged. The useful causal conclusion is
therefore limited: response-body volume affects the observed 17--32 shape, but
resource cache state does not repair the underlying topology/scheduling gap.
No product behavior should depend on warm or persistent cache state.

`tree-complete-css` and `tree-root-overlap-css` are harness-only one-asset
controls. They map to the unchanged production `tree-complete` and
`tree-root-overlap` modes with `max-assets=1`, so the parsed root opens only the
first stylesheet and never the later script. Passive admission requires exact
`started_resources=1` and `completed_resources=1` lifecycle evidence. The H3
decrypted diagnostic admits `tree-root-overlap-css` only beside
`tree-complete-css`; it proves one QUIC identity, identical root/CSS selected
request semantics and response size, and root FIN before CONNECT. Any observed
CSS FIN ordering relative to CONNECT remains report-only and is never a
resampling criterion.

Controlled causal screens may set
`NAIVEFOX_FIXTURE_CAMOUFLAGE_STYLE_SIZE` and
`NAIVEFOX_FIXTURE_CAMOUFLAGE_SCRIPT_SIZE` before starting the fixture. The
defaults remain 64 KiB and 128 KiB. Both values are bounded and recorded in
safe metadata. Vary them only as a declared response-profile condition while
keeping the normal CSS/Script channels and orchestration unchanged; never
select them from observed packet positions.

`root-pmtud-control` is an H3-only, same-binary harness control. It uses the
same production `document-complete` configuration and root workload as `root`,
but only the NaiveFox participant profile explicitly sets
`network.http.http3.pmtud=true`. Passive screening can therefore compare the
new default route with the former forced-PMTUD behavior without changing the
socket, product binary, or preamble semantics. Decrypted admission proves one
complete root GET with equal selected request semantics and response size; it
does not claim PMTUD wire equivalence. That conclusion requires the passive
capture itself.

The dedicated H3 diagnostic gives every NaiveFox arm the same inner HTTPS URL;
the direct reference uses the same page path and query on the outer fixture.
It rejects any Firefox nonzero exit or empty screenshot and requires
successful response headers and `Content-Length` for every preamble GET plus
an observed root FIN before CONNECT. Application completion at a known response
length may make upstream Firefox end an H3 fetch with
`H3_REQUEST_CANCELLED` instead of waiting for the peer FIN, so asset FIN is not
used as a universal completion predicate. The diagnostic checks every root
against Firefox navigation semantics and uses
`tree-complete` as the required root/resource-size control for all two-resource
overlap modes. `tree-early-overlap` and `tree-overlap` have no existing private
normal-drain completion marker, so their available decrypted ordering
predicates must pass directly; a missing predicate is not repaired with a wait
heuristic or selective resampling. Until exact H3 DATA-byte accounting is
available, do not use those two arms for a strict whole-volume conclusion: one
background asset can complete by known `Content-Length` without an observed
FIN. `tree-root-overlap` has the deterministic all-resource drain marker used
for that comparison.

For H3, compare:

- QUIC version and negotiated `h3`;
- semantic ClientHello and client transport parameters;
- H3/QPACK settings;
- classic CONNECT rather than CONNECT-UDP or extended CONNECT;
- multiple CONNECT streams on one QUIC connection;
- no established TCP fallback at the strict H3 endpoint;
- padding negotiation and absence of synthetic markers.

Same-base `run-h3-capture-comparison.sh --compare-arms` additionally audits
document/tree preamble ordering against the browser-page root and resources.
The experimental `tree-early-overlap` arm is opt-in and must be selected
together with `tree-complete`.
Use repeated `--compare-arm` options to restrict the candidate set. Its safe
event table reports only method/status sequencing, connection/stream indices,
packet positions, and observable response-header or stream-FIN overlap; raw
headers, values, captures, and NSS keys remain private. For tree causal
validation, a transient private GET-only extract compares selected root/CSS/JS
request values and ordering between complete and overlap. For
`tree-early-overlap`, a second private-only extract proves that the two asset
`Content-Length` values also equal `tree-complete`; all values are deleted with
the private capture directory. The safe summary retains only booleans:
`tree_request_semantics_match=yes` for complete/overlap equality,
`tree_early_overlap_request_semantics_match=yes` and
`tree_early_overlap_asset_sizes_match=yes` for the experimental arm, and
`tree_expected_request_semantics=yes` after the root has navigation/site-none
semantics and CSS/JS have same-origin no-cors subresource semantics with
referers exactly equal to the root URL computed from its private pseudo-header
values.

The browser-page fixture uses a 64 KiB stylesheet and a 128 KiB script, with
the root document and both assets remaining below the configured 256 KiB tree
budget. Direct Firefox, preamble traversal, and the inner browser load all use
those same URLs and response bodies. This controlled size increase is intended
to keep ordinary resource streams alive when `tree-overlap` or
`tree-early-overlap` starts CONNECT on loopback; it is not tuned to reproduce
any packet sequence. Neither the origin
nor Caddy applies `Content-Encoding` to these fixture assets: adding gzip or
zstd would invalidate the intended on-wire stream lifetime.

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

## Statistical camouflage experiment

`run-camouflage-suite.sh` attacks the project assumption from the point of
view of a passive traffic classifier. It does not prove indistinguishability.
It attempts to distinguish ordinary Firefox from NaiveFox using selected
externally observable features, reports the measured classifier advantage, and
compares that advantage with an independent Firefox-versus-Firefox baseline.

The collector uses seeded complete blocks for three cohorts: Firefox A,
Firefox B, and NaiveFox. Each sample gets a fresh, symmetric trusted profile.
All cohorts run on the same host and network namespace, use the same capture
interface, endpoint IP and port, certificate, target, Caddy process, and
controlled Firefox page workload. The NaiveFox browser reaches the target
through its private SOCKS listener. HTTPS/TLS inside CONNECT is the default
NaiveFox workload; `--inner-transport http` produces a separate cleartext
diagnostic dataset. Forced Alt-Svc applies only to direct H3 reference traffic,
not to the SOCKS browser. The SOCKS browser uses a fail-closed PAC: exact
loopback destinations use the sample's SOCKS5 port, while every other hostname
goes to a dead local proxy with no `DIRECT` fallback. This keeps Mozilla
background requests out of the captured NaiveFox flow. H2 and H3 are separate
datasets. Strict H3 rejects
established TCP or TCP payload.

The browser controller remains alive until after the primary capture has
stopped. After receiving the browser's completion POST, the target writes a
private completion file which the controller watches without generating a
second network flow. Browser or NaiveFox process shutdown is therefore not part
of the primary sample.
`dumpcap` readiness uses its runtime marker rather than pcap header creation.
Every sample rejects nonzero capture drops, an H2 flow missing its opening
client SYN and ClientHello, or an H3 flow missing its opening client Initial.
The passive H2 collector starts the outer Caddy listener in H2-only mode and
requires exactly one proxy-port TCP identity with exactly one client SYN and
one visible ClientHello. TLS 1.3 encrypts the server's selected ALPN, so the
passive gate does not mislabel ClientHello advertisement as selection: the
H2-only listener plus successful workload completion is the keyless ALPN
contract. Inner HTTPS/H2 uses a distinct port and cannot enter this identity.

`--h2-proxy-floor-superblocks` is a same-base passive causal design fixed to
the `browser_page` workload. Each randomized block contains direct Firefox A
and B, the same Firefox using its native authenticated HTTPS-proxy path, and
NaiveFox `off`. All browsers are Selenium-prelaunched before capture; a shared
wire completion token is reset locally before every participant, and every
capture ends at completion plus the same 250 ms tail. The native-proxy and
NaiveFox participants must also complete the same workload over the dedicated
inner HTTPS/H2 fixture. The ordinary H2 transport gate rejects any participant
with more than one outer TCP identity, SYN, or ClientHello.

The safe multi-arm output treats `firefox-proxied` as an analysis-only
candidate slot. Its distance from direct Firefox estimates the unavoidable
proxy architecture floor; `off` versus direct Firefox is the total NaiveFox
gap, and the paired difference between those two distances is the remaining
product-specific signal. The fixed views are packets 1--16, packets 17--32,
packets 1--32, the first 250 ms, and the whole flow. The internal `naivefox`
label for the native Firefox candidate is only a legacy analysis-schema slot,
not a claim about which executable produced it.

Six-block same-base artifact `bc9ee30b969b6318` first measured that floor
against ordinary direct Firefox, which remains the camouflage target.  It was
captured before the MTU-1500 policy and is retained only as lifecycle evidence;
its packet-index distances must not select H2 product behavior.

Fresh fail-closed six-block artifact `8f0c619bcd38385e` (seed `2026082712`)
repeated the control with loopback MTU 1500, SYN MSS 1460, no oversized outer
TCP segment, pre-launched same-base browsers, and inner HTTPS/H2.  Native
Firefox using its ordinary authenticated HTTPS-proxy path remained only
modestly closer to direct Firefox than NaiveFox `off`: distances were
`0.05923/0.46731/0.20420/0.10955/0.24196` versus
`0.06217/0.48243/0.21055/0.11034/0.26441` for packets 1--16, packets 17--32,
packets 1--32, 250 ms, and whole flow.  The paired native-proxy-minus-NaiveFox
interval crossed zero in every view.  This screening result does not prove
equivalence with six blocks, but it localizes most current H2 residual to the
shared nested `CONNECT + inner TLS/H2` architecture rather than a distinct
NaiveFox client fingerprint.  The control diagnoses that floor; it does not
redefine the camouflage target as Firefox behind a proxy.

A server-side response-coalescing candidate was then tested because stock
`forwardproxy` flushes every target TCP read. The candidate was backward
compatible and request-opt-in: unmodified NaiveProxy clients retained the
historical path, while the experiment coalesced adjacent target reads until a
one-millisecond idle window without adding or removing bytes. Cross-run data
looked favorable, but the authoritative randomized paired artifact
`01c1250aa7a7f14a` rejected it. Relative to direct Firefox, packets 17--32
improved only `0.5279 -> 0.5194`, packets 1--32 regressed
`0.2432 -> 0.2500`, and the server-byte deficit by packet 32 increased from
about 49.3 KiB to 50.0 KiB. The patch and experimental arm were therefore not
retained. This is evidence against server read-boundary batching as a useful
H2 camouflage mechanism, not evidence that the remaining H2 signal is
unfixable.

Two request-opt-in TLS-boundary controls then replaced arbitrary target TCP
read boundaries with semantic inner-TLS boundaries without changing Variant 1
payload or padding bytes. Full-record alignment split the initial inner server
flight into seven ordinary padded response records. In 30-block paired
artifact `e742cfd3a0c19fe9` (seed `2026082717`) this improved packets 17--32
from `0.54512` to `0.49152` and packets 1--32 from `0.22555` to `0.21010`,
but worsened whole flow from `0.22023` to `0.23771` (`p=0.0288`). A causal
flight cutoff stopped alignment when the client sent its next TLS flight, so
application DATA returned to stock streaming. The authoritative 30-block
artifact `9ce752e81ecd307b` (seed `2026082723`) reproduced the early benefit
(`0.53960 -> 0.48008` for packets 17--32 and `0.22238 -> 0.20419` for packets
1--32) but also the whole-flow regression (`0.22811 -> 0.24725`, `p=0.0063`).
Safe record sequences show why: the useful initial split itself shifts bulk
records by roughly seven visible TLS-record ordinals, so ending the policy
after the flight cannot remove its new fingerprint.

A final cleartext-only control aligned just TLS Handshake records and returned
to stock streaming at the first CCS or encrypted record. Six-block artifact
`64e7a03ff341ffe2` (seed `2026082729`) removed the large ordinal shift, but it
also removed the early benefit: packets 17--32 changed `0.54819 -> 0.55041`
and packets 1--32 `0.22286 -> 0.22699`. It did not qualify for a larger run.
The server module source, binary, and temporary client header hook were
restored byte-for-byte. Together these controls close deterministic inner-TLS
record alignment as a default H2 mechanism under the no-tail-regression rule:
the multi-record split is the cause of both its local improvement and its
later distinguishability.

A final client-side diagnostic reduced only the existing Variant 1 upstream
padding range from 0--255 to 0--63 bytes, without adding cover traffic or
changing the stock server. Strict decrypted artifact
`20260827T111901Z-af8eee26` admitted the H2-only runtime hook and ordinary
CONNECT lifecycle. Six-block paired artifact `7bd5dd64a07e662f` (seed
`2026082731`) found a local packets 1--16 improvement (`0.06600 -> 0.05847`),
but packets 17--32 (`0.55279 -> 0.58610`), packets 1--32
(`0.23201 -> 0.24500`), 250 ms (`0.06341 -> 0.07417`), and whole flow
(`0.24158 -> 0.25524`) all moved away from direct Firefox. The diagnostic
hook was removed without a larger run. Reducing random client padding only
reorders the early nested-TLS phase; it does not repair the remaining H2
application-phase shape.

Controlled workloads cover a cold small operation, a browser page with CSS,
JavaScript, images, and an API response, warm sequential requests, burst and
concurrent requests, bulk download and upload, bidirectional transfer, and an
idle-then-resume lifecycle. The runner keeps request counts and byte budgets
comparable where the protocol roles allow it; payload equality is not assumed.

The safe feature extractor uses only information available on the wire:

- signed direction and normalized IP/TCP-payload or UDP-datagram lengths for
  the first 128 packets, plus 16/32/64/128-packet aggregates;
- inter-packet timing, response delay, bursts, idle gaps, direction n-grams and
  run lengths, and byte/packet windows through two seconds;
- visible TLS-over-TCP record boundaries and lengths;
- TCP SYN options, FIN/RST/reconnect behavior, and actual retransmission or
  out-of-order observations;
- public TLS ClientHello capability sets with GREASE values normalized;
- for QUIC, version, Initial sizes and ordering, CID lengths, Retry/version
  negotiation, public ClientHello and transport parameters, packet phases, and
  observable TCP probe attempts at the strict-H3 endpoint.

It deliberately excludes labels and harness artifacts, filenames, process
data, source/destination ports, absolute timestamps, profile paths, credentials,
queries, decrypted H2/H3 headers and frames, CONNECT authority, plaintext,
private NSS secrets, and NaiveFox logs. H2/H3 decryption remains available only
to the separate wire-parity diagnostics.

The analyzer is dependency-free Python. It fits an L2-regularized logistic
classifier after train-only standardization and feature screening. Splits are
grouped by experiment block when that metadata is present, otherwise by
session, and stratified by workload; the two Firefox cohorts form the
experimental baseline. The primary metric is orientation-fixed ROC AUC with
NaiveFox, or Firefox B in the baseline, always treated as the positive class.
AUC is calculated within each outer fold and pair-weighted across folds, so
uncalibrated scores from different models are never pooled. The report retains
`D = max(AUC, 1 - AUC)` only as a diagnostic and never uses it for a verdict.

Empty steady or lifecycle phases have explicit observable presence indicators.
The general diagnostic confidence interval uses a conditional clustered
bootstrap of held-out groups within their outer folds. Research verdicts do not
use that fixed-model interval: for both the target comparison and its Firefox
negative control, the three primary views additionally resample complete
workload blocks and refit the entire grouped-CV pipeline, including feature
screening and standardization. The workload/block-stratified permutation test
also refits that pipeline after reshuffling session labels. Expensive
permutation refits run only for Firefox-versus-NaiveFox in the three
predeclared verdict views: initial 32 packets, steady state after 32 packets,
and lifecycle. Secondary reports explicitly record why refit inference was not
run.
The report also includes an outer-fold threshold learned only from training
data, confusion metrics, standardized coefficient importance, and
leave-one-workload-out generalization. Whole-flow, initial packet windows,
initial time windows, steady state, and lifecycle views are reported
separately. Raw metrics are retained so policy thresholds can be changed
without recapturing traffic.

Run levels are:

- `gate`: two samples per cohort and protocol; structural collection, analysis,
  and sanitization only;
- `smoke`: ten samples per cohort and protocol; end-to-end validation only;
- `standard`: sixty samples per cohort and protocol; preliminary statistics;
- `research`: 240 samples per cohort and protocol; the only level that emits a
  GREEN/YELLOW/RED research classification. This is a hard minimum: the runner
  and non-screening analyzer reject a smaller research dataset.

Gate and smoke results are always `INCONCLUSIVE`, regardless of their point
estimate. Research policy currently treats a sufficiently narrow, validated
refit-bootstrap interval contained within `0.40--0.60`, a healthy Firefox
negative control whose interval contains chance, and no more than 0.05 excess
absolute advantage over that control as GREEN. Reverse-oriented, confounded,
or uncertain evidence is YELLOW. Stable `AUC >= 0.70` is RED only when its
refit lower bound is at least 0.60 and the full-refit permutation test also has
`p <= 0.05`. These are project policy thresholds, not a security proof.

Examples:

```bash
./run-camouflage-self-tests.sh
./run-camouflage-suite.sh --mode gate --protocol both
./run-camouflage-suite.sh --mode gate --protocol both --inner-transport http
./run-camouflage-suite.sh --mode smoke --protocol h2
./run-camouflage-suite.sh --mode standard --protocol both --seed 20260824
./run-camouflage-suite.sh --mode research --protocol h3
```

The same explicit seed reproduces schedule order for HTTPS-primary and
HTTP-diagnostic runs, but separately collected samples are not statistically
paired. For arm comparison, `--multi-arm-superblocks` requires same-base mode
and collects Firefox A, Firefox B, and NaiveFox `off`, `gate`, and `root` once
per seeded randomized block. The sanitized full dataset records
`experiment_block` and `naivefox_arm`, then materializes one arm-specific
dataset per arm using the same two contemporaneous Firefox controls. In that
mode `--samples-per-cohort` is the number of five-member blocks per protocol;
it cannot be combined with `--naivefox-arm`.

The default remains that bounded five-member design. A deliberate screening
run may instead select arms with `--multi-arm-arms`; for the first preamble
shape screen use
`gate,root,tree-complete,tree-overlap` (`root` is document-complete). A causal
follow-up may replace `gate` with the opt-in `tree-early-overlap` control while
keeping the root, CSS, JavaScript, and asset sizes identical. Add
`--multi-arm-views initial_packets_16,packets_17_32,initial_packets_32` to
report both the cumulative early views and the non-overlapping 17--32 packet
slice. `packets_17_32` contains only passive per-packet (and, for H2,
per-record) sequence features from those positions; it does not repeat the
handshake or first-16 aggregates. That creates randomized six-member blocks: the four
selected modes are all paired against the same Firefox A/B controls. The
analyzer emits every selected pair and its Holm family contains only the
protocols and views in that report. This opt-in design is screening-only and
cannot produce an absolute verdict.

The first isolated same-base H3/inner-HTTPS smoke for
`root,tree-complete,tree-root-overlap` is retained as safe artifact
`183164d35decbb0f` (seed `24082420`). It used ten paired blocks and therefore
supports neither relative nor absolute inference. Descriptively,
`tree-root-overlap` was closest to the shared Firefox A/B
controls for packets 17--32 (0.53163 versus 0.56326 for `tree-complete` and
0.64406 for `root`) and for packets 1--32 (0.20633 versus 0.22243 and
0.23118). `root` remained closest for packets 1--16 and for the whole-flow
view. Together with the paired decrypted trace, which observed CONNECT at
packet 19 while both resource responses were still active, this supports the
root-complete/start-resources admission mechanism but does not support making
it the default. The remaining early signal is concentrated around packets 12,
14, and 17, while the whole-flow penalty remains dominated by tree response
volume. A follow-up should vary the number of naturally opened resources with
the same admission rule before adding delays or packet-size targets.

The first ten-block same-base H3/inner-HTTPS screen of
`document-complete,document-overlap,document-start-overlap` is retained as safe
artifact `c7ef700ffa3c42ae` (seed `20260825`, revision `2ab204178e4b`). All 50
participants passed the isolated-network mutation monitor and physical-QUIC
checks. This is scheduling evidence only: the reference used the measured
scenario query while the outer preamble still used the shorter fixed document
path. Relative to the common Firefox A/B envelope, request-committed overlap
was worse in every selected view: 0.13721 versus 0.08776/0.08299 for packets
1--16, 0.63527 versus 0.61094/0.61219 for packets 17--32, and 0.23219 versus
0.20479/0.20280 for packets 1--32. Its new packet-13 client phase opposed the
Firefox server phase. The two older document modes remained descriptively
indistinguishable from one another. Consequently none of these modes warrants
a 30-block confirmation from this run.

A subsequent exact-path decrypted diagnostic mapped the early phase boundary
for Firefox and `document-complete`. Both used the same full scenario/query
path and a 494-byte root response. Their packet 9 H3 control/SETTINGS and QPACK
encoder/decoder stream initialization matched semantically, with no dynamic
QPACK instructions. NaiveFox then emitted document GET HEADERS in outer packet
10, before receiving its server post-handshake bundle in packet 11. Firefox
first received the post-handshake transport work (`HANDSHAKE_DONE`, session
ticket/token, and ACK), with its navigation GET appearing only later; the exact
GET index varied across controlled traces while this ordering remained stable.
Packets 13--18 were consequently different lifecycle phases: Firefox still
contained PMTUD/ACK/navigation work while NaiveFox had already completed the
document and opened CONNECT. This rules out a post-document drain fence as the
primary repair for the earliest split and motivates an H3-only causal control
which queues the document transaction normally but releases its HEADERS only
after QUIC handshake confirmation. Packet positions remain outcomes, never the
barrier definition.

A six-block same-base H3/SOCKS screen of the bounded post-confirmation dwell
and six-resource native-parser arm is retained as safe artifact
`f07dc0cb1e282738` (seed `20260828118`). Descriptively, the candidate measured
0.13428 for packets 1--16, 0.19702 for packets 17--32, 0.12997 for packets
1--32, 0.15636 for the first 250 ms, and 0.35166 for the whole-flow view. The
shared `document-start-overlap` control measured 0.65307 for packets 17--32
and 0.44082 for the whole flow. This is the strongest controlled fixture result
so far, but it is not evidence for a product default: the settling interval is
fixed, the page has one fixed six-resource shape, and the namespace has stable
localhost latency and bandwidth. A real candidate must retain its advantage
when object sizes, response pacing, RTT, and available bandwidth vary. Those
conditions must remain experimental inputs, not values inferred from packet
indices in this artifact.

The complete follow-up screen is recorded below. Every `page base` label in
this table refers to the inner tunneled browser workload; the six outer
fronting resources retained their fixed fixture sizes. Distances are ordered as
packets 1--16 / packets 17--32 / packets 1--32 / first 250 ms / whole flow.
One- and two-block rows are diagnostics only; their purpose is to reject ideas
cheaply and preserve negative results, not compare tiny differences across
independent seeds.

| Safe artifact | Variant | Blocks | Distances |
| --- | --- | ---: | --- |
| `d403f00e1eaa227e` | first 16-ms post-confirmation dwell | 1 | 0.18711 / 0.23813 / 0.16551 / 0.19828 / 0.37031 |
| `23f0f7cb5a58b9f3` | hold images for stylesheet/script response headers | 1 | 0.22357 / 0.34407 / 0.23429 / 0.21802 / 0.41994 |
| `472e78a791458b2d` | 15-ms post-confirmation dwell | 1 | 0.16362 / 0.30478 / 0.17779 / 0.20493 / 0.45740 |
| `dbdf4f41d5460ae8` | 17-ms post-confirmation dwell | 1 | unavailable / 0.47632 / 0.29546 / 0.33291 / 0.50266 |
| `d747406b98716d1c` | 16-ms dwell plus forced candidate PMTUD | 1 | 0.19568 / 0.24770 / 0.21073 / 0.23474 / 0.43461 |
| `cdee6e1b67e8d6e0` | image response-tailing class | 1 | 0.17274 / 0.17441 / 0.16196 / 0.18151 / 0.47484 |
| `2dd5b3b7dde3920a` | release images at parser finish | 1 | 0.21567 / 0.28686 / 0.18901 / 0.22479 / 0.44302 |
| `7819f87eab73fd56` | preconstruct and synchronously activate all channels | 1 | 0.24242 / 0.26010 / 0.22714 / 0.31901 / 0.44565 |
| `8acfa2e9bf69fd25` | preconstruct channels; CSS/script then image task | 2 | 0.19958 / 0.21088 / 0.17982 / 0.22551 / 0.39621 |
| `353ec240b34c1cc1` | preceding two-phase activation with 12-ms dwell | 1 | 0.17917 / 0.48634 / 0.21403 / 0.22754 / 0.46054 |
| `96e4f0433451f926` | exact Firefox User-Agent override | 1 | 0.20197 / 0.27632 / 0.18694 / 0.24588 / 0.44651 |
| `54edcd1cb261bd6b` | activate all prepared resources on next main turn | 1 | 0.23997 / 0.17685 / 0.21067 / 0.24860 / 0.43406 |
| `1e3021030d540730` | sequentially open all resources at parser finish | 1 | 0.25506 / 0.34635 / 0.23902 / 0.24322 / 0.45066 |
| `dcb8ca36bf574d7a` | confirmation only, then 16-ms resource delay | 1 | 0.13137 / 0.21756 / 0.15600 / 0.20159 / 0.39685 |
| `86c38709c99b66a0` | split delay: 8 ms before root and 8 ms before resources | 1 | 0.17666 / 0.51109 / 0.26344 / 0.24243 / 0.50742 |
| `fe08089dde0ac15c` | one socket-thread turn after handshake confirmation | 1 | 0.26040 / 0.62362 / 0.34047 / 0.32277 / 0.53048 |
| `6c1da178f259ec37` | confirmation-gated six-resource arm, 65536-byte page base | 4 | 0.10039 / 0.33793 / 0.15398 / 0.16820 / 0.41965 |
| `02da1771dd44e616` | confirmation-gated six-resource arm, 1048576-byte page base | 4 | 0.08761 / 0.42522 / 0.17234 / 0.16295 / 0.38674 |
| `99b99423b650ff11` | explicit 16-ms dwell, 65536-byte page base | 4 | 0.18995 / 0.13934 / 0.15949 / 0.19980 / 0.37883 |
| `4f29ad91fa7f29f6` | explicit 16-ms dwell, 1048576-byte page base | 4 | 0.14023 / 0.18591 / 0.13661 / 0.16593 / 0.36313 |
| `a8977a8f77e3e129` | explicit 16-ms dwell, 20-ms one-way delay and 20 Mbit/s | 4 | 0.19669 / 0.55600 / 0.24601 / 0.16671 / 0.44047 |
| `54ea85af8f8ade5a` | cancel resource bodies on first tunneled application bytes, 20-ms one-way delay and 20 Mbit/s | 1 | 0.34306 / 0.63746 / 0.37520 / 0.30147 / 0.51192 |
| `4f6a2d5f9d5d7f7e` | cancel each resource at response headers, confirmation gate, 20-ms one-way delay and 20 Mbit/s | 1 | 0.15433 / 0.51371 / 0.24360 / 0.18631 / 0.45597 |
| `b269891ca820c21c` | cancel all resources after all request HEADERS are sent, confirmation gate, shaped link | 1 | 0.28176 / 0.49624 / 0.32068 / 0.22380 / 0.55319 |
| `79bd0652b13f9da6` | cancel all resources on first `RECEIVING_FROM`, confirmation gate, shaped link | 1 | 0.29122 / 0.53469 / 0.32290 / 0.19148 / 0.54446 |
| `3ac531854f16583d` | preceding response stop plus first post-confirmation transmitted-packet gate, shaped link | 1 | 0.17466 / 0.61024 / 0.27430 / 0.14788 / 0.49282 |
| `783fb4014bc2c827` | ordinary pre-confirmation root scheduling, shaped link | 1 | 0.22187 / 0.30808 / 0.23385 / 0.20438 / 0.40084 |
| `9041afc666c4d27a` | ordinary pre-confirmation root scheduling, shaped link | 4 | 0.21975 / 0.41939 / 0.24150 / 0.20281 / 0.37755 |
| `98ff5b58e8acabbf` | ordinary pre-confirmation root scheduling, unshaped localhost | 4 | 0.13015 / 0.42408 / 0.19173 / 0.18586 / 0.40104 |
| `52eb19521edabbc2` | prepare four images, open them together on the next main-thread turn, shaped link | 1 | 0.10745 / 0.24945 / 0.15401 / 0.18001 / 0.36747 |
| `3db870baccdd8047` | preceding next-turn image scheduling, shaped link | 4 | 0.14180 / 0.42302 / 0.20045 / 0.19663 / 0.35352 |
| `69ee3fd2559c47e1` | preceding next-turn image scheduling, unshaped localhost | 4 | 0.11254 / 0.43472 / 0.17955 / 0.17351 / 0.41992 |
| `f9a240071240a55b` | open images in `2+1+1` successive main-thread turns, shaped link | 1 | 0.19743 / 0.37870 / 0.22054 / 0.20749 / 0.38823 |
| `752d40ac53187d45` | open images in `2+2` successive main-thread turns with retained first-body admission, shaped link | 1 | 0.24093 / 0.31250 / 0.23199 / 0.20919 / 0.44428 |
| `f5cfa9eff387313d` | next-turn image scheduling plus exact Firefox application UA token, shaped link | 1 | 0.12563 / 0.62333 / 0.24182 / 0.17464 / 0.39800 |
| `906de419f0b1605d` | release CONNECT after stylesheet and deferred-script response HEADERS, shaped link | 1 | 0.09888 / 0.53957 / 0.19785 / 0.19506 / 0.37942 |
| `e0780953cbebd8a4` | release CONNECT as soon as all six request transactions commit, shaped link | 1 | 0.22115 / 0.69741 / 0.32680 / 0.22386 / 0.47965 |
| `9c0b28cfd8163df8` | defer first-resource-HEADERS CONNECT admission by one main-thread task, shaped link | 1 | 0.10124 / 0.25761 / 0.15044 / 0.17865 / 0.36383 |
| `5247083ce674bb8c` | preceding task-deferred admission, shaped link | 4 | 0.18055 / 0.42214 / 0.21098 / 0.16720 / 0.38589 |
| `e003c4bcdeae430f` | release CONNECT after the first consumed resource body buffer, shaped link | 1 | 0.22977 / 0.35266 / 0.23174 / 0.20103 / 0.38039 |
| `390cc24ccb6ef8c9` | preceding first-resource-body-buffer admission, shaped link | 4 | 0.11745 / 0.29471 / 0.15293 / 0.16835 / 0.34363 |
| `4e4edd7c53b91735` | preceding first-resource-body-buffer admission, unshaped localhost | 4 | 0.10945 / 0.44214 / 0.18431 / 0.17123 / 0.38250 |
| `c9643a1e000b3c2c` | preceding first-resource-body-buffer admission, 65536-byte page base, shaped link | 1 | 0.20808 / 0.72418 / 0.32068 / 0.22956 / 0.45906 |
| `8925499c80a97688` | preceding first-resource-body-buffer admission, 1048576-byte page base, shaped link | 1 | 0.21060 / 0.41138 / 0.24301 / 0.19447 / 0.42297 |
| `47adda8f2a4b7783` | preceding first-resource-body-buffer admission, 65536-byte page base, shaped link | 4 | 0.08814 / 0.33185 / 0.14079 / 0.13174 / 0.31845 |
| `07c422cacf441cd1` | preceding first-resource-body-buffer admission, 1048576-byte page base, shaped link | 4 | 0.14312 / 0.39571 / 0.18047 / 0.15179 / 0.36178 |
| `d62dc8f4ca95d6e1` | preceding first-resource-body-buffer admission, 50-ms one-way delay and 5 Mbit/s | 1 | 0.15058 / 0.38089 / 0.19917 / 0.10000 / 0.29532 |
| `4606cb015d86e68a` | preceding first-resource-body-buffer admission, 50-ms one-way delay and 5 Mbit/s | 4 | 0.09429 / 0.42424 / 0.16478 / 0.07301 / 0.31681 |
| `d25a8910b028a6d5` | release CONNECT after the deferred-script body buffer, shaped link | 1 | 0.22782 / 0.46535 / 0.25761 / 0.22191 / 0.40645 |
| `4a893926cca282f9` | release deferred images and CONNECT on the first resource body buffer, shaped link | 1 | 0.26798 / 0.51989 / 0.32041 / 0.32007 / 0.47166 |
| `ee1d57bb7b7817f0` | give CONNECT document-equivalent H3 priority `u=0, i`, shaped link | 1 | 0.27480 / 0.62412 / 0.33962 / 0.30322 / 0.43876 |
| `c276f92a62ecafdd` | open two images next turn and two on first resource body buffer, shaped link | 1 | 0.29429 / 0.44772 / 0.31302 / 0.28711 / 0.45058 |
| `e26fad3d3d44af3c` | open all six resources together next turn, then admit on first body buffer, shaped link | 1 | 0.22284 / 0.71720 / 0.32656 / 0.24533 / 0.49797 |
| `dcbd84d7270ad4d2` | admit after first body buffers from two distinct resources, shaped link | 1 | 0.12912 / 0.29712 / 0.17971 / 0.18160 / 0.39582 |
| `e37b8b45df2c72f7` | cross-process stylesheet rendezvous before resource activation, shaped link | 1 | 0.10459 / 0.23626 / 0.14855 / 0.19669 / 0.36922 |
| `a0ead86d96ae38c9` | preceding cross-process resource activation, shaped link | 4 | 0.12889 / 0.32005 / 0.16100 / 0.15227 / 0.34415 |
| `cdf15c6b3cc11142` | request one-byte ranges for the four image-cover responses, shaped link | 1 | 0.20619 / 0.47585 / 0.23629 / 0.20118 / 0.42462 |
| `bfd151c6ae8b307f` | keep CONNECT admission, but delay local SOCKS success until a second resource progresses, shaped link | 1 | 0.12217 / 0.56947 / 0.24059 / 0.21827 / 0.38174 |
| `6f3abdd953241f36` | promote CONNECT locally from generic `u=4` to default-wire `u=3`, shaped link | 1 | 0.28571 / 0.67590 / 0.35053 / 0.27983 / 0.44583 |
| `6f32c2baf9c1115f` | stop still-active resource bodies on first positive target response, shaped link | 1 | 0.18774 / 0.43453 / 0.25554 / 0.27522 / 0.35880 |
| `11363e69e10c73f7` | cap retained first-body topology at four resources, shaped link | 1 | 0.29180 / 0.56690 / 0.32295 / 0.28668 / 0.49588 |
| `516a52ecdf7325fe` | request a one-byte range only for the largest, third image-cover response, shaped link | 1 | 0.20167 / 0.20686 / 0.21134 / 0.26134 / 0.38629 |
| `3ccc3e566c17bf55` | preceding selective third-image Range request, shaped link | 4 | 0.13225 / 0.38692 / 0.17637 / 0.14720 / 0.36567 |
| `b4404c4a0ed5af8f` | delay image activation by half the measured root request-to-response interval, shaped link | 1 | 0.11576 / 0.43308 / 0.18672 / 0.17680 / 0.37198 |
| `c3e2674327356bfc` | release all prepared images from the main-thread idle queue, shaped link | 1 | 0.24089 / 0.66662 / 0.30821 / 0.22581 / 0.46435 |
| `78cbd9ef5ace8048` | open each prepared image after the preceding image request commits, shaped link | 1 | 0.30343 / 0.37128 / 0.30186 / 0.28342 / 0.45118 |
| `ef003b01f019fe12` | open three images next turn and the final image on the following main-thread turn, shaped link | 1 | 0.30779 / 0.58293 / 0.35812 / 0.31232 / 0.44871 |
| `d1c08c6718fa278f` | admit CONNECT after the first successful image response HEADERS, shaped link | 1 | 0.11972 / 0.38830 / 0.17140 / 0.18762 / 0.38223 |
| `e386025177b9432c` | make only the first preamble-owned H3 CONNECT stream incremental at generic urgency, shaped link | 1 | 0.12355 / 0.42295 / 0.17830 / 0.16060 / 0.35493 |
| `f9c4c26050a2988a` | bound H3 duplex-pump reads to Gecko's 4096-byte default segment, shaped link | 1 | 0.23503 / 0.36697 / 0.25550 / 0.24040 / 0.41194 |
| `3500eb894d951e57` | demote only the first preamble-owned H3 CONNECT from generic `u=4` to `u=5`, shaped link | 1 | 0.25290 / 0.49920 / 0.28958 / 0.22799 / 0.47932 |
| `7770ec81beea4b8a` | release prepared images after CSS and script request commits, shaped link | 1 | 0.28935 / 0.39986 / 0.29283 / 0.29333 / 0.42113 |
| `c3d1643c1f966245` | enable standard PLPMTUD on the page-mode H3 proxy route, shaped link | 1 | 0.31425 / 0.55231 / 0.35320 / 0.29369 / 0.52470 |
| `5720930db5b46be2` | open the prepared script one main-thread turn after CSS, then images on the following task, shaped link | 1 | 0.12781 / 0.38475 / 0.19378 / 0.19891 / 0.35858 |
| `a6de9bd760c43a2e` | root-rendezvous native parser, one CSS control, shaped link | 1 | 0.20613 / 0.46121 / 0.25115 / 0.21464 / 0.48865 |
| `a6de9bd760c43a2e` | separate activation-process native parser, one CSS control, shaped link | 1 | 0.09027 / 0.47334 / 0.18639 / 0.16738 / 0.44881 |
| `a6de9bd760c43a2e` | full-process native parser rendezvous, one CSS control, shaped link | 1 | 0.20120 / 0.45172 / 0.24339 / 0.20945 / 0.49445 |

None of the rejected rows improved the early and whole-flow views together.
They are not timing constants to carry into production. The fixed dwell
remains an opt-in research mechanism while event-driven or measured-network
alternatives are evaluated.

The first predeclared size matrix exposed a provenance mismatch rather than a
fixed-dwell robustness result. Safe artifact `f07dc0cb1e282738` used dirty
revision `3d005bd87721599c99b1ad5c20516243e5961e51` and libxul digest
`8fe40a8763fa79e11ccf2ba9a0864288aa2c9c25ae515b36410d362211f3d312`.
The two size artifacts used the later committed binary
`59ec40bb48fe587f445c924600c3dbf4698ef414e9c5d04cb98e4ec78d5ab6b0`,
whose six-resource arm waits for handshake confirmation but has no dwell; its
16-ms setter belongs to the separate `document-handshake-confirmed` mode.
Therefore the 0.33793/0.41965 and 0.42522/0.38674 pairs are retained as valid
confirmation-gate measurements, but they neither validate nor reject the
fixed-dwell candidate. Fixed-dwell size testing must first rebuild an
explicitly identified arm and record its binary digest. Results from different
mechanisms must not be compared as a robustness matrix merely because the arm
label was reused during an uncommitted experiment.

The corrected fixed-dwell size screen rebuilt the six-resource arm explicitly
with `SetProxyPreambleHandshakeDwell(16)`. Both new artifacts identify NaiveFox
build ID `fa86f083ff437943123733a262ce02ff` and libxul digest
`eb462bba4c0ffe345bbc5961b5a9d90b70b55ae65b3850dc7ad51715b9c181b7`.
At a 65536-byte inner-workload page base, packets 17--32 measured 0.13934 and
whole flow 0.37883; at 1048576 bytes they measured 0.18591 and 0.36313. Both
sizes retain
the main advantage over `document-start-overlap`, whose corresponding values
were 0.63512/0.45239 and 0.60016/0.45256. The tradeoff is consistent and
limited but real: candidate packets 1--16 exceeded control by 0.05848 and
0.05072, while 250 ms exceeded it by 0.01797 and 0.00784. Thus inner-workload
size alone does not invalidate the dwell, but these four-block diagnostics do
not vary the outer resource bodies, answer slower-link or RTT robustness, or
promote the timer to default.

The predeclared shaped-link run rejects the fixed dwell as a general default.
Artifact `a8977a8f77e3e129` uses the same identified 16-ms binary at the default
page size, with a verified 20-ms one-way delay, 20-Mbit/s loopback rate, and
receive-side timestamps. Packets 17--32 regressed to 0.55600, only slightly
below the `document-start-overlap` control's 0.60207, and whole flow regressed
to 0.44047 versus 0.45630. Packets 1--16, 1--32, and 250 ms were effectively
near the control rather than retaining the localhost advantage. Firefox A/B
whole durations remained close (means 541.7 and 547.6 ms), so this is not
explained by a broken control envelope. The opt-in fixed dwell remains a useful
laboratory oracle, but a production successor must derive release from actual
transport progress or native scheduling under the current path conditions.

An event-driven attempt to stop all still-active outer resource requests on
the first positive client-to-target tunnel application bytes is also rejected.
One-block shaped diagnostic `54ea85af8f8ade5a` used the same 20-ms one-way,
20-Mbit/s profile. It regressed packets 17--32 to 0.63746 and whole flow to
0.51192, while whole server bytes still exceeded the Firefox midpoint by
724090 bytes. By the time the inner application signal reached the main
thread, the localhost origin and H3 stack had already queued the large cover
bodies ahead of `netem`; cancelling their channels could not withdraw those
datagrams. The result rules out late channel cancellation, not causal
handoff generally. A viable successor must bound or suspend response-body
delivery before QUIC accepts the excess bytes, then use observed transport or
application progress to release or retire that bounded cover.

Three earlier resource-stop boundaries and one transport gate refine that
negative result. Stopping each channel at `OnStartRequest` in
`4f6a2d5f9d5d7f7e` was still too late: whole server bytes exceeded the Firefox
midpoint by 723282 bytes. Stopping all six channels as soon as H3 reported all
request HEADERS sent in `b269891ca820c21c` reduced the excess over the matched
`document-start-overlap` arm from roughly 73 KiB to 1231 bytes, but packets
17--32 remained 0.49624 and whole flow regressed to 0.55319. Delaying that stop
until the first resource `NS_NET_STATUS_RECEIVING_FROM` in
`79bd0652b13f9da6` admitted one path-controlled congestion flight, about
42.8 KiB beyond the control, without improving the phase: packets 17--32 were
0.53469 and whole flow 0.54446. These are one-block diagnostics, but the large
volume and phase effects are sufficient to reject the three boundaries.

Artifact `3ac531854f16583d` also replaced the wall-clock dwell with a causal
transport gate: the root transaction remained queued until Neqo's transmitted
packet counter proved that one packet had been emitted after handshake
confirmation. Combined with the first-response stop, this improved packets
1--16 and 250 ms descriptively, but packets 17--32 regressed to 0.61024 and
whole flow measured 0.49282. Thus neither a generic next socket turn nor an
actually transmitted post-confirmation packet reproduces the localhost
16-ms result on the shaped path. No timer, byte threshold, cancellation, or
transport-gate code from these diagnostics is retained.

Removing the six-resource arm's handshake-confirmation gate is retained as the
next production-oriented candidate. Packet-sequence inspection showed that on
the shaped path direct Firefox sent its navigation request near 46 ms, before
transport confirmation, while the gated preamble did not send its root request
until roughly 104 ms. The gate therefore introduced an RTT-dependent phase
error that the localhost 16-ms dwell had hidden. The replacement uses ordinary
H3 transaction scheduling; it contains no timer, RTT estimate, packet-count
barrier, body-size rule, or response cancellation.

The one-block shaped screen `783fb4014bc2c827` measured 0.30808 for packets
17--32 and 0.40084 whole. Four-block follow-up `9041afc666c4d27a` corrected the
optimistic early estimate but retained the material advantage: 0.41939
[`0.38538`, `0.47197`] for packets 17--32 versus control 0.58280, and 0.37755
[`0.36902`, `0.39015`] whole versus control 0.44734. The 250-ms view paid a
0.02960 descriptive penalty. Unshaped artifact `98ff5b58e8acabbf` measured
0.42408 packets 17--32 and 0.40104 whole, so it gives up the fixed dwell's
fixture-specific localhost advantage while avoiding its shaped-link collapse.
Both four-block runs identify NaiveFox build ID
`22e819b8198cdbefef03759293a2b6d1` and libxul digest
`10233fa0a10e43bb6f993d84d9b5d01205ee1f5597d43dac00876abf9628a284`.
This candidate is retained because its scheduling rule generalizes across RTT;
its roughly 0.42 packets-17--32 residual remains the next optimization target,
not an acceptable endpoint.

The next experiment split native parser discovery from image-channel
activation without using elapsed time or response progress. Stylesheet and
script channels still open in the parser callback; the four image channels are
prepared there and opened together by the next ordinary main-thread task. The
one-block shaped diagnostic `52eb19521edabbc2` was optimistic. Four-block
artifact `3db870baccdd8047` measured 0.42302 for packets 17--32, statistically
unchanged from the ordinary-scheduling candidate's 0.41939, but improved
packets 1--16 from 0.21975 to 0.14180 and whole flow from 0.37755 to 0.35352.
The same binary on unshaped localhost in `69ee3fd2559c47e1` measured 0.43472
for packets 17--32 and 0.41992 whole, small descriptive regressions from
0.42408 and 0.40104. All three artifacts identify NaiveFox build ID
`7304222bbd34555aef8b8526c6fc70f9` and libxul digest
`04f77b4941775bb7d3c3a7a093e5e8a9ea1096de6f80e0c4c214e633178731d5`.
The split is retained as an experimental scheduling primitive because it
materially reduces shaped whole-flow residual without encoding fixture
latency, bandwidth, or resource size. It is not promoted to default by these
screens: packets 17--32 remain unresolved, and the localhost whole-flow cost
needs to be recovered. Follow-ups should vary the image release batches on
ordinary event-loop turns and reject any schedule that only moves the shaped
whole-flow score.

The first such follow-up is rejected. It opened two images on the first
deferred main-thread turn and one image on each of the next two turns, matching
the `2+1+1` grouping seen in one decrypted Firefox trace without adding a
timer. The first attempted capture, private failure artifact
`8e5e218c073f4b6f`, stopped after its first candidate because the validator
incorrectly required every deferred image open to precede even stylesheet and
script `WAITING_FOR` callbacks. The log proved the intended `2+1+1` execution;
the validator was temporarily corrected to require causal ordering per stream,
and the capture was rerun. Safe one-block artifact `f9a240071240a55b`
measured 0.37870 for packets 17--32 and 0.38823 whole. This did not improve
both target views over the one-turn diagnostic's 0.24945/0.36747, so no
four-block confirmation was spent. The binary identified build ID
`7477b35b421bf7f30eaa264106fa625c` and libxul digest
`9a1a314167aeaaa2cec3277efa671335c80605a35bf237ae1cbbbe968368c43c`.
The batching code and its temporary validator relaxation were removed; the
single next-turn image release remains the working candidate.

Exact User-Agent parity does not repair the remaining phase. Decrypted traces
showed that every candidate GET used the otherwise native
`Mozilla/... Gecko/20100101 /156.0a1` string because the lean product has no
application UA name, while same-base Firefox used the corresponding
`Firefox/156.0` token. This affects every QPACK/HEADERS block independently of
RTT and resource size, so it was combined with the retained next-turn image
schedule. One-block shaped artifact `f5cfa9eff387313d` regressed packets
17--32 to 0.62333 and whole flow to 0.39800; the earlier isolated UA screen's
low packets-17--32 value therefore did not compose with the robust scheduler.
The binary identified build ID `acb2989186801906f1b1936d4fc9c4da` and libxul
digest `a220cfc201cc62361a82322b8570bf5b5ffae0f96bfc6c7bddf8f9901fb06701`.
The temporary HTTP-handler override was removed. `Alt-Used` and
`Sec-Fetch-User` remain untouched as well: the former must come from an actual
Alt-Svc mapping and the latter from a genuine user navigation, so fabricating
either would change semantics rather than recover native scheduling.

Waiting for both blocking-resource response HEADERS is also rejected. The
causal rationale came from the decrypted reference, where image requests
followed the stylesheet and deferred-script response HEADERS. The candidate
kept the existing next-turn image activation but delayed CONNECT admission
until both stream 1 and stream 2 had a successful response start. This uses no
body progress or fixed delay, but one-block shaped artifact
`906de419f0b1605d` regressed packets 17--32 to 0.53957 and whole flow to
0.37942. The binary identified build ID `064783d96e998ba682750149381d4d92`
and libxul digest
`79c52c4ab62be2e8033694b8d241fd5b394410133aa69fc8c3c9317e2da3afc8`.
Additional response waiting therefore moves CONNECT in the wrong direction on
the shaped path and would impose an avoidable slow-server penalty. The
blocking-HEADERS condition and its temporary lifecycle label were removed.

Releasing at the opposite edge, before any resource response HEADERS, is
rejected more strongly. The six native channels and their real H3 transactions
were still required, but CONNECT was admitted as soon as all request
transactions reported `NS_NET_STATUS_WAITING_FOR`. This rule has no
slow-server wait and no body-size input. Nevertheless one-block shaped
artifact `e0780953cbebd8a4` regressed packets 17--32 to 0.69741 and whole flow
to 0.47965. The binary identified build ID
`8072c68d5db43c67e0af4af1fb3cb879` and libxul digest
`a109abf36fe551224012910accbb91c64a254a77f8f7b393df0a4ce83d922d26`.
Together with the two-HEADERS result, this brackets the useful admission
boundary at the first resource response HEADERS: moving CONNECT by a whole
network event in either direction is harmful. The all-request-committed label
and release were removed; later work should vary task scheduling at the first
HEADERS boundary rather than add another network wait.

Deferring that first-HEADERS boundary by one ordinary main-thread task did not
survive replication. One-block shaped artifact `9c0b28cfd8163df8` looked
promising at 0.25761 for packets 17--32 and 0.36383 whole, so it was expanded
to the predeclared four-block screen. Artifact `5247083ce674bb8c` measured
0.42214 [`0.37848`, `0.45709`] for packets 17--32 and 0.38589
[`0.36954`, `0.40224`] whole, versus 0.59751 and 0.46798 for its matched
control. Relative to the retained next-turn image scheduler's four-block
0.42302/0.35352, the task handoff did not change the target packet window and
regressed whole flow; packets 1--16 also regressed from 0.14180 to 0.18055.
Only the first-250-ms view improved, from 0.19663 to 0.16720. Both artifacts
identify NaiveFox build ID `a354ee4827c7cb896758774641abf777` and libxul
digest
`ab98ac290569de1224f862ffc8c5a688c24b6ba544bbb8806b01eb3eb3a235d5`.
The task deferral, temporary lifecycle label, and validator allowance were
removed. The result also demonstrates why single-block improvements are used
only to decide whether a replication is worth collecting.

Waiting for the first successfully consumed resource body buffer is retained
as the stronger causal candidate. All six request transactions must still be
committed first, and the barrier is armed only after `OnDataAvailable` has
consumed one complete positive resource buffer. The rule has no elapsed-time
constant, byte threshold, packet count, response-size assumption, or wait for
the complete resource. It follows actual response progress, while failures,
short reads, empty responses, and response HEADERS without body data do not
release CONNECT.

One-block shaped artifact `e003c4bcdeae430f` measured 0.35266 for packets
17--32 and 0.38039 whole. Four-block replication `390cc24ccb6ef8c9` improved
all five requested views: 0.11745 [`0.07902`, `0.15589`] for packets 1--16,
0.29471 [`0.25981`, `0.32960`] for packets 17--32, 0.15293
[`0.11978`, `0.18609`] for packets 1--32, 0.16835
[`0.13830`, `0.19840`] for the first 250 ms, and 0.34363
[`0.32339`, `0.36381`] whole. Its matched control measured
0.19749/0.54571/0.24224/0.17254/0.43664. Relative to the retained next-turn
image scheduler's earlier four-block 0.42302/0.35352, the new boundary reduces
the target packets-17--32 residual materially and also lowers whole-flow
residual. Both new artifacts identify NaiveFox build ID
`89a4a90ceb64afd12b532dbe7ee67913` and libxul digest
`eb4ed5f1dd05c40d1ab17a4419f5ca238ef620854149b501ff0a40bc4b32dcb4`.
The mechanism and fail-closed lifecycle validation are retained for
inner-workload-size and unshaped robustness screens; these gate-sized runs do
not vary the outer resources or yet promote it to the product default.

Unshaped localhost artifact `4e4edd7c53b91735` used the identical binary and
measured 0.44214 [`0.37933`, `0.50496`] for packets 17--32 and 0.38250
[`0.37084`, `0.39415`] whole. The target packet window is descriptively close
to the prior next-turn scheduler's localhost 0.43472, while whole flow improves
materially from 0.41992 and also improves over ordinary scheduling's 0.40104.
Packets 1--16 were effectively tied with the matched control
(0.10945 versus 0.10739); the first 250 ms paid a limited 0.01567 descriptive
cost. The causal body-buffer boundary therefore retains its shaped-link gain
without repeating the previous scheduler's localhost whole-flow regression.
Outer-resource-size and slower-link screens remain required because the first
body callback depends on actual server and path progress even though it
contains no fixed time or byte threshold.

The first one-block inner-workload size screens are mixed and remain
diagnostic. At a 65536-byte page base, artifact `c9643a1e000b3c2c` measured
0.72418 for packets 17--32 and 0.45906 whole; at 1048576 bytes, artifact
`8925499c80a97688`
measured 0.41138 and 0.42297. In both cases the candidate still improved all
five views over its matched `document-start-overlap` control, whose target
pairs were respectively 0.88117/0.58286 and 0.58080/0.53749. The high absolute
residual at 65536 bytes is not acceptable evidence of size robustness, while
the simultaneous control movement makes a single-block rejection equally
unsafe. The smaller-size condition is therefore selected for four-block
replication before changing the mechanism.

That replication rejects the apparent small-inner-workload failure rather
than the mechanism. Four-block artifact `47adda8f2a4b7783` measured 0.33185
[`0.23825`, `0.41572`] for packets 17--32 and 0.31845
[`0.28458`, `0.34555`] whole at the same 65536-byte page base. All five views
improved over the matched control: 0.08814/0.33185/0.14079/0.13174/0.31845
versus 0.17985/0.55323/0.23522/0.16931/0.43635. The earlier 0.72418 one-block
target was therefore an unstable Firefox-envelope diagnostic, not evidence
that a smaller tunneled workload deterministically releases CONNECT at the
wrong phase. Outer-resource and slower-link replication are still needed.

The large inner-workload replication also retains the candidate. Four-block
artifact `07c422cacf441cd1` measured 0.39571 [`0.29175`, `0.49966`] for packets
17--32 and 0.36178 [`0.33620`, `0.38808`] whole at the 1048576-byte page base.
All five views again improved over the matched control:
0.14312/0.39571/0.18047/0.15179/0.36178 versus
0.20947/0.63845/0.26754/0.16318/0.48312. The target residual is higher than
the default-size 0.29471/0.34363 and small-size 0.33185/0.31845, but it does
not collapse as the inner workload duration grows. The historical claim that
this validated first-resource-body admission against outer resource duration
was too broad: those outer bodies did not change. Together the two four-block
endpoints reject only a fixed inner-workload-size explanation; outer resource
sizes and lower bandwidth or higher RTT remain separate robustness axes.

The slower-link screen retains the causal candidate where the fixed dwell had
failed. One-block artifact `d62dc8f4ca95d6e1` at 50-ms one-way delay and
5 Mbit/s measured 0.38089 for packets 17--32 and 0.29532 whole. Four-block
replication `4606cb015d86e68a` measured 0.42424
[`0.26067`, `0.58780`] and 0.31681 [`0.28349`, `0.35695`], versus matched
control 0.73615/0.48809. The wider packets-17--32 interval records real
slow-path variability, but its upper endpoint remains below 0.6 and the mean
retains a large improvement. All other views also improve:
0.09429/0.16478/0.07301 for packets 1--16, packets 1--32, and 250 ms, versus
control 0.21167/0.29908/0.18354. These runs use the same build ID and libxul
digest as the default-size, localhost, and size screens. The event-driven
boundary therefore generalizes across the tested RTT/rate range without
encoding either value; broader network and server-TTFB coverage remains future
validation rather than a reason to add a fixed pause. Together with the
default-size, unshaped, and two replicated size endpoints, this completes the
predeclared robustness matrix and promotes the retained six-resource mode as
the implicit SOCKS-only H3 default. HTTP CONNECT and mixed listeners retain
the ingress-safe `document-start-overlap` control, and explicit preamble
configuration remains authoritative.

Releasing CONNECT after only the first successfully consumed resource byte is
rejected after replication. This moved the barrier inside the first positive
`OnDataAvailable` callback: one byte was read successfully, CONNECT was
admitted, and only then was the rest of that callback drained. It therefore
tested whether the Necko callback size itself caused the remaining target
distance, without adding a timer, waiting for a complete response, or requiring
a server-dependent body size. One-block shaped artifact `d21d819300a31433`
measured 0.24021/0.46981/0.29011/0.24730/0.47484. Four-block replication
`178f43ceda873438` measured 0.16215 [`0.12115`, `0.18980`] for packets 1--16,
0.30412 [`0.24145`, `0.33866`] for packets 17--32, 0.17515 [`0.13674`,
`0.20065`] for packets 1--32, 0.15015 [`0.13084`, `0.16092`] for the first
250 ms, and 0.34798 [`0.32732`, `0.36864`] whole. The target pair does not
improve the retained complete-buffer candidate's replicated 0.29471/0.34363,
while packets 1--16 and 1--32 regress from 0.11745/0.15293. Both artifacts
identify NaiveFox build ID `3464761a0c1b92b78d8459f1f1232e16` and libxul
digest
`3c7df4963e6efe05b7a883485db9c89707704b46bfa75d2c84fcbb9d96902560`.
The byte-level state and lifecycle labels were removed; admission again
follows successful consumption of the complete first Necko resource buffer.

Releasing after the first successful bounded input-stream read is also
rejected. The resource drain loop requested at most its existing 4096-byte
scratch capacity, accepted any positive short read, logged the actual returned
length, and admitted CONNECT before draining the rest of the callback. Thus it
tested an intermediate input-stream boundary without requiring a 4096-byte
body or waiting for resource completion. One-block shaped artifact
`5e675ae5652db1fb` measured 0.16059/0.43240/0.21810/0.17386/0.38263. Neither
packets 17--32 nor whole improves the retained complete-buffer candidate's
replicated 0.29471/0.34363, and the early packet views are also higher. The
binary identified build ID `7d0dc259cb8d106445836d2d7df4e24e` and libxul
digest
`d8be3d8949b82fbcd612715d26a481f3b0a9a69de0621424cb74a3fd2bcd88e3`.
The read-level state, byte diagnostic, and validator markers were removed.
Together with the replicated byte-level result, this rejects callback-drain
granularity as the next optimization axis; the complete first buffer remains
the simpler and slightly stronger boundary.

Selecting the deferred script's first body buffer instead of the first ready
resource is rejected. The semantic stream-2 rule avoided a timer and byte
threshold, but one-block shaped artifact `d25a8910b028a6d5` regressed packets
17--32 to 0.46535 and whole flow to 0.40645; the first 250 ms also regressed to
0.22191, above its matched control's 0.20256. This is worse in both target
views than the first-ready-resource candidate's one-block 0.35266/0.38039 and
replicated 0.29471/0.34363. The binary identified build ID
`28ab359279fe717eeb2c33df8d60556e` and libxul digest
`536b1ee21f409a357bbec3b3cd20ec83a85da4995146ebf5e3069671f557425a`.
The stream-specific condition and lifecycle labels were removed; admission
again follows whichever valid resource delivers body data first.

Using that same first body event to open the four prepared images is also
rejected. This attempted to reproduce the decrypted Firefox ordering in which
image GETs followed blocking-resource response progress, while retaining real
channels and avoiding a time or size constant. Instead one-block shaped
artifact `4a893926cca282f9` regressed packets 17--32 to 0.51989 and whole flow
to 0.47166; packets 1--16, 1--32, and 250 ms also rose to
0.26798/0.32041/0.32007. Its matched control measured 0.64491/0.52655 for the
two target views, so the arm still differed from the baseline control but was
clearly worse than retained first-body admission with next-turn images. The
binary identified build ID `bf1fbfed66c17fe744a7d4c0266e8669` and libxul
digest
`a338ef2b94b96c24b16994aa3097c608555289d07d46f33a0170c28886e84a32`.
The body-triggered image release and validator ordering were removed; image
activation again occurs on its independent ordinary main-thread turn.

Giving the tunneled CONNECT document-equivalent H3 priority is rejected.
Upstream Gecko maps an ordinary `TYPE_OTHER` transaction to urgency 4, while
the direct Firefox navigation in the decrypted trace carried `priority: u=0,
i`. The experiment combined `UrgentStart`, `PRIORITY_HIGHEST`, and incremental
delivery only for the exact six-resource H3 body-buffer arm, producing that
same priority value without a timer. One-block shaped artifact
`ee1d57bb7b7817f0` regressed packets 17--32 to 0.62412 and whole flow to
0.43876; the other views rose to 0.27480/0.33962/0.30322. The binary identified
build ID `cc5666f97b91aa87a297258769c0e408` and libxul digest
`e84a1d875957baf7234fe4e3329d1d11b988523c6e9baa83789c21cfc13df45e`.
CONNECT carries a document but also competes with the outer cover streams, so
the direct document priority over-promotes it. The new parameter, class flags,
and priority marker were removed; ordinary CONNECT priority is retained.

Splitting image activation `2+2` across two causal events is rejected. Streams
3--4 opened on the existing next-main-turn boundary, while streams 5--6 stayed
prepared until the first valid resource body buffer; only then did they open
and commit before CONNECT admission. This avoided every timer, byte threshold,
and complete-resource wait, but one-block shaped artifact
`c276f92a62ecafdd` measured 0.44772 for packets 17--32 and 0.45058 whole,
worse than the retained candidate's replicated 0.29471/0.34363. Its packets
1--16 and first-250-ms distances, 0.29429 and 0.28711, also exceeded the
matched control's 0.26596 and 0.20538. The experimental binary identified
build ID `2150abeda012a918bdb326ffa2ad35d0` and libxul digest
`13eac5dee0318aa619fb9d0a9c54a4a2b07441e1298e0792719cebe9d8a7e15a`.
The partial body-triggered image release and its validator ordering were
removed. The result also rejects treating the incomplete decrypted trace's
apparent image grouping as sufficient causal evidence.

Combining all-resource next-turn activation with first-body-buffer CONNECT
admission is rejected. The stylesheet, deferred script, and four images were
all prepared during parser output and opened together on one ordinary main
thread turn; CONNECT still waited for the first valid resource body buffer.
Although each boundary was event-driven, their composition produced the worst
recent target result: one-block shaped artifact `e26fad3d3d44af3c` measured
0.71720 for packets 17--32 and 0.49797 whole, versus the retained candidate's
replicated 0.29471/0.34363. Packets 1--16, packets 1--32, and 250 ms were also
0.22284/0.32656/0.24533. The binary identified build ID
`d0b5461df5032118d8d786feb737dae2` and libxul digest
`51010bd3f4df3fb69c4d0f4c15b48fbf623d60d9ac6d5e5e80f228770079f2bb`.
The all-resource deferral and generalized validator markers were removed;
stylesheet/script activation remains in the parser callback and only images
use the following main-thread turn.

Waiting for first body buffers from two distinct resources is rejected. The
boundary counted at most one successfully consumed buffer per response, so it
did not depend on elapsed time, buffer length, or full resource completion and
remained reachable for both small and large resources. One-block shaped
artifact `dcbd84d7270ad4d2` measured 0.29712 for packets 17--32, effectively
the retained four-block candidate's 0.29471, but whole flow regressed from
0.34363 to 0.39582. Its other views were
0.12912/0.17971/0.18160. The binary identified build ID
`1086f63b07794dd90ff8f74b69d36489` and libxul digest
`5e240d15097355e6bd0a129171d9988f327d167332ff0993b87626281f01b541`.
The per-stream progress accounting and second-resource lifecycle markers were
removed; the first valid resource body buffer again admits CONNECT.

A cross-process parser-cadence rendezvous is rejected after replication. This
experiment mirrored the root response into the existing persistent native
activation child while the parent lean parser independently discovered all six
resources. CSS/script channels stayed prepared until the child reported its
stylesheet descriptor and that URL matched the parent's CSS exactly; images
then opened on the following main-thread turn. CONNECT retained the first-body
boundary. Thus the new ordering used actual parser and cross-process IPC work,
not a timer or resource-size threshold. The first attempt, private failure
artifact `983af062e23d11a3`, failed closed before the process route started
because the page mode's parent parser target was rejected by a CSS-only process
precondition. It timed out and produced no admissible distances; the
precondition was corrected before measurement.

One-block shaped artifact `e37b8b45df2c72f7` looked promising at packets
17--32, measuring 0.23626, but whole flow rose to 0.36922 and 250 ms to
0.19669. The predeclared four-block replication `a0ead86d96ae38c9` did not
retain that target gain: packets 17--32 measured 0.32005 [`0.26996`,
`0.37014`], worse than the retained candidate's 0.29471 [`0.25981`,
`0.32960`]. Whole flow was effectively unchanged at 0.34415 [`0.31509`,
`0.37297`] versus retained 0.34363 [`0.32339`, `0.36381`]. The replicated
other views were 0.12889/0.16100/0.15227 for packets 1--16, packets 1--32,
and 250 ms. Both valid artifacts identify build ID
`ae852c7c0e6698d875e921452f25b346` and libxul digest
`6a6d141cb374a0694e2650aef0d03c70bf9891ce16c12ac89788b39d02e40971`.
The extra process startup, root mirroring, rendezvous state, and generalized
validator lifecycle were removed; their complexity did not improve either
replicated target view.

Removing duplicated image-cover bodies is rejected in both tested forms. A
first attempt changed the four image requests to standard `HEAD` while leaving
CSS/script as `GET`, so first-body admission remained reachable. Private
failure artifact `4a11c4f36f4af40c` showed all six transactions committed and
CONNECT succeeded, but the H3 HEAD streams never delivered the terminal Necko
callbacks required for a normal six-resource drain before the fixed cutoff.
The harness therefore failed closed and published no distances; weakening the
drain contract to accept those hanging channels was not considered.

The follow-up retained `GET` and requested `Range: bytes=0-0`, which completed
normally and would fall back to full bodies on an origin that ignored Range.
Despite removing almost all image response payload without a timer or a
resource-size-dependent completion rule, one-block shaped artifact
`cdf15c6b3cc11142` regressed packets 17--32 to 0.47585 and whole flow to
0.42462. Packets 1--16, packets 1--32, and 250 ms also rose to
0.20619/0.23629/0.20118. The binary identified build ID
`a485895d17c1e5964fdbd488362870c3` and libxul digest
`944e7468380d2be8bac89df91da8ee0a3d7b07adc0f082ff30415a091d19a21a`.
The HEAD/Range mutations and their validator markers were removed. The result
shows that retained whole-flow behavior depends on the natural multiplexed
cover-body shape, not merely eliminating duplicated bytes.

Separating outer CONNECT readiness from local SOCKS success is rejected. The
retained first resource body buffer still opened CONNECT, so the natural root,
six cover requests, and CONNECT transaction were unchanged. `TunnelReady`
instead waited until a second distinct successful cover response had made
body progress; a normally completed empty response also counted, avoiding a
fixed byte minimum and preserving reachability for zero-length resources. This
causal gate follows network pacing instead of wall-clock time, but one-block
shaped artifact `bfd151c6ae8b307f` regressed packets 17--32 to 0.56947. Whole
flow was 0.38174 and the remaining views were
0.12217/0.24059/0.21827. The binary identified build ID
`09690181c93c9a2530add74f51d1cc01` and libxul digest
`d11ecf6711f0577cb58b269155b35e3f5a26d728f966eaf2a847791485328024`.
The secondary preamble callback and SOCKS admission state were removed. This
result shows that holding the inner browser after CONNECT reorders the target
window much more destructively than moving CONNECT itself to the same second
resource boundary.

A moderate H3 CONNECT priority is also rejected. Gecko maps a generic channel
to urgency 4 and the `Unblocked` class to urgency 3. Because non-incremental
urgency 3 is the HTTP Priority default, this experiment changed local H3
scheduling without adding a Priority request field, unlike the earlier
document-equivalent urgent experiment. One-block shaped artifact
`6f3abdd953241f36` nevertheless measured 0.67590 for packets 17--32 and
0.44583 whole; its other views were 0.28571/0.35053/0.27983. The binary
identified build ID `5dfb91f3521b088e904b9b97976fce6f` and libxul digest
`6122c5a00c58b7a7e0e8900764bb0bc8a4b323913eb6c33315d9d7132d11ca9b`.
The `Unblocked` class assignment was removed. Together with the rejected
urgent CONNECT arm, this rules out simple CONNECT promotion as the next
default direction.

Selectively reducing only the largest, third image cover is rejected after
replication. This kept the first two image bodies and final API image intact,
while requesting `Range: bytes=0-0` only on stream 5; an origin ignoring Range
still returned its full representation. One-block shaped artifact
`516a52ecdf7325fe` initially looked promising at 0.20686 for packets 17--32,
with 0.38629 whole. The predeclared four-block artifact
`3ccc3e566c17bf55` did not reproduce it: packets 17--32 measured 0.38692
[`0.32087`, `0.45108`] and whole flow 0.36567 [`0.32993`, `0.39984`], both
worse than retained 0.29471/0.34363. Its other views were
0.13225/0.17637/0.14720. Both artifacts identify build ID
`faf694cd8abf0bee3cd5a2881496842e` and libxul digest
`4c332156e983765b277039439ddf57b4b498b871f90bb3b3cf0e3008b7c9c96d`.
Server-byte diagnostics remained close to the full-body retained arm, so the
fixture ignored the range and the single-block movement came only from the
extra request-header/QPACK shape. The selective header was removed; neither
wholesale nor one-stream Range requests improve the replicated target views.

Delaying image activation by half the measured root request-to-response
interval is rejected. This replaced a fixed dwell with a per-connection
`nsITimedChannel` observation and fell back to ordinary next-turn activation
when timing was unavailable. Private failure artifact `67ac2a4d35b104a7`
first exposed that the measured half-flight was 62 ms on the nominal
20-ms-one-way profile; CSS/script therefore committed before the delayed image
opens, and the original strict lifecycle validator correctly refused that new
ordering. A temporary validator accepted only the explicit positive H3 timing
marker and the exact split order (blocking commits, four image opens, four
image commits), without weakening count or drain checks. The resulting
one-block shaped artifact `b4404c4a0ed5af8f` measured 0.43308 for packets
17--32 and 0.37198 whole, with other views
0.11576/0.18672/0.17680. Its binary identified build ID
`269e859d236cac7d6f240bbf54740b79` and libxul digest
`fd77fac46590b5df6257c0543463c9fc2f5e28cef4d71e9845148c1a05e3c8fd`.
The timing state and temporary validator branch were removed. The measured
interval includes connection/queue work and is not an RTT estimator; dividing
it by another fitted constant would recreate the fixture-specific timing
problem this experiment was meant to avoid.

Main-thread idle-queue image activation is rejected. All four images remained
prepared together, but their release runnable used Gecko's
`EventQueuePriority::Idle` instead of an ordinary next-turn dispatch. This
introduced no timer, byte threshold, or resource completion dependency, yet
one-block shaped artifact `c3e2674327356bfc` regressed packets 17--32 to
0.66662 and whole flow to 0.46435. Packets 1--16, packets 1--32, and 250 ms
were also 0.24089/0.30821/0.22581. The binary identified build ID
`628395e375ae5a29ee6179e4e11c555e` and libxul digest
`cc8dc4c497fcdf48ff6ec2a7b23e6bafd0352e13b5060d06ceebf763adf381fb`.
The idle dispatch and its temporary exact lifecycle marker were removed.
Yielding behind normal network callbacks is much coarser than ordinary parser
cadence and is not suitable as a product scheduling cause.

Sequential image activation at request-commit boundaries is rejected. The
first prepared image still opened on the ordinary next main-thread turn, but
each remaining image opened only after the preceding image emitted Necko's
`WAITING_FOR` request-commit status. This was a causal chain with no timer,
response-size threshold, body wait, or bandwidth estimate, and the strict
temporary lifecycle validator required the exact open/commit alternation.
One-block shaped artifact `78cbd9ef5ace8048` measured 0.37128 for packets
17--32 and 0.45118 whole; packets 1--16, packets 1--32, and 250 ms also rose
to 0.30343/0.30186/0.28342. Its binary identified build ID
`0f7fc9070bdf29f71a3f2819d3aac833` and libxul digest
`c05045cac8f79043c6776a07ee752c3cf663972e87d8848f49796beb349ea8dd`.
The request-commit chain and temporary validator ordering were removed. A
transaction commit is deterministic but too coarse as a parser-cadence proxy:
serializing all four image submissions harms both the target window and the
whole-flow view.

Opening three images on the ordinary next turn and the final image on one
following main-thread turn is rejected. This approximated separate image-load
tasks without a timer or network/body dependency, while keeping the split much
smaller than the earlier `2+1+1` experiment. The strict temporary validator
required exactly streams 3--5 with `next-main-turn` cause and stream 6 with
`following-main-turn` cause. One-block shaped artifact `ef003b01f019fe12`
regressed packets 17--32 to 0.58293 and whole flow to 0.44871; its other views
were 0.30779/0.35812/0.31232. The binary identified build ID
`832d32cad6dad72e455ff6244008e97f` and libxul digest
`deb6c282de08da7094482573a9f1c3a01247517e7616b26a9340602ecc29fa35`.
The second dispatch and temporary validator marker were removed. Together
with the `2+1+1`, idle-queue, and request-commit results, this shows that even
a one-image event-loop split is too coarse for the desired H3 burst shape.

CONNECT admission after the first successful image response HEADERS is
rejected. The gate still required all six request commits, then used the first
2xx image response start as a causal, body-size-independent boundary. The
temporary validator required that exact image-header observation between all
commits and CONNECT admission. One-block shaped artifact
`d1c08c6718fa278f` kept packets 1--16 low at 0.11972, but packets 17--32 rose
to 0.38830 and whole flow was 0.38223; the remaining views were
0.17140/0.18762. The binary identified build ID
`17baed0a323625250d73534cc06beb36` and libxul digest
`f71697b265a4486de74890ee7c3d41b0b49a1dcbbeaf05ea9fa33f27b5eb0cd2`.
The image-header state and temporary validator marker were removed. Moving
admission from first body progress to a specific response class does not
improve the target window.

An incremental H3 CONNECT at unchanged generic urgency is rejected. The first
preamble-owned tunnel set Gecko's incremental class-of-service bit without
promoting urgency; this produced the standard `u=4, i` Priority signal and
allowed Neqo's incremental scheduling, while later SOCKS connections retained
their normal behavior. The first private run `0e250cd494490c3f` failed closed:
the implementation initially applied the bit to a second non-preamble SOCKS
connection too, and the exact validator rejected two markers. After scoping it
to the preamble operation generation, one-block shaped artifact
`e386025177b9432c` measured 0.42295 for packets 17--32 and 0.35493 whole; the
other views were 0.12355/0.17830/0.16060. Its binary identified build ID
`006056bd843c9e546c26872cab533982` and libxul digest
`a3233c9126b1adb1941c0b25665faf7385a6adce331e6d688d631c07d54685f9`.
The CONNECT API flag, generation scope, and exact marker were removed. Whole
flow remained close to retained, but DATA interleaving and the additional
Priority signal worsened the target packet sequence substantially.

Bounding H3 duplex-pump reads to Gecko's default 4096-byte network segment is
rejected. Ordinary H3 transactions ask for that quantum, whereas the product
pump historically reads up to 64 KiB; the experiment applied the upstream
runtime segment value symmetrically in both tunnel directions without timers,
Priority changes, or resource-size assumptions. One-block shaped artifact
`f9c4c26050a2988a` measured 0.36697 for packets 17--32 and 0.41194 whole,
with other views 0.23503/0.25550/0.24040. Its binary identified build ID
`c7d69296800b54f9e5f0d1b36fe5965b` and libxul digest
`658ac4fb91a913157197094b681c6df40d325779c7f9d36e35066f8cdb27e9d8`.
The pump quantum and diagnostic marker were removed. Sequential small writes
remain eligible for aggregation inside Neqo, so matching the ordinary read
size does not reproduce ordinary multi-stream packetization.

Demoting the first preamble-owned H3 CONNECT by one scheduler step is
rejected. The experiment left the stream non-incremental and changed only its
generic Gecko priority from `u=4` to `u=5`, so the already-committed cover
transactions could win H3 scheduling without any timer, RTT, throughput, or
resource-size constant. A strict temporary marker required exactly one such
CONNECT on the owning preamble generation and rejected the priority on every
later SOCKS connection. The first private capture
`7c50b7798ccafd5c` failed closed because the initial validator assumed that
the marker logged before the synchronously delivered preamble result; the
actual `AsyncOpen` callback logged the result before the call returned. After
correcting that diagnostic ordering, one-block shaped artifact
`3500eb894d951e57` measured 0.49920 for packets 17--32 and 0.47932 whole, with
other views 0.25290/0.28958/0.22799. Its binary identified build ID
`187868e93104742a431cd46725894c6c` and libxul digest
`bb8627aac5326cc30ae838e4c09aaf3a2d0c3c5748c09a5261cf712d6a1e3312`.
The priority flag and exact validator marker were removed. Both one-step
promotion and one-step demotion of CONNECT now regress the target window, so
further urgency-only tuning is not a useful direction.

Holding the four prepared image channels until both blocking requests reach
`NS_NET_STATUS_WAITING_FOR` is rejected. Decrypted packet inspection motivated
the experiment: direct Firefox's packets 17--32 contained the stylesheet and
deferred-script GETs followed by stylesheet response data, while the retained
candidate had already placed its four image GETs in that window. The new gate
depended only on CSS/script request commitment, then opened all images on the
next ordinary main-thread task; it did not wait for a response, body bytes,
elapsed time, RTT, or bandwidth. The strict temporary validator required both
blocking commits before the task and all four image commits afterwards.
One-block shaped artifact `7770ec81beea4b8a` nevertheless measured 0.39986
for packets 17--32 and 0.42113 whole, with other views
0.28935/0.29283/0.29333. Its binary identified build ID
`6163349c5fa7afdb18ac1a857435cb79` and libxul digest
`179fb8677622398e29caf982acc7f4fafb4731afa3c10f4921e9d2021f778661`.
Moving the image HEADERS out of the target window alone left mismatched ACK
and response-flight positions and worsened the 250-ms view. The request gate,
task, and temporary lifecycle validation were removed.

Forcing standard PLPMTUD on the page-mode outer H3 route is rejected.
Decrypted inspection showed that direct Firefox's server-side response flight
had advanced from 1280-byte QUIC packets to larger path-validated datagrams by
packet 18, while the retained preamble's dense response flight stayed at 1280
until roughly packet 92. The experiment removed the route's PMTUD opt-out for
both preamble and CONNECT, keeping one proxy identity and asking Neqo's normal
PLPMTUD algorithm to discover the actual path; it did not prescribe an MTU,
packet size, delay, resource size, or bandwidth. One-block shaped artifact
`c3d1643c1f966245` regressed packets 17--32 to 0.55231 and whole flow to
0.52470, with other views 0.31425/0.35320/0.29369. Its binary identified build
ID `7bbd9e285252c729dad1a826d134cd8e` and libxul digest
`36384253d67b6738ff033c195b2b9e5bf3369f96651aff2f02ab9fd2944c74fe`.
The additional probe and ACK transitions outweighed any later DATA-size
alignment. The route flag and exact validator marker were removed; production
must not force path probing merely to reproduce this fixture's packet size.

Deferring only the prepared script by one main-thread turn is rejected. The
stylesheet opened immediately, the script opened from the first queued task,
and the four already prepared images opened from the existing following task.
This introduced no timer, response gate, packet counter, resource-size check,
or link-rate dependency. One-block shaped artifact `5720930db5b46be2`
measured 0.12781/0.38475/0.19378/0.19891/0.35858: the whole-flow result stayed
near the retained candidate, but packets 17--32 were materially worse than the
retained four-block replication's 0.29471. The measured binary identified build
ID `9482377fbe41a310f96d94869eaccb8a` and libxul digest
`1694ba3b3513757d37cc2fb0c7f33e1426b8b048a7d45b0756c3c5d3f8bb8df6`.
The extra script task and its lifecycle marker were removed. A single
event-loop split is therefore insufficient to reproduce native parser/resource
scheduling and is not retained.

The existing separate-process native-parser paths were screened before trying
to generalize their IPC protocol from one stylesheet to the full six-resource
tree. In shaped paired artifact `a6de9bd760c43a2e`, the root-rendezvous,
activation-process, and full-process arms respectively measured packets 17--32
at 0.46121, 0.47334, and 0.45172, with whole-flow distances 0.48865, 0.44881,
and 0.49445. All three used the real streamed HTML body, native speculative
parser output, and event/IPC rendezvous rather than a time delay, but all were
materially worse than the retained six-resource candidate's replicated
0.29471/0.34363. The measured binary identified build ID
`9482377fbe41a310f96d94869eaccb8a` and libxul digest
`1694ba3b3513757d37cc2fb0c7f33e1426b8b048a7d45b0756c3c5d3f8bb8df6`;
the earlier deferred-script branch in that binary is scoped to the
resource-committed page mode and was inactive for these three controls. The
process boundary by itself therefore does not supply useful browser-like H3
pacing, so the larger full-tree IPC generalization was not implemented.

Publishing the complete six-resource lean-parser descriptor stream through the
full activation process is rejected in its all-at-once form. This follow-up
filtered non-network document-context descriptors in the child, transferred
the CSS, parser-blocking script, and four image descriptors with their native
fields over IPDL, opened each native parent channel as its paired process event
arrived, and admitted CONNECT only after the parser finished and all six
requests committed. It introduced no timer, RTT, packet-count, response-size,
or bandwidth dependency, and the strict validator proved six distinct ordered
descriptor identities and clean process/channel completion. One-block shaped
artifact `9448a2a3eb7ce638` nevertheless measured
0.24504/0.67761/0.31918/0.24062/0.47613. The full tree improved the 250-ms view
relative to the two process controls in that block, but its packets 17--32
distance was worst and whole remained high because all resource requests were
front-loaded. The measured binary identified build ID
`d80db43f07d97bdd93a9bc1e7a882b79` and libxul digest
`77724d918db91ab7b1ac849f350ef3105b86c1c9f8f0c4f3e40066dc13886fa2`.
The descriptor transport is useful diagnostic scaffolding, but immediate
publication of every discovered image is not a candidate default.

Releasing all four prepared images only after the parser-blocking script's
successful native `OnStop` is also rejected as a candidate default. This was a
causal response gate: slower transport or a larger script naturally postponed
the image wave, while no elapsed-time, RTT, packet, or byte threshold was
consulted. The validator accepted either ordering between final preamble drain
and target establishment so that small resources cannot invalidate a healthy
sample, but required script actor completion before the exact four image opens.
One-block shaped artifact `576d37b44e6dbb09` improved the eager full-process
result to 0.21196/0.33680/0.22121/0.19824/0.44306. Packets 17--32 were still
worse than the retained page candidate's four-block 0.29471 and whole remained
well above its 0.34363. Diagnostic signed packet sizes showed the four image
responses becoming another dense flight around packets 23--24 instead of the
two later direct-reference groups. The measured binary identified build ID
`4f6cbbaf6282135c2c7c87a4d7745688` and libxul digest
`04e91ec7013735bb76cea355159585262cb9219638ff02cae0c457b72daee21f`.
Script completion is a useful adaptive boundary, but one four-image wave is
still too coarse.

Splitting the images into two causal waves is rejected after direct paired
replication. The first two prepared images opened after successful script
`OnStop`; the second two opened only after both first-wave channels received
successful response headers. Thus body sizes could not accelerate the second
wave, while slower RTT or server response naturally delayed it, and no timer,
packet index, byte count, or fixture-specific resource length was used. In the
four-block shaped artifact `07829ee5bb0b5ad3`, this arm measured
0.13976/0.43070/0.18764/0.14583/0.39706. The retained page arm in the same
randomized superblocks measured 0.13185/0.37573/0.16462/0.13457/0.35488 and
ranked closer in every view. The two-wave binary identified build ID
`43873d9f2e5168673d6b87baa5a98ec0` and libxul digest
`a564f1152f5da8e7b221dd37c7dcc9dea3f5b351bdf0d29c2048bdc44814d8e5`.
All four samples passed strict process identity, descriptor-order, two-wave,
request-commit, and terminal-drain validation, so the rejection is numerical
rather than a lifecycle failure. The full-tree IPC and wave scheduler are
removed rather than retained as dormant product complexity.

A retained-candidate decrypted capture initially did not pass strict admission
and must not be treated as wire evidence. Private artifact
`20260828T220612Z-2af3b849` logged all six request commits, first resource body
progress on the deferred-script stream, successful CONNECT admission, and a
normal six-resource drain. A fresh reproduction,
`20260829T021624Z-605b5eb7`, exposed the actual harness defect: the generic tree
validator still expected exactly two asset responses for the dense page arm,
so it rejected all six correctly decoded responses with the same missing-header
message. Revision `bcf08666f1b7` gives this arm its explicit six-response
contract and adds a regression test; neither failed private trace enters the
passive table or validates a production change.

The corrected strict run passed as sanitized decrypted artifact
`20260829T021917Z-03764004`. It proves one QUIC identity and ClientHello, seven
ordered GETs, all six asset response HEADERS, one normal resource drain, and
CONNECT after every cover GET but before the final two response HEADERS. The
wire sequence identifies the remaining scheduling gap without exposing header
values: the candidate issued CSS/script 5.600/5.707 ms after its first H3 event
and all four image GETs by 6.062 ms. Direct Firefox issued CSS/script together
at 24.970 ms and its image group at 27.721--28.774 ms. Candidate CONNECT then
appeared at 7.567 ms; later CONNECT streams belong to additional inner-browser
connections and do not invalidate the first tunnel. The binary identified
build ID `b8dcfa8525b93374ed9e0c34cb5e344b` and libxul digest
`f2f2ab2cc9b0395be9b109fcf052e5108db3657b0b8593a985e0d79e2e70bb34`.
This rejects body-callback granularity as the explanation and motivates
separating early outer CONNECT establishment from the retained body-progress
gate for local SOCKS success, rather than fitting the observed millisecond gap.

Private event-trace diagnostic `20260829T031759Z-3c779253` intentionally added
verbose `nsHttp` logging to the retained six-resource candidate and published
no passive distances. The extra logging changed callback interleaving enough
that the first valid stylesheet body buffer was consumed after its own request
commit but before the four image commits; the product correctly waited for all
six commits before firing its barrier, while the harness incorrectly required
every commit before that first body marker. Validation now expresses the real
causal contract per stream: each resource opens before its own commit, the
body-producing stream commits before its body callback, and both the body event
and all six commits precede the barrier. A regression test accepts this valid
interleaving and still rejects body progress before the producing request
commits. The temporary verbose-trace arm selection was removed.

That separation is rejected after a one-block shaped screen. All six cover
transactions first had to commit, which opened the outer CONNECT stream; the
SOCKS success callback and tunnel pump remained explicitly blocked until the
complete first valid resource body buffer was consumed. Thus target connection
establishment could overlap cover responses, but no browser application bytes
could enter H3 early. The two gates were event-driven and retained the same
body-size, RTT, and bandwidth independence as the working candidate. Artifact
`5b393915a38fb0e4` nevertheless measured
0.22035/0.35901/0.22541/0.20450/0.39240. Packets 17--32 and whole do not improve
the retained candidate's replicated 0.29471/0.34363, while both cumulative
early views regress materially. The binary identified build ID
`7f2eef53c6845ab4849be9b19bfa0b51` and libxul digest
`97dd9e85108aac94812534e607c3c279cb1cb429d5974bab2d20ca8626251a6a`.
Strict lifecycle validation proved the intended outer-ready/local-wait order,
so the result rejects the extra early CONNECT scheduling work rather than an
accidental release of tunneled DATA. The secondary callback, wait state, and
temporary validator markers were removed.

Holding all four image requests until both stylesheet and deferred-script
response HEADERS is rejected after a one-block shaped screen. This boundary
was causal rather than timed: both blocking channels had to report successful
2xx headers, then one ordinary main-thread turn released the prepared images.
It therefore adapted to origin latency and resource delivery instead of using
a byte count, resource size, packet position, or fitted delay. That adaptation
was also the defect: under 20 ms one-way delay and 20 Mbit/s shaping the first
resource body arrived roughly 86 ms after the dense parser flush and the
second blocking response did not admit images until roughly 126 ms. Sanitized
artifact `92847815c44749d0` measured
0.18382/0.47777/0.27143/0.26629/0.49262. Packets 17--32 and whole are both
materially worse than the retained candidate's replicated 0.29471/0.34363,
so further replication would only spend samples on a mechanism already unsafe
for slower real connections. The measured binary identified build ID
`56ba4685c920014f3ac5f9429e7d95f4` and libxul digest
`51163a7c0807c1403cebabe253c55d24f5e6600b2b671ed930ad2f7179f53eac`.
The first collection attempt, private artifact `785da5e357ffbc05`, completed
the intended lifecycle but was rejected because its new valid ordering placed
the first CSS body buffer before the deferred-image gate; the validator was
corrected and its dedicated regression stayed green before the successful
rerun. No result from that failed artifact is used. The response-header gate
and temporary lifecycle grammar were removed.

Splitting the retained next-turn image activation into two event-loop waves is
also rejected. Streams 3--4 opened on the first ordinary main-thread task and
streams 5--6 on the immediately following task; CONNECT still required all six
commits and the first complete valid resource body buffer. Unlike the rejected
response-header gate, this adds no network wait and cannot accumulate an RTT
when resources or the connection are slow. One-block shaped artifact
`752d40ac53187d45` measured
0.24093/0.31250/0.23199/0.20919/0.44428. The p17--32 result does not improve
the retained replicated 0.29471 and whole regresses by about 0.10, indicating
that yielding between `AsyncOpen` calls does not usefully separate the later H3
DATA scheduling. The binary identified build ID
`97176048457701e28771d4601a4264f5` and libxul digest
`6f41be3b1a9feed091237b7742527d938e90392795f8b07049df388f14bade52`.
Upstream inspection with `searchfox-cli` also confirmed that the retained
channel semantics already match Firefox's normal priorities: stylesheet
`Leader`, deferred script `Unblocked`, and images `PRIORITY_LOW` with
incremental delivery. Arbitrary urgency tuning is therefore not a justified
follow-up. The wave counter and temporary validator grammar were removed.

Preconstructing the dense page's CSS and script channels before opening either
one is rejected in combination with the retained first-body CONNECT barrier.
The tree callback created all six channel objects without `AsyncOpen`; after
the script descriptor existed, one ordinary main-thread task opened CSS and
script back-to-back, and the existing next-turn task then opened all four
images. This is event-order based rather than a timer, resource-size threshold,
or response wait, and was intended to let Necko coalesce the two blocking H3
requests as the direct Firefox trace does. The lifecycle validator proved that
all channels were prepared first, both blocking opens preceded the image opens,
every request committed, and the first complete resource body still preceded
CONNECT. Nevertheless, shaped one-block artifact `1fb93ac539c0cff8` measured
0.21981/0.34961/0.25818/0.28214/0.46133. Both packets 17--32 and whole regress
against the retained replicated 0.29471/0.34363, so the mechanism is not a
candidate for size/slow-link replication. The measured binary identified build
ID `37f71f6fdd3b042e747ad79090e0041e` and libxul digest
`024d7d4c9a4c8184a73e0a28236bcf888e04e97f2f9e02a6c31c600ca5ae53d7`.
The preconstruction state and its temporary validator grammar were removed.

Stopping only cover responses that were still active at the first positive
target-to-client application payload is a safe no-op on the default shaped
profile, not a whole-flow improvement. The causal rule deliberately avoided a
timer: it selected only resource channels that already had successful 2xx
response HEADERS and were still running, never pending or unopened requests.
Small or fast resources could finish normally, while a slow or silent target
could not terminate cover traffic prematurely. One-block shaped artifact
`6f32c2baf9c1115f` (seed `2026082935`) measured
0.18774/0.43453/0.25554/0.27522/0.35880. Its sanitized terminal counters report
one natural-completion sample, zero aborting samples, and zero stopped
resources: all six cover bodies had completed before the target response. The
whole-flow diagnostic consequently still reports about 724 KiB more server
wire bytes than the matched Firefox midpoint, and packets 17--32 do not improve
the retained candidate's replicated 0.29471. The binary identified build ID
`37f71f6fdd3b042e747ad79090e0041e` and libxul digest
`febfafdc94318190f98dd1f2a09164b43e5b32988f8839a7a6073f382c7152f4`.
Moving this stop earlier would again make it depend on client request timing
and repeat the already rejected response-cancellation family; waiting for the
real target response is robust but too late to affect this workload. The stop
state, callback, and validator grammar were removed rather than retained as
unproductive product complexity.

Reducing the retained topology from six selected resources to four is also
rejected. The experimental cap kept the stylesheet, deferred script, and first
two images as ordinary native `GET` channels, retained next-main-turn image
activation and first-resource-body-buffer admission, and required all four
selected responses to complete normally. Later ordinary resource descriptors
were ignored only after the cap; unsupported CSP/referrer/parser events still
failed closed. Thus the experiment used a bounded topology count rather than a
response byte, elapsed-time, RTT, or bandwidth threshold.

The first private attempt, `4532515217d376a2`, published no distances. Its log
proved two blocking opens, two prepared images, and first body progress, but
the older exact-six parser contract rejected the fifth ordinary descriptor as
`NS_ERROR_FILE_TOO_BIG` and the operation timed out. The cap was corrected to
select the first four from the parser's complete seven-descriptor output while
preserving exact selected-channel validation. The successful one-block shaped
artifact `11363e69e10c73f7` (seed `2026082937`) then measured
0.29180/0.56690/0.32295/0.28668/0.49588. Packets 17--32 and whole flow regress
substantially from the retained six-resource replication's 0.29471/0.34363.
Whole server wire-byte excess over the matched Firefox midpoint fell only from
about 720.8 KiB to 712.5 KiB, so the omitted final two resources were not the
dominant whole-flow duplication, while their missing multiplexed requests and
responses damaged the early packet shape. The binary identified build ID
`37f71f6fdd3b042e747ad79090e0041e` and libxul digest
`f37c49d94fecc13f6f768e1ad5269f9bf9877f921a9b5221e0cfb8809d26e4bf`.
The four-resource allowance, selection cap, and temporary harness expectations
were removed; this result does not justify testing still smaller topologies.

Applying the retained six-resource native-parser topology to the HTTP CONNECT
listener was first screened without changing the implicit default. The
experimental arm kept the exact
`tree-native-parser-resource-committed-overlap` product mode, dense page,
six cached resources, 384-KiB budget, next-main-turn image activation, and
first-complete-resource-body admission used by the SOCKS candidate; only the
local ingress changes from SOCKS5 to HTTP CONNECT. It therefore introduces no
fixed pause, packet-index rule, response-size threshold, or measured-link
parameter.

One-block isolated gate `22509d99648b3070` (seed `2026082961`) first measured
the current `document-start-http-connect` control at
0.21680/0.73137/0.32695/0.24582/0.52378 and the tree treatment at
0.17979/0.60120/0.27555/0.29096/0.47956. Because a single QUIC ordering can be
misleading, six fresh randomized same-base blocks were collected next.
Artifact `695873adeb4f7f4b` (seed `2026082962`) measured the control at
0.09215/0.54761/0.18906/0.15156/0.43277 and the tree treatment at
0.08777/0.35793/0.15090/0.15903/0.41305. Thus packets 17--32 improved by
0.18968 and whole by 0.01971 while packets 1--16 and 1--32 also improved; the
first 250 ms regressed slightly by 0.00747. Every one of the six paired blocks
favored the treatment on packets 17--32 and whole (descriptive exact sign-flip
`p=0.03125` for each), but six gate blocks remain below the 30-block paired
inference minimum and cannot support an absolute verdict. Size and shaped-link
robustness must be checked before promotion or replacement of the canonical
four-row matrix.

A deliberately broad HTTP CONNECT document-barrier family does not beat that
tree mechanism. One isolated randomized block `062396842874c530` (seed
`2026082963`) compared request commit, response HEADERS, first complete body
buffer, an additional ordinary main-thread turn after each of those events,
and the tree treatment. In arm order, the five-view distances were:

| H3 HTTP CONNECT screening arm | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| `document-start-http-connect` | 0.20480 | 0.79587 | 0.32684 | 0.21093 | 0.51381 |
| `document-start-task-http-connect` | 0.20089 | 0.79061 | 0.32202 | 0.21640 | 0.50653 |
| `document-overlap-http-connect` | 0.19888 | 0.74201 | 0.31147 | 0.20236 | 0.50199 |
| `document-headers-task-http-connect` | 0.20232 | 0.73568 | 0.31820 | 0.20387 | 0.51003 |
| `document-first-buffer-http-connect` | 0.20153 | 0.73336 | 0.31345 | 0.20661 | 0.50500 |
| `document-first-buffer-task-http-connect` | 0.20663 | 0.74508 | 0.31869 | 0.20485 | 0.50277 |
| six-resource tree over HTTP CONNECT | 0.19516 | 0.66853 | 0.28654 | 0.20601 | 0.48066 |

The extra main-thread turn had no useful directional effect at any of the
three document boundaries. Response HEADERS and first body progress improved
packets 17--32 relative to request commit in that block, but remained well
behind the tree. A fresh four-block screen retained only the simplest
response-HEADERS variant, the current start control, and tree. Sanitized
artifact `b24fe9b411e9cca4` (seed `2026082964`) measured start at
0.11398/0.52914/0.19860/0.16573/0.43794, response HEADERS at
0.09509/0.46146/0.18541/0.17115/0.43099, and tree at
0.09779/0.35342/0.15104/0.16612/0.41673. The response-HEADERS barrier is a real
improvement over the current H3 HTTP default, but tree wins packets 17--32,
1--32, and whole while essentially tying start in the 250-ms view. These gate
screens remain descriptive and below the 30-block minimum. The late document
barriers and their task variants are retained as explicit harness aliases so
the negative family is reproducible, but they are not product-default
candidates and should not be repeated without a new mechanism hypothesis.

The HTTP tree treatment also passed the predeclared inner-workload-size and
slower-link directional screens. All runs kept the same product policy and
budgets; only the inner fixture input or namespace link profile changed:

| Safe artifact | Condition | Blocks | Start control | HTTP tree treatment |
| --- | --- | ---: | --- | --- |
| `2608e4200af64969` | 65536-byte inner-workload base, unshaped | 2 | 0.14352 / 0.58915 / 0.24531 / 0.18110 / 0.46113 | 0.12884 / 0.45060 / 0.19795 / 0.20407 / 0.44064 |
| `8d81953d90610c34` | 1048576-byte inner-workload base, unshaped | 2 | 0.18880 / 0.59725 / 0.25579 / 0.20983 / 0.44874 | 0.17881 / 0.44431 / 0.22058 / 0.22831 / 0.43562 |
| `ef1c22cb0e387413` | default inner workload, 20-ms one-way and 20 Mbit/s | 2 | 0.16877 / 0.59901 / 0.24823 / 0.13968 / 0.42727 | 0.09124 / 0.33052 / 0.14420 / 0.11115 / 0.30928 |

Packets 17--32, packets 1--32, and whole improved at both inner-workload bases.
The unshaped 250-ms view regressed by 0.023 and 0.018 respectively, while the
shaped profile improved every retained view, including 250 ms by 0.029. Safe
shaped metadata confirms receive-side capture after netem, the exact
20-ms/20-Mbit profile, eight successful proxy resets, and zero dropped or
offload-oversized captures. These two-block gates are not inferential.
Together with the six- and four-block default-size screens they reject a
tunneled-workload-size or localhost-speed explanation, but they do not vary
the outer fronting resources. The existing six-resource mode was still a
viable H3 HTTP implicit-default candidate, while an outer-resource size matrix
remains open. These screening rows were not spliced into the dashboard;
promotion required a fresh product build and canonical four-row campaign.

That promotion was completed at revision `aa4bd846af3a`. Omitted-preamble H3
now selects the same exact six-resource tree policy for SOCKS5, HTTP CONNECT,
and mixed listeners; explicit preamble fields remain authoritative. The lean
release product was rebuilt incrementally without a clobber or a Firefox
build, and the staged package passed the complete runtime verifier. Product
gtests passed 114/114 across 17 suites. The first staged verifier attempt is
retained as a failed integration-fixture check: its root still returned the
old text-only response, so the newly exact tree default correctly failed
closed rather than silently admitting CONNECT. Rewriting that test root to
the existing dense camouflage page restored the same documented stylesheet,
deferred-script, and four-image contract; both H2 and H3 SOCKS/HTTP config
tests and the full staged verifier then passed.

The fresh canonical artifacts `e026109117dd8141` (H2) and
`8bc66f8738d559b2` (H3), both seed `2026082968`, regenerated all four dashboard
rows together. The H3 superblock retained both historical request-commit
controls so the mechanism remained visible:

| H3 arm in the canonical superblock | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| SOCKS5 `document-start-overlap` control | 0.09075 | 0.58376 | 0.18757 | 0.13718 | 0.41942 |
| SOCKS5 six-resource tree default | 0.07392 | 0.39623 | 0.13837 | 0.15011 | 0.39149 |
| HTTP CONNECT `document-start-overlap` control | 0.08747 | 0.58210 | 0.18384 | 0.13771 | 0.39665 |
| HTTP CONNECT six-resource tree default | 0.06375 | 0.40952 | 0.13753 | 0.14458 | 0.39012 |

For HTTP CONNECT, the tree lowers packets 17--32 by 0.17258 and whole by
0.00653 relative to its contemporaneous control. Relative to the preceding
published HTTP default row, packets 17--32 fall from 0.55108 to 0.40952
(25.7%), packets 1--32 from 0.19621 to 0.13753, and whole from 0.40795 to
0.39012; packets 1--16 and 250 ms also improve in that cross-campaign
dashboard comparison. Within the new matched block, the tree trades a small
0.00687 increase in the 250-ms distance for the large non-overlapping packet
gain. All 60 H3 participants passed capture-drop, network-mutation, QUIC
origin, inner-H2, exact resource-commit, and normal-drain validation. Ten
smoke blocks remain descriptive and below the 30-block inference minimum, so
this promotes a robust default rather than claiming absolute
indistinguishability.

Several bring-up attempts produced no candidate result and must not be
reinterpreted as measurements. A clean research objdir initially tried to
download the pinned Go/Caddy fixture toolchain after entering the isolated
namespace, where public DNS is deliberately unavailable; fixture dependencies
are now prepared before namespace entry. Subsequent partial collections found
that the newly added HTTP alias was missing independently from the superblock,
feature-extractor, and lifecycle-validator CLI registries. Those collections
stopped fail-closed before analysis. The registries and a cross-registry parser
test were fixed before either successful artifact above was collected.

### H2 server and payload-framing follow-up

A later H2 campaign kept the canonical browser-page workload, inner HTTPS/H2,
same-base Firefox controls, MTU 1500, offload rejection, and isolated Linux
network namespace, but screened changes to the document boundary and the
Caddy/forwardproxy tunnel. Only packets 17--32 and whole were retained during
these one- and two-block screens. They are mechanism diagnostics, not new
dashboard rows or paired inference. The canonical H2 defaults and their
published rows remain unchanged.

The first HTTP-listener screen revisited next-main-thread-task admission after
the non-suspending implementation had been completed. One-block artifact
`0c0770517e13dc9c` ranked response-HEADERS plus one task best: packets 17--32
and whole were `0.58977/0.34708`, versus `0.66674/0.40737` for direct
first-buffer, `0.60401/0.39263` for first-buffer plus one task, and
`0.65518/0.36600` for request-commit plus one task. The focused two-block
replication `477043458740c175` did not reproduce the early result: direct
first-buffer measured `0.48415/0.33816`, while HEADERS plus one task measured
`0.51567/0.28978`. The large whole improvement is interesting, but the
packets-17--32 direction is unstable. No default or extended run followed.

Server-only candidates were then built from isolated forwardproxy source
forks and installed only into the private fixture. Establishing the target TCP
connection before returning `200 CONNECT` is causal and timer-free, but
one-block artifact `d93ef627d2851497` was a clear rejection: SOCKS and HTTP
measured `0.76902/0.41583` and `0.76899/0.37055`. Keeping the ordinary eight
framed response records while forcing their server padding lengths to zero
also failed in `556cd86759536f2f`: SOCKS measured `0.62161/0.31906` and HTTP
`0.64853/0.30986`. Thus neither target-readiness ordering nor the random
server padding bytes themselves explain the useful no-padding early shape.

Earlier server framing diagnostics are retained here with the same caveat. A
self-compensating TLS-flight alignment first deadlocked because it withheld a
final write while waiting for a future target read; private artifact
`7fa83b2ca397dc2` published no metrics. The corrected form repaid alignment
debt only from bytes already queued on the target socket. One-block artifact
`40509fc9a437ded0` measured SOCKS at `0.51330/0.31894`, but HTTP at
`0.52996/0.31830`; the HTTP whole regression rejected a shared mechanism.
Forcing the first eight server padding lengths to 255 was worse in
`a348380e48c8c17e`: SOCKS was `0.53812/0.35133` and HTTP
`0.64055/0.34906`. These forks were not installed as product dependencies.

Complete H2 payload-padding removal was admitted only under the explicit
fail-closed `NAIVEFOX_CAPTURE_EXPECT_PADDING=no` diagnostic condition. It is
otherwise rejected by the harness, and it remains restricted to gate/smoke
runs. Artifact `96e8e7354ee5ea05` produced the largest early change in this
family: SOCKS measured `0.41146/0.33675` and HTTP `0.37249/0.31880`. The HTTP
whole regression prevents promotion. The result nevertheless localized much
of the packets-17--32 residual to the historical eight-record payload-framing
phase rather than to a fixed Caddy resource size or delay.

Because a jointly changed client and server was explicitly allowed for
research, two incompatible directional variants decomposed that phase. Raw
client-to-server with ordinary padded server-to-client records measured
`0.37641/0.38446` for SOCKS and `0.46791/0.41060` for HTTP in
`02d82188c376ca39`. The inverse measured `0.47376/0.40367` and
`0.40755/0.34275` in `e98d177eb118ac30`. Both improved at least one early
point estimate but made the flow directionally asymmetric and substantially
worsened whole. Directional framing is therefore rejected.

A final incompatible symmetric sweep shortened the framed phase while keeping
the normal random padding distribution in both directions:

| Initial framed records per direction | Safe artifact | SOCKS 17--32 / whole | HTTP 17--32 / whole |
| ---: | --- | ---: | ---: |
| 2 | `130419efd9b6390a` | 0.52067 / 0.41692 | 0.45217 / 0.35922 |
| 4 | `a8e8a4e61187fe7d` | 0.45298 / 0.34698 | 0.47416 / 0.30469 |
| 6 | `4401f010fe86411a` | 0.48939 / 0.29574 | 0.48971 / 0.26763 |
| 6, fresh two-block replication | `4a16e9903f386398` | 0.42907 / 0.26369 | 0.41778 / 0.30528 |

The six-record replication is the most balanced result, but relative to the
canonical H2 rows it changes packets 17--32 by approximately 0% for SOCKS and
2.5% for HTTP, improves SOCKS whole by about 4%, and worsens HTTP whole by
about 18%. This is far below the product rule for an incompatible Caddy/client
wire change: compatibility may be broken for a default only when a replicated
candidate gives at least a 20% material improvement and does not introduce an
unacceptable counter-regression. No candidate in this campaign meets that
bar. The client record count, product source, incremental object directory,
and fixture Caddy were restored to the canonical eight-record/stock state.
Because the record boundary is driven by actual read calls, it is not a fixed
timer or response-byte cutoff, but it can still vary with chunking and link
conditions; size/link matrices would have been required after a strong result.
They were deliberately not spent on these rejected screens.

A compatible server-side queued-burst read was also rejected. The fork kept
the ordinary eight-record padding format, performed the normal blocking target
read, and then appended only bytes already readable from the target TCP socket
with `MSG_DONTWAIT`. It therefore added no timer, future-data wait, response
size threshold, or client compatibility change. One-block artifact
`7e680bed35085449` nevertheless measured SOCKS at
`0.70503/0.43634` and HTTP at `0.74554/0.36086`. Coalescing an already
queued target burst made the early packet order much less Firefox-like and did
not repair whole flow. The stock Caddy binary was restored.

Combining complete no-padding with alternative client admission boundaries
did not rescue that incompatible family. In HTTP artifact
`e857415d5ad9effe`, direct first-buffer admission measured
`0.36749/0.29565`, while response HEADERS plus one task measured
`0.37989/0.32720`; direct admission won both retained views. In SOCKS
artifact `077729c45551dcca`, direct and next-task first-buffer admission
measured `0.62721/0.46215` and `0.62055/0.43814`. Both were much worse
than the earlier no-padding point estimate and the canonical whole result.
The screens show that a task or HEADERS boundary does not make removal of the
framing phase robust. Padding and all temporary admission changes were
restored.

The next site-side experiment used HTTP `103 Early Hints` with same-origin
`Link: rel=preload` fields before the ordinary final `browser_page`
response. History preflight found no prior NaiveFox experiment at this causal
boundary: earlier native `FromParser` preload arms discovered resources only
after root body bytes arrived. The diagnostic flag changes only the outer
reference/preamble path; the tunneled inner workload remains the canonical
page. Stock Caddy and the normal H2 framing/default policies were retained.

| Early Hint set | Safe artifact | SOCKS 17--32 / whole | HTTP 17--32 / whole |
| --- | --- | ---: | ---: |
| stylesheet only | `567f47778b70c4d9` | 0.64002 / 0.27867 | 0.65291 / 0.29663 |
| stylesheet plus blocking script | `f589490d0ab2c0f9` | 0.34743 / 0.37827 | 0.36667 / 0.37973 |
| all six page resources | `e98062104b89ccbe` | 0.40660 / 0.36125 | 0.40563 / 0.34438 |

These are one-block isolated-namespace screens with seed `2026082971`, not
paired inference. The two blocking-resource hints produced the interesting
early direction, approximately 19% below the canonical SOCKS packets-17--32
row and 14% below HTTP, but whole regressed by approximately 37% and 46%.
Hinting every resource retained only a small early gain and still regressed
whole materially; stylesheet-only hints regressed packets 17--32 outright.
Requiring a special `103` response from the fronting site is therefore not a
default contract, and no replication or size/link matrix was spent on it. The
gate/smoke-only H2 switch
`--outer-early-hints css|blocking|all` remains only to reproduce this rejected
causal family.

A separate history preflight found no experiment which put the same preload
links on the ordinary final `200` response. This boundary is later than
`103` but earlier than discovering the URLs in the HTML body. The
gate/smoke-only `--outer-final-preloads` switch again changes only the outer
reference and preamble path; it is mutually exclusive with Early Hints and
does not alter the inner tunneled workload or product binary.

The first one-block blocking-pair screen `aade9604cf410007` looked balanced:
SOCKS and HTTP packets 17--32 were `0.39982/0.38275`, with whole
`0.26172/0.26969`. A stylesheet-only control was then clearly rejected in
`1deeda6981aeba33`: it measured `0.68566/0.35983` for SOCKS and
`0.63957/0.39122` for HTTP. This non-monotonic result made a fresh
replication mandatory rather than treating the first ordering as a tuning
point.

Four-block artifact `ea162ea7be104577` (seed `2026082974`) did not
reproduce a common blocking-pair win. SOCKS measured `0.43769/0.24862` and
HTTP `0.41999/0.27334`. Relative to the canonical rows, that is roughly a 2%
packets-17--32 regression and 9.7% whole improvement for SOCKS, but a 2%
packets-17--32 improvement and 5.3% whole regression for HTTP. A special
final-response `Link` contract therefore transfers residual between
listeners rather than reducing both targets. It is not a fronting-site
requirement or default; the all-resource variant and size/link matrix were not
spent after this replicated rejection. The bounded harness switch remains for
reproduction.

Optimistically acknowledging the local proxy request before the outer CONNECT
completed is rejected after a focused replication. History preflight separated
this boundary from the earlier `bfd151c6ae8b307f` and `5b393915a38fb0e4`
experiments: those held local SOCKS success later than outer readiness, while
this diagnostic deliberately let Firefox construct and queue its inner TLS
ClientHello earlier. The duplex pump still remained closed until Necko reported
the real outer tunnel established. Strict markers required the local SOCKS
success or HTTP `200` to flush before outer establishment and required pump
startup afterwards; an upstream failure closed the already acknowledged local
connection because the success could no longer be retracted.

One-block artifact `5d118ec9a43a1d3d` initially made HTTP CONNECT look
interesting. Its default and optimistic arms measured `0.58627/0.37557` and
`0.45023/0.38087` for packets 17--32/whole: a 23.2% early improvement with a
1.4% whole regression. The same block rejected the SOCKS form outright:
default and optimistic measured `0.55083/0.28560` and
`0.57846/0.31942`. A fresh four-block HTTP-only replication was therefore
collected instead of spending a full listener/protocol or resource-size
matrix.

Artifact `2402ddf719abded2` did not retain the large HTTP change. Default and
optimistic HTTP CONNECT measured `0.51647/0.23874` and
`0.48820/0.26799`: packets 17--32 improved only 5.5%, while whole regressed
12.3%. All participants ran in the isolated namespace and passed the strict
local-reply, outer-establishment, pump, inner-H2, and single-outer-connection
contracts. The diagnostic product identified libxul digest
`08ce2a8de1e7b61544f2d9f4d4d4572cf2336e395c6f11c369e1020694f53dea`.
An irreversible early success is not justified by that replicated tradeoff,
so production semantics and both H2 defaults remain unchanged. The opt-in
arms remain only to reproduce the rejected mechanism.

An informational response on the CONNECT stream itself was rejected before
passive measurement. History preflight found only the later site-page `103`
experiment above; no earlier server fork inserted `1xx` before the proxy's
final `200 CONNECT`. A private forwardproxy fork therefore flushed an empty
standards-defined `103 Early Hints` and then followed the ordinary fast-open
path, including its random `Padding` header and final `200`. This added no
timer, target-byte threshold, or dependency on the fronting page resources.

The fork's own tests immediately proved that legacy Go CONNECT clients treat
the first `103` as final and fail, so the mechanism was already known to be
compatibility-breaking. Firefox's general HTTP transaction parser can consume
multiple informational responses, but private fail-closed capture
`c62a7a440da14346` exposed a narrower tunnel handoff problem: NaiveFox logged
the first CONNECT HEADERS as established with `padding=no`, then the inner
browser navigation never completed before its 45-second cutoff. No passive
distances were published and the sample was not retried. Adding client-side
special handling solely to make an unmeasured incompatible shaping mechanism
work was not justified. The fixture was restored byte-for-byte to stock Caddy
digest `444ca421ae27be5d83f6cc5e6641badd8bcd7a1a92e1130dab027cbf8bb2a938`.

A later preflight found no previous experiment with the ordering `outer
preamble ready -> local proxy success -> first inner browser bytes -> outer
CONNECT`. This looked like a possible timer-free source of the natural pause
seen in direct Firefox: in fresh artifact `2402ddf719abded2`, direct packet 22
followed packet 21 after 29--36 ms, while the corresponding NaiveFox gap was
about 0.28 ms. Before changing the cross-thread tunnel lifecycle, a scratch
loopback HTTP proxy measured the necessary premise directly with the same-base
reference Firefox. It accepted only `timing.invalid:443`, disabled Nagle on the
accepted socket, flushed a normal local `200`, and timestamped the first inner
TLS bytes without logging the CONNECT authority or payload.

Three independent headless launches delivered the same 1827-byte first inner
TLS flight after `0.555`, `0.770`, and `0.869` ms. Thus Firefox contributes
less than 1 ms of natural work at this boundary on the isolated local setup,
not the missing tens of milliseconds. Interposing an inner-byte gate would add
lifecycle complexity while merely moving outer CONNECT by approximately the
existing sub-millisecond gap. No product code, build, passive capture, or
matrix was spent on this rejected premise.

The next H2 preflight revisited the retained six-resource native-parser policy
without repeating the old H2 resource-tree experiment. Commit `1589da63dcdc`
had used three resources and admitted CONNECT at document request commit, so
its observable order was root GET, CONNECT, then resource GETs. The exact
six-resource policy instead opens the stylesheet and blocking script
immediately, defers four image opens to the next main-thread turn, and admits
CONNECT only after all six requests commit and the first complete successful
resource body buffer is consumed. History and documentation searches found
that exact contract only in H3 runs.

The first one-block attempt stopped fail-closed in private artifact
`a433dcc2d6f252a8` and published no distances. Configuration and harness
validation accepted the explicit H2 mode, but a second runtime whitelist in
`ProxyPreambleOperation::Start` returned `NS_ERROR_INVALID_ARG` before opening
the root. The whitelist was corrected without changing scheduling: H2 accepts
this mode only with exactly six resources, while H3 retains its three- or
six-resource contract. The same seed then completed all eight participants in
safe artifact `4266ee23a358db8b`:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.58908 | 0.52290 | control |
| SOCKS six-resource candidate | 0.54909 | 0.51022 | -6.8% / -2.4% |
| HTTP current `document-first-buffer-http-connect` | 0.49645 | 0.51241 | control |
| HTTP six-resource candidate | 0.65647 | 0.53841 | +32.2% / +5.1% |

This was an isolated, unshaped, same-base H2/inner-HTTPS block with seed
`2026082921`, the canonical 262144-byte page, stock Caddy, Firefox A/B, and
the required listener-specific `document-start` causal controls. One block is
not inference, but the SOCKS movement is far below the threshold for a costly
replication and HTTP CONNECT moves strongly in the wrong direction. No
default, site contract, resource-size matrix, or link-profile matrix followed.
The explicit arms remain only for reproduction of this rejected mechanism.

Before the next H2 implementation, a proposed cancellation of the active
outer root at the first target response was rejected without implementation.
The history search found the H3 response-stop/cancellation family, including
commit `6f32c2baf9c1115f`, where the cover resources had already completed and
the stop was a no-op. More importantly, preserved H2 default lifecycle logs
showed that its document-only root was 620 bytes and drained within about
0.1 ms of first-buffer admission; outer CONNECT was established roughly
0.65 ms later. A target-response callback therefore occurs after the only H2
cover stream has already ended and cannot change the packet sequence. No code,
build, or passive matrix was spent on that causally ineffective variant.

A distinct ordering then passed the mandatory history preflight. Old H2
three-resource work admitted CONNECT before resource opens, while the H2
six-resource screen above waited for all six request commits plus a successful
resource body buffer. The related H3 image-deferral experiment also admitted
CONNECT only after the deferred image commits and body progress. No prior arm
used the ordering `exact root parsed -> CSS and blocking script committed ->
CONNECT -> four image opens on the next main-thread turn`.

Commit `38fc853f9ccb` implemented that ordering only in the explicit H2
six-resource diagnostic arm. It used no timer, RTT estimate, response-size
threshold, or target-body event: the exact root had to finish successfully,
the native parser had to accept the fixed page contract, and request-commit
events for streams 1 and 2 released CONNECT. Streams 3--6 remained prepared
until a queued main-thread task after the admission callback. H3 and every
production default were unchanged. The strict synthetic lifecycle validator
covered both listener aliases and rejected a missing commit barrier or wrong
deferred-open cause; the implementation passed 121/121 harness tests, 57/57
focused C++ gtests, and an incremental product build.

The isolated same-base one-block screen used seed `2026082922`, the canonical
262144-byte inner page, the fixed stock-Caddy outer profile, Firefox A/B, both
current defaults, and both required document-start controls. Safe artifact
`a2ea4b3dcde8c23a` was directionally strong:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.67644 | 0.50223 | control |
| SOCKS CSS/JS-commit candidate | 0.41428 | 0.48613 | -38.8% / -3.2% |
| HTTP current `document-first-buffer-http-connect` | 0.63848 | 0.49068 | control |
| HTTP CSS/JS-commit candidate | 0.55869 | 0.48713 | -12.5% / -0.7% |

Because the SOCKS movement crossed the strong-screen threshold, the candidate
received a focused four-block replication rather than a size, link-profile,
or full default matrix. The harness retained both listener-specific
document-start controls, so each of the four isolated blocks contained eight
participants. Safe artifact `7a9df73a476bf808`, seed `2026082923`, did not
reproduce the first block's magnitude:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.45379 | 0.43380 | control |
| SOCKS CSS/JS-commit candidate | 0.42451 | 0.42214 | -6.5% / -2.7% |
| HTTP current `document-first-buffer-http-connect` | 0.42051 | 0.42717 | control |
| HTTP CSS/JS-commit candidate | 0.40848 | 0.43004 | -2.9% / +0.7% |

Four gate blocks remain below the 30-block inference floor. The conditional
paired bootstrap difference for SOCKS 17--32 favored the candidate by 0.02928
with interval `[0.00493, 0.04591]`, but its exact four-block sign-flip
`p=0.25`; the SOCKS Whole interval crossed zero. Both HTTP intervals crossed
zero, and its mean Whole direction was slightly worse. This is too small and
unstable to justify replacing the more general H2 defaults with an exact-page
policy. No promotion or robustness matrix followed. Commit `1c05e11d6a3f`
retired the experimental runtime/validator changes and restored the prior
explicit six-resource behavior; the two safe artifacts and these notes retain
the complete result for future preflight searches.

The next incompatible padding experiment also passed the mandatory exact and
causal history preflight before implementation. Searches across this document,
commit messages, pickaxe diffs for the eight-record codec constant, and the
old forwardproxy forks found complete no-padding, client padding ranges,
two/four/six framed records, first-eight zero or maximum server padding,
directional framing, and an early server padding-only phase. None kept eight
random padding lengths per direction while moving that same random budget from
records 1--8 to records 9--16. This distinction was recorded before code so a
superficially new name could not repeat an old mechanism.

Commits `1680114ac61f` and `c6629c50c443` implemented the explicit H2-only
`diagnostic-delayed-padding-phase` client, codec, and two listener-specific
harness arms. Records 1--8 were still framed but used zero padding; records
9--16 used the ordinary eight independently random 0--255 padding lengths;
the stream was raw afterwards. Thus the random byte budget was preserved and
only 24 bytes of additional record headers were introduced per direction.
There was no timer, future-byte wait, RTT estimate, page-resource dependency,
or response-size threshold. A `~2` prefix which the normal header-padding
generator cannot emit negotiated the mode in both CONNECT directions. A
diagnostic client failed closed if the server did not echo it, and a normal
client rejected an unexpected delayed marker before starting its duplex pump.

The matching forwardproxy fork selected legacy or delayed framing separately
for every CONNECT request, so current and candidate arms shared one Caddy
process without changing their respective wire contracts. Its own `go test
./...` passed. The private Caddy digest was
`0456d1d3b515ee4966a86eb6073c8c8baf5e4f25dd287aad971597cfd4e5d327`;
the preserved stock digest was
`444ca421ae27be5d83f6cc5e6641badd8bcd7a1a92e1130dab027cbf8bb2a938`.
Client work passed 67/67 focused C++ gtests, including all fail-closed
negotiation combinations and fragmented codec round trips, 123/123 complete
harness tests, and an incremental product build. Isolated single-arm smoke
artifact `6af887ff30cebc6b` (seed `2026082924`) then proved the real negotiated
path and strict runtime marker before comparative collection.

The one-block same-base screen `3b0d17fb60e70fca` (seed `2026082925`) used
the canonical 262144-byte browser page, inner HTTPS/H2, Firefox A/B, and both
current and delayed listener arms in one randomized six-participant block:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.59685 | 0.30333 | control |
| SOCKS delayed-padding candidate | 0.61947 | 0.28784 | +3.8% / -5.1% |
| HTTP current `document-first-buffer-http-connect` | 0.60158 | 0.29358 | control |
| HTTP delayed-padding candidate | 0.62224 | 0.28766 | +3.4% / -2.0% |

Moving the random budget later therefore worsened the primary packets-17--32
slice for both listeners and improved Whole only slightly. It is nowhere near
the at-least-20% rule required to justify a client/Caddy compatibility break,
so no replication, resource-size matrix, or constrained-link matrix was
spent. Commit `7823a21c5b01` retired the client and harness diagnostics;
121/121 harness tests and a second incremental product build passed after the
retirement. The active fixture was restored byte-for-byte to the stock Caddy
digest above. Future padding work must treat delayed random budget as tested
and rejected rather than retrying it under another record-count or phase name.

The next idea pass applied the experiment-history gate before touching code.
Two superficially new proposals were rejected at that stage. First, sharing or
echoing one constant CONNECT `Padding` value so HPACK could reuse it has no
causal opportunity in the canonical browser-page workload: one
`TunnelSession` opens one CONNECT on its outer H2 session. Necko's H2 encoder
does index an ordinary non-sensitive `Padding` field, but there is no second
CONNECT header block on which that dynamic-table entry could save bytes. The
strict negotiation validator used by the preceding smoke also required exactly
one marker for that session and would have rejected a second one. Therefore
header-value reuse could not change the measured wire and was not implemented.

Second, replacing forwardproxy's per-target-read response flushes with ordinary
Go buffering is causally the already closed response-coalescing family, not a
new transport idea. Safe artifact `01c1250aa7a7f14a` had already tested a 1-ms
idle batch and found only a small packets-17--32 movement while 1--32 and the
server-byte deficit worsened. The later semantic full-TLS-record and flight
cutoffs in artifacts `e742cfd3a0c19fe9` and `9ce752e81ecd307b` improved an
early slice but significantly worsened Whole; cleartext-handshake-only artifact
`64e7a03ff341ffe2` removed the apparent benefit. The no-code preflight therefore
prevented another implementation of the same read-boundary batching mechanism.

The genuinely untried branch was the size of the single CONNECT `Padding`
header itself. History and pickaxe searches found payload-padding ranges,
record-count changes, server zero/max padding, and the delayed budget above,
but no experiment that changed the request/response header-value endpoints
while leaving the payload codec intact. Commits `adf9d49a91d0` and
`6efd6724218e` added four explicit H2-only diagnostic arms for SOCKS and HTTP
CONNECT. A `~5` request selected an exact two-byte value in both directions; a
`~6` prefix on an exact 96-byte request selected exact 96-byte values. These
markers cannot be emitted by the stock 16--32-byte client generator. An arm
failed closed unless the response had the same exact profile, protocol, and
connection identity. The ordinary eight randomized payload records in each
direction and the raw stream after them were unchanged; there was no timer,
resource-size dependency, link estimate, or future-byte wait.

The matching private forwardproxy fork selected a profile per CONNECT while
preserving the stock 30--61-byte response generator for every unmarked request.
Pinned `go test ./...` passed, including marker near misses. The custom Caddy
digest was
`4dcd4ad6a59791510e08910898d4f22c42d4bf8f50d7ce7deab197d061f1266b`;
the preserved and restored stock digest was
`444ca421ae27be5d83f6cc5e6641badd8bcd7a1a92e1130dab027cbf8bb2a938`.
Client work passed 105/105 NaiveFox C++ gtests, 123/123 complete harness tests,
and incremental test/product builds without a clobber. Isolated single-arm
smoke artifact `ca9910ed900dc422` (seed `2026082926`) proved real 2/2-byte
negotiation through the strict runtime validator.

The one-block same-base screen `2ac627c3521ec666` (seed `2026082927`) used the
canonical 262144-byte browser page, inner HTTPS/H2, Firefox A/B, and all six
current/profile listener arms in one randomized eight-participant block:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.55473 | 0.34608 | control |
| SOCKS two-byte header | 0.48077 | 0.35099 | -13.3% / +1.4% |
| SOCKS 96-byte header | 0.58148 | 0.34886 | +4.8% / +0.8% |
| HTTP current `document-first-buffer-http-connect` | 0.59045 | 0.35839 | control |
| HTTP two-byte header | 0.58654 | 0.34527 | -0.7% / -3.7% |
| HTTP 96-byte header | 0.61926 | 0.32556 | +4.9% / -9.2% |

The best focus movement, SOCKS with two-byte headers, was only 13.3% and made
Whole slightly worse. The best Whole movement, HTTP with 96-byte headers, was
9.2% and made packets 17--32 worse. Neither endpoint approached the required
20% compatibility-break threshold or improved both target views, so no
replication, resource-size matrix, or constrained-link matrix followed.
Commit `c67adfa9ccc9` retired the diagnostic client and harness; 100/100 C++
gtests, 121/121 harness tests, and an incremental product build passed after
retirement. The active fixture is again byte-for-byte stock. Future work must
treat CONNECT header-size endpoints, HPACK reuse without a second CONNECT, and
server read-boundary batching as examined rather than recycling them under new
names.

The next candidate passed the mandatory causal-history preflight before code
was written. The earlier full multi-process reconstruction changed where
Firefox subsystems ran but retained one bidirectional CONNECT stream and did
not help. Likewise, CAPTURE's earlier "directional framing" changed Variant-1
record boundaries inside one stream. Neither experiment split the two
transport directions across independently scheduled H2 streams. Existing
captures with several CONNECT stream IDs represented several independent
browser tunnels, not two lanes paired to one logical tunnel. A two-stream
directional tunnel was therefore new rather than another process-topology or
payload-framing retry.

Commits `ca672f0faa73`, `27dd6854d0a7`, `86d89858439d`, and
`db3d8dda57fc` implemented and admitted the explicit H2-only
`diagnostic-directional-connect` screen. Each logical tunnel used two classic
CONNECT requests with a shared random token and complementary fail-closed
markers: the first request body carried only local-to-target bytes, while the
second response body carried only target-to-local bytes. The server paired the
lanes, made exactly one target dial, and rejected duplicates, mismatched
targets, and incomplete pairs. Variant-1 framing remained unchanged exactly
once in each direction. The downstream lane was opened only after a valid
upstream H2 `200` with an echoed marker. There was no timer, page-resource
condition, link estimate, target-body threshold, or future-byte wait.

The pinned forwardproxy fork was based on
`github.com/klzgrad/forwardproxy` commit `d62c80d3dd2c`; its modified
`forwardproxy.go` digest was
`19477a1885d7c7500f09fcf483484c7f857b244bd0d672cce45065e4d1b17535`,
and `go test ./...` passed. The private Caddy 2.11.2 binary digest was
`335ee7df79a8a975abdc365e0c06c3f7bac2b051e403e46c162a5056258729b2`;
the preserved stock binary remained
`444ca421ae27be5d83f6cc5e6641badd8bcd7a1a92e1130dab027cbf8bb2a938`.
Client work passed 106/106 focused C++ gtests, 124/124 complete harness tests,
and incremental test/product builds without a clobber.

Three failed private lifecycle smokes were diagnostically useful and must not
be repeated as nominal candidate captures. Seed `2026082928` opened 58 outer
TCP flows because both lanes were released together. Staging the downstream
lane reduced this to 29 flows at seed `2026082929`, but did not restore H2
multiplexing. A private Necko pool log at seed `2026082930` still observed 31
flows and exposed the cause: each H2 CONNECT stream becomes a virtual
`nsHttpConnection`, and `UsingConnect()` applies
`network.http.max-persistent-connections-per-server`, whose default limit is
six. Five upstream CONNECTs released by the outer-session gate plus the first
downstream CONNECT consumed all six slots; later lanes started new H2 winners
and a `DontReuse`/TLS-race cascade. This was not host-network churn.

The diagnostic profile was therefore given the structural bound of 12 slots,
two lanes for each of the six browser connections; every non-directional arm
rejected that override. Seed `2026082931` then used one physical outer TCP
connection and paired all six logical tunnels, but exposed an overly narrow
validator which expected one marker for the entire session. After the
validator required one ordered, unique pair per established logical CONNECT,
isolated smoke artifact `668436d850d86dfd` (seed `2026082932`) passed with
one ClientHello, one outer TCP flow, and all six pairs.

The randomized same-base one-block SOCKS screen `a9ffaf1c49a77777` (seed
`2026082933`) used the canonical 262144-byte browser page, inner HTTPS/H1.1,
Firefox A/B, and only the two requested target views:

| H2 SOCKS arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| current `document-first-buffer-task-overlap` | 0.73616 | 0.35785 | control |
| two-stream directional candidate | 0.65013 | 0.33508 | -11.7% / -6.4% |

Both views moved in the desired direction, but the gains were far below the
at-least-20% requirement for making the client and Caddy mutually incompatible.
No HTTP-listener screen, replication, resource-size matrix, or constrained-link
matrix was spent on it. Commit `bfcef096f398` retired the client and harness
experiment; the affected files exactly match their pre-experiment state.
After retirement, 100/100 C++ gtests, 121/121 harness tests, and incremental
test/product builds passed. Production defaults are unchanged and the active
fixture remains stock Caddy.

A related proposal to stop cover traffic on the first inner or target bytes
was also rejected by the same preflight rather than reimplemented. Artifact
`54ea85af8f8ade5a` had already made cancellation on first tunneled application
bytes strongly worse on a shaped link. H3 commit `08c70e5382e9` tried the
target-response form and `983b1705709e` retired it as ineffective; the retained
H2 lifecycle evidence also shows its small root finishes before such a callback
can affect the wire. Future research must treat two-stream direction splitting,
raising the per-server cap solely to support it, and first-inner/target-byte
cover cancellation as tested causal families, not rename and retry them.

### H2 ordinary-method carrier follow-up

The next H2 candidate also passed the mandatory causal-history preflight before
implementation. The full multi-process reconstruction had already changed
Firefox process/parser topology without changing the classic bidirectional
CONNECT carrier and did not help. The strict H2 priority experiment had already
reported `mechanism_verdict=wire-null`; WebSocket extended CONNECT, two-stream
direction splitting, CONNECT header-size endpoints, and HPACK reuse were
separate previously tested causes. Searches through this document, the harness,
the full git history, and pickaxe history for `HTTPUpgrade`, `SetConnectOnly`,
ordinary GET/POST tunnel methods, and `Http2StreamTunnel` found no experiment
which kept the raw Necko duplex stream but replaced classic H2 CONNECT with an
ordinary H2 request. That method-carrier cause was therefore new; process
topology, priority, cache/classifier, and directional framing were not retried.

Commits `24abb804708a` and `b8a3bebf5236` implemented and admitted an explicit
H2-only `diagnostic-h2-get-carrier` screen. Request padding retained the stock
16--32-byte distribution but reserved the impossible-in-stock `~8` prefix.
Only a valid marked H2 request used ordinary `GET`; all other traffic remained
classic CONNECT. The forwardproxy fork accepted only that bounded marker,
returned the stock 30--61-byte response-padding distribution while echoing the
first 16 marker bytes, and then used the unchanged Variant-1 duplex stream.
The client required H2 plus the exact marker echo before admitting the tunnel.
There was no timer, resource-size threshold, link estimate, target-body
condition, or future-byte wait. The server retained the stock forwardproxy
Fast Open order (`200` before target dial), so slow-origin behavior was not
silently replaced by an extra target-dial barrier.

The fork was based on `github.com/klzgrad/forwardproxy` commit
`d62c80d3dd2c`. Its final modified `forwardproxy.go` digest was
`642f8d3ca7b4822c82322e351c8402f9dfb193a2518977efda4c6886b2027fdc`,
the focused Go-test digest was
`55f856b826c7ec81376a60c22eab1f0659534f5750a4d81c242c2a6ce4ad1b0e`,
and `go test ./...` passed. The private Caddy 2.11.2 binary digest was
`954e0c3c9ff5c87cc54666a3b9f74935cd2a5d8ea4835f4fe7db2f75ab5f80c2`;
the preserved stock binary remained
`444ca421ae27be5d83f6cc5e6641badd8bcd7a1a92e1130dab027cbf8bb2a938`.
Client work passed 103/103 focused C++ gtests, 124/124 complete harness tests,
and incremental test/product builds without a clobber.

One failed private lifecycle smoke, `87f8b5dd8029eb50` (seed `2026082934`),
must not be treated as a candidate measurement or repeated as the nominal
configuration. It proved a valid marked H2 GET reached forwardproxy, but an
over-specific server guard required path `/`. The fixture's ordinary
`rewrite / /camouflage/index.html?scenario=fronting_page_dense` runs before
forwardproxy, so the guard rejected a correctly negotiated request before any
target dial. Removing that path guard made negotiation independent of the
fronting-site route and resource set. An isolated manual SOCKS/TLS duplex probe
then succeeded, followed by strict one-sample SOCKS artifact
`0b0ffa018502ae33` (seed `2026082935`) and HTTP-listener artifact
`8765f4f771bfca1e` (seed `2026082936`). Each admitted one physical H2 session,
one exact marker echo, a completed preamble, and a working inner HTTPS/H2
request.

The randomized same-base one-block screen `9a73f27ea029dcd5` (seed
`2026082937`) used the canonical 262144-byte browser page, inner HTTPS/H2,
Firefox A/B, an isolated network namespace, and only packets 17--32 plus Whole:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.49978 | 0.44192 | control |
| SOCKS ordinary-GET carrier | 0.50532 | 0.42741 | +1.1% / -3.3% |
| HTTP current `document-first-buffer-http-connect` | 0.49030 | 0.39485 | control |
| HTTP ordinary-GET carrier | 0.40576 | 0.45081 | -17.2% / +14.2% |

SOCKS was effectively neutral in the focus window. HTTP improved packets
17--32 by only 17.2%, below the required 20% threshold for a mutually breaking
client/Caddy change, while making Whole 14.2% worse. No replication,
resource-size matrix, constrained-link matrix, or full default matrix was
spent on this weak trade. Commits `e5522f840f78` and `52c456d88c98` retired the
harness and client experiment respectively. After retirement, 121/121 harness
tests, 100/100 C++ gtests, and both incremental test/product builds passed.
Production defaults and the documented fronting-site requirements are
unchanged; the active fixture is stock Caddy.

Future preflight must treat replacing H2 CONNECT with an ordinary GET or POST,
or merely moving the same negotiation marker between headers, as this rejected
method-carrier causal family. A new proposal needs a distinct mechanism which
explains both the 17--32 signal and Whole rather than renaming this wire-method
substitution.

### H2 early CONNECT request-DATA follow-up

The next H2 idea passed the mandatory exact and causal-history preflight before
implementation. Prior work had tried optimistic local listener admission,
ordinary GET/POST carriers, two directional CONNECT streams, payload framing
and padding variants, CONNECT header sizes, H2 priority, parser/process
topology, classifier/cache changes, and fixed or event-driven response gates.
None put bytes which were already buffered from the inner TLS ClientHello into
DATA on the same classic H2 CONNECT request before the proxy response. That
wire-ordering mechanism was therefore distinct; it did not wait for a future
resource, fixed delay, target response, bandwidth estimate, or page size.

Commits `87c158b74f2f`, `1cc1c6edafdb`, and `51354215991e` implemented the
explicit H2-only client and two listener-specific harness arms. The first
implementation exposed an important false path which must not be repeated:
attaching the 1,822 buffered bytes through the original channel upload stream
produced zero request-body bytes at the server and a 45-second timeout. H2
CONNECT does not serialize that upload stream; `nsHttpConnection` constructs a
separate `mProxyConnectStream`. The corrected diagnostic stored a bounded
payload in the private request head and appended it directly after that
CONNECT stream's headers, which Necko then encoded as request DATA before the
`200`. The normal CONNECT path stayed unchanged, and the client required an
H2-only exact length echo before accepting the experimental tunnel.

The matching forwardproxy fork read exactly the declared 1--65,536 bytes after
the stock-time `200`, copied them to the dialed target, and then continued with
the unchanged Variant-1 decoder. It rejected duplicate, malformed, short, and
H3-marked requests; unmarked requests retained the stock behavior. Its pinned
`go test ./...` passed. The final `forwardproxy.go` digest was
`1ef5885d5ed1d9d9645ece4d6df4a4d45d4eb1f33522738f2792b8d19cf597fb`,
the focused Go-test digest was
`5671cc976a8c9fb8c97f23ea149b17256c630b9089eb03b05cd9b77e046e6ef8`,
and the private Caddy 2.11.2 digest was
`429d56eb01bb4cf81b781b3b0df7b94601edb7b8530d5e3bc9c3bfa3f456b533`.
The preserved stock Caddy digest remained
`444ca421ae27be5d83f6cc5e6641badd8bcd7a1a92e1130dab027cbf8bb2a938`.

Incremental product and test builds passed without a clobber, as did the three
new request-head boundary tests, the existing NaiveFox focused gtests, and
124/124 complete harness tests. Clean isolated lifecycle artifacts
`4a4d42ffd0fcf2f2` (SOCKS, seed `2026082945`) and
`e750bcf796a4b2cb` (HTTP listener, seed `2026082946`) proved real pre-response
DATA delivery, exact echo negotiation, one physical H2 session, and a working
inner HTTPS/H2 request.

The randomized same-base one-block screen `797aa31a0b8d8e73` (seed
`2026082947`) used the canonical 262144-byte browser page, inner HTTPS/H2, an
isolated WSL network namespace, Firefox A/B, both current defaults, and both
early-DATA arms. Only the requested packets 17--32 and Whole views were
collected:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.64087 | 0.38297 | control |
| SOCKS early CONNECT DATA | 0.62021 | 0.38350 | -3.2% / +0.1% |
| HTTP current `document-first-buffer-http-connect` | 0.56351 | 0.34759 | control |
| HTTP early CONNECT DATA | 0.53051 | 0.40755 | -5.9% / +17.3% |

The focus-window changes were weak, and HTTP Whole regressed substantially.
The mutually incompatible client/Caddy mechanism is far below the required
20% improvement and does not improve both targets, so no replication,
resource-size matrix, constrained-link matrix, or full default matrix was
spent. The diagnostic client, harness, and fixture fork were retired; active
defaults and fronting-site requirements remain unchanged. Future preflight
must treat both attaching buffered bytes to the original request upload and
injecting them into the actual H2 proxy CONNECT stream as the tested and
rejected pre-response request-DATA causal family, rather than retrying either
form under a new early-data, Fast Open, or zero-RTT name.

### H2 single-CONNECT multiplexing premise check

Before implementing another incompatible transport, the mandatory history
gate searched the documentation, harness, full Git history, and pickaxe diffs
for shared tunnels, substreams, demultiplexing, and multiple local transports
inside one outer CONNECT. No prior NaiveFox experiment had implemented that
custom multiplexing layer; the two-stream directional experiment did the
opposite and split one logical tunnel across two CONNECT streams. A new
implementation was nevertheless rejected because its required premise was
false for the focus workload.

Clean isolated lifecycle-only artifacts `74cf25555a2a36a7` (SOCKS, seed
`2026082948`) and `80c8273acbc4565f` (HTTP listener, seed `2026082949`) each
ran the current H2 default with the canonical browser page and inner HTTPS/H2.
The fixture access lifecycle contained exactly one outer CONNECT in each run.
Consequently there were no repeated CONNECT headers or Variant-1 startup
phases for an application substream multiplexer to eliminate. Adding a custom
flow-control, target-routing, and demultiplexing protocol would only replace
the already single tunnel, not collapse several tunnels into one. No product,
Caddy, harness, build, or passive matrix change was spent. Future preflight
must not infer multiple logical tunnels merely from the old directional
experiment's diagnostic slot bound; it must first measure the actual workload
being proposed for multiplexing.

A proposed bodyless/`HEAD` companion-resource variant was also rejected at
this gate without implementation. The exact method label was new, but its
causal event was not: the earlier interleaved blocking-resource experiments
already exercised `stylesheet and script request commits -> CONNECT`, and the
related H3 `HEAD`/Range family showed that replacing ordinary resource bodies
can alter terminal channel behavior without creating a new admission event.
Changing GET volume to a bodyless response would tune the fixture resource
shape while leaving the controlling two-commit boundary intact. No product,
fixture, harness, build, or capture work was spent on this causally equivalent
proposal.

### H2 DATA-frame padding follow-up

The next incompatible client/server candidate passed the mandatory exact and
causal-history search before code was written. Prior experiments had varied
Variant-1 application-record padding, initial record count, directionality,
CONNECT header padding, TLS-flight alignment, batching, and H2 priority.
Searches of the Markdown, harness, complete Git history, and pickaxe/regex
history for H2 PADDED flags and padded-DATA writer paths found no experiment
which placed padding in the outer H2 DATA frames themselves. This was therefore
a distinct framing-layer cause rather than another application-record count or
amount endpoint.

Commits `5dcc4e3cb25f` and `e7c825c10367` implemented and admitted an explicit
H2-only diagnostic. A reserved request-padding marker negotiated the candidate
and had to be echoed by the response; the client rejected a protocol mismatch,
missing echo, or observed/requested diagnostic mismatch. Request-side
Variant-1 framing remained stock. On the response side, the first eight
Variant-1 records remained decodable but used zero application padding, while
up to the first eight Caddy handler writes used standards-compliant H2 DATA
padding. Pad Length was random in `0..255`, padding octets were zero, and both
frame-size and flow-control accounting included the pad-length field and pad
octets. There was no pause, resource-size threshold, fronting-page condition,
bandwidth/RTT estimate, or wait for future target bytes; unmarked clients kept
the stock path.

The first server build exposed an implementation path which must not be
repeated. Patching Go's bundled `net/http/h2_bundle.go` produced private Caddy
binary digest
`a342d1abd21569473dc5516254220cbf88e62d437fc4b974861cdda1dac132ca`,
and its unit tests passed, but Caddy 2.11.2 actually used
`golang.org/x/net/http2`. Private lifecycle artifact `c55165adc9ced6d8`
therefore negotiated the marker on five established tunnels but contained no
PADDED DATA frames and published no passive distances. The first validator
also passed decrypted PDML bytes to the XML parser as a pathname; wrapping the
bytes in an in-memory stream fixed that diagnostic error. Neither failure is a
candidate measurement.

The corrected fork patched the pinned `golang.org/x/net` v0.51.0 writer and
scheduler, added marker, zero-padding, split-frame, and flow-control tests, and
passed `go test ./http2`; the pinned forwardproxy fork passed `go test ./...`.
The resulting Caddy 2.11.2 binary digest was
`6d3ba1829b4f2d3ad87bbc3e21d7810e804bf2447e852fb36a51da3491283c31`.
Incremental product/test builds required no clobber, 103/103 focused C++ gtests
passed, and 129/129 harness plus validator tests passed. Strict isolated
lifecycle artifacts `f6e7e40aba138a05` (SOCKS) and `0236019a82b63c4c`
(HTTP listener) each admitted the expected marker and padded-frame sample,
proved that padding occurred only on server-to-client DATA of marked CONNECT
streams, and deleted the private key log/capture after retaining safe counts.

The randomized one-block same-base screen `06350ee8cab9df76` (seed
`2026083053`) used the canonical 262144-byte inner page, inner HTTPS/H1.1,
Firefox A/B, both current listener defaults, an isolated WSL network namespace,
and only packets 17--32 plus Whole:

| H2 listener / arm | 17--32 | Whole | Change from same-block current default |
| --- | ---: | ---: | --- |
| SOCKS current `document-first-buffer-task-overlap` | 0.45765 | 0.31613 | control |
| SOCKS H2 DATA padding | 0.45872 | 0.29880 | +0.2% / -5.5% |
| HTTP current `document-first-buffer-http-connect` | 0.45128 | 0.40461 | control |
| HTTP H2 DATA padding | 0.39976 | 0.29954 | -11.4% / -26.0% |

The isolated wire validator admitted both candidate samples. SOCKS was
effectively neutral in the focus window. The HTTP point estimate was promising
for Whole but below the 20% breaking-change rule for packets 17--32, so it
required a focused replication instead of a full matrix.

Fresh four-block HTTP-only artifact `b0454b18ae89a387` (seed `2026083054`)
also used inner HTTPS/H1.1 and reversed both directions. The current HTTP default measured
`0.46318 [0.42335, 0.50301]` for packets 17--32 and
`0.26826 [0.23575, 0.30440]` for Whole. DATA padding measured
`0.49186 [0.44626, 0.53385]` and `0.28606 [0.25119, 0.33062]`, regressions of
6.2% and 6.6%. All four candidate samples passed decrypted wire admission and
all 16 participants passed the isolated-network mutation gate. Four smoke
blocks remain descriptive rather than paired inference, but they directly
reject the one-block promotion signal.

The jointly incompatible Caddy/client mechanism therefore fails replication,
does not meet the at-least-20% default threshold, and must not be tuned through
nearby DATA-padding counts or lengths as though those were new causal ideas.
No resource-size, constrained-link, cross-platform, or full default matrix was
spent after this rejection. The product and harness diagnostics are retired;
the production defaults and documented fronting-site requirements remain
unchanged. The named custom Caddy and forks remain only as inactive private
scratch material for reproducing this negative result.

A follow-on connection-control proposal was rejected by the history gate and
a read-only decrypted audit rather than implemented. Injecting H2 PINGs,
no-op repeated SETTINGS, or extra WINDOW_UPDATE frames would be distinct from
DATA padding, but it first needed evidence that direct Firefox naturally used
such control traffic in the target phase. Safe lifecycle artifact
`20260829T230030Z-bf81d6e9` used one isolated same-base H2 connection per
cohort and confirmed equal client SETTINGS. Direct Firefox emitted only the
initial SETTINGS/WINDOW_UPDATE exchange and acknowledgements, then ordinary
GET/HEADERS/DATA until its late connection shutdown; it emitted no PING and no
repeated SETTINGS in the resource-start phase. Its first six resource GETs
began about 135 ms after the first H2 request in this cold command-line trace,
whereas the diagnostic document-start arm emitted its first CONNECT after
0.444 ms. Synthetic control frames would add a new stable marker without
reproducing the browser parser/resource cause of that gap, so no product,
Caddy, or passive-screen code was written.

The first attempt to collect that audit, private artifact
`20260829T225655Z-05bd71e8`, stopped after the reference cohort and published
no comparison. Commit `5efc697fe10b` had made the PAC generator require both
the local listener and exact workload target ports, while three dedicated
decrypted runners still passed only the listener port. Commit `11af5b11b318`
updated those callers, added a cross-runner regression test, and restored the
fail-closed exact-target PAC scope. The complete harness then passed 122/122
tests before the successful audit above. This is harness provenance, not a
candidate residual measurement.

A proposed asymmetric reuse of the outer root response was also rejected by
the mandatory causal-history preflight without implementation. The suggested
shape would keep the ordinary root `GET` response open as the
target-to-client lane and use a separately paired `CONNECT` only for
client-to-target bytes. Although that exact arm name had not appeared before,
all of its wire-changing causes are already bracketed by admitted failures:
the paired two-stream tunnel improved the SOCKS focus/Whole views by only
11.7%/6.4% in `a9ffaf1c49a77777`; the ordinary-GET carrier was neutral for
SOCKS and traded a 17.2% HTTP focus gain for a 14.2% Whole regression in
`9a73f27ea029dcd5`; the retained-open bounded response carrier worsened every
reported HTTP view in `59cb2995524f019d`; and CSS/JS resource interleaving
collapsed to -6.5%/-2.7% for SOCKS and -2.9%/+0.7% for HTTP in four-block
replication `7a9df73a476bf808`.

The decrypted direct-H2 audit above also contradicts the proposed causal
model: Firefox completed its small root DATA stream before issuing the later
resource-request wave. Keeping that root response alive for tunneled target
bytes would therefore invent a stable half-duplex carrier rather than copy a
native Firefox stream lifecycle. It would additionally require a new
cross-channel backpressure, half-close, and pairing layer in both client and
Caddy while combining two changes that individually fell below the 20%
compatibility-break threshold. There is no untested causal premise here which
justifies that cost, so no product, server, harness, build, or capture work was
started. Future preflight must treat root/resource-response reuse, including
renaming a real resource as the downstream lane, as this rejected hybrid
rather than retrying its component mechanisms.

### H2 pre-launched reference lifecycle audit

The next investigation began with the mandatory exact and causal history gate,
not a product implementation. The cold command-line H2 control above measured
a 135-ms startup-heavy request gap and was not the Selenium cohort used by the
current matrix. Conversely, commits `0f80b315388f`, `d5d41e52bfbd`, and
`bbc166eea5e9` built a repeated-navigation decomposition only for H3. The
parser-process, activation-process, and full-process arms had already moved
real parser/channel work across process boundaries and all lost to the retained
H3 candidate; the larger full-tree IPC form and its two-wave follow-up were
also rejected. Thus reproducing that process topology for H2 would repeat a
closed causal family. What remained unmeasured was the actual pre-launched H2
reference lifecycle.

Commit `f97476fdbac1` added only a Selenium backend to the existing decrypted
H2 runner; command-line behavior and every product/Caddy path remained
unchanged. Clean isolated artifact
`20260829T233038Z-27a63bf9` then used a browser which was ready before capture
and navigated only after capture began. The direct root GET was packet 16,
response HEADERS packet 19 at 0.985 ms, and terminal root DATA packet 20. CSS
and script GETs did not appear until packets 22--23 at 27.627/27.736 ms; later
resource GETs followed at 29.201--29.916 ms. The matching NaiveFox root began
at packet 15, while its first CONNECT request/response appeared at packets
22--23 only 1.099/1.284 ms after that root request. This closes the earlier
cohort mismatch: the roughly 27-ms native phase is neither cold browser launch,
target RTT, nor a large root body, and a fixed pause would still worsen the
observed early server-byte deficit.

Four reference-only H2 lifecycle collections then enabled ordinary
`nsHttp`/`nsCSSLoader` logging without changing or rebuilding Firefox. The
runner was intentionally stopped after the reference cohort by supplying a
non-starting candidate, so it correctly retained private diagnostics and
published no passive candidate distances. The final clean-worktree artifact is
`20260829T235802Z-3f09871c`; earlier private replications
`20260829T234644Z-11c2fd2a`, `20260829T234910Z-bcda1188`, and
`20260829T234939Z-a31fc615` had an unrelated temporary repeat-analyzer/docs
edit in the worktree and are used only to check lifecycle stability. Every
reference capture completed in the isolated namespace with an empty network
mutation log.

The clean sample measured 31.667 ms from direct root response HEADERS to the
CSS GET. Its exhaustive coarse decomposition was 0.775 ms to root suspension,
4.095 ms in the ordinary document-channel suspend/resume phase, 2.960 ms to
parser body delivery, 0.201 ms to stylesheet discovery, 0.168 ms to child
`AsyncOpen`, 14.996 ms to parent `RecvAsyncOpen`, 3.198 ms to parent
`InvokeAsyncOpen`, 4.974 ms through parent channel open/transaction dispatch,
and only 0.300 ms from socket-thread dispatch through
`Http2Session::AddStream` to the wire. Across all four diagnostics the total
was 33.029/27.869/30.380/31.667 ms. The dominant child-to-parent interval was
remarkably stable at 14.055--14.996 ms; parent channel dispatch was
2.766--5.160 ms. After H2 `AddStream`, the GET reached the wire in only
0.136--0.235 ms.

An attempted run of the older H3 repeat-navigation tool also failed closed and
must not be promoted to safe evidence. Private artifact
`20260829T233458Z-bab07379` contained the eight requested tokenized trees plus
one Firefox-generated `/favicon.ico` GET, while the analyzer requires exactly
seven GETs per navigation. Its pristine same-base reference also lacks the
product-branch-only fine-grained `NATIVE_CHANNEL_ACTIVATION_DIAGNOSTIC`
markers. A read-only coarse decomposition was possible, but no analyzer or
fixture relaxation was retained merely to rescue this side diagnostic.

The H2 result identifies the missing native interval but does not justify
copying its implementation into NaiveFox. The earlier real multi-process
screens already show that process startup, parser IPC, and native-channel
rendezvous add their own stable traffic and scheduling without improving both
target views. The new audit therefore strengthens their rejection: H2 encoder,
socket dispatch, and wire emission are not the bottleneck, and future work must
not rename full-process reconstruction as an H2 IPC, PBackground, or channel
bootstrap experiment.

The same preflight rejected two other no-code variants. Negotiating a smaller
TLS record-size limit would add a ClientHello marker while reproducing the
already rejected record-fragmentation/ordinal family. Adding more directional
H2 lanes would quantitatively repeat the rejected paired-stream tunnel: Gecko's
12-MiB initial H2 receive window already exceeds the complete canonical flow,
so extra lanes cannot aggregate flow-control capacity and would only add
HEADERS, pairing, and reordering. Neither proposal reached product, Caddy,
harness, build, or passive-screen code.

### H2 root-document size endpoint

The next idea also passed through the complete exact and causal history gate
before implementation. The history already contained H2 resource trees,
Early Hints and final-response preloads, document-start/HEADERS/body barriers,
and the H3 outer-resource size campaign. The H2 `initial`, bulk-download, and
bidirectional controls changed only small HTML programs, while
`--browser-page-base-size` changed tunneled resources rather than the root
document. No prior run had scaled the H2 root body itself. This made root size
a distinct site-envelope question rather than a renamed resource or admission
experiment.

Commit `6dff7c66142d` added a diagnostic-only
`--document-body-size` fixture input. It pads the exact same HTML response path
seen by Firefox A/B, the outer preamble, and the tunneled browser, preserves
the resource URLs and completion semantics, is limited to H2 gate/smoke runs
and 64 KiB, and cannot be combined with another fixture-shape axis. It changes
neither product nor Caddy defaults. The ordinary measured `browser_page` root
is 494 bytes including its fixed-length completion token.

Predeclared one-block endpoint artifact `46354f735ce3d8a6` (seed
`2026083073`) used a 65,536-byte root, inner HTTPS/H2, Selenium, the isolated
WSL namespace, Firefox A/B, and both current listener policies. All four
participants passed capture-drop, network-mutation, inner-H2, offload, and
normal-drain checks. Its descriptive distances were:

| H2 policy at 65,536-byte root | 1--16 | 17--32 | 1--32 | 250 ms | Whole |
| --- | ---: | ---: | ---: | ---: | ---: |
| SOCKS `document-first-buffer-task-overlap` | 0.07051 | 0.31378 | 0.16014 | 0.15433 | 0.33178 |
| HTTP `document-first-buffer-http-connect` | 0.06084 | 0.14497 | 0.10085 | 0.16218 | 0.33533 |

The 17--32 values are much lower than the separate canonical small-root
dashboard, but this is not a joint improvement. The 250-ms and Whole values
move materially upward, and the signed Whole diagnostic reports an initial
50-ms server-wire deficit of 611,296.5/611,169.5 bytes for SOCKS/HTTP, versus
362,854.6/100,530.4 bytes in the separate ten-block canonical artifact. The
large direct Firefox document changes the early server envelope more than the
candidate can reproduce; extra root volume therefore transfers residual out
of packets 17--32 instead of removing it. Cross-artifact differences and one
block cannot support inference, but they are sufficient for the predeclared
screening stop rule because both requested aggregate views regress strongly.
No larger run, product build, Caddy change, or default promotion was started.
The 64-KiB limit remains a functional safety bound, not a residual-equivalence
claim, and future work must not retry root padding as an H2 timing fix.

### H2 server stream-concurrency premise check

The next server-side proposal was stopped by the mandatory exact and causal
history gate before implementation. Full-history and current-tree searches
found prior measurement of `SETTINGS_MAX_CONCURRENT_STREAMS`, the rejected
two-directional-CONNECT experiment's client connection-slot override, and the
rejected synthetic SETTINGS/PING family, but no experiment which lowered the
Caddy H2 server's advertised stream limit. The mechanism was therefore
syntactically new: unlike a no-op SETTINGS marker, it would serialize native
resource admission.

The measured lifecycle makes the only potentially effective endpoint unsafe.
In safe decrypted artifact `20260829T233038Z-27a63bf9`, direct Firefox opened
the stylesheet and script as streams 2 and 3 at 27.627/27.736 ms; their server
responses were already present at packets 25 and 29. The next request began at
packet 26, but its response did not begin until packet 227. Consequently a
limit of two cannot remove either of the two resource responses already inside
packets 17--32. A limit of one could, but the first long-lived proxy CONNECT
would then permanently consume the only stream after the root completed. A
second target connection or origin could not share that H2 connection, while
a direct site would fetch its blocking stylesheet and script serially. Older
diagnostic evidence also observed up to six ordinary browser target
connections, confirming that this is a real lifecycle rather than a
theoretical edge case.

Caddy 2.11.2 does not expose this field in its Caddyfile or JSON server model;
the stock `x/net/http2` default is 250 and the only relevant knob is the
internal `http2.Server.MaxConcurrentStreams`. A private Caddy fork would thus
be required merely to test an endpoint which either misses the focus window
(`2` or more) or creates an unbounded multi-origin/page-load regression (`1`).
No Caddy fork, harness arm, build, or passive capture was created. Future
preflight must not retry a static low H2 stream limit as a safe site-independent
fix; any genuinely new concurrency proposal must preserve multiple
long-lived CONNECT streams and ordinary parallel page loading by construction.

Two further proposals were closed by retained evidence rather than code. A
combined optimistic-local-reply plus pre-response ClientHello pipeline would
still be the rejected early CONNECT request-DATA family. That earlier arm
already included the optimistic local reply, captured the real 1,822-byte
inner ClientHello, and delivered it as request DATA before `200`; its focus
gain was only 3.2%/5.9% for SOCKS/HTTP and HTTP Whole regressed 17.3%.
Separately, dialing the target before `200` made both listener focus views
approximately 0.77. Combining the two could overlap only target dial and the
first target TLS flight. The measured local-listener premise puts the complete
1,827-byte ClientHello less than 0.9 ms after local success, while the direct
Firefox gap which needs explaining is 28--33 ms of parser/channel/IPC work.
The composition therefore has no untested event capable of closing the main
gap and would repeat two incompatible rejected causes. No client or Caddy
source was restored from the retired diagnostics.

Changing the tunneled origin from H2 to H1.1 had also already been measured
with the current listener controls. Artifact `06350ee8cab9df76` used the same
`browser_page`, outer H2, and `inner_transport=https` (the fixture's
HTTPS/H1.1 server). Its one-block current controls measured
`0.45765/0.31613` for SOCKS and `0.45128/0.40461` for HTTP at packets
17--32/Whole. Fresh four-block HTTP control `b0454b18ae89a387` measured
`0.46318 [0.42335, 0.50301]` and
`0.26826 [0.23575, 0.30440]`. These screens are not an H1.1-versus-H2 paired
inference, but they rule out the proposed large structural reduction: their
focus values are not lower than the separate current inner-H2 dashboard, and
the four-block HTTP Whole point is similar. Creating several ordinary target
connections instead of the canonical single inner-H2 CONNECT is therefore not
a new unmeasured cause and did not receive a duplicate run.

### H2 nested request lifecycle diagnosis

Before adding instrumentation, the mandatory exact and causal-history search
checked the existing native-proxy floor, local first-inner-byte timing,
inner-H2 validation, and repeated/pre-launched Firefox lifecycle diagnostics.
The first three did not retain a request timeline; the last decomposed direct
outer root-to-resource discovery only. No retained artifact correlated the
current default's outer root/CONNECT with the tunneled H2 root, stylesheet,
script, images, and completion. This missing decomposition is distinct from
another parser/process reconstruction or admission-boundary experiment.

The predeclared diagnostic is one isolated, unshaped H2/inner-HTTPS-H2 block
with Firefox A/B and both current listener defaults, the canonical page, seed
`2026083074`, and only packets 17--32 plus Whole. The opt-in
`--h2-request-timing` harness reads already-existing Caddy access logs after
capture and shutdown. It publishes only a separate sanitized timeline using
`log timestamp - handler duration` as a coarse request-start estimate. This
does not add log fields, timing barriers, product code, a Caddy fork, or a
Firefox build. Its purpose is to locate the early server-byte deficit, not to
select a new default from one block or feed HTTP semantics into passive
features.

The first plumbing attempt, private artifact `3e128eb9258b2b81`, completed its
first proxy navigation but stopped before publishing any metrics: the new
summary accepted single-design `h2_b...` identities while the selected
superblock planner emits `h2_sb...`. The validator was corrected to the actual
superblock contract, with a regression test that consumes the real planner's
output. This failed run is not candidate or residual evidence.

Two further plumbing collections (`1a3ed0a8763d08d8` and the private-snapshot
follow-up `b2e28a878b737d35`) stopped on a duplicate API image request, again
without publishing metrics. The snapshot proved one outer root and one
CONNECT, but two inner image-destination GETs of the canonical 34-byte JSON
API response, followed by an empty favicon request. This is consistent with
Firefox repeating the invalid image after its speculative attempt, not an
extra proxy session. The diagnostic now explicitly retains one bounded API
repeat only with the unchanged image destination, JSON MIME, and 34-byte
response, plus one optional empty favicon. It still rejects duplicate roots,
stylesheets, scripts, ordinary images, completions, or further API attempts.
No request is removed from the passive capture and no fixture response is
changed to make the diagnostic pass. Private log slices are retained on error
so future admission failures can be investigated without another capture.

Clean-worktree artifact `8c774dd53fa31ff7` completed the predeclared four-member
block. All participants passed the unchanged capture-drop, offload,
network-mutation, protocol, and drain gates; both proxy workloads passed inner
H2 validation, and all four request timelines passed. Product and stock Caddy
were not rebuilt. The coarse server intervals were:

| Participant | Outer root to CONNECT | CONNECT to inner root | Workload root to CSS | Workload root to script | Outer root to workload completion |
| --- | ---: | ---: | ---: | ---: | ---: |
| Firefox A | n/a | n/a | 29.113 ms | 29.137 ms | 53.937 ms |
| Firefox B | n/a | n/a | 28.934 ms | 28.933 ms | 53.921 ms |
| Current H2 SOCKS | 1.538 ms | 5.660 ms | 54.826 ms | 54.820 ms | 90.154 ms |
| Current H2 HTTP CONNECT | 1.145 ms | 4.964 ms | 58.963 ms | 58.967 ms | 93.319 ms |

All root handlers completed in 0.498--0.860 ms. The direct resource wave
arrived at Caddy about 29--33 ms after root; the tunneled waves did not reach
the inner Caddy until about 62--66 ms after the outer root. Both proxy
workloads had two canonical API image requests, whereas each direct Firefox
control had one; every participant also made one empty favicon request. These
events are preserved, not filtered from the passive sample. Descriptive
17--32/Whole distances were `0.65340/0.42868` for SOCKS and
`0.52648/0.40525` for HTTP, with first-50-ms server-wire deficits of
679,622/680,339 bytes. One block is not a new default dashboard or inference.

The new localization is the extra roughly 26--30 ms between the tunneled
document request and its resource wave, beyond the direct browser's ordinary
29-ms phase. It is not explained by the roughly 5-ms CONNECT-to-inner-root
setup or by slow fixture root handlers. Access logs do not show when the
browser receives or parses the body, however, so this does not yet assign the
extra interval to NaiveFox, Caddy, or Firefox's proxy/channel path. The
browser-side follow-up below supersedes any interpretation of this one-block
difference as a constant nested-tunnel penalty. Production defaults and
fronting-site requirements remain unchanged.

#### Browser-side follow-up: document handoff, not a constant tunnel delay

The exact and causal-history preflight rechecked the closed classifier/cache,
manual-proxy-versus-PAC, and parser/process-topology experiments, plus the
already-fixed accepted-socket Nagle issue. None was reopened. The new task was
attribution of an already-observed interval, not another implementation of
those mechanisms. Ordinary `nsHttp`/`nsCSSLoader` logging was enabled without
rebuilding Firefox, NaiveFox, or Caddy, or changing browser preferences or the
fixture page. All following runs used the isolated unshaped namespace,
same-base Selenium-prelaunched browser, canonical inner HTTPS/H2, and current
defaults.

Single-SOCKS lifecycle artifact `1ec060018f1c3ca0`, seed `2026083075`, already
failed to reproduce the proposed extra 26--30 ms. The canonical 494-byte HTML
body reached the browser transaction 0.055 ms after its response-header
processing; root suspend/resume took 4.790 ms and the CSS transaction reached
socket dispatch at 28.823 ms. This diagnostic collected no Firefox controls
and published no residual comparison.

Clean-worktree artifact `7e642a9b7006c992`, seed `2026083076`, then collected
one four-participant block with both server request timelines and ordinary
browser logs. All four capture/network/timeline participants and both inner-H2
workloads passed. In collection order:

| Participant | Server workload root to CSS | Browser root headers to CSS socket dispatch | Root suspend to resume | CSS child open to parent receive |
| --- | ---: | ---: | ---: | ---: |
| Firefox B | 53.080 ms | 51.674 ms | 26.017 ms | 11.278 ms |
| Current H2 HTTP CONNECT | 57.785 ms | 55.657 ms | 30.003 ms | 10.873 ms |
| Current H2 SOCKS | 32.494 ms | 30.616 ms | 4.655 ms | 13.460 ms |
| Firefox A | 57.496 ms | 56.264 ms | 33.457 ms | 13.385 ms |

Every browser was already processing the root body within 0.054--0.062 ms of
root header processing, before the long suspension. Root server handlers took
only 0.547--0.906 ms. Outer-root-to-inner-root setup was 7.532 ms for HTTP and
7.898 ms for SOCKS. The direct controls now exhibited the longer interval,
while SOCKS did not: its dominant variation is therefore not an invariant
26--30-ms nested transport delay. Each participant made one API image request
and one empty favicon request; none was removed from capture. Logging can
perturb scheduling, so this block is lifecycle evidence only, not a new
passive ranking, default matrix, or proof that SOCKS became faster.

The offline HTTP log mapping initially found two `HandleContentStart` events
on the root transaction. They are the local proxy CONNECT response and the
later origin response, not duplicate root requests. The latter is anchored to
the canonical HTML body; the former precedes it by 6.602 ms. Transaction
pointer lifetimes also have to be respected because Firefox can reuse freed
objects. This mapping was corrected using the retained logs, without another
capture or dropping a network event.

Finally, single-SOCKS lifecycle artifact `47e393e61eb24d6a`, seed
`2026083077`, added the existing `DocumentChannel` log module to identify the
suspending caller. `DocumentLoadListener::OnStartRequest` immediately preceded
root `Suspend` by 0.021 ms. Redirect-to-real-channel finished at 27.754 ms,
`ResumeSuspendedChannel` ran at 27.764 ms, and root `ResumeInternal` at
27.885 ms, all relative to origin-response header processing. The suspension
itself lasted 27.319 ms; the root body had already reached the browser
transaction at 0.057 ms. CSS socket dispatch followed at 54.448 ms.
The upstream `DocumentLoadListener::DoOnStartRequest` and
`ResumeSuspendedChannel` call sites confirm this document handoff. The earlier
H2 audit's description of this interval as a "classifier" phase was therefore
incorrect and is corrected above; these timings do not measure a URL
classifier lookup.

This rejects the interpretation of the observed extra 26--30 ms as a constant
inner-tunnel penalty and prevents a misdirected Caddy buffering fix,
classifier/cache retry, or another renamed full-process reconstruction. It
does not establish the cause of all handoff variance or solve the residual.
No product default, fronting-site restriction,
resource-size recommendation, or canonical matrix entry changed. There was no
product/browser/Caddy rebuild, size/link matrix, or compatibility break.

Strict decrypted artifact `20260826T051112Z-deaf291f` admits the H3-only
`tree-resource-committed-overlap-css` experiment. It uses the same root and
64-KiB stylesheet as `tree-complete-css`, but releases CONNECT only after
Gecko reports `NS_NET_STATUS_WAITING_FOR` for that stylesheet. The trace proves
one QUIC identity and ClientHello, root completion before the resource commit,
CSS GET before CONNECT, CSS response after CONNECT, and identical HTTP
semantics/content length in the paired control. Two-block passive artifact
`c1bd74f7b299c8a1` found a descriptive improvement for packets 17--32 and
1--32, but a regression for packets 1--16 and the first 250 ms. It is a causal
screen, not evidence for tuning asset volume or selecting a product default.

Future paired captures pass the exact same scenario path and block-scoped wire
token to the direct reference, the candidate outer preamble, and the inner
browser. The local completion file is still removed before every participant,
so reusing a wire token cannot satisfy the next controller from a stale marker.
Safe metadata records this token/reset policy, the fixed
`browser-done + 250 ms` cutoff, and fail-closed preamble-drain admission. This
removes request-target and root-body differences without tuning a response
size or copying decrypted fields into the passive classifier.

Each paired view also retains diagnostic-only top passive features and mean
signed packet-size sequences. The sequence records the Firefox A/B mean
absolute disagreement (the local noise floor) and each arm's mean delta from
the matched Firefox midpoint. These aggregates help distinguish a reduced
13--20 signal from a divergence merely shifted after packet 16. They never
enter the arm distance, bootstrap, sign-flip tests, or Holm family, and contain
no decrypted fields, HTTP semantics, stream identifiers, endpoints, or raw
timestamps.

Tree results collected before the decrypted H3 profile split are invalid.
Those runs injected `network.http.http3.alt-svc-mapping-for-testing` into the
NaiveFox process even though its outer H3 route was already explicit. The root
GET could use the proxy connection while the resource GETs used the newly
ready test mapping and a second QUIC connection. In particular, private
capture `20260824T123959Z-725a1454` diagnoses harness contamination; it is not
product evidence and must not be used in passive or decrypted conclusions.
The direct same-base Firefox profile still needs the test mapping, while every
NaiveFox arm profile must omit it.

Controlled same-base screening must also exclude host network mutation as an
experimental variable. WSL mirrored networking can project host VPN
route/address churn into Linux, where Gecko correctly emits
`network:link-status-changed`, runs `VerifyTraffic`, and marks an active H3
session `DontReuse`. This is real Firefox network-change behavior, not a pool
reuse defect and not something production code may suppress. Use
`NAIVEFOX_CAPTURE_ISOLATED_NETWORK=1` to run the complete localhost experiment
inside a stable private Linux network namespace. Independently of that mode,
the harness rejects every sample that observes a link/address/route mutation
after its sample monitor starts. Fixture cold reset and namespace convergence
finish before this measurement boundary; the monitor is then active before
NaiveFox startup or reference-browser startup, fails closed if its own process
or netlink parser fails, and drains queued events before confirming the sample
boundary closed.

Packet-shape screening inside the private namespace sets loopback MTU 1500 and
disables GRO, GSO, TSO, UDP segmentation, and GSO-list aggregation. H2 rejects
outer TCP payload segments larger than 1460 bytes. H3 rejects
any proxy UDP frame whose captured UDP length exceeds 1500 bytes. Without this
step, Linux can expose one host-side UDP GSO superframe as a 13--20 KiB
"packet" even though it is segmented into ordinary QUIC datagrams before the
wire. Private run `909bdbd9c1d68824` was collected before this invariant was
added. Its arm rankings and packet-13--20 sequence are offload-contaminated and
must not be cited as wire-level camouflage evidence.

The first passive multi-arm attempt after that fix, private run
`5f45fb110cc57517`, exposed the same contamination in a second harness entry
point and was stopped after two samples. Its shell profile helper had inferred
"direct H3 reference" from the absence of a SOCKS port, which also describes
the private profile owned by the NaiveFox process. That entire run, including
its gate sample, is invalid and must be discarded. Profile creation now
requires an explicit `reference`, `naivefox`, or `socks-browser` role and
fails before capture if a NaiveFox/SOCKS profile contains either forced test
mapping preference or a pre-existing `AlternateServices.bin`. Only the direct
H3 reference role may contain the mapping.

The default reference is the pinned current-Nightly artifact already used by
the quick capture diagnostics, so it is a version-drift experiment. For a
same-base experiment, set `NAIVEFOX_CAPTURE_MODE=same-base` and the three
`NAIVEFOX_CAPTURE_REFERENCE_*` paths shown above. Same-base is the primary
stack-parity mode but is never built implicitly or made an ordinary merge
prerequisite.

Successful single-arm runs leave only `metadata.txt`, `features.csv`,
`metrics.json`, and `summary.txt` below the sanitized run directory. Multi-arm
runs instead retain `metadata.txt`, `features-superblocks.csv`,
`arm-comparison.{json,txt}`, and
`arms/<arm>/{features.csv,metrics.json,summary.txt}`. The paired arm report is a
model-free, within-`experiment_block` relative ranking. For every protocol and
passive feature view it measures bounded featurewise excess outside the common
Firefox A/B envelope, with feature scales derived only from Firefox control
disagreement. It reports workload-stratified paired-block bootstrap intervals,
paired sign-flip tests, and a Holm family-wise correction across eligible
comparisons. Arm labels are never classifier inputs and no arm-dependent
feature screening is performed.

Arm-specific classifier reports from a multi-arm run are explicitly
screening-only, even when 240 blocks were collected in `research` mode. They
retain descriptive metrics and refit uncertainty, but record
`supports_absolute_verdict=false`; all classifications and the overall
conclusion remain `INCONCLUSIVE`. Select a candidate with the paired report,
then preregister it and collect a fresh single-arm confirmation such as
`--mode research --naivefox-arm root`. Experimental
`tree-complete`, `tree-early-overlap`, `tree-resource-committed-overlap-css`, `tree-root-overlap`,
`tree-warm-css-304`, and `tree-overlap` single-arm runs remain
screening-only, and
the fixed default superblock deliberately excludes them to bound collection
cost. Screening rows must not be reused as
confirmatory evidence.

This ranking is not an absolute camouflage verdict: the same Firefox A/B rows
define the envelope, so there is no independent third Firefox null observation.
Gate and smoke mode, fewer than 30 paired blocks per protocol, or any workload
with only one block are explicitly insufficient for paired inference. The
distance also gives equal weight to correlated engineered features and its
bootstrap intervals are conditional on the observed Firefox-only scales. These
limitations are recorded in both paired output files rather than left as an
interpretation convention.
Metadata includes platform, kernel, architecture, browser/product versions,
build identifiers, library hashes, capture-tool version, revision, mode, and
seed, making artifacts suitable for later regression history.

Current limitations are deliberate: localhost timing is useful only relative
to its Firefox baseline; smoke samples cannot support a camouflage conclusion;
current-Nightly differences may be version drift; platform fingerprints must
not be mixed; optional `tc netem`, persistent-profile and long-idle studies,
cross-version analytics and no-padding A/B remain separate research
extensions. Multi-arm superblocks provide the screening collection design but
cannot support an absolute camouflage conclusion; that requires a fresh,
preregistered single-arm research run. The suite selects explicit runtime
configuration but never mutates production defaults.

## H2 finite-exchange transport experiment

The causal-history preflight checked the complete Git history (including
pickaxe searches for finite exchanges, finite responses, long polling and
exchange sequencing), the carrier sections above, and retained metadata for
`a9ffaf1c49a77777` and `9a73f27ea029dcd5`. Two directional CONNECTs kept two
long-lived streams; the ordinary-GET carrier changed the method but kept one
long-lived raw stream. Neither tested repeatedly completed ordinary HTTP
transactions. Retaining an unfinished fronting response and changing padding
or process topology are also closed, distinct families, not the proposed test.

The first prototype is explicitly diagnostic, H2-only and incompatible with
stock servers. Native Necko channels carry a finite session-open POST, finite
upload POSTs and finite download responses. One logical byte stream is ordered
by per-direction sequence numbers. The initial structural bounds are 64 KiB
per exchange, two outstanding uploads, four outstanding downloads, and bounded
native pipes. A download completes after the first available bounded read;
it does not wait for a full block, a timer, a packet ordinal, a resource-size
target or an estimate of bandwidth. Empty receive requests may wait for target
data, but a response carrying data completes normally and is replaced within
the bounded window. Upload and receive admission are independent, avoiding a
request/response deadlock and one stop-and-wait RTT per useful block.

The initial test deliberately preserves the stock eight Variant-1 records per
direction across exchange boundaries. It also preserves target-dial Fast Open
ordering, existing listener-specific preamble policy, scoped TLS verification,
server-side target DNS/ACL checks and unmodified default CONNECT operation.
This avoids attributing padding removal or a new dial barrier to finite stream
lifecycle. Sequence errors, oversized bodies, unexpected protocol/negotiation,
and transport loss must fail closed. Cancellation, half-close and bounded
buffering are functional gates before passive comparisons.

First collect short randomized H2/inner-H2 screens against both current local
listener defaults and Firefox A/B in the isolated WSL namespace. Validate one
physical outer H2 connection, useful byte integrity and actual normal response
completion. Compare packets 17--32 and Whole first; do not spend a full default,
resource-size or link matrix on a weak candidate. Only a replicated improvement
of at least 20%, without unacceptable regressions, justifies developing this
breaking transport as a default. A short screen is not statistical acceptance.
The ordinary fronting-site requirements and canonical default matrix remain
unchanged during this experiment.

### Finite-exchange prototype functional admission

The prototype client used the explicit `diagnostic-h2-finite-exchanges` boolean and
rejects H3, other transport diagnostics, extra CONNECT headers and native-parser
preamble combinations. Stock CONNECT defaults are untouched. The diagnostic
Caddy overlay was reproducibly built by `build-finite-exchange-caddy.sh` from the
pinned forwardproxy module; it does not replace the fixture's stock binary.
The harness accepts it only by an explicit private fixture path inside the
isolated namespace. Ordinary requests and CONNECT on that same server retain
the unmodified path when the finite version marker is absent.

Two setup smokes (`131d5be5034efaa4`, seed `2026083080`, and
`ce3c6953f9c8d9d1`, seed `2026083081`) failed before establishment with HTTP 407.
Necko correctly prunes `Proxy-Authorization` from ordinary origin requests.
The finite API now uses scoped origin `Authorization`, with redirects forbidden;
the server feeds it into its existing credential verifier only for explicitly
marked finite requests. No shared Necko/authentication behavior was weakened.
The API must reach that authenticated handler before ordinary fronting-page
handlers; unlike the unauthenticated preamble, these are protocol requests, not
cover resources. This is an experimental server-routing requirement, not a
change to the stock-default fronting-page contract.

Seed `2026083082` loaded the inner page but hit an unregistered arm name in the
feature extractor (`bd8b6f3fbb60e8ee`). Seed `2026083083` also completed the
workload but the new marker validator omitted the optional timestamp prefix
(`f88357f9c6d35234`). These harness defects were corrected and regression-tested;
neither failed artifact is a passive candidate score. Clean isolated smoke
`c8dabc167b8b4df8` (seed `2026083084`) then passed the one-connection, one
ClientHello, preamble, inner-H2, unchanged-padding and finite-rotation gates.

The prototype passed 101 focused C++ gtests, 138 harness/lifecycle tests, the
server module's `go test -race ./...`, and incremental product/test builds.
`run-finite-exchange-tests.sh` passed both SOCKS and HTTP CONNECT with TLS,
byte-exact slow 1 MiB download, digest-checked 768 KiB upload, half-close,
four-way concurrency, cancellation, invalid upstream credentials and bounded
normal process shutdown. Test-driver setup errors (a preamble path outside its
allowlist, an omitted newline in the small-body expectation, and treating
SIGTERM as orderly CLI shutdown) were corrected against the existing fixture
contracts; shutdown is now verified through the exact accepted-connection
bound. Functional success does not establish a residual improvement.

The first randomized four-arm block, seed `2026083085`, completed all six
captures and their per-sample gates, but the aggregate analyzer still had a
separate arm-name allowlist. Its rejection was repaired and tested. Existing
feature fragments were revalidated and reanalyzed without recollection or
sample selection as recovered artifact `a6381fe95c5b49d9`; recovery metadata
records that provenance separately from an ordinary successful harness run.

| H2 listener / arm | 17--32 | Whole |
| --- | ---: | ---: |
| SOCKS current default | 0.64830 | 0.41413 |
| SOCKS finite exchanges | 0.71753 | 0.46926 |
| HTTP CONNECT current default | 0.53741 | 0.40328 |
| HTTP CONNECT finite exchanges | 0.71826 | 0.47271 |

This first block is negative in both target views: SOCKS worsens by 10.7% / 13.3%
and HTTP CONNECT by 33.7% / 17.2%. It is not evidence for a breaking default.
Two additional short paired blocks were collected to check whether the negative
direction repeats despite browser scheduling variance. No outer-size, shaped
link, full default matrix or release build is justified by these results.

### Finite-exchange replication and decision

Fresh artifact `9de8396c3b9573d7` (seed `2026083086`) completed normally after
the analyzer admission fix. It contains two randomized paired blocks: all four
client/listener arms plus Firefox A/B, 12 controlled navigations in total. The
same unshaped isolated H2/inner-H2 fixture, canonical browser workload, cold
profiles, prelaunched Selenium control and unchanged eight-record padding were
used. All 12 samples passed capture health, network-mutation and one-connection
gates; every finite sample also proved negotiation and repeated successful
ordinary-response completion. The private server digest was
`e7367ab1b188aa2e701129f746b8d087d45ae84f5fa355da52acd3f418846a70`.

| H2 listener / arm | 17--32 | Whole | Change from same-run listener default |
| --- | ---: | ---: | --- |
| SOCKS current default | 0.34590 | 0.24936 | control |
| SOCKS finite exchanges | 0.36622 | 0.35459 | +5.9% / +42.2% |
| HTTP CONNECT current default | 0.37813 | 0.25720 | control |
| HTTP CONNECT finite exchanges | 0.37581 | 0.35068 | -0.6% / +36.3% |

Whole regressed in both the initial block and the fresh replication. HTTP's
0.6% packets-17--32 reduction in the repeat is effectively neutral at this
screening scale, not the required 20% gain; SOCKS worsened. These are diagnostic
point estimates from three blocks total, not a statistical camouflage claim.
Do not compare their absolute values with another campaign's Firefox-only
normalization or replace the canonical default matrix with these rows.

Byte accounting helps explain the negative direction without inventing a
fixed browser pause. In the repeat, mean client wire bytes rose from 18,573.5
to 27,288 on SOCKS and from 20,852.5 to 26,752 on HTTP. Server wire traffic in
the first 128 packets fell from 89,965.5 to 47,967.5 bytes and from 89,833.5 to
38,727.5 bytes respectively. Extra request/response lifecycle traffic is
consistent with displacing server payload in the early packet-index views;
normal finite completion alone does not reproduce Firefox's application
resource-discovery schedule. This is an interpretation of the measured
accounting, not proof that every possible finite-exchange design must fail.

Post-hoc accounting of the same retained features (no new captures or changed
ranking) separates the Whole score into disjoint feature families. The entries
below are additive score changes, not percentages or causal effect estimates.
Explicit timing includes millisecond-valued features, idle gaps and timed
bursts; packet structure excludes those timing features. The remaining group
includes byte/direction aggregates, including aggregates in clock-time windows.

| Whole contribution, finite minus same-run default | SOCKS | HTTP CONNECT |
| --- | ---: | ---: |
| Explicit timing and timed bursts | +0.04531 | +0.03465 |
| Packet sizes and directions | +0.01659 | +0.02172 |
| TLS record structure | -0.00163 | +0.00004 |
| Remaining aggregates and handshake features | +0.04496 | +0.03706 |
| Total | +0.10523 | +0.09348 |

For a concrete early-window mismatch, Firefox and both listener defaults had
a mean 1,500-byte server packet at index 28, whereas finite SOCKS had a mean
209-byte client packet. At index 29 Firefox again had 1,500 server bytes, while
finite HTTP had 209 client bytes. Direction and packet placement changed, not
just an overall latency scalar. SOCKS mean whole duration was essentially
unchanged, 118.037 to 116.885 ms; HTTP changed from 124.855 to 132.160 ms.
Whole is a distance over the entire feature vector, not page-load duration.

The archived implementation also exposes two plausible contributors that this
screen did not isolate by ablation. `FillDownloads()` replenishes four receive
GETs independently of resource discovery, and upload POST boundaries follow
available opaque tunnel bytes rather than browser resource boundaries. In
addition, `OnDataAvailable()` buffers each response and `FlushDownloads()`
delivers it only after `OnStopRequest()`; the server likewise accepts a complete
finite upload body before forwarding it. These completion barriers can alter
inner TLS/request scheduling even though individual bodies are bounded and the
directions are pipelined. A successful native response completion is therefore
not equivalent to reproducing Firefox's resource-discovery/application work.
The byte/direction changes and score decomposition are observed; attributing
specific fractions to headers, receive-ahead or completion buffering would
require distinct controlled ablations and is not established here.

Decision: reject this bounded, independently sequenced upload/download
prototype for default and do not proceed to a full breaking implementation.
Its implementation and tests remain reproducible in commits `ab19acb41807`
and `f6f1500cd629`. The diagnostic client, server overlay and capture admission
were removed from the active tree; all affected runtime, configuration and
test/harness files were checked byte-for-byte against the pre-experiment
version. Stock Caddy, both H2 listener defaults, H3 defaults, the canonical
matrix and the stock fronting-site contract remain unchanged. Future preflight
must treat this exact finite-cell topology as tested, and require a different
causal premise rather than merely renaming the cells or changing a bound.

After retirement, incremental minimized product and test builds passed, as did
all 100 restored NaiveFox C++ gtests and 172 analysis/harness/lifecycle Python
tests. No full Firefox rebuild, generated-source export or default-matrix
replacement was performed for this rejected candidate.

### Finite-exchange read-through ablation

The follow-up history preflight checked all-ref commit messages and pickaxe
history for finite/body buffering and streaming delivery, the retained finite
capture metadata, and the earlier document-body and server-coalescing reports.
The first-buffer preamble policies change CONNECT admission; server coalescing
and TLS alignment change tunnel write boundaries. None removed the newly
introduced client-side `OnStopRequest` barrier inside a finite response. This
is a controlled correction to the rejected prototype, not another finite-cell
size or timing sweep.

Restore that prototype only as an explicit diagnostic control, and add an H2-only
read-through variant. It forwards validated response bytes from
`OnDataAvailable` as the next ordered response arrives, rather than waiting for
the complete body. It must retain the same finite-response boundary, sequence
ordering, 64 KiB body bound, two-upload/four-download windows, server binary,
eight-record padding and listener-specific preambles. Slots are not recycled
until a response completes normally and its buffered bytes have drained;
backpressure, error propagation and cancellation remain mandatory. The server
upload-body barrier is deliberately unchanged so this test isolates receive
delivery. No resource-size threshold, pacing timer or RTT guess is added.

Before scoring, require the functional probes and a runtime marker proving that
bytes were actually delivered before response completion. The short randomized
same-base H2 screen compares both listener defaults, the original finite arms
and their read-through counterparts with shared Firefox A/B controls, in the
isolated namespace. The preregistered views remain packets 17--32 and Whole.
An improvement over the poor finite control alone is not enough: a breaking
default still needs at least 20% meaningful improvement over the current
listener default and fresh replication. Do not run a full matrix or size/link
sweep unless this new candidate first earns those gates.

The read-through prototype passed incremental minimized product/test builds,
102 C++ gtests and 174 analysis/harness tests. Both original finite arms and
both read-through arms passed the private TLS, exact 1 MiB slow download,
768 KiB upload, half-close, concurrency, cancellation, bad-authentication and
normal-shutdown probes. Read-through probes additionally proved successful
delivery before `OnStopRequest`; the original arms rejected that marker. The
server source and binary are byte-identical to the first finite experiment.
These are functional admissions, not camouflage results.

Two-block same-base artifact `52eeaed67ad9bdad` (seed `2026083087`) passed all
16 isolated captures, including the new per-connection delivery marker. Only
the preregistered packets-17--32 and Whole views were evaluated:

| H2 listener / arm | 17--32 | Whole |
| --- | ---: | ---: |
| SOCKS current default | 0.40875 | 0.30114 |
| SOCKS original finite | 0.37773 | 0.38091 |
| SOCKS finite read-through | 0.30714 | 0.40301 |
| HTTP CONNECT current default | 0.41450 | 0.33340 |
| HTTP CONNECT original finite | 0.32026 | 0.40024 |
| HTTP CONNECT finite read-through | 0.30342 | 0.38115 |

Read-through improves packets 17--32 by 24.9% / 26.8% relative to the same-run
SOCKS / HTTP defaults, but Whole regresses by 33.8% / 14.3%. Relative to the
original finite controls, the focus window improves by 18.7% / 5.3%, while
Whole changes by +5.8% / -4.8%. This is a useful early-window diagnostic, not
a qualifying default or a replicated statistical claim. The original finite
arms themselves score better in this campaign's early window than in prior
campaigns, reinforcing the need for within-block comparisons and fresh controls.

The receive-completion barrier was therefore not a sufficient explanation of
the Whole failure. Client wire bytes remain elevated (25,948 / 27,625 versus
19,704 / 17,686.5 for the defaults), and server bytes in the first 128 packets
remain deficient (41,698 / 36,265.5 versus 90,401 / 87,587). The code and this
screen are retained in history; no default or fronting-site contract changes.

### Finite-exchange callback-affinity correction

Functional admission of the subsequent upload variant exposed a shared bug in
the diagnostic adapter, before any new comparative capture. Private probe
`finite-exchange-probes.s280pm5e` reset a second local HTTP connection in the
original finite arm. Instrumented repeat `finite-exchange-probes.sy70g37m`
recorded SIGSEGV after the receive-read-through SOCKS concurrency test. The
debugger caught `PumpDirection::Produce()` on the socket thread dereferencing
its cancelled owner. A conditional main-thread breakpoint then proved the
other side of the race: `FiniteExchange::FlushDownloads()` wrote the native
pipe, whose inline `CallbackHolder::Notify` invoked
`PumpDirection::OnInputStreamReady()` on the main thread.

The pump's null-target `AsyncWait` had relied on native socket streams to
provide socket-thread callbacks. A native pipe has a different callback
contract; the first finite prototype had not explicitly restored that thread
affinity. All finite variants inherited the defect, not just upload v2.
Historical finite captures remain observations of that unsafe prototype and
must not qualify a default or establish the final effect of read-through.

The correction supplies the existing socket event target to both pump callback
registrations only for the diagnostic finite adapter, with release assertions
on callback thread affinity. Ordinary socket-backed defaults keep their exact
null-target callback behavior. New comparative collection must use a corrected
binary and fresh controls; include the original finite arms as well as the two
read-through variants so the unsafe earlier scores are not reused as controls.

### Finite-exchange response-lifetime correction

After fixing callback affinity, repeated functional probes still exposed
intermittent truncation, including the v1 receive-only SOCKS arm in private
probe `finite-exchange-probes.xzwrpdtf`: 34,752 of 131,072 bytes arrived, with
every received byte matching the expected prefix. Thus it was not evidence
against the v2 upload change specifically, and no new capture was admitted.

The server's request-context cancellation guard covered both the ordered pipe
operation and the subsequent HTTP response write. A focused regression test
cancelling the request context during an otherwise successful response write
reproduced closure of the entire logical tunnel. The guard is now stopped
after the pipe operation, before response completion; cancellation still
interrupts blocked pipe reads/writes, and a failed response write still closes
the session. The regression fails before this change and passes afterwards.

Three complete six-arm functional rounds then passed, with private probe IDs
`a61atabm`, `z6vb6jpe`, and `665h90o8`: TLS, exact 1 MiB slow download,
768 KiB slow upload, half-close, four concurrent 128 KiB downloads,
cancellation, wrong credentials, and bounded process shutdown. This is
functional admission for a new screen, not evidence that the historical
finite captures were safe. The Caddy overlay's complete Go race-test suite
also passes in a private network/mount namespace. Its two upstream ACL tests
use namespace-local static host mappings for rejected destinations instead
of consulting host DNS; no tests are skipped or host files changed.

### Finite-exchange server upload read-through ablation

History preflight for the symmetric server-side barrier checked all-ref
pickaxe history for upload streaming and `ReadAll`, the finite metadata above,
and the rejected early-CONNECT-DATA report. That older experiment moved initial
payload before the CONNECT response; this one preserves establishment and
changes only forwarding of already-accepted ordinary finite POST bodies. It
also does not batch server target reads or align inner TLS records. The
unmeasured variable is the explicit full-body wait introduced by the finite
server adapter itself.

Keep client receive read-through enabled and replace server upload `ReadAll`
with bounded streaming copy, preserving ordered delivery, exact length checks,
error closure, half-close and cancellation. The one-byte finite protocol marker
selects v1 (original upload buffering) or v2 (streamed upload); a session may
not mix them. Authentication, target ACL/DNS, response boundaries, window sizes,
padding and every unmarked CONNECT remain unchanged. Add a deterministic
prefix-before-request-EOF server test before capturing. Compare original v1,
v1 receive read-through, v2 read-through and the two listener defaults in short
randomized paired blocks; the same two residual views and promotion gates
apply. This is not permission to tune resource sizes, introduce a delay or
launch a full matrix.

Functional admission includes the prefix-before-request-EOF test, rejection
of short/long/oversized streamed bodies and mixed session versions, the
response-lifetime regression above, 103 project gtests and 174 Python tests.
The minimized product/test graphs were rebuilt incrementally, not Firefox.
The corrected diagnostic Caddy binary has SHA-256
`f8afd1146cfbebd66b689bacf6e0f59f1911246e0a3df9a5e5636e3cc309f02e`.
The next screen is preregistered as seed `2026083088`, two randomized blocks,
eight client arms plus shared Firefox A/B, H2 / inner HTTPS-H2, unchanged
scheduled outer fixture and private MTU-1500 network namespace. Defaults and the
fronting-site contract remain unchanged.

### Corrected finite-exchange H2 screen

Seed `2026083088` completed as safe artifact `adf8c603b590072c` on
`dbf22ddf2906`, with the corrected client callback affinity and Caddy response
lifetime shared by all finite arms. Both randomized blocks and all 20
participants passed transport, completion, padding, stream ownership, drain,
capture-drop and network-mutation admission. Both streaming markers were
checked where required. Private capture inputs were deleted after success.

These are within-campaign screening distances, not a replacement for the
canonical default matrix and not statistically established gains: two blocks,
`INSUFFICIENT_FOR_INFERENCE`, no independent Firefox null verdict. Lower is
better; percentages compare with the same listener's default in this campaign.

| Arm | p17--32 | Whole | p17--32 vs default | Whole vs default |
| --- | ---: | ---: | ---: | ---: |
| SOCKS current default | 0.38048 | 0.31653 | — | — |
| SOCKS original finite | 0.36945 | 0.34185 | -2.9% | +8.0% |
| SOCKS receive read-through | 0.38717 | 0.35177 | +1.8% | +11.1% |
| SOCKS receive + upload read-through | 0.34145 | 0.37551 | -10.3% | +18.6% |
| HTTP CONNECT current default | 0.37918 | 0.28976 | — | — |
| HTTP CONNECT original finite | 0.35150 | 0.35130 | -7.3% | +21.2% |
| HTTP CONNECT receive read-through | 0.34426 | 0.35849 | -9.2% | +23.7% |
| HTTP CONNECT receive + upload read-through | 0.35780 | 0.35972 | -5.6% | +24.1% |

No finite variant improves Whole, and none meets the >=20% breaking-default
gate. The receive-only early-window improvement from the unsafe earlier
prototype is not reproduced here. Do not start a full implementation, full
matrix or resource/link sweep for these variants. This rejects the measured
variants as default candidates, not every possible finite-exchange protocol.

Post-hoc accounting is retained in `mechanism-accounting.json` beside the safe
dataset and exactly reconstructs the unchanged distance metric. For
bidirectional SOCKS, the Whole timing/burst contribution improves by 0.00450,
but packet size/direction, other aggregates and TLS-record contributions
worsen by 0.02548, 0.03573 and 0.00228: net +0.05899. Thus another pause cannot
by itself explain this arm's failure. For bidirectional HTTP CONNECT,
timing/bursts add 0.03627 of the net +0.06995 penalty; non-timing structure also
contributes substantially.

The raw aggregates support investigating exchange overhead, not assuming it
has been causally isolated: bidirectional SOCKS / HTTP send 25,690.5 / 25,175
client wire bytes versus 16,601.5 / 18,646 for their defaults. Server bytes in
the first 128 packets are 45,982.5 / 49,149 versus 93,588 / 98,125. A smaller
completion barrier has not removed the extra exchanges or their changed
packet/record ordering. These data do not prove that trimming headers alone
would recover the deficit; any continuation needs a distinct preregistered
transport premise, not another fixed delay or a new name for read-through.

There is no new fronting-site requirement and no production compatibility
change. H2 and H3 defaults, their canonical matrix, and the existing site
contract remain unchanged. The finite measurements do not establish
size-independent or slow-link performance.

The corrected prototype and its functional tests are preserved in
`dbf22ddf2906`; all three finite variants and their fixture-only protocol were
retired from the working product after this screen. Runtime and integration
files were verified byte-for-byte against pre-finite base `0c67b4388495`,
excluding this experiment log. Incremental minimized product/test rebuilds,
100 project gtests, 172 Python tests and the staged H2 config/listener gate
against stock Caddy all pass after retirement. The latter runs in a private
network namespace and exercises SOCKS, authenticated SOCKS and HTTP CONNECT.
No Firefox rebuild, default promotion, full matrix, resource sweep or history
rewrite was performed. The retained safe datasets and prototype commits allow
reproduction without retaining an unsuccessful transport in the product.

### Budgeted streaming finite responses: preregistration

The next history preflight searched the complete all-ref Git history with
pickaxe terms for finite/bounded streaming and response byte budgets, current
documentation, the three retained finite metadata sets, and corrected source
`dbf22ddf2906`. Earlier finite variants all completed each download after one
available pipe read. Client read-through and streamed uploads did not change
that response-completion rule. The rejected 1-ms server read-coalescing and
inner-TLS alignment experiments instead delayed or regrouped target delivery;
they did not keep immediately flushed bytes inside a size-bounded ordinary
HTTP response. The present proposal must not repeat those delivery barriers.

Two alternatives were screened out before implementation. Piggybacking open
with an optimistic first ClientHello overlaps the already rejected early
CONNECT-DATA/local-success family. Combining separate Naive header, payload
and padding writes has no opportunity in the pinned server: its existing
codec already constructs each padded record in one buffer and calls `Write`
once. Compacting session/auth headers remains a separate, unmeasured overhead
idea; it is not combined with the response-count experiment.

Restore only the corrected diagnostic adapter as a control. Keep both client
receive read-through and server upload streaming enabled. The new v3 changes
download response completion only: retain the existing 64-KiB maximum body,
forward and flush every positive read immediately, and finish at that budget
or target EOF. H2 response length is unspecified until END_STREAM, not padded
to a target. No delivery waits for a full block, timer, TLS boundary, resource
size, RTT or bandwidth estimate. An underfilled response may remain open
while the tunnel is idle, just as the previous next empty receive request
could; its already received bytes must remain available to the application.
Successful data progress refreshes the existing idle safety timeout.

Preserve the two-upload/four-download windows, ordered delivery, bounded
pipes, eight Variant-1 records, target Fast Open, TLS/auth/ACL checks, listener
admission and fronting-page policy. Every unmarked CONNECT remains unchanged.
The one-byte finite version marker selects the new mode explicitly and mixed
versions fail closed. Required tests prove a flushed prefix before the target
produces the suffix, exact 64-KiB rotation and short EOF, cancellation during
an unfinished response, response-write failure, and the prior callback-affinity
and response-lifetime fixes. Failed partial responses must abort the H2 stream,
not report a successful truncated body.

After functional admission, collect two randomized H2/inner-H2 blocks at seed
`2026083089`: SOCKS/HTTP defaults, corrected v2 read-through controls and v3
budgeted candidates, with shared Firefox A/B. Keep the scheduled outer/inner
fixtures, cold profiles, capture cutoff and isolated MTU-1500 namespace fixed.
Compare p17--32 and Whole only; retain a proof of a completed full-budget
response and report request/response count and byte aggregates separately
from passive features. Improvement over v2 alone is insufficient: the breaking
default gate remains a replicated >=20% gain against current defaults without
unacceptable regressions. No full matrix or size/link sweep for a weak screen.
There is no change to the site's contract or the canonical default matrix.

### Finite-exchange native-pool ownership correction

The first v3 functional round stopped before reaching v3: the old v1
receive-read-through HTTP arm truncated a parallel 128-KiB download at 66,984
bytes in private probe `smzg6q09`. The received prefix was exact. Client
diagnostics recorded HTTP 409 on later exchanges, not a codec mismatch.
An aggregate audit of the server log showed successful requests on the
session's initial TCP source port and rejected requests on another source
port. Native Necko pooling had moved ordinary requests between outer
connections, while the diagnostic server required exact `RemoteAddr` equality.

All-ref history preflight found no previous correction for this ownership
case. A deterministic port-migration test fails with HTTP 409 for v1, v2 and
v3 before the fix. The adapter now compares normalized peer IP addresses,
retaining the same credential digest, random 128-bit session capability,
handler/version checks, sequence/replay bounds and target ACL. Different
addresses, malformed addresses and wrong credentials still fail closed;
only the source port may change. This correction is shared by controls and
candidates. Ordinary CONNECT defaults are unaffected.

The camouflage admission rule still requires exactly one outer ClientHello;
the functional correction is not permission to accept extra connections in
the primary capture. Earlier singleton measurements are not reused as fresh
controls. No residual measurement was collected during the failed functional
round, and no default or site policy changed.

### Budgeted-response functional admission

After the ownership correction, all eight finite/listener combinations passed
in private probe `pciwebne`; both v2 controls and both v3 candidates repeated
successfully in `gzoy8pf3` and `xf4p22jo`. Every arm exercised TLS, exact 1-MiB
slow download, 768-KiB upload, half-close, four concurrent 128-KiB downloads,
cancellation, bad authentication and bounded process shutdown. The v3 probes
also required a completed 65,536-byte response and receive-before-stop proof.

Incremental minimized product/test builds, 104 C++ gtests, 175 Python tests and
the complete server Go race-test suite passed. The server tests include v2/v3
upload-prefix and length validation, byte-budget rotation without target EOF,
a short final response, cancellation before and after the first body bytes,
write failure and the earlier response-lifetime regression. A real local H2
client additionally receives the prefix immediately and then a stream error,
not successful EOF, when the target fails mid-response. All network checks
run in private WSL namespaces, including the Go suite.

The admitted Caddy overlay SHA-256 is
`b7bed380ba5845cd121d489962b2a51b6af3e01204a5be8e5988b1e241708272`.
The new per-sample `finite-exchanges/*.json` files contain only summed numeric
counters and arm/schema/scope labels. Their scope is the entire product
session including post-capture shutdown; open/close requests are excluded.
They are mechanism diagnostics, not new passive classifier features or a
change to capture cutoff. Missing/duplicate terminal counters fail closed.
Body-byte counters describe encoded tunnel bytes, including Naive padding,
not the application resources themselves.
The planned seed `2026083089` screen remains p17--32 and Whole only.

### Budgeted-response H2 screen: fewer exchanges, no default win

Seed `2026083089` completed on `7be2c317e82f` as safe artifact
`25de70a0225eed54`. All 16 participants passed isolated-network, completion,
padding, single-outer-ClientHello, drain and capture admission. Every candidate
proved full-budget rotation. The safe artifact includes eight terminal counter
files plus `finite-counts-summary.json` and the unchanged-distance decomposition
in `mechanism-accounting.json`. Successful private capture inputs were deleted.

Two randomized blocks are a diagnostic screen (`INSUFFICIENT_FOR_INFERENCE`),
not a statistical camouflage verdict or a replacement for the canonical
default matrix. Lower is better; the default comparisons below are within
this campaign only.

| H2 listener / arm | p17--32 | Whole | p17--32 vs default | Whole vs default |
| --- | ---: | ---: | ---: | ---: |
| SOCKS default | 0.41901 | 0.30911 | — | — |
| SOCKS v2 read-through control | 0.42534 | 0.40280 | +1.5% | +30.3% |
| SOCKS v3 budgeted response | 0.39065 | 0.38571 | -6.8% | +24.8% |
| HTTP CONNECT default | 0.39524 | 0.35644 | — | — |
| HTTP CONNECT v2 read-through control | 0.37937 | 0.39327 | -4.0% | +10.3% |
| HTTP CONNECT v3 budgeted response | 0.39875 | 0.36429 | +0.9% | +2.2% |

The transport premise did work: mean started download requests fell from
28.5 to 15 for SOCKS (-47.4%) and 27.5 to 15 for HTTP (-45.5%). Each v3
session completed ten full-budget responses. Upload request counts stayed
near 11--12.5. These counters include post-capture shutdown and exclude
open/close requests; they are not additional features of the primary metric.
Client wire bytes fell from 25,518 to 22,788.5 and 26,009 to 23,657.5 versus
v2, but remain above their defaults of 17,766 and 19,737. Server bytes in the
first 128 packets rose from 44,187.5 to 63,956.5 and 53,342.5 to 69,157, still
below defaults of 105,866 and 87,289.5.

Relative to v2, Whole improves by only 4.2% / 7.4%; neither listener beats its
default. SOCKS retains both timing/burst (+0.03455) and non-timing (+0.04205)
Whole penalties against its default. HTTP timing/bursts improve slightly
(-0.00237), but non-timing structure adds +0.01021. Reducing response count is
therefore helpful to the prototype, not sufficient to solve its camouflage
gap. There is no >=20% default candidate, no full matrix/size/link sweep, and
no production or site-contract change. This result does not prove that a
different bounded protocol cannot work.

### Data-activated receive credits: preregistration

The next all-ref history preflight covers finite window/credit/lookahead,
`FillDownloads`, initial receive terms, the retained finite arms and their
source. They all issue four download requests immediately after open. The
older rejection of additional H2 lanes concerns transport flow-control
capacity (the 12-MiB initial window), not when these four ordinary GETs start.
No earlier finite variant starts with one request and expands on received data.

Keep v3 and its server byte-for-byte. The single new client variable is to
start with one outstanding receive request, then restore the four-request
maximum on the first positive, validated response body callback. Forward those
bytes immediately; do not wait for a complete response, timer, resource size
or throughput estimate. This removes three speculative GETs from the initial
exchange without reducing the steady-state receive window. It is an event-
driven startup experiment, not another fixed pause or H2 SETTINGS change.

The motivation is specific but not yet causal proof: v3 still has small
client-direction packets where the Firefox controls already deliver large
server-direction packets in the late p17--32 region. Retain strict markers
for initial one-credit admission and expansion after data, plus the existing
read-through, rotation, body cap, integrity and single-outer-session gates.

After functional checks, seed `2026083090` will compare both defaults, both
ordinary v3 controls and both data-activated v3 candidates in two randomized
H2/inner-H2 blocks with Firefox A/B. Only p17--32 and Whole are preregistered.
The breaking-default threshold, absence of site-specific waits, and the rule
against a full matrix/resource sweep for weak candidates are unchanged.

Functional admission for the data-activated window passed incremental minimized
product/test builds, 105 C++ gtests and 175 Python tests. The ordinary v3
controls and new candidates passed the complete four-arm integrity probes
twice (`mefig5i4`, `gizsm6we`). The new capture gate checks actual initial
request count and event ordering: ready, one-credit admission, streamed bytes,
window expansion, full-budget completion. Missing, duplicate, reversed and
timer-triggered evidence is rejected. Server source and the admitted Caddy
binary remain byte-identical to the preceding v3 screen.

### Data-activated receive credits: negative H2 screen

Seed `2026083090` completed on `c2c29bdded77` as safe artifact
`bd4a3c377ba1a291`. All 16 participants passed the same admission gates;
candidate markers additionally proved one initial request and expansion after
streamed data but before full-budget completion. Caddy is unchanged from the
preceding screen. Private successful capture inputs were deleted. The safe
artifact retains terminal counters, `finite-counts-summary.json`,
`wire-aggregate-summary.json` and `mechanism-accounting.json`.

These are within-campaign two-block diagnostics, not inference and not new
default-matrix values. In particular, do not compare the absolute p17--32
distances with the previous campaign: the Firefox observations and distance
normalization differ. The contemporaneous defaults and controls are mandatory.

| H2 listener / arm | p17--32 | Whole | p17--32 vs default | Whole vs default |
| --- | ---: | ---: | ---: | ---: |
| SOCKS default | 0.57654 | 0.33963 | — | — |
| SOCKS v3 four-credit control | 0.57708 | 0.40297 | +0.1% | +18.6% |
| SOCKS v3 data-activated credits | 0.57635 | 0.43450 | -0.03% | +27.9% |
| HTTP CONNECT default | 0.54448 | 0.30949 | — | — |
| HTTP CONNECT v3 four-credit control | 0.55403 | 0.38339 | +1.8% | +23.9% |
| HTTP CONNECT v3 data-activated credits | 0.53002 | 0.39721 | -2.7% | +28.3% |

Against v3 itself, p17--32 changes by -0.1% / -4.3%, while Whole worsens by
7.8% / 3.6%. Both controls and candidates start fifteen download requests and
complete ten full-budget bodies, as expected: this changes startup ordering,
not total response count or steady-state capacity.

The early direction/size mismatch remains. Mean client bytes in the first
32 packets rise from 4,548.5 to 6,016 for SOCKS and 4,548 to 5,962.5 for HTTP;
server bytes rise only from 4,539.5 to 4,656.5 and 4,536.5 to 4,615.5.
Server bytes in the first 128 packets improve from 64,831 to 70,564 and
73,399 to 74,262, still below defaults of 89,593 and 98,049. The experiment
does not establish the identity of encrypted payloads from these aggregates.
It does show that deferring speculative GETs did not yield the hoped-for
server-heavy early profile. The SOCKS Whole penalty against v3 includes
+0.01514 timing/bursts and +0.01640 non-timing terms; HTTP adds +0.00504 and
+0.00877 respectively. A timer is not justified by these results.

Reject this startup-credit variant as a default candidate. Together, the two
new screens isolate response count and initial receive lookahead; neither
meets the >=20% breaking-default threshold, so neither proceeds to a full
implementation, full matrix or resource/link sweep. These observations do
not prove that all possible finite protocols fail, but repeating fixed block
sizes or adding another arbitrary pause has no support here. Any next trial
needs a distinct transport premise, history preflight and fresh controls.
H2/H3 defaults, the canonical matrix and the fronting-site contract are
unchanged; no size-independent or slow-link improvement is claimed.

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
