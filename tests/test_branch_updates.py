from __future__ import annotations

import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from litegit_core import GitError, StatusEntry, run_git
from litegit_diff import DirectoryPreview, aligned_diff, read_file_versions
from litegit_merge import abort_update, conflict_regions, finish_update, has_conflict_markers, pending_updates, read_conflict, save_resolution, unresolved_paths, update_branch
from ui_events import process_events


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="litegit-210-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "LiteGit Test")
        self.git("config", "user.email", "test@example.test")
        self.git("config", "core.autocrlf", "false")
        (self.repository / "file.txt").write_text("base\n", encoding="utf-8", newline="\n")
        self.commit("Initial")

    def git(self, *arguments: str) -> str:
        return run_git(self.repository, arguments).output.strip()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def remote(self, *, conflicting: bool = False) -> str:
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        self.git("branch", "target")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "--set-upstream", "origin", "target")
        self.git("switch", "-c", "incoming")
        (self.repository / ("file.txt" if conflicting else "incoming.txt")).write_text("incoming\n", encoding="utf-8", newline="\n")
        incoming = self.commit("Incoming")
        self.git("push", "origin", "HEAD:target")
        self.git("switch", "main")
        return incoming

    def diverge(self, *, conflicting: bool = False) -> tuple[str, str]:
        incoming = self.remote(conflicting=conflicting)
        self.git("switch", "target")
        (self.repository / ("file.txt" if conflicting else "local.txt")).write_text("local\n", encoding="utf-8", newline="\n")
        original = self.commit("Local")
        self.git("switch", "main")
        return original, incoming

    def dirty_snapshot(self) -> tuple[str, bytes, bytes, bytes]:
        (self.repository / "file.txt").write_bytes(b"staged\r\n")
        self.git("add", "file.txt")
        (self.repository / "file.txt").write_bytes(b"unstaged\r\n")
        (self.repository / "untracked.txt").write_bytes(b"untracked\n")
        return self.snapshot()

    def snapshot(self) -> tuple[str, bytes, bytes, bytes]:
        return (
            self.git("rev-parse", "HEAD"),
            (self.repository / ".git" / "index").read_bytes(),
            (self.repository / "file.txt").read_bytes(),
            (self.repository / "untracked.txt").read_bytes(),
        )

    def test_fast_forward_preserves_head_index_and_dirty_files(self) -> None:
        incoming = self.remote()
        snapshot = self.dirty_snapshot()
        result = update_branch(self.repository, "target")
        self.assertEqual("Fast-forwarded", result.outcome)
        self.assertEqual(incoming, self.git("rev-parse", "target"))
        self.assertEqual(snapshot, self.snapshot())
        self.assertEqual("main", self.git("branch", "--show-current"))
        self.assertEqual([], pending_updates(self.repository))
        self.assertEqual("", self.git("for-each-ref", "refs/litegit"))
        self.assertEqual("Already up to date", update_branch(self.repository, "target").outcome)

    def test_diverged_non_conflicting_branch_is_merged_in_isolation(self) -> None:
        original, incoming = self.diverge()
        snapshot = self.dirty_snapshot()
        result = update_branch(self.repository, "target")
        self.assertEqual("Merged and updated", result.outcome)
        self.assertEqual(f"{original} {incoming}", self.git("show", "-s", "--format=%P", "target"))
        self.assertEqual(snapshot, self.snapshot())
        self.assertEqual([], pending_updates(self.repository))
        self.assertEqual(1, self.git("worktree", "list").count(str(self.repository).replace("\\", "/")))

    def test_conflict_recovery_resolution_and_publish_preserve_current_work(self) -> None:
        original, incoming = self.diverge(conflicting=True)
        snapshot = self.dirty_snapshot()
        result = update_branch(self.repository, "target")
        self.assertIsNotNone(result.pending)
        self.assertEqual(original, self.git("rev-parse", "target"))
        recovered = pending_updates(self.repository)[0]
        worktree = Path(recovered.worktree)
        self.assertEqual(["file.txt"], unresolved_paths(worktree))
        conflict = read_conflict(worktree, "file.txt")
        self.assertEqual(("base\n", "local\n", "incoming\n"), (conflict.base, conflict.ours, conflict.theirs))
        with self.assertRaises(ValueError):
            finish_update(recovered)
        with self.assertRaises(ValueError):
            save_resolution(conflict, conflict.result)
        save_resolution(conflict, "local and incoming\n")
        self.assertEqual([], unresolved_paths(worktree))
        finish_update(pending_updates(self.repository)[0])
        self.assertEqual(f"{original} {incoming}", self.git("show", "-s", "--format=%P", "target"))
        self.assertEqual("local and incoming", self.git("show", "target:file.txt"))
        self.assertEqual(snapshot, self.snapshot())
        self.assertFalse(worktree.exists())
        self.assertEqual([], pending_updates(self.repository))

    def test_aborting_background_merge_keeps_original_branch_and_dirty_work(self) -> None:
        original, _incoming = self.diverge(conflicting=True)
        snapshot = self.dirty_snapshot()
        result = update_branch(self.repository, "target")
        abort_update(result.pending)
        self.assertEqual(original, self.git("rev-parse", "target"))
        self.assertEqual(snapshot, self.snapshot())
        self.assertEqual([], pending_updates(self.repository))

    def test_ref_update_uses_compare_and_swap_and_keeps_recovery(self) -> None:
        original, incoming = self.diverge(conflicting=True)
        result = update_branch(self.repository, "target")
        conflict = read_conflict(Path(result.pending.worktree), "file.txt")
        save_resolution(conflict, "resolved\n")
        self.git("update-ref", "refs/heads/target", incoming, original)
        with self.assertRaises(GitError):
            finish_update(result.pending)
        self.assertEqual(incoming, self.git("rev-parse", "target"))
        self.assertEqual(1, len(pending_updates(self.repository)))
        abort_update(result.pending)

    def test_checked_out_branches_and_missing_upstream_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "checked out"):
            update_branch(self.repository, "main")
        self.git("branch", "untracked-branch")
        with self.assertRaisesRegex(ValueError, "upstream"):
            update_branch(self.repository, "untracked-branch")
        linked = self.root / "linked"
        self.git("worktree", "add", str(linked), "untracked-branch")
        try:
            with self.assertRaisesRegex(ValueError, "checked out"):
                update_branch(self.repository, "untracked-branch")
        finally:
            self.git("worktree", "remove", str(linked))

    def test_failed_fetch_and_failed_fast_forward_preserve_branch(self) -> None:
        incoming = self.remote()
        original = self.git("rev-parse", "target")
        self.git("config", "branch.target.merge", "refs/heads/missing")
        with self.assertRaises(GitError):
            update_branch(self.repository, "target")
        self.assertEqual(original, self.git("rev-parse", "target"))
        self.git("config", "branch.target.merge", "refs/heads/target")
        real_git = run_git

        def fail_publish(repository: Path, arguments: list[str], **kwargs: object) -> object:
            if arguments[:2] == ["update-ref", "-m"]:
                raise GitError(arguments, 1, "Simulated ref lock")
            return real_git(repository, arguments, **kwargs)

        with patch("litegit_merge.run_git", side_effect=fail_publish):
            with self.assertRaises(GitError):
                update_branch(self.repository, "target")
        self.assertEqual(original, self.git("rev-parse", "target"))
        finish_update(pending_updates(self.repository)[0])
        self.assertEqual(incoming, self.git("rev-parse", "target"))

    def make_conflict(self, *, delete: bool = False, filename: str = "file.txt") -> None:
        if filename != "file.txt":
            self.git("mv", "file.txt", filename)
            self.commit("Rename file")
        self.git("switch", "-c", "other")
        (self.repository / filename).write_bytes(b"theirs\r\n")
        self.commit("Other")
        self.git("switch", "main")
        if delete:
            self.git("rm", "--", filename)
        else:
            (self.repository / filename).write_bytes(b"ours\r\n")
        self.commit("Ours")
        result = run_git(self.repository, ["merge", "--no-edit", "other"], check=False)
        self.assertNotEqual(0, result.returncode)

    def test_resolution_detects_external_edits_and_preserves_crlf(self) -> None:
        self.make_conflict()
        conflict = read_conflict(self.repository, "file.txt")
        (self.repository / "file.txt").write_text("external edit\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            save_resolution(conflict, "my result\n")
        self.assertEqual("external edit\n", (self.repository / "file.txt").read_text())
        (self.repository / "file.txt").write_bytes(conflict.working)
        save_resolution(conflict, "resolved\n")
        self.assertEqual(b"resolved\r\n", (self.repository / "file.txt").read_bytes())

    def test_literal_filename_and_stage_failure_keep_original_content(self) -> None:
        filename = "file[1] space.txt"
        self.make_conflict(filename=filename)
        conflict = read_conflict(self.repository, filename)
        lock = self.repository / ".git" / "index.lock"
        lock.touch()
        try:
            with self.assertRaises(GitError):
                save_resolution(conflict, "resolved\n")
            self.assertEqual(conflict.working, (self.repository / filename).read_bytes())
        finally:
            lock.unlink()
        save_resolution(conflict, "resolved\n")
        self.assertEqual([], unresolved_paths(self.repository))

    def test_modify_delete_resolution_can_preserve_the_deletion(self) -> None:
        self.make_conflict(delete=True)
        conflict = read_conflict(self.repository, "file.txt")
        self.assertIsNone(conflict.ours)
        self.assertIsNotNone(conflict.theirs)
        save_resolution(conflict, "", delete=True)
        self.assertFalse((self.repository / "file.txt").exists())
        self.assertEqual([], unresolved_paths(self.repository))

    def test_binary_conflict_is_rejected_without_modifying_bytes(self) -> None:
        self.make_conflict()
        target = self.repository / "file.txt"
        target.write_bytes(b"binary\0data")
        with self.assertRaisesRegex(ValueError, "Binary"):
            read_conflict(self.repository, "file.txt")
        self.assertEqual(b"binary\0data", target.read_bytes())

    def test_diff_views_include_both_staged_and_unstaged_content(self) -> None:
        target = self.repository / "file.txt"
        target.write_text("staged\n", encoding="utf-8", newline="\n")
        self.git("add", "file.txt")
        target.write_text("working\n", encoding="utf-8", newline="\n")
        entry = StatusEntry("M", "M", "file.txt")
        all_changes = read_file_versions(self.repository, entry)
        staged = read_file_versions(self.repository, entry, "Staged")
        unstaged = read_file_versions(self.repository, entry, "Unstaged")
        self.assertEqual(("base\n", "working\n"), (all_changes.before, all_changes.after))
        self.assertEqual(("base\n", "staged\n"), (staged.before, staged.after))
        self.assertEqual(("staged\n", "working\n"), (unstaged.before, unstaged.after))
        target.write_bytes(b"line\n" * 20001)
        with self.assertRaisesRegex(ValueError, "too many lines"):
            read_file_versions(self.repository, entry)

    def test_diff_handles_renames_deleted_and_untracked_files(self) -> None:
        self.git("mv", "file.txt", "renamed[1].txt")
        versions = read_file_versions(self.repository, StatusEntry("R", " ", "renamed[1].txt", "file.txt"))
        self.assertEqual(("base\n", "base\n"), (versions.before, versions.after))
        (self.repository / "renamed[1].txt").unlink()
        versions = read_file_versions(self.repository, StatusEntry("R", "D", "renamed[1].txt", "file.txt"))
        self.assertEqual(("base\n", ""), (versions.before, versions.after))
        (self.repository / "new.txt").write_text("new\n", encoding="utf-8", newline="\n")
        versions = read_file_versions(self.repository, StatusEntry("?", "?", "new.txt"))
        self.assertEqual(("", "new\n"), (versions.before, versions.after))

    def test_directory_previews_list_changed_files_without_reading_a_directory_as_bytes(self) -> None:
        directory = self.repository / "folder"
        directory.mkdir()
        (directory / "file[1].txt").write_text("first\n", encoding="utf-8")
        (directory / "second.txt").write_text("second\n", encoding="utf-8")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("A directory must not be read as a file")):
            preview = read_file_versions(self.repository, StatusEntry("?", "?", "folder/"))
        self.assertIsInstance(preview, DirectoryPreview)
        self.assertEqual({"folder/file[1].txt", "folder/second.txt"}, {entry.path for entry in preview.changes})
        self.assertFalse(preview.nested_repository)
        self.assertEqual((), read_file_versions(self.repository, StatusEntry("?", "?", "folder/"), "Staged").changes)

    def test_repository_refresh_lists_individual_untracked_files_inside_folders(self) -> None:
        from litegit import RepositoryView

        folder = self.repository / "folder"
        folder.mkdir()
        (folder / "first.txt").write_text("first\n", encoding="utf-8")
        (folder / "second.txt").write_text("second\n", encoding="utf-8")
        state = RepositoryView._collect_repository_state(type("State", (), {"repository": self.repository})())
        self.assertEqual({"folder/first.txt", "folder/second.txt"}, {entry.path for entry in state["status"]})

    def ui(self) -> object:
        import litegit

        for replacement in (
            patch("litegit.load_settings", return_value={}), patch("litegit.save_settings"),
            patch.object(litegit.RepositoryView, "refresh_all"),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        try:
            root = litegit.LiteGitApp()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root._close)
        root._activate_repository(self.repository)
        return root

    def pump(self, root: object) -> None:
        deadline = time.monotonic() + 60
        while root.active_view._busy_count and time.monotonic() < deadline:
            process_events(root)
            time.sleep(0.01)
        process_events(root)
        self.assertEqual(0, root.active_view._busy_count, "The repository operation timed out")

    def test_background_update_conflict_can_be_completed_through_the_actual_ui(self) -> None:
        from litegit_core import BranchEntry

        root = self.ui()
        self.diverge(conflicting=True)
        snapshot = self.dirty_snapshot()
        view = root.active_view
        branch = BranchEntry("Local", "target", "", "origin/target", "", "", "", "", False)
        with patch.object(view, "selected_branch", return_value=branch), patch("litegit.messagebox.askyesno", return_value=True), patch("litegit.messagebox.showerror") as error:
            view.update_selected_branch()
            self.pump(root)
            manager = view._conflict_manager
            self.assertIsNotNone(manager)
            self.assertIsNotNone(manager._selected()[2])
            manager.edit_selected()
            self.pump(root)
            editor = next(iter(manager.editors.values()))
            editor.accept_block("both")
            editor.save()
            self.pump(root)
            self.assertEqual([], manager._selected()[3])
            manager.complete()
            self.pump(root)
            error.assert_not_called()
        self.assertEqual("local\nincoming", self.git("show", "target:file.txt"))
        self.assertEqual(snapshot, self.snapshot())
        self.assertEqual([], pending_updates(self.repository))

    def test_current_merge_conflicts_open_the_editor_workflow_instead_of_an_error(self) -> None:
        root = self.ui()
        self.make_conflict()
        self.git("merge", "--abort")
        view = root.active_view
        with patch("litegit.messagebox.showerror") as error:
            view.execute_git(["merge", "--no-edit", "other"], refresh=True)
            self.pump(root)
            self.assertIsNotNone(view._conflict_manager)
            self.assertEqual(["file.txt"], view._conflict_manager._selected()[3])
            error.assert_not_called()

    def test_repository_smoke_opens_refreshes_and_reads_history_without_user_settings(self) -> None:
        from litegit import repository_smoke_test

        original = self.git("rev-parse", "HEAD")
        alternate = self.root / "alternate repository"
        subprocess.run(["git", "clone", "--local", str(self.repository), str(alternate)], check=True, capture_output=True)
        with patch("litegit.load_settings") as load, patch("litegit.save_settings") as save:
            try:
                report = repository_smoke_test(self.repository, alternate_repository=alternate)
            except tk.TclError as exc:
                self.skipTest(f"Tk is unavailable: {exc}")
            load.assert_not_called()
            save.assert_not_called()
        self.assertEqual("passed", report["status"])
        self.assertEqual(2, report["refreshes"])
        self.assertIn(report["mode"], {"interface", "headless"})
        self.assertEqual("main", report["branch"])
        if report["mode"] == "interface":
            self.assertEqual("passed", report["interactions"]["status"])
            self.assertEqual(20, report["interactions"]["sidebar_toggles"])
            self.assertEqual(20, report["interactions"]["project_switches"])
            self.assertTrue(report["interactions"]["drafts_preserved"])
        self.assertEqual(original, self.git("rev-parse", "HEAD"))
        self.assertEqual("", self.git("status", "--porcelain"))


