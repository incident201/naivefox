# Passive traffic verification

Use the same-base Firefox reference and matched ordinary HTTP application
described in [CAPTURE.md](../../CAPTURE.md) and the
[integration guide](../integration/README.md).

The maintained views are p1-16, p17-32, p1-32, 250 ms and Whole. Preserve their
feature definitions, randomized paired controls, complete-session scope and
health checks. A workload or reference change must be explicit and applied to
both baseline and candidate; it must not hide an adverse result.

Per-run observations, captures and reports belong in the chosen temporary
artifact catalog, with source/binary/workload provenance. Earlier classic and
WebSocket-workload observations remain available in Git history; they are not
current supported configurations or acceptance results.
