from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


FIELD_SEPARATOR = "\x1f"


class GitError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, output: str) -> None:
        self.command = tuple(command)
        self.returncode = returncode
        self.output = output
        super().__init__(output.strip() or f"Git exited with status {returncode}")


@dataclass(frozen=True)
class GitResult:
    command: tuple[str, ...]
    returncode: int
    output: str


@dataclass(frozen=True)
class StatusEntry:
    index: str
    worktree: str
    path: str
    original_path: str = ""

    @property
    def state(self) -> str:
        if self.index == "?" and self.worktree == "?":
            return "Untracked"
        if self.index == "!" and self.worktree == "!":
            return "Ignored"
        labels = {
            "M": "Modified",
            "A": "Added",
            "D": "Deleted",
            "R": "Renamed",
            "C": "Copied",
            "U": "Conflicted",
            "T": "Type changed",
        }
        parts: list[str] = []
        if self.index != " ":
            parts.append(f"Staged {labels.get(self.index, self.index)}")
        if self.worktree != " ":
            parts.append(f"Unstaged {labels.get(self.worktree, self.worktree)}")
        return "; ".join(parts) or "Unchanged"


@dataclass(frozen=True)
class WorktreeEntry:
    path: str
    head: str
    branch: str
    detached: bool
    locked: str
    prunable: str


@dataclass(frozen=True)
class BranchEntry:
    kind: str
    name: str
    short_hash: str
    upstream: str
    tracking: str
    date: str
    author: str
    subject: str
    current: bool
    head_only: str = "-"
    branch_only: str = "-"


@dataclass(frozen=True)
class CommitEntry:
    full_hash: str
    short_hash: str
    parents: str
    date: str
    author: str
    decorations: str
    subject: str


@dataclass(frozen=True)
class StashEntry:
    reference: str
    date: str
    subject: str


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    fetch_url: str
    push_url: str


def display_command(arguments: Sequence[str]) -> str:
    return subprocess.list2cmdline([redact_text(argument) for argument in arguments])


def redact_text(value: str) -> str:
    value = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1***@", value)
    value = re.sub(
        r"(?i)([?&](?:access_token|token|password|secret|key)=)[^&\s]+",
        r"\1***",
        value,
    )
    return value


def run_git(
    repository: str | os.PathLike[str],
    arguments: Sequence[str],
    *,
    check: bool = True,
    timeout: int = 180,
    environment_overrides: Mapping[str, str] | None = None,
) -> GitResult:
    command = (
        "git",
        "-C",
        str(Path(repository)),
        "--no-pager",
        "-c",
        "color.ui=false",
        "-c",
        "core.quotepath=false",
        *arguments,
    )
    environment = build_git_environment()
    if environment_overrides:
        environment.update(environment_overrides)
    try:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise GitError(command, 127, "Git was not found. Install Git and ensure that git is available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        raise GitError(command, 124, f"The Git command exceeded {timeout} seconds.\n{output}") from exc
    result = GitResult(command, process.returncode, process.stdout)
    if check and result.returncode:
        raise GitError(result.command, result.returncode, result.output)
    return result


def find_git_askpass() -> Path | None:
    configured = os.environ.get("GIT_ASKPASS") or os.environ.get("SSH_ASKPASS")
    if configured and Path(configured).is_file():
        return Path(configured)
    git_path = shutil.which("git")
    if not git_path:
        return None
    executable = Path(git_path).resolve()
    candidates = (
        Path(shutil.which("git-askpass") or ""),
        Path(shutil.which("ssh-askpass") or ""),
        executable.parent / "git-askpass.exe",
        executable.parent.parent / "mingw64" / "bin" / "git-askpass.exe",
        executable.parent.parent / "mingw64" / "libexec" / "git-core" / "git-gui--askpass",
        executable.parent.parent / "libexec" / "git-core" / "git-gui--askpass",
    )
    return next((candidate for candidate in candidates if str(candidate) and candidate.is_file()), None)


def build_git_environment(
    base: dict[str, str] | None = None,
    *,
    askpass_path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
        }
    )
    askpass = Path(askpass_path) if askpass_path else find_git_askpass()
    if askpass and askpass.is_file():
        for variable in ("GIT_ASKPASS", "SSH_ASKPASS"):
            configured = environment.get(variable, "")
            if not configured or not (Path(configured).is_file() or shutil.which(configured)):
                environment[variable] = str(askpass)
        environment["SSH_ASKPASS_REQUIRE"] = "force"
        environment.setdefault("DISPLAY", "LiteGitWorkbench")
    return environment


def discover_repository(path: str | os.PathLike[str]) -> Path:
    result = run_git(path, ["rev-parse", "--show-toplevel"])
    return Path(result.output.strip()).resolve()


