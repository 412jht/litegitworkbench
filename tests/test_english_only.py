from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7f]")
IGNORED_DIRECTORIES = {".git", "__pycache__", "build", "release"}
TEXT_SUFFIXES = {"", ".cmd", ".json", ".md", ".py", ".sh", ".txt", ".yml", ".yaml"}


class EnglishOnlyRepositoryTests(unittest.TestCase):
    def test_tracked_source_files_contain_ascii_only_text(self) -> None:
        failures: list[str] = []
        for path in PROJECT_ROOT.rglob("*"):
            relative = path.relative_to(PROJECT_ROOT)
            if not path.is_file() or any(part in IGNORED_DIRECTORIES for part in relative.parts):
                continue
            if path.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if NON_ASCII_PATTERN.search(text):
                failures.append(relative.as_posix())
        self.assertEqual([], failures, f"Non-ASCII text found in: {', '.join(failures)}")

    def test_git_commit_messages_contain_ascii_only_text(self) -> None:
        if not (PROJECT_ROOT / ".git").exists():
            self.skipTest("Git history is unavailable")
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "log", "--format=%B"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertIsNone(NON_ASCII_PATTERN.search(result.stdout), "Non-ASCII text found in Git commit messages")


if __name__ == "__main__":
    unittest.main()
