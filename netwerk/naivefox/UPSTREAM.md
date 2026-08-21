# Upstream synchronization and product export

This document is the normative maintenance workflow. It deliberately contains
no current commit SHA, dated status, or copied test result. Git ancestry,
machine-readable evidence, `UPSTREAM-BASE`, and annotated release tags record
the exact provenance of each snapshot.

Downstream modifications to existing Firefox files are listed separately in
[`UPSTREAM-PATCHES.md`](UPSTREAM-PATCHES.md).

## Branch model

```text
mozilla-firefox/firefox:main
             |
             v
main            clean upstream mirror
             |
             v
naivefox        full Firefox + shared NaiveFox implementation
             |
             v
minimal         full source + minimized product graph/export tooling
             |
             | generated allowlist export
             v
minimal-source  standalone product snapshot
```

- `main` contains only commits reachable from Mozilla `main`. Update it with an
  explicit fetch and fast-forward; never merge a project branch into it.
- `naivefox` is the source-integration branch. It retains the full Firefox tree,
  all shared product code, downstream hooks, focused regressions, and fixtures.
- `minimal` retains the full tree for ordinary Git ancestry while reducing the
  configured build, link, runtime, and exported source closure.
- `minimal-source` is generated from a validated `minimal` snapshot. Never edit
  it by hand and never merge it into an upper branch.

Allowed long-lived direction is only:

```text
upstream/main -> main -> naivefox -> minimal -> minimal-source
```

Feature branches for shared behavior start from `naivefox`; minimization-only
branches start from `minimal`. Protect all long-lived branches from force-push
and deletion where repository settings permit it.

## Ordinary refresh workflow

The normal cycle has three gates. It never configures or builds a Firefox
browser package.

### Gate 1: Firefox to `naivefox`

Fast-forward the mirror, then merge it on a temporary refresh branch:

```bash
git fetch upstream
git switch main
git merge --ff-only upstream/main

git switch naivefox
git switch -c refresh/firefox-<name>
git merge main
```

Before merging the refresh into `naivefox`:

1. inspect every entry in `UPSTREAM-PATCHES.md`, including clean textual merges;
2. review adjacent upstream changes for semantic conflicts and obsolete hooks;
3. keep every resolution narrow and preserve unflagged Firefox behavior;
4. inspect manifests/configuration for unintended graph changes;
5. run source-level syntax or focused regression checks needed to validate a
   resolution, but do not build the Firefox browser or run the product gate.

Remove a downstream patch when Firefox now supplies an equivalent supported
API or fix. Merge the reviewed refresh into `naivefox` without inserting its
SHA into Markdown.

### Gate 2: `naivefox` to `minimal`

Only a Gate 1 result may enter the minimized product:

```bash
git switch minimal
git switch -c refresh/minimal-<name>
git merge naivefox
```

Resolve minimization conflicts, then validate the complete minimized product
graph. The gate includes Linux and Windows product builds where supported,
focused unit regressions, H2/H3/Auto/config/listener behavior, padding and
integrity, concurrency/backpressure/lifecycle, package manifests, staged
runtime checks, and size/closure assertions. Use the full minimal graph after
build-system or dependency changes:

```bash
MOZCONFIG=netwerk/naivefox/mozconfig-minimal ./mach build -j4
```

If Gate 1 passes but Gate 2 fails, treat the failure as a minimization
integration defect until evidence proves the full reference is also affected.
Do not weaken shared behavior merely to make the minimized graph pass.

### Gate 3: `minimal` to `minimal-source`

Gate 3 starts only after Gate 2 is complete. Use exactly two adjacent commits
on `minimal`:

```text
S  source, build configuration, tools, and durable documentation
|
E  generated evidence only; direct child of S
```

`E` may change only the canonical generated report set. Reports attest `S`, so
no fixed-point commit or follow-up documentation commit is needed. If source or
tooling changes after evidence collection, discard/regenerate the report
change and create a new direct child of the new source commit.

The gate is:

