# Gemini handoff: NaiveFox minimisation

You are taking over a validated NaiveFox checkout. Do not assume this is a
generic Firefox tree and do not update Firefox upstream during the first pass.

## Where the work is

- WSL distribution: `Ubuntu24Dev`
- Full checkout: `/home/zubastik/src/naivefox`
- GitHub repository: `incident201/naivefox`
- Current working branch: `minimal`
- Upstream Firefox mirror: `upstream/main`
- Reference branch: `naivefox`
- Generated product branch: `minimal-source` does not exist yet and must not be
  edited by hand.

Start with:

```bash
cd /home/zubastik/src/naivefox
git switch minimal
git status -sb
git log -1 --decorate --oneline
```

Read these files before changing code:

```text
AGENTS.md
netwerk/naivefox/AGENTS.md
netwerk/naivefox/README.md
netwerk/naivefox/ROADMAP.md
netwerk/naivefox/UPSTREAM.md
netwerk/naivefox/TEST-REPORT.md
netwerk/naivefox/MINIMISATION-TASK.MD
```

The current handoff commit is tagged `minimization-handoff-v0.1` and is
already pushed to `origin/minimal`. Do not rewrite it. Create small logical
commits and push only after the relevant checks pass.

## Validated baseline

This is one existing executable, `naivefox`, using Firefox Necko/Neqo/NSS/PSM.
It already contains:

- strict H2, strict H3, and Auto proxy transport selection;
- Naive Variant 1 header and payload padding;
- SOCKS5 and HTTP CONNECT local listeners sharing one tunnel backend;
- NaiveProxy-style config mode and automatic profile handling;
- H2/H3 local fixture suites, real-Caddy tests, robustness and soak tests;
- staged Linux runtime and a native Windows x86_64 package.

The last minimisation checkpoint disables the DOM/GFX implementation build
graph under the NaiveFox build configuration while retaining the complete
Firefox source tree. This is deliberately not a mass source deletion. Generated
metadata required by the shared build remains. The next objective is dependency
measurement, not another speculative subsystem removal.

The retained object directories are:

```text
obj-naivefox-minimal/
obj-naivefox-windows-x86_64/
```

Use them for incremental work. Do not recreate every historical object
directory or run an unnecessary clean build. The WSL VHDX was cleaned outside
Git; old object trees, ignored artifacts, fixture copies, and `sccache` were
removed. The source checkout and Git history were not altered by that cleanup.

The native Windows archive is an external artifact at:

```text
D:\naivefox\naivefox-windows-x86_64.tar.gz
```

It is not part of the Git source of truth.

## What to do next

1. Verify the branch/tag and read the documents above. Keep the working tree
   clean before starting.
2. Produce machine-readable build/link/runtime dependency reports from the
   existing minimized configuration and retained object directory. Identify
   which large closure remains (DOM/GFX, browser-only, media, or generic
   libxul) before editing build files.
3. Pick exactly one evidence-backed dependency group. Prefer build-graph
   exclusion over source deletion. Keep all Necko, HTTP/1.1, HTTP/2, HTTP/3,
   Neqo, NSS/PSM, NSPR, DNS/proxy, XPCOM, config, SOCKS, HTTP CONNECT, padding,
   and required event-loop services intact.
4. Make the smallest guarded build change, document every Firefox file in
   `UPSTREAM.md`, and run an incremental compile followed by focused tests.
5. For a networking/TLS/Neqo change, run the relevant xpcshell tests and a
   bounded Caddy H2/H3 test. For a build-closure-only change, run gtests,
   staged-runtime smoke, H2/H3/config/robustness suites, and size/link checks.
6. Update `ROADMAP.md`, `TEST-REPORT.md`, and `UPSTREAM.md` with exact commit,
   base, commands, and limitations. Never claim a milestone from compilation
   alone.
7. Only after the minimized closure is stable, implement the allowlist source
   manifest and deterministic `tools/export-minimal-source.sh`. Validate the
   export in a clean directory with the original checkout unavailable, then
   create/update `minimal-source` as an orphan-history generated snapshot.

## Guardrails

- Never merge `main` directly into `minimal`; use the documented
  `main -> refresh/firefox-* -> naivefox -> refresh/minimal-* -> minimal` gates.
- Never merge `minimal-source` back into `minimal`, `naivefox`, or `main`.
- Do not replace Firefox networking with curl, quiche, msquic, ngtcp2, OpenSSL,
  or a standalone Neqo client.
- Do not add credentials, profiles, pcaps, keylogs, logs, object directories,
  or package artifacts to Git.
- Do not delete large upstream source directories merely to make the checkout
  look smaller. Preserve them for future Firefox refreshes.
- Avoid broad clean rebuilds; retain and reuse the two object directories
  listed above, and record why a clean build is genuinely required.
- Keep credentials in environment/config fixtures only; never print or commit
  them.
