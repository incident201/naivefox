import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "android-runtime-package.py"
)
SPEC = importlib.util.spec_from_file_location("android_runtime_package", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class AndroidRuntimePackageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.objdir = self.root / "obj"
        self.stage = self.root / "stage"
        self.dist = self.objdir / "dist/bin"
        self.ndk = self.root / "android-ndk-r29"
        self.dist.mkdir(parents=True)
        self.stage.mkdir()
        (self.source / "netwerk/naivefox").mkdir(parents=True)
        (self.source / "netwerk/naivefox/NaiveFoxAPI.h").write_text(
            "int NaiveFoxRunEmbedded(const char*, const char*, const char*);\n",
            encoding="utf-8",
        )
        (self.source / "netwerk/naivefox/NAIVEFOX_VERSION").write_text(
            "0.3.0-dev\n", encoding="utf-8"
        )
        system = (
            self.ndk
            / "toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib"
            / "aarch64-linux-android/26"
        )
        system.mkdir(parents=True)
        (system / "libc.so").write_text("stub", encoding="utf-8")

        self.readelf = self.root / "fake-readelf"
        self.readelf.write_text(
            """#!/usr/bin/env python3
import os
import sys
name = os.path.basename(sys.argv[-1])
if '--file-header' in sys.argv:
    print('  Class:                             ELF64')
    print('  Machine:                           AArch64')
elif '--dynamic' in sys.argv:
    needed = {
        'libxul.so': ('libnss3.so', 'libc.so'),
        'libnss3.so': ('libsoftokn3.so',),
        'libsoftokn3.so': ('libc.so',),
    }.get(name, ())
    for library in needed:
        print(f' 0x0000000000000001 (NEEDED) Shared library: [{library}]')
elif '--dyn-syms' in sys.argv:
    for index, symbol in enumerate((
        'NaiveFoxMain', 'NaiveFoxRequestStop',
        'NaiveFoxRunEmbedded', 'NaiveFoxVersion'
    ), 1):
        print(f'{index}: 0 0 FUNC GLOBAL DEFAULT 1 {symbol}')
""",
            encoding="utf-8",
        )
        self.readelf.chmod(0o755)
        self.strip = self.root / "fake-strip"
        self.strip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.strip.chmod(0o755)

        for name in ("libxul.so", "libnss3.so", "libsoftokn3.so"):
            (self.dist / name).write_bytes(name.encode("ascii"))
        (self.dist / "dependentlibs.list").write_text(
            "libnss3.so\nlibxul.so\n", encoding="utf-8"
        )
        self.objdir.joinpath("config.status").write_text(
            "substs = {"
            f"'ANDROID_NDK': {str(self.ndk)!r}, "
            "'MOZ_BUILD_APP': 'netwerk/naivefox', "
            "'MOZ_NAIVEFOX': '1', "
            "'MOZ_WIDGET_TOOLKIT': 'android', "
            "'OS_TARGET': 'Android', "
            f"'READELF': {str(self.readelf)!r}, "
            f"'STRIP': {str(self.strip)!r}, "
            "'target': 'aarch64-unknown-linux-android'}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_and_verify_measured_runtime(self):
        MODULE.create_package(self.source, self.objdir, self.stage)

        manifest = json.loads(
            (self.stage / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["abi"], "arm64-v8a")
        self.assertEqual(manifest["version"], "0.3.0-dev")
        self.assertEqual(
            manifest["required_android_system_libraries"], ["libc.so"]
        )
        self.assertEqual(
            sorted(path.name for path in (self.stage / "lib/arm64-v8a").iterdir()),
            ["libnss3.so", "libsoftokn3.so", "libxul.so"],
        )
        self.assertEqual(
            stat.S_IMODE((self.stage / "include/NaiveFoxAPI.h").stat().st_mode),
            0o644,
        )
        MODULE.verify_package(self.stage, str(self.readelf))

    def test_verify_rejects_modified_package(self):
        MODULE.create_package(self.source, self.objdir, self.stage)
        (self.stage / "include/NaiveFoxAPI.h").write_text(
            "modified\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(SystemExit, "does not match"):
            MODULE.verify_package(self.stage, None)

    def test_dependent_library_entries_must_be_basenames(self):
        (self.dist / "dependentlibs.list").write_text(
            "../libxul.so\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(SystemExit, "unsafe entry"):
            MODULE.create_package(self.source, self.objdir, self.stage)

    def test_executable_preserves_multicall_symlink_basename(self):
        multicall = self.root / "llvm-multicall"
        multicall.write_text(
            """#!/usr/bin/env python3
import os
import sys
if os.path.basename(sys.argv[0]) != 'llvm-readelf':
    raise SystemExit(2)
print('  Class:                             ELF64')
print('  Machine:                           AArch64')
""",
            encoding="utf-8",
        )
        multicall.chmod(0o755)
        readelf = self.root / "llvm-readelf"
        readelf.symlink_to(multicall.name)

        selected = MODULE.executable(str(readelf), "Android readelf")

        self.assertEqual(selected, str(readelf.absolute()))
        MODULE.validate_android_elf(selected, self.dist / "libxul.so")

    def test_dynamic_symbols_require_exported_definitions(self):
        readelf = self.root / "symbol-readelf"
        readelf.write_text(
            """#!/bin/sh
cat <<'EOF'
1: 0 0 FUNC GLOBAL DEFAULT UND NaiveFoxMain
2: 0 0 FUNC LOCAL DEFAULT 1 NaiveFoxRequestStop
3: 0 0 FUNC GLOBAL HIDDEN 1 NaiveFoxRunEmbedded
4: 0 0 FUNC GLOBAL DEFAULT 17 NaiveFoxVersion
EOF
""",
            encoding="utf-8",
        )
        readelf.chmod(0o755)

        self.assertEqual(
            MODULE.dynamic_naivefox_symbols(
                str(readelf), self.dist / "libxul.so"
            ),
            {"NaiveFoxVersion"},
        )


if __name__ == "__main__":
    unittest.main()
