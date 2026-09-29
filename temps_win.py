# CrystalX CoreView LCD for Windows
# Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program (the LICENSE file). If not, see
# <https://www.gnu.org/licenses/>.
#
# Additional term under section 7(b) of the GNU GPL version 3: the author
# attribution "Abdul Moez (https://github.com/Abdul-Moez)" must be preserved
# in this file and in all copies and modified versions of it.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU and GPU temperatures, read through LibreHardwareMonitor.

Windows hands temperatures only to kernel-mode code, so unlike the other
stats there is no performance counter to read them from. LibreHardwareMonitor
does the reading; its DLLs ship in lib/LibreHardwareMonitor and are loaded
through pythonnet.

The CPU sensor also needs the PawnIO driver (installed separately, see the
README) and administrator rights. The GPU sensor needs neither on an AMD
card, so the two are looked up and reported separately: without admin rights
the GPU temperature still shows.
"""

import ctypes
import os

LHM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "lib", "LibreHardwareMonitor")

# Preferred sensor names, best first. Ryzen reports "Core (Tctl/Tdie)", the
# figure AMD's own tools show; Intel reports "CPU Package". Anything else
# falls back to the first temperature the device has.
CPU_SENSORS = ("Core (Tctl/Tdie)", "CPU Package", "Core (Tctl)",
               "Core (Tdie)", "Core Average")
GPU_SENSORS = ("GPU Core",)
# A discrete card beats an integrated one when both are present.
GPU_TYPES = ("GpuNvidia", "GpuAmd", "GpuIntel")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_pawnio():
    """Start the PawnIO driver if it is installed but stopped.

    Even the official installer registers it as a manual-start service, so
    after a reboot it is not running until something starts it -- and
    LibreHardwareMonitor never does. Needs administrator rights.

    Returns "running", "started", "missing" or "stopped" (would not start).
    """
    import pywintypes
    import win32service
    import win32serviceutil
    try:
        state = win32serviceutil.QueryServiceStatus("PawnIO")[1]
    except pywintypes.error:
        return "missing"
    if state == win32service.SERVICE_RUNNING:
        return "running"
    try:
        win32serviceutil.StartService("PawnIO")
        win32serviceutil.WaitForServiceStatus(
            "PawnIO", win32service.SERVICE_RUNNING, 5)
        return "started"
    except Exception:
        return "stopped"


def _pick(hardware, names):
    """The first of `names` this device has, else its first temperature."""
    temps = {str(s.Name): s for s in hardware.Sensors
             if str(s.SensorType) == "Temperature"}
    for name in names:
        if name in temps:
            return temps[name]
    return next(iter(temps.values()), None)


def _explain(error):
    text = str(error)
    if isinstance(error, ModuleNotFoundError):
        return "pythonnet is not installed -- run setup.cmd again"
    if "network location" in text:
        # .NET refuses DLLs that still carry Windows' "downloaded from the
        # internet" mark, which every file in a GitHub ZIP does.
        return "Windows is blocking the DLLs -- run setup.cmd again"
    return f"{type(error).__name__}: {text.splitlines()[0] if text else ''}"


class Temperatures:
    """CPU and GPU temperature sensors, held open for the life of the process."""

    def __init__(self):
        self.admin = is_admin()
        self.pawnio = ensure_pawnio() if self.admin else None
        self.error = None
        self._pc = None
        self._hw = {}           # "cpu" / "gpu" -> (hardware, sensor)
        try:
            from pythonnet import load
            load("netfx")
            import clr
            clr.AddReference(os.path.join(LHM_DIR, "LibreHardwareMonitorLib.dll"))
            from LibreHardwareMonitor import Hardware
            pc = Hardware.Computer()
            pc.IsCpuEnabled = True
            pc.IsGpuEnabled = True
            pc.Open()
        except Exception as e:
            self.error = _explain(e)
            return
        self._pc = pc
        gpus = []
        for hw in pc.Hardware:
            hw.Update()
            kind = str(hw.HardwareType)
            if kind == "Cpu" and "cpu" not in self._hw:
                sensor = _pick(hw, CPU_SENSORS)
                if sensor is not None:
                    self._hw["cpu"] = (hw, sensor)
            elif kind in GPU_TYPES:
                sensor = _pick(hw, GPU_SENSORS)
                if sensor is not None:
                    gpus.append((GPU_TYPES.index(kind), hw, sensor))
        if gpus:
            _, hw, sensor = min(gpus, key=lambda g: g[0])
            self._hw["gpu"] = (hw, sensor)

    def read(self, which):
        """Degrees C for "cpu" or "gpu", or None when there is no reading."""
        entry = self._hw.get(which)
        if entry is None:
            return None
        hw, sensor = entry
        try:
            hw.Update()
            value = sensor.Value
        except Exception:
            return None
        # An unreadable sensor reports 0 rather than nothing -- the CPU does
        # without admin rights or PawnIO -- so 0 and below count as missing.
        return float(value) if value is not None and value > 0 else None

    def status(self):
        """One line for the console: what is being read, or why not."""
        if self.error:
            return f"temperatures unavailable: {self.error}"
        parts = []
        for which, label in (("cpu", "CPU"), ("gpu", "GPU")):
            if self.read(which) is not None:
                parts.append(f"{label} from {self._hw[which][1].Name}")
            else:
                parts.append(f"{label} -- ({self._why(which)})")
        return "temperatures: " + ", ".join(parts)

    def _why(self, which):
        if which not in self._hw:
            return "no sensor found"
        if which == "cpu":
            if not self.admin:
                return "needs administrator rights"
            if self.pawnio == "missing":
                return "needs the PawnIO driver from pawnio.eu"
            if self.pawnio == "stopped":
                return "the PawnIO driver would not start"
        return "the sensor reads nothing"

    def close(self):
        if self._pc is not None:
            try:
                self._pc.Close()
            except Exception:
                pass
            self._pc = None
