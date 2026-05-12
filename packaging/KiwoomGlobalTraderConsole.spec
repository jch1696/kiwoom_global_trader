# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files
from pathlib import Path


repo_root = Path(SPECPATH).parent

hiddenimports = [
    "google.oauth2.service_account",
    "google.auth.transport.requests",
    "googleapiclient.discovery",
    "googleapiclient.errors",
    "googleapiclient.http",
    "google_auth_httplib2",
    "httplib2",
    "pywinauto",
    "pywinauto.application",
    "pywinauto.clipboard",
    "pywinauto.controls.common_controls",
    "pywinauto.controls.hwndwrapper",
    "pywinauto.controls.uiawrapper",
    "pywinauto.controls.win32_controls",
    "pywinauto.findwindows",
    "pywinauto.keyboard",
    "pywinauto.mouse",
    "pywinauto.timings",
    "pywinauto.uia_defines",
    "pywinauto.uia_element_info",
    "pywinauto.win32_element_info",
    "pyperclip",
    "win32api",
    "win32clipboard",
    "win32con",
    "win32gui",
    "comtypes",
    "comtypes.client",
]
datas = [
    (str(repo_root / "README.md"), "."),
    (str(repo_root / "config.example.json"), "."),
    (str(repo_root / "requirements.txt"), "."),
]

for package in ["googleapiclient", "google_auth_httplib2"]:
    try:
        datas += collect_data_files(package)
    except Exception:
        pass


a = Analysis(
    [str(repo_root / "src" / "packaged.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
    excludes=[
        "google.generativeai",
        "google.genai",
        "cv2",
        "pandas",
        "pyarrow",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

gui_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KiwoomGlobalTraderConsole",
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
)

cli_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KiwoomGlobalTraderCli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="KiwoomGlobalTraderConsole",
)
