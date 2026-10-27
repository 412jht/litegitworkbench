from __future__ import annotations

import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import litegit
from ui_events import process_events


class ProjectCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.cache = self.directory / "run" / ".litegit-projects.json"
        self.global_file = self.directory / "user" / "settings.json"
        for replacement in (patch("litegit.PROJECT_CACHE_FILE", self.cache), patch("litegit.CONFIG_FILE", self.global_file)):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_local_cache_round_trip_uses_utf8_and_only_project_fields(self) -> None:
        project = str(self.directory / "\u5de5\u7a0b")
        settings = {"projects": [project], "last_repository": project, "sidebar_collapsed": True, "project_main_paths": {project: project}, "history_limit": 250}
        litegit.save_settings(settings)
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual([project], stored["projects"])
        self.assertEqual(1, stored["version"])
        self.assertNotIn("history_limit", stored)
        self.global_file.unlink()
        restored = litegit.load_settings()
        self.assertEqual(project, restored["last_repository"])
        self.assertTrue(restored["sidebar_collapsed"])
        self.assertEqual({project: project}, restored["project_main_paths"])

    def test_transient_diagnostics_do_not_read_or_save_user_projects(self) -> None:
        with (
            patch("litegit.load_settings") as load,
            patch("litegit.save_settings") as save,
            patch.object(litegit.RepositoryView, "refresh_all"),
        ):
            try:
                root = litegit.LiteGitApp(transient=True)
            except tk.TclError as exc:
                self.skipTest(f"Tk is unavailable: {exc}")
            root.withdraw()
            try:
                root._activate_repository(self.directory)
                self.assertEqual([str(self.directory)], root.project_paths)
                load.assert_not_called()
                save.assert_not_called()
            finally:
                root._close()

    def test_empty_local_cache_prevents_removed_projects_returning_from_global_settings(self) -> None:
        litegit._write_settings_json(self.global_file, {"projects": ["old"], "last_repository": "old", "history_limit": 250})
        litegit._write_settings_json(self.cache, {"projects": [], "last_repository": "", "version": 1})
        restored = litegit.load_settings()
        self.assertEqual([], restored["projects"])
        self.assertEqual("", restored["last_repository"])
        self.assertEqual(250, restored["history_limit"])

    def test_missing_or_corrupt_local_cache_falls_back_to_existing_settings(self) -> None:
        expected = {"projects": ["existing"], "last_repository": "existing"}
        litegit._write_settings_json(self.global_file, expected)
        self.assertEqual(expected, litegit.load_settings())
        self.cache.parent.mkdir()
        for content in (b"invalid JSON", b"\xff\xfe", b'{"projects": "invalid"}'):
            self.cache.write_bytes(content)
            self.assertEqual(expected, litegit.load_settings())

    def test_failed_cache_replace_preserves_previous_cache_and_cleans_temporary_file(self) -> None:
        litegit.save_settings({"projects": ["existing"]})
        previous = self.cache.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("Simulated locked cache")):
            with self.assertRaises(PermissionError):
                litegit.save_settings({"projects": ["replacement"]})
        self.assertEqual(previous, self.cache.read_bytes())
        self.assertEqual([], list(self.cache.parent.glob("*.tmp")))

    def test_cached_projects_restore_the_list_labels_and_last_project_on_launch(self) -> None:
        main = self.directory / "main-project"
        worktree = self.directory / "worktree-with-another-parent"
        main.mkdir()
        worktree.mkdir()
        main_paths = {litegit.LiteGitApp._project_key(main): str(main), litegit.LiteGitApp._project_key(worktree): str(main)}
        litegit.save_settings({"projects": [str(main), str(worktree)], "last_repository": str(worktree), "sidebar_collapsed": False, "project_main_paths": main_paths})
        for replacement in (patch.object(litegit.RepositoryView, "refresh_all"), patch("litegit.discover_repository", side_effect=lambda path: Path(path).resolve())):
            replacement.start()
            self.addCleanup(replacement.stop)
        try:
            root = litegit.LiteGitApp()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root._close)
        deadline = time.monotonic() + 5
        while (root.active_view.repository != worktree or root.active_view._busy_count) and time.monotonic() < deadline:
            process_events(root)
            time.sleep(0.01)
        self.assertEqual(worktree, root.active_view.repository)
        self.assertEqual("main-project", root.project_tree.item("0", "text"))
        self.assertEqual("worktree-with-another-parent / main-project", root.project_tree.item("1", "text"))
        root.remove_project(str(worktree))
        deadline = time.monotonic() + 5
        while (root.active_view._busy_count or root._settings_saved_revision < root._settings_revision) and time.monotonic() < deadline:
            process_events(root)
            time.sleep(0.01)
        process_events(root)
        self.assertEqual([str(main)], litegit.load_settings()["projects"])
        self.assertEqual(str(main), litegit.load_settings()["last_repository"])
        self.assertTrue(worktree.is_dir())


if __name__ == "__main__":
    unittest.main()
