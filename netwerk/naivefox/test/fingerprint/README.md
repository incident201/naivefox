# Firefox traffic compatibility findings

These are scoped observations about encrypted client-to-endpoint traffic,
separate from the matched page-capture comparisons and from a general
browser-activity score. They do not describe every connection made by a browser
or every possible Firefox activity.

## Client TLS fingerprints

The sanitized [observation](observation-h2.json) records agreement of both
client JA3 and the JA4 returned by nDPI across upstream Firefox browsing,
upstream Firefox HTTPS CONNECT, NaiveFox classic and NaiveFox no-connect.
Ordinary browser controls include real public sites and the proxy's Caddy
application. A curl control has different client fingerprints. The JSON is
the canonical record of versions, capture/feature hashes and measured scope;
raw captures, generated credentials and logs remain outside Git.

The result means these client fingerprint projections expose no difference
in the measured sessions. It does not mean every ClientHello field is equal,
that nDPI identifies a program as Firefox, or that traffic timing, lengths,
request scheduling or connection reuse are indistinguishable. nDPI can append
site-specific labels such as TLS.Wikipedia; those labels are not transport
regression criteria. H3, resumption and other platforms need separate evidence.

## Legitimate browser repetition

The separate [upload counterexample](observation-repetition-h2.json) records
ordinary upstream Firefox performing sequential `fetch` uploads to Caddy,
with fixed-size and varied-size blocks. Every block was acknowledged by its
size and SHA-256 and checked at both ends. The browser did not execute a
proxy worker, the no-connect carrier, or WebSocket code.

Both ordinary browser controls triggered the preregistered generic alarm for
repeated substantial pairs of client TLS-record lengths. Simple repetition
therefore occurs in legitimate Firefox activity and is not, by itself, a
proxy identifier. This is a constructed browser counterexample, not an
estimate of how frequently the pattern occurs on public websites.

NaiveFox no-connect exhibited stronger repetition in these samples, but the
exact sequences and occurrence counts were not equal across roles. Rejecting
this generic alarm does not prove that every specific pattern is harmless,
or that either complete transport is browser-indistinguishable. Hardcoding
the observed byte lengths, raising a threshold on the same controls, or
removing ordinary uploads from the reference would not validate a new alarm.

The JSON defines the counting rule and preserves the measurements and artifact
hashes. Its units are TLS records, not TCP packets, HTTP requests or a
similarity percentage. The fingerprint agreement also held in this screen.

## What the scoring experiments did not establish

The intended question is whether observed traffic is compatible with a
plausible, legitimate Firefox activity, not merely whether a trained model can
separate labeled examples in one corpus. Corpus separability can be useful
evidence within a stated scope, but it does not establish that a trace could
not have come from an ordinary browser.

The [scoped classifier outcome](observation-classifier-screen.json) records
strong proxy detection on its held-out examples, a false alarm on ordinary
Firefox, and no alarms on its ordinary Firefox Caddy controls. It failed the
specified false-alarm operating point. Passing the Caddy control alone was
insufficient. No general browser-likeness score was validated or promoted;
exploratory classifier outputs and alternative score normalizations are not
release evidence.

Fingerprint agreement and the absence of a demonstrated incompatible marker
must not be converted into a percentage or a probability that traffic is
Firefox. A meaningful numerical benchmark would need explicit observer
visibility, browser versions/settings, workload coverage, calibration and
uncertainty. Those qualifications cannot be replaced with a universal score.

The retained findings prove neither that NaiveFox necessarily reveals itself
nor that it is indistinguishable from all ordinary Firefox activity. Future
work should state a specific observable marker and test it against legitimate
browser counterexamples. The experimental collectors, trained models, private
captures, credentials and logs are deliberately not published with these notes.

## Fast regression contract

A future maintained quick test should:

1. Capture an actual successful new TLS session for each selected transport.
   Require a complete ClientHello, normal SNI, working application traffic and
   validated capture health; absent fingerprints must never pass by omission.
2. Compare the complete observed JA3/JA4 sets with a versioned Firefox reference
   collected under the same TLS options. Preserve unknown fingerprints as a
   regression signal. Do not silently add them to the reference.
3. On an upstream refresh, also compare contemporary Firefox with the frozen
   reference: shared drift and NaiveFox-only drift are different outcomes.
4. Return fingerprint match, mismatch or invalid capture, not a behavioral
   percentage. No corpus training is required.

This directory preserves the result and test contract, not a completed CI
runner. The existing bounded collection/extraction prototype and its cached
PCAP/features remain in the ignored fixture laboratory. A portable runner can
be promoted independently of the behavioral benchmark.

Fingerprint definitions: [JA3](https://github.com/salesforce/ja3) and
[JA4](https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md).
