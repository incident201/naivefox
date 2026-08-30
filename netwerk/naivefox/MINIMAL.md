# Minimal build and source export

This guide is the maintainer runbook for the `naivefox-full-source` branch. It deliberately
contains no release SHA, date, size, or test transcript. Git and the generated
evidence files are the source of those values.

The supported flow is:

```text
validated naivefox-full-source -> source commit S -> report-only commit E
                   -> generated naivefox-minimal-source snapshot
```

`naivefox-full-source` retains the full Firefox checkout for merges and review.
The supported product graphs are Linux x86-64, Windows x86-64, and Android
ARM64 embedded. All remain `--enable-project=netwerk/naivefox`; none of the
commands below configures or builds the Firefox browser or GeckoView.

NaiveFox product mozconfigs pass `--without-intl-api`. This removes the
SpiderMonkey ECMAScript `Intl` API and the ICU4C source/data graph. The
minimal graph still builds the locale parser and Unicode helpers required by
Necko and IDNA, backed by the ICU4X Rust crates already used by the product;
the closure must contain `icu4x_unicode_glue` and no ICU4C objects or shared
libraries. Browser-only date/time formatting, collation, and other `Intl`
components are intentionally not part of this target.

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

Both `classic` and `no-connect` belong to the same minimized product graph.
The native application-cell codec and ordinary Necko HTTP channels do not
require a browser worker, WebSocket bridge, DOM, graphics or SpiderMonkey.
The exact experimental port boundary is maintained in
[NO-CONNECT.md](NO-CONNECT.md).

For iteration, reuse an existing object directory whose `.mozconfig.json`
selects this source checkout and `--enable-project=netwerk/naivefox`. Keep new
object directories in one external build-root directory, with one child per
target, instead of scattering them through the checkout or home directory.
A build-file change needs an incremental full **product** graph gate; it never
requires configuring or cold-building the Firefox browser. Preserve the
existing configure options and managed toolchain to keep warm objects reusable.

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
export NAIVEFOX_DISABLE_SCCACHE=1 SCCACHE_DISABLE=1
./mach gtest 'NaiveFoxTunnelSessionLifecycle.*'
```

The product wrapper disables the local sccache daemon by default. Set
`NAIVEFOX_USE_SCCACHE=1` explicitly only for a deliberately configured cache;
otherwise configure inputs remain stable across runs.

Unset `NAIVEFOX_ENABLE_TESTS` before a release build. Do not substitute a
browser mozconfig or run an ordinary Firefox build as a product gate.

Windows cross-build and staging:

```bash
./netwerk/naivefox/tools/build-product.sh windows \
  --objdir /absolute/path/to/obj-naivefox-windows
```

The generated source tree is intentionally git-less and must be built without
`--bootstrap`. Bootstrap Mozilla dependencies, when needed, from the full Git
checkout before exporting; the product build wrapper rejects `--bootstrap` in
an export with an actionable error.

On Windows, verify the staged directory, including the deterministic
TunnelSession stop-lifecycle churn:

```powershell
py -3 netwerk/naivefox/tools/verify-staged-windows-smoke.py `
  --package-dir C:\absolute\path\to\naivefox-windows-x86_64
```

For live H2 and H3 acceptance, run this verifier once for each fixture protocol
with `NAIVEFOX_WINDOWS_PROXY_URL` set privately and `--target-url` pointing to
a deterministic fixture body. It compares eight transfers at concurrency four
through each local listener with the direct body digest. Set `SSL_CERT_FILE`
to the fixture CA for both NaiveFox and curl. Without an upstream URL the
verifier checks local lifecycle and malformed-input behavior only, not traffic.

Android ARM64 cross-build, staging, and static harness verification:

```bash
./netwerk/naivefox/tools/build-product.sh android \
  --objdir /absolute/path/to/obj-naivefox-android-aarch64

NAIVEFOX_OBJDIR=/absolute/path/to/obj-naivefox-android-aarch64 \
./netwerk/naivefox/tools/verify-staged-android-runtime.sh \
  /absolute/path/to/obj-naivefox-android-aarch64/package/naivefox-android-aarch64

./netwerk/naivefox/test/integration/run-android-embedded-tests.sh \
  --package /absolute/path/to/obj-naivefox-android-aarch64/package/naivefox-android-aarch64 \
  --check-only
```

