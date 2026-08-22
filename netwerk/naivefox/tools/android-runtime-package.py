#!/usr/bin/env python3

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ABI = "arm64-v8a"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
MIN_ANDROID_API = 26
PRODUCT = "naivefox-android-embedded"
RUNTIME_PATH = f"lib/{ABI}"
TARGET = "android-aarch64"
CONFIGURE_TARGET = "aarch64-unknown-linux-android"
TARGET_TRIPLE = "aarch64-linux-android"
PUBLIC_SYMBOLS = {
    "NaiveFoxMain",
    "NaiveFoxRequestStop",
    "NaiveFoxRunEmbedded",
    "NaiveFoxVersion",
}
NSS_SIDE_MODULES = (
    "libsoftokn3.so",
    "libfreebl3.so",
    "libfreeblpriv3.so",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def executable(value: str, description: str) -> str:
    resolved = shutil.which(value) if not os.path.isabs(value) else value
    if not resolved or not os.access(resolved, os.X_OK):
        fail(f"{description} is not executable: {value}")
    # LLVM tools can be symlinks into a multicall binary whose behavior is
    # selected from argv[0]. Keep the requested basename intact.
    return os.path.abspath(resolved)


def config_status_values(path: Path) -> dict[str, object]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        fail(f"cannot read config.status: {error}")

    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "target":
            if isinstance(node.value, ast.Constant):
                values["target"] = node.value.value
            continue
        if not isinstance(target, ast.Name) or target.id != "substs":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
            ):
                values[key.value] = value.value
    return values


