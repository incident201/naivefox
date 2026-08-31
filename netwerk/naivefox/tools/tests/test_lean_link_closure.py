#!/usr/bin/env python3

import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyzer = load_script("lean_closure_analyzer", "analyze-full-closure.py")
assertions = load_script("lean_closure_assertions", "assert-closure.py")


class StagedPackageSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.objdir = self.root / "obj"
        self.objdir.mkdir()
        self.target = {"staged_package": "naivefox-test"}
        self.canonical = self.objdir / "package/naivefox-test"
        self.canonical.mkdir(parents=True)
        (self.canonical / "binary").write_bytes(b"stale")
        self.fresh = self.objdir / ".naivefox-evidence-test/package"
        self.fresh.mkdir(parents=True)
        (self.fresh / "binary").write_bytes(b"fresh")

    def test_explicit_fresh_stage_takes_priority_without_modifying_canonical(self):
        selected = analyzer.staged_package_dir(self.objdir, self.target, self.fresh)
        self.assertEqual(selected, self.fresh)
        self.assertEqual((selected / "binary").read_bytes(), b"fresh")
        self.assertEqual((self.canonical / "binary").read_bytes(), b"stale")
        self.assertEqual(
            analyzer.staged_package_dir(self.objdir, self.target), self.canonical
        )

    def test_explicit_stage_requires_existing_directory_below_exact_objdir(self):
        outside = self.root / "other-obj/package"
        outside.mkdir(parents=True)
        for path in (
            outside, self.objdir, self.objdir / "missing", Path("relative"),
            self.objdir / "package/../package/naivefox-test",
        ):
            with self.subTest(path=path):
                with self.assertRaises(analyzer.AuditConsistencyError):
                    analyzer.staged_package_dir(self.objdir, self.target, path)

    def test_explicit_stage_rejects_symlink_directory_or_parent(self):
        linked = self.objdir / "link"
        linked.symlink_to(self.fresh.parent, target_is_directory=True)
        for path in (linked, linked / "package"):
            with self.subTest(path=path):
                with self.assertRaises(analyzer.AuditConsistencyError):
                    analyzer.staged_package_dir(self.objdir, self.target, path)

    def test_explicit_stage_rejects_linked_runtime_files(self):
        (self.fresh / "library").symlink_to(self.canonical / "binary")
        with self.assertRaises(analyzer.AuditConsistencyError):
            analyzer.staged_package_dir(self.objdir, self.target, self.fresh)


class ConfiguredToolchainTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.objdir = Path(self.temporary.name)
        self.compiler = self.objdir / "clang"
        self.linker = self.objdir / "ld.lld"
        for tool, version in (
            (self.compiler, "clang version 22.1.8"),
            (self.linker, "LLD 22.1.8"),
        ):
            tool.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
            tool.chmod(0o755)
        self.substs = {
            "CC": [str(self.compiler), "--target=aarch64-linux-android26"],
            "CC_TYPE": "clang",
            "CC_VERSION": "22.1.8",
            "RELRHACK_LDFLAGS": ["-Wl,--real-linker," + str(self.linker)],
        }

    def inspect(self):
        (self.objdir / "config.status.json").write_text(
            json.dumps({"substs": self.substs})
        )
        return analyzer.get_configured_toolchain_versions(self.objdir)

    def test_records_configured_compiler_and_real_linker(self):
        self.assertEqual(self.inspect(), {
            "compiler_version": "clang 22.1.8", "linker_version": "LLD 22.1.8",
            "sccache_state": "disabled",
        })

    def test_records_explicit_windows_linker(self):
        self.substs["CC_TYPE"] = "clang-cl"
        self.substs["LINKER"] = str(self.linker)
        del self.substs["RELRHACK_LDFLAGS"]
        self.assertEqual(self.inspect()["compiler_version"], "clang-cl 22.1.8")
        self.assertEqual(self.inspect()["linker_version"], "LLD 22.1.8")

    def test_cannot_fall_back_to_path_tools_or_fabricated_versions(self):
        for field, value in (
            ("CC", ["clang"]),
            ("CC_VERSION", "18.1.8"),
            ("RELRHACK_LDFLAGS", []),
        ):
            with self.subTest(field=field):
                previous = self.substs[field]
                self.substs[field] = value
                with self.assertRaises(analyzer.AuditConsistencyError):
                    self.inspect()
                self.substs[field] = previous


class SourceDependenciesTest(unittest.TestCase):
    def test_relative_unified_inputs_use_the_active_object_compile_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            objdir = root / "object"
            compile_dir = objdir / "netwerk/base"
            dependencies = compile_dir / ".deps"
            dependencies.mkdir(parents=True)
            paths = [
                source / "netwerk/base/Native.cpp",
                source / "netwerk/base/Native.h",
            ]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            relative_cpp = os.path.relpath(paths[0], compile_dir)
            active = compile_dir / "Unified_cpp_base0.o"
            (dependencies / (active.name + ".pp")).write_text(
                f"{active.name}: Unified_cpp_base0.cpp \\\n"
                f"  {relative_cpp} \\\n  {paths[1]}\n"
            )
            stale = source / "dom/base/Document.cpp"
            stale.parent.mkdir(parents=True)
            stale.write_text("")
            (dependencies / "old.o.pp").write_text(f"old.o: {stale}\n")
            result = analyzer.get_source_and_build_inputs(
                source, objdir, [str(active)], []
            )
            self.assertEqual(
                result["cxx_translation_units"], ["netwerk/base/Native.cpp"]
            )
            self.assertEqual(result["headers"], ["netwerk/base/Native.h"])
            self.assertEqual(result["depfiles_scanned"], 1)

    def test_normalized_windows_archive_member_keeps_its_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            objdir = root / "object"
            compile_dir = objdir / "build/pure_virtual"
            dependencies = compile_dir / ".deps"
            dependencies.mkdir(parents=True)
            native = source / "build/pure_virtual/pure_virtual.c"
            native.parent.mkdir(parents=True)
            native.write_text("")
            (dependencies / "pure_virtual.obj.pp").write_text(
                f"pure_virtual.obj: {os.path.relpath(native, compile_dir)}\n"
            )
            stale_dependencies = objdir / "old/.deps"
            stale_dependencies.mkdir(parents=True)
            (stale_dependencies / "pure_virtual.obj.pp").write_text(
                f"pure_virtual.obj: {source}/dom/base/Document.cpp\n"
            )
            result = analyzer.get_source_and_build_inputs(
                source, objdir, [], ["objdir/build/pure_virtual/pure_virtual.obj"]
            )
            self.assertEqual(
                result["cxx_translation_units"], ["build/pure_virtual/pure_virtual.c"]
            )


