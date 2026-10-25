# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Scribd Downloader Windows app.

Build (on Windows):
    pyinstaller ScribdDownloader.spec
Produces: dist/ScribdDownloader.exe  (single file, no console window)

Notes
-----
* ``scribd-downloader.py`` is bundled as data and loaded by gui_app.py from
  ``sys._MEIPASS`` at runtime (its hyphenated name can't be imported).
* Selenium must be collected with its bundled ``selenium-manager`` binaries.
* CustomTkinter ships theme JSON/assets that must be bundled as a folder.
* Chrome itself is NOT bundled (as with the CLI): the user needs Google
  Chrome installed; Selenium Manager fetches the matching ChromeDriver.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
project = Path(SPECPATH)

datas = [
    (str(project / "scribd-downloader.py"), "."),
    (str(project / "assets"), "assets"),
]
binaries = []
hiddenimports = []

for package in ("selenium", "customtkinter", "pypdf"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(project / "gui_app.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
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
    name="ScribdDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed GUI app — no console flashes on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project / "assets" / "icon.ico"),
    version_file=None,
)