1. prove the checkout is clean and `E` has the required report-only parent/diff;
2. run closure and provenance assertions;
3. run the exporter in plan-only mode twice and require byte-identical output;
4. export once into a new empty directory from `E`;
5. validate the manifest, file modes, hashes, links, licenses, forbidden paths,
   and absence of secrets, VCS data, objdirs, profiles, logs, and captures;
6. copy the export to an isolated location with no access to the full checkout
   or old object directories;
7. configure and build only the exported NaiveFox graph for the supported
   platforms, stage it, and run the applicable product acceptance suites;
8. create one new linear `minimal-source` snapshot and an annotated release tag.

If `minimal` passes but the isolated export fails, fix the allowlist, exporter,
or source closure on `minimal`; never patch `minimal-source` manually.

## Provenance without SHA churn

Tools derive the current references from Git instead of reading copied values
from Markdown:

```bash
git rev-parse HEAD
git merge-base HEAD main
git merge-base HEAD naivefox
```

Every recorded object ID must resolve to one canonical 40-hex commit and satisfy
the required ancestry. Existing but stale ancestors are invalid inputs, not a
reason to update several documents by hand.

The evidence collector generates the full canonical report set into a temporary
directory, cross-validates every report, and installs the set only after all
checks pass. Partial evidence updates are invalid. Exact tool/config inputs are
included in evidence so a change automatically invalidates old reports.

The public export manifest records the exact Firefox base, NaiveFox reference,
minimal source commit `S`, evidence commit `E`, file count, sorted path/mode/hash
entries, and a canonical manifest hash. Internal closure categories and raw
trace details remain build evidence rather than product-tree prose.

`UPSTREAM-BASE` records source provenance that can be known at export time. The
publication commit is represented by the generated branch commit and annotated
tag; it must not use a `NOT_YET_PUBLISHED` placeholder or trigger a follow-up
commit. Validators may diagnose older manifest formats, but exporters create
only the current format.

For each release, therefore:

- collect reports once;
- make one report-only evidence commit;
- export once;
- publish one generated snapshot and annotated tag;
- do not copy the resulting SHAs into active Markdown.

## Minimization rules

The goal is to reduce what is configured, built, linked, staged, and exported,
not to delete the Firefox checkout:

```text
full source -> minimized configured graph -> measured closure
            -> explicit source allowlist -> empty-directory export
```

Prefer build exclusion over deleting upstream directories. Physical deletion
from the full-source branch requires measured benefit and explicit review of
future merge cost. The exporter is allowlist-based; copying the whole tree and
deleting suspected unused paths is forbidden.

Almost all project code belongs under `netwerk/naivefox/`. Before modifying an
existing Firefox file, establish that no usable API exists, preserve default
Firefox behavior, add a focused test, and update `UPSTREAM-PATCHES.md`.

## Refresh cadence and failure ownership

The clean mirror may move frequently, but product branches update only for a
planned refresh: relevant security/network fixes, a required upstream API,
meaningful TLS/H2/H3 behavior changes, or release preparation. Releases remain
on a concrete Git-recorded base between refreshes.

- Gate 1 failure: resolve or remove the downstream Firefox integration.
- Gate 2-only failure: repair minimized configuration/runtime assumptions.
- Gate 3-only failure: repair evidence, manifest, allowlist, or isolated export.
- Shared product behavior changes flow down from `naivefox`; fixes never flow
  upward from generated source.

## Capture policy

An ordinary Firefox package may be built only for an explicitly requested
same-base capture/control comparison. It must use the same Firefox base as
NaiveFox and a separate object/package directory. This diagnostic is outside
Gates 1-3 and never becomes a merge or release prerequisite. See
[`CAPTURE.md`](CAPTURE.md).

## Cleanup

After a successful gate, remove only task-created raw traces, temporary evidence
and export directories, disposable object directories/worktrees, fixture state,
and logs. Preserve pre-existing object directories and user files. Sensitive
failure artifacts may be retained privately only long enough for diagnosis;
their location must remain ignored and their contents must never enter a
commit.