The WSL gate uses a Linux ARM64 emulator, not a Windows-host emulator. Keep the
managed SDK and AVD under `${XDG_DATA_HOME:-$HOME/.local/share}/naivefox/` in
`android-sdk` and `android-avd`, respectively. The launcher discovers these
directories and the `naivefox-arm64-api27-raw` AVD automatically; explicit SDK,
AVD and emulator environment overrides remain supported. Append
`--start-emulator` to the online runner (without `--check-only`). Launch the
runner inside the isolated WSL network namespace so adb, QEMU and Caddy share
the same loopback network. No KVM is required for ARM64 software emulation.

The maintained emulator is the official Linux x86-64 build `34.1.20`, build ID
`11610631`, with the Android API-27 default ARM64 system image. The archive
`https://dl.google.com/android/repository/emulator-linux_x64-11610631.zip` has
SHA-256 `83a27f7936a8e89fa9e5e220a2cd2622db05f343065d66a92c4397f94df247a0`.
The SDK needs `platforms`, `platform-tools` and `system-images` directories;
its runtime needs Linux `libpulse0` and `libgl1` even with `-no-audio` and
`-no-window`. The launcher adds `-qemu -machine virt` and waits for Android's
boot-completed property (up to 360 seconds), not merely a stopped animation.
Headless Linux launches disable Vulkan and WSLg display discovery and select
software rendering, so the test does not depend on Windows GPU drivers.
When running as another user (for example root inside the namespace), set
`XDG_DATA_HOME` to the SDK owner's data directory or pass explicit SDK/AVD paths.
Do not remove a previous emulator/image until a relocated, Windows-independent
H2/H3 runtime gate has passed. Keep the managed emulator when cleaning objdirs.

The final command compiles and inspects the native harness but does not start
Gecko on Android. Device acceptance requires the runner without `--check-only`
on an online ARM64 API-26+ device or emulator. Lack of `adb` or KVM is not a
pass and must be recorded as an unrun device gate, not replaced with
`--allow-skip-device`.

Run the integration suites appropriate to the change as described in
`test/integration/README.md`. H2, H3, Auto, config, padding, parser robustness,
and staged-runtime checks are release gates when their code paths change.
Transport integration additionally requires both `classic` and `no-connect`
against one Caddy with both modules, using H2/H3 and both local listeners.
Inspect the actual linker inputs and dynamic dependencies after rebuilding:
no `js_static`, JavaScript execution, full DOM, layout, GFX or ICU4C may enter
the closure. A mozconfig label or a small executable launcher alone is not
proof that its dependent `libxul` remains lean.

## 3. Freeze source commit S and evidence commit E

Commit all final source, build rules, tools, tests, and documentation first.
That clean commit is `S`:

```bash
test -z "$(git status --porcelain)"
S=$(git rev-parse HEAD)
```

Build and test that exact clean commit. Then collect all nine target reports in
one operation, using the validated Linux, Windows, and Android object
directories and a new external work directory:

```bash
python3 netwerk/naivefox/tools/collect-minimal-source-evidence.py \
  --linux-objdir /absolute/path/to/obj-naivefox-linux \
  --windows-objdir /absolute/path/to/obj-naivefox-windows \
  --android-objdir /absolute/path/to/obj-naivefox-android-aarch64 \
  --work-dir /absolute/path/to/new-evidence-work
```

The collector derives the Firefox and NaiveFox ancestry from Git. It rejects
abbreviated, stale, or hand-entered provenance and atomically installs only:

