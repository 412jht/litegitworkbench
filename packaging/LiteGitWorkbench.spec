# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(SPECPATH).resolve().parent
WINDOWS_VERSION = PROJECT_ROOT / "packaging" / "windows_version_info.txt"
IS_WINDOWS = sys.platform == "win32"

a = Analysis(
    [str(PROJECT_ROOT / "litegit.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["doctest", "pydoc", "unittest"],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    *([] if IS_WINDOWS else [a.binaries, a.datas]),
    [],
    exclude_binaries=IS_WINDOWS,
    name="LiteGitWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(WINDOWS_VERSION) if sys.platform == "win32" else None,
)

if IS_WINDOWS:
    distribution = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="LiteGitWorkbench",
    )

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="LiteGit Workbench.app",
        icon=None,
        bundle_identifier="dev.litegit.workbench",
        info_plist={
            "CFBundleShortVersionString": "2.1.3",
            "CFBundleVersion": "2.1.3",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
