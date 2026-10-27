from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging" / "build_release.py"
SPEC = importlib.util.spec_from_file_location("litegit_build_release", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {MODULE_PATH}")
BUILD_RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_RELEASE)


class LocalBuildPolicyTests(unittest.TestCase):
    def test_blocks_local_windows_executable_build(self) -> None:
        policy = {"block_windows_executable_build": True}
        self.assertTrue(
            BUILD_RELEASE.local_windows_build_is_blocked(
                policy,
                environment={},
                system="Windows",
            )
        )

    def test_allows_github_actions_windows_build(self) -> None:
        policy = {"block_windows_executable_build": True}
        self.assertFalse(
            BUILD_RELEASE.local_windows_build_is_blocked(
                policy,
                environment={"GITHUB_ACTIONS": "true"},
                system="Windows",
            )
        )

    def test_allows_non_windows_build(self) -> None:
        policy = {"block_windows_executable_build": True}
        self.assertFalse(
            BUILD_RELEASE.local_windows_build_is_blocked(
                policy,
                environment={},
                system="Linux",
            )
        )


class WindowsNativeReleaseTests(unittest.TestCase):
    def test_windows_downloads_preserve_the_same_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artefact = root / "LiteGitWorkbench.exe"
            content = b"MZ" + bytes(range(256))
            artefact.write_bytes(content)
            with (
                mock.patch.object(BUILD_RELEASE.platform, "system", return_value="Windows"),
                mock.patch.object(BUILD_RELEASE, "normalised_architecture", return_value="x86_64"),
            ):
                archive = BUILD_RELEASE.package(artefact, root / "release")
            self.assertEqual("LiteGitWorkbench-2.1.0-windows-x86_64.zip", archive.name)
            self.assertEqual(content, archive.with_suffix(".exe").read_bytes())
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(content, bundle.read("LiteGitWorkbench.exe"))
                self.assertEqual(
                    {"LiteGitWorkbench.exe", "README.md", "RELEASE.md", "CHANGELOG.md"},
                    set(bundle.namelist()),
                )

    def test_manifest_covers_exe_and_zip_and_preserves_portable_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artefact = root / "LiteGitWorkbench.exe"
            artefact.write_bytes(b"MZ native executable fixture")
            release = root / "release"
            release.mkdir()
            checksum_path = release / "SHA256SUMS.txt"
            portable_checksum = f"{'A' * 64}  LiteGitWorkbench-2.1.0-windows-portable.zip"
            stale_exe = f"{'0' * 64}  LiteGitWorkbench-2.1.0-windows-x86_64.exe"
            stale_zip = f"{'0' * 64}  LiteGitWorkbench-2.1.0-windows-x86_64.zip"
            checksum_path.write_text(
                "\n".join((portable_checksum, stale_exe, stale_zip)) + "\n",
                encoding="ascii",
            )
            with (
                mock.patch.object(BUILD_RELEASE.platform, "system", return_value="Windows"),
                mock.patch.object(BUILD_RELEASE, "normalised_architecture", return_value="x86_64"),
                mock.patch.object(BUILD_RELEASE, "load_local_policy", return_value={}),
                mock.patch.object(BUILD_RELEASE, "build", return_value=artefact),
                mock.patch.object(BUILD_RELEASE, "verify_artefact_structure", return_value={"status": "passed"}),
                mock.patch.object(BUILD_RELEASE, "smoke_test", return_value={"version": "2.1.0", "frozen": True}),
                mock.patch.object(
                    BUILD_RELEASE.sys,
                    "argv",
                    ["build_release.py", "--output-dir", str(release), "--build-dir", str(root / "build")],
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, BUILD_RELEASE.main())
            archive = release / "LiteGitWorkbench-2.1.0-windows-x86_64.zip"
            executable = archive.with_suffix(".exe")
            metadata = json.loads(archive.with_suffix(".zip.json").read_text(encoding="utf-8"))
            self.assertEqual(BUILD_RELEASE.sha256(archive), metadata["sha256"])
            self.assertEqual(executable.name, metadata["standalone_executable"]["file"])
            self.assertEqual(BUILD_RELEASE.sha256(executable), metadata["standalone_executable"]["sha256"])
            self.assertEqual("unsigned", metadata["standalone_executable"]["code_signing"])
            self.assertEqual("passed", metadata["runtime_smoke_test"]["status"])
            self.assertEqual(
                {
                    portable_checksum,
                    f"{BUILD_RELEASE.sha256(archive)}  {archive.name}",
                    f"{BUILD_RELEASE.sha256(executable)}  {executable.name}",
                },
                set(checksum_path.read_text(encoding="ascii").splitlines()),
            )


if __name__ == "__main__":
    unittest.main()
