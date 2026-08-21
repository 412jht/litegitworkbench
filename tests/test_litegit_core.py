from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import litegit
from litegit_core import (
    FIELD_SEPARATOR,
    GitError,
    branch_format,
    build_git_environment,
    discover_repository,
    find_remote_reachable_commits,
    is_potentially_destructive,
    is_commit_message_editable,
    parse_branches,
    parse_remotes,
    parse_status_porcelain,
    parse_worktree_porcelain,
    redact_text,
    run_git,
    split_command_line,
)
from litegit_reword import rewrite_sequence


class ParserTests(unittest.TestCase):
    def test_status_parser_handles_regular_untracked_and_rename_entries(self) -> None:
        output = " M tracked.txt\0?? new file.txt\0R  new-name.txt\0old-name.txt\0"
        entries = parse_status_porcelain(output)
        self.assertEqual(3, len(entries))
        self.assertEqual((" ", "M", "tracked.txt"), (entries[0].index, entries[0].worktree, entries[0].path))
        self.assertEqual("Untracked", entries[1].state)
        self.assertEqual("old-name.txt", entries[2].original_path)

    def test_worktree_parser_handles_branch_detached_and_flags(self) -> None:
        output = (
            "worktree C:/repo\nHEAD abcdef\nbranch refs/heads/main\n\n"
            "worktree C:/repo-task\nHEAD 123456\ndetached\nlocked maintenance\nprunable missing\n"
        )
        entries = parse_worktree_porcelain(output)
        self.assertEqual("main", entries[0].branch)
        self.assertFalse(entries[0].detached)
        self.assertTrue(entries[1].detached)
        self.assertEqual("maintenance", entries[1].locked)
        self.assertEqual("missing", entries[1].prunable)

    def test_branch_parser(self) -> None:
        line = FIELD_SEPARATOR.join(
            ("refs/heads/main", "abc1234", "origin/main", "=", "2026-08-21 10:00:00 +0800", "A User", "Initial commit", "*", "main")
        )
        branch = parse_branches(line)[0]
        self.assertEqual("Local", branch.kind)
        self.assertEqual("main", branch.name)
        self.assertTrue(branch.current)

    def test_branch_parser_maps_single_pass_divergence(self) -> None:
        line = FIELD_SEPARATOR.join(
            ("refs/heads/feature", "def5678", "", "", "2026-08-21 10:00:00 +0800", "A User", "Feature", "", "feature", "3 7")
        )
        branch = parse_branches(line)[0]
        self.assertEqual("7", branch.head_only)
        self.assertEqual("3", branch.branch_only)

    def test_remote_parser_coalesces_fetch_and_push(self) -> None:
        output = "origin\thttps://example.test/repo.git (fetch)\norigin\tssh://example.test/repo.git (push)\n"
        remotes = parse_remotes(output)
        self.assertEqual(1, len(remotes))
        self.assertEqual("https://example.test/repo.git", remotes[0].fetch_url)
        self.assertEqual("ssh://example.test/repo.git", remotes[0].push_url)

    def test_dangerous_command_detection(self) -> None:
        for command in (
            ["reset", "--hard", "HEAD~1"],
            ["clean", "-fd"],
            ["push", "--force", "origin", "main"],
            ["worktree", "remove", "somewhere"],
            ["stash", "drop", "stash@{0}"],
        ):
            self.assertTrue(is_potentially_destructive(command), command)
        self.assertFalse(is_potentially_destructive(["push", "--force-with-lease"]))
        self.assertFalse(is_potentially_destructive(["status", "--short"]))

    def test_command_line_parser(self) -> None:
        self.assertEqual(["log", "--oneline", "feature branch"], split_command_line('log --oneline "feature branch"'))

    def test_url_credentials_are_redacted(self) -> None:
        self.assertEqual("https://***@example.test/repo.git", redact_text("https://user:secret@example.test/repo.git"))
        self.assertEqual("https://example.test/repo?token=***&x=1", redact_text("https://example.test/repo?token=secret&x=1"))

    def test_git_environment_enables_gui_askpass_for_encrypted_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            askpass = Path(directory) / "git-askpass.exe"
            askpass.touch()
            environment = build_git_environment({}, askpass_path=askpass)
        self.assertEqual(str(askpass), environment["GIT_ASKPASS"])
        self.assertEqual(str(askpass), environment["SSH_ASKPASS"])
        self.assertEqual("force", environment["SSH_ASKPASS_REQUIRE"])
        self.assertEqual("0", environment["GIT_TERMINAL_PROMPT"])
        self.assertNotIn("passphrase", " ".join(environment).casefold())

    def test_invalid_preconfigured_askpass_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            askpass = Path(directory) / "git-askpass.exe"
            askpass.touch()
            environment = build_git_environment(
                {"GIT_ASKPASS": str(Path(directory) / "missing.exe"), "SSH_ASKPASS_REQUIRE": "never"},
                askpass_path=askpass,
            )
        self.assertEqual(str(askpass), environment["GIT_ASKPASS"])
        self.assertEqual("force", environment["SSH_ASKPASS_REQUIRE"])

    def test_reword_helper_marks_the_selected_commit_only(self) -> None:
        original = "pick abc1234 First\npick def5678 Second\n"
        rewritten = rewrite_sequence(original, "def5678")
        self.assertEqual("pick abc1234 First\nreword def5678 Second\n", rewritten)

    def test_remote_commits_are_not_message_editable(self) -> None:
        remote_hashes = {"remote-hash"}
        self.assertFalse(is_commit_message_editable("remote-hash", remote_hashes))
        self.assertTrue(is_commit_message_editable("local-hash", remote_hashes))

    def test_frozen_reword_helper_reinvokes_the_packaged_executable(self) -> None:
        with patch.object(litegit.sys, "frozen", True, create=True), patch.object(
            litegit.sys, "executable", r"C:\Program Files\LiteGit\LiteGitWorkbench.exe"
        ):
            environment = litegit.build_reword_environment("abc123", "New message")
        self.assertIn("LiteGitWorkbench.exe", environment["GIT_SEQUENCE_EDITOR"])
        self.assertIn("--reword-helper sequence", environment["GIT_SEQUENCE_EDITOR"])
        self.assertNotIn("litegit.py", environment["GIT_SEQUENCE_EDITOR"])


