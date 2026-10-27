from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk
from typing import Sequence


class DeferredPanedwindow(ttk.Panedwindow):
    """Preview a divider drag without repeatedly resizing its content widgets."""

    def __init__(self, parent: tk.Misc, **options: object) -> None:
        super().__init__(parent, **options)
        self._drag_index: int | None = None
        self._drag_offset = 0
        self._preview_position = 0
        self._escape_binding: str | None = None
        self._toplevel = self.winfo_toplevel()
        self._drag_tag = "LiteGitDividerDrag:" + str(self)
        self._drag_widgets: list[tk.Misc] = []
        self._drag_bindings: list[tuple[str, str]] = []
        self._preview_line = tk.Frame(self, background="#377DC5", borderwidth=0, highlightthickness=0)
        self.bind("<ButtonPress-1>", self._start_drag, add="+")
        self.bind("<B1-Motion>", self._move_drag, add="+")
        self.bind("<ButtonRelease-1>", self._finish_drag, add="+")
        self.bind("<Unmap>", self._cancel_drag, add="+")
        self.bind("<Destroy>", self._cancel_drag, add="+")

    def _coordinate(self, event: tk.Event) -> int:
        return event.x_root - self.winfo_rootx() if str(self.cget("orient")) == tk.HORIZONTAL else event.y_root - self.winfo_rooty()

    def _start_drag(self, event: tk.Event) -> str | None:
        index = self.identify(event.x, event.y)
        if index == "":
            return None
        self._cancel_drag()
        self._drag_index = int(index)
        self._preview_position = self.sashpos(self._drag_index)
        self._drag_offset = self._coordinate(event) - self._preview_position
        # Capture pointer events before child text/tree bindings whilst dragging.
        # A Tk grab alone still sends events to descendants under the pointer.
        pending: list[tk.Misc] = [self]
        while pending:
            widget = pending.pop()
            self._drag_widgets.append(widget)
            widget.bindtags((self._drag_tag, *widget.bindtags()))
            pending.extend(widget.winfo_children())
        for sequence, callback in (("<B1-Motion>", self._move_drag), ("<ButtonRelease-1>", self._finish_drag)):
            identifier = self.bind_class(self._drag_tag, sequence, callback)
            self._drag_bindings.append((sequence, identifier))
        self.grab_set()
        self._escape_binding = self._toplevel.bind("<Escape>", self._cancel_drag, add="+")
        self._show_preview()
        return "break"

    def _show_preview(self) -> None:
        thickness = max(2, round(self.winfo_fpixels("2p")))
        if str(self.cget("orient")) == tk.HORIZONTAL:
            self._preview_line.place(x=self._preview_position, y=0, width=thickness, relheight=1)
        else:
            self._preview_line.place(x=0, y=self._preview_position, relwidth=1, height=thickness)
        self._preview_line.lift()

    def _move_drag(self, event: tk.Event) -> str | None:
        if self._drag_index is None:
            return None
        index = self._drag_index
        extent = self.winfo_width() if str(self.cget("orient")) == tk.HORIZONTAL else self.winfo_height()
        lower = self.sashpos(index - 1) + 16 if index else 16
        upper = self.sashpos(index + 1) - 16 if index + 1 < len(self.panes()) - 1 else extent - 16
        self._preview_position = max(lower, min(upper, self._coordinate(event) - self._drag_offset))
        self._show_preview()
        return "break"

    def _finish_drag(self, event: tk.Event) -> str | None:
        if self._drag_index is None:
            return None
        self._move_drag(event)
        index, position = self._drag_index, self._preview_position
        self._cancel_drag()
        self.sashpos(index, position)
        return "break"

    def _cancel_drag(self, event: tk.Event | None = None) -> str | None:
        if event is not None and event.type in {tk.EventType.Unmap, tk.EventType.Destroy} and event.widget is not self:
            return None
        active = self._drag_index is not None
        self._drag_index = None
        try:
            self._preview_line.place_forget()
        except tk.TclError:
            pass
        try:
            if self._toplevel.grab_current() is self:
                self.grab_release()
        except (tk.TclError, KeyError):
            pass
        for widget in self._drag_widgets:
            try:
                widget.bindtags(tuple(tag for tag in widget.bindtags() if tag != self._drag_tag))
            except tk.TclError:
                pass
        self._drag_widgets.clear()
        for sequence, identifier in self._drag_bindings:
            try:
                self.unbind_class(self._drag_tag, sequence)
                self._root().deletecommand(identifier)
            except tk.TclError:
                pass
        self._drag_bindings.clear()
        try:
            if self._escape_binding is not None:
                self._toplevel.unbind("<Escape>", self._escape_binding)
        except (tk.TclError, KeyError):
            pass
        self._escape_binding = None
        return "break" if active else None


