# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for cs2pov Windows .exe build.

Usage:
    pip install .[gui,windows] pyinstaller
    pyinstaller cs2pov.spec

Output:
    dist/cs2pov.exe
"""

import sys

block_cipher = None

a = Analysis(
    ["cs2pov/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # GUI framework
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # Windows automation
        "win32gui",
        "win32api",
        "win32con",
        # Audio capture
        "sounddevice",
        "_sounddevice_data",
        # Process management
        "psutil",
        # Demo parsing (Rust extension)
        "demoparser2",
        # Standard lib modules that PyInstaller sometimes misses
        "json",
        "wave",
        "tempfile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="cs2pov",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console=True so CLI output works; GUI creates its own window
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
