from __future__ import annotations

from litegit_startup import begin_startup, recorded_stages, startup_phase

begin_startup()

import copy
import gc
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from litegit_core import (
    BranchEntry,
    CommitEntry,
    GitError,
    GitResult,
    RemoteEntry,
    StashEntry,
    StatusEntry,
    WorktreeEntry,
    branch_format,
    find_git_askpass,
    commit_format,
    discover_repository,
    display_command,
    is_potentially_destructive,
    is_commit_message_editable,
    find_remote_reachable_commits,
    parse_branches,
    parse_commits,
    parse_remotes,
    parse_stashes,
    parse_status_porcelain,
    parse_worktree_porcelain,
    redact_text,
    run_git,
    split_command_line,
    stash_format,
)
from litegit_reword import main as reword_helper_main
from litegit_layout import DeferredPanedwindow, ResponsiveSummary
from litegit_process import external_environment, external_runtime_status, find_executable, is_packaged_application, launch_external, native_launcher, prepare_external_processes
from litegit_diff import DiffRow, DirectoryPreview, FileVersions, aligned_diff, read_file_versions
from litegit_merge import BranchUpdateResult, unresolved_paths, update_branch
from litegit_views import ConflictEditor, ConflictManager, DiffPreview, HoverTip, bind_context_menu, context_row, popup_menu


APP_NAME = "LiteGit Workbench"
VERSION = "2.1.7"


def application_config_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "LiteGitWorkbench"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LiteGitWorkbench"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "litegit-workbench"


CONFIG_DIR = application_config_dir()
CONFIG_FILE = CONFIG_DIR / "settings.json"
PROJECT_CACHE_FILE = Path.cwd() / ".litegit-projects.json"
PROJECT_CACHE_FIELDS = ("projects", "last_repository", "sidebar_collapsed", "project_main_paths")
SETTINGS_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.16, 0.32, 0.64)


def _editor_argument(value: str | os.PathLike[str]) -> str:
    normalised = str(value).replace("\\", "/")
    return '"' + normalised.replace('"', '\\"') + '"'


def build_reword_environment(target: str, message: str) -> dict[str, str]:
    executable = _editor_argument(native_launcher() or sys.executable)
    if is_packaged_application():
        editor_prefix = f"{executable} --reword-helper"
    elif Path(sys.argv[0]).suffix.casefold() == ".pyz":
        archive = _editor_argument(Path(sys.argv[0]).resolve())
        editor_prefix = f"{executable} {archive} --reword-helper"
    else:
        editor_prefix = f"{executable} {_editor_argument(Path(__file__).resolve())} --reword-helper"
    return {
        "GIT_SEQUENCE_EDITOR": f"{editor_prefix} sequence",
        "GIT_EDITOR": f"{editor_prefix} message",
        "LITEGIT_REWORD_TARGET": target,
        "LITEGIT_REWORD_MESSAGE": message,
    }


def _read_settings_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def load_settings() -> dict[str, object]:
    settings = _read_settings_json(CONFIG_FILE)
    cache = _read_settings_json(PROJECT_CACHE_FILE)
    fallback_path = project_cache_fallback_file()
    fallback = _read_settings_json(fallback_path)
    if isinstance(fallback.get("projects"), list) and (
        not isinstance(cache.get("projects"), list)
        or _cache_saved_at(fallback_path, fallback) > _cache_saved_at(PROJECT_CACHE_FILE, cache)
    ):
        cache = fallback
    if isinstance(cache.get("projects"), list):
        for field in PROJECT_CACHE_FIELDS:
            settings.pop(field, None)
        settings.update({field: cache[field] for field in PROJECT_CACHE_FIELDS if field in cache})
    return settings


def project_cache_fallback_file() -> Path:
    scope = os.path.normcase(os.path.abspath(PROJECT_CACHE_FILE))
    name = hashlib.sha256(scope.encode("utf-8")).hexdigest() + ".json"
    return CONFIG_FILE.parent / "project-caches" / name


def _cache_saved_at(path: Path, cache: dict[str, object]) -> int:
    saved_at = cache.get("saved_at_ns")
    if isinstance(saved_at, int) and not isinstance(saved_at, bool) and saved_at > 0:
        return saved_at
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _replace_settings_file(temporary: Path, path: Path) -> None:
    for attempt in range(len(SETTINGS_RETRY_DELAYS) + 1):
        try:
            temporary.replace(path)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == len(SETTINGS_RETRY_DELAYS):
                raise
            time.sleep(SETTINGS_RETRY_DELAYS[attempt])