def run_readelf(readelf: str, arguments: list[str], path: Path) -> str:
    result = subprocess.run(
        [readelf, *arguments, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        fail(f"readelf failed for {path.name}: {result.stderr.strip()}")
    return result.stdout


def validate_android_elf(readelf: str, path: Path) -> None:
    header = run_readelf(readelf, ["--file-header"], path)
    if not re.search(r"^\s*Class:\s+ELF64\s*$", header, re.MULTILINE):
        fail(f"runtime library is not ELF64: {path.name}")
    if not re.search(r"^\s*Machine:\s+AArch64\s*$", header, re.MULTILINE):
        fail(f"runtime library is not AArch64: {path.name}")


def needed_libraries(readelf: str, path: Path) -> set[str]:
    dynamic = run_readelf(readelf, ["--dynamic"], path)
    return set(re.findall(r"\(NEEDED\).*Shared library: \[([^\]]+)\]", dynamic))


def dynamic_naivefox_symbols(readelf: str, path: Path) -> set[str]:
    symbols = run_readelf(readelf, ["--dyn-syms", "--wide"], path)
    names = set()
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        symbol_type, binding, visibility, section, raw_name = fields[-5:]
        name = raw_name.split("@", 1)[0]
        if (
            name.startswith("NaiveFox")
            and symbol_type in ("FUNC", "IFUNC")
            and binding in ("GLOBAL", "WEAK")
            and visibility == "DEFAULT"
            and section != "UND"
        ):
            names.add(name)
    return names


def safe_library_names(path: Path) -> list[str]:
    if not path.is_file():
        fail(f"dependent library list is missing: {path}")
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name or name != Path(name).name or name in (".", ".."):
            fail(f"unsafe entry in dependentlibs.list: {raw!r}")
        if not name.endswith(".so"):
            fail(f"unexpected non-library in dependentlibs.list: {name}")
        names.append(name)
    return names


def android_system_libraries(ndk: Path) -> set[str]:
    roots = list(
        ndk.glob(
            "toolchains/llvm/prebuilt/*/sysroot/usr/lib/"
            f"{TARGET_TRIPLE}/{MIN_ANDROID_API}"
        )
    )
    if len(roots) != 1 or not roots[0].is_dir():
        fail(f"cannot locate the Android {MIN_ANDROID_API} sysroot in {ndk}")
    return {path.name for path in roots[0].glob("*.so") if path.is_file()}


def collect_runtime_libraries(
    dist_bin: Path, ndk: Path, readelf: str
) -> tuple[list[str], list[str]]:
    requested = set(safe_library_names(dist_bin / "dependentlibs.list"))
    requested.add("libxul.so")
    if "libnss3.so" in requested:
        requested.update(
            name for name in NSS_SIDE_MODULES if (dist_bin / name).is_file()
        )

    system = android_system_libraries(ndk)
    resolved: set[str] = set()
    system_needed: set[str] = set()
    pending = sorted(requested)
    while pending:
        name = pending.pop()
        if name in resolved:
            continue
        source = dist_bin / name
        if not source.is_file():
            fail(f"required Android runtime library is missing: {source}")
        validate_android_elf(readelf, source)
        resolved.add(name)
        for needed in needed_libraries(readelf, source):
            if (dist_bin / needed).is_file():
                if needed not in resolved:
                    pending.append(needed)
            elif needed in system:
                system_needed.add(needed)
            else:
                fail(f"unresolved DT_NEEDED {needed} required by {name}")
    return sorted(resolved), sorted(system_needed)


def reject_links(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            fail(f"package contains a symbolic link: {path.relative_to(root)}")


def reject_sensitive_names(root: Path) -> None:
    patterns = (
        re.compile(r"cert9", re.IGNORECASE),
        re.compile(r"key4", re.IGNORECASE),
        re.compile(r"pkcs11", re.IGNORECASE),
        re.compile(r"keylog", re.IGNORECASE),
        re.compile(r"\.(?:log|pcap|pcapng)$", re.IGNORECASE),
    )
    for path in root.rglob("*"):
        if path.is_file() and any(pattern.search(path.name) for pattern in patterns):
            fail(f"package contains a sensitive artifact: {path.relative_to(root)}")


def file_manifest(root: Path) -> tuple[list[dict[str, object]], int]:
    files = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        files.append(
            {
                "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                "path": relative,
                "sha256": sha256(path),
                "size": size,
            }
        )
    return files, total_bytes


def read_version(source_root: Path) -> str:
    path = source_root / "netwerk/naivefox/VERSION"
    if not path.is_file():
        fail(f"NaiveFox version file is missing: {path}")
    version = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version):
        fail(f"invalid NaiveFox version: {version!r}")
    return version


def create_package(source_root: Path, objdir: Path, stage: Path) -> None:
    if any(stage.iterdir()):
        fail(f"staging directory is not empty: {stage}")
    values = config_status_values(objdir / "config.status")
    expected = {
        "MOZ_BUILD_APP": "netwerk/naivefox",
        "MOZ_NAIVEFOX": "1",
        "MOZ_WIDGET_TOOLKIT": "android",
        "OS_TARGET": "Android",
        "target": CONFIGURE_TARGET,
    }
    for name, value in expected.items():
        if values.get(name) != value:
            fail(f"config.status {name} must be {value!r}, got {values.get(name)!r}")

    ndk_value = values.get("ANDROID_NDK")
    if not isinstance(ndk_value, str):
        fail("config.status does not contain ANDROID_NDK")
    ndk = Path(ndk_value).resolve(strict=True)
    readelf = executable(
        os.environ.get("NAIVEFOX_READELF", str(values.get("READELF", ""))),
        "Android readelf",
    )
    strip = executable(
        os.environ.get("NAIVEFOX_STRIP", str(values.get("STRIP", ""))),
        "Android strip",
    )

    dist_bin = objdir / "dist/bin"
    if not dist_bin.is_dir():
        fail(f"Android dist/bin is missing: {dist_bin}")
    libraries, system_needed = collect_runtime_libraries(dist_bin, ndk, readelf)

    include_dir = stage / "include"
    library_dir = stage / RUNTIME_PATH
    include_dir.mkdir(mode=0o755)
    library_dir.mkdir(parents=True, mode=0o755)
    header = source_root / "netwerk/naivefox/NaiveFoxAPI.h"
    if not header.is_file():
        fail(f"public API header is missing: {header}")
    shutil.copy2(header, include_dir / header.name)
    os.chmod(include_dir / header.name, 0o644)

    for name in libraries:
        destination = library_dir / name
        shutil.copy2(dist_bin / name, destination, follow_symlinks=True)
        os.chmod(destination, 0o755)
        subprocess.run([strip, "--strip-debug", str(destination)], check=True)

    symbols = dynamic_naivefox_symbols(readelf, library_dir / "libxul.so")
    if symbols != PUBLIC_SYMBOLS:
        fail(
            "unexpected NaiveFox dynamic symbols in libxul.so: "
            f"expected {sorted(PUBLIC_SYMBOLS)}, got {sorted(symbols)}"
        )

    reject_links(stage)
    reject_sensitive_names(stage)
    files, total_bytes = file_manifest(stage)
    manifest = {
        "abi": ABI,
        "exported_symbols": sorted(PUBLIC_SYMBOLS),
        "files": files,
        "format_version": FORMAT_VERSION,
        "min_android_api": MIN_ANDROID_API,
        "product": PRODUCT,
        "required_android_system_libraries": system_needed,
        "runtime_path": RUNTIME_PATH,
        "target": TARGET,
        "target_triple": TARGET_TRIPLE,
        "total_bytes": total_bytes,
        "version": read_version(source_root),
    }
    manifest_path = stage / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(manifest_path, 0o644)


def verify_package(root: Path, readelf_value: str | None) -> None:
    reject_links(root)
    reject_sensitive_names(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        fail(f"Android runtime manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixed = {
        "abi": ABI,
        "exported_symbols": sorted(PUBLIC_SYMBOLS),
        "format_version": FORMAT_VERSION,
        "min_android_api": MIN_ANDROID_API,
        "product": PRODUCT,
        "runtime_path": RUNTIME_PATH,
        "target": TARGET,
        "target_triple": TARGET_TRIPLE,
    }
    for name, value in fixed.items():
        if manifest.get(name) != value:
            fail(f"manifest {name} must be {value!r}")
    files, total_bytes = file_manifest(root)
    if manifest.get("files") != files or manifest.get("total_bytes") != total_bytes:
        fail("Android runtime manifest does not match package contents")
    header = root / "include/NaiveFoxAPI.h"
    libxul = root / RUNTIME_PATH / "libxul.so"
    if not header.is_file() or not libxul.is_file():
        fail("Android package is missing NaiveFoxAPI.h or libxul.so")
    if readelf_value:
        readelf = executable(readelf_value, "readelf")
        validate_android_elf(readelf, libxul)
        symbols = dynamic_naivefox_symbols(readelf, libxul)
        if symbols != PUBLIC_SYMBOLS:
            fail(f"unexpected NaiveFox dynamic symbols: {sorted(symbols)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--objdir", type=Path, required=True)
    create.add_argument("--stage", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("root", type=Path)
    verify.add_argument("--readelf")
    args = parser.parse_args()

    if args.action == "create":
        create_package(
            args.source_root.resolve(strict=True),
            args.objdir.resolve(strict=True),
            args.stage.resolve(strict=True),
        )
    else:
        verify_package(args.root.resolve(strict=True), args.readelf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
