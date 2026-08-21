# NaiveFox agent instructions

These instructions apply to `netwerk/naivefox/` and to the minimal documented
hooks elsewhere in the Firefox tree. Read the repository-root `AGENTS.md` too.

Before changing anything, read completely:

1. `netwerk/naivefox/README.md`
2. `netwerk/naivefox/ROADMAP.md`
3. `netwerk/naivefox/UPSTREAM.md`
4. `netwerk/naivefox/KNOWN-ISSUES.md`
5. `netwerk/naivefox/PRE-EXPORT-AUDIT.md`

Do not assume access to an earlier conversation. The files above and current
Git state are authoritative.

## Current branch model

- `main`: clean Mozilla mirror; never add NaiveFox changes.
- `naivefox`: full-tree reference implementation and source-only Firefox refresh layer.
- `minimal`: full-tree product/minimization source of truth.
- `minimal-source`: generated, independent orphan-history product snapshot.

Work on the branch appropriate to the task. Never merge `main` directly into
`minimal`, never merge generated `minimal-source` changes upward, and never
edit `minimal-source` as the source of truth. Firefox upstream is frozen unless
the user explicitly starts a controlled refresh milestone.

## Architecture that must be preserved

NaiveFox is one binary with one Gecko runtime:

```text
SOCKS5 / HTTP CONNECT listeners
  -> common TunnelSession / DuplexPump / Variant-1 padding
  -> Necko HTTP/2 or HTTP/3 proxy CONNECT
  -> NSS/PSM and Neqo
  -> unmodified Naive-compatible Caddy forwardproxy
```

Firefox owns TLS, HTTP/2, HTTP/3, QUIC, flow control, pooling, DNS/proxy
infrastructure, and certificate validation. Do not introduce curl/libcurl,
OpenSSL/BoringSSL/rustls, quiche, msquic, ngtcp2, standalone Neqo, custom H2/H3,
or a second client/listener architecture.

Single-process networking is a deliberate current product decision. The
socket process remains disabled because raw upgraded tunnel streams are not
published through that IPC path. Do not re-enable it casually.

The supported user surface is NaiveProxy-compatible config with SOCKS5 TCP
CONNECT and HTTP CONNECT listeners, strict H2 (`https://`), strict H3
(`quic://`), developer Auto mode, common padding, and persistent-or-temporary
profile selection. UDP ASSOCIATE, CONNECT-UDP/MASQUE, forward GET/POST proxying,
TUN/TAP, GUI, listener authentication, and proxy chains remain out of scope.

## Working rules

Start with read-only state checks:

```bash
git status --short
git branch --show-current
git log -5 --oneline --decorate
git remote -v
```

Preserve unrelated or user-owned worktree changes. Do not reset, checkout, or
delete them. Keep upstream hooks minimal, generic where possible, separately
committed, tested, and recorded in `UPSTREAM.md` with files, purpose,
project-only insufficiency, risk, tests, and commit.

Use Firefox's `mach`/`moz.build` build system. For normal incremental source
changes use the narrowest justified target. After build-graph/configure/Cargo
changes and for every standalone clean gate, use a full NaiveFox product build
with the minimal graph:

```bash
MOZCONFIG=netwerk/naivefox/mozconfig-minimal \
NAIVEFOX_OBJDIR=/absolute/external/objdir \
./mach build -j4
```

`mach build binaries` is not a valid clean-source acceptance command because it
can skip early generators.

Do not build the ordinary Firefox browser package during the normal
upstream/minimal cycle. Gate 1 is source/inventory/conflict review only; Gate 2
builds and tests the NaiveFox minimal product, and Gate 3 builds and tests the
standalone export. An ordinary Firefox build is allowed only in a separate,
explicitly requested same-base capture/comparison and is never a merge or
release condition.

Never put credentials, proxy URLs with userinfo, TLS keys, pcaps, profiles,
raw logs, or private fixture state in Git or documentation. Runtime logs must
not include credentials or `Proxy-Authorization`.

## Minimal-source workflow (normative)

The goal is a standalone source tree, not merely a list of linked objects.
Compiler/link reports do not cover every configure or generated-action input.

Source closure is the union of:

- one attested successful configure file-access trace;
- Linux and Windows `backend.RecursiveMakeBackend.in` and
  `config_status_deps.in` inputs;
- compiler and generated-action `.d`/`.pp` prerequisites, including relative
  source paths;
- generated Makefile prerequisites and active component manifests;
- target-filtered Cargo normal/build/proc-macro dependency packages;
- linked/runtime closure reports;
- explicit project, bootstrap, runtime-resource, and license inputs.

Discovery and clean export are different operations:

1. Maintain one disposable diagnostic source tree.
2. Add missing input *classes* to it in place.
3. Continue the same configure/full-build objdir until it passes.
4. Collect a final report from that fresh diagnostic build and require it to be
   a subset of the conservative target allowlist.
5. Commit build/export tooling, then regenerate reports on a clean exact source
   commit; reports live in a report-only child commit.
6. Run `tools/export-minimal-source.sh --plan-only`. It must validate without
   copying source.
7. Only then run one physical clean export to a new empty path.
8. Make the full checkout and original objdirs unavailable; bootstrap,
   configure, and fully build the clean export.
9. Run Linux and Windows acceptance before creating the first orphan
   `minimal-source` commit.

Do not restart clean exports to discover one file at a time. Do not patch the
generated product tree manually. A failing clean export invalidates an input
class/report/tool and is fixed on `minimal`, then regenerated.

The product snapshot contains the NaiveFox product README, config example,
required source/build/test/staging inputs, curated technical documents,
licenses, a content-hashed manifest, and `UPSTREAM-BASE`. It excludes this
file, coding-agent handoffs/tasks, internal roadmap/pre-export notes, report
generators, `.git`, objdirs, artifacts, captures, profiles, logs, and secrets.

## Required validation

Before publication, proportionally run:

- Python/shell formatting and syntax checks;
- target closure assertions and provenance validation;
- full Linux build from the isolated export;
- project/focused tests;
- H2, H3, Auto, config, SOCKS5, HTTP CONNECT, padding, integrity,
  concurrency/robustness, no-home profile, and staged-runtime gates;
- native Windows build plus file logging, H2/H3/Auto, malformed SOCKS/HTTP,
  churn/concurrency, clean shutdown, and stability soak;
- optional isolated capture/comparison only when explicitly requested, using
  ordinary Firefox and NaiveFox packages built from the same Firefox base; it
  is not a routine gate.

Record exact commands, commits, outcomes, sizes, and limitations in
`TEST-REPORT.md`, `MINIMISATION-REPORT.md`, `UPSTREAM.md`, `ROADMAP.md`, and
`KNOWN-ISSUES.md`. Mark a milestone complete only after its acceptance gate,
not merely after compilation.