class ResponsiveSummary(ttk.Frame):
    """Keep all repository fields inside the available width, wrapping values."""

    def __init__(self, parent: tk.Misc, fields: Sequence[tuple[str, tk.StringVar]]) -> None:
        super().__init__(parent, padding=(8, 2, 8, 7))
        self.fields = tuple(fields)
        self.cells: list[ttk.Frame] = []
        self.labels: list[tuple[ttk.Label, ttk.Label]] = []
        self.columns = len(fields)
        self._layout_job: str | None = None
        self._signature: tuple[object, ...] | None = None
        self._grid_widths: tuple[int, ...] = ()
        self._wraps: list[tuple[int, int] | None] = [None] * len(fields)
        style = ttk.Style(self)
        self._caption_font = font.Font(self, font=style.lookup("TLabel", "font") or "TkDefaultFont")
        self._value_font = font.Font(self, font=style.lookup("Summary.TLabel", "font") or "TkDefaultFont")
        self._traces: list[tuple[tk.StringVar, str]] = []
        for index, (caption, variable) in enumerate(fields):
            cell = ttk.Frame(self)
            cell.grid(row=0, column=index, sticky="nw", padx=(0, 12), pady=2)
            cell.columnconfigure(1, weight=1)
            title = ttk.Label(cell, text=caption + ":")
            value = ttk.Label(cell, textvariable=variable, style="Summary.TLabel")
            title.grid(row=0, column=0, sticky="nw", padx=(0, 5))
            value.grid(row=0, column=1, sticky="nw")
            self.cells.append(cell)
            self.labels.append((title, value))
            self._traces.append((variable, variable.trace_add("write", self._schedule_layout)))
        self.bind("<Configure>", self._schedule_layout, add="+")
        self.bind("<Map>", self._schedule_layout, add="+")
        self.bind("<Destroy>", self._dispose, add="+")

    def _schedule_layout(self, *_arguments: object) -> None:
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._layout)

    def _layout(self) -> None:
        self._layout_job = None
        if not self.winfo_ismapped():
            return
        available = max(1, self.winfo_width() - 16)
        values = tuple(variable.get() for _caption, variable in self.fields)
        signature = (available, values)
        if signature == self._signature:
            return
        self._signature = signature
        captions = [self._caption_font.measure(caption + ":") + 8 for caption, _variable in self.fields]
        minimum_value = self._value_font.measure("origin/feature")
        minimums = [caption + minimum_value + 17 for caption in captions]
        value_sizes = [self._value_font.measure(value) for value in values]
        desired = [min(380, caption + size + 17) for caption, size in zip(captions, value_sizes)]
        columns = 1
        for count in (len(self.fields), 3, 2, 1):
            if count == len(self.fields):
                fits = sum(desired) <= available
            else:
                fits = sum(max(minimums[index::count]) for index in range(count)) <= available
            if fits or count == 1:
                columns = count
                break
        natural = [max(desired[index::columns]) for index in range(columns)]
        bases = [max(minimums[index::columns]) for index in range(columns)]
        if sum(natural) <= available:
            # Keep short fields at their natural size. Resizing the viewport
            # then leaves their geometry and wrapping untouched.
            widths = natural
        elif sum(bases) > available:
            widths = [available // columns] * columns
        else:
            extra = (available - sum(bases)) // columns
            widths = [width + extra for width in bases]
            widths[-1] += available - sum(widths)
        grid_widths = tuple(widths + [0] * (len(self.fields) - columns))
        if grid_widths != self._grid_widths:
            for column, width in enumerate(grid_widths):
                self.columnconfigure(column, weight=0, minsize=width)
            self._grid_widths = grid_widths
        for index, (cell, labels) in enumerate(zip(self.cells, self.labels)):
            if columns != self.columns:
                cell.grid_configure(row=index // columns, column=index % columns)
            width = max(1, widths[index % columns] - 12)
            caption_width = max(1, min(captions[index], width // 2))
            value_width = max(1, width - caption_width - 5)
            wraps = (caption_width if captions[index] - 8 > caption_width else 0, value_width if value_sizes[index] > value_width else 0)
            if wraps != self._wraps[index]:
                labels[0].configure(wraplength=wraps[0])
                labels[1].configure(wraplength=wraps[1])
                self._wraps[index] = wraps
        self.columns = columns

    def _dispose(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        for variable, identifier in self._traces:
            try:
                variable.trace_remove("write", identifier)
            except tk.TclError:
                pass
