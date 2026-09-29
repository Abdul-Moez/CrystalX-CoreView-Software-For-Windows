# CrystalX CoreView LCD for Windows
# Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
# SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
"""Put the licence of everything bundled into the app into its licenses folder.

The installed app contains Python and several libraries, and the installer
contains the PawnIO installer. Their licences require their notices to travel
with them, so this copies each licence into <app>/licenses and writes a
THIRD-PARTY-NOTICES.txt summary.

Run with the Python that did the PyInstaller build, after the build:
    python packaging\\collect_licenses.py "dist\\CrystalX LCD"
"""

import glob
import importlib.metadata as metadata
import os
import sys
import tkinter

HERE = os.path.dirname(os.path.abspath(__file__))
PAWNIO_VERSION = "2.2.0"        # keep in step with the release workflow


def dist_file(dist, path):
    """A licence file shipped inside an installed package."""
    def read():
        for f in metadata.distribution(dist).files or []:
            if f.as_posix().endswith(path):
                return f.read_text(encoding="utf-8")
        raise FileNotFoundError(f"{path} not found in {dist}")
    return read


def plain_file(*patterns):
    """The first file matching any of `patterns` -- Python installs differ a
    little in where they keep these, so each is given a fallback."""
    def read():
        for pattern in patterns:
            for path in sorted(glob.glob(pattern)):
                with open(path, encoding="utf-8", errors="replace") as f:
                    return f.read()
        raise FileNotFoundError(f"no licence file at {patterns}")
    return read


def dist_version(dist):
    return lambda: metadata.version(dist)


# name, version, licence, source code, licence text
COMPONENTS = [
    ("Python", lambda: sys.version.split()[0], "PSF-2.0",
     "https://www.python.org/",
     plain_file(os.path.join(sys.base_prefix, "LICENSE.txt"),
                os.path.join(sys.base_prefix, "LICENSE*"))),
    ("Tcl-Tk", lambda: str(tkinter.TkVersion), "Tcl/Tk licence (BSD-style)",
     "https://www.tcl-lang.org/",
     plain_file(os.path.join(sys.base_prefix, "tcl", "tk*", "license.terms"),
                os.path.join(sys.base_prefix, "tcl", "*", "license.terms"))),
    ("Pillow", dist_version("pillow"), "MIT-CMU",
     "https://github.com/python-pillow/Pillow", dist_file("pillow", "licenses/LICENSE")),
    ("pyserial", dist_version("pyserial"), "BSD-3-Clause",
     "https://github.com/pyserial/pyserial",
     plain_file(os.path.join(HERE, "licenses", "pyserial-LICENSE.txt"))),
    ("psutil", dist_version("psutil"), "BSD-3-Clause",
     "https://github.com/giampaolo/psutil", dist_file("psutil", "LICENSE")),
    ("pywin32", dist_version("pywin32"), "PSF-2.0",
     "https://github.com/mhammond/pywin32", dist_file("pywin32", "pythonwin/License.txt")),
    ("pythonnet", dist_version("pythonnet"), "MIT",
     "https://github.com/pythonnet/pythonnet", dist_file("pythonnet", "licenses/LICENSE")),
    ("clr_loader", dist_version("clr_loader"), "MIT",
     "https://github.com/pythonnet/clr-loader", dist_file("clr_loader", "licenses/LICENSE")),
    ("cffi", dist_version("cffi"), "MIT-0",
     "https://github.com/python-cffi/cffi", dist_file("cffi", "licenses/LICENSE")),
    ("pycparser", dist_version("pycparser"), "BSD-3-Clause",
     "https://github.com/eliben/pycparser", dist_file("pycparser", "licenses/LICENSE")),
    ("PyInstaller-bootloader", dist_version("pyinstaller"),
     "GPL-2.0 with the bootloader exception",
     "https://github.com/pyinstaller/pyinstaller",
     dist_file("pyinstaller", "licenses/COPYING.txt")),
    ("PawnIO", lambda: PAWNIO_VERSION,
     "GPL-2.0 (its installer is included and run only if PawnIO is missing)",
     "https://github.com/namazso/PawnIO",
     plain_file(os.path.join(HERE, "licenses", "PawnIO-COPYING.txt"))),
]


def main(app_folder):
    out = os.path.join(app_folder, "licenses")
    os.makedirs(out, exist_ok=True)
    # The app's own licence, next to the programs where people look for it.
    with open(os.path.join(HERE, "..", "LICENSE"), encoding="utf-8") as src, \
            open(os.path.join(app_folder, "LICENSE.txt"), "w", encoding="utf-8") as dst:
        dst.write(src.read())
    summary = [
        "CrystalX LCD - third-party components",
        "=" * 38,
        "",
        "CrystalX LCD itself is Copyright (C) 2026 Abdul Moez and licensed",
        "under the GNU GPL v3.0 or later (see LICENSE.txt). It bundles the",
        "components below, each under its own licence; the full texts are in",
        "this folder.",
        "",
    ]
    for name, version, licence, url, text in COMPONENTS:
        filename = f"{name}.txt"
        with open(os.path.join(out, filename), "w", encoding="utf-8") as f:
            f.write(text())
        summary += [f"{name} {version()}", f"  Licence: {licence}",
                    f"  Source:  {url}", f"  Text:    licenses\\{filename}", ""]
    summary += [
        "LibreHardwareMonitor and the libraries it needs",
        "  Licences: MPL-2.0, Apache-2.0 and MIT",
        "  Details:  _internal\\lib\\LibreHardwareMonitor\\THIRD-PARTY-NOTICES.md",
        "",
    ]
    with open(os.path.join(out, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary))
    print(f"wrote {len(COMPONENTS)} licences to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
