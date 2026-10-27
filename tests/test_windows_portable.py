from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
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
                self.assertIn("start-without-restore.cmd", names)
                self.assertFalse(any(Path(name).suffix.casefold() in {".dll", ".exe", ".pyd"} for name in names))
            metadata_path = archive.with_suffix(archive.suffix + ".json")
            saved_metadata = json.loads(metadata_path.read_text(encoding="ascii"))
            self.assertEqual(archive.name, saved_metadata["archive"])

    @unittest.skipUnless(os.name == "nt", "The Windows command interpreter is required")
    def test_launcher_forwards_quoted_arguments_and_child_exit_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="litegit portable launcher ") as directory:
            root = Path(directory)
            archive, _ = BUILD_PORTABLE.build_release(root / "release", root / "build")
            application = root / "portable application"
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(application)
            launcher = application / "run-portable.cmd"
            environment = dict(os.environ, LITEGIT_STARTUP_LOG=str(root / "startup.jsonl"))
            python_path = os.pathsep.join((str(Path(sys.executable).parent), str(Path(os.environ["SystemRoot"]) / "System32")))
            environments = {"default": environment, "python": dict(environment, PATH=python_path)}
            for runtime, selected_environment in environments.items():
                for expected_status in (0, 1):
                    with self.subTest(runtime=runtime, exit_status=expected_status):
                        output = root / f"smoke result {runtime} {expected_status}.json"
                        command = ["cmd.exe", "/d", "/c", "call", str(launcher), "--smoke-test", str(output)]
                        if expected_status:
                            command.extend(["--repository", str(root / "missing repository")])
                        result = subprocess.run(
                            command, cwd=root, env=selected_environment, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=30,
                        )
                        self.assertEqual(expected_status, result.returncode, result.stdout + result.stderr)
                        smoke = json.loads(output.read_text(encoding="utf-8"))
                        self.assertEqual(BUILD_PORTABLE.VERSION, smoke["version"])
                        self.assertIs(False, smoke["frozen"])
                        if expected_status:
                            self.assertEqual("failed", smoke["repository_check"]["status"])


if __name__ == "__main__":
    unittest.main()
