from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from litegit_core import GitError, parse_worktree_porcelain, run_git, run_git_bytes


MAX_EDITOR_BYTES = 2 * 1024 * 1024
MAX_EDITOR_LINES = 20000


def unresolved_paths(repository: Path) -> list[str]:
    output = run_git(repository, ["diff", "--name-only", "--diff-filter=U", "-z"]).output
    return [path for path in output.split("\0") if path]


def _safe_file(repository: Path, path: str) -> Path:
    target = repository / path
    if target.is_symlink() or not target.resolve().is_relative_to(repository.resolve()):
        raise ValueError("The conflict editor cannot overwrite symbolic links or files outside the repository.")
    return target


def _blob(repository: Path, object_id: str) -> bytes:
    return run_git_bytes(repository, ["cat-file", "blob", object_id])


def _decode(data: bytes) -> str:
    if b"\0" in data:
        raise ValueError("Binary files require an external editor. The original file has been preserved.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("This file is not UTF-8 text. Resolve it externally to preserve its encoding.") from exc


def _check_editor_size(data: bytes) -> None:
    if len(data) > MAX_EDITOR_BYTES or data.count(b"\n") > MAX_EDITOR_LINES:
        raise ValueError("This file is too large for inline editing (2 MiB or 20,000 lines per version). Use an external editor.")


@dataclass(frozen=True)
class ConflictFile:
    repository: Path
    path: str
    stages: str
    base: str | None
    ours: str | None
    theirs: str | None
    working: bytes | None

    @property
    def result(self) -> str:
        return _decode(self.working) if self.working is not None else ""


def read_conflict(repository: Path, path: str) -> ConflictFile:
    target = _safe_file(repository, path)
    stages = run_git(repository, ["--literal-pathspecs", "ls-files", "--unmerged", "-z", "--", path]).output
    if not stages:
        raise ValueError("This file no longer has an unresolved conflict. Refresh the conflict list.")
    contents: dict[int, str] = {}
    for record in stages.split("\0"):
        if not record:
            continue
        metadata, _, _filename = record.partition("\t")
        mode, object_id, stage = metadata.split()
        if mode not in {"100644", "100755"}:
            raise ValueError("Symbolic-link and submodule conflicts must be resolved externally.")
        if int(run_git(repository, ["cat-file", "-s", object_id]).output.strip()) > MAX_EDITOR_BYTES:
            raise ValueError("This file is too large for inline editing (2 MiB per version). Use an external editor.")
        data = _blob(repository, object_id)
        _check_editor_size(data)
        contents[int(stage)] = _decode(data)
    if target.exists() and target.stat().st_size > MAX_EDITOR_BYTES:
        raise ValueError("This file is too large for inline editing (2 MiB per version). Use an external editor.")
    working = target.read_bytes() if target.exists() else None
    if working is not None:
        _check_editor_size(working)
        _decode(working)
    return ConflictFile(repository, path, stages, contents.get(1), contents.get(2), contents.get(3), working)


def has_conflict_markers(content: str) -> bool:
    # A standalone equals line can be a legitimate Markdown heading underline.
    content = content.replace("\r\n", "\n")
    return bool(re.search(r"(?m)^(?:<{7,}|>{7,}|\|{7,})(?:[^\r\n]*)$", content))


def save_resolution(conflict: ConflictFile, content: str, *, delete: bool = False) -> None:
    target = _safe_file(conflict.repository, conflict.path)
    stages = run_git(conflict.repository, ["--literal-pathspecs", "ls-files", "--unmerged", "-z", "--", conflict.path]).output
    working = target.read_bytes() if target.exists() else None
    if stages != conflict.stages or working != conflict.working:
        raise ValueError("The file or conflict index changed after opening. Reopen it before saving.")
    if delete:
        if conflict.ours is not None and conflict.theirs is not None:
            raise ValueError("Neither side deleted this file. An explicit external deletion is required.")
        target.unlink(missing_ok=True)
    else:
        if has_conflict_markers(content):
            raise ValueError("Resolve all conflict markers before saving and staging the result.")
        original = conflict.working or (conflict.ours or conflict.theirs or "").encode("utf-8")
        newline = "\r\n" if b"\r\n" in original else "\n"
        content = content.replace("\r\n", "\n").replace("\n", newline)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    written = target.read_bytes() if target.exists() else None
    try:
        run_git(conflict.repository, ["--literal-pathspecs", "add", "--", conflict.path])
    except Exception:
        current = target.read_bytes() if target.exists() else None
        unchanged_index = run_git(conflict.repository, ["--literal-pathspecs", "ls-files", "--unmerged", "-z", "--", conflict.path]).output == conflict.stages
        if current == written and unchanged_index:
            if conflict.working is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(conflict.working)
        raise


@dataclass(frozen=True)
class ConflictRegion:
    start: int
    end: int
    ours: str
    theirs: str


def conflict_regions(content: str) -> list[ConflictRegion]:
    pattern = re.compile(
        r"^<{7,}[^\n]*\n(.*?)(?:^\|{7,}[^\n]*\n.*?)?^={7,}[^\n]*\n(.*?)^>{7,}[^\n]*(?:\n|$)",
        re.MULTILINE | re.DOTALL,
    )
    return [ConflictRegion(match.start(), match.end(), match[1], match[2]) for match in pattern.finditer(content)]


@dataclass
class PendingUpdate:
    repository: str
    branch: str
    original_head: str
    incoming_head: str
    worktree: str
    private_ref: str
    record: str
    published_head: str = ""

    def persist(self) -> None:
        path = Path(self.record)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)


