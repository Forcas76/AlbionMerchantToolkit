"""PyInstaller one-folder build used by local and GitHub Actions releases."""

import os
import re
from pathlib import Path


root = Path(SPECPATH).parent
app_version = (root / "VERSION").read_text(encoding="utf-8").strip()
version_match = re.fullmatch(
    r"(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
    app_version,
)
if version_match is None:
    raise ValueError(f"Érvénytelen VERSION érték: {app_version}")
major, minor, patch = (int(value) for value in version_match.groups())
serial_match = re.search(r"-[^+]*[.-](\d+)(?:\+.*)?$", app_version)
serial = int(serial_match.group(1)) if serial_match else 0
numeric_version = (major, minor, patch, serial)
metadata_dir = root / "build"
metadata_dir.mkdir(parents=True, exist_ok=True)
version_info_file = metadata_dir / "AlbionMerchantToolkit-version-info.txt"
version_info_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric_version},
    prodvers={numeric_version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Albion Merchant Toolkit'),
         StringStruct('FileDescription', 'Albion Merchant Toolkit ({app_version})'),
         StringStruct('FileVersion', '{app_version}'),
         StringStruct('InternalName', 'AlbionMerchantToolkit'),
         StringStruct('OriginalFilename', 'AlbionMerchantToolkit.exe'),
         StringStruct('ProductName', 'Albion Merchant Toolkit'),
         StringStruct('ProductVersion', '{app_version}')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)
datas = [
    (str(root / "VERSION"), "."),
    (str(root / "data" / "catalog.db"), "data"),
    (str(root / "scripts" / "data_sorter.py"), "scripts"),
    (str(root / "assets" / "branding"), "assets/branding"),
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
    name="AlbionMerchantToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=str(root / "assets" / "branding" / "AlbionMerchantToolkit_icon.ico"),
    version=str(version_info_file),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AlbionMerchantToolkit",
)
