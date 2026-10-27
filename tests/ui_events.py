from __future__ import annotations

import _tkinter
import time
import tkinter as tk


def process_events(root: tk.Misc) -> None:
    """Keep Aqua's continuous event stream from blocking test deadlines."""
    deadline = time.monotonic() + 0.05
    flags = _tkinter.ALL_EVENTS | _tkinter.DONT_WAIT
    for _ in range(100):
        if not root.tk.dooneevent(flags) or time.monotonic() >= deadline:
            return
