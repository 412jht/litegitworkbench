from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "packaging" / "build_windows_portable.py"
SPEC = importlib.util.spec_from_file_location("litegit_build_windows_portable", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {MODULE_PATH}")
BUILD_PORTABLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_PORTABLE)


class WindowsPortableReleaseTests(unittest.TestCase):
    def test_builds_smoke_tests_and_packages_without_native_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, metadata = BUILD_PORTABLE.build_release(root / "release", root / "build")
            self.assertTrue(archive.is_file())
            self.assertEqual(0, metadata["structural_verification"]["native_binary_count"])
            self.assertEqual(False, metadata["runtime_smoke_test"]["result"]["frozen"])
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertIn("LiteGitWorkbench.pyz", names)
                self.assertIn("run-portable.cmd", names)
                self.assertFalse(any(Path(name).suffix.casefold() in {".dll", ".exe", ".pyd"} for name in names))
            metadata_path = archive.with_suffix(archive.suffix + ".json")
            saved_metadata = json.loads(metadata_path.read_text(encoding="ascii"))
            self.assertEqual(archive.name, saved_metadata["archive"])


if __name__ == "__main__":
    unittest.main()