@dataclass(frozen=True)
class BranchUpdateResult:
    branch: str
    outcome: str
    pending: PendingUpdate | None = None


def _updates_directory(repository: Path) -> Path:
    common = Path(run_git(repository, ["rev-parse", "--git-common-dir"]).output.strip())
    return (repository / common).resolve() / "litegit-updates"


def pending_updates(repository: Path) -> list[PendingUpdate]:
    directory = _updates_directory(repository)
    updates = []
    for record in sorted(directory.glob("*/session.json")):
        update = PendingUpdate(**json.loads(record.read_text(encoding="utf-8")))
        if (
            not re.fullmatch(r"[a-f0-9]{32}", record.parent.name)
            or update.private_ref != f"refs/litegit/updates/{record.parent.name}"
            or Path(update.record).resolve() != record.resolve()
            or Path(update.worktree).resolve() != record.parent / "worktree"
            or _updates_directory(Path(update.repository)) != directory
        ):
            raise ValueError("An update recovery record contains an unexpected path. No files were removed.")
        updates.append(update)
    return updates


def _available_branch(repository: Path, branch: str) -> str:
    run_git(repository, ["check-ref-format", f"refs/heads/{branch}"])
    worktrees = parse_worktree_porcelain(run_git(repository, ["worktree", "list", "--porcelain"]).output)
    if any(entry.branch == branch for entry in worktrees):
        raise ValueError(f"{branch} is checked out in a worktree. Update it from that worktree instead.")
    return run_git(repository, ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"]).output.strip()


def _publish(update: PendingUpdate, head: str) -> None:
    repository = Path(update.repository)
    current = _available_branch(repository, update.branch)
    # A retry may follow a crash between publishing the ref and saving the journal.
    if current != head or current == update.original_head:
        run_git(repository, ["update-ref", "-m", "LiteGit: update branch from upstream", f"refs/heads/{update.branch}", head, update.original_head])
    update.published_head = head
    update.persist()


def _clean_update(update: PendingUpdate) -> None:
    repository = Path(update.repository)
    if Path(update.worktree).exists():
        try:
            run_git(repository, ["worktree", "remove", "--", update.worktree])
        except GitError as exc:
            if update.published_head:
                raise GitError(exc.command, exc.returncode, "The branch has already been updated. Its recovery worktree needs cleanup; use Complete merge / update to retry.\n" + exc.output) from exc
            raise
    run_git(repository, ["update-ref", "-d", update.private_ref, update.incoming_head])
    record = Path(update.record)
    record.unlink(missing_ok=True)
    record.parent.rmdir()


def update_branch(repository: Path, branch: str) -> BranchUpdateResult:
    original = _available_branch(repository, branch)
    if any(update.branch == branch for update in pending_updates(repository)):
        raise ValueError("An update of this branch is already pending. Resume or abort it in Resolve conflicts.")
    remote = run_git(repository, ["config", "--get", f"branch.{branch}.remote"], check=False).output.strip()
    merge_ref = run_git(repository, ["config", "--get", f"branch.{branch}.merge"], check=False).output.strip()
    if not remote or not merge_ref.startswith("refs/heads/"):
        raise ValueError("Set an upstream for this local branch before updating it.")
    token = uuid.uuid4().hex
    private_ref = f"refs/litegit/updates/{token}"
    run_git(repository, ["fetch", "--no-tags", "--no-write-fetch-head", "--", remote, f"{merge_ref}:{private_ref}"])
    incoming = run_git(repository, ["rev-parse", "--verify", private_ref]).output.strip()
    directory = _updates_directory(repository) / token
    update = PendingUpdate(str(repository), branch, original, incoming, str(directory / "worktree"), private_ref, str(directory / "session.json"))
    update.persist()
    if run_git(repository, ["merge-base", "--is-ancestor", incoming, original], check=False).returncode == 0:
        _clean_update(update)
        return BranchUpdateResult(branch, "Already up to date")
    if run_git(repository, ["merge-base", "--is-ancestor", original, incoming], check=False).returncode == 0:
        _publish(update, incoming)
        _clean_update(update)
        return BranchUpdateResult(branch, "Fast-forwarded")
    run_git(repository, ["worktree", "add", "--detach", update.worktree, original])
    result = run_git(Path(update.worktree), ["merge", "--no-edit", incoming], check=False)
    if result.returncode:
        if unresolved_paths(Path(update.worktree)):
            return BranchUpdateResult(branch, "Conflicts need resolving", update)
        raise GitError(result.command, result.returncode, result.output + f"\nRecovery worktree: {update.worktree}")
    return finish_update(update)


def finish_update(update: PendingUpdate) -> BranchUpdateResult:
    worktree = Path(update.worktree)
    if not update.published_head:
        if not worktree.exists():
            if run_git(Path(update.repository), ["merge-base", "--is-ancestor", update.original_head, update.incoming_head], check=False).returncode:
                raise ValueError("This update has no merge worktree. Abort it and start again; the branch has not been moved.")
            _publish(update, update.incoming_head)
            _clean_update(update)
            return BranchUpdateResult(update.branch, "Fast-forwarded")
        if unresolved_paths(worktree):
            raise ValueError("Resolve and stage every conflict before completing the update.")
        merge_head = run_git(worktree, ["rev-parse", "--verify", "MERGE_HEAD"], check=False)
        if merge_head.returncode == 0:
            run_git(worktree, ["-c", "core.editor=true", "commit", "--no-edit"])
        if run_git(worktree, ["status", "--porcelain=v1", "-z"]).output:
            raise ValueError("The update worktree still has uncommitted files. They have been preserved.")
        head = run_git(worktree, ["rev-parse", "HEAD"]).output.strip()
        if run_git(worktree, ["merge-base", "--is-ancestor", update.incoming_head, head], check=False).returncode:
            raise ValueError("The incoming revision has not been merged. Resume or abort this update.")
        _publish(update, head)
    _clean_update(update)
    return BranchUpdateResult(update.branch, "Merged and updated")


def abort_update(update: PendingUpdate) -> None:
    if update.published_head:
        _clean_update(update)
        return
    worktree = Path(update.worktree)
    if worktree.exists():
        if run_git(worktree, ["rev-parse", "--verify", "MERGE_HEAD"], check=False).returncode == 0:
            run_git(worktree, ["merge", "--abort"])
    _clean_update(update)