class MergeAndDiffParserTests(unittest.TestCase):
    def test_diff3_regions_and_non_conflicting_context_are_preserved(self) -> None:
        text = "before\n<<<<<<< HEAD\nleft\n||||||| base\nold\n=======\nright\n>>>>>>> other\nafter\n"
        regions = conflict_regions(text)
        self.assertEqual(1, len(regions))
        self.assertEqual(("left\n", "right\n"), (regions[0].ours, regions[0].theirs))
        self.assertEqual("before\n", text[:regions[0].start])
        self.assertEqual("after\n", text[regions[0].end:])
        self.assertTrue(has_conflict_markers(text))
        self.assertFalse(has_conflict_markers("before\nleft\nright\nafter\n"))
        self.assertTrue(has_conflict_markers(text.replace("\n", "\r\n")))
        self.assertFalse(has_conflict_markers("Heading\n=======\n"))

    def test_side_by_side_alignment_keeps_line_numbers(self) -> None:
        rows = aligned_diff("same\nold\nlast\n", "same\nnew\nextra\nlast\n")
        self.assertEqual(["equal", "replace", "replace", "equal"], [row.kind for row in rows])
        self.assertEqual((None, 3), (rows[2].before_number, rows[2].after_number))
        self.assertEqual((3, 4), (rows[3].before_number, rows[3].after_number))


if __name__ == "__main__":
    unittest.main()
