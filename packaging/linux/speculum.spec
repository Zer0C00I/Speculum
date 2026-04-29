from pathlib import Path

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata


project_root = Path(__file__).resolve().parents[2]


def merge_collect(package_name: str, datas: list, binaries: list, hiddenimports: list) -> None:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports


datas: list = []
binaries: list = []
hiddenimports: list = []

for package in [
    "pdftranslator",
    "babeldoc",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "skimage",
    "sklearn",
    "xsdata",
    "rtree",
]:
    merge_collect(package, datas, binaries, hiddenimports)

datas += copy_metadata("pdftranslator")
datas += copy_metadata("openai")
datas += copy_metadata("anthropic")
datas += copy_metadata("onnxruntime")
datas += copy_metadata("rapidocr-onnxruntime")


a = Analysis(
    [str(project_root / "pdftranslator" / "main.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="speculum",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="speculum",
)