def parse_status_porcelain(output: str) -> list[StatusEntry]:
    records = output.split("\0")
    entries: list[StatusEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record or len(record) < 3:
            continue
        status = record[:2]
        path = record[3:]
        original_path = ""
        if "R" in status or "C" in status:
            if index < len(records):
                original_path = records[index]
                index += 1
        entries.append(StatusEntry(status[0], status[1], path, original_path))
    return entries


def parse_worktree_porcelain(output: str) -> list[WorktreeEntry]:
    entries: list[WorktreeEntry] = []
    for block in re.split(r"\r?\n\r?\n", output.strip()):
        if not block.strip():
            continue
        values: dict[str, str] = {}
        flags: set[str] = set()
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if value:
                values[key] = value
            else:
                flags.add(key)
        branch = values.get("branch", "")
        if branch.startswith("refs/heads/"):
            branch = branch.removeprefix("refs/heads/")
        entries.append(
            WorktreeEntry(
                path=values.get("worktree", ""),
                head=values.get("HEAD", ""),
                branch=branch,
                detached="detached" in flags,
                locked=values.get("locked", "Yes" if "locked" in flags else ""),
                prunable=values.get("prunable", "Yes" if "prunable" in flags else ""),
            )
        )
    return entries


def parse_separated_rows(output: str, field_count: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in output.splitlines():
        if not line:
            continue
        fields = line.split(FIELD_SEPARATOR)
        if len(fields) < field_count:
            fields.extend([""] * (field_count - len(fields)))
        rows.append(fields[:field_count])
    return rows


def parse_branches(output: str) -> list[BranchEntry]:
    entries: list[BranchEntry] = []
    for fields in parse_separated_rows(output, 10):
        refname, short_hash, upstream, tracking, date, author, subject, head, short_name, ahead_behind = fields
        kind = "Remote" if refname.startswith("refs/remotes/") else "Local"
        values = ahead_behind.split()
        branch_only, head_only = (values[0], values[1]) if len(values) == 2 else ("-", "-")
        entries.append(
            BranchEntry(kind, short_name, short_hash, upstream, tracking, date, author, subject, head == "*", head_only, branch_only)
        )
    return entries


def parse_commits(output: str) -> list[CommitEntry]:
    return [CommitEntry(*fields) for fields in parse_separated_rows(output, 7)]


def parse_stashes(output: str) -> list[StashEntry]:
    return [StashEntry(*fields) for fields in parse_separated_rows(output, 3)]


def parse_remotes(output: str) -> list[RemoteEntry]:
    by_name: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        match = re.match(r"^(\S+)\s+(.+?)\s+\((fetch|push)\)$", line)
        if not match:
            continue
        name, url, operation = match.groups()
        by_name.setdefault(name, {})[operation] = url
    return [
        RemoteEntry(name, values.get("fetch", ""), values.get("push", ""))
        for name, values in sorted(by_name.items())
    ]


def split_command_line(command_line: str) -> list[str]:
    if not command_line.strip():
        return []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            count = ctypes.c_int()
            parser = ctypes.windll.shell32.CommandLineToArgvW
            parser.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            parser.restype = ctypes.POINTER(wintypes.LPWSTR)
            pointer = parser(command_line, ctypes.byref(count))
            if not pointer:
                raise ValueError("The command line could not be parsed")
            try:
                return [pointer[i] for i in range(count.value)]
            finally:
                ctypes.windll.kernel32.LocalFree(pointer)
        except (AttributeError, OSError):
            pass
    return shlex.split(command_line)


DANGEROUS_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("clean",),
    ("reset", "--hard"),
    ("checkout", "--"),
    ("restore",),
    ("branch", "-D"),
    ("branch", "--delete", "--force"),
    ("push", "--force"),
    ("push", "-f"),
    ("reflog", "expire"),
    ("gc",),
    ("worktree", "remove"),
    ("worktree", "prune"),
    ("stash", "drop"),
    ("stash", "clear"),
)


def is_potentially_destructive(arguments: Sequence[str]) -> bool:
    lowered = tuple(argument.lower() for argument in arguments)
    for pattern in DANGEROUS_PATTERNS:
        if all(token.lower() in lowered for token in pattern):
            return True
    return False


def branch_format(*, include_divergence: bool = True) -> str:
    fields = (
        "%(refname)",
        "%(objectname:short)",
        "%(upstream:short)",
        "%(upstream:trackshort)",
        "%(committerdate:iso8601)",
        "%(authorname)",
        "%(subject)",
        "%(HEAD)",
        "%(refname:short)",
    )
    if include_divergence:
        fields = (*fields, "%(ahead-behind:HEAD)")
    return FIELD_SEPARATOR.join(fields)


def commit_format() -> str:
    return FIELD_SEPARATOR.join(("%H", "%h", "%P", "%ad", "%an", "%D", "%s"))


def stash_format() -> str:
    return FIELD_SEPARATOR.join(("%gd", "%ad", "%gs"))


def changed_paths(arguments: Iterable[StatusEntry]) -> list[str]:
    return [entry.path for entry in arguments]


def find_remote_reachable_commits(
    repository: str | os.PathLike[str],
    commit_hashes: Sequence[str],
) -> set[str]:
    requested = set(commit_hashes)
    if not requested:
        return set()
    remote_history = set(run_git(repository, ["rev-list", "--remotes"]).output.splitlines())
    return requested.intersection(remote_history)


def is_commit_message_editable(commit_hash: str, remote_reachable: set[str]) -> bool:
    return commit_hash not in remote_reachable