def _write_settings_json(path: Path, settings: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, indent=2)
        _replace_settings_file(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # An external reader must not mask the original save result.


def save_settings(settings: dict[str, object]) -> None:
    cache = {field: settings[field] for field in PROJECT_CACHE_FIELDS if field in settings}
    cache.setdefault("projects", [])
    cache["version"] = 2
    cache["saved_at_ns"] = time.time_ns()
    try:
        _write_settings_json(PROJECT_CACHE_FILE, cache)
    except OSError as local_error:
        try:
            _write_settings_json(project_cache_fallback_file(), cache)
        except OSError as fallback_error:
            raise local_error from fallback_error
    try:
        _write_settings_json(CONFIG_FILE, settings)
    except OSError:
        pass


def main_worktree_path(repository: Path) -> Path:
    worktrees = parse_worktree_porcelain(run_git(repository, ["worktree", "list", "--porcelain"]).output)
    if not worktrees:
        raise ValueError("Git did not return a main worktree for this repository.")
    return Path(worktrees[0].path).resolve()


class CommitMessageDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, initial: str) -> None:
        super().__init__(parent)
        self.title("Edit commit message")
        self.geometry("720x420")
        self.minsize(520, 300)
        self.transient(parent)
        self.result: str | None = None
        self.initial = initial.strip()
        ttk.Label(self, text="Use a concise title on the first line; add details after a blank line.", padding=(10, 10, 10, 5)).pack(fill=tk.X)
        frame = ttk.Frame(self, padding=(10, 0, 10, 5))
        frame.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(frame, wrap=tk.WORD, undo=True, font=("Cascadia Mono", 10))
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.insert("1.0", initial)
        buttons = ttk.Frame(self, padding=(10, 5, 10, 10))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(5, 0))
        self.save_button = ttk.Button(buttons, text="Save changes", command=self._accept)
        self.save_button.pack(side=tk.RIGHT)
        self.bind("<Control-s>", lambda _event: self._accept())
        self.bind("<Control-S>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.text.focus_set()

    def _accept(self) -> None:
        value = self.text.get("1.0", "end-1c").strip()
        if not value:
            messagebox.showinfo(APP_NAME, "The commit message cannot be empty.", parent=self)
            return
        self.result = value
        self.destroy()

    def _cancel(self) -> None:
        current = self.text.get("1.0", "end-1c").strip()
        if current != self.initial:
            if not messagebox.askyesno(APP_NAME, "Discard the unsaved commit message changes?", icon="warning", parent=self):
                return
        self.destroy()

    @classmethod
    def ask(cls, parent: tk.Misc, initial: str) -> str | None:
        dialog = cls(parent, initial)
        parent.wait_window(dialog)
        return dialog.result


class RepositoryView(ttk.Frame):
    def __init__(self, app: LiteGitApp) -> None:
        super().__init__(app)
        self.app = app
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="litegit")
        self.repository: Path | None = None
        self.settings = app.settings
        self.branches: list[BranchEntry] = []
        self.commits: list[CommitEntry] = []
        self.remote_commit_hashes: set[str] = set()
        self.status_entries: list[StatusEntry] = []
        self.worktrees: list[WorktreeEntry] = []
        self.stashes: list[StashEntry] = []
        self.remotes: list[RemoteEntry] = []
        self._busy_count = 0
        self._closing = False
        self._request_serials: dict[str, int] = {}
        self._change_row_paths: dict[str, str] = {}
        self._conflict_manager: ConflictManager | None = None
        self._build_style()
        self._build_ui()

    def _build_style(self) -> None:
        if self.app._style_initialised:
            return
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                if style.theme_use() != theme:
                    style.theme_use(theme)
                break
        style.configure("Treeview", rowheight=25)
        style.configure("Summary.TLabel", font=("Segoe UI", 10, "bold"))
        self.app._style_initialised = True

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Repository:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar()
        path_entry = ttk.Entry(top, textvariable=self.path_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        path_entry.bind("<Return>", lambda _event: self.open_repository(self.path_var.get()))
        ttk.Button(top, text="Open...", command=self.choose_repository).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Initialise...", command=self.initialise_repository).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Clone...", command=self.clone_repository).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Refresh F5", command=self.refresh_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Fetch", command=lambda: self.execute_git(["fetch", "--all", "--prune"], refresh=True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Pull", command=self.pull).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Push", command=self.push).pack(side=tk.LEFT, padx=2)

        self.summary_vars = {key: tk.StringVar(value="-") for key in ("branch", "head", "upstream", "sync", "state", "worktrees")}
        captions = (
            ("Branch", "branch"),
            ("HEAD", "head"),
            ("Upstream", "upstream"),
            ("Synchronisation", "sync"),
            ("Working tree", "state"),
            ("Worktrees", "worktrees"),
        )
        self.summary_bar = ResponsiveSummary(self, [(caption, self.summary_vars[key]) for caption, key in captions])
        self.summary_bar.pack(fill=tk.X)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 5))
        self._build_branches_tab()
        self._build_history_tab()
        self._build_changes_tab()
        self._build_compare_tab()
        self._build_worktrees_tab()
        self._build_stashes_tags_tab()
        self._build_remotes_tab()
        self._build_advanced_tab()
        self._build_console_tab()

        bottom = ttk.Frame(self, padding=(8, 2, 8, 5))
        bottom.pack(fill=tk.X)
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=120)
        self.progress.pack(side=tk.RIGHT)
        self.status_var = tk.StringVar(value="Select a Git repository")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _tree(self, parent: tk.Misc, columns: Sequence[tuple[str, str, int]], *, selectmode: str = "browse") -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=[column[0] for column in columns], show="headings", selectmode=selectmode)
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for name, heading, width in columns:
            tree.heading(name, text=heading, command=lambda c=name, t=tree: self._sort_tree(t, c, False))
            tree.column(name, width=width, minwidth=55, stretch=name in {"name", "subject", "path", "url"})
        return tree

    def _text(self, parent: tk.Misc, *, height: int = 10) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(frame, height=height, wrap=tk.NONE, undo=False, font=("Cascadia Mono", 10))
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        text.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return text

    def _build_branches_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Branches")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        self.branch_toolbar = toolbar
        for column, (text, command) in enumerate((
            ("Refresh branches", self.refresh_all),
            ("Update selected", self.update_selected_branch),
            ("Resolve conflicts...", self.open_conflicts),
        )):
            ttk.Button(toolbar, text=text, command=command).grid(row=0, column=column, padx=2, sticky="w")
        toolbar.columnconfigure(3, weight=1)
        self.branch_filter_var = tk.StringVar()
        ttk.Label(toolbar, text="Filter:").grid(row=0, column=4, padx=(12, 2), sticky="e")
        branch_filter = ttk.Entry(toolbar, textvariable=self.branch_filter_var, width=25)
        branch_filter.grid(row=0, column=5, sticky="e")
        branch_filter.bind("<KeyRelease>", lambda _event: self.render_branches())
        actions = ttk.Frame(tab)
        actions.pack(fill=tk.X, pady=(0, 5))
        self.branch_groups: dict[str, ttk.LabelFrame] = {}
        for column, (caption, buttons, columns) in enumerate((
            ("Branches", (("Check out", self.checkout_branch), ("New", self.create_branch), ("Rename", self.rename_branch), ("Delete", self.delete_branch)), 2),
            ("Integration", (("Merge selected...", self.merge_selected_branch), ("Rebase current...", self.rebase_selected_branch), ("Abort merge", self.abort_merge)), 2),
            ("Tracking", (("Set upstream", self.set_upstream), ("Delete remote branch", self.delete_remote_branch)), 1),
        )):
            actions.columnconfigure(column, weight=1, uniform="branch-actions")
            group = ttk.LabelFrame(actions, text=caption, padding=4)
            group.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 0))
            self.branch_groups[caption] = group
            for index, (text, command) in enumerate(buttons):
                ttk.Button(group, text=text, command=command).grid(row=index // columns, column=index % columns, padx=2, pady=2, sticky="ew")
            for index in range(columns):
                group.columnconfigure(index, weight=1, uniform="buttons")
        self.branch_tree = self._tree(
            tab,
            (
                ("current", "", 35),
                ("kind", "Type", 60),
                ("name", "Branch", 250),
                ("hash", "HEAD", 90),
                ("upstream", "Upstream", 200),
                ("tracking", "Tracking", 70),
                ("head_only", "HEAD only", 75),
                ("branch_only", "Branch only", 75),
                ("date", "Last commit", 155),
                ("author", "Author", 120),
                ("subject", "Subject", 360),
            ),
        )
        self.branch_tree.bind("<Double-1>", lambda _event: self.checkout_branch())
        self.branch_tree.bind("<<TreeviewSelect>>", lambda _event: self.show_branch_summary())
        self.branch_menu = tk.Menu(self, tearoff=False)
        for label, action in (
            ("Update branch from upstream", "update_selected_branch"), ("Check out", "checkout_branch"),
            ("Merge into current branch...", "merge_selected_branch"), ("Rebase current branch...", "rebase_selected_branch"),
            ("Set upstream...", "set_upstream"), ("Rename...", "rename_branch"),
            ("Delete local branch...", "delete_branch"), ("Delete remote branch...", "delete_remote_branch"),
        ):
            self.branch_menu.add_command(label=label, command=lambda method=action: getattr(self, method)())
        self.branch_menu.add_separator()
        self.branch_menu.add_command(label="Refresh branches", command=self.refresh_all)
        self.branch_menu.add_command(label="Resolve conflicts...", command=self.open_conflicts)
        bind_context_menu(self.branch_tree, self._on_branch_context_menu)

    def _build_history_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="History")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(toolbar, text="Reference:").pack(side=tk.LEFT)
        self.history_ref_var = tk.StringVar(value="HEAD")
        self.history_ref_combo = ttk.Combobox(toolbar, textvariable=self.history_ref_var, width=30)
        self.history_ref_combo.pack(side=tk.LEFT, padx=(2, 8))
        self.history_ref_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_history())
        ttk.Label(toolbar, text="Limit:").pack(side=tk.LEFT)
        self.history_limit_var = tk.IntVar(value=200)
        ttk.Spinbox(toolbar, from_=10, to=5000, increment=50, textvariable=self.history_limit_var, width=7).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Button(toolbar, text="Refresh", command=self.refresh_history).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Show commit", command=self.show_commit).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Copy hash", command=self.copy_commit_hash).pack(side=tk.LEFT, padx=2)
        self.edit_message_button = ttk.Button(toolbar, text="Edit message", command=self.edit_commit_message, state=tk.DISABLED)
        self.edit_message_button.pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Cherry-pick", command=self.cherry_pick).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Revert", command=self.revert_commit).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Create tag", command=self.create_tag_at_commit).pack(side=tk.LEFT, padx=2)
        tk.Label(toolbar, text="* Local only / not pushed", foreground="#087A3E").pack(side=tk.RIGHT, padx=(10, 2))
        tk.Label(toolbar, text="* Published remotely", foreground="#7A8494").pack(side=tk.RIGHT, padx=2)
        pane = DeferredPanedwindow(tab, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)
        upper = ttk.Frame(pane)
        lower = ttk.Frame(pane)
        pane.add(upper, weight=3)
        pane.add(lower, weight=2)
        self.commit_tree = self._tree(
            upper,
            (
                ("remote_state", "Remote state", 110),
                ("hash", "Hash", 90),
                ("date", "Date", 150),
                ("author", "Author", 140),
                ("decorations", "References", 220),
                ("subject", "Commit message", 600),
            ),
        )
        self.commit_tree.tag_configure("local_only", background="#DDF6E5", foreground="#075E32")
        self.commit_tree.tag_configure("remote", background="#F2F4F7", foreground="#667085")
        self.commit_tree.bind("<<TreeviewSelect>>", self._on_commit_selection)
        self.commit_tree.bind("<Double-1>", self._on_commit_double_click)
        self.commit_menu = tk.Menu(self, tearoff=False)
        for label, action in (
            ("Show commit", "show_commit"), ("Copy hash", "copy_commit_hash"),
            ("Edit message...", "edit_commit_message"), ("Cherry-pick...", "cherry_pick"),
            ("Revert...", "revert_commit"), ("Create tag...", "create_tag_at_commit"),
        ):
            self.commit_menu.add_command(label=label, command=lambda method=action: getattr(self, method)())
        self.commit_menu.add_separator()
        self.commit_menu.add_command(label="Refresh history", command=self.refresh_history)
        bind_context_menu(self.commit_tree, self._on_commit_context_menu)
        self.commit_detail_text = self._text(lower, height=12)

    def _build_changes_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.changes_tab = tab
        self.notebook.add(tab, text="Local changes")
        pane = DeferredPanedwindow(tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)
        toolbar = ttk.Frame(left)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(toolbar, text="Changed files").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Refresh", command=self.refresh_all).pack(side=tk.RIGHT, padx=2)
        actions = ttk.Frame(left)
        actions.pack(fill=tk.X, pady=(0, 5))
        for index, (text, command) in enumerate((
            ("Stage selected", self.stage_selected),
            ("Unstage selected", self.unstage_selected),
            ("Stage all", lambda: self.execute_git(["add", "-A"], refresh=True)),
            ("Unstage all", lambda: self.execute_git(["reset", "--mixed"], refresh=True)),
            ("Discard selected", self.discard_selected),
            ("Resolve conflicts...", self.open_conflicts),
        )):
            ttk.Button(actions, text=text, command=command).grid(row=index // 2, column=index % 2, padx=2, pady=2, sticky="ew")
        actions.columnconfigure((0, 1), weight=1, uniform="change-actions")
        self.change_tree = self._tree(
            left,
            (("code", "State", 75), ("state", "Description", 160), ("path", "Path", 430)),
            selectmode="extended",
        )
        self.change_tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected_diff())
        self.change_tree.bind("<Double-1>", lambda _event: self.open_selected_conflict())
        diff_tools = ttk.Frame(right)
        self.compare_toolbar = diff_tools
        diff_tools.pack(fill=tk.X)
        ttk.Label(diff_tools, text="Compare:").pack(side=tk.LEFT)
        self.diff_scope_var = tk.StringVar(value="All changes")
        scope = ttk.Combobox(diff_tools, textvariable=self.diff_scope_var, values=("All changes", "Staged", "Unstaged"), state="readonly", width=16)
        scope.pack(side=tk.LEFT, padx=4)
        scope.bind("<<ComboboxSelected>>", lambda _event: self.show_selected_diff())
        self.diff_preview = DiffPreview(right)
        commit_frame = ttk.LabelFrame(tab, text="Commit", padding=5)
        commit_frame.pack(fill=tk.X, pady=(6, 0))
        self.commit_message_var = tk.StringVar()
        ttk.Entry(commit_frame, textvariable=self.commit_message_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(commit_frame, text="Commit staged changes", command=self.commit_staged).pack(side=tk.LEFT)
        self.amend_var = tk.BooleanVar()
        ttk.Checkbutton(commit_frame, text="Amend", variable=self.amend_var).pack(side=tk.LEFT, padx=8)

    def _build_compare_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Branch comparison")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(toolbar, text="Base:").pack(side=tk.LEFT)
        self.compare_base_var = tk.StringVar(value="main")
        self.compare_base_combo = ttk.Combobox(toolbar, textvariable=self.compare_base_var, width=28)
        self.compare_base_combo.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(toolbar, text="Target:").pack(side=tk.LEFT)
        self.compare_target_var = tk.StringVar(value="HEAD")
        self.compare_target_combo = ttk.Combobox(toolbar, textvariable=self.compare_target_var, width=28)
        self.compare_target_combo.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Button(toolbar, text="Three-dot diff", command=lambda: self.refresh_compare(True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Direct diff", command=lambda: self.refresh_compare(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Statistics only", command=self.refresh_compare_stat).pack(side=tk.LEFT, padx=2)
        self.compare_summary_var = tk.StringVar(value="Select two references")
        ttk.Label(tab, textvariable=self.compare_summary_var, style="Summary.TLabel").pack(fill=tk.X, pady=(0, 4))
        self.compare_text = self._text(tab)

    def _build_worktrees_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Worktrees")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        for text, command in (
            ("Add", self.add_worktree),
            ("Open folder", self.open_worktree_folder),
            ("Lock", lambda: self.worktree_action("lock")),
            ("Unlock", lambda: self.worktree_action("unlock")),
            ("Remove", self.remove_worktree),
            ("Prune", self.prune_worktrees),
            ("Refresh", self.refresh_all),
        ):
            ttk.Button(toolbar, text=text, command=command).pack(side=tk.LEFT, padx=2)
        self.worktree_tree = self._tree(
            tab,
            (
                ("path", "Directory", 500),
                ("branch", "Branch", 220),
                ("head", "HEAD", 110),
                ("detached", "Detached", 75),
                ("locked", "Locked", 150),
                ("prunable", "Prunable", 170),
            ),
        )
        self.worktree_tree.bind("<Double-1>", lambda _event: self.open_worktree_folder())

    def _build_stashes_tags_tab(self) -> None:
        outer = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(outer, text="Stash / Tags")
        nested = ttk.Notebook(outer)
        nested.pack(fill=tk.BOTH, expand=True)
        stash_tab = ttk.Frame(nested, padding=5)
        tag_tab = ttk.Frame(nested, padding=5)
        nested.add(stash_tab, text="Stashes")
        nested.add(tag_tab, text="Tags")
        toolbar = ttk.Frame(stash_tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        for text, command in (
            ("New stash", self.create_stash),
            ("Apply", lambda: self.stash_action("apply")),
            ("Pop", lambda: self.stash_action("pop")),
            ("Drop", lambda: self.stash_action("drop")),
            ("Show", self.show_stash),
        ):
            ttk.Button(toolbar, text=text, command=command).pack(side=tk.LEFT, padx=2)
        self.stash_tree = self._tree(stash_tab, (("ref", "Reference", 110), ("date", "Date", 190), ("subject", "Description", 700)))
        tag_toolbar = ttk.Frame(tag_tab)
        tag_toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(tag_toolbar, text="New tag", command=self.create_tag).pack(side=tk.LEFT, padx=2)
        ttk.Button(tag_toolbar, text="Delete tag", command=self.delete_tag).pack(side=tk.LEFT, padx=2)
        ttk.Button(tag_toolbar, text="Push tag", command=self.push_tag).pack(side=tk.LEFT, padx=2)
        ttk.Button(tag_toolbar, text="Refresh", command=self.refresh_all).pack(side=tk.LEFT, padx=2)
        self.tag_tree = self._tree(tag_tab, (("name", "Tag", 260), ("hash", "Object", 110), ("date", "Date", 180), ("subject", "Description", 650)))

    def _build_remotes_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Remotes")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        for text, command in (
            ("Add", self.add_remote),
            ("Edit URL", self.edit_remote),
            ("Delete", self.delete_remote),
            ("Fetch", lambda: self.execute_git(["fetch", "--all", "--prune"], refresh=True)),
            ("Pull", self.pull),
            ("Push", self.push),
        ):
            ttk.Button(toolbar, text=text, command=command).pack(side=tk.LEFT, padx=2)
        self.force_with_lease_var = tk.BooleanVar()
        self.set_upstream_push_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="force-with-lease", variable=self.force_with_lease_var).pack(side=tk.LEFT, padx=(18, 2))
        ttk.Checkbutton(toolbar, text="Set upstream on first push", variable=self.set_upstream_push_var).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Test origin authentication", command=self.test_origin_authentication).pack(side=tk.LEFT, padx=(12, 2))
        askpass = find_git_askpass()
        self.askpass_status_var = tk.StringVar(value=f"AskPass: {askpass}" if askpass else "AskPass: not found; passphrases for encrypted keys cannot be entered")
        ttk.Label(tab, textvariable=self.askpass_status_var).pack(fill=tk.X, pady=(0, 5))
        self.remote_tree = self._tree(tab, (("name", "Name", 130), ("fetch", "Fetch URL", 520), ("push", "Push URL", 520)))

    def _build_console_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Git console")
        info = ttk.Label(
            tab,
            text="Enter the arguments after git, for example: reflog --date=iso. Commands are passed directly to Git without PowerShell or cmd; potentially destructive commands require confirmation.",
        )
        info.pack(fill=tk.X, pady=(0, 5))
        command_frame = ttk.Frame(tab)
        command_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(command_frame, text="git ").pack(side=tk.LEFT)
        self.console_command_var = tk.StringVar()
        command_entry = ttk.Entry(command_frame, textvariable=self.console_command_var)
        command_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        command_entry.bind("<Return>", lambda _event: self.run_console_command())
        ttk.Button(command_frame, text="Run", command=self.run_console_command).pack(side=tk.LEFT, padx=(5, 2))
        ttk.Button(command_frame, text="Clear output", command=lambda: self._set_text(self.console_text, "")).pack(side=tk.LEFT, padx=2)
        self.console_text = self._text(tab)

    def _build_advanced_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Advanced")
        file_frame = ttk.LabelFrame(tab, text="File history and conflicts", padding=5)
        file_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(file_frame, text="Repository-relative path:").pack(side=tk.LEFT)
        self.advanced_path_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.advanced_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 5))
        for text, command in (
            ("Select file", self.choose_advanced_file),
            ("File history", self.show_file_history),
            ("Blame", self.show_blame),
            ("Conflict list", self.show_conflicts),
            ("Use ours", lambda: self.resolve_conflict("--ours")),
            ("Use theirs", lambda: self.resolve_conflict("--theirs")),
            ("Mark resolved", self.mark_resolved),
        ):
            ttk.Button(file_frame, text=text, command=command).pack(side=tk.LEFT, padx=2)
        action_frame = ttk.LabelFrame(tab, text="Repository tools", padding=5)
        action_frame.pack(fill=tk.X, pady=(0, 6))
        for text, command in (
            ("Reflog", self.show_reflog),
            ("Export patch", self.export_patch),
            ("Apply patch", self.apply_patch_file),
            ("Submodules", self.show_submodules),
            ("Initialise/update submodules", self.update_submodules),
            ("Full status", self.show_full_status),
        ):
            ttk.Button(action_frame, text=text, command=command).pack(side=tk.LEFT, padx=2)
        self.advanced_text = self._text(tab)

    def choose_repository(self) -> None:
        initial = self.path_var.get() or str(Path.home())
        selected = filedialog.askdirectory(title="Select a Git repository", initialdir=initial, mustexist=True)
        if selected:
            self.open_repository(selected)

    def initialise_repository(self) -> None:
        selected = filedialog.askdirectory(title="Select a directory to initialise", initialdir=str(Path.home()), mustexist=True)
        if not selected:
            return

        def finished(result: GitResult) -> None:
            self._append_console(result)
            self.open_repository(selected)

        self._submit("Initialising repository...", lambda: run_git(selected, ["init", "-b", "main"]), finished)

    def clone_repository(self) -> None:
        url = simpledialog.askstring(APP_NAME, "Repository URL:", parent=self)
        if not url:
            return
        parent = filedialog.askdirectory(title="Select the parent directory for the clone", initialdir=str(Path.home()), mustexist=True)
        if not parent:
            return
        suggested = Path(url.rstrip("/")).name.removesuffix(".git") or "repository"
        name = simpledialog.askstring(APP_NAME, "Destination directory name:", initialvalue=suggested, parent=self)
        if not name:
            return
        target = Path(parent) / name
        if target.exists():
            messagebox.showerror(APP_NAME, f"The destination already exists:\n{target}", parent=self)
            return

        def finished(result: GitResult) -> None:
            self._append_console(result)
            self.open_repository(str(target))

        self._submit("Cloning repository...", lambda: run_git(parent, ["clone", "--", url, name], timeout=1800), finished)

    def open_repository(self, path: str) -> None:
        self.app.open_repository(path)

    def _repository_opened(self, repository: Path) -> None:
        if self.repository is not None and self.repository != repository:
            raise ValueError("An open repository session cannot be reassigned to another project.")
        self.repository = repository
        self.path_var.set(str(repository))
        self.refresh_all()

    def require_repository(self) -> Path | None:
        if self.repository is None:
            messagebox.showinfo(APP_NAME, "Select a Git repository first.", parent=self)
        return self.repository

    def _collect_repository_state(self) -> dict[str, object]:
        assert self.repository is not None
        repository = self.repository
        commands: dict[str, tuple[list[str], bool]] = {
            "branches": (["for-each-ref", f"--format={branch_format()}", "refs/heads", "refs/remotes"], False),
            "status": (["status", "--porcelain=v1", "-z", "--untracked-files=all"], True),
            "worktrees": (["worktree", "list", "--porcelain"], True),
            "stashes": (["stash", "list", f"--format={stash_format()}", "--date=iso"], True),
            "remotes": (["remote", "-v"], False),
            "tags": (["for-each-ref", "--sort=-creatordate", "--format=%(refname:short)\x1f%(objectname:short)\x1f%(creatordate:iso8601)\x1f%(subject)", "refs/tags"], True),
            "head": (["rev-parse", "--short", "HEAD"], False),
            "branch": (["branch", "--show-current"], False),
            "upstream": (["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], False),
        }
        with ThreadPoolExecutor(max_workers=min(8, len(commands)), thread_name_prefix="litegit-refresh") as executor:
            futures = {
                key: executor.submit(run_git, repository, arguments, check=check)
                for key, (arguments, check) in commands.items()
            }
            results = {key: future.result() for key, future in futures.items()}
        branch_result = results["branches"]
        if branch_result.returncode:
            branch_result = run_git(
                repository,
                ["for-each-ref", f"--format={branch_format(include_divergence=False)}", "refs/heads", "refs/remotes"],
            )
        branches = parse_branches(branch_result.output)
        upstream_result = results["upstream"]
        upstream = upstream_result.output.strip() if upstream_result.returncode == 0 else "Not set"
        ahead, behind = "-", "-"
        if upstream_result.returncode == 0:
            counts = run_git(repository, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], check=False).output.split()
            if len(counts) == 2:
                ahead, behind = counts
        return {
            "branches": branches,
            "status": parse_status_porcelain(results["status"].output),
            "worktrees": parse_worktree_porcelain(results["worktrees"].output),
            "stashes": parse_stashes(results["stashes"].output),
            "remotes": parse_remotes(results["remotes"].output),
            "tags": results["tags"].output,
            "head": results["head"].output.strip(),
            "branch": results["branch"].output.strip() or "(detached HEAD)",
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
        }

    def refresh_all(self) -> None:
        if not self.require_repository():
            return
        self._submit("Refreshing repository state...", self._collect_repository_state, self._render_repository_state, channel="state")

    def _render_repository_state(self, state: dict[str, object]) -> None:
        self.branches = list(state["branches"])  # type: ignore[arg-type]
        self.status_entries = list(state["status"])  # type: ignore[arg-type]
        self.worktrees = list(state["worktrees"])  # type: ignore[arg-type]
        if self.repository and self.worktrees:
            self.app.remember_main_worktree(self.repository, Path(self.worktrees[0].path))
        self.stashes = list(state["stashes"])  # type: ignore[arg-type]
        self.remotes = list(state["remotes"])  # type: ignore[arg-type]
        self.summary_vars["branch"].set(str(state["branch"]))
        self.summary_vars["head"].set(str(state["head"]) or "Empty repository")
        self.summary_vars["upstream"].set(str(state["upstream"]))
        self.summary_vars["sync"].set(f"Ahead {state['ahead']} / behind {state['behind']}")
        self.summary_vars["state"].set("Clean" if not self.status_entries else f"{len(self.status_entries)} changes")
        self.summary_vars["worktrees"].set(str(len(self.worktrees)))
        self.render_branches()
        self._render_changes()
        self._render_worktrees()
        self._render_stashes()
        self._render_remotes()
        self._render_tags(str(state["tags"]))
        branch_names = [branch.name for branch in self.branches if branch.kind == "Local"]
        all_refs = branch_names + [branch.name for branch in self.branches if branch.kind == "Remote"]
        self.history_ref_combo["values"] = ["HEAD", "--all", *all_refs]
        self.compare_base_combo["values"] = all_refs
        self.compare_target_combo["values"] = ["HEAD", *all_refs]
        if self.compare_base_var.get() not in all_refs and branch_names:
            preferred = next((name for name in ("main", "master", "develop") if name in branch_names), branch_names[0])
            self.compare_base_var.set(preferred)
        self.refresh_history()

    def render_branches(self) -> None:
        selection = {tuple(self.branch_tree.item(item, "values")[1:3]) for item in self.branch_tree.selection()}
        self._clear_tree(self.branch_tree)
        needle = self.branch_filter_var.get().casefold().strip()
        for branch in self.branches:
            if needle and needle not in branch.name.casefold() and needle not in branch.subject.casefold():
                continue
            item = self.branch_tree.insert(
                "",
                tk.END,
                values=("*" if branch.current else "", branch.kind, branch.name, branch.short_hash, branch.upstream, branch.tracking, branch.head_only, branch.branch_only, branch.date, branch.author, branch.subject),
            )
            if (branch.kind, branch.name) in selection:
                self.branch_tree.selection_set(item)

    def _render_changes(self) -> None:
        selected_paths = {self._change_row_paths.get(item) for item in self.change_tree.selection()}
        self._clear_tree(self.change_tree)
        self._change_row_paths = {}
        for index, entry in enumerate(self.status_entries):
            code = f"{entry.index}{entry.worktree}"
            display_path = f"{entry.original_path} -> {entry.path}" if entry.original_path else entry.path
            self.change_tree.insert("", tk.END, iid=str(index), values=(code, entry.state, display_path))
            self._change_row_paths[str(index)] = entry.path
            if entry.path in selected_paths:
                self.change_tree.selection_add(str(index))
        if not self.status_entries:
            self.diff_preview.message("The working tree is clean.")
        elif not self.change_tree.selection():
            self.diff_preview.message("Select a changed file to compare its versions.")

    def _render_worktrees(self) -> None:
        self._clear_tree(self.worktree_tree)
        for entry in self.worktrees:
            branch = entry.branch or "(detached)"
            self.worktree_tree.insert("", tk.END, values=(entry.path, branch, entry.head[:12], "Yes" if entry.detached else "", entry.locked, entry.prunable))

    def _render_stashes(self) -> None:
        self._clear_tree(self.stash_tree)
        for stash in self.stashes:
            self.stash_tree.insert("", tk.END, values=(stash.reference, stash.date, stash.subject))

    def _render_remotes(self) -> None:
        self._clear_tree(self.remote_tree)
        for remote in self.remotes:
            self.remote_tree.insert("", tk.END, values=(remote.name, remote.fetch_url, remote.push_url))

    def _render_tags(self, output: str) -> None:
        self._clear_tree(self.tag_tree)
        for line in output.splitlines():
            fields = line.split("\x1f")
            fields.extend([""] * (4 - len(fields)))
            self.tag_tree.insert("", tk.END, values=fields[:4])

    def refresh_history(self) -> None:
        if not self.repository:
            return
        reference = self.history_ref_var.get().strip() or "HEAD"
        try:
            limit = max(1, min(5000, int(self.history_limit_var.get())))
        except (ValueError, tk.TclError):
            limit = 200
        arguments = ["log", f"--max-count={limit}", "--date=iso", f"--pretty=format:{commit_format()}"]
        if reference == "--all":
            arguments.append("--all")
        else:
            arguments.append(reference)
        repository = self.repository

        def collect() -> tuple[list[CommitEntry], set[str]]:
            commits = parse_commits(run_git(repository, arguments).output)
            remote_hashes = find_remote_reachable_commits(repository, [commit.full_hash for commit in commits])
            return commits, remote_hashes

        self._submit("Reading commit history...", collect, self._render_history, channel="history")

    def _render_history(self, result: tuple[list[CommitEntry], set[str]]) -> None:
        commits, remote_hashes = result
        self.commits = commits
        self.remote_commit_hashes = remote_hashes
        self._clear_tree(self.commit_tree)
        for index, commit in enumerate(commits):
            pushed = commit.full_hash in remote_hashes
            self.commit_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=("Published remotely" if pushed else "Local only", commit.short_hash, commit.date, commit.author, commit.decorations, commit.subject),
                tags=("remote" if pushed else "local_only",),
            )
        self.edit_message_button.configure(state=tk.DISABLED)

    def _on_commit_selection(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        commit = self.selected_commit(show_prompt=False)
        editable = bool(commit and is_commit_message_editable(commit.full_hash, self.remote_commit_hashes))
        self.edit_message_button.configure(state=tk.NORMAL if editable else tk.DISABLED)
        if commit:
            self.show_commit(show_prompt=False)

    def _on_commit_double_click(self, event: tk.Event[tk.Misc]) -> None:
        row = self.commit_tree.identify_row(event.y)
        column = self.commit_tree.identify_column(event.x)
        if not row or column != "#6":
            return
        self.commit_tree.selection_set(row)
        self.commit_tree.focus(row)
        self.edit_commit_message()

    def _on_branch_context_menu(self, event: tk.Event) -> str:
        row = context_row(self.branch_tree, event)
        if not row:
            return "break"
        branch = self.selected_branch()
        if branch is None:
            return "break"
        local = branch.kind == "Local"
        for label in ("Update branch from upstream", "Set upstream...", "Rename..."):
            self.branch_menu.entryconfigure(label, state=tk.NORMAL if local else tk.DISABLED)
        self.branch_menu.entryconfigure("Check out", state=tk.DISABLED if branch.current else tk.NORMAL)
        self.branch_menu.entryconfigure("Delete local branch...", state=tk.NORMAL if local and not branch.current else tk.DISABLED)
        self.branch_menu.entryconfigure("Delete remote branch...", state=tk.NORMAL if not local and not branch.name.endswith("/HEAD") else tk.DISABLED)
        for label in ("Merge into current branch...", "Rebase current branch..."):
            self.branch_menu.entryconfigure(label, state=tk.DISABLED if branch.current else tk.NORMAL)
        popup_menu(self.branch_tree, self.branch_menu, event, row)
        return "break"

    def _on_commit_context_menu(self, event: tk.Event) -> str:
        row = context_row(self.commit_tree, event)
        if not row:
            return "break"
        commit = self.selected_commit(show_prompt=False)
        if commit is not None:
            editable = is_commit_message_editable(commit.full_hash, self.remote_commit_hashes)
            self.commit_menu.entryconfigure("Edit message...", state=tk.NORMAL if editable else tk.DISABLED)
            popup_menu(self.commit_tree, self.commit_menu, event, row)
        return "break"

    def selected_branch(self, *, local_only: bool = False) -> BranchEntry | None:
        selected = self.branch_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a branch first.", parent=self)
            return None
        values = self.branch_tree.item(selected[0], "values")
        name = str(values[2])
        branch = next((item for item in self.branches if item.name == name and item.kind == str(values[1])), None)
        if branch and local_only and branch.kind != "Local":
            messagebox.showinfo(APP_NAME, "This operation requires a local branch.", parent=self)
            return None
        return branch

    def selected_commit(self, *, show_prompt: bool = True) -> CommitEntry | None:
        selected = self.commit_tree.selection()
        if not selected:
            if show_prompt:
                messagebox.showinfo(APP_NAME, "Select a commit first.", parent=self)
            return None
        try:
            return self.commits[int(selected[0])]
        except (ValueError, IndexError):
            return None

    def selected_status_entries(self) -> list[StatusEntry]:
        entries: list[StatusEntry] = []
        for item in self.change_tree.selection():
            try:
                entries.append(self.status_entries[int(item)])
            except (ValueError, IndexError):
                continue
        if not entries:
            messagebox.showinfo(APP_NAME, "Select at least one file first.", parent=self)
        return entries

    def selected_worktree(self) -> WorktreeEntry | None:
        selected = self.worktree_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a worktree first.", parent=self)
            return None
        path = str(self.worktree_tree.item(selected[0], "values")[0])
        return next((item for item in self.worktrees if item.path == path), None)

    def checkout_branch(self) -> None:
        branch = self.selected_branch()
        if not branch:
            return
        if branch.kind == "Remote":
            remote_parts = branch.name.split("/", 1)
            suggested = remote_parts[1] if len(remote_parts) == 2 else branch.name
            local_name = simpledialog.askstring(APP_NAME, "Local branch name:", initialvalue=suggested, parent=self)
            if local_name:
                self.execute_git(["switch", "--track", "-c", local_name, branch.name], refresh=True)
        else:
            self.execute_git(["switch", branch.name], refresh=True)

    def create_branch(self) -> None:
        name = simpledialog.askstring(APP_NAME, "New branch name:", parent=self)
        if not name:
            return
        start = simpledialog.askstring(APP_NAME, "Starting point (HEAD by default):", initialvalue="HEAD", parent=self) or "HEAD"
        self.execute_git(["switch", "-c", name, start], refresh=True)

    def rename_branch(self) -> None:
        branch = self.selected_branch(local_only=True)
        if not branch:
            return
        name = simpledialog.askstring(APP_NAME, "New branch name:", initialvalue=branch.name, parent=self)
        if name and name != branch.name:
            self.execute_git(["branch", "-m", branch.name, name], refresh=True)

    def delete_branch(self) -> None:
        branch = self.selected_branch(local_only=True)
        if not branch:
            return
        force = messagebox.askyesnocancel(
            APP_NAME,
            f"Delete local branch {branch.name}?\n\nYes: force deletion. No: delete only if merged. Cancel: do nothing.",
            parent=self,
        )
        if force is None:
            return
        self.execute_git(["branch", "-D" if force else "-d", branch.name], refresh=True, confirm_destructive=force)

    def delete_remote_branch(self) -> None:
        branch = self.selected_branch()
        if not branch or branch.kind != "Remote":
            messagebox.showinfo(APP_NAME, "Select a remote branch.", parent=self)
            return
        if branch.name.endswith("/HEAD"):
            messagebox.showinfo(APP_NAME, "The remote HEAD symbolic reference cannot be deleted.", parent=self)
            return
        remote, separator, remote_branch = branch.name.partition("/")
        if not separator or not remote_branch:
            return
        if messagebox.askyesno(APP_NAME, f"Permanently delete remote branch {remote_branch} from {remote}?", icon="warning", parent=self):
            self.execute_git(["push", remote, "--delete", remote_branch], refresh=True)

    def merge_selected_branch(self) -> None:
        branch = self.selected_branch()
        if branch and messagebox.askyesno(
            APP_NAME,
            f"Merge {branch.name} into the current branch {self.summary_vars['branch'].get()}?",
            parent=self,
        ):
            self.execute_git(["merge", "--no-edit", branch.name], refresh=True)

    def abort_merge(self) -> None:
        if messagebox.askyesno(APP_NAME, "Abort the merge currently in progress?", icon="warning", parent=self):
            self.execute_git(["merge", "--abort"], refresh=True, confirm_destructive=False)

    def update_selected_branch(self) -> None:
        branch = self.selected_branch(local_only=True)
        if not branch or not self.repository:
            return
        if branch.current:
            self.pull()
            return
        if not messagebox.askyesno(APP_NAME, f"Fetch the upstream of {branch.name} and update that local branch?\n\nYour current branch and uncommitted files will be preserved. Diverged history will be merged in a separate worktree.", parent=self):
            return
        repository = self.repository

        def finished(result: BranchUpdateResult) -> None:
            self.refresh_all()
            if result.pending:
                self.open_conflicts(result.pending.worktree)
            else:
                messagebox.showinfo(APP_NAME, f"{result.branch}: {result.outcome}", parent=self)

        def failed(_error: Exception) -> None:
            self.open_conflicts()

        self._submit(f"Updating {branch.name}...", lambda: update_branch(repository, branch.name), finished, on_error=failed)

    def open_conflicts(self, target: str | None = None) -> None:
        if not self.repository:
            return
        if self._conflict_manager and self._conflict_manager.winfo_exists():
            self._conflict_manager.refresh(target)
            self._conflict_manager.lift()
        else:
            self._conflict_manager = ConflictManager(self, self.repository, target)

    def open_selected_conflict(self) -> None:
        selected = self.change_tree.selection()
        if selected and self.repository:
            entry = self.status_entries[int(selected[0])]
            if entry.index + entry.worktree in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
                self.open_conflicts()

    def rebase_selected_branch(self) -> None:
        branch = self.selected_branch()
        if branch and messagebox.askyesno(APP_NAME, f"Rebase the current branch onto {branch.name}?", parent=self):
            self.execute_git(["rebase", branch.name], refresh=True)

    def set_upstream(self) -> None:
        branch = self.selected_branch(local_only=True)
        if not branch:
            return
        upstream = simpledialog.askstring(APP_NAME, "Upstream branch:", initialvalue=f"origin/{branch.name}", parent=self)
        if upstream:
            self.execute_git(["branch", "--set-upstream-to", upstream, branch.name], refresh=True)

    def show_branch_summary(self) -> None:
        selected = self.branch_tree.selection()
        if selected:
            values = self.branch_tree.item(selected[0], "values")
            self.compare_target_var.set(str(values[2]))

    def show_commit(self, *, show_prompt: bool = True) -> None:
        commit = self.selected_commit(show_prompt=show_prompt)
        if commit and self.repository:
            self._submit(
                "Reading commit...",
                lambda: run_git(self.repository, ["show", "--stat", "--patch", "--find-renames", commit.full_hash]).output,
                lambda output: self._set_text(self.commit_detail_text, output),
            )

    def copy_commit_hash(self) -> None:
        commit = self.selected_commit()
        if commit:
            self.clipboard_clear()
            self.clipboard_append(commit.full_hash)
            self.status_var.set(f"Copied {commit.full_hash}")

    def edit_commit_message(self) -> None:
        commit = self.selected_commit()
        if not commit or not self.repository:
            return
        if not is_commit_message_editable(commit.full_hash, self.remote_commit_hashes):
            messagebox.showinfo(
                APP_NAME,
                "This commit is reachable from a remote-tracking branch. Its message cannot be edited because that would rewrite published history.",
                parent=self,
            )
            return
        repository = self.repository

        def inspect() -> dict[str, object]:
            message = run_git(repository, ["show", "-s", "--format=%B", commit.full_hash]).output.rstrip()
            head = run_git(repository, ["rev-parse", "HEAD"]).output.strip()
            ancestor = run_git(repository, ["merge-base", "--is-ancestor", commit.full_hash, "HEAD"], check=False).returncode == 0
            remote_refs = run_git(repository, ["branch", "-r", "--contains", commit.full_hash], check=False).output.strip()
            parent = run_git(repository, ["rev-parse", f"{commit.full_hash}^"], check=False)
            return {
                "message": message,
                "head": head,
                "ancestor": ancestor,
                "published": bool(remote_refs),
                "root": parent.returncode != 0,
            }

        def inspected(details: dict[str, object]) -> None:
            if not bool(details["ancestor"]):
                messagebox.showerror(
                    APP_NAME,
                    "Only commits in the current branch history can be edited. Check out a local branch containing this commit first.",
                    parent=self,
                )
                return
            if bool(details["published"]):
                self.remote_commit_hashes.add(commit.full_hash)
                self.edit_message_button.configure(state=tk.DISABLED)
                messagebox.showinfo(
                    APP_NAME,
                    "This commit is reachable from a remote-tracking branch. Its message cannot be edited because that would rewrite published history.\n\nRefresh the history to update its colour and state.",
                    parent=self,
                )
                return
            original = str(details["message"])
            replacement = CommitMessageDialog.ask(self, original)
            if replacement is None or replacement == original.strip():
                return
            is_head = commit.full_hash == str(details["head"])
            warning = (
                "This rewrites HEAD and creates a new commit hash.\nCurrently staged changes will not be added to the commit."
                if is_head
                else "This uses an interactive rebase to rewrite this commit and every later commit hash.\nWorking-tree changes will be protected with autostash."
            )
            if not messagebox.askyesno(APP_NAME, f"{warning}\n\nEdit the commit message?", icon="warning", parent=self):
                return
            if is_head:
                self.execute_git(
                    ["commit", "--amend", "--only", "-m", replacement],
                    refresh=True,
                    confirm_destructive=False,
                )
                return
            environment = build_reword_environment(commit.full_hash, replacement)
            rebase_base = "--root" if bool(details["root"]) else f"{commit.full_hash}^"
            self.execute_git(
                ["rebase", "--interactive", "--rebase-merges", "--autostash", rebase_base],
                refresh=True,
                confirm_destructive=False,
                environment_overrides=environment,
            )

        self._submit("Checking commit position...", inspect, inspected)

    def cherry_pick(self) -> None:
        commit = self.selected_commit()
        if commit and messagebox.askyesno(APP_NAME, f"Cherry-pick {commit.short_hash}?", parent=self):
            self.execute_git(["cherry-pick", commit.full_hash], refresh=True)

    def revert_commit(self) -> None:
        commit = self.selected_commit()
        if commit and messagebox.askyesno(APP_NAME, f"Create a reverse commit to revert {commit.short_hash}?", parent=self):
            self.execute_git(["revert", "--no-edit", commit.full_hash], refresh=True)

    def create_tag_at_commit(self) -> None:
        commit = self.selected_commit()
        if commit:
            self._prompt_create_tag(commit.full_hash)

    def show_selected_diff(self) -> None:
        self._request_serials["diff"] = self._request_serials.get("diff", 0) + 1
        selected = self.change_tree.selection()
        if not selected or not self.repository:
            return
        try:
            entry = self.status_entries[int(selected[0])]
        except (ValueError, IndexError):
            return
        repository, scope = self.repository, self.diff_scope_var.get()
        self.diff_preview.message("Loading " + entry.path + "...")

        def collect() -> tuple[FileVersions | DirectoryPreview | None, list[DiffRow] | None, str]:
            try:
                versions = read_file_versions(repository, entry, scope)
                rows = aligned_diff(versions.before, versions.after) if isinstance(versions, FileVersions) else None
                return versions, rows, ""
            except (ValueError, OSError) as exc:
                return None, None, str(exc)

        def render(result: tuple[FileVersions | DirectoryPreview | None, list[DiffRow] | None, str]) -> None:
            versions, rows, error = result
            if error:
                self.diff_preview.message(error)
            elif isinstance(versions, DirectoryPreview):
                self.diff_preview.render_directory(versions)
            elif versions is not None:
                self.diff_preview.render(versions, entry.path, rows=rows)

        self._submit(
            "Reading differences...",
            collect, render, channel="diff",
        )

    def stage_selected(self) -> None:
        entries = self.selected_status_entries()
        if entries:
            self.execute_git(["add", "--", *[entry.path for entry in entries]], refresh=True)

    def unstage_selected(self) -> None:
        entries = self.selected_status_entries()
        if entries:
            self.execute_git(["reset", "HEAD", "--", *[entry.path for entry in entries]], refresh=True)

    def discard_selected(self) -> None:
        entries = self.selected_status_entries()
        if not entries:
            return
        paths = [entry.path for entry in entries]
        if not messagebox.askyesno(APP_NAME, "Permanently discard uncommitted changes in the selected files?\n\n" + "\n".join(paths[:12]), icon="warning", parent=self):
            return
        tracked = [entry.path for entry in entries if not (entry.index == "?" and entry.worktree == "?")]
        untracked = [entry.path for entry in entries if entry.index == "?" and entry.worktree == "?"]
        commands: list[list[str]] = []
        if tracked:
            commands.append(["restore", "--staged", "--worktree", "--", *tracked])
        if untracked:
            commands.append(["clean", "-f", "--", *untracked])
        self.execute_sequence(commands, refresh=True, confirmed=True)

    def commit_staged(self) -> None:
        message = self.commit_message_var.get().strip()
        if not message and not self.amend_var.get():
            messagebox.showinfo(APP_NAME, "Enter a commit message.", parent=self)
            return
        arguments = ["commit"]
        if self.amend_var.get():
            arguments.append("--amend")
        if message:
            arguments.extend(["-m", message])
        else:
            arguments.append("--no-edit")
        self.execute_git(arguments, refresh=True, on_success=lambda: self.commit_message_var.set(""))

    def refresh_compare(self, three_dot: bool) -> None:
        if not self.repository:
            return
        base = self.compare_base_var.get().strip()
        target = self.compare_target_var.get().strip()
        if not base or not target:
            return
        revision = f"{base}...{target}" if three_dot else f"{base}..{target}"

        def collect() -> tuple[str, str]:
            counts = run_git(self.repository, ["rev-list", "--left-right", "--count", f"{base}...{target}"], check=False).output.split()
            summary = f"{base} has {counts[0] if len(counts) > 0 else '?'} unique commits; {target} has {counts[1] if len(counts) > 1 else '?'} unique commits"
            diff = run_git(self.repository, ["diff", "--find-renames", "--stat", "--patch", revision]).output
            return summary, diff

        self._submit("Comparing branches...", collect, lambda result: (self.compare_summary_var.set(result[0]), self._set_text(self.compare_text, result[1] or "There are no differences.")))

    def refresh_compare_stat(self) -> None:
        if not self.repository:
            return
        base = self.compare_base_var.get().strip()
        target = self.compare_target_var.get().strip()
        self._submit(
            "Calculating difference statistics...",
            lambda: run_git(self.repository, ["diff", "--stat", f"{base}...{target}"]).output,
            lambda output: self._set_text(self.compare_text, output or "There are no differences."),
        )

    def add_worktree(self) -> None:
        if not self.repository:
            return
        selected = filedialog.askdirectory(title="Select the parent directory for the new worktree", initialdir=str(self.repository.parent), mustexist=True)
        if not selected:
            return
        directory_name = simpledialog.askstring(APP_NAME, "New worktree directory name:", initialvalue=f"{self.repository.name}-worktree", parent=self)
        if not directory_name:
            return
        target = str(Path(selected) / directory_name)
        branch = simpledialog.askstring(APP_NAME, "Existing branch name, or a new branch to create:", parent=self)
        if not branch:
            return
        existing = any(item.name == branch and item.kind == "Local" for item in self.branches)
        arguments = ["worktree", "add", target, branch] if existing else ["worktree", "add", "-b", branch, target, "HEAD"]
        self.execute_git(arguments, refresh=True)

    def open_worktree_folder(self) -> None:
        worktree = self.selected_worktree()
        if not worktree:
            return
        try:
            if os.name == "nt":
                prepare_external_processes()
                os.startfile(worktree.path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                launch_external(["open", worktree.path])
            else:
                launch_external(["xdg-open", worktree.path])
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def worktree_action(self, action: str) -> None:
        worktree = self.selected_worktree()
        if worktree:
            self.execute_git(["worktree", action, worktree.path], refresh=True)

    def remove_worktree(self) -> None:
        worktree = self.selected_worktree()
        if worktree and messagebox.askyesno(APP_NAME, f"Remove this worktree?\n{worktree.path}", icon="warning", parent=self):
            self.execute_git(["worktree", "remove", worktree.path], refresh=True, confirm_destructive=False)

    def prune_worktrees(self) -> None:
        if messagebox.askyesno(APP_NAME, "Prune metadata for worktrees that no longer exist?", parent=self):
            self.execute_git(["worktree", "prune", "--verbose"], refresh=True, confirm_destructive=False)

    def create_stash(self) -> None:
        message = simpledialog.askstring(APP_NAME, "Stash description:", parent=self)
        arguments = ["stash", "push", "--include-untracked"]
        if message:
            arguments.extend(["-m", message])
        self.execute_git(arguments, refresh=True)

    def selected_stash(self) -> str | None:
        selected = self.stash_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a stash first.", parent=self)
            return None
        return str(self.stash_tree.item(selected[0], "values")[0])

    def stash_action(self, action: str) -> None:
        reference = self.selected_stash()
        if not reference:
            return
        if action == "drop" and not messagebox.askyesno(APP_NAME, f"Permanently delete {reference}?", icon="warning", parent=self):
            return
        self.execute_git(["stash", action, reference], refresh=True, confirm_destructive=False if action == "drop" else None)

    def show_stash(self) -> None:
        reference = self.selected_stash()
        if reference and self.repository:
            self._submit("Reading stash...", lambda: run_git(self.repository, ["stash", "show", "--stat", "--patch", reference]).output, lambda output: self._show_output("Stash", output))

    def create_tag(self) -> None:
        self._prompt_create_tag("HEAD")

    def _prompt_create_tag(self, target: str) -> None:
        name = simpledialog.askstring(APP_NAME, "Tag name:", parent=self)
        if not name:
            return
        annotation = simpledialog.askstring(APP_NAME, "Annotation (leave blank for a lightweight tag):", parent=self)
        arguments = ["tag"]
        if annotation:
            arguments.extend(["-a", name, "-m", annotation, target])
        else:
            arguments.extend([name, target])
        self.execute_git(arguments, refresh=True)

    def selected_tag(self) -> str | None:
        selected = self.tag_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a tag first.", parent=self)
            return None
        return str(self.tag_tree.item(selected[0], "values")[0])

    def delete_tag(self) -> None:
        tag = self.selected_tag()
        if tag and messagebox.askyesno(APP_NAME, f"Delete local tag {tag}?", parent=self):
            self.execute_git(["tag", "-d", tag], refresh=True)

    def push_tag(self) -> None:
        tag = self.selected_tag()
        if tag:
            remote = self._choose_remote()
            if remote:
                self.execute_git(["push", remote, f"refs/tags/{tag}"], refresh=True)

    def add_remote(self) -> None:
        name = simpledialog.askstring(APP_NAME, "Remote name:", initialvalue="origin", parent=self)
        if not name:
            return
        url = simpledialog.askstring(APP_NAME, "Remote URL:", parent=self)
        if url:
            self.execute_git(["remote", "add", name, url], refresh=True)

    def selected_remote(self) -> RemoteEntry | None:
        selected = self.remote_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a remote first.", parent=self)
            return None
        name = str(self.remote_tree.item(selected[0], "values")[0])
        return next((remote for remote in self.remotes if remote.name == name), None)

    def edit_remote(self) -> None:
        remote = self.selected_remote()
        if not remote:
            return
        url = simpledialog.askstring(APP_NAME, "New fetch/push URL:", initialvalue=remote.fetch_url, parent=self)
        if url:
            self.execute_git(["remote", "set-url", remote.name, url], refresh=True)

    def delete_remote(self) -> None:
        remote = self.selected_remote()
        if remote and messagebox.askyesno(APP_NAME, f"Delete remote {remote.name}?", parent=self):
            self.execute_git(["remote", "remove", remote.name], refresh=True)

    def _choose_remote(self) -> str | None:
        names = [remote.name for remote in self.remotes]
        default = "origin" if "origin" in names else (names[0] if names else "origin")
        return simpledialog.askstring(APP_NAME, "Remote name:", initialvalue=default, parent=self)

    def pull(self) -> None:
        self.execute_git(["pull", "--no-rebase", "--no-edit"], refresh=True)

    def test_origin_authentication(self) -> None:
        if not any(remote.name == "origin" for remote in self.remotes):
            messagebox.showinfo(APP_NAME, "This repository has no origin remote.", parent=self)
            return
        self.execute_git(["ls-remote", "--heads", "origin"], refresh=False)

    def push(self) -> None:
        branch = self.summary_vars["branch"].get()
        if branch in {"-", "(detached HEAD)"}:
            messagebox.showinfo(APP_NAME, "A detached HEAD cannot be pushed as a branch.", parent=self)
            return
        arguments = ["push"]
        if self.force_with_lease_var.get():
            if not messagebox.askyesno(APP_NAME, "Push with --force-with-lease?\nThis may rewrite remote branch history.", icon="warning", parent=self):
                return
            arguments.append("--force-with-lease")
        if self.summary_vars["upstream"].get() == "Not set" and self.set_upstream_push_var.get():
            remote = self._choose_remote()
            if not remote:
                return
            arguments.extend(["--set-upstream", remote, branch])
        self.execute_git(arguments, refresh=True)

    def choose_advanced_file(self) -> None:
        if not self.repository:
            return
        selected = filedialog.askopenfilename(title="Select a file in the repository", initialdir=str(self.repository))
        if not selected:
            return
        try:
            relative = Path(selected).resolve().relative_to(self.repository.resolve())
        except ValueError:
            messagebox.showerror(APP_NAME, "The selected file is outside the current repository.", parent=self)
            return
        self.advanced_path_var.set(relative.as_posix())

    def _advanced_path(self) -> str | None:
        path = self.advanced_path_var.get().strip()
        if not path:
            messagebox.showinfo(APP_NAME, "Enter or select a repository-relative path first.", parent=self)
            return None
        if Path(path).is_absolute() or ".." in Path(path).parts:
            messagebox.showerror(APP_NAME, "The path must be relative to the repository.", parent=self)
            return None
        return path

    def show_file_history(self) -> None:
        path = self._advanced_path()
        if path and self.repository:
            self._submit(
                "Reading file history...",
                lambda: run_git(self.repository, ["log", "--follow", "--date=iso", "--stat", "--", path]).output,
                lambda output: self._set_text(self.advanced_text, output or "There is no file history."),
            )

    def show_blame(self) -> None:
        path = self._advanced_path()
        if path and self.repository:
            self._submit(
                "Reading blame information...",
                lambda: run_git(self.repository, ["blame", "--date=short", "--", path]).output,
                lambda output: self._set_text(self.advanced_text, output),
            )

    def show_conflicts(self) -> None:
        if self.repository:
            self._submit(
                "Checking conflicts...",
                lambda: run_git(self.repository, ["diff", "--name-status", "--diff-filter=U"], check=False).output,
                lambda output: self._set_text(self.advanced_text, output or "There are no unresolved conflicts."),
            )

    def resolve_conflict(self, side: str) -> None:
        path = self._advanced_path()
        if not path:
            return
        label = "ours (current branch)" if side == "--ours" else "theirs (incoming change)"
        if messagebox.askyesno(APP_NAME, f"Replace the file with {label} and stage it?\n{path}", icon="warning", parent=self):
            self.execute_sequence([["checkout", side, "--", path], ["add", "--", path]], refresh=True, confirmed=True)

    def mark_resolved(self) -> None:
        path = self._advanced_path()
        if path:
            self.execute_git(["add", "--", path], refresh=True)

    def show_reflog(self) -> None:
        if self.repository:
            self._submit(
                "Reading reflog...",
                lambda: run_git(self.repository, ["reflog", "--date=iso", "--all"]).output,
                lambda output: self._set_text(self.advanced_text, output or "The reflog is empty."),
            )

    def export_patch(self) -> None:
        if not self.repository:
            return
        selected = filedialog.asksaveasfilename(
            title="Export current changes relative to HEAD",
            initialdir=str(self.repository.parent),
            initialfile=f"{self.repository.name}-changes.patch",
            defaultextension=".patch",
            filetypes=(("Patch", "*.patch"), ("All files", "*.*")),
        )
        if not selected:
            return

        def worker() -> tuple[GitResult, Path]:
            result = run_git(self.repository, ["diff", "--binary", "HEAD"], check=False)
            if result.returncode not in (0, 1):
                raise GitError(result.command, result.returncode, result.output)
            target = Path(selected)
            target.write_text(result.output, encoding="utf-8", newline="\n")
            return result, target

        def finished(value: tuple[GitResult, Path]) -> None:
            result, target = value
            self._append_console(result)
            self.status_var.set(f"Patch saved: {target}")
            messagebox.showinfo(APP_NAME, f"Patch saved:\n{target}", parent=self)

        self._submit("Exporting patch...", worker, finished)

    def apply_patch_file(self) -> None:
        if not self.repository:
            return
        selected = filedialog.askopenfilename(
            title="Select a patch",
            initialdir=str(self.repository),
            filetypes=(("Patch", "*.patch *.diff"), ("All files", "*.*")),
        )
        if selected and messagebox.askyesno(APP_NAME, f"Apply this patch?\n{selected}", parent=self):
            self.execute_git(["apply", "--3way", selected], refresh=True)

    def show_submodules(self) -> None:
        if self.repository:
            self._submit(
                "Reading submodules...",
                lambda: run_git(self.repository, ["submodule", "status", "--recursive"], check=False).output,
                lambda output: self._set_text(self.advanced_text, output or "The repository has no submodules."),
            )

    def update_submodules(self) -> None:
        if messagebox.askyesno(APP_NAME, "Initialise and recursively update all submodules?\nThis operation may access the network.", parent=self):
            self.execute_git(["submodule", "update", "--init", "--recursive"], refresh=True)

    def show_full_status(self) -> None:
        if self.repository:
            self._submit(
                "Reading full status...",
                lambda: run_git(self.repository, ["status", "--branch", "--show-stash"]).output,
                lambda output: self._set_text(self.advanced_text, output),
            )

    def run_console_command(self) -> None:
        try:
            arguments = split_command_line(self.console_command_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        if arguments and arguments[0].lower() == "git":
            arguments = arguments[1:]
        if not arguments:
            return
        self.execute_git(arguments, refresh=True)

    def execute_sequence(self, commands: list[list[str]], *, refresh: bool, confirmed: bool = False) -> None:
        if not self.repository or not commands:
            return
        if not confirmed and any(is_potentially_destructive(command) for command in commands):
            if not messagebox.askyesno(APP_NAME, "The command may delete or overwrite data. Continue?", icon="warning", parent=self):
                return

        repository = self.repository

        def worker() -> list[GitResult]:
            return [run_git(repository, command) for command in commands]

        def finished(results: list[GitResult]) -> None:
            for result in results:
                self._append_console(result)
            if refresh:
                self.refresh_all()

        self._submit("Running Git operations...", worker, finished)

    def execute_git(
        self,
        arguments: Sequence[str],
        *,
        refresh: bool = False,
        confirm_destructive: bool | None = None,
        on_success: Callable[[], None] | None = None,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> None:
        if not self.require_repository():
            return
        potentially_destructive = is_potentially_destructive(arguments)
        should_confirm = potentially_destructive if confirm_destructive is None else confirm_destructive
        if should_confirm:
            if not messagebox.askyesno(
                APP_NAME,
                "The following command may delete, overwrite or rewrite data:\n\n"
                + display_command(["git", *arguments])
                + "\n\nContinue?",
                icon="warning",
                parent=self,
            ):
                return
        repository = self.repository

        def worker() -> GitResult:
            assert repository is not None
            return run_git(repository, list(arguments), environment_overrides=environment_overrides)

        def finished(result: GitResult) -> None:
            self._append_console(result)
            if on_success:
                on_success()
            if refresh:
                self.refresh_all()

        self._submit(f"Running git {arguments[0]}...", worker, finished)

    def _submit(
        self, label: str, worker: Callable[[], object], success: Callable[[object], None], *,
        on_error: Callable[[Exception], None] | None = None, channel: str | None = None,
    ) -> None:
        if self._closing:
            return
        serial = self._request_serials.get(channel, 0) + 1 if channel else 0
        if channel:
            self._request_serials[channel] = serial
        self._busy_count += 1
        if self._busy_count == 1:
            self.progress.start(12)
        self.status_var.set(label)
        future = self.executor.submit(worker)

        def complete() -> None:
            if self._closing:
                return
            self._busy_count = max(0, self._busy_count - 1)
            if self._busy_count == 0:
                self.progress.stop()
            if channel and self._request_serials.get(channel) != serial:
                return
            try:
                value = future.result()
                success(value)
            except GitError as exc:
                safe_error = redact_text(str(exc))
                self.status_var.set(f"Failed: {safe_error}")
                self._append_console_error(exc)
                if on_error:
                    on_error(exc)
                conflicts = False
                if self.repository:
                    try:
                        conflicts = bool(unresolved_paths(self.repository))
                    except GitError:
                        pass
                if conflicts:
                    self.open_conflicts()
                    self.refresh_all()
                else:
                    if self.app.transient_mode:
                        self.app.background_errors.append(safe_error)
                    else:
                        messagebox.showerror(APP_NAME, safe_error, parent=self)
            except Exception as exc:  # UI boundary: always report unexpected failures.
                self.status_var.set(f"Failed: {exc}")
                if on_error:
                    on_error(exc)
                if self.app.transient_mode:
                    self.app.background_errors.append(f"{type(exc).__name__}: {exc}")
                else:
                    messagebox.showerror(APP_NAME, f"{type(exc).__name__}: {exc}", parent=self)
            else:
                self.status_var.set("Completed")

        def poll() -> None:
            if self._closing:
                return
            if future.done():
                complete()
            else:
                self.after(40, poll)

        self.after(40, poll)

    def _append_console(self, result: GitResult) -> None:
        heading = f"> {display_command(result.command)}\n"
        body = redact_text(result.output) if result.output else "(Command completed successfully with no output)\n"
        self.console_text.insert(tk.END, heading + body.rstrip() + "\n\n")
        self.console_text.see(tk.END)

    def _append_console_error(self, error: GitError) -> None:
        heading = f"> {display_command(error.command)}\n"
        self.console_text.insert(tk.END, heading + redact_text(error.output).rstrip() + f"\n[Exit code {error.returncode}]\n\n")
        self.console_text.see(tk.END)

    def _show_output(self, title: str, output: str) -> None:
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("1000x650")
        text = self._text(window)
        self._set_text(text, output)

    @staticmethod
    def _set_text(widget: tk.Text, content: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        items = tree.get_children()
        if items:
            tree.delete(*items)

    @staticmethod
    def _sort_tree(tree: ttk.Treeview, column: str, reverse: bool) -> None:
        values = [(str(tree.set(item, column)).casefold(), item) for item in tree.get_children("")]
        values.sort(reverse=reverse)
        for index, (_value, item) in enumerate(values):
            tree.move(item, "", index)
        tree.heading(column, command=lambda: RepositoryView._sort_tree(tree, column, not reverse))

    def _close(self) -> None:
        self._closing = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


class LiteGitApp(tk.Tk):
    def __init__(self, *, transient: bool = False, restore_projects: bool = True) -> None:
        # Dispose of earlier closed Tk interpreters on the interface thread.
        gc.collect()
        super().__init__()
        startup_phase("tk_created")
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("1500x900")
        self.minsize(1100, 680)
        self.option_add("*tearOff", False)
        self.transient_mode = transient
        self.background_errors: list[str] = []
        self.settings = {} if transient else load_settings()
        startup_phase("settings_loaded")
        self._style_initialised = False
        self._settings_executor = None if transient else ThreadPoolExecutor(max_workers=1, thread_name_prefix="litegit-settings")
        self._settings_future: Future[None] | None = None
        self._settings_save_job: str | None = None
        self._settings_poll_job: str | None = None
        self._settings_revision = 0
        self._settings_inflight_revision = 0
        self._settings_saved_revision = 0
        self.views: dict[str, RepositoryView] = {}
        self._all_views: set[RepositoryView] = set()
        saved = self.settings.get("projects", [])
        self.project_paths: list[str] = []
        if isinstance(saved, list):
            for path in saved:
                if isinstance(path, str) and path.strip() and not any(self._project_key(Path(path)) == self._project_key(Path(existing)) for existing in self.project_paths):
                    self.project_paths.append(str(Path(path).resolve()))
        main_paths = self.settings.get("project_main_paths", {})
        self.project_main_paths = {
            self._project_key(Path(path)): Path(main).resolve()
            for path, main in main_paths.items() if isinstance(path, str) and isinstance(main, str) and path and main
        } if isinstance(main_paths, dict) else {}
        self.sidebar_collapsed = bool(self.settings.get("sidebar_collapsed", False))
        self._open_request = 0
        self._closing = False
        self._suppress_project_selection = False
        self._project_menu_path: str | None = None
        self._project_rows: dict[str, str] = {}
        self._sidebar_rendered_collapsed: bool | None = None
        self._build_project_sidebar()
        self.active_view = self._new_view()
        self._empty_view = self.active_view
        self._place_active_view()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<F5>", lambda _event: self.active_view.refresh_all())
        self.bind("<Control-o>", lambda _event: self.active_view.choose_repository())
        self._render_projects()
        startup_phase("interface_built")
        if self.project_paths and restore_projects:
            self._save_project_state()
            self._refresh_project_labels()
        last = self.settings.get("last_repository", "")
        if restore_projects and isinstance(last, str) and last and Path(last).is_dir():
            self.after(100, lambda: self.open_repository(last))

    def _build_project_sidebar(self) -> None:
        self.project_menu = tk.Menu(self, tearoff=False)
        self.project_menu.add_command(label="Open project", command=lambda: self.open_repository(self._project_menu_path) if self._project_menu_path else None)
        self.project_menu.add_command(label="Remove from Projects", command=lambda: self.remove_project(self._project_menu_path))
        self._sidebar_modes: dict[bool, SimpleNamespace] = {}
        for collapsed in (False, True):
            width = 56 if collapsed else 240
            sidebar = ttk.Frame(self, padding=(4, 8), width=width)
            sidebar.pack_propagate(False)
            sidebar.place(x=-width, y=0, width=width, relheight=1)
            header = ttk.Frame(sidebar)
            header.pack(fill=tk.X)
            label = ttk.Label(header, text="Projects")
            if not collapsed:
                label.pack(side=tk.LEFT)
            collapse = ttk.Button(header, text=">" if collapsed else "<", width=3, command=self.toggle_sidebar)
            collapse.pack(side=tk.RIGHT)
            add = ttk.Button(sidebar, text="+" if collapsed else "+ Add Git directory...", command=lambda: self.active_view.choose_repository())
            add.pack(fill=tk.X, pady=8)
            remove = ttk.Button(sidebar, text="-" if collapsed else "Remove from Projects", command=self.remove_project)
            remove.pack(fill=tk.X, pady=(0, 8))
            tree = ttk.Treeview(sidebar, show="tree", selectmode="browse")
            tree.column("#0", width=40 if collapsed else 200, minwidth=24)
            tree.pack(fill=tk.BOTH, expand=True)
            tree.bind("<<TreeviewSelect>>", self._select_project)
            tooltip = HoverTip(tree, self._project_tooltip_text)
            bind_context_menu(tree, self._on_project_context_menu)
            hint = ttk.Label(sidebar, text="Open projects keep their tabs,\nfilters and commit drafts.", wraplength=225)
            if not collapsed:
                hint.pack(fill=tk.X, pady=8)
            self._sidebar_modes[collapsed] = SimpleNamespace(
                sidebar=sidebar, project_label=label, collapse_button=collapse,
                add_project_button=add, remove_project_button=remove,
                project_tree=tree, project_tooltip=tooltip, project_hint=hint,
            )
        self._update_sidebar_layout()

    @staticmethod
    def _project_key(repository: Path) -> str:
        # Opened repositories are canonicalised by the background Git discovery.
        return os.path.normcase(os.path.abspath(repository))

    def _new_view(self) -> RepositoryView:
        view = RepositoryView(self)
        self._all_views.add(view)
        return view

    def _place_active_view(self) -> None:
        width = 56 if self.sidebar_collapsed else 240
        self.active_view.place(x=width, y=0, relwidth=1, width=-width, relheight=1)

    def _save_project_state(self) -> None:
        self.settings["projects"] = list(self.project_paths)
        self.settings["sidebar_collapsed"] = self.sidebar_collapsed
        self.settings["project_main_paths"] = {
            self._project_key(Path(path)): str(self.project_main_paths[self._project_key(Path(path))])
            for path in self.project_paths if self._project_key(Path(path)) in self.project_main_paths
        }
        if self.transient_mode:
            return
        self._settings_revision += 1
        if self._settings_save_job is not None:
            self.after_cancel(self._settings_save_job)
        self._settings_save_job = self.after(250, self._begin_settings_save)

    def _settings_save_error(self, error: Exception) -> None:
        messagebox.showerror(APP_NAME, f"The project list remains available in this window, but its cache could not be saved:\n{PROJECT_CACHE_FILE}\n\n{error}", parent=self)

    def _begin_settings_save(self) -> None:
        self._settings_save_job = None
        if self._closing or self._settings_future is not None or self._settings_executor is None:
            return
        self._settings_inflight_revision = self._settings_revision
        self._settings_future = self._settings_executor.submit(save_settings, copy.deepcopy(self.settings))
        self._settings_poll_job = self.after(30, self._poll_settings_save)

    def _poll_settings_save(self) -> None:
        self._settings_poll_job = None
        future = self._settings_future
        if future is None or self._closing:
            return
        if not future.done():
            self._settings_poll_job = self.after(30, self._poll_settings_save)
            return
        try:
            future.result()
            self._settings_saved_revision = self._settings_inflight_revision
        except Exception as error:
            self._settings_save_error(error)
        self._settings_future = None
        if self._settings_revision > self._settings_inflight_revision and self._settings_save_job is None:
            self._begin_settings_save()

    def _finish_settings_save(self) -> bool:
        for attribute in ("_settings_save_job", "_settings_poll_job"):
            job = getattr(self, attribute)
            if job is not None:
                self.after_cancel(job)
                setattr(self, attribute, None)
        if self._settings_future is not None:
            try:
                self._settings_future.result()
                self._settings_saved_revision = self._settings_inflight_revision
            except Exception:
                pass  # Retry the latest complete snapshot before closing.
            self._settings_future = None
        if not self.transient_mode and self._settings_saved_revision < self._settings_revision:
            try:
                save_settings(copy.deepcopy(self.settings))
                self._settings_saved_revision = self._settings_revision
            except Exception as error:
                self._settings_save_error(error)
                return False
        return True

    def remember_main_worktree(self, repository: Path, main: Path) -> None:
        key = self._project_key(repository)
        main = Path(os.path.abspath(main))
        if self.project_main_paths.get(key) != main:
            self.project_main_paths[key] = main
            self._save_project_state()
            self._render_projects()

    def _refresh_project_labels(self) -> None:
        missing = [Path(path) for path in self.project_paths if self._project_key(Path(path)) not in self.project_main_paths]
        if not missing:
            return

        def collect() -> dict[str, Path]:
            result = {}
            for repository in missing:
                try:
                    result[self._project_key(repository)] = main_worktree_path(repository)
                except (GitError, ValueError, OSError):
                    continue
            return result

        def rendered(names: dict[str, Path]) -> None:
            self.project_main_paths.update(names)
            self._save_project_state()
            self._render_projects()

        self.active_view._submit("Reading project worktree names...", collect, rendered, channel="project-labels")

    def _project_name(self, path: str) -> str:
        repository = Path(path)
        name = repository.name or str(repository)
        main = self.project_main_paths.get(self._project_key(repository))
        if main is not None and self._project_key(main) != self._project_key(repository):
            return name + " / " + (main.name or str(main))
        return name

    def _project_tooltip_text(self, event: tk.Event) -> str | None:
        row = self.project_tree.identify_row(event.y)
        if row and row.isdigit() and int(row) < len(self.project_paths):
            path = self.project_paths[int(row)]
            return self._project_name(path) + "\n" + str(Path(path))
        return None

    def _on_project_context_menu(self, event: tk.Event) -> str:
        self._suppress_project_selection = True
        self.after_idle(lambda: setattr(self, "_suppress_project_selection", False))
        row = context_row(self.project_tree, event)
        if row:
            self.project_tooltip.hide()
            self._project_menu_path = self.project_paths[int(row)]
            popup_menu(self.project_tree, self.project_menu, event, row)
        return "break"

    def remove_project(self, path: str | None = None) -> None:
        selection = self.project_tree.selection()
        if path is None and selection:
            path = self.project_paths[int(selection[0])]
        if path is None:
            return
        key = self._project_key(Path(path))
        if not any(self._project_key(Path(existing)) == key for existing in self.project_paths):
            return
        self._open_request += 1
        self.project_paths = [existing for existing in self.project_paths if self._project_key(Path(existing)) != key]
        if self.active_view.repository and self._project_key(self.active_view.repository) == key:
            remaining = next((self.views[self._project_key(Path(existing))] for existing in self.project_paths if self._project_key(Path(existing)) in self.views), None)
            if remaining is not None:
                self._activate_repository(remaining.repository)
            else:
                self.active_view.place_forget()
                if self._empty_view.repository is not None:
                    self._empty_view = self._new_view()
                self.active_view = self._empty_view
                self._place_active_view()
                self.settings["last_repository"] = ""
                self.title(f"{APP_NAME} {VERSION}")
                if self.project_paths:
                    self.open_repository(self.project_paths[0])
        self._save_project_state()
        self._render_projects()

    def toggle_sidebar(self) -> None:
        self.sidebar_collapsed = not self.sidebar_collapsed
        self._save_project_state()
        self._update_sidebar_layout()

    def _update_sidebar_layout(self) -> None:
        if self._sidebar_rendered_collapsed == self.sidebar_collapsed:
            return
        self._sidebar_rendered_collapsed = self.sidebar_collapsed
        mode = self._sidebar_modes[self.sidebar_collapsed]
        focused = self.focus_get()
        focus_target = None
        if hasattr(self, "sidebar"):
            self.project_tooltip.hide()
            for name, widget in vars(mode).items():
                if focused is getattr(self, name):
                    focus_target = widget
            self.sidebar.place_configure(x=-int(self.sidebar.cget("width")))
        for name, widget in vars(mode).items():
            setattr(self, name, widget)
        self.sidebar.place_configure(x=0)
        self.sidebar.lift()
        selection = self.project_tree.selection()
        if selection:
            self.project_tree.see(selection[0])
        if hasattr(self, "active_view"):
            self._place_active_view()
        if focus_target is not None:
            focus_target.focus_set()

    def _render_projects(self) -> None:
        self.project_tooltip.hide()
        self._update_sidebar_layout()
        desired_rows = {str(index) for index in range(len(self.project_paths))}
        self._project_rows = {self._project_key(Path(path)): str(index) for index, path in enumerate(self.project_paths)}
        for collapsed, mode in self._sidebar_modes.items():
            tree = mode.project_tree
            mode.remove_project_button.configure(state=tk.NORMAL if self.project_paths else tk.DISABLED)
            obsolete = [row for row in tree.get_children() if row not in desired_rows]
            if obsolete:
                tree.delete(*obsolete)
            for index, path in enumerate(self.project_paths):
                label = f"{index + 1:02d}" if collapsed else self._project_name(path)
                row = str(index)
                if tree.exists(row):
                    if tree.item(row, "text") != label:
                        tree.item(row, text=label)
                else:
                    tree.insert("", tk.END, iid=row, text=label)
        self._select_active_project()

    def _select_active_project(self) -> None:
        active = self._project_key(self.active_view.repository) if self.active_view.repository else ""
        row = self._project_rows.get(active)
        if row is not None:
            for mode in self._sidebar_modes.values():
                tree = mode.project_tree
                if tree.selection() != (row,):
                    tree.selection_set(row)
                    if tree is self.project_tree:
                        tree.see(row)

    def _select_project(self, event: tk.Event[tk.Misc] | None = None) -> None:
        if self._suppress_project_selection or (event is not None and event.widget is not self.project_tree):
            return
        selection = self.project_tree.selection()
        if selection:
            path = self.project_paths[int(selection[0])]
            key = self._project_key(Path(path))
            if not self.active_view.repository or self._project_key(self.active_view.repository) != key:
                self.open_repository(path)

    def open_repository(self, path: str) -> None:
        if not path.strip():
            return
        startup_phase("repository_open_requested")
        key = self._project_key(Path(path))
        self._open_request += 1
        request = self._open_request
        if key in self.views:
            self._activate_repository(self.views[key].repository)
            return

        def opened(repository: Path) -> None:
            if request == self._open_request:
                self._activate_repository(repository)

        self.active_view._submit("Recognising Git repository...", lambda: discover_repository(path), opened, channel="open-project")

    def _activate_repository(self, repository: Path) -> None:
        key = self._project_key(repository)
        view = self.views.get(key)
        listed = key in self._project_rows
        if view is self.active_view and view.repository is not None and listed:
            return
        new = view is None
        if view is None:
            view = self.active_view if self.active_view.repository is None else self._new_view()
            self.views[key] = view
        previous = self.active_view
        focused = self.focus_get()
        restore_focus = focused is not None and str(focused).startswith(str(previous) + ".")
        if restore_focus:
            previous._last_focus = focused
        if previous is not view:
            previous.place_forget()
        self.active_view = view
        self._place_active_view()
        if restore_focus:
            target = getattr(view, "_last_focus", None)
            (target if target is not None and target.winfo_exists() else view).focus_set()
        if new:
            view._repository_opened(repository)
        if not listed:
            self.project_paths.append(str(repository))
        self.settings["last_repository"] = str(repository)
        self._save_project_state()
        self.title(f"{APP_NAME} {VERSION} - {repository.name}")
        self.project_tooltip.hide()
        if not listed:
            self._render_projects()
        else:
            self._select_active_project()

    def _close(self) -> None:
        if self._closing:
            return
        views = self._all_views
        editors = [child for view in views for child in view.winfo_children() if isinstance(child, ConflictEditor)]
        if any(editor.saving for editor in editors) or any(view._busy_count for view in views):
            messagebox.showinfo(APP_NAME, "Wait for the running repository operations to finish before closing.", parent=self)
            return
        if any(editor.has_unsaved_changes() for editor in editors):
            if not messagebox.askyesno(APP_NAME, "Discard unsaved conflict editor results and close? Pending Git conflicts will be preserved for recovery.", icon="warning", parent=self):
                return
        if not self._finish_settings_save():
            return
        self._closing = True
        if self._settings_executor is not None:
            self._settings_executor.shutdown(wait=False, cancel_futures=True)
        for view in views:
            view._close()
        self.destroy()


def repository_smoke_test(repository: Path, *, refreshes: int = 2, alternate_repository: Path | None = None) -> dict[str, object]:
    repository = discover_repository(repository)
    executable = find_executable("git", external_environment())
    if executable is None:
        raise RuntimeError("Git was not found on PATH")
    runtime = external_runtime_status()
    if runtime.get("windows_dll_search_reset") is False:
        raise RuntimeError("Git inherited the frozen application's DLL search directory")
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        for _ in range(refreshes):
            state = RepositoryView._collect_repository_state(SimpleNamespace(repository=repository))
            history = run_git(repository, ["log", "-1", "--format=%H"]).output.strip()
        return {"status": "passed", "mode": "headless", "refreshes": refreshes, "head": history, "branch": state["branch"], "runtime": runtime}
    app = LiteGitApp(transient=True)
    app.withdraw()
    result: dict[str, object] = {}
    deadline = time.monotonic() + 120
    completed = 0
    app._activate_repository(repository)
    primary_view = app.active_view
    interactions = 0
    layout_widths: list[int] = []
    layout_targets: list[int] = []

    def finish(error: str | None = None) -> None:
        if error:
            result["error"] = error
        app._closing = True
        for view in tuple(app._all_views):
            view._close()
        app.destroy()

    def poll_layout() -> None:
        if app.background_errors or time.monotonic() >= deadline:
            finish("; ".join(app.background_errors) or f"The packaged layout check timed out: window {app.winfo_width()}, summary {app.active_view.summary_bar.winfo_width()}, completed widths {layout_widths}, targets {layout_targets}")
            return
        view = app.active_view
        summary = view.summary_bar
        expected = layout_targets[len(layout_widths)]
        app.update_idletasks()
        if app.winfo_width() != expected or summary.winfo_width() != expected - app.sidebar.winfo_width() or summary._layout_job is not None or view.diff_preview._render_job is not None:
            app.after(30, poll_layout)
            return
        try:
            for cell, labels in zip(summary.cells, summary.labels):
                if not cell.winfo_ismapped() or cell.winfo_x() < 0 or cell.winfo_x() + cell.winfo_width() > summary.winfo_width() or cell.winfo_y() + cell.winfo_height() > summary.winfo_height():
                    raise RuntimeError("A repository summary field is outside the available area")
                for label in labels:
                    if not label.winfo_ismapped() or label.winfo_x() + label.winfo_reqwidth() > cell.winfo_width() or label.winfo_y() + label.winfo_reqheight() > cell.winfo_height():
                        raise RuntimeError(f"A repository summary caption or value is clipped: {labels[0].cget('text')}, label extent {label.winfo_x() + label.winfo_reqwidth()}x{label.winfo_y() + label.winfo_reqheight()}, cell {cell.winfo_width()}x{cell.winfo_height()}")
                title, value = labels
                title_reference = ttk.Label(app, text=title.cget("text"), style=title.cget("style"), wraplength=0)
                value_reference = ttk.Label(app, text=value.cget("text"), style=value.cget("style"), wraplength=0)
                try:
                    if (title.winfo_reqwidth(), title.winfo_reqheight()) != (title_reference.winfo_reqwidth(), title_reference.winfo_reqheight()):
                        raise RuntimeError("A repository summary caption was split across lines")
                    natural = title_reference.winfo_reqwidth() + 5 + value_reference.winfo_reqwidth() + 12
                    if natural <= summary.winfo_width() - 16 and (value.winfo_reqwidth(), value.winfo_reqheight()) != (value_reference.winfo_reqwidth(), value_reference.winfo_reqheight()):
                        raise RuntimeError("A repository summary value remained wrapped despite sufficient width")
                finally:
                    title_reference.destroy()
                    value_reference.destroy()
            if sum(mode.sidebar.winfo_x() >= 0 for mode in app._sidebar_modes.values()) != 1:
                raise RuntimeError("More than one sidebar layout is inside the window")
            layout_widths.append(expected)
            if len(layout_widths) == 1:
                app.geometry(f"{layout_targets[1]}x800")
                app.after(50, poll_layout)
                return
            pane = next(child for child in view.changes_tab.winfo_children() if isinstance(child, DeferredPanedwindow))
            child = pane.nametowidget(pane.panes()[0])
            width = child.winfo_width()
            start = pane.sashpos(0)
            pane.event_generate("<ButtonPress-1>", x=start, y=20)
            if app.grab_current() is not pane:
                raise RuntimeError("The divider did not capture the pointer")
            for offset in range(3, 61, 3):
                pane.event_generate("<B1-Motion>", x=start + offset, y=20)
                app.update_idletasks()
                if child.winfo_width() != width or pane.sashpos(0) != start:
                    raise RuntimeError("Divider motion resized the content before release")
            pane.event_generate("<ButtonRelease-1>", x=start + 60, y=20)
            app.update_idletasks()
            if pane.sashpos(0) != start + 60 or app.grab_current() is not None:
                raise RuntimeError("The divider release did not commit and clean up")
            result["layout"] = {
                "status": "passed", "window_widths": layout_widths, "summary_fields_visible": len(summary.cells),
                "divider_motions": 20, "resize_deferred_until_release": True, "pointer_grab_released": True,
                "single_sidebar_visible": True,
                "captions_single_line": True, "fitting_values_single_line": True,
            }
            result["status"] = "passed"
            finish()
        except Exception as error:
            finish(str(error))

    def poll_interactions() -> None:
        nonlocal interactions
        if app.background_errors or time.monotonic() >= deadline:
            finish("; ".join(app.background_errors) or "The packaged interaction check timed out")
            return
        if any(item._busy_count for item in app._all_views):
            app.after(50, poll_interactions)
            return
        try:
            secondary_view = app.views[app._project_key(alternate_repository)]
            if not secondary_view.commit_tree.get_children():
                raise RuntimeError("The alternate repository history did not load")
            target = repository if interactions % 2 == 0 else alternate_repository
            expected = primary_view if interactions % 2 == 0 else secondary_view
            app._activate_repository(target)
            app.toggle_sidebar()
            app.update_idletasks()
            if app.active_view is not expected or not expected.winfo_ismapped():
                raise RuntimeError("The selected project view was not mapped")
            if any(view.winfo_ismapped() for view in app._all_views if view is not expected):
                raise RuntimeError("An inactive project still participates in the mapped layout")
            if primary_view.commit_message_var.get() != "Primary smoke draft" or secondary_view.commit_message_var.get() != "Secondary smoke draft":
                raise RuntimeError("A packaged project switch discarded its draft")
        except Exception as error:
            finish(str(error))
            return
        interactions += 1
        if interactions < 20:
            app.after(50, poll_interactions)
            return
        result["interactions"] = {"status": "passed", "sidebar_toggles": interactions, "project_switches": interactions, "drafts_preserved": True, "mapped_window": True}
        view = app.active_view
        view.summary_vars["branch"].set("feature/" + "long-branch-name-" * 5)
        view.summary_vars["upstream"].set("origin/feature/" + "long-branch-name-" * 5)
        view.notebook.select(view.changes_tab)
        view.diff_preview.render(FileVersions("before\n" * 300, "after\n" * 300, "HEAD", "Working tree"), "layout-check.txt")
        app.minsize(640, 480)
        maximum = max(640, min(app.winfo_screenwidth() - 80, app.maxsize()[0] - 32))
        layout_targets.extend((min(800, maximum), min(1500, maximum)))
        app.geometry(f"{layout_targets[0]}x800")
        app.after(50, poll_layout)

    def poll() -> None:
        nonlocal completed
        if app.background_errors:
            finish("; ".join(app.background_errors))
            return
        if time.monotonic() >= deadline:
            finish("The repository refresh smoke test timed out")
            return
        view = app.active_view
        if any(item._busy_count for item in app._all_views):
            app.after(30, poll)
            return
        if not view.commit_tree.get_children():
            finish("The repository history did not load")
            return
        completed += 1
        if completed < refreshes:
            view.refresh_all()
            app.after(30, poll)
            return
        checked_runtime = external_runtime_status()
        if checked_runtime.get("windows_dll_search_reset") is False:
            finish("The inherited DLL search directory changed during repository refresh")
            return
        if native_launcher() is not None:
            bundle = Path(sys.executable).resolve().parent
            try:
                for name, library in (("tcl_data_in_runtime", app.tk.call("info", "library")), ("tk_data_in_runtime", app.tk.call("set", "tk_library"))):
                    value = str(library)
                    checked_runtime[name] = value.startswith(("//zipfs:/", "zipfs:/")) or Path(value).resolve().is_relative_to(bundle)
            except Exception as error:
                finish(f"The bundled Tcl/Tk library check failed: {error}")
                return
        result.update({
            "status": "passed", "mode": "interface", "refreshes": completed,
            "branch": view.summary_vars["branch"].get(), "head": view.summary_vars["head"].get(),
            "history_rows": len(view.commit_tree.get_children()),
            "git_executable_absolute": executable.is_absolute(),
            "runtime": checked_runtime,
        })
        if alternate_repository is not None:
            result["status"] = "pending"
            primary_view.commit_message_var.set("Primary smoke draft")
            app.attributes("-alpha", 0.0)
            app.deiconify()
            app._activate_repository(alternate_repository)
            app.active_view.commit_message_var.set("Secondary smoke draft")
            app.after(50, poll_interactions)
            return
        finish()

    app.after(30, poll)
    app.mainloop()
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    if result.get("status") != "passed":
        raise RuntimeError("The repository smoke test closed before completion")
    return result


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--reword-helper":
        return reword_helper_main(sys.argv[2:])
    if len(sys.argv) in {3, 5, 7} and sys.argv[1] == "--smoke-test":
        payload = {
            "app": APP_NAME,
            "version": VERSION,
            "frozen": is_packaged_application(),
            "platform": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "tk": str(tk.TkVersion),
        }
        if len(sys.argv) >= 5:
            if sys.argv[3] != "--repository":
                raise ValueError("Expected --repository <path> after the smoke-test output")
            try:
                if len(sys.argv) == 7:
                    if sys.argv[5] != "--alternate-repository":
                        raise ValueError("Expected --alternate-repository <path> after the primary repository")
                    alternate = discover_repository(Path(sys.argv[6]))
                    payload["repository_check"] = repository_smoke_test(Path(sys.argv[4]), alternate_repository=alternate)
                else:
                    payload["repository_check"] = repository_smoke_test(Path(sys.argv[4]))
            except Exception as error:
                payload["repository_check"] = {
                    "status": "failed", "error": redact_text(f"{type(error).__name__}: {error}"),
                }
                Path(sys.argv[2]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                return 1
        payload["startup_stages"] = recorded_stages()
        Path(sys.argv[2]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    app = LiteGitApp(restore_projects="--no-restore" not in sys.argv[1:])
    app.bind("<Map>", lambda event: startup_phase("window_mapped") if event.widget is app else None, add="+")
    startup_phase("event_loop_started")
    app.mainloop()
    startup_phase("event_loop_finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
