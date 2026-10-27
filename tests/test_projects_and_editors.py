from __future__ import annotations

import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

import litegit
from litegit_core import BranchEntry, CommitEntry, StatusEntry
from litegit_diff import FileVersions
from litegit_merge import ConflictFile
from litegit_views import ConflictEditor
from ui_events import process_events


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
        directory = Path(self.temporary.name).resolve()
        self.first = directory / "first"
        self.second = directory / "second"
        self.first.mkdir()
        self.second.mkdir()

    def tearDown(self) -> None:
        root = getattr(self, "root", None)
        if root is None or not root.winfo_exists():
            return
        self.pump(lambda: all(not view._busy_count for view in root._all_views))
        for view in root._all_views:
            for child in view.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()
        root._close()

    def pump(self, condition: Callable[[], bool], timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            process_events(self.root)
            time.sleep(0.01)
        process_events(self.root)
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
            delivered: list[bool] = []
            self.root.after_idle(lambda: delivered.append(True))
            self.pump(lambda: bool(delivered))
        self.assertEqual(0, view._busy_count)

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

    def test_project_names_use_the_main_worktree_relationship(self) -> None:
        self.activate(self.first)
        self.root.remember_main_worktree(self.first, self.first)
        self.activate(self.second)
        self.root.remember_main_worktree(self.second, self.first)
        self.assertEqual("first", self.root.project_tree.item("0", "text"))
        self.assertEqual("second / first", self.root.project_tree.item("1", "text"))

    def test_full_name_and_path_tooltip_work_for_long_labels_and_collapsed_numbers(self) -> None:
        long_worktree = self.first.parent / ("long-worktree-name-" * 5)
        long_worktree.mkdir()
        self.activate(long_worktree)
        self.root.remember_main_worktree(long_worktree, self.first)
        event = SimpleNamespace(x=8, y=8, x_root=20, y_root=20)
        tip = self.root.project_tooltip
        tip.delay = 0
        for collapsed in (False, True):
            if self.root.sidebar_collapsed != collapsed:
                self.root.toggle_sidebar()
            process_events(self.root)
            with patch.object(self.root.project_tree, "identify_row", return_value="0"):
                tip._motion(event)
            self.pump(lambda: tip.window is not None)
            self.assertEqual(long_worktree.name + " / first\n" + str(long_worktree), tip.label.cget("text"))
            tip.hide()
            self.assertIsNone(tip.window)
        self.assertEqual("01", self.root.project_tree.item("0", "text"))

    def test_removing_projects_keeps_directories_drafts_and_other_sessions(self) -> None:
        first = self.activate(self.first)
        first.commit_message_var.set("Keep this draft")
        second = self.activate(self.second)
        self.root.remove_project(str(self.first))
        self.assertEqual([str(self.second)], self.root.settings["projects"])
        self.assertIs(second, self.root.active_view)
        self.assertTrue(self.first.is_dir())
        self.root.open_repository(str(self.first))
        self.assertIs(first, self.root.active_view)
        self.assertEqual("Keep this draft", first.commit_message_var.get())
        self.root.remove_project(str(self.first))
        self.assertIs(second, self.root.active_view)
        self.root.remove_project(str(self.second))
        self.assertEqual([], self.root.settings["projects"])
        self.assertEqual("", self.root.settings["last_repository"])
        self.assertIsNone(self.root.active_view.repository)
        self.assertIn("disabled", self.root.remove_project_button.state())
        self.assertTrue(self.second.is_dir())

    def test_project_context_menu_removes_the_clicked_row(self) -> None:
        first = self.activate(self.first)
        self.activate(self.second)
        event = SimpleNamespace(y=20, x_root=100, y_root=100)
        with patch.object(self.root.project_tree, "identify_row", return_value="0"), patch.object(self.root.project_menu, "tk_popup") as popup:
            self.root._on_project_context_menu(event)
            popup.assert_called_once_with(100, 100)
            self.root.project_menu.invoke("Remove from Projects")
        self.assertEqual([str(self.second)], self.root.project_paths)
        self.assertTrue(first.winfo_exists())

    @staticmethod
    def branches() -> list[BranchEntry]:
        return [
            BranchEntry("Local", "main", "a", "origin/main", "", "", "", "", True),
            BranchEntry("Local", "feature", "b", "origin/feature", "", "", "", "", False),
            BranchEntry("Remote", "origin/feature", "c", "", "", "", "", "", False),
        ]

    def test_branch_context_menu_updates_the_clicked_branch_and_handles_remote_rows(self) -> None:
        view = self.activate(self.first)
        view.branches = self.branches()
        view.render_branches()
        rows = view.branch_tree.get_children()
        view.branch_tree.selection_set(rows[0])
        event = SimpleNamespace(y=30, x_root=100, y_root=100)
        with patch.object(view.branch_tree, "identify_row", return_value=rows[1]), patch.object(view.branch_menu, "tk_popup"), patch.object(view, "update_selected_branch") as update:
            view._on_branch_context_menu(event)
            self.assertEqual("feature", view.selected_branch().name)
            view.branch_menu.invoke("Update branch from upstream")
            update.assert_called_once()
        with patch.object(view.branch_tree, "identify_row", return_value=rows[2]), patch.object(view.branch_menu, "tk_popup"):
            view._on_branch_context_menu(event)
            self.assertEqual("disabled", view.branch_menu.entrycget("Update branch from upstream", "state"))
            self.assertEqual("normal", view.branch_menu.entrycget("Delete remote branch...", "state"))

    def test_branch_menu_ignores_blank_space_and_supports_the_keyboard(self) -> None:
        view = self.activate(self.first)
        view.branches = self.branches()
        view.render_branches()
        view.branch_tree.selection_set(view.branch_tree.get_children()[1])
        with patch.object(view.branch_tree, "identify_row", return_value=""), patch.object(view.branch_menu, "tk_popup") as popup:
            view._on_branch_context_menu(SimpleNamespace(y=1000, x_root=0, y_root=0))
            popup.assert_not_called()
        with patch.object(view.branch_tree, "bbox", return_value=(0, 25, 400, 25)), patch.object(view.branch_menu, "tk_popup") as popup:
            view._on_branch_context_menu(SimpleNamespace(keysym="F10"))
            popup.assert_called_once()
            self.assertEqual("feature", view.selected_branch().name)

    def test_history_menu_uses_the_clicked_commit_and_protects_published_messages(self) -> None:
        view = self.activate(self.first)
        commits = [
            CommitEntry("a" * 40, "aaaaaaa", "", "", "Tester", "", "Published"),
            CommitEntry("b" * 40, "bbbbbbb", "", "", "Tester", "", "Local"),
        ]
        view._render_history((commits, {commits[0].full_hash}))
        event = SimpleNamespace(y=25, x_root=100, y_root=100)
        with patch.object(view, "show_commit"), patch.object(view.commit_menu, "tk_popup"):
            with patch.object(view.commit_tree, "identify_row", return_value="0"), patch.object(view, "edit_commit_message") as edit:
                view._on_commit_context_menu(event)
                self.assertEqual("disabled", view.commit_menu.entrycget("Edit message...", "state"))
                view.commit_menu.invoke("Edit message...")
                edit.assert_not_called()
            with patch.object(view.commit_tree, "identify_row", return_value="1"), patch("litegit.messagebox.askyesno", return_value=True), patch.object(view, "execute_git") as execute:
                view._on_commit_context_menu(event)
                self.assertEqual("normal", view.commit_menu.entrycget("Edit message...", "state"))
                view.commit_menu.invoke("Copy hash")
                self.assertEqual(commits[1].full_hash, self.root.clipboard_get())
                view.commit_menu.invoke("Cherry-pick...")
                execute.assert_called_once_with(["cherry-pick", commits[1].full_hash], refresh=True)
            process_events(self.root)

    def test_branch_groups_span_the_tab_and_compare_starts_at_the_top(self) -> None:
        view = self.activate(self.first)
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("1100x680")
        self.root.deiconify()
        process_events(self.root)
        groups = list(view.branch_groups.values())
        self.pump(lambda: groups[0].winfo_x() < groups[1].winfo_x() < groups[2].winfo_x())
        self.assertLess(groups[0].winfo_x(), groups[1].winfo_x())
        self.assertLess(groups[1].winfo_x(), groups[2].winfo_x())
        self.assertLessEqual(groups[2].master.winfo_width() - groups[2].winfo_x() - groups[2].winfo_width(), 2)
        view.notebook.select(2)
        self.pump(lambda: view.compare_toolbar.winfo_ismapped())
        self.assertLessEqual(view.compare_toolbar.winfo_rooty() - view.changes_tab.winfo_rooty(), 8)
        self.root.withdraw()

    def test_preview_read_errors_are_shown_inline_and_do_not_block_the_next_file(self) -> None:
        view = self.activate(self.first)
        view.status_entries = [StatusEntry("?", "?", "folder/")]
        view._render_changes()
        view.change_tree.selection_set("0")
        with patch("litegit.read_file_versions", side_effect=PermissionError("Permission denied")), patch("litegit.messagebox.showerror") as error:
            view.show_selected_diff()
            self.pump(lambda: view._busy_count == 0)
            self.assertIn("Permission denied", view.diff_preview.caption.get())
            error.assert_not_called()
        with patch("litegit.read_file_versions", return_value=FileVersions("old\n", "new\n", "HEAD", "Working tree")):
            view.show_selected_diff()
            self.pump(lambda: view._busy_count == 0)
            self.assertIn("new", view.diff_preview.after_text.get("1.0", tk.END))


if __name__ == "__main__":
    unittest.main()
