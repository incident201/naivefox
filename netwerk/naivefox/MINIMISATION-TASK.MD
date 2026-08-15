# NaiveFox minimisation task

This document is the implementation roadmap for producing a compact,
standalone NaiveFox source tree without sacrificing safe Firefox integration.
It is authoritative together with `UPSTREAM.md`; functional networking
requirements remain in `README.md`, `ROADMAP.md`, and `TEST-REPORT.md`.

## Target repository model

```text
mozilla-firefox/firefox:main
             |
             v
main            clean Firefox mirror
             |
             v
naivefox        full Firefox + NaiveFox reference
             |
             v
minimal         full Firefox + minimized build/runtime rules
             |
             | tools/export-minimal-source.sh
             v
minimal-source  generated standalone product tree
```

`naivefox` and `minimal` retain the full upstream source checkout.
`minimal-source` has independent orphan ancestry and is never used as a merge
base or source of hand-edited changes. Fixes flow downward and the product tree
is regenerated.

Allowed directions:

```text
upstream/main -> main
main -> refresh/firefox-* -> naivefox
naivefox -> refresh/minimal-* -> minimal
minimal -> validated export snapshot -> minimal-source
```

Direct `main -> minimal`, any direct upper-branch merge into `minimal-source`,
and every reverse merge from `minimal-source` are forbidden.

## Dependency-first order

Minimization proceeds only in this order:

```text
runtime package
    -> build graph
    -> linked dependency closure
    -> source dependency closure
    -> standalone source export
```

Source directories are not deleted from the full-tree branches merely to make
the checkout look smaller. A component is first removed from runtime, build,
and link closure with tests proving equivalence. Only the generated allowlist
export omits source which is outside the proven closure.

## Baseline

The initial minimization baseline is recorded in `UPSTREAM.md`. Before changing
the runtime or build graph, record:

- exact Firefox, NaiveFox, and minimal commits;
- build configuration and object directory;
- package file manifest and byte size;
- ELF `DT_NEEDED` and loaded-library closure;
- largest runtime files and directories;
- H2/H3/Auto/config regression results;
- staged-runtime result outside the object directory.

Every completed group updates a machine-readable report and the human test and
size reports.

## Phase 1: runtime package

Determine the smallest runnable staged package without changing compiled
Gecko. Use an explicit deterministic allowlist and measure successful file
accesses and loaded ELF objects. Remove resources only after runtime smoke,
public HTTPS, strict H2/H3 config workloads, Auto, SOCKS5, HTTP CONNECT,
padding, integrity, concurrency, no-home startup, and copied-package checks
pass.

Acceptance:

- no Firefox build-graph or Necko/Neqo/NSS source change;
- deterministic runtime manifest;
- staged runtime works outside the object directory;
- before/after bytes and largest remaining entries are recorded;
- hidden source, objdir, profile, credential, log, and capture dependencies are
  absent.

## Phase 2: NaiveFox build configuration

Stop building browser-only groups only after proving they are outside NaiveFox
closure. Candidate groups include browser UI/chrome, DevTools, updater, crash
reporter, accessibility, printing, media, WebRTC, UI-only gfx, extensions, and
frontend telemetry.

Each logical group is a separate commit and gate:

```text
build
-> project gtests
-> focused Firefox network tests
-> H2/H3/Auto
-> config/SOCKS/HTTP CONNECT
-> staged runtime
```

The full upstream source remains present on `minimal`.

## Phase 3: link and dependency closure

Produce a machine-readable report of libraries and Gecko modules linked into
NaiveFox, including dependencies introduced by registration or static
constructors. Remove small groups from the link graph and repeat the complete
gate.

The retained closure must include the XPCOM/runtime minimum, Necko, required
HTTP/1.1 paths, HTTP/2, HTTP/3/Neqo, NSS/PSM, NSPR, DNS and proxy services,
required URI/channel/principal/load-info code, prefs/services, and NaiveFox
frontends/config/padding. These are not replaced with custom network or TLS
implementations for size.

## Phase 4: source dependency closure

