from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import litegit
from litegit_core import CommitEntry


class CommitHistoryUiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            with patch("litegit.load_settings", return_value={}):
                self.app = litegit.LiteGitApp()
            self.app.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")

    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is not None and app.winfo_exists():
            app._close()

    @staticmethod
    def _commits() -> list[CommitEntry]:
        return [
            CommitEntry("a" * 40, "aaaaaaa", "", "2026-08-21", "Tester", "", "Pushed commit"),
            CommitEntry("b" * 40, "bbbbbbb", "", "2026-08-21", "Tester", "", "Local commit"),
        ]

    def test_remote_and_local_rows_have_distinct_states_and_colours(self) -> None:
        commits = self._commits()
        self.app._render_history((commits, {commits[0].full_hash}))
        self.assertEqual("Published remotely", self.app.commit_tree.item("0", "values")[0])
        self.assertEqual(("remote",), self.app.commit_tree.item("0", "tags"))
        self.assertEqual("Local only", self.app.commit_tree.item("1", "values")[0])
        self.assertEqual(("local_only",), self.app.commit_tree.item("1", "tags"))

    def test_edit_button_is_disabled_for_remote_commit(self) -> None:
        commits = self._commits()
        self.app._render_history((commits, {commits[0].full_hash}))
        self.app.commit_tree.selection_set("0")
        self.app._on_commit_selection()
        self.assertIn("disabled", self.app.edit_message_button.state())
        self.app.commit_tree.selection_set("1")
        self.app._on_commit_selection()
        self.assertNotIn("disabled", self.app.edit_message_button.state())

    def test_refresh_clearing_selection_does_not_prompt(self) -> None:
        commits = self._commits()
        self.app._render_history((commits, set()))
        self.app.commit_tree.selection_set("0")
        with patch("litegit.messagebox.showinfo") as showinfo:
            self.app._render_history((commits, set()))
            self.app.update()
        showinfo.assert_not_called()

    def test_message_editor_has_explicit_save_and_shortcut(self) -> None:
        dialog = litegit.CommitMessageDialog(self.app, "Initial message")
        try:
            self.assertEqual("Save changes", dialog.save_button.cget("text"))
            self.assertTrue(dialog.bind("<Control-s>"))
            self.assertTrue(self.app.commit_tree.bind("<Double-1>"))
        finally:
            dialog.destroy()

    def test_merge_selected_branch_confirms_and_runs_git_merge(self) -> None:
        branch = SimpleNamespace(name="feature/merge-test")
        self.app.summary_vars["branch"].set("main")
        with patch.object(self.app, "selected_branch", return_value=branch), patch(
            "litegit.messagebox.askyesno", return_value=True
        ) as confirm, patch.object(self.app, "execute_git") as execute_git:
            self.app.merge_selected_branch()
        confirm.assert_called_once()
        execute_git.assert_called_once_with(["merge", "--no-edit", "feature/merge-test"], refresh=True)


if __name__ == "__main__":
    unittest.main()
