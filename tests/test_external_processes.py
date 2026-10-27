from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import litegit_core
import litegit_merge
import litegit_process


class ExternalProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bundle = self.root / "bundle"
        self.system = self.root / "system-git"
        self.bundle.mkdir()
        self.system.mkdir()
        name = "git.exe" if os.name == "nt" else "git"
        self.git = self.system / name
        self.git.touch()
        self.git.chmod(0o755)

    def frozen(self) -> None:
        replacement = mock.patch.object(litegit_process.sys, "frozen", True, create=True)
        replacement.start()
        self.addCleanup(replacement.stop)
        replacement = mock.patch.object(litegit_process.sys, "_MEIPASS", str(self.bundle), create=True)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_source_environment_is_preserved_and_copied(self) -> None:
        original = {"PATH": str(self.bundle), "LD_LIBRARY_PATH": "custom", "GIT_SSH_COMMAND": "custom-ssh"}
        with mock.patch.object(litegit_process.sys, "frozen", False, create=True):
            cleaned = litegit_process.external_environment(original)
        self.assertEqual(original, cleaned)
        self.assertIsNot(original, cleaned)

    def test_bundle_paths_are_removed_without_removing_neighbours_or_authentication(self) -> None:
        self.frozen()
        neighbour = self.root / "bundle-neighbour"
        original = {
            "PATH": os.pathsep.join((str(self.bundle), str(self.bundle / "bin"), str(neighbour), str(self.system))),
            "DYLD_LIBRARY_PATH": os.pathsep.join((str(self.bundle), str(self.system))),
            "GIT_SSH_COMMAND": "custom-ssh",
            "GIT_EDITOR": "custom-editor",
            "SSH_AUTH_SOCK": "existing-agent",
        }
        cleaned = litegit_process.external_environment(original)
        self.assertEqual(os.pathsep.join((str(neighbour), str(self.system))), cleaned["PATH"])
        self.assertEqual(str(self.system), cleaned["DYLD_LIBRARY_PATH"])
        self.assertEqual("existing-agent", cleaned["SSH_AUTH_SOCK"])
        self.assertEqual("custom-editor", cleaned["GIT_EDITOR"])
        self.assertEqual("custom-ssh", cleaned["GIT_SSH_COMMAND"])
        self.assertIn(str(self.bundle), original["PATH"])

    def test_linux_library_path_is_restored_for_git_only(self) -> None:
        self.frozen()
        original = {"LD_LIBRARY_PATH": str(self.bundle), "LD_LIBRARY_PATH_ORIG": str(self.system)}
        with mock.patch.object(litegit_process.sys, "platform", "linux"):
            cleaned = litegit_process.external_environment(original)
            absent = litegit_process.external_environment({"LD_LIBRARY_PATH": str(self.bundle)})
        self.assertEqual(str(self.system), cleaned["LD_LIBRARY_PATH"])
        self.assertEqual(str(self.bundle), original["LD_LIBRARY_PATH"])
        self.assertNotIn("LD_LIBRARY_PATH", absent)

    def test_executable_resolution_uses_explicit_path_and_returns_an_absolute_filename(self) -> None:
        self.assertEqual(self.git, litegit_process.find_executable("git", {"PATH": str(self.system)}))
        self.assertIsNone(litegit_process.find_executable("git", {"PATH": str(self.bundle)}))

    def test_runtime_status_distinguishes_adjacent_libraries_from_a_temporary_bundle(self) -> None:
        self.frozen()
        with mock.patch.object(litegit_process.sys, "platform", "linux"):
            with mock.patch.object(litegit_process.sys, "executable", str(self.root / "LiteGitWorkbench.exe")):
                self.assertTrue(litegit_process.external_runtime_status()["bundle_in_application_directory"])
            with mock.patch.object(litegit_process.sys, "executable", str(self.root / "other-folder" / "LiteGitWorkbench.exe")):
                self.assertFalse(litegit_process.external_runtime_status()["bundle_in_application_directory"])

    def test_concurrent_windows_launches_initialise_the_dll_search_once(self) -> None:
        self.frozen()
        handle = mock.Mock()
        kernel = mock.Mock()
        kernel.SetDllDirectoryW.return_value = 1
        with (
            mock.patch.object(litegit_process.sys, "platform", "win32"),
            mock.patch.object(litegit_process, "_windows_runtime_ready", False),
            mock.patch.object(litegit_process, "_dll_directory_handle", None),
            mock.patch.object(litegit_process.os, "add_dll_directory", return_value=handle, create=True) as add,
            mock.patch.object(litegit_process.ctypes, "WinDLL", return_value=kernel, create=True),
        ):
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda _index: litegit_process.prepare_external_processes(), range(24)))
            self.assertTrue(litegit_process._windows_runtime_ready)
            self.assertIs(handle, litegit_process._dll_directory_handle)
            add.assert_called_once_with(str(self.bundle))
            kernel.SetDllDirectoryW.assert_called_once_with(None)
            handle.close.assert_not_called()

    def test_failed_windows_initialisation_releases_its_registration_and_can_retry(self) -> None:
        self.frozen()
        failed, retained = mock.Mock(), mock.Mock()
        kernel = mock.Mock()
        kernel.SetDllDirectoryW.side_effect = [OSError("DLL reset refused"), 1]
        with (
            mock.patch.object(litegit_process.sys, "platform", "win32"),
            mock.patch.object(litegit_process, "_windows_runtime_ready", False),
            mock.patch.object(litegit_process, "_dll_directory_handle", None),
            mock.patch.object(litegit_process.os, "add_dll_directory", side_effect=[failed, retained], create=True),
            mock.patch.object(litegit_process.ctypes, "WinDLL", return_value=kernel, create=True),
        ):
            with self.assertRaisesRegex(OSError, "DLL reset refused"):
                litegit_process.prepare_external_processes()
            self.assertFalse(litegit_process._windows_runtime_ready)
            failed.close.assert_called_once()
            litegit_process.prepare_external_processes()
            self.assertIs(retained, litegit_process._dll_directory_handle)
            retained.close.assert_not_called()

    def test_windows_status_checks_the_directory_contents_instead_of_its_required_buffer_size(self) -> None:
        self.frozen()
        kernel = mock.Mock()
        for value, expected in (("", True), (str(self.bundle), False)):
            with self.subTest(directory=value):
                def query(length, buffer):
                    if length == 0:
                        return len(value) + 1
                    buffer.value = value
                    return len(value)

                kernel.GetDllDirectoryW.side_effect = query
                with (
                    mock.patch.object(litegit_process.sys, "platform", "win32"),
                    mock.patch.object(litegit_process, "_windows_runtime_ready", True),
                    mock.patch.object(litegit_process.ctypes, "WinDLL", return_value=kernel, create=True),
                ):
                    self.assertEqual(expected, litegit_process.external_runtime_status()["windows_dll_search_reset"])

    def test_text_and_binary_git_use_the_same_clean_runtime_after_overrides(self) -> None:
        self.frozen()
        environment = {"PATH": str(self.system), "GIT_ASKPASS": "configured-helper"}
        dirty_path = os.pathsep.join((str(self.bundle), str(self.system)))
        text = subprocess.CompletedProcess([], 0, "branch\n")
        binary = subprocess.CompletedProcess([], 0, b"\x00\xff\r\n", b"")
        with (
            mock.patch.object(litegit_core, "build_git_environment", return_value=environment),
            mock.patch.object(litegit_core, "prepare_external_processes") as prepare,
            mock.patch.object(litegit_core.subprocess, "run", side_effect=[text, binary]) as launch,
        ):
            result = litegit_core.run_git(self.root, ["branch"], environment_overrides={"PATH": dirty_path})
            blob = litegit_merge._blob(self.root, "object-id")
        self.assertEqual("branch\n", result.output)
        self.assertEqual(b"\x00\xff\r\n", blob)
        self.assertEqual(2, prepare.call_count)
        for call in launch.call_args_list:
            self.assertEqual(str(self.git), call.args[0][0])
            self.assertEqual(str(self.system), call.kwargs["env"]["PATH"])
            self.assertEqual("configured-helper", call.kwargs["env"]["GIT_ASKPASS"])
            self.assertNotIn("shell", call.kwargs)

    def test_git_is_not_started_when_runtime_preparation_fails(self) -> None:
        with (
            mock.patch.object(litegit_core, "build_git_environment", return_value={"PATH": str(self.system)}),
            mock.patch.object(litegit_core, "prepare_external_processes", side_effect=OSError("DLL reset refused")),
            mock.patch.object(litegit_core.subprocess, "run") as launch,
        ):
            with self.assertRaises(litegit_core.GitError) as caught:
                litegit_core.run_git(self.root, ["status"])
        self.assertEqual(126, caught.exception.returncode)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
