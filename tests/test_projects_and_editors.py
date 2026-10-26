from __future__ import annotations

import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import litegit
from litegit_core import BranchEntry, StatusEntry
from litegit_diff import FileVersions
from litegit_merge import ConflictFile
from litegit_views import ConflictEditor


class ProjectAndEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        for replacement in (
            patch("litegit.load_settings", return_value={}),
            patch("litegit.save_settings"),
            patch.object(litegit.RepositoryView, "refresh_all"),
            patch("litegit.messagebox.showerror"),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        try:
            self.root = litegit.LiteGitApp()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.first = Path(self.temporary.name) / "first"
        self.second = Path(self.temporary.name) / "second"
        self.first.mkdir()
        self.second.mkdir()

    def tearDown(self) -> None:
        root = getattr(self, "root", None)
        if root is None or not root.winfo_exists():
            return
        self.pump(lambda: all(not view._busy_count for view in set(root.views.values()) | {root.active_view}))
        for view in set(root.views.values()) | {root.active_view}:
            for child in view.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()
        root._close()

    def pump(self, condition: Callable[[], bool], timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertTrue(condition(), "The asynchronous UI operation did not finish")

    def activate(self, repository: Path) -> litegit.RepositoryView:
        self.root._activate_repository(repository)
        return self.root.active_view

    def test_switching_keeps_widget_identity_tabs_filters_and_commit_drafts(self) -> None:
        first = self.activate(self.first)
        first.commit_message_var.set("First project draft")
        first.branch_filter_var.set("first filter")
        first.notebook.select(2)
        first.diff_scope_var.set("Unstaged")
        first.console_text.insert(tk.END, "First project console")
        second = self.activate(self.second)
        second.commit_message_var.set("Second project draft")
        self.root.open_repository(str(self.first))
        self.assertIs(first, self.root.active_view)
        self.assertEqual("First project draft", first.commit_message_var.get())
        self.assertEqual("first filter", first.branch_filter_var.get())
        self.assertEqual(2, first.notebook.index(first.notebook.select()))
        self.assertEqual("Unstaged", first.diff_scope_var.get())
        self.assertIn("First project console", first.console_text.get("1.0", tk.END))
        self.root.open_repository(str(self.second))
        self.assertIs(second, self.root.active_view)
        self.assertEqual("Second project draft", second.commit_message_var.get())
        self.assertEqual(2, len(self.root.views))
        self.assertEqual([str(self.first), str(self.second)], self.root.settings["projects"])

    def test_late_background_result_stays_in_its_original_project(self) -> None:
        first = self.activate(self.first)
        release = threading.Event()

        def worker() -> str:
            release.wait(3)
            return str(first.repository)

        first._submit("Old project operation", worker, lambda value: first.commit_message_var.set(value))
        second = self.activate(self.second)
        second.commit_message_var.set("Second project draft")
        release.set()
        self.pump(lambda: first._busy_count == 0)
        self.assertEqual(str(self.first), first.commit_message_var.get())
        self.assertEqual("Second project draft", second.commit_message_var.get())

    def test_older_preview_response_cannot_overwrite_the_latest_selection(self) -> None:
        view = self.activate(self.first)
        release = threading.Event()
        results = []
        view._submit("Old preview", lambda: (release.wait(3), "old")[1], results.append, channel="diff")
        view._submit("New preview", lambda: "new", results.append, channel="diff")
        self.pump(lambda: results == ["new"])
        release.set()
        self.pump(lambda: view._busy_count == 0)
        self.assertEqual(["new"], results)

    def test_sidebar_collapse_preserves_projects_and_can_switch_back(self) -> None:
        first = self.activate(self.first)
        self.activate(self.second)
        self.root.toggle_sidebar()
        self.assertEqual(56, self.root.sidebar.cget("width"))
        self.assertEqual("01", self.root.project_tree.item("0", "text"))
        self.root.project_tree.selection_set("0")
        self.root._select_project()
        self.assertIs(first, self.root.active_view)
        self.root.toggle_sidebar()
        self.assertEqual(240, self.root.sidebar.cget("width"))
        self.assertIn("first", self.root.project_tree.item("0", "text"))
        self.assertEqual(2, len(self.root.views))

    def test_branches_refresh_button_and_branch_selection_are_preserved(self) -> None:
        view = self.activate(self.first)
        view.branches = [BranchEntry("Local", "feature", "abc123", "origin/feature", "<", "", "", "Test", False)]
        view.render_branches()
        view.branch_tree.selection_set(view.branch_tree.get_children()[0])
        view.render_branches()
        self.assertEqual("feature", view.selected_branch().name)
        tab = view.notebook.nametowidget(view.notebook.tabs()[0])
        toolbar = tab.winfo_children()[0]
        refresh = next(widget for widget in toolbar.winfo_children() if widget.cget("text") == "Refresh branches")
        view.refresh_all.reset_mock()
        refresh.invoke()
        view.refresh_all.assert_called_once()

    def test_diff_preview_uses_colours_line_numbers_and_read_only_text(self) -> None:
        view = self.activate(self.first)
        preview = view.diff_preview
        preview.render(FileVersions("same\nold\n", "same\nnew\nextra\n", "HEAD", "Working tree"), "file.txt")
        self.assertIn("    2 | old", preview.before_text.get("1.0", tk.END))
        self.assertIn("    3 | extra", preview.after_text.get("1.0", tk.END))
        self.assertTrue(preview.before_text.tag_ranges("removed"))
        self.assertTrue(preview.after_text.tag_ranges("added"))
        self.assertEqual("disabled", preview.before_text.cget("state"))
        with patch.object(view, "show_selected_diff"):
            view.status_entries = [StatusEntry(" ", "M", "first.txt"), StatusEntry(" ", "M", "second.txt")]
            view._render_changes()
            view.change_tree.selection_set("1")
            view.status_entries.reverse()
            view._render_changes()
            self.assertEqual("0", view.change_tree.selection()[0])
            self.root.update()

    def conflict(self, working: str) -> ConflictFile:
        return ConflictFile(self.first, "file.txt", "stages", "base\n", "left\n", "right\n", working.encode("utf-8"))

    def test_adjacent_conflicts_can_choose_different_sides_without_losing_context(self) -> None:
        view = self.activate(self.first)
        working = "\U0001f600 before\n<<<<<<< HEAD\nleft one\n=======\nright one\n>>>>>>> other\n<<<<<<< HEAD\nleft two\n=======\nright two\n>>>>>>> other\nafter\n"
        editor = ConflictEditor(view, self.conflict(working), lambda: None)
        editor.accept_block("ours")
        editor.accept_block("theirs")
        self.assertEqual("\U0001f600 before\nleft one\nright two\nafter\n", editor.result_text.get("1.0", "end-1c"))
        self.assertEqual([], editor.regions)

    def test_undoing_a_block_choice_allows_choosing_the_other_side(self) -> None:
        view = self.activate(self.first)
        working = "before\n<<<<<<< HEAD\nleft\n=======\nright\n>>>>>>> other\nafter\n"
        editor = ConflictEditor(view, self.conflict(working), lambda: None)
        editor.accept_block("ours")
        editor.result_text.edit_undo()
        editor.accept_block("theirs")
        self.assertEqual("before\nright\nafter\n", editor.result_text.get("1.0", "end-1c"))

    def test_editor_save_failure_preserves_the_draft_and_reenables_saving(self) -> None:
        view = self.activate(self.first)
        editor = ConflictEditor(view, self.conflict("working\n"), lambda: None)
        editor.result_text.delete("1.0", tk.END)
        editor.result_text.insert("1.0", "manual resolution\n")
        with patch("litegit_views.save_resolution", side_effect=ValueError("Simulated concurrent edit")):
            editor.save()
            self.pump(lambda: not editor.saving)
        self.assertEqual("manual resolution\n", editor.result_text.get("1.0", "end-1c"))
        self.assertNotIn("disabled", editor.save_button.state())
        self.assertTrue(editor.winfo_exists())

    def test_editor_save_stages_the_exact_manual_result_and_closes_after_success(self) -> None:
        view = self.activate(self.first)
        saved = []
        conflict = self.conflict("working\n")
        editor = ConflictEditor(view, conflict, lambda: saved.append(True))
        editor.result_text.delete("1.0", tk.END)
        editor.result_text.insert("1.0", "resolution without final newline")
        with patch("litegit_views.save_resolution") as save:
            editor.save()
            self.pump(lambda: saved == [True])
        save.assert_called_once_with(conflict, "resolution without final newline", delete=False)
        self.assertFalse(editor.winfo_exists())

    def test_closing_unsaved_editor_requires_discard_and_preserves_it_when_declined(self) -> None:
        view = self.activate(self.first)
        editor = ConflictEditor(view, self.conflict("working\n"), lambda: None)
        editor.result_text.insert(tk.END, "unsaved")
        with patch("litegit_views.messagebox.askyesno", return_value=False):
            editor.close()
        self.assertTrue(editor.winfo_exists())
        with patch("litegit.messagebox.askyesno", return_value=False):
            self.root._close()
        self.assertTrue(self.root.winfo_exists())


if __name__ == "__main__":
    unittest.main()
