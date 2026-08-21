# Minimal build and source export

This guide is the maintainer runbook for the `naivefox-full-source` branch. It deliberately
contains no release SHA, date, size, or test transcript. Git and the generated
evidence files are the source of those values.

The supported flow is:

```text
validated naivefox-full-source -> source commit S -> report-only commit E
                   -> generated naivefox-minimal-source snapshot
```

`naivefox-full-source` retains the full Firefox checkout for merges and review. The build
graph selected by `mozconfig-minimal` is the headless NaiveFox application;
none of the commands below configures or builds the Firefox browser.

## 1. Prepare the branch

Start from a clean `naivefox-full-source` checkout after the corresponding
`firefox-upstream` refresh has been reviewed:

```bash
git switch naivefox-full-source
git status --short
git merge-base --is-ancestor firefox-upstream HEAD
```

Review `MINIMAL-PATCHES.md` whenever a Firefox refresh changes an inventoried
file. Keep object directories outside the checkout so they cannot enter source
closure evidence.

## 2. Build and test the minimal graph

Linux product build:

```bash
./netwerk/naivefox/tools/build-product.sh linux \
  --objdir /absolute/path/to/obj-naivefox-linux
NAIVEFOX_OBJDIR=/absolute/path/to/obj-naivefox-linux \
  ./netwerk/naivefox/tools/verify-staged-runtime.sh \
  package/naivefox-linux-x86_64
```

The default graph has tests disabled. To build the same NaiveFox application
with its test targets, use a separate object directory and opt in explicitly:

```bash
export MOZCONFIG=netwerk/naivefox/mozconfig-minimal
export NAIVEFOX_ENABLE_TESTS=1
export NAIVEFOX_OBJDIR=/absolute/path/to/obj-naivefox-linux-tests
./netwerk/naivefox/tools/build-product.sh linux
export LD_LIBRARY_PATH="$NAIVEFOX_OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
./mach gtest 'NaiveFoxTunnelSessionLifecycle.*'
```

Unset `NAIVEFOX_ENABLE_TESTS` before a release build. Do not substitute a
browser mozconfig or run an ordinary Firefox build as a product gate.

Windows cross-build and staging:

```bash
./netwerk/naivefox/tools/build-product.sh windows \
  --objdir /absolute/path/to/obj-naivefox-windows \
  --bootstrap
```

On Windows, verify the staged directory, including the deterministic
TunnelSession stop-lifecycle churn:

```powershell
py -3 netwerk/naivefox/tools/verify-staged-windows-smoke.py `
  --package-dir C:\absolute\path\to\naivefox-windows-x86_64
```

Run the integration suites appropriate to the change as described in
`test/integration/README.md`. H2, H3, Auto, config, padding, parser robustness,
and staged-runtime checks are release gates when their code paths change.

## 3. Freeze source commit S and evidence commit E

Commit all final source, build rules, tools, tests, and documentation first.
That clean commit is `S`:

```bash
test -z "$(git status --porcelain)"
S=$(git rev-parse HEAD)
```

Build and test that exact clean commit. Then collect all six target reports in
one operation, using the validated Linux and Windows object directories and a
new external work directory:

```bash
python3 netwerk/naivefox/tools/collect-minimal-source-evidence.py \
  --linux-objdir /absolute/path/to/obj-naivefox-linux \
  --windows-objdir /absolute/path/to/obj-naivefox-windows \
  --work-dir /absolute/path/to/new-evidence-work
```

The collector derives the Firefox and NaiveFox ancestry from Git. It rejects
abbreviated, stale, or hand-entered provenance and atomically installs only:

```text
netwerk/naivefox/reports/build-inputs-linux-x86_64.json
netwerk/naivefox/reports/build-inputs-windows-x86_64.json
netwerk/naivefox/reports/closure-report-linux-x86_64.json
netwerk/naivefox/reports/closure-report-windows-x86_64.json
netwerk/naivefox/reports/configure-inputs-linux-x86_64.json
netwerk/naivefox/reports/configure-inputs-windows-x86_64.json
```

Review those files, stage exactly that set, and create direct child `E`:

```bash
git add \
  netwerk/naivefox/reports/build-inputs-linux-x86_64.json \
  netwerk/naivefox/reports/build-inputs-windows-x86_64.json \
  netwerk/naivefox/reports/closure-report-linux-x86_64.json \
  netwerk/naivefox/reports/closure-report-windows-x86_64.json \
  netwerk/naivefox/reports/configure-inputs-linux-x86_64.json \
  netwerk/naivefox/reports/configure-inputs-windows-x86_64.json
git diff --cached --name-only
git commit -m 'reports: freeze naivefox-minimal-source evidence'
E=$(git rev-parse HEAD)
test "$(git rev-parse HEAD^)" = "$S"
python3 netwerk/naivefox/tools/assert-closure.py
```

Do not make a documentation, tool, or SHA-fix commit after `E`. If anything
outside the six reports must change, reset the release candidate to a new
source commit `S`, rerun the builds, regenerate all evidence, and create a new
direct report-only child `E`.

## 4. Export and validate minimal-source

Export from clean `E` into a new directory:

```bash
test -z "$(git status --porcelain)"
plan_a=$(mktemp /tmp/naivefox-plan-a.XXXXXX)
plan_b=$(mktemp /tmp/naivefox-plan-b.XXXXXX)
./netwerk/naivefox/tools/export-minimal-source.sh --plan-only >"$plan_a"
./netwerk/naivefox/tools/export-minimal-source.sh --plan-only >"$plan_b"
cmp "$plan_a" "$plan_b"
rm -f "$plan_a" "$plan_b"

export_root=$(mktemp -d /tmp/naivefox-export.XXXXXX)
./netwerk/naivefox/tools/export-minimal-source.sh \
  "$export_root/minimal-source"
python3 netwerk/naivefox/tools/validate-minimal-source.py \
  "$export_root/minimal-source"
```

The two planner runs and byte-for-byte comparison are mandatory for every
publication. The exported manifest is the public compact file inventory; rich
diagnostic categories remain temporary evidence and are not published.

Build Linux and Windows again from the generated directory with new external
object directories:

```bash
cd "$export_root/minimal-source"

./netwerk/naivefox/tools/build-product.sh linux \
  --objdir /absolute/path/to/export-obj-linux

./netwerk/naivefox/tools/build-product.sh windows \
  --objdir /absolute/path/to/export-obj-windows
```

For the independence gate, run those commands in a disposable namespace, VM,
or container where the full checkout and its old object directories are not
visible. Repeat staging, validation, and integration acceptance against the
exported binaries.

Only then replace the contents of a disposable `naivefox-minimal-source`
worktree and commit one linear generated snapshot. Preserve the
`.github/workflows/` control-plane overlay in that branch; it is intentionally
maintained independently from full-source exports. Never hand-edit the product
tree and never merge it back into `naivefox-full-source`.

## 5. Cleanup

Remove only the temporary export, evidence work directory, object directories,
logs, and staged packages created for the current run. Do not delete shared
caches, existing object directories, or another maintainer's diagnostics.