```text
netwerk/naivefox/reports/build-inputs-linux-x86_64.json
netwerk/naivefox/reports/build-inputs-windows-x86_64.json
netwerk/naivefox/reports/build-inputs-android-aarch64.json
netwerk/naivefox/reports/closure-report-linux-x86_64.json
netwerk/naivefox/reports/closure-report-windows-x86_64.json
netwerk/naivefox/reports/closure-report-android-aarch64.json
netwerk/naivefox/reports/configure-inputs-linux-x86_64.json
netwerk/naivefox/reports/configure-inputs-windows-x86_64.json
netwerk/naivefox/reports/configure-inputs-android-aarch64.json
```

Review those files, stage exactly that set, and create direct child `E`:

```bash
git add \
  netwerk/naivefox/reports/build-inputs-linux-x86_64.json \
  netwerk/naivefox/reports/build-inputs-windows-x86_64.json \
  netwerk/naivefox/reports/build-inputs-android-aarch64.json \
  netwerk/naivefox/reports/closure-report-linux-x86_64.json \
  netwerk/naivefox/reports/closure-report-windows-x86_64.json \
  netwerk/naivefox/reports/closure-report-android-aarch64.json \
  netwerk/naivefox/reports/configure-inputs-linux-x86_64.json \
  netwerk/naivefox/reports/configure-inputs-windows-x86_64.json \
  netwerk/naivefox/reports/configure-inputs-android-aarch64.json
git diff --cached --name-only
git commit -m 'reports: freeze naivefox-minimal-source evidence'
E=$(git rev-parse HEAD)
test "$(git rev-parse HEAD^)" = "$S"
python3 netwerk/naivefox/tools/assert-closure.py
```

Do not make a documentation, tool, or SHA-fix commit after `E`. If anything
outside the nine reports must change, reset the release candidate to a new
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

Keep that validated directory pristine for publication. Build an isolated
verification copy with new external object directories:

```bash
full_source=$PWD
cp -a "$export_root/minimal-source" "$export_root/verification-source"
cd "$export_root/verification-source"

./netwerk/naivefox/tools/build-product.sh linux \
  --objdir /absolute/path/to/export-obj-linux

./netwerk/naivefox/tools/build-product.sh windows \
  --objdir /absolute/path/to/export-obj-windows

./netwerk/naivefox/tools/build-product.sh android \
  --objdir /absolute/path/to/export-obj-android-aarch64

NAIVEFOX_OBJDIR=/absolute/path/to/export-obj-android-aarch64 \
./netwerk/naivefox/tools/verify-staged-android-runtime.sh \
  /absolute/path/to/export-obj-android-aarch64/package/naivefox-android-aarch64

NAIVEFOX_OBJDIR=/absolute/path/to/export-obj-android-aarch64 \
./netwerk/naivefox/test/integration/run-android-embedded-tests.sh \
  --package /absolute/path/to/export-obj-android-aarch64/package/naivefox-android-aarch64 \
  --check-only
```

For the independence gate, run those commands in a disposable namespace, VM,
or container where the full checkout and its old object directories are not
visible. In particular, the exported tree must complete a clean Android ARM64
configure, clean build, embedded-runtime stage, package verification, and
static harness construction without borrowing any file from full-source.
Repeat staging, validation, and the applicable integration acceptance against
the exported binaries. Device H2/H3 acceptance remains a separate mandatory
online-device gate and must not be inferred from the standalone static check.

Only then replace the contents of a disposable `naivefox-minimal-source`
worktree and commit one linear generated snapshot. The helper checks the
manifest again, requires a clean linked worktree on the generated branch, and
preserves its `.git` metadata:

```bash
python3 "$full_source/netwerk/naivefox/tools/replace-minimal-source-worktree.py" \
  "$export_root/minimal-source" \
  /absolute/path/to/naivefox-minimal-source
git -C /absolute/path/to/naivefox-minimal-source status --short
```

Review and commit the resulting generated changes. The helper never stages or
commits files. Preserve the `.github/workflows/` control-plane overlay in that
branch; it is intentionally maintained independently from full-source exports.
Never hand-edit the product tree and never merge it back to
`naivefox-full-source`.

## 5. Cleanup

Remove only the temporary export, evidence work directory, object directories,
logs, and staged packages created for the current run. Do not delete shared
caches, existing object directories, or another maintainer's diagnostics.
