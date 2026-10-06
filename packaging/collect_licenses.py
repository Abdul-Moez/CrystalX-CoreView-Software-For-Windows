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
import shutil
import sys
import tkinter

HERE = os.path.dirname(os.path.abspath(__file__))
PAWNIO_VERSION = "2.2.0"        # keep in step with the release workflow

# FFmpeg and the libraries built into it, which play and convert videos. They
# come inside PyAV's wheel (its av.libs folder), built by the pyav-ffmpeg
# project. The versions and sources below are those of the build PyAV
# FFMPEG_FOR_PYAV uses: PyAV's scripts/ffmpeg-latest.json names the pyav-ffmpeg
# release, whose scripts/pkg.py names every source. Their licence texts,
# fetched from those very sources, are in licenses/ffmpeg.
#
# When PyAV is upgraded: look both files up again, correct this list, fetch the
# licence texts again, and only then change FFMPEG_FOR_PYAV. The build stops
# if the installed PyAV is not the one this list was made for.
FFMPEG_FOR_PYAV = "19.0.0"
FFMPEG_BUILD = "https://github.com/PyAV-Org/pyav-ffmpeg/tree/9.0.2-1"
_X264 = "b35605ace3ddf7c1a5d67a2eb553f034aef41d55"
TOOLCHAIN = "(the build toolchain's)"
# name, version, licence, source code, licence files
FFMPEG_PARTS = [
    ("FFmpeg", "9.0.2", "LGPL-3.0-or-later (as this build is configured)",
     "https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz",
     ["FFmpeg-LICENSE.md", "FFmpeg-COPYING.LGPLv3.txt"]),
    ("x264", _X264, "GPL-2.0-or-later",
     f"https://code.videolan.org/videolan/x264/-/archive/{_X264}/x264-{_X264}.tar.bz2",
     ["x264-COPYING.txt"]),
    ("x265", "4.3", "GPL-2.0-or-later",
     "https://github.com/Multicorewareinc/x265/releases/download/4.3/x265_4.3.tar.gz",
     ["x265-COPYING.txt"]),
    ("lamer (LAME)", "3.101.0", "LGPL-2.1",
     "https://github.com/basswood-io/lamer/archive/refs/tags/v3.101.0.tar.gz",
     ["lamer-LICENSE.txt"]),
    ("Opus", "1.6.1", "BSD-3-Clause",
     "https://ftp.osuosl.org/pub/xiph/releases/opus/opus-1.6.1.tar.gz", ["opus-COPYING.txt"]),
    ("dav1d", "1.5.4", "BSD-2-Clause",
     "https://code.videolan.org/videolan/dav1d/-/archive/1.5.4/dav1d-1.5.4.tar.bz2",
     ["dav1d-COPYING.txt"]),
    ("SVT-AV1", "4.2.0", "BSD-3-Clause-Clear, with the AOM Patent License 1.0",
     "https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v4.2.0/SVT-AV1-v4.2.0.tar.bz2",
     ["SVT-AV1-LICENSE.md", "SVT-AV1-PATENTS.md"]),
    ("libvpx", "1.17.0", "BSD-3-Clause",
     "https://github.com/webmproject/libvpx/archive/refs/tags/v1.17.0.tar.gz",
     ["libvpx-LICENSE.txt", "libvpx-PATENTS.txt"]),
    ("libpng", "1.6.58", "libpng licence",
     "https://downloads.sourceforge.net/project/libpng/libpng16/1.6.58/libpng-1.6.58.tar.xz",
     ["libpng-LICENSE.txt"]),
    ("libwebp", "1.6.0", "BSD-3-Clause",
     "https://github.com/webmproject/libwebp/archive/refs/tags/v1.6.0.tar.gz",
     ["libwebp-COPYING.txt", "libwebp-PATENTS.txt"]),
    ("libvmaf", "3.2.1", "BSD-2-Clause-Patent",
     "https://github.com/Netflix/vmaf/archive/refs/tags/v3.2.1.tar.gz", ["libvmaf-LICENSE.txt"]),
    ("libvpl", "2.16.0", "MIT",
     "https://github.com/intel/libvpl/archive/refs/tags/v2.16.0.tar.gz", ["libvpl-LICENSE.txt"]),
    ("AMF headers", "1.5.2", "MIT",
     "https://github.com/GPUOpen-LibrariesAndSDKs/AMF/releases/download/v1.5.2/"
     "AMF-headers-v1.5.2.tar.gz", ["AMF-LICENSE.txt"]),
    ("nv-codec-headers", "13.0.19.0", "MIT (stated in each header)",
     "https://github.com/FFmpeg/nv-codec-headers/archive/refs/tags/n13.0.19.0.tar.gz", []),
    ("zlib", TOOLCHAIN, "Zlib", "https://zlib.net/", ["zlib-LICENSE.txt"]),
    ("libiconv", TOOLCHAIN, "LGPL-2.1-or-later",
     "https://www.gnu.org/software/libiconv/", ["libiconv-COPYING.LIB.txt"]),
    ("GCC runtime libraries (libgcc, libstdc++)", TOOLCHAIN,
     "GPL-3.0-or-later with the GCC Runtime Library Exception 3.1",
     "https://gcc.gnu.org/", ["GCC-COPYING.RUNTIME.txt"]),
    ("winpthreads", TOOLCHAIN, "MIT and BSD-3-Clause",
     "https://www.mingw-w64.org/", ["winpthreads-COPYING.txt"]),
]


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
    ("PyAV", dist_version("av"), "BSD-3-Clause",
     "https://github.com/PyAV-Org/PyAV", dist_file("av", "licenses/LICENSE.txt")),
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
    summary += ffmpeg_notices(out)
    summary += [
        "Weather data",
        "-" * 12,
        "Nothing is bundled for it: while its block is switched on, the app reads",
        "the weather from Open-Meteo (https://open-meteo.com/), whose data is",
        "licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).",
        "",
    ]
    with open(os.path.join(out, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary))
    print(f"wrote {len(COMPONENTS)} licences and {len(FFMPEG_PARTS)} of FFmpeg's to {out}")


