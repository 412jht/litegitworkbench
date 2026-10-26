from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from litegit_core import GitError, StatusEntry, parse_status_porcelain, run_git
from litegit_merge import MAX_EDITOR_LINES, _blob, _decode, _safe_file


MAX_PREVIEW_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class FileVersions:
    before: str
    after: str
    before_label: str
    after_label: str


@dataclass(frozen=True)
class DirectoryPreview:
    path: str
    changes: tuple[StatusEntry, ...]
    nested_repository: bool


@dataclass(frozen=True)
class DiffRow:
    before_number: int | None
    before: str
    after_number: int | None
    after: str
    kind: str


def aligned_diff(before: str, after: str) -> list[DiffRow]:
    left, right = before.splitlines(keepends=True), after.splitlines(keepends=True)
    rows = []
    matcher = SequenceMatcher(None, left, right, autojunk=len(left) + len(right) > 20000)
    for kind, start_left, end_left, start_right, end_right in matcher.get_opcodes():
        for offset in range(max(end_left - start_left, end_right - start_right)):
            old, new = start_left + offset, start_right + offset
            rows.append(DiffRow(
                old + 1 if old < end_left else None,
                left[old].rstrip("\r\n") if old < end_left else "",
                new + 1 if new < end_right else None,
                right[new].rstrip("\r\n") if new < end_right else "",
                kind,
            ))
    return rows


def _revision_text(repository: Path, path: str, *, staged: bool) -> str:
    if staged:
        records = run_git(repository, ["--literal-pathspecs", "ls-files", "--stage", "-z", "--", path]).output
    else:
        result = run_git(repository, ["--literal-pathspecs", "ls-tree", "-z", "HEAD", "--", path], check=False)
        if result.returncode:
            if run_git(repository, ["rev-parse", "--verify", "HEAD"], check=False).returncode:
                return ""
            raise GitError(result.command, result.returncode, result.output)
        records = result.output
    if not records:
        return ""
    metadata, _, _name = records.split("\0")[0].partition("\t")
    mode, object_type, object_id = metadata.split()
    if staged:
        object_id, stage = object_type, object_id
        if stage != "0":
            raise ValueError("This file has conflicts. Open Resolve conflicts to edit the merge result.")
    if mode not in {"100644", "100755"}:
        raise ValueError("Symbolic links and submodules do not have a text preview.")
    size = int(run_git(repository, ["cat-file", "-s", object_id]).output.strip())
    if size > MAX_PREVIEW_BYTES:
        raise ValueError("This file is too large for the inline preview (2 MiB per version).")
    return _decode(_blob(repository, object_id))


def read_file_versions(repository: Path, entry: StatusEntry, scope: str = "All changes") -> FileVersions | DirectoryPreview:
    target = _safe_file(repository, entry.path)
    if target.is_dir():
        output = run_git(repository, ["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", entry.path]).output
        changes = tuple(
            change for change in parse_status_porcelain(output)
            if change.path.rstrip("/") != entry.path.rstrip("/")
            and (scope != "Staged" or change.index not in {" ", "?", "!"})
            and (scope != "Unstaged" or change.worktree not in {" ", "!"})
        )
        return DirectoryPreview(entry.path, changes, (target / ".git").exists())
    original = entry.original_path or entry.path
    before_path = original if scope != "Unstaged" or entry.worktree in {"R", "C"} else entry.path
    before = _revision_text(repository, before_path, staged=scope == "Unstaged")
    if scope == "Staged":
        after = _revision_text(repository, entry.path, staged=True)
    else:
        if target.exists() and target.stat().st_size > MAX_PREVIEW_BYTES:
            raise ValueError("This file is too large for the inline preview (2 MiB per version).")
        after = _decode(target.read_bytes()) if target.exists() else ""
    if before.count("\n") > MAX_EDITOR_LINES or after.count("\n") > MAX_EDITOR_LINES:
        raise ValueError("This file has too many lines for the inline preview (20,000 per version). Use an external editor.")
    return FileVersions(before, after, "Index" if scope == "Unstaged" else "HEAD", "Index" if scope == "Staged" else "Working tree")