After the minimized full-tree build is stable, create an allowlist manifest,
normally `netwerk/naivefox/minimal-source.manifest`. It includes:

- NaiveFox sources and tests;
- required Firefox/Gecko and Neqo sources;
- NSS/NSPR sources when built from tree;
- build/configure/mach infrastructure actually used;
- generated-data inputs needed for a clean build;
- licenses and notices for every included component;
- standalone build, validation, and packaging scripts.

The exporter starts with an empty directory and copies manifest entries. A
copy-everything-then-delete blacklist is not acceptable.

## Phase 5: deterministic source export

`tools/export-minimal-source.sh` creates an export below the object/output
directory. It must reject a dirty or unvalidated source unless an explicit
diagnostic mode is selected, and must never overwrite an existing export.

The export contains no `.git`, objdir, build artifacts, profiles, credentials,
logs, captures, or absolute development paths. Its root contains the NaiveFox
`README.md`, this task, upstream policy, build instructions, licenses/notices,
and a generated `UPSTREAM-BASE` with:

```text
Firefox base SHA: <sha>
NaiveFox SHA: <sha>
Minimal SHA: <sha>
Export manifest version: <hash>
Generated at: <UTC date>
```

File order, modes, report content, and source archive generation must be
deterministic for a fixed source commit and declared timestamp.

## Phase 6: isolated standalone build

Copy the export to a clean directory or container and make the original full
source and object directory unavailable. Bootstrap, configure, build, test,
and stage solely from exported content. Absolute paths, developer-home state,
and undeclared generated Firefox inputs are failures.

The resulting package must pass the same core H2/H3/Auto/config/SOCKS/HTTP
CONNECT, padding, integrity, concurrency, certificate, no-home, and wire sanity
gates as `minimal`.

## Phase 7: publish `minimal-source`

Publication begins only after the isolated build passes. The first
`minimal-source` commit is an orphan snapshot. Later generated commits are
linear descendants of the previous product snapshot, not of Firefox or
`minimal`. Publication tooling must not change `main`, `naivefox`, or
`minimal`, and must not force-push without a separate explicit decision.

Recommended clone forms are:

```bash
git clone --branch minimal-source --single-branch \
  https://github.com/incident201/naivefox.git

git clone --branch minimal-source --single-branch --depth 1 \
  https://github.com/incident201/naivefox.git
```

A release source archive is generated from the identical validated export
tree.

## Phase 8: product view

After `minimal-source` independently builds and passes acceptance, make it the
GitHub default branch. The repository root must then show NaiveFox product
documentation, config examples, architecture, build/run instructions, and
upstream traceability rather than Firefox browser documentation.

## Phase 9: full refresh proof

Before declaring the architecture maintainable, perform one controlled Firefox
refresh through the whole chain:

```text
upstream/main -> main
main -> refresh/firefox-* -> naivefox -> full gate
naivefox -> refresh/minimal-* -> minimal -> minimal gate
minimal -> export -> isolated build -> minimal-source snapshot
```

A written workflow without one successful real refresh is insufficient.

## Export validation requirements

The validator checks at least:

- every manifest entry exists and no stale entry remains;
- no forbidden browser directory is exported unless explicitly required;
- no `.git`, objdir, generated binary, profile, credential, log, pcap, TLS key,
  or absolute development path is present;
- root README belongs to NaiveFox;
- all required licenses/notices are present;
- build scripts reference only exported paths;
- `UPSTREAM-BASE` matches the source checkout and manifest hash;
- clean standalone build and package acceptance pass.

## Completion criteria

Minimization is complete only when:

- `naivefox` remains the full reference and Firefox refresh gate;
- `minimal` retains full upstream source while using the minimized build and
  runtime closure;
- runtime and linked closure are materially smaller and measured;
- the source dependency allowlist is explicit and reproducible;
- the exported tree builds without access to the full checkout;
- its binary passes the complete networking and config acceptance suite;
- `minimal-source` has no Firefox ancestry and is the GitHub default branch;
- shallow single-branch clone avoids Firefox history;
- release source archive and branch snapshot come from the same export;
- at least one Firefox refresh has passed all three validation gates.
