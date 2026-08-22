#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location(
    "collect_build_inputs", TOOLS / "collect-build-inputs.py"
)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class AssemblyPreprocessorInputsTest(unittest.TestCase):
    def test_gyp_objdir_uses_generated_srcdir_and_recursive_includes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source_tree = root / "source"
            objdir = root / "obj"
            source_dir = source_tree / "security" / "freebl"
            object_dir = objdir / "security" / "freebl" / "generated-library"
            assembly_dir = source_dir / "aarch64-gcm"
            include_dir = source_dir / "include"
            assembly_dir.mkdir(parents=True)
            include_dir.mkdir()
            object_dir.mkdir(parents=True)

            assembly = assembly_dir / "gcm-128-dec.S"
            compatibility = assembly_dir / "asm-compat.h"
            nested = include_dir / "asm-nested.h"
            assembly.write_text(
                '#include "asm-compat.h"\n#include <system-only.h>\n',
                encoding="utf-8",
            )
            compatibility.write_text(
                '#include "asm-nested.h"\n', encoding="utf-8"
            )
            nested.write_text('#include "asm-compat.h"\n', encoding="utf-8")

            makefile = object_dir / "Makefile"
            makefile.write_text(
                f"topsrcdir := {source_tree}\n"
                f"topobjdir := {objdir}\n"
                f"srcdir := {source_dir}\n",
                encoding="utf-8",
            )
            backend = object_dir / "backend.mk"
            backend.write_text(
                "LOCAL_INCLUDES += -I$(srcdir)/include\n"
                "SSRCS += $(srcdir)/aarch64-gcm/gcm-128-dec.S\n",
                encoding="utf-8",
            )

            sources, includes, include_directories = (
                collector.assembly_preprocessor_inputs(
                    backend, source_tree, objdir
                )
            )

            self.assertEqual(sources, [assembly.resolve()])
            self.assertEqual(set(includes), {compatibility.resolve(), nested.resolve()})
            self.assertEqual(include_directories, [include_dir])


if __name__ == "__main__":
    unittest.main()