class CompiledSourceBoundaryTest(unittest.TestCase):
    def test_value_helpers_and_browser_interface_headers_are_allowed(self):
        report = {
            "cxx_translation_units": [
                "dom/security/ReferrerInfo.cpp", "dom/security/SecFetch.cpp",
                "js/xpconnect/loader/AutoMemMap.cpp", "js/xpconnect/src/XPCString.cpp",
            ],
            "build_inputs": {"headers": ["dom/base/Document.h", "js/public/Value.h"]},
        }
        violations = []
        assertions._check_compiled_source_boundaries(report, violations)
        self.assertEqual(violations, [])

    def test_unified_includes_cannot_hide_heavy_implementations(self):
        for path in (
            "dom/base/Document.cpp",
            "js/src/vm/Interpreter.cpp",
            "gfx/thebes/gfxPlatform.cpp",
            "layout/base/PresShell.cpp", "intl/icu/source/common/utext.cpp",
            "mobile/android/geckoview/GeckoView.cpp", "browser/app/nsBrowserApp.cpp",
        ):
            with self.subTest(source=path):
                report = {"build_inputs": {"cxx_translation_units": [path]}}
                violations = []
                assertions._check_compiled_source_boundaries(report, violations)
                self.assertEqual(len(violations), 1)


class NativeWebSocketClosureTest(unittest.TestCase):
    def setUp(self):
        root = "netwerk/protocol/websocket/"
        self.report = {
            "cxx_translation_units": [
                root + "BaseWebSocketChannel.cpp", root + "WebSocketChannel.cpp"
            ],
            "direct_objects": [
                {"path": "objdir/" + root + "BaseWebSocketChannel.o"},
                {"path": "objdir/" + root + "WebSocketChannel.o"},
                {"path": "objdir/netwerk/naivefox/core/NoConnectWebSocket.o"},
                {"path": "objdir/ipc/ipdl/IPCMessageTypeName.o"},
            ],
            "static_libraries": [],
            "build_inputs": {
                "headers": [root + "WebSocketChannelChild.h"],
                "xpidl_inputs": [root + "nsIWebSocketEventService.idl"],
                "ipdl_inputs": [],
                "webidl_binding_inputs": ["dom/chrome-webidl/OriginAttributes.webidl"],
            },
        }

    def check(self, report):
        violations = []
        assertions._check_native_websocket_inputs(report, violations)
        return violations

    def test_native_sources_and_interface_headers_are_allowed_on_all_targets(self):
        self.assertEqual(self.check(self.report), [])
        windows = copy.deepcopy(self.report)
        for entry in windows["direct_objects"]:
            entry["path"] = str(Path(entry["path"]).with_suffix(".obj"))
        self.assertEqual(self.check(windows), [])

    def test_browser_source_is_rejected_even_in_an_unrelated_unified_object(self):
        self.report["build_inputs"]["cxx_translation_units"] = [
            "netwerk/protocol/websocket/WebSocketEventService.cpp"
        ]
        self.assertTrue(self.check(self.report))

    def test_missing_native_source_is_rejected(self):
        self.report["cxx_translation_units"].pop()
        self.assertTrue(self.check(self.report))

    def test_extra_browser_object_is_rejected(self):
        self.report["direct_objects"].append({
            "path": "objdir/netwerk/protocol/websocket/WebSocketChannelChild.o"
        })
        self.assertTrue(self.check(self.report))

    def test_generated_actor_object_is_rejected_outside_websocket_directory(self):
        for name in (
            "PWebSocketChild.o", "PTransportProviderParent.o",
            "Unified_cpp_ipc_ipdl0.o",
        ):
            with self.subTest(object=name):
                report = copy.deepcopy(self.report)
                report["direct_objects"].append({"path": "objdir/ipc/ipdl/" + name})
                self.assertTrue(self.check(report))

    def test_browser_archive_member_is_rejected(self):
        for name in (
            "WebSocketEventService.o", "WebSocketChannelParent.obj",
            "IPCTransportProvider.o", "PWebSocketChild.o", "PTransportProviderChild.obj",
            "Unified_cpp_netwerk_protocol_websocket0.o", "Unified_cpp_ipc_ipdl0.obj",
        ):
            with self.subTest(member=name):
                report = copy.deepcopy(self.report)
                report["static_libraries"] = [
                    {"path": "objdir/other.a", "members": [name]}
                ]
                self.assertTrue(self.check(report))

    def test_websocket_actor_or_dom_binding_generator_is_rejected(self):
        for field, path in (
            ("ipdl_inputs", "netwerk/protocol/websocket/PWebSocket.ipdl"),
            ("webidl_binding_inputs", "dom/webidl/WebSocket.webidl"),
        ):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["build_inputs"][field].append(path)
                self.assertTrue(self.check(report))


if __name__ == "__main__":
    unittest.main()
