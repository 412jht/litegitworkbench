# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(SPECPATH).resolve().parent
WINDOWS_VERSION = PROJECT_ROOT / "packaging" / "windows_version_info.txt"

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
    a.binaries,
    a.datas,
    [],
    name="LiteGitWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(WINDOWS_VERSION) if sys.platform == "win32" else None,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="LiteGit Workbench.app",
        icon=None,
        bundle_identifier="dev.litegit.workbench",
        info_plist={
            "CFBundleShortVersionString": "2.0.0",
            "CFBundleVersion": "2.0.0",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