def ffmpeg_notices(out):
    """Copy FFmpeg's and its libraries' licence texts to <out>/ffmpeg and
    return the lines that describe them."""
    installed = metadata.version("av")
    if installed != FFMPEG_FOR_PYAV:
        sys.exit(f"PyAV {installed} is installed, but the list of what its FFmpeg is made "
                 f"of (FFMPEG_PARTS in {os.path.basename(__file__)}) was written for PyAV "
                 f"{FFMPEG_FOR_PYAV}. Check the list and the licence texts against the new "
                 "build, then change FFMPEG_FOR_PYAV.")
    folder = os.path.join(out, "ffmpeg")
    os.makedirs(folder, exist_ok=True)
    lines = [
        "FFmpeg and the libraries built into it (for video)",
        "-" * 50,
        f"These are the unchanged files of PyAV {installed}'s Windows package, in",
        "_internal\\av.libs. They were built by the pyav-ffmpeg project:",
        f"  {FFMPEG_BUILD}",
        "which also holds the exact recipe (scripts) and the few patches it applies",
        "(patches). The source code of each part, at the version used, is at the",
        "address given with it. The licence texts are in licenses\\ffmpeg.",
        "",
        "x264 and x265 are under the GNU General Public License, so these libraries",
        "are passed on here, together, under the GNU GPL version 3 -- which is also",
        "CrystalX LCD's own licence (LICENSE.txt).",
        "",
    ]
    for name, version, licence, url, files in FFMPEG_PARTS:
        for file in files:
            shutil.copyfile(os.path.join(HERE, "licenses", "ffmpeg", file),
                            os.path.join(folder, file))
        lines += [f"{name} {version}", f"  Licence: {licence}", f"  Source:  {url}"]
        if files:
            lines.append("  Text:    " + ", ".join(f"licenses\\ffmpeg\\{file}" for file in files))
        lines.append("")
    return lines


if __name__ == "__main__":
    main(sys.argv[1])
