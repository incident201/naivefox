#!/usr/bin/env python3
"""
analyze-full-closure.py - Comprehensive Multi-Target Link & Source Closure Audit
Produces detailed machine-readable JSON reports capturing the complete dependency graph.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def parse_response_file(rsp_path):
    """Recursively parse linker response files (.list / .rsp)."""
    items = []
    if not os.path.exists(rsp_path):
        return items
    base_dir = os.path.dirname(rsp_path)
    with open(rsp_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for token in line.split():
                if token.startswith("@"):
                    sub_rsp = token[1:]
                    if not os.path.isabs(sub_rsp):
                        sub_rsp = os.path.normpath(os.path.join(base_dir, sub_rsp))
                    items.extend(parse_response_file(sub_rsp))
                else:
                    if not os.path.isabs(token):
                        token = os.path.normpath(os.path.join(base_dir, token))
                    items.append(token)
    return items


def get_archive_members(archive_path):
    """Extract object member names from a static archive (.a / .lib)."""
    members = []
    if not os.path.exists(archive_path):
        return members
    for cmd in [["ar", "t", archive_path], ["llvm-ar", "t", archive_path]]:
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
            for line in out.splitlines():
                line = line.strip()
                if line:
                    members.append(line)
            if members:
                return members
        except Exception:
            pass
    return members


def get_rust_closure(topsrcdir):
    """Get full Rust dependency closure via cargo metadata."""
    manifest_path = os.path.join(
        topsrcdir, "toolkit", "library", "rust", "naivefox", "Cargo.toml"
    )
    if not os.path.exists(manifest_path):
        manifest_path = os.path.join(
            topsrcdir, "toolkit", "library", "rust", "Cargo.toml"
        )
    crates = []
    if os.path.exists(manifest_path):
        try:
            cmd = [
                "cargo",
                "metadata",
                "--manifest-path",
                manifest_path,
                "--format-version",
                "1",
            ]
            raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
            meta = json.loads(raw)
            for pkg in meta.get("packages", []):
                manifest = pkg.get("manifest_path", "")
                rel_manifest = manifest
                if manifest.startswith(topsrcdir):
                    rel_manifest = os.path.relpath(manifest, topsrcdir)
                crates.append(
                    {
                        "name": pkg.get("name"),
                        "version": pkg.get("version"),
                        "source": "in-tree" if manifest.startswith(topsrcdir) else (pkg.get("source") or "crates.io"),
                        "manifest_path": rel_manifest,
                    }
                )
            crates.sort(key=lambda x: x.get("name", ""))
        except Exception as e:
            crates.append({"error": str(e)})
    return crates


def get_dynamic_dependencies(binary_path):
    """Get DT_NEEDED or PE imports."""
    deps = []
    if not os.path.exists(binary_path):
        return deps
    # Try readelf (ELF / Linux)
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
    # Try objdump or llvm-readobj (PE / Windows)
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
    return deps


def get_idl_inputs(topsrcdir, objdir_path):
    """Gather all xpidl and ipdl sources used in the build."""
    xpidl_sources = []
    ipdl_sources = []
    # Search generated .stub files in objdir to find active idl files
    for root, dirs, files in os.walk(str(objdir_path / "dist" / "include")):
        for f in files:
            if f.endswith(".h"):
                # Corresponding idl in topsrcdir
                idl_name = f[:-2] + ".idl"
                xpidl_sources.append(f)
    return {
        "exported_headers_count": len(xpidl_sources),
    }


def analyze_target(topsrcdir, objdir, target_name):
    """Analyze full closure for a specific build target object directory."""
    objdir_path = Path(objdir)
    if not objdir_path.exists():
        return {"error": f"Object directory {objdir} not found"}

    list_candidates = [
        objdir_path / "toolkit" / "library" / "build" / "libxul_so.list",
        objdir_path / "toolkit" / "library" / "build" / "xul_dll.list",
        objdir_path / "toolkit" / "library" / "build" / "xul.dll.list",
    ]

    libxul_list = None
    for cand in list_candidates:
        if cand.exists():
            libxul_list = cand
            break

    link_inputs = []
    if libxul_list:
        link_inputs = parse_response_file(str(libxul_list))

    objects = []
    static_libs = []
    other_flags = []

    total_unstripped_obj_bytes = 0
    component_groups = {}

    for item in link_inputs:
        full_path = os.path.normpath(item)
        if item.endswith(".o") or item.endswith(".obj"):
            sz = os.path.getsize(full_path) if os.path.exists(full_path) else 0
            total_unstripped_obj_bytes += sz
            rel = os.path.relpath(full_path, str(objdir_path))
            top_comp = rel.split(os.sep)[0]
            if top_comp not in component_groups:
                component_groups[top_comp] = {"files": 0, "bytes": 0}
            component_groups[top_comp]["files"] += 1
            component_groups[top_comp]["bytes"] += sz
            objects.append({"path": rel, "size_bytes": sz})
        elif item.endswith(".a") or item.endswith(".lib"):
            sz = os.path.getsize(full_path) if os.path.exists(full_path) else 0
            members = get_archive_members(full_path)
            static_libs.append(
                {
                    "path": os.path.relpath(full_path, str(objdir_path)),
                    "size_bytes": sz,
                    "member_count": len(members),
                    "members_sample": members[:20],
                }
            )
        else:
            other_flags.append(item)

    # Inspect final libxul binary
    is_linux = "linux" in target_name.lower()
    xul_name = "libxul.so" if is_linux else "xul.dll"
    naivefox_name = "naivefox" if is_linux else "naivefox.exe"

    bin_xul = objdir_path / "dist" / "bin" / xul_name
    if not bin_xul.exists():
        bin_xul = objdir_path / "toolkit" / "library" / "build" / xul_name

    bin_naivefox = objdir_path / "dist" / "bin" / naivefox_name
    if not bin_naivefox.exists():
        bin_naivefox = objdir_path / "netwerk" / "naivefox" / naivefox_name

    bin_xul_size = bin_xul.stat().st_size if bin_xul.exists() else 0
    bin_naivefox_size = bin_naivefox.stat().st_size if bin_naivefox.exists() else 0
    dynamic_deps = get_dynamic_dependencies(str(bin_xul))

    # SpiderMonkey static library audit
    js_static = objdir_path / "js" / "src" / "build" / ("libjs_static.a" if is_linux else "js_static.lib")
    js_static_members = get_archive_members(str(js_static)) if js_static.exists() else []

    # Rust static library audit
    gkrust_lib = objdir_path / "toolkit" / "library" / "rust" / ("libgkrust.a" if is_linux else "gkrust.lib")
    gkrust_members = get_archive_members(str(gkrust_lib)) if gkrust_lib.exists() else []

    # Rust crate closure
    rust_crates = get_rust_closure(topsrcdir)

    # Runtime shared libraries in dist/bin
    runtime_libs = []
    dist_bin = objdir_path / "dist" / "bin"
    if dist_bin.exists():
        for f in sorted(dist_bin.iterdir()):
            if f.is_file() and (f.suffix in [".so", ".dll", ".dylib", ".exe"] or f.name == "naivefox"):
                runtime_libs.append(
                    {
                        "name": f.name,
                        "size_bytes": f.stat().st_size,
                    }
                )

    report = {
        "target": target_name,
        "summary": {
            "object_count": len(objects),
            "unstripped_link_objects_bytes": total_unstripped_obj_bytes,
            "unstripped_link_objects_mb": round(total_unstripped_obj_bytes / (1024 * 1024), 2),
            "static_library_count": len(static_libs),
            "libxul_size_bytes": bin_xul_size,
            "libxul_size_mb": round(bin_xul_size / (1024 * 1024), 2),
            "naivefox_bin_size_bytes": bin_naivefox_size,
            "naivefox_bin_size_mb": round(bin_naivefox_size / (1024 * 1024), 2),
            "rust_crate_count": len(rust_crates),
            "runtime_libraries_count": len(runtime_libs),
            "dynamic_dependencies_count": len(dynamic_deps),
        },
        "component_groups": {
            k: {
                "files": v["files"],
                "bytes": v["bytes"],
                "mb": round(v["bytes"] / (1024 * 1024), 2),
            }
            for k, v in sorted(component_groups.items(), key=lambda x: x[1]["bytes"], reverse=True)
        },
        "dynamic_dependencies": dynamic_deps,
        "runtime_libraries": runtime_libs,
        "static_libraries": static_libs,
        "spidermonkey": {
            "archive": str(js_static.relative_to(objdir_path)) if js_static.exists() else None,
            "size_bytes": js_static.stat().st_size if js_static.exists() else 0,
            "size_mb": round((js_static.stat().st_size / (1024 * 1024)), 2) if js_static.exists() else 0,
            "member_count": len(js_static_members),
        },
        "gkrust": {
            "archive": str(gkrust_lib.relative_to(objdir_path)) if gkrust_lib.exists() else None,
            "size_bytes": gkrust_lib.stat().st_size if gkrust_lib.exists() else 0,
            "size_mb": round((gkrust_lib.stat().st_size / (1024 * 1024)), 2) if gkrust_lib.exists() else 0,
            "member_count": len(gkrust_members),
        },
        "rust_crates": rust_crates,
        "objects": objects,
    }
    return report


def main():
    topsrcdir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    reports_dir = os.path.join(topsrcdir, "netwerk", "naivefox", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    targets = [
        ("Linux x86_64", os.path.join(topsrcdir, "obj-naivefox-minimal"), "closure-report-linux-x86_64.json"),
        ("Windows x86_64", os.path.join(topsrcdir, "obj-naivefox-windows-x86_64"), "closure-report-windows-x86_64.json"),
    ]

    print("Generating comprehensive multi-target closure reports...")
    for name, objdir, filename in targets:
        print(f"  Analyzing {name} ({objdir})...")
        rep = analyze_target(topsrcdir, objdir, name)
        out_path = os.path.join(reports_dir, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
        print(f"  Wrote report: {out_path} ({os.path.getsize(out_path) / 1024:.1f} KB)")
        if "summary" in rep:
            s = rep["summary"]
            print(f"    -> Objects: {s['object_count']} files ({s['unstripped_link_objects_mb']} MB)")
            print(f"    -> libxul:  {s['libxul_size_mb']} MB")
            print(f"    -> Crates:  {s['rust_crate_count']} Rust packages")
            print(f"    -> DynDeps: {s['dynamic_dependencies_count']} shared libraries")

    print("\nClosure analysis completed successfully.")


if __name__ == "__main__":
    main()
