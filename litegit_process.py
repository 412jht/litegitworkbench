from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Mapping, Sequence


_runtime_lock = threading.Lock()
_dll_directory_handle: object | None = None
_windows_runtime_ready = False


def is_packaged_application() -> bool:
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))


def _bundle_directory() -> Path | None:
    compiled = globals().get("__compiled__")
    if compiled is not None:
        return Path(compiled.containing_dir).resolve()
    directory = getattr(sys, "_MEIPASS", None)
    return Path(directory).resolve() if getattr(sys, "frozen", False) and directory else None


def _outside_bundle(value: str, bundle: Path) -> bool:
    if not value:
        return True
    try:
        return not Path(value.strip('"')).resolve().is_relative_to(bundle)
    except (OSError, ValueError):
        return True


def external_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    bundle = _bundle_directory()
    if bundle is None:
        return environment
    for name in ("PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        if name in environment:
            environment[name] = os.pathsep.join(
                entry for entry in environment[name].split(os.pathsep) if _outside_bundle(entry, bundle)
            )
    for name in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME"):
        if name in environment and not _outside_bundle(environment[name], bundle):
            environment.pop(name)
    if sys.platform not in {"win32", "darwin"}:
        name = "LIBPATH" if sys.platform.startswith("aix") else "LD_LIBRARY_PATH"
        original = environment.get(name + "_ORIG")
        if original is None:
            environment.pop(name, None)
        else:
            environment[name] = original
    return environment


def prepare_external_processes() -> None:
    global _dll_directory_handle, _windows_runtime_ready
    bundle = _bundle_directory()
    if sys.platform != "win32" or bundle is None:
        return
    with _runtime_lock:
        if _windows_runtime_ready:
            return
        # Python retains its own DLL directory without exporting it to Git.
        handle = os.add_dll_directory(str(bundle))
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            reset = kernel.SetDllDirectoryW
            reset.argtypes = [ctypes.c_wchar_p]
            reset.restype = ctypes.c_int
            if not reset(None):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            handle.close()
            raise
        _dll_directory_handle = handle
        _windows_runtime_ready = True


def find_executable(name: str, environment: Mapping[str, str]) -> Path | None:
    filename = name + ".exe" if os.name == "nt" and not name.lower().endswith(".exe") else name
    for entry in environment.get("PATH", os.defpath).split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry.strip('"')) / filename
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def external_runtime_status() -> dict[str, object]:
    status: dict[str, object] = {
        "frozen": is_packaged_application(),
        "packager": "Nuitka" if globals().get("__compiled__") is not None else "PyInstaller" if getattr(sys, "frozen", False) else "source",
    }
    bundle = _bundle_directory()
    if bundle is not None:
        status["bundle_in_application_directory"] = bundle.is_relative_to(Path(sys.executable).resolve().parent)
    if sys.platform == "win32" and bundle is not None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.GetDllDirectoryW
        query.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        query.restype = ctypes.c_uint
        directory = ctypes.create_unicode_buffer(32768)
        length = query(len(directory), directory)
        status["windows_dll_search_reset"] = _windows_runtime_ready and length == 0 and directory.value == ""
    return status


def launch_external(arguments: Sequence[str]) -> subprocess.Popen[bytes]:
    environment = external_environment()
    executable = find_executable(arguments[0], environment)
    if executable is None:
        raise FileNotFoundError(f"The external program was not found on PATH: {arguments[0]}")
    prepare_external_processes()
    return subprocess.Popen([str(executable), *arguments[1:]], env=environment)
