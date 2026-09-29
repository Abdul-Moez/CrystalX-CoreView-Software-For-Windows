# -*- mode: python -*-
# CrystalX CoreView LCD for Windows
# Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
# SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
#
# PyInstaller recipe for the installable app: two programs sharing one folder
# and one bundled Python.
#
#   CrystalXLCD-Service.exe   the Windows service (service_win.py)
#   CrystalXLCD.exe           the tray icon (tray_win.py)
#
# Build from the project folder:
#   pip install -r packaging\requirements-build.txt
#   pyinstaller packaging\crystalx-lcd.spec --noconfirm
import os
import re

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "assets", "crystalx-lcd.ico")
with open(os.path.join(ROOT, "ipc_win.py"), encoding="utf-8") as f:
    VERSION = re.search(r'^VERSION = "([\d.]+)"', f.read(), re.M).group(1)


def version_info(description, filename):
    """The file properties Windows shows -- and the name Task Manager lists
    the process under, instead of a blank or "python"."""
    numbers = tuple(int(n) for n in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Abdul Moez"),
                StringStruct("FileDescription", description),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", filename),
                StringStruct("LegalCopyright",
                             "Copyright (C) 2026 Abdul Moez. GPL-3.0-or-later."),
                StringStruct("OriginalFilename", filename),
                StringStruct("ProductName", "CrystalX LCD"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ])

datas = [
    (os.path.join(ROOT, "lib", "LibreHardwareMonitor"),
     os.path.join("lib", "LibreHardwareMonitor")),
    (os.path.join(ROOT, "retro_pixel_guy_smoking_on_rooftop.gif"), "."),
    (ICON, "assets"),
    (os.path.join(ROOT, "LICENSE"), "."),
]

service = Analysis(
    [os.path.join(ROOT, "service_win.py")],
    pathex=[ROOT],
    datas=datas,
    # pywin32's service host needs this, and nothing imports it visibly.
    hiddenimports=["win32timezone"],
)
tray = Analysis(
    [os.path.join(ROOT, "tray_win.py")],
    pathex=[ROOT],
    datas=datas,
)

service_exe = EXE(
    PYZ(service.pure), service.scripts, [],
    exclude_binaries=True,
    name="CrystalXLCD-Service",
    icon=ICON,
    version=version_info("CrystalX LCD service", "CrystalXLCD-Service.exe"),
    console=True,           # no window as a service; prints for install/remove
    upx=False,              # packed executables upset antivirus scanners
)
tray_exe = EXE(
    PYZ(tray.pure), tray.scripts, [],
    exclude_binaries=True,
    name="CrystalXLCD",
    icon=ICON,
    version=version_info("CrystalX LCD", "CrystalXLCD.exe"),
    console=False,
    upx=False,
)
COLLECT(
    service_exe, service.binaries, service.datas,
    tray_exe, tray.binaries, tray.datas,
    name="CrystalX LCD",
    upx=False,
)
