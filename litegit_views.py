from __future__ import annotations

import tkinter as tk
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from litegit_diff import DirectoryPreview, FileVersions, aligned_diff
from litegit_core import run_git
from litegit_merge import ConflictFile, PendingUpdate, abort_update, conflict_regions, finish_update, pending_updates, read_conflict, save_resolution, unresolved_paths


class HoverTip:
    def __init__(self, widget: tk.Misc, describe: Callable[[tk.Event], str | None], *, delay: int = 450) -> None:
        self.widget = widget
        self.describe = describe
        self.delay = delay
        self.window: tk.Toplevel | None = None
        self.label: ttk.Label | None = None
        self.pending: str | None = None
        self.content = ""
        self.position = (0, 0)
        widget.bind("<Motion>", self._motion, add="+")
        for event in ("<Leave>", "<ButtonPress>", "<MouseWheel>", "<Button-4>", "<Button-5>", "<Configure>", "<Unmap>", "<Destroy>"):
            widget.bind(event, self.hide, add="+")

    def _motion(self, event: tk.Event) -> None:
        content = self.describe(event) or ""
        if content == self.content:
            return
        self.hide()
        self.content = content
        self.position = (event.x_root + 14, event.y_root + 18)
        if content:
            self.pending = self.widget.after(self.delay, self._show)

    def _show(self) -> None:
        self.pending = None
        if not self.content or not self.widget.winfo_exists():
            return
        window = tk.Toplevel(self.widget)
        self.window = window
        window.wm_overrideredirect(True)
        self.label = ttk.Label(window, text=self.content, padding=8, relief=tk.SOLID, borderwidth=1, wraplength=680)
        self.label.pack()
        window.update_idletasks()
        if self.window is not window or not window.winfo_exists():
            return
        x, y = self.position
        x = max(0, min(x, self.widget.winfo_screenwidth() - window.winfo_reqwidth() - 6))
        y = max(0, min(y, self.widget.winfo_screenheight() - window.winfo_reqheight() - 6))
        window.wm_geometry(f"+{x}+{y}")

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.label = None
        self.content = ""


def bind_context_menu(tree: ttk.Treeview, callback: Callable[[tk.Event], str]) -> None:
    for event in ("<Button-3>", "<Shift-F10>", "<Menu>"):
        tree.bind(event, callback)
    if tree.tk.call("tk", "windowingsystem") == "aqua":
        tree.bind("<Button-2>", callback)
        tree.bind("<Control-Button-1>", callback)


def context_row(tree: ttk.Treeview, event: tk.Event) -> str:
    if getattr(event, "keysym", "") in {"Menu", "F10"}:
        selection = tree.selection()
        row = selection[0] if selection else tree.focus()
    else:
        row = tree.identify_row(event.y)
    if row and tree.exists(row):
        tree.selection_set(row)
        tree.focus(row)
        tree.focus_set()
        return row
    return ""


def popup_menu(tree: ttk.Treeview, menu: tk.Menu, event: tk.Event, row: str) -> None:
    if getattr(event, "keysym", "") in {"Menu", "F10"}:
        bounds = tree.bbox(row)
        if not bounds:
            tree.see(row)
            tree.update_idletasks()
            bounds = tree.bbox(row)
        if not bounds:
            return
        x, y, _width, height = bounds
        position = (tree.winfo_rootx() + x + 24, tree.winfo_rooty() + y + height)
    else:
        position = (event.x_root, event.y_root)
    try:
        menu.tk_popup(*position)
    finally:
        menu.grab_release()


def text_panel(parent: tk.Misc, *, editable: bool = False) -> tk.Text:
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.BOTH, expand=True)
    text = tk.Text(frame, width=1, wrap=tk.NONE, undo=editable, font=("Cascadia Mono", 10), exportselection=False)
    vertical = ttk.Scrollbar(frame, command=text.yview)
    horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
    text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    text.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    text.tag_configure("removed", background="#FCE4E4", foreground="#8A2020")
    text.tag_configure("added", background="#DDF6E5", foreground="#075E32")
    text.tag_configure("empty", background="#F0F1F3")
    if not editable:
        text.configure(state=tk.DISABLED)
    return text