@unittest.skipUnless(subprocess.run(["git", "--version"], capture_output=True).returncode == 0, "Git is required")
class GitIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "LiteGit Test")
        self._git("config", "user.email", "litegit@example.test")
        (self.repository / "README.md").write_text("initial\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "Initial commit")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_discovers_repository_from_nested_directory(self) -> None:
        nested = self.repository / "one" / "two"
        nested.mkdir(parents=True)
        self.assertEqual(self.repository.resolve(), discover_repository(nested))

    def test_reads_branches_status_and_worktrees(self) -> None:
        self._git("branch", "feature/test")
        (self.repository / "README.md").write_text("changed\n", encoding="utf-8")
        branches = parse_branches(
            run_git(self.repository, ["for-each-ref", f"--format={branch_format()}", "refs/heads"]).output
        )
        status = parse_status_porcelain(run_git(self.repository, ["status", "--porcelain=v1", "-z"]).output)
        worktrees = parse_worktree_porcelain(run_git(self.repository, ["worktree", "list", "--porcelain"]).output)
        self.assertEqual({"main", "feature/test"}, {branch.name for branch in branches})
        self.assertEqual("README.md", status[0].path)
        self.assertEqual(self.repository.resolve(), Path(worktrees[0].path).resolve())

    def test_non_zero_exit_raises_git_error(self) -> None:
        with self.assertRaises(GitError):
            run_git(self.repository, ["rev-parse", "definitely-not-a-revision"])

    def test_creates_and_reads_a_secondary_worktree(self) -> None:
        worktree_path = Path(self.temporary_directory.name) / "feature-worktree"
        run_git(self.repository, ["worktree", "add", "-b", "feature/worktree", str(worktree_path)])
        entries = parse_worktree_porcelain(run_git(self.repository, ["worktree", "list", "--porcelain"]).output)
        self.assertEqual(2, len(entries))
        secondary = next(entry for entry in entries if entry.branch == "feature/worktree")
        self.assertEqual(worktree_path.resolve(), Path(secondary.path).resolve())
        run_git(self.repository, ["worktree", "remove", str(worktree_path)])

    def test_merges_a_selected_branch_into_the_current_branch(self) -> None:
        run_git(self.repository, ["switch", "-c", "feature/merge-test"])
        feature_file = self.repository / "feature.txt"
        feature_file.write_text("feature\n", encoding="utf-8")
        run_git(self.repository, ["add", "feature.txt"])
        run_git(self.repository, ["commit", "-m", "Add feature file"])
        feature_head = run_git(self.repository, ["rev-parse", "HEAD"]).output.strip()
        run_git(self.repository, ["switch", "main"])

        result = run_git(self.repository, ["merge", "--no-edit", "feature/merge-test"])

        self.assertEqual(0, result.returncode)
        self.assertEqual(feature_head, run_git(self.repository, ["rev-parse", "HEAD"]).output.strip())
        self.assertEqual("feature\n", feature_file.read_text(encoding="utf-8"))

    def test_pushes_to_a_local_bare_remote(self) -> None:
        remote = Path(self.temporary_directory.name) / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        run_git(self.repository, ["remote", "add", "origin", str(remote)])
        result = run_git(self.repository, ["push", "--set-upstream", "origin", "main"])
        self.assertEqual(0, result.returncode)
        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        self.assertEqual(run_git(self.repository, ["rev-parse", "HEAD"]).output.strip(), remote_head)

    def test_classifies_remote_and_local_only_commits(self) -> None:
        remote = Path(self.temporary_directory.name) / "classification-remote.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        run_git(self.repository, ["remote", "add", "origin", str(remote)])
        run_git(self.repository, ["push", "--set-upstream", "origin", "main"])
        pushed = run_git(self.repository, ["rev-parse", "HEAD"]).output.strip()
        local_file = self.repository / "local-only.txt"
        local_file.write_text("local only\n", encoding="utf-8")
        run_git(self.repository, ["add", "local-only.txt"])
        run_git(self.repository, ["commit", "-m", "Local only commit"])
        local_only = run_git(self.repository, ["rev-parse", "HEAD"]).output.strip()
        reachable = find_remote_reachable_commits(self.repository, [local_only, pushed])
        self.assertEqual({pushed}, reachable)

    def test_amend_message_only_preserves_staged_changes(self) -> None:
        previous_tree = run_git(self.repository, ["rev-parse", "HEAD^{tree}"]).output.strip()
        (self.repository / "README.md").write_text("staged but not amended\n", encoding="utf-8")
        run_git(self.repository, ["add", "README.md"])
        run_git(self.repository, ["commit", "--amend", "--only", "-m", "Updated initial message"])
        self.assertEqual("Updated initial message", run_git(self.repository, ["show", "-s", "--format=%s", "HEAD"]).output.strip())
        self.assertEqual(previous_tree, run_git(self.repository, ["rev-parse", "HEAD^{tree}"]).output.strip())
        self.assertEqual("M  README.md", run_git(self.repository, ["status", "--porcelain"]).output.strip())

    def test_rewords_a_historical_commit_with_interactive_rebase(self) -> None:
        second = self.repository / "second.txt"
        second.write_text("second\n", encoding="utf-8")
        run_git(self.repository, ["add", "second.txt"])
        run_git(self.repository, ["commit", "-m", "Original second message"])
        target = run_git(self.repository, ["rev-parse", "HEAD"]).output.strip()
        third = self.repository / "third.txt"
        third.write_text("third\n", encoding="utf-8")
        run_git(self.repository, ["add", "third.txt"])
        run_git(self.repository, ["commit", "-m", "Third message"])
        old_head = run_git(self.repository, ["rev-parse", "HEAD"]).output.strip()
        environment = litegit.build_reword_environment(
            target,
            "Reworded second message\n\nDetailed body.",
        )
        run_git(
            self.repository,
            ["rebase", "--interactive", "--rebase-merges", "--autostash", f"{target}^"],
            environment_overrides=environment,
        )
        subjects = run_git(self.repository, ["log", "--format=%s", "-3"]).output.splitlines()
        self.assertEqual(["Third message", "Reworded second message", "Initial commit"], subjects)
        self.assertNotEqual(old_head, run_git(self.repository, ["rev-parse", "HEAD"]).output.strip())
        body = run_git(self.repository, ["show", "-s", "--format=%B", "HEAD^"]).output.strip()
        self.assertEqual("Reworded second message\n\nDetailed body.", body)


if __name__ == "__main__":
    unittest.main()
