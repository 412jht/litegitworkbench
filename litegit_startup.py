from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


_log_file: Path | None = None
_stages: set[str] = set()
_lock = threading.Lock()


def begin_startup() -> None:
    global _log_file
    selected = os.environ.get("LITEGIT_STARTUP_LOG")
    packaged = bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))
    if not selected and (not packaged or sys.platform != "win32"):
        return
    if selected:
        _log_file = Path(selected)
    else:
        directory = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LiteGitWorkbench" / "diagnostics"
        _log_file = directory / f"startup-{time.time_ns()}-{os.getpid()}.jsonl"
    startup_phase("python_entry")


def startup_phase(stage: str) -> None:
    """Record each phase once, without repository paths, arguments or credentials."""
    if _log_file is None:
        return
    with _lock:
        if stage in _stages:
            return
        _stages.add(stage)
        try:
            _log_file.parent.mkdir(parents=True, exist_ok=True)
            with _log_file.open("a", encoding="ascii") as stream:
                stream.write(json.dumps({"time_ns": time.time_ns(), "pid": os.getpid(), "stage": stage}) + "\n")
        except OSError:
            pass  # Diagnostics must not prevent the application from starting.


def recorded_stages() -> list[str]:
    with _lock:
        return sorted(_stages)