class DiffPreview(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.pack(fill=tk.BOTH, expand=True)
        self.caption = tk.StringVar(value="Select a changed file to compare its versions.")
        ttk.Label(self, textvariable=self.caption).pack(fill=tk.X)
        pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left, right = ttk.LabelFrame(pane, text="Before / HEAD"), ttk.LabelFrame(pane, text="After / working tree")
        pane.add(left, weight=1)
        pane.add(right, weight=1)
        self.before_frame, self.after_frame = left, right
        self.before_text, self.after_text = text_panel(left), text_panel(right)
        self._scrolling = False
        for source, other in ((self.before_text, self.after_text), (self.after_text, self.before_text)):
            scroll = source.master.grid_slaves(row=0, column=1)[0]
            source.configure(yscrollcommand=lambda first, last, s=scroll, o=other: self._sync_scroll(first, last, s, o))

    def _sync_scroll(self, first: str, last: str, scrollbar: ttk.Scrollbar, other: tk.Text) -> None:
        scrollbar.set(first, last)
        if not self._scrolling:
            self._scrolling = True
            try:
                other.yview_moveto(first)
            finally:
                self._scrolling = False

    def message(self, content: str) -> None:
        self.caption.set(content)
        for text in (self.before_text, self.after_text):
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.configure(state=tk.DISABLED)

    def render(self, versions: FileVersions, path: str) -> None:
        self.message(path + "   |   Removed lines: red   Added lines: green")
        self.before_frame.configure(text="Before / " + versions.before_label)
        self.after_frame.configure(text="After / " + versions.after_label)
        for text in (self.before_text, self.after_text):
            text.configure(state=tk.NORMAL)
        for row in aligned_diff(versions.before, versions.after):
            for text, number, content, tag in (
                (self.before_text, row.before_number, row.before, "removed"),
                (self.after_text, row.after_number, row.after, "added"),
            ):
                colour = () if row.kind == "equal" else ("empty" if number is None else tag,)
                text.insert(tk.END, f"{number:>5} | {content}\n" if number is not None else "      |\n", colour)
        if versions.before == versions.after:
            self.caption.set(path + "   |   No differences in this view")
        elif versions.before.rstrip("\r\n") == versions.after.rstrip("\r\n"):
            self.caption.set(path + "   |   End-of-line or final newline changed")
        for text in (self.before_text, self.after_text):
            text.configure(state=tk.DISABLED)
            text.yview_moveto(0)

    def render_directory(self, preview: DirectoryPreview) -> None:
        self.message(preview.path + "   |   Directory")
        self.before_frame.configure(text="Directory")
        self.after_frame.configure(text="Changed files")
        guidance = (
            "This directory is a Git repository or submodule.\nAdd it to Projects to inspect its files."
            if preview.nested_repository else
            "Select an individual file to compare its contents.\nRefresh Local changes to list untracked files inside folders."
        )
        listing = "\n".join(f"{entry.index}{entry.worktree}  {entry.path}" for entry in preview.changes[:200])
        if len(preview.changes) > 200:
            listing += f"\n\nShowing 200 of {len(preview.changes)} changed files."
        for text, content in ((self.before_text, guidance), (self.after_text, listing or "No individual changed files are listed in the parent repository.")):
            text.configure(state=tk.NORMAL)
            text.insert("1.0", content)
            text.configure(state=tk.DISABLED)


class ConflictEditor(tk.Toplevel):
    def __init__(self, parent: tk.Misc, conflict: ConflictFile, on_saved: Callable[[], None]) -> None:
        super().__init__(parent)
        self.owner = parent
        self.conflict = conflict
        self.on_saved = on_saved
        self.title("Resolve conflict - " + conflict.path)
        self.geometry("1250x760")
        self.minsize(850, 500)
        self.saving = False
        self.delete_result = False
        self.current_region = 0
        self.regions = conflict_regions(conflict.result.replace("\r\n", "\n"))
        self.initial = conflict.result.replace("\r\n", "\n")
        self.status = tk.StringVar()
        ttk.Label(self, text=conflict.path, padding=8).pack(fill=tk.X)
        ttk.Label(self, text="Left = index stage 2; right = index stage 3. During a rebase, left is the upstream and right is the replayed commit.", padding=(8, 0, 8, 5)).pack(fill=tk.X)
        tools = ttk.Frame(self, padding=(8, 0, 8, 6))
        tools.pack(fill=tk.X)
        for label, command in (
            ("Previous conflict", lambda: self.navigate(-1)),
            ("Next conflict", lambda: self.navigate(1)),
            ("Use left block", lambda: self.accept_block("ours")),
            ("Use right block", lambda: self.accept_block("theirs")),
            ("Use both blocks", lambda: self.accept_block("both")),
            ("Use entire left", lambda: self.accept_file("ours")),
            ("Use entire right", lambda: self.accept_file("theirs")),
        ):
            ttk.Button(tools, text=label, command=command).pack(side=tk.LEFT, padx=2)
        pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=8)
        panels = []
        for label in ("Left / stage 2 (read only)", "Result (editable)", "Right / stage 3 (read only)"):
            panel = ttk.LabelFrame(pane, text=label)
            pane.add(panel, weight=1)
            panels.append(panel)
        self.left_text = text_panel(panels[0])
        self.result_text = text_panel(panels[1], editable=True)
        self.right_text = text_panel(panels[2])
        for text, content in ((self.left_text, conflict.ours), (self.right_text, conflict.theirs)):
            text.configure(state=tk.NORMAL)
            text.insert("1.0", (content if content is not None else "(File deleted on this side)").replace("\r\n", "\n"))
            base = (conflict.base or "").splitlines()
            lines = (content or "").splitlines()
            for kind, _a, _b, start, end in SequenceMatcher(None, base, lines).get_opcodes():
                if kind != "equal" and start != end:
                    text.tag_add("added", f"{start + 1}.0", f"{end + 1}.0")
            text.configure(state=tk.DISABLED)
        self.result_text.insert("1.0", self.initial)
        self.result_text.edit_reset()
        self.result_text.tag_configure("conflict_region", background="#FFF0CC")
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, textvariable=self.status).pack(side=tk.LEFT)
        self.save_button = ttk.Button(bottom, text="Save and stage", command=self.save)
        self.save_button.pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom, text="Close", command=self.close).pack(side=tk.RIGHT, padx=2)
        self.bind("<Control-s>", lambda _event: self.save())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._show_region()

    def _show_region(self) -> None:
        self.regions = conflict_regions(self.result_text.get("1.0", "end-1c"))
        self.status.set(f"{len(self.regions)} conflict blocks remain. Manual edits are checked when saving.")
        self.result_text.tag_remove("conflict_region", "1.0", tk.END)
        for region in self.regions:
            self.result_text.tag_add("conflict_region", f"1.0+{region.start}c", f"1.0+{region.end}c")
        if self.regions:
            self.current_region %= len(self.regions)
            index = f"1.0+{self.regions[self.current_region].start}c"
            self.result_text.see(index)
            self.result_text.mark_set(tk.INSERT, index)

    def navigate(self, offset: int) -> None:
        self.current_region += offset
        self._show_region()

    def _replace_result(self, start: str, end: str, content: str) -> None:
        self.result_text.configure(autoseparators=False)
        try:
            self.result_text.edit_separator()
            self.result_text.delete(start, end)
            self.result_text.insert(start, content)
            self.result_text.edit_separator()
        finally:
            self.result_text.configure(autoseparators=True)

    def accept_block(self, side: str) -> None:
        if self.saving:
            return
        self._show_region()
        if not self.regions:
            return
        region = self.regions[self.current_region]
        content = region.ours if side == "ours" else region.theirs if side == "theirs" else region.ours + region.theirs
        start = f"1.0+{region.start}c"
        self._replace_result(start, f"1.0+{region.end}c", content)
        self.delete_result = False
        self._show_region()

    def accept_file(self, side: str) -> None:
        if self.saving:
            return
        content = self.conflict.ours if side == "ours" else self.conflict.theirs
        if not messagebox.askyesno("Resolve conflict", "Replace the complete result with this side?" + ("\nThis will delete the file." if content is None else ""), parent=self):
            return
        self._replace_result("1.0", tk.END, (content or "").replace("\r\n", "\n"))
        self.delete_result = content is None
        self._show_region()
        if self.delete_result:
            self.status.set("The result will delete the file when saved and staged.")

    def save(self) -> None:
        if self.saving:
            return
        content = self.result_text.get("1.0", "end-1c")
        if self.delete_result and content:
            self.delete_result = False
        self.saving = True
        self.save_button.configure(state=tk.DISABLED)
        self.result_text.configure(state=tk.DISABLED)

        def failed(_error: Exception) -> None:
            if self.winfo_exists():
                self.saving = False
                self.save_button.configure(state=tk.NORMAL)
                self.result_text.configure(state=tk.NORMAL)

        def saved(_value: object) -> None:
            self.saving = False
            self.destroy()
            self.on_saved()

        self.owner._submit("Saving conflict resolution...", lambda: save_resolution(self.conflict, content, delete=self.delete_result), saved, on_error=failed)

    def has_unsaved_changes(self) -> bool:
        return self.delete_result or self.result_text.get("1.0", "end-1c") != self.initial

    def close(self) -> None:
        if self.saving:
            return
        if self.has_unsaved_changes() and not messagebox.askyesno("Resolve conflict", "Discard the unsaved result edits? The Git conflict will remain unresolved.", parent=self):
            return
        self.destroy()


