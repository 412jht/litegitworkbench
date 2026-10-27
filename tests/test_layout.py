from __future__ import annotations

import time
import tkinter as tk
import unittest
from tkinter import ttk

from litegit_layout import DeferredPanedwindow, ResponsiveSummary
from ui_events import process_events


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
            self.root.geometry("900x600")
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        self.errors: list[str] = []
        self.root.report_callback_exception = lambda *_arguments: self.errors.append(str(_arguments))
        self.maximum_width = min(self.root.winfo_screenwidth() - 80, self.root.maxsize()[0] - 32)

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()
        self.assertEqual([], self.errors)

    def settle(self, condition=lambda: True) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            process_events(self.root)
            self.root.update_idletasks()
            if condition():
                return
            time.sleep(0.005)
        self.fail("The widget geometry did not settle")

    def make_panes(self, orient: str, count: int = 2) -> DeferredPanedwindow:
        pane = DeferredPanedwindow(self.root, orient=orient)
        pane.pack(fill=tk.BOTH, expand=True)
        for _ in range(count):
            child = ttk.Frame(pane, width=200, height=160)
            pane.add(child, weight=1)
        self.settle(lambda: pane.winfo_ismapped() and pane.winfo_width() == self.root.winfo_width())
        return pane

    def pointer(self, pane: DeferredPanedwindow, event: str, position: int) -> None:
        coordinates = {"x": position, "y": 20} if str(pane.cget("orient")) == tk.HORIZONTAL else {"x": 20, "y": position}
        pane.event_generate(event, **coordinates)
        self.settle()

    def test_drag_defers_content_resize_until_release_in_both_orientations(self) -> None:
        for orient in (tk.HORIZONTAL, tk.VERTICAL):
            with self.subTest(orient=orient):
                pane = self.make_panes(orient)
                children = [pane.nametowidget(name) for name in pane.panes()]
                sizes = [(child.winfo_width(), child.winfo_height()) for child in children]
                events = []
                for child in children:
                    child.bind("<Configure>", lambda event: events.append(event))
                start = pane.sashpos(0)
                self.pointer(pane, "<ButtonPress-1>", start)
                self.assertIs(pane, self.root.grab_current())
                for offset in (10, 30, 60, 90):
                    self.pointer(pane, "<B1-Motion>", start + offset)
                    self.assertEqual(sizes, [(child.winfo_width(), child.winfo_height()) for child in children])
                self.assertEqual([], events)
                self.pointer(pane, "<ButtonRelease-1>", start + 90)
                self.assertEqual(start + 90, pane.sashpos(0))
                self.assertTrue(events)
                self.assertIsNone(self.root.grab_current())
                self.assertFalse(pane._preview_line.winfo_ismapped())
                pane.destroy()

    def test_three_pane_drag_stays_between_adjacent_dividers(self) -> None:
        pane = self.make_panes(tk.HORIZONTAL, 3)
        second = pane.sashpos(1)
        self.pointer(pane, "<ButtonPress-1>", pane.sashpos(0))
        self.pointer(pane, "<B1-Motion>", 2000)
        self.assertLess(pane._preview_position, second)
        self.pointer(pane, "<ButtonRelease-1>", 2000)
        self.assertLess(pane.sashpos(0), pane.sashpos(1))
        self.assertEqual(second, pane.sashpos(1))
        first = pane.sashpos(0)
        self.pointer(pane, "<ButtonPress-1>", pane.sashpos(1))
        self.pointer(pane, "<B1-Motion>", -2000)
        self.pointer(pane, "<ButtonRelease-1>", -2000)
        self.assertGreater(pane.sashpos(1), first)
        self.assertEqual(first, pane.sashpos(0))

    def test_drag_over_child_text_does_not_select_or_edit_its_content(self) -> None:
        pane = self.make_panes(tk.HORIZONTAL)
        child = pane.nametowidget(pane.panes()[1])
        text = tk.Text(child)
        text.insert("1.0", "Retain this draft\n" * 10)
        text.pack(fill=tk.BOTH, expand=True)
        self.settle()
        bindings = text.bindtags()
        start = pane.sashpos(0)
        self.pointer(pane, "<ButtonPress-1>", start)
        text.event_generate("<B1-Motion>", x=30, y=10)
        self.settle()
        self.assertEqual(start, pane.sashpos(0))
        self.assertFalse(text.tag_ranges("sel"))
        text.event_generate("<ButtonRelease-1>", x=30, y=10)
        self.settle()
        self.assertGreater(pane.sashpos(0), start)
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(bindings, text.bindtags())
        self.assertEqual("Retain this draft\n" * 10, text.get("1.0", "end-1c"))

    def test_hiding_or_destroying_a_dragged_pane_releases_the_grab(self) -> None:
        for destroy in (False, True):
            pane = self.make_panes(tk.HORIZONTAL)
            start = pane.sashpos(0)
            self.pointer(pane, "<ButtonPress-1>", start)
            self.pointer(pane, "<B1-Motion>", start + 70)
            if destroy:
                pane.destroy()
            else:
                pane.pack_forget()
            self.settle()
            self.assertIsNone(self.root.grab_current())
            self.assertEqual("", self.root.bind("<Escape>"))
            if not destroy:
                pane.pack(fill=tk.BOTH, expand=True)
                self.settle()
                self.assertEqual(start, pane.sashpos(0))
                self.assertFalse(pane._preview_line.winfo_ismapped())
                pane.destroy()

    def test_escape_cancels_the_drag_without_removing_existing_bindings(self) -> None:
        previous = self.root.bind("<Escape>", lambda _event: None, add="+")
        pane = self.make_panes(tk.HORIZONTAL)
        start = pane.sashpos(0)
        self.pointer(pane, "<ButtonPress-1>", start)
        self.pointer(pane, "<B1-Motion>", start + 70)
        # Invoke the registered key callback without taking keyboard focus
        # away from any other application running on the test desktop.
        binding = pane._escape_binding
        substitutions = {"%W": str(self.root), "%T": tk.EventType.KeyPress.value, "%K": "Escape"}
        self.root.tk.call(binding, *(substitutions.get(code, "0") for code in self.root._subst_format))
        self.settle()
        self.assertEqual(start, pane.sashpos(0))
        self.assertIsNone(self.root.grab_current())
        self.assertIn(previous, self.root.bind("<Escape>"))
        self.assertNotIn(binding, self.root.bind("<Escape>"))

    def test_summary_keeps_all_fields_and_labels_inside_narrow_and_wide_windows(self) -> None:
        captions = ("Branch", "HEAD", "Upstream", "Synchronisation", "Working tree", "Worktrees")
        values = (
            "feature/" + "a-long-branch-name-" * 5,
            "1234567890ab",
            "origin/feature/" + "a-long-branch-name-" * 5,
            "Ahead 12 / behind 34",
            "Uncommitted changes",
            "12",
        )
        variables = [tk.StringVar(self.root, value) for value in values]
        summary = ResponsiveSummary(self.root, list(zip(captions, variables)))
        summary.pack(fill=tk.X)
        seen_columns = set()
        for requested in (1600, 1050, 700, 460, 300, 1050):
            width = min(requested, self.maximum_width)
            with self.subTest(width=width):
                self.root.geometry(f"{width}x900")
                self.settle(lambda: summary.winfo_width() == width and summary._layout_job is None)
                seen_columns.add(summary.columns)
                height = summary.winfo_height()
                for cell, (caption, value), expected in zip(summary.cells, summary.labels, values):
                    self.assertTrue(cell.winfo_ismapped())
                    self.assertGreaterEqual(cell.winfo_x(), 0)
                    self.assertLessEqual(cell.winfo_x() + cell.winfo_width(), width)
                    self.assertLessEqual(cell.winfo_y() + cell.winfo_height(), height)
                    self.assertEqual(expected, value.cget("text"))
                    for label in (caption, value):
                        self.assertTrue(label.winfo_ismapped())
                        self.assertLessEqual(label.winfo_x() + label.winfo_reqwidth(), cell.winfo_width())
                        self.assertLessEqual(label.winfo_y() + label.winfo_reqheight(), cell.winfo_height())
                self.settle()
                self.assertEqual(height, summary.winfo_height())
                self.assertIsNone(summary._layout_job)
        self.assertGreater(len(seen_columns), 1)
        variables[0].set("updated/branch")
        self.settle(lambda: summary._layout_job is None)
        self.assertEqual("updated/branch", summary.labels[0][1].cget("text"))
        summary.destroy()
        self.assertTrue(all(not variable.trace_info() for variable in variables))

    def test_roomier_summary_keeps_short_field_geometry_unchanged(self) -> None:
        fields = [(caption, tk.StringVar(self.root, "-")) for caption in ("Branch", "HEAD", "Upstream", "Synchronisation", "Working tree", "Worktrees")]
        summary = ResponsiveSummary(self.root, fields)
        summary.pack(fill=tk.X)
        narrow, wide = min(1300, self.maximum_width - 120), min(1500, self.maximum_width)
        self.root.geometry(f"{narrow}x600")
        self.settle(lambda: summary.winfo_width() == narrow and summary._layout_job is None)
        before = [(cell.winfo_x(), cell.winfo_y(), cell.winfo_width(), cell.winfo_height()) for cell in summary.cells]
        self.root.geometry(f"{wide}x600")
        self.settle(lambda: summary.winfo_width() == wide and summary._layout_job is None)
        # Aqua can forward Configure events from a resized ancestor even when
        # the child geometry is unchanged. Compare the resulting bounds.
        self.assertEqual(before, [(cell.winfo_x(), cell.winfo_y(), cell.winfo_width(), cell.winfo_height()) for cell in summary.cells])


if __name__ == "__main__":
    unittest.main()
