from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import litegit


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
    def test_runtime_signature_gate_rejects_invalid_foreign_or_incomplete_reports(self) -> None:
        paths = [Path("runtime/python.exe"), Path("runtime/pythonw.exe")]
        valid = [{"file": path.name, "status": "Valid", "signer": "CN=Python Software Foundation"} for path in paths]
        reports = (
            (valid, True),
            ([dict(valid[0], status="HashMismatch"), valid[1]], False),
            ([dict(valid[0], signer="CN=Other publisher"), valid[1]], False),
            (valid[:1], False),
            ([valid[0], valid[0]], False),
        )
        for report, accepted in reports:
            with self.subTest(report=report), mock.patch.object(
                BUILD_RELEASE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(report), ""),
            ):
                if accepted:
                    self.assertEqual(report, BUILD_RELEASE.verify_windows_runtime_signatures(paths))
                else:
                    with self.assertRaises(RuntimeError):
                        BUILD_RELEASE.verify_windows_runtime_signatures(paths)

    def test_runtime_copy_preserves_official_files_and_omits_third_party_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "official", root / "bundled"
            source.mkdir()
            filenames = ("python.exe", "pythonw.exe", "python3.dll", f"python{sys.version_info.major}{sys.version_info.minor}.dll", "vcruntime140.dll", "LICENSE.txt")
            for filename in filenames:
                (source / filename).write_bytes(f"Unmodified fixture for {filename}".encode("ascii"))
            for name in ("DLLs", "tcl", "Lib", "Lib/site-packages", "Lib/__pycache__"):
                (source / name).mkdir(exist_ok=True)
            (source / "Lib" / "os.py").write_text("# Standard library fixture\n", encoding="ascii")
            (source / "Lib" / "site-packages" / "private.py").touch()
            (source / "Lib" / "__pycache__" / "stale.pyc").touch()
            with mock.patch.object(BUILD_RELEASE, "verify_windows_runtime_signatures", return_value=[{"status": "Valid"}]):
                metadata = BUILD_RELEASE.copy_windows_runtime(source, destination)
            for filename in filenames:
                self.assertEqual((source / filename).read_bytes(), (destination / filename).read_bytes())
                self.assertEqual(BUILD_RELEASE.sha256(source / filename), metadata["files"][filename])
            self.assertTrue((destination / "Lib" / "os.py").is_file())
            self.assertFalse((destination / "Lib" / "site-packages").exists())
            self.assertFalse((destination / "Lib" / "__pycache__").exists())

    def test_small_directory_executable_requires_a_complete_adjacent_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artefact = Path(directory) / "LiteGitWorkbench"
            runtime = artefact / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "Lib").mkdir()
            (artefact / "LiteGitWorkbench.exe").write_bytes(b"MZ" + bytes(128 * 1024))
            with mock.patch.object(BUILD_RELEASE.platform, "system", return_value="Windows"):
                with self.assertRaisesRegex(RuntimeError, "adjacent Python runtime"):
                    BUILD_RELEASE.verify_artefact_structure(artefact)
                (runtime / "python313.dll").write_bytes(b"Python runtime fixture")
                (runtime / "_tkinter.cp314-win_amd64.pyd").write_bytes(b"Tkinter extension fixture")
                (runtime / "init.tcl").write_bytes(b"Tcl runtime fixture")
                (runtime / "tk.tcl").write_bytes(b"Tk runtime fixture")
                (runtime / "pythonw.exe").touch()
                (runtime / "Lib" / "os.py").touch()
                (artefact / "LiteGitWorkbench.pyz").touch()
                self.assertEqual("passed", BUILD_RELEASE.verify_artefact_structure(artefact)["status"])

    def test_directory_bundle_preserves_the_exe_and_adjacent_runtime_without_a_standalone_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artefact = root / "LiteGitWorkbench"
            runtime = artefact / "runtime"
            runtime.mkdir(parents=True)
            executable = artefact / "LiteGitWorkbench.exe"
            executable.write_bytes(b"MZ directory executable fixture")
            (runtime / "python313.dll").write_bytes(b"Python runtime fixture")
            with (
                mock.patch.object(BUILD_RELEASE.platform, "system", return_value="Windows"),
                mock.patch.object(BUILD_RELEASE, "normalised_architecture", return_value="x86_64"),
            ):
                archive = BUILD_RELEASE.package(artefact, root / "release")
                self.assertEqual(executable, BUILD_RELEASE.executable_inside(artefact))
            self.assertFalse(archive.with_suffix(".exe").exists())
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(executable.read_bytes(), bundle.read("LiteGitWorkbench.exe"))
                self.assertEqual(b"Python runtime fixture", bundle.read("runtime/python313.dll"))
                self.assertIn("start-without-restore.cmd", bundle.namelist())

    def test_directory_bundle_metadata_and_manifest_require_the_complete_runtime_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artefact = root / "LiteGitWorkbench"
            runtime = artefact / "runtime"
            runtime.mkdir(parents=True)
            executable = artefact / "LiteGitWorkbench.exe"
            executable.write_bytes(b"MZ directory executable fixture")
            (runtime / "python313.dll").write_bytes(b"Python runtime fixture")
            (artefact / "runtime-verification.json").write_text('{"implementation": "Official CPython fixture"}', encoding="ascii")
            release = root / "release"
            with (
                mock.patch.object(BUILD_RELEASE.platform, "system", return_value="Windows"),
                mock.patch.object(BUILD_RELEASE, "normalised_architecture", return_value="x86_64"),
                mock.patch.object(BUILD_RELEASE, "load_local_policy", return_value={}),
                mock.patch.object(BUILD_RELEASE, "build", return_value=artefact),
                mock.patch.object(BUILD_RELEASE, "verify_artefact_structure", return_value={"status": "passed"}),
                mock.patch.object(BUILD_RELEASE, "smoke_test", return_value={"version": BUILD_RELEASE.VERSION, "frozen": True}),
                mock.patch.object(BUILD_RELEASE, "smoke_test_reword_helper", return_value={"status": "passed"}),
                mock.patch.object(BUILD_RELEASE.sys, "argv", ["build_release.py", "--output-dir", str(release), "--build-dir", str(root / "build")]),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, BUILD_RELEASE.main())
            archive = release / f"LiteGitWorkbench-{BUILD_RELEASE.VERSION}-windows-x86_64.zip"
            metadata = json.loads(archive.with_suffix(".zip.json").read_text(encoding="utf-8"))
            self.assertEqual("MSVC launcher with official CPython runtime", metadata["packager"])
            self.assertEqual("runtime", metadata["executable"]["runtime_directory"])
            self.assertEqual("Official CPython fixture", metadata["python_runtime"]["implementation"])
            self.assertFalse(metadata["executable"]["standalone"])
            self.assertEqual(BUILD_RELEASE.sha256(executable), metadata["executable"]["sha256"])
            self.assertNotIn("standalone_executable", metadata)
            self.assertEqual(f"{BUILD_RELEASE.sha256(archive)}  {archive.name}\n", (release / "SHA256SUMS.txt").read_text(encoding="ascii"))

    def test_windowed_smoke_failure_writes_a_diagnostic_before_returning_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "smoke.json"
            with (
                mock.patch.object(litegit.sys, "argv", ["LiteGitWorkbench", "--smoke-test", str(output), "--repository", directory]),
                mock.patch.object(litegit, "repository_smoke_test", side_effect=RuntimeError("Repository refresh failed")),
            ):
                self.assertEqual(1, litegit.main())
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("failed", result["repository_check"]["status"])
            self.assertEqual("RuntimeError: Repository refresh failed", result["repository_check"]["error"])

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
            self.assertEqual(f"LiteGitWorkbench-{BUILD_RELEASE.VERSION}-windows-x86_64.zip", archive.name)
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
            portable_checksum = f"{'A' * 64}  LiteGitWorkbench-{BUILD_RELEASE.VERSION}-windows-portable.zip"
            stale_exe = f"{'0' * 64}  LiteGitWorkbench-{BUILD_RELEASE.VERSION}-windows-x86_64.exe"
            stale_zip = f"{'0' * 64}  LiteGitWorkbench-{BUILD_RELEASE.VERSION}-windows-x86_64.zip"
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
                mock.patch.object(BUILD_RELEASE, "smoke_test", return_value={"version": BUILD_RELEASE.VERSION, "frozen": True}),
                mock.patch.object(BUILD_RELEASE, "smoke_test_reword_helper", return_value={"status": "passed"}),
                mock.patch.object(
                    BUILD_RELEASE.sys,
                    "argv",
                    ["build_release.py", "--output-dir", str(release), "--build-dir", str(root / "build")],
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, BUILD_RELEASE.main())
            archive = release / f"LiteGitWorkbench-{BUILD_RELEASE.VERSION}-windows-x86_64.zip"
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
