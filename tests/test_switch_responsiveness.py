from __future__ import annotations

import json
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import litegit
from litegit_core import StatusEntry
from litegit_diff import FileVersions
from ui_events import process_events


class SwitchingResponsivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="litegit-switch-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.cache = self.directory / "projects.json"
        for replacement in (
            patch("litegit.load_settings", return_value={}),
            patch("litegit.PROJECT_CACHE_FILE", self.cache),
            patch("litegit.CONFIG_FILE", self.directory / "global.json"),
            patch.object(litegit.RepositoryView, "refresh_all"),
            patch("litegit.messagebox.showerror"),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        try:
            self.root = litegit.LiteGitApp()
            self.root.withdraw()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        self.projects = [self.directory / name for name in ("first", "second")]
        for project in self.projects:
            project.mkdir()
            self.root._activate_repository(project)

    def tearDown(self) -> None:
        root = getattr(self, "root", None)
        if root is not None and not root._closing:
            self.pump(lambda: all(view._busy_count == 0 for view in root._all_views))
            root._close()

    def pump(self, condition, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            process_events(self.root)
            time.sleep(0.005)
        self.assertTrue(condition(), "The interface operation did not finish")

    def test_cached_switches_keep_views_without_disk_access_or_sidebar_reconstruction(self) -> None:
        first = self.root.views[self.root._project_key(self.projects[0])]
        first.notebook.select(2)
        first.commit_message_var.set("Keep the first draft")
        with (
            patch.object(Path, "resolve", side_effect=AssertionError("A cached switch must not resolve paths")),
            patch("litegit.save_settings") as save,
            patch.object(self.root.project_tree, "insert") as insert,
            patch.object(self.root.project_tree, "delete") as delete,
        ):
            for index in range(20):
                self.root.open_repository(str(self.projects[index % 2]))
            self.root.open_repository(str(self.projects[0]))
            save.assert_not_called()
            insert.assert_not_called()
            delete.assert_not_called()
        self.assertIs(first, self.root.active_view)
        self.assertEqual("place", first.winfo_manager())
        self.assertEqual(2, first.notebook.index(first.notebook.select()))
        self.assertEqual("Keep the first draft", first.commit_message_var.get())
        self.assertEqual(("0",), self.root.project_tree.selection())

    def test_rapid_switches_write_one_latest_snapshot_on_a_worker(self) -> None:
        main_thread = threading.get_ident()
        writer_threads = []
        original = litegit.save_settings

        def save(settings):
            writer_threads.append(threading.get_ident())
            original(settings)

        with patch("litegit.save_settings", side_effect=save):
            for index in range(20):
                self.root.open_repository(str(self.projects[index % 2]))
            self.assertEqual([], writer_threads)
            self.pump(lambda: self.root._settings_saved_revision == self.root._settings_revision)
        self.assertEqual(1, len(writer_threads))
        self.assertNotIn(main_thread, writer_threads)
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(str(self.projects[1]), stored["last_repository"])
        self.assertEqual([str(project) for project in self.projects], stored["projects"])

    def test_programmatic_switch_restores_focus_in_the_visible_project(self) -> None:
        first = self.root.views[self.root._project_key(self.projects[0])]
        second = self.root.active_view
        first_entry, second_entry = tk.Entry(first), tk.Entry(second)
        first._last_focus = first_entry
        with patch.object(self.root, "focus_get", return_value=second_entry), patch.object(first_entry, "focus_set") as focus:
            self.root.open_repository(str(self.projects[0]))
            focus.assert_called_once_with()
        self.assertIs(second_entry, second._last_focus)

    def test_sidebar_resize_keeps_inactive_projects_unmapped_and_reuses_rows(self) -> None:
        first = self.root.views[self.root._project_key(self.projects[0])]
        first.commit_message_var.set("Retain the hidden draft")
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.pump(lambda: self.root.active_view.winfo_ismapped() and not first.winfo_ismapped())
        process_events(self.root)
        events = []
        first.bind("<Configure>", lambda event: events.append(event), add="+")
        original_selection = self.root.project_tree.selection()
        with patch.object(self.root.project_tree, "insert") as insert, patch.object(self.root.project_tree, "delete") as delete:
            for _ in range(6):
                self.root.toggle_sidebar()
                expected = int(self.root.sidebar.cget("width"))
                self.pump(lambda: self.root.sidebar.winfo_width() == expected and self.root.active_view.winfo_width() == self.root.winfo_width() - expected)
                self.assertEqual(expected, self.root.active_view.winfo_x())
            insert.assert_not_called()
            delete.assert_not_called()
        self.assertEqual([], events)
        self.assertEqual(original_selection, self.root.project_tree.selection())
        self.assertEqual("Retain the hidden draft", first.commit_message_var.get())
        self.root.open_repository(str(self.projects[0]))
        self.pump(lambda: first.winfo_ismapped())
        self.assertEqual(1, sum(bool(view.winfo_ismapped()) for view in self.root._all_views))
        self.root.geometry("1280x760")
        self.pump(lambda: self.root.winfo_width() == 1280 and first.winfo_width() == 1280 - self.root.sidebar.winfo_width())
        self.assertEqual(self.root.winfo_height(), first.winfo_height())

    def test_slow_settings_storage_does_not_block_switching_or_interface_events(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def slow_save(_settings):
            entered.set()
            release.wait(5)

        with patch("litegit.save_settings", side_effect=slow_save):
            try:
                self.pump(entered.is_set)
                self.root.open_repository(str(self.projects[0]))
                delivered = []
                self.root.after(1, lambda: delivered.append(True))
                self.pump(lambda: bool(delivered))
                self.assertFalse(release.is_set())
                self.assertEqual(self.projects[0], self.root.active_view.repository)
            finally:
                release.set()
            self.pump(lambda: self.root._settings_saved_revision == self.root._settings_revision)

    def test_close_flushes_the_last_selection_before_the_debounce_timer_runs(self) -> None:
        self.root.open_repository(str(self.projects[0]))
        self.root.toggle_sidebar()
        self.root._close()
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(str(self.projects[0]), stored["last_repository"])
        self.assertTrue(stored["sidebar_collapsed"])

    def test_failed_background_save_preserves_the_cache_and_can_retry_on_close(self) -> None:
        self.assertTrue(self.root._finish_settings_save())
        previous = self.cache.read_bytes()
        with (
            patch("litegit.save_settings", side_effect=PermissionError("Cache is temporarily locked")),
            patch("litegit.messagebox.showerror") as error,
        ):
            self.root.open_repository(str(self.projects[0]))
            self.pump(lambda: error.called)
            self.assertEqual(previous, self.cache.read_bytes())
            self.assertTrue(self.root.winfo_exists())
        self.root._close()
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(str(self.projects[0]), stored["last_repository"])

    def test_close_save_failure_keeps_the_window_and_project_state_available(self) -> None:
        self.assertTrue(self.root._finish_settings_save())
        self.root.open_repository(str(self.projects[0]))
        with patch("litegit.save_settings", side_effect=PermissionError("Cache is locked")):
            self.root._close()
        self.assertFalse(self.root._closing)
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(str(self.projects[0]), self.root.settings["last_repository"])

    def test_alignment_runs_off_the_interface_thread_and_keeps_all_preview_lines(self) -> None:
        view = self.root.active_view
        view.status_entries = [StatusEntry(" ", "M", "large.txt")]
        with patch.object(view, "show_selected_diff"):
            view._render_changes()
            view.change_tree.selection_set("0")
            delivered = []
            self.root.after_idle(lambda: delivered.append(True))
            self.pump(lambda: bool(delivered))
        versions = FileVersions("old\n" * 1200, "new\n" * 1200, "HEAD", "Working tree")
        main_thread = threading.get_ident()
        worker_threads = []
        original = litegit.aligned_diff

        def align(before, after):
            worker_threads.append(threading.get_ident())
            return original(before, after)

        with patch("litegit.read_file_versions", return_value=versions), patch("litegit.aligned_diff", side_effect=align):
            view.show_selected_diff()
            self.pump(lambda: view._busy_count == 0 and view.diff_preview._render_job is None)
        self.assertEqual(1, len(worker_threads))
        self.assertNotIn(main_thread, worker_threads)
        self.assertIn(" 1200 | new", view.diff_preview.after_text.get("1.0", "end-1c"))
        self.assertTrue(view.diff_preview.after_text.tag_ranges("added"))
        self.assertEqual("disabled", view.diff_preview.after_text.cget("state"))

    def test_new_preview_cancels_unfinished_batches_from_the_previous_file(self) -> None:
        preview = self.root.active_view.diff_preview
        preview.render(FileVersions("old\n" * 1500, "changed\n" * 1500, "HEAD", "Working tree"), "old.txt")
        self.assertIsNotNone(preview._render_job)
        preview.render(FileVersions("before\n", "after\n", "HEAD", "Working tree"), "new.txt")
        process_events(self.root)
        self.assertIsNone(preview._render_job)
        self.assertEqual("    1 | after\n", preview.after_text.get("1.0", "end-1c"))
        self.assertNotIn("changed", preview.after_text.get("1.0", "end-1c"))

    def test_unchanged_scroll_position_does_not_schedule_another_scroll(self) -> None:
        preview = self.root.active_view.diff_preview
        other = preview.after_text
        with patch.object(other, "yview", return_value=(0.0, 0.25)), patch.object(other, "yview_moveto") as move:
            preview._sync_scroll("0.0", "0.25", Mock(), other)
            move.assert_not_called()
            preview._sync_scroll("0.5", "0.75", Mock(), other)
            move.assert_called_once_with("0.5")


if __name__ == "__main__":
    unittest.main()
