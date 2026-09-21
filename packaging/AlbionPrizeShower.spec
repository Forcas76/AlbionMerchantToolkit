"""PyInstaller one-folder build used by local and GitHub Actions releases."""

import os
from pathlib import Path


root = Path(SPECPATH).parent
datas = [
    (str(root / "data" / "catalog.db"), "data"),
    (str(root / "scripts" / "data_sorter.py"), "scripts"),
]
if os.environ.get("APS_INCLUDE_SOURCE_JSON") == "1":
    for optional_name in ("items.json", "localization.json"):
        optional_file = root / optional_name
        if optional_file.exists():
            datas.append((str(optional_file), "."))

a = Analysis(
    [str(root / "scripts" / "pyqt_app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AlbionPrizeShower",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AlbionPrizeShower",
)
