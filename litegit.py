from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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


APP_NAME = "LiteGit Workbench"
VERSION = "2.0.0"


def application_config_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "LiteGitWorkbench"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LiteGitWorkbench"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "litegit-workbench"


CONFIG_DIR = application_config_dir()
CONFIG_FILE = CONFIG_DIR / "settings.json"


def _editor_argument(value: str | os.PathLike[str]) -> str:
    normalised = str(value).replace("\\", "/")
    return '"' + normalised.replace('"', '\\"') + '"'


def build_reword_environment(target: str, message: str) -> dict[str, str]:
    executable = _editor_argument(sys.executable)
    if getattr(sys, "frozen", False):
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


def load_settings() -> dict[str, object]:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict[str, object]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


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


class LiteGitApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry("1320x850")
        self.minsize(980, 650)
        self.option_add("*tearOff", False)
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="litegit")
        self.repository: Path | None = None
        self.settings = load_settings()
        self.branches: list[BranchEntry] = []
        self.commits: list[CommitEntry] = []
        self.remote_commit_hashes: set[str] = set()
        self.status_entries: list[StatusEntry] = []
        self.worktrees: list[WorktreeEntry] = []
        self.stashes: list[StashEntry] = []
        self.remotes: list[RemoteEntry] = []
        self._busy_count = 0
        self._closing = False
        self._build_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        last_repository = str(self.settings.get("last_repository", ""))
        if last_repository and Path(last_repository).exists():
            self.after(100, lambda: self.open_repository(last_repository))

    def _build_style(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("Treeview", rowheight=25)
        style.configure("Summary.TLabel", font=("Segoe UI", 10, "bold"))

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

        summary = ttk.Frame(self, padding=(8, 2, 8, 7))
        summary.pack(fill=tk.X)
        self.summary_vars = {key: tk.StringVar(value="-") for key in ("branch", "head", "upstream", "sync", "state", "worktrees")}
        captions = (
            ("Branch", "branch"),
            ("HEAD", "head"),
            ("Upstream", "upstream"),
            ("Synchronisation", "sync"),
            ("Working tree", "state"),
            ("Worktrees", "worktrees"),
        )
        for caption, key in captions:
            frame = ttk.Frame(summary)
            frame.pack(side=tk.LEFT, padx=(0, 20))
            ttk.Label(frame, text=f"{caption}:").pack(side=tk.LEFT)
            ttk.Label(frame, textvariable=self.summary_vars[key], style="Summary.TLabel").pack(side=tk.LEFT)

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
        self.bind("<F5>", lambda _event: self.refresh_all())
        self.bind("<Control-o>", lambda _event: self.choose_repository())

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
        for text, command in (
            ("Check out", self.checkout_branch),
            ("New", self.create_branch),
            ("Rename", self.rename_branch),
            ("Delete", self.delete_branch),
            ("Merge selected...", self.merge_selected_branch),
            ("Abort merge", self.abort_merge),
            ("Rebase current branch", self.rebase_selected_branch),
            ("Set upstream", self.set_upstream),
            ("Delete remote branch", self.delete_remote_branch),
        ):
            ttk.Button(toolbar, text=text, command=command).pack(side=tk.LEFT, padx=2)
        self.branch_filter_var = tk.StringVar()
        ttk.Label(toolbar, text="Filter:").pack(side=tk.LEFT, padx=(15, 2))
        branch_filter = ttk.Entry(toolbar, textvariable=self.branch_filter_var, width=25)
        branch_filter.pack(side=tk.LEFT)
        branch_filter.bind("<KeyRelease>", lambda _event: self.render_branches())
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
        pane = ttk.Panedwindow(tab, orient=tk.VERTICAL)
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
        self.commit_detail_text = self._text(lower, height=12)

    def _build_changes_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Local changes")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        for text, command in (
            ("Stage selected", self.stage_selected),
            ("Unstage selected", self.unstage_selected),
            ("Stage all", lambda: self.execute_git(["add", "-A"], refresh=True)),
            ("Unstage all", lambda: self.execute_git(["reset", "--mixed"], refresh=True)),
            ("Discard selected", self.discard_selected),
            ("Refresh", self.refresh_all),
        ):
            ttk.Button(toolbar, text=text, command=command).pack(side=tk.LEFT, padx=2)
        pane = ttk.Panedwindow(tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)
        self.change_tree = self._tree(
            left,
            (("code", "State", 75), ("state", "Description", 160), ("path", "Path", 430)),
            selectmode="extended",
        )
        self.change_tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected_diff())
        self.diff_text = self._text(right)
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
        if not path.strip():
            return
        self._submit(
            "Recognising Git repository...",
            lambda: discover_repository(path),
            self._repository_opened,
        )

    def _repository_opened(self, repository: Path) -> None:
        self.repository = repository
        self.path_var.set(str(repository))
        self.settings["last_repository"] = str(repository)
        save_settings(self.settings)
        self.title(f"{APP_NAME} {VERSION} - {repository.name}")
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
            "status": (["status", "--porcelain=v1", "-z", "--untracked-files=normal"], True),
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
        self._submit("Refreshing repository state...", self._collect_repository_state, self._render_repository_state)

    def _render_repository_state(self, state: dict[str, object]) -> None:
        self.branches = list(state["branches"])  # type: ignore[arg-type]
        self.status_entries = list(state["status"])  # type: ignore[arg-type]
        self.worktrees = list(state["worktrees"])  # type: ignore[arg-type]
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
        self._clear_tree(self.branch_tree)
        needle = self.branch_filter_var.get().casefold().strip()
        for branch in self.branches:
            if needle and needle not in branch.name.casefold() and needle not in branch.subject.casefold():
                continue
            self.branch_tree.insert(
                "",
                tk.END,
                values=("*" if branch.current else "", branch.kind, branch.name, branch.short_hash, branch.upstream, branch.tracking, branch.head_only, branch.branch_only, branch.date, branch.author, branch.subject),
            )

    def _render_changes(self) -> None:
        self._clear_tree(self.change_tree)
        for index, entry in enumerate(self.status_entries):
            code = f"{entry.index}{entry.worktree}"
            display_path = f"{entry.original_path} -> {entry.path}" if entry.original_path else entry.path
            self.change_tree.insert("", tk.END, iid=str(index), values=(code, entry.state, display_path))
        if not self.status_entries:
            self._set_text(self.diff_text, "The working tree is clean.")

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

        self._submit("Reading commit history...", collect, self._render_history)

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

    def selected_branch(self, *, local_only: bool = False) -> BranchEntry | None:
        selected = self.branch_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a branch first.", parent=self)
            return None
        values = self.branch_tree.item(selected[0], "values")
        name = str(values[2])
        branch = next((item for item in self.branches if item.name == name), None)
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
        selected = self.change_tree.selection()
        if not selected or not self.repository:
            return
        try:
            entry = self.status_entries[int(selected[0])]
        except (ValueError, IndexError):
            return
        if entry.index == "?" and entry.worktree == "?":
            path = self.repository / entry.path
            try:
                output = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                output = str(exc)
            self._set_text(self.diff_text, f"Untracked file: {entry.path}\n\n{output}")
            return
        arguments = ["diff", "--find-renames"]
        if entry.index != " ":
            arguments.append("--cached")
        arguments.extend(["--", entry.path])
        self._submit(
            "Reading differences...",
            lambda: run_git(self.repository, arguments, check=False).output,
            lambda output: self._set_text(self.diff_text, output or "There are no differences to display."),
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
                os.startfile(worktree.path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", worktree.path])
            else:
                subprocess.Popen(["xdg-open", worktree.path])
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
        self.execute_git(["pull", "--ff-only"], refresh=True)

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

        def worker() -> list[GitResult]:
            return [run_git(self.repository, command) for command in commands]

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

    def _submit(self, label: str, worker: Callable[[], object], success: Callable[[object], None]) -> None:
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
            try:
                value = future.result()
            except GitError as exc:
                safe_error = redact_text(str(exc))
                self.status_var.set(f"Failed: {safe_error}")
                self._append_console_error(exc)
                messagebox.showerror(APP_NAME, safe_error, parent=self)
            except Exception as exc:  # UI boundary: always report unexpected failures.
                self.status_var.set(f"Failed: {exc}")
                messagebox.showerror(APP_NAME, f"{type(exc).__name__}: {exc}", parent=self)
            else:
                success(value)
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
        tree.heading(column, command=lambda: LiteGitApp._sort_tree(tree, column, not reverse))

    def _close(self) -> None:
        self._closing = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--reword-helper":
        return reword_helper_main(sys.argv[2:])
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        payload = {
            "app": APP_NAME,
            "version": VERSION,
            "frozen": bool(getattr(sys, "frozen", False)),
            "platform": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "tk": str(tk.TkVersion),
        }
        Path(sys.argv[2]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    app = LiteGitApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