class ConflictManager(tk.Toplevel):
    def __init__(self, parent: tk.Misc, repository: Path, target: str | None = None) -> None:
        super().__init__(parent)
        self.owner = parent
        self.repository = repository
        self.targets: list[tuple[str, Path, PendingUpdate | None, list[str]]] = []
        self.editors: dict[tuple[str, str], ConflictEditor] = {}
        self.working = False
        self.title("Resolve conflicts - " + repository.name)
        self.geometry("850x520")
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(self, textvariable=self.target_var, state="readonly")
        self.target_combo.pack(fill=tk.X, padx=8, pady=8)
        self.target_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_paths())
        self.paths = tk.Listbox(self, exportselection=False)
        self.paths.pack(fill=tk.BOTH, expand=True, padx=8)
        self.paths.bind("<Double-1>", lambda _event: self.edit_selected())
        self.status = tk.StringVar(value="Reading conflicts...")
        ttk.Label(self, textvariable=self.status, padding=8, wraplength=810).pack(fill=tk.X)
        tools = ttk.Frame(self, padding=8)
        tools.pack(fill=tk.X)
        for label, command in (
            ("Refresh", self.refresh), ("Open three-way editor", self.edit_selected),
            ("Complete merge / update", self.complete), ("Abort background update", self.abort),
        ):
            ttk.Button(tools, text=label, command=command).pack(side=tk.LEFT, padx=2)
        self.refresh(target)

    def refresh(self, target: str | None = None) -> None:
        if not self.winfo_exists():
            return
        selected = self._selected()
        desired = target or (str(selected[1]) if selected else str(self.repository))

        def collect() -> list[tuple[str, Path, PendingUpdate | None, list[str]]]:
            targets = [("Current worktree: " + str(self.repository), self.repository, None, unresolved_paths(self.repository))]
            for update in pending_updates(self.repository):
                worktree = Path(update.worktree)
                paths = unresolved_paths(worktree) if worktree.exists() else []
                state = " (updated; cleanup pending)" if update.published_head else ""
                targets.append(("Background update: " + update.branch + state, worktree, update, paths))
            return targets

        def render(targets: list[tuple[str, Path, PendingUpdate | None, list[str]]]) -> None:
            if not self.winfo_exists():
                return
            self.targets = targets
            self.target_combo["values"] = [entry[0] for entry in targets]
            index = next((index for index, entry in enumerate(targets) if str(entry[1]) == desired), 0)
            self.target_combo.current(index)
            self._render_paths()

        self.owner._submit("Reading conflicts and pending updates...", collect, render, channel="conflict-list")

    def _selected(self) -> tuple[str, Path, PendingUpdate | None, list[str]] | None:
        index = self.target_combo.current()
        return self.targets[index] if 0 <= index < len(self.targets) else None

    def _render_paths(self) -> None:
        selected = self._selected()
        if not selected:
            return
        self.paths.delete(0, tk.END)
        for path in selected[3]:
            self.paths.insert(tk.END, path)
        if selected[3]:
            self.paths.selection_set(0)
        self.status.set(f"{len(selected[3])} unresolved files. Save and stage each result, then complete the operation.\n{selected[1]}")

    def edit_selected(self) -> None:
        selected, indices = self._selected(), self.paths.curselection()
        if not selected or not indices:
            return
        repository, path = selected[1], self.paths.get(indices[0])
        key = (str(repository), path)
        editor = self.editors.get(key)
        if editor is not None and editor.winfo_exists():
            editor.lift()
            return

        def opened(conflict: ConflictFile) -> None:
            if self.winfo_exists():
                self.editors[key] = ConflictEditor(self.owner, conflict, lambda: (self.refresh(str(repository)), self.owner.refresh_all()))

        self.owner._submit("Reading the three conflict versions...", lambda: read_conflict(repository, path), opened)

    def _begin_operation(self) -> bool:
        if self.working:
            return False
        if any(editor.winfo_exists() for editor in self.editors.values()):
            messagebox.showinfo("Resolve conflicts", "Save or close the open conflict editors before completing or aborting this operation.", parent=self)
            return False
        self.working = True
        return True

    def _operation_finished(self, _result: object) -> None:
        self.working = False
        if self.winfo_exists():
            self.refresh()
        self.owner.refresh_all()

    def _operation_failed(self, _error: Exception) -> None:
        self.working = False
        if self.winfo_exists():
            self.refresh()

    def complete(self) -> None:
        selected = self._selected()
        if not selected or not self._begin_operation():
            return
        _label, repository, update, _paths = selected

        def worker() -> object:
            if update:
                return finish_update(update)
            if unresolved_paths(repository):
                raise ValueError("Resolve and stage all conflicted files before continuing.")
            for directory in ("rebase-merge", "rebase-apply"):
                location = Path(run_git(repository, ["rev-parse", "--git-path", directory]).output.strip())
                if (repository / location).exists():
                    return run_git(repository, ["-c", "core.editor=true", "rebase", "--continue"])
            for reference, command in (("MERGE_HEAD", ["commit", "--no-edit"]), ("CHERRY_PICK_HEAD", ["cherry-pick", "--continue"]), ("REVERT_HEAD", ["revert", "--continue"])):
                if run_git(repository, ["rev-parse", "--verify", reference], check=False).returncode == 0:
                    return run_git(repository, ["-c", "core.editor=true", *command])
            raise ValueError("There is no merge, rebase, cherry-pick or revert to continue. Resolved files remain staged.")

        self.owner._submit("Completing merge or branch update...", worker, self._operation_finished, on_error=self._operation_failed)

    def abort(self) -> None:
        selected = self._selected()
        if not selected or not selected[2]:
            messagebox.showinfo("Resolve conflicts", "Select a background branch update to abort. Use Branches / Abort merge for the current worktree.", parent=self)
            return
        prompt = "Clean up this completed update? The branch has already been updated." if selected[2].published_head else "Abort this background update? Its merge resolutions will be discarded. The original branch and your current worktree will be preserved."
        if not messagebox.askyesno("Resolve conflicts", prompt, icon="warning", parent=self):
            return
        if self._begin_operation():
            self.owner._submit("Aborting background branch update...", lambda: abort_update(selected[2]), self._operation_finished, on_error=self._operation_failed)
