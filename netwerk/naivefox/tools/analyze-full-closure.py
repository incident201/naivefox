#!/usr/bin/env python3
"""
analyze-full-closure.py - Comprehensive Multi-Target Link & Source Closure Audit
Produces complete, reproducible, normalized machine-readable JSON reports.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from provenance import TARGET_SPECS, derive_source_provenance
from provenance import sha256 as provenance_sha256


class AuditConsistencyError(Exception):
    pass


def sha256_file(filepath):
    if not filepath or not os.path.exists(filepath):
        return None
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def normalize_path(full_path, topsrcdir, objdir=None):
    """Normalize any path to repository-relative format, removing all absolute paths."""
    if not full_path:
        return full_path
    p = os.path.normpath(str(full_path)).replace("\\", "/")
    top = os.path.normpath(str(topsrcdir)).replace("\\", "/")
    if p.startswith(top + "/"):
        p = p[len(top) + 1 :]
    elif p == top:
        return "."
    if objdir:
        obj = os.path.normpath(str(objdir)).replace("\\", "/")
        if obj.startswith(top + "/"):
            obj = obj[len(top) + 1 :]
        if p.startswith(obj + "/"):
            return "objdir/" + p[len(obj) + 1 :]
        elif p == obj:
            return "objdir"
    # If path is still absolute, strip developer home/drive
    if p.startswith("/") or re.match(r"^[a-zA-Z]:", p):
        for marker in [
            "obj-naivefox",
            "toolkit",
            "netwerk",
            "xpcom",
            "security",
            "intl",
            "storage",
            "js",
            "third_party",
            "config",
        ]:
            if "/" + marker in p:
                idx = p.find("/" + marker)
                return p[idx + 1 :]
        return os.path.basename(p)
    return p


def map_crate_manifest(raw_manifest, pkg_name, topsrcdir):
    """Map crate manifest to in-tree or vendored repository-relative path."""
    top = os.path.normpath(str(topsrcdir)).replace("\\", "/")
    raw = os.path.normpath(str(raw_manifest)).replace("\\", "/")
    if raw.startswith(top + "/"):
        mapped = raw[len(top) + 1 :]
        if not os.path.exists(os.path.join(topsrcdir, mapped)):
            raise AuditConsistencyError(
                f"Rust manifest is inside the checkout but does not exist: {mapped}"
            )
        return mapped
    vendored_candidate = os.path.join(
        topsrcdir, "third_party", "rust", pkg_name, "Cargo.toml"
    )
    if os.path.exists(vendored_candidate):
        return f"third_party/rust/{pkg_name}/Cargo.toml"
    alt_name = pkg_name.replace("-", "_")
    vendored_alt = os.path.join(
        topsrcdir, "third_party", "rust", alt_name, "Cargo.toml"
    )
    if os.path.exists(vendored_alt):
        return f"third_party/rust/{alt_name}/Cargo.toml"
    raise AuditConsistencyError(
        "Reachable Rust crate is not vendored in the repository: "
        f"{pkg_name} ({raw_manifest})"
    )


def parse_response_file(rsp_path, topsrcdir):
    """Recursively parse linker response files (.list / .rsp)."""
    items = []
    if not os.path.exists(rsp_path):
        raise AuditConsistencyError(f"Response file not found: {rsp_path}")
    base_dir = os.path.dirname(rsp_path)
    with open(rsp_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for token in line.split():
                if token.startswith("@"):
                    sub_rsp = token[1:]
                    if not os.path.isabs(sub_rsp):
                        sub_rsp = os.path.normpath(os.path.join(base_dir, sub_rsp))
                    items.extend(parse_response_file(sub_rsp, topsrcdir))
                else:
                    if not os.path.isabs(token):
                        token = os.path.normpath(os.path.join(base_dir, token))
                    items.append(token)
    return items


def get_archive_members(archive_path, topsrcdir, objdir):
    """Extract object member names from a static archive (.a / .lib) and normalize paths."""
    members = []
    if not os.path.exists(archive_path):
        return members
    for cmd in [["ar", "t", archive_path], ["llvm-ar", "t", archive_path]]:
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
            for line in out.splitlines():
                line = line.strip()
                if line:
                    norm = normalize_path(line, topsrcdir, objdir)
                    if re.match(r"^[A-Za-z]:", norm) or (
                        norm.startswith("/") and not norm.startswith("objdir/")
                    ):
                        norm = os.path.basename(norm)
                    members.append(norm)
            if members:
                return sorted(members)
        except Exception:
            pass
    return sorted(members)


def parse_backend_mk(backend_mk_path):
    """Parse backend.mk to get exact STATIC_LIBS, SHARED_LIBS, and OS_LIBS."""
    static_libs = []
    shared_libs = []
    os_libs = []
    if not os.path.exists(backend_mk_path):
        return static_libs, shared_libs, os_libs

    depth = str(Path(backend_mk_path).parent.parent.parent.parent)
    with open(backend_mk_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("STATIC_LIBS +="):
                val = line.split("+=", 1)[1].strip().replace("$(DEPTH)", depth)
                static_libs.append(val)
            elif line.startswith("SHARED_LIBS +="):
                val = line.split("+=", 1)[1].strip().replace("$(DEPTH)", depth)
                shared_libs.append(val)
            elif line.startswith("OS_LIBS +="):
                val = line.split("+=", 1)[1].strip()
                os_libs.append(val)
    return static_libs, shared_libs, os_libs


def get_dynamic_dependencies(binary_path):
    """Get DT_NEEDED (Linux) or PE imports (Windows)."""
    deps = []
    if not os.path.exists(binary_path):
        return deps
    try:
        out = subprocess.check_output(
            ["readelf", "-d", binary_path], stderr=subprocess.DEVNULL, text=True
        )
        for match in re.finditer(r"\(NEEDED\)\s+Shared library:\s+\[([^\]]+)\]", out):
            deps.append(match.group(1))
        if deps:
            return sorted(set(deps))
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["objdump", "-p", binary_path], stderr=subprocess.DEVNULL, text=True
        )
        for match in re.finditer(r"DLL Name:\s+(\S+)", out):
            deps.append(match.group(1))
        if deps:
            return sorted(set(deps))
    except Exception:
        pass
    return sorted(set(deps))


def get_configured_toolchain_versions(objdir):
    path = Path(objdir) / "config.status.json"
    try:
        substs = json.loads(path.read_text(encoding="utf-8"))["substs"]
        compiler = substs["CC"]
        compiler_type = substs["CC_TYPE"]
        configured_version = substs["CC_VERSION"]
        linker = substs.get("LINKER")
        if not linker:
            candidates = [
                flag.removeprefix("-Wl,--real-linker,")
                for flag in substs.get("RELRHACK_LDFLAGS", [])
                if flag.startswith("-Wl,--real-linker,")
            ]
            if len(candidates) != 1:
                raise ValueError("configured real linker is missing or ambiguous")
            linker = candidates[0]
        if not isinstance(compiler, list) or not compiler or not all(
            isinstance(argument, str) for argument in compiler
        ):
            raise ValueError("configured CC is not a command array")
        for tool in (compiler[0], linker):
            if (
                not isinstance(tool, str)
                or not os.path.isabs(tool)
                or not os.access(tool, os.X_OK)
            ):
                raise ValueError("configured tool is not an absolute executable path")
        compiler_output = subprocess.check_output(
            [*compiler, "--version"], stderr=subprocess.PIPE, text=True
        ).splitlines()[0]
        version = re.search(r"\bversion (\d+(?:\.\d+)+)", compiler_output)
        if not version or version.group(1) != configured_version:
            raise ValueError("compiler version differs from config.status.json")
        linker_version = subprocess.check_output(
            [linker, "--version"], stderr=subprocess.PIPE, text=True
        ).splitlines()[0].strip()
        if not linker_version:
            raise ValueError("configured linker reported an empty version")
        return {
            "compiler_version": f"{compiler_type} {configured_version}",
            "linker_version": linker_version,
            "sccache_state": "configured" if substs.get("CCACHE") else "disabled",
        }
    except (
        OSError, ValueError, KeyError, TypeError, IndexError,
        subprocess.CalledProcessError,
    ) as error:
        raise AuditConsistencyError(
            f"Cannot attest configured compiler/linker: {path}: {error}"
        ) from error


def get_reachable_rust_closure(topsrcdir, target_triple, objdir=None):
    """
    Run cargo metadata from the actual NaiveFox Rust root. The outer
    `gkrust` staticlib is only a wrapper which selects this package;
    traversing the workspace root also pulls dev-only/browser feature graphs
    into the audit. Traverse normal dependencies from `gkrust-naivefox` with
    Cargo's target filtering and default-feature policy intact.
    Map all crates.io and in-tree crates to repository-relative paths inside third_party/rust or in-tree.
    """
    manifest_path = os.path.join(
        topsrcdir, "toolkit", "library", "rust", "naivefox", "Cargo.toml"
    )
    if not os.path.exists(manifest_path):
        raise AuditConsistencyError(f"Rust root manifest not found: {manifest_path}")

    cargo = shutil.which("cargo")
    if not cargo:
        raise AuditConsistencyError(
            "cargo is not on PATH; run the audit as the configured build user"
        )

    # Cargo's resolver is the source of truth for package identity.  The
    # metadata inventory intentionally includes host/build packages too (the
    # Windows target is still resolved by a Linux host); the target-filtered
    # `cargo tree` below decides which normal runtime edges are reachable.
    cmd = [
        cargo,
        "metadata",
        "--manifest-path",
        manifest_path,
        "--format-version",
        "1",
        "--no-default-features",
        "--locked",
        "--offline",
    ]
    cargo_cwd = topsrcdir
    if objdir and os.path.exists(os.path.join(objdir, ".cargo", "config.toml")):
        # Mozilla's configured vendored-source replacement lives in the
        # target objdir. Running Cargo from the checkout would try to fetch
        # the pinned git crates even in offline mode.
        cargo_cwd = objdir
    try:
        raw = subprocess.check_output(
            cmd, stderr=subprocess.PIPE, text=True, cwd=cargo_cwd
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[-1:]
        raise AuditConsistencyError(
            f"cargo metadata failed for {target_triple}: "
            + (detail[0] if detail else "unknown error")
        ) from exc
    meta = json.loads(raw)

    packages_by_id = {p["id"]: p for p in meta.get("packages", [])}
    root_ids = [
        package_id
        for package_id, package in packages_by_id.items()
        if package.get("name") == "gkrust-naivefox"
    ]
    if not root_ids:
        raise AuditConsistencyError(
            "Cannot resolve root crate 'gkrust-naivefox' in Cargo metadata"
        )

    # Cargo metadata resolves the complete workspace and unifies features
    # across members. Its resolve.nodes[].deps can therefore contain optional
    # Glean/profiler dependencies inactive in the NaiveFox package. Ask Cargo
    # for the target-specific, no-default-features normal-edge tree instead;
    # retain metadata only for manifests and source paths.
    tree_cmd = [
        cargo,
        "tree",
        "--manifest-path",
        manifest_path,
        "--no-default-features",
        "--edges",
        "normal",
        "--target",
        target_triple,
        "--locked",
        "--offline",
        "--prefix",
        "none",
        "--format",
        "{p}",
    ]
    try:
        tree_output = subprocess.check_output(
            tree_cmd, stderr=subprocess.PIPE, text=True, cwd=cargo_cwd
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[-1:]
        raise AuditConsistencyError(
            f"cargo tree failed for {target_triple}: "
            + (detail[0] if detail else "unknown error")
        ) from exc

    by_name_version = {}
    for package_id, package in packages_by_id.items():
        key = (package.get("name"), package.get("version"))
        by_name_version.setdefault(key, []).append(package_id)

    reachable_ids = set()
    package_line = re.compile(
        r"^(?P<name>\S+) v(?P<version>\S+)(?: \((?P<source>.*)\))?$"
    )
    for raw_line in tree_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = package_line.match(line)
        if not match:
            raise AuditConsistencyError(f"cannot parse cargo tree package line: {line}")
        name = match.group("name")
        version = match.group("version")
        source = match.group("source")
        candidates = list(by_name_version.get((name, version), []))
        if source and candidates:
            if source.startswith("/"):
                exact = [
                    package_id
                    for package_id in candidates
                    if Path(packages_by_id[package_id]["manifest_path"]).parent
                    == Path(source)
                ]
                if exact:
                    candidates = exact
            else:
                matching_source = [
                    package_id
                    for package_id in candidates
                    if packages_by_id[package_id].get("source")
                    and source in packages_by_id[package_id]["source"]
                ]
                if matching_source:
                    candidates = matching_source
        if not candidates:
            raise AuditConsistencyError(
                f"cargo tree package is absent from metadata: {line}"
            )
        reachable_ids.update(candidates)

    if not set(root_ids).intersection(reachable_ids):
        raise AuditConsistencyError(
            "cargo tree did not include gkrust-naivefox root package"
        )

    reachable_crates = []
    for pkg_id in sorted(reachable_ids):
        pkg = packages_by_id.get(pkg_id)
        if not pkg:
            continue
        name = pkg.get("name", "")
        norm_manifest = map_crate_manifest(
            pkg.get("manifest_path", ""), name, topsrcdir
        )

        source_type = "in-tree"
        if norm_manifest.startswith("third_party/rust/"):
            source_type = "vendored"
        elif any(
            norm_manifest.startswith(p)
            for p in [
                "toolkit/",
                "netwerk/",
                "xpcom/",
                "security/",
                "intl/",
                "storage/",
                "js/",
            ]
        ):
            source_type = "in-tree-component"

        source_paths = []
        for target in pkg.get("targets", []):
            target_kinds = set(target.get("kind", []))
            if target_kinds and target_kinds.issubset({"example", "test", "bench"}):
                continue
            raw_source = target.get("src_path")
            if not raw_source:
                continue
            # Cargo metadata can resolve a git/registry package from a cache
            # even when the same crate is vendored in-tree.  Preserve the
            # path relative to Cargo.toml, then apply it to the audited
            # repository manifest directory instead of leaking that cache
            # path into the report.
            raw_relative = os.path.relpath(
                raw_source, os.path.dirname(pkg.get("manifest_path", raw_source))
            )
            mapped_source = os.path.normpath(
                os.path.join(os.path.dirname(norm_manifest), raw_relative)
            ).replace("\\", "/")
            if mapped_source.startswith("../") or not os.path.exists(
                os.path.join(topsrcdir, mapped_source)
            ):
                raise AuditConsistencyError(
                    "Reachable Rust target source is outside the checkout or missing: "
                    f"{raw_source} (mapped to {mapped_source})"
                )
            source_paths.append(mapped_source)

        reachable_crates.append({
            "name": name,
            "version": pkg.get("version"),
            "source_type": source_type,
            "manifest_path": norm_manifest,
            "source_paths": sorted(set(source_paths)),
        })

    reachable_crates.sort(key=lambda x: x["name"])
    return reachable_crates


def get_source_and_build_inputs(
    topsrcdir, objdir, active_object_paths, archive_member_names
):
    """Gather complete source translation units, headers, IDL files, and generator scripts from build outputs."""
    objdir_path = Path(objdir)
    cxx_sources = set()
    headers = set()
    xpidl_inputs = set()
    ipdl_inputs = set()
    generators = set()
    depfiles_scanned = 0

    top_prefix = os.path.normpath(str(topsrcdir)).replace("\\", "/") + "/"
    active_objects = {os.path.normpath(str(path)) for path in active_object_paths}
    archive_members = {name for name in archive_member_names if os.path.basename(name) == name}
    archive_objects = {
        os.path.normpath(os.path.join(objdir, name.removeprefix("objdir/")))
        for name in archive_member_names if name.startswith("objdir/")
    }

    for root, dirs, files in os.walk(str(objdir_path)):
        if ".deps" in root:
            for f in files:
                if f.endswith(".pp") or f.endswith(".d"):
                    pp_path = os.path.join(root, f)
                    # Reusable objdirs retain dependency files for objects
                    # removed by a later lean build graph.  Restrict parsing
                    # to depfiles whose corresponding object is in the
                    # current libxul response file.
                    suffix = ".pp" if f.endswith(".pp") else ".d"
                    object_path = os.path.normpath(
                        os.path.join(os.path.dirname(root), f[: -len(suffix)])
                    )
                    if (
                        object_path not in active_objects
                        and object_path not in archive_objects
                        and os.path.basename(object_path) not in archive_members
                    ):
                        continue
                    try:
                        depfiles_scanned += 1
                        with open(pp_path, encoding="utf-8", errors="ignore") as pf:
                            content = pf.read()
                            for token in content.replace("\\\n", " ").split():
                                if token.endswith(":") or token.startswith("-"):
                                    continue
                                if not os.path.isabs(token):
                                    token = os.path.join(os.path.dirname(root), token)
                                token_norm = os.path.normpath(token).replace("\\", "/")
                                if not token_norm.startswith(top_prefix):
                                    continue
                                norm = normalize_path(token_norm, topsrcdir, objdir)
                                if norm.startswith("objdir/"):
                                    continue
                                if norm.endswith((
                                    ".cpp",
                                    ".c",
                                    ".cc",
                                    ".cxx",
                                    ".mm",
                                    ".S",
                                    ".s",
                                    ".asm",
                                    ".rc",
                                )):
                                    cxx_sources.add(norm)
                                elif norm.endswith(".h") or norm.endswith(".hpp"):
                                    headers.add(norm)
                                elif norm.endswith(".idl"):
                                    xpidl_inputs.add(norm)
                                elif norm.endswith(".ipdl") or norm.endswith(".ipdlh"):
                                    ipdl_inputs.add(norm)
                                elif norm.endswith(".py"):
                                    generators.add(norm)
                    except Exception:
                        pass

    # Exact target-specific moz.build/moz.configure inputs are collected from
    # backend.RecursiveMakeBackend.in by collect-build-inputs.py.  A broad
    # checkout walk here used to include inactive test/product definitions and
    # even ignored objdirs when they lived below the source root.
    mozbuild_files = set()

    runtime_resources = [
        "netwerk/naivefox/tools/runtime-resources.manifest",
        "netwerk/naivefox/tools/runtime-chrome.manifest",
        "modules/libpref/init/all.js",
    ]

    licenses = [
        "toolkit/content/license.html",
        "LEGAL",
        "LICENSE",
    ]

    return {
        "cxx_translation_units": sorted(cxx_sources),
        "headers": sorted(headers),
        "headers_count": len(headers),
        "headers_sample": sorted(headers)[:50],
        "depfiles_scanned": depfiles_scanned,
        "xpidl_inputs": sorted(xpidl_inputs),
        "ipdl_inputs": sorted(ipdl_inputs),
        "webidl_binding_inputs": [
            "dom/bindings/parser/WebIDL.py",
            "dom/bindings/Configuration.py",
            "dom/chrome-webidl/OriginAttributes.webidl",
        ],
        "generators_and_python_scripts": sorted(generators),
        "mozbuild_definition_inputs": sorted(mozbuild_files),
        "mozbuild_definition_inputs_count": len(mozbuild_files),
        "runtime_resource_sources": runtime_resources,
        "licenses_and_notices": [
            l for l in licenses if os.path.exists(os.path.join(topsrcdir, l))
        ],
    }


def get_glean_inputs(topsrcdir, objdir):
    """Record the real Glean generator/cache/YAML inputs without exporting objdir paths."""
    generator_scripts = [
        "toolkit/components/glean/build_scripts/glean_parser_ext/cache_yaml.py",
        "toolkit/components/glean/build_scripts/glean_parser_ext/run_glean_parser.py",
        "toolkit/components/glean/build_scripts/glean_parser_ext/metrics_header_names.py",
    ]
    generator_scripts = [
        path
        for path in generator_scripts
        if os.path.exists(os.path.join(topsrcdir, path))
    ]

    yaml_inputs = {"metrics": set(), "pings": set()}
    cache_inputs = []
    glean_objdir = os.path.join(objdir, "toolkit", "components", "glean")
    for kind in ("metrics", "pings"):
        cache = os.path.join(glean_objdir, f"{kind}_yamls.cached")
        if not os.path.exists(cache):
            continue
        cache_inputs.append(normalize_path(cache, topsrcdir, objdir))
        try:
            text = Path(cache).read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        prefix = os.path.normpath(str(topsrcdir)).replace("\\", "/") + "/"
        for match in re.finditer(re.escape(prefix) + r"([A-Za-z0-9_./-]+\.yaml)", text):
            candidate = match.group(1).replace("\\", "/")
            if os.path.exists(os.path.join(topsrcdir, candidate)):
                yaml_inputs[kind].add(candidate)

    generated_outputs = []
    generated_sources = os.path.join(objdir, "generated-sources.json")
    if os.path.exists(generated_sources):
        try:
            generated = json.loads(Path(generated_sources).read_text(encoding="utf-8"))
            raw_text = json.dumps(generated)
            generated_outputs = sorted(
                set(
                    value
                    for value in re.findall(
                        r"toolkit/components/glean/[A-Za-z0-9_./-]+", raw_text
                    )
                    if value.startswith("toolkit/components/glean/")
                )
            )
        except (OSError, ValueError):
            generated_outputs = []

    return {
        "enabled": bool(cache_inputs or generator_scripts),
        "generator_scripts": sorted(generator_scripts),
        "cache_inputs": sorted(cache_inputs),
        "metrics_yaml_inputs": sorted(yaml_inputs["metrics"]),
        "pings_yaml_inputs": sorted(yaml_inputs["pings"]),
        "generated_outputs_count": len(generated_outputs),
        "generated_outputs_sample": generated_outputs[:100],
    }


def staged_package_dir(objdir, target_spec, explicit=None):
    objdir_path = Path(objdir).resolve(strict=True)
    package_name = target_spec["staged_package"]
    candidates = (
        objdir_path / "package" / package_name,
        objdir_path / "naivefox-package" / package_name,
        objdir_path / package_name,
    )
    selected = (
        Path(explicit) if explicit is not None
        else next((path for path in candidates if path.is_dir()), candidates[0])
    )
    if not selected.is_absolute():
        raise AuditConsistencyError("staged package directory must be absolute")
    try:
        relative = selected.relative_to(objdir_path)
    except ValueError as error:
        raise AuditConsistencyError(
            "staged package must remain below the exact objdir"
        ) from error
    if not relative.parts or ".." in relative.parts:
        raise AuditConsistencyError("staged package must remain below the exact objdir")
    current = objdir_path
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise AuditConsistencyError("staged package path must not contain symlinks")
    if not selected.is_dir():
        raise AuditConsistencyError(f"staged package directory does not exist: {selected}")
    if any(path.is_symlink() for path in selected.rglob("*")):
        raise AuditConsistencyError("staged package contents must not contain symlinks")
    return selected


def analyze_target(
    topsrcdir,
    objdir,
    target_spec,
    firefox_ref="firefox-upstream",
    naivefox_ref="naivefox-full-source",
    package_dir=None,
):
    """Analyze comprehensive full link and source closure for target."""
    objdir_path = Path(objdir)
    if not objdir_path.exists():
        raise AuditConsistencyError(f"Objdir does not exist: {objdir}")

    target_triple = target_spec["cargo_target"]
    mozconfig_relpath = target_spec["mozconfig"]
    xul_name = Path(target_spec["libxul"]).name
    executable = target_spec["executable"]

    libxul_list_path = objdir_path / target_spec["link_response"]

    if not libxul_list_path.exists():
        raise AuditConsistencyError(
            f"Linker response file not found: {libxul_list_path}"
        )

    link_items = parse_response_file(str(libxul_list_path), topsrcdir)
    objects = []
    total_obj_bytes = 0
    component_groups = {}

    for item in link_items:
        full_path = os.path.normpath(item)
        if not os.path.exists(full_path):
            raise AuditConsistencyError(f"Unresolved link object file: {item}")
        sz = os.path.getsize(full_path)
        total_obj_bytes += sz
        rel_obj = normalize_path(full_path, topsrcdir, objdir)
        top_comp = (
            rel_obj.split("/")[1]
            if rel_obj.startswith("objdir/")
            else rel_obj.split("/")[0]
        )
        if top_comp not in component_groups:
            component_groups[top_comp] = {"files": 0, "bytes": 0}
        component_groups[top_comp]["files"] += 1
        component_groups[top_comp]["bytes"] += sz
        objects.append({"path": rel_obj, "size_bytes": sz})

    backend_mk_path = str(objdir_path / "toolkit" / "library" / "build" / "backend.mk")
    raw_static_libs, raw_shared_libs, raw_os_libs = parse_backend_mk(backend_mk_path)

    static_libs = []
    js_static_found = False
    gkrust_found = False

    for lib in raw_static_libs:
        norm_lib = os.path.normpath(lib)
        if not os.path.exists(norm_lib):
            raise AuditConsistencyError(f"Static library not found on disk: {lib}")
        sz = os.path.getsize(norm_lib)
        members = get_archive_members(norm_lib, topsrcdir, objdir)
        rel_lib = normalize_path(norm_lib, topsrcdir, objdir)
        if "js_static" in rel_lib:
            js_static_found = True
        if "gkrust" in rel_lib:
            gkrust_found = True
        static_libs.append({
            "path": rel_lib,
            "size_bytes": sz,
            "member_count": len(members),
            # Keep the complete archive member inventory.  The first 30
            # members are not a source closure: omitting the rest would make
            # a future allowlist silently incomplete.
            "members": members,
        })

    if not gkrust_found:
        raise AuditConsistencyError(
            "Self-consistency failure: gkrust is missing from STATIC_LIBS"
        )

    bin_xul = objdir_path / "dist" / "bin" / xul_name
    bin_naivefox = objdir_path / executable if executable else None
    dynamic_deps = get_dynamic_dependencies(str(bin_xul))

    rust_crates = get_reachable_rust_closure(topsrcdir, target_triple, objdir)
    archive_member_names = [
        member for archive in static_libs for member in archive["members"]
    ]
    source_inputs = get_source_and_build_inputs(
        topsrcdir,
        objdir,
        [item for item in link_items if os.path.exists(item)],
        archive_member_names,
    )
    glean_inputs = get_glean_inputs(topsrcdir, objdir)

    dist_bin_files = []
    dist_bin = objdir_path / "dist" / "bin"
    if dist_bin.exists():
        for f in sorted(dist_bin.iterdir()):
            if f.is_file():
                dist_bin_files.append({"name": f.name, "size_bytes": f.stat().st_size})

    staged_pkg_dir = staged_package_dir(objdir_path, target_spec, package_dir)

    staged_manifest_files = []
    if staged_pkg_dir.exists():
        for root, dirs, files in os.walk(str(staged_pkg_dir)):
            for f in sorted(files):
                full = os.path.join(root, f)
                rel = normalize_path(full, str(staged_pkg_dir))
                staged_manifest_files.append({
                    "path": rel,
                    "size_bytes": os.path.getsize(full),
                    "sha256": sha256_file(full),
                })

    source_provenance = derive_source_provenance(
        Path(topsrcdir), firefox_ref=firefox_ref, naivefox_ref=naivefox_ref
    )
    mozconfig_full = os.path.join(topsrcdir, mozconfig_relpath)
    mozconfig_hash = sha256_file(mozconfig_full)

    toolchain = get_configured_toolchain_versions(objdir)

    report = {
        "report_provenance": {
            "provenance_version": 2,
            "source_commit_sha": source_provenance.source_commit,
            "source_worktree_clean": True,
            "firefox_base_sha": source_provenance.firefox_base_commit,
            "naivefox_reference_sha": source_provenance.naivefox_reference_commit,
            "analyzer_sha256": sha256_file(__file__),
            "provenance_sha256": provenance_sha256(
                Path(__file__).resolve().with_name("provenance.py")
            ),
            "evidence_collector_sha256": provenance_sha256(
                Path(__file__).resolve().with_name("collect-minimal-source-evidence.py")
            ),
            "target": target_spec["name"],
            "platform": target_spec["platform"],
            "target_triple": target_triple,
            "mozconfig_path": mozconfig_relpath,
            "mozconfig_sha256": mozconfig_hash,
            "analyzer_version": "2.7.2-three-target-fresh-staged-packages",
            **toolchain,
            "staged_runtime_manifest_count": len(staged_manifest_files),
        },
        "summary": {
            "cxx_translation_units_count": len(source_inputs["cxx_translation_units"]),
            "direct_link_objects_count": len(objects),
            "unstripped_link_objects_bytes": total_obj_bytes,
            "unstripped_link_objects_mb": round(total_obj_bytes / (1024 * 1024), 2),
            "static_libraries_count": len(static_libs),
            "spidermonkey_static_present": js_static_found,
            "libxul_size_bytes": bin_xul.stat().st_size if bin_xul.exists() else 0,
            "naivefox_bin_size_bytes": bin_naivefox.stat().st_size
            if bin_naivefox and bin_naivefox.exists()
            else 0,
            "reachable_rust_crates_count": len(rust_crates),
            "dynamic_dependencies_count": len(dynamic_deps),
            "staged_runtime_files_count": len(staged_manifest_files),
        },
        "component_groups": {
            k: {
                "files": v["files"],
                "bytes": v["bytes"],
                "mb": round(v["bytes"] / (1024 * 1024), 2),
            }
            for k, v in sorted(
                component_groups.items(), key=lambda x: x[1]["bytes"], reverse=True
            )
        },
        "cxx_translation_units": source_inputs["cxx_translation_units"],
        "static_libraries": static_libs,
        "shared_libraries": [
            normalize_path(l, topsrcdir, objdir) for l in raw_shared_libs
        ],
        "dynamic_dependencies": dynamic_deps,
        "rust_closure": {
            "platform_filter": target_triple,
            "root_package": "gkrust-naivefox",
            "reachable_crates_count": len(rust_crates),
            "crates": rust_crates,
        },
        "build_inputs": {
            "cxx_translation_units": source_inputs["cxx_translation_units"],
            "cargo_manifests": sorted(c["manifest_path"] for c in rust_crates),
            "cargo_root_manifest": "toolkit/library/rust/naivefox/Cargo.toml",
            "cargo_lockfile": "Cargo.lock",
            "cargo_config_template": ".cargo/config.toml.in",
            "depfiles_scanned": source_inputs["depfiles_scanned"],
            "headers_count": source_inputs["headers_count"],
            "headers": source_inputs["headers"],
            "xpidl_inputs_count": len(source_inputs["xpidl_inputs"]),
            "xpidl_inputs": source_inputs["xpidl_inputs"],
            "ipdl_inputs_count": len(source_inputs["ipdl_inputs"]),
            "ipdl_inputs": source_inputs["ipdl_inputs"],
            "webidl_binding_inputs": source_inputs["webidl_binding_inputs"],
            "generators_and_python_scripts_count": len(
                source_inputs["generators_and_python_scripts"]
            ),
            "generators_and_python_scripts": source_inputs[
                "generators_and_python_scripts"
            ],
            "mozbuild_definition_inputs_count": source_inputs[
                "mozbuild_definition_inputs_count"
            ],
            "mozbuild_definition_inputs": source_inputs["mozbuild_definition_inputs"],
            "runtime_resource_sources": source_inputs["runtime_resource_sources"],
            "licenses_and_notices": source_inputs["licenses_and_notices"],
            "glean": glean_inputs,
        },
        "runtime_inventory": {
            "dist_bin_unfiltered_count": len(dist_bin_files),
            "dist_bin_inventory": dist_bin_files,
            "staged_package_manifest": staged_manifest_files,
        },
        "direct_objects": objects,
    }
    return report


def main():
    default_repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=default_repo)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--linux-objdir", type=Path)
    parser.add_argument("--windows-objdir", type=Path)
    parser.add_argument("--android-objdir", type=Path)
    parser.add_argument("--linux-package-dir", type=Path)
    parser.add_argument("--windows-package-dir", type=Path)
    parser.add_argument("--android-package-dir", type=Path)
    parser.add_argument("--firefox-ref", default="firefox-upstream")
    parser.add_argument("--naivefox-ref", default="naivefox-full-source")
    args = parser.parse_args()
    topsrcdir = str(args.repo.resolve(strict=True))
    if subprocess.check_output(
        ["git", "status", "--porcelain=v1"], cwd=topsrcdir, text=True
    ).strip():
        raise AuditConsistencyError(
            "source checkout must be clean before closure report generation"
        )
    reports_dir = str(
        args.output_dir.resolve()
        if args.output_dir
        else Path(topsrcdir) / "netwerk" / "naivefox" / "reports"
    )
    os.makedirs(reports_dir, exist_ok=True)

    supplied_objdirs = {
        "linux-x86_64": args.linux_objdir,
        "windows-x86_64": args.windows_objdir,
        "android-aarch64": args.android_objdir,
    }
    supplied_packages = {
        "linux-x86_64": args.linux_package_dir,
        "windows-x86_64": args.windows_package_dir,
        "android-aarch64": args.android_package_dir,
    }

    print("Executing comprehensive multi-target link and source closure audit...")
    for target in TARGET_SPECS:
        supplied_objdir = supplied_objdirs[target["name"]]
        objdir = (
            str(supplied_objdir.resolve())
            if supplied_objdir
            else os.path.join(topsrcdir, target["default_objdir"])
        )
        triple = target["cargo_target"]
        print(f"\n--- Analyzing {target['name']} ({triple}) ---")
        report = analyze_target(
            topsrcdir,
            objdir,
            target,
            firefox_ref=args.firefox_ref,
            naivefox_ref=args.naivefox_ref,
            package_dir=supplied_packages[target["name"]],
        )
        filename = f"closure-report-{target['name']}.json"
        out_path = os.path.join(reports_dir, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(
            f"  -> Saved report: {normalize_path(out_path, topsrcdir)} ({os.path.getsize(out_path) / 1024:.1f} KB)"
        )
        s = report["summary"]
        print(
            f"  -> C/C++ TUs:     {s['cxx_translation_units_count']} translation units"
        )
        print(
            f"  -> Direct Objs:   {s['direct_link_objects_count']} files ({s['unstripped_link_objects_mb']} MB)"
        )
        print(
            f"  -> Static Libs:   {s['static_libraries_count']} archives "
            f"(gkrust verified; SpiderMonkey={'present' if s['spidermonkey_static_present'] else 'absent'})"
        )
        print(
            f"  -> Reachable Rust:{s['reachable_rust_crates_count']} crates (filtered from 850 total)"
        )
        print(
            f"  -> Dynamic Deps:  {s['dynamic_dependencies_count']} dynamic libraries"
        )
        print(f"  -> Staged Runtime:{s['staged_runtime_files_count']} package files")

    print("\nAll closure audits completed and self-consistency assertions passed.")


if __name__ == "__main__":
    main()
