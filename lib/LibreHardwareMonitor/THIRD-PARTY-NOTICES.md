# Third-party components

The DLLs in this folder are **unmodified** files from the official
LibreHardwareMonitor v0.9.6 release. They are used only to read CPU and GPU
temperatures (see `temps_win.py`). Each keeps its own license, listed below;
none of them is covered by this project's GPL-3.0 license.

Source of every file: `LibreHardwareMonitor.zip` from
https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6
(SHA-256 of the zip: `086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001`).
Only the library and the files it needs were kept; the LibreHardwareMonitor
app itself is not included.

| File | Version | Project | License |
|---|---|---|---|
| `LibreHardwareMonitorLib.dll` | 0.9.6 | [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) | MPL-2.0 — `LICENSE-MPL-2.0.txt` |
| `DiskInfoToolkit.dll` | 1.1.2 | [DiskInfoToolkit](https://github.com/Blacktempel/DiskInfoToolkit) (Florian K.) | MPL-2.0 — `LICENSE-MPL-2.0.txt` |
| `RAMSPDToolkit-NDD.dll` | 1.4.2 | [RAMSPDToolkit](https://github.com/Blacktempel/RAMSPDToolkit) (Florian K.) | MPL-2.0 — `LICENSE-MPL-2.0.txt` |
| `BlackSharp.Core.dll` | 1.0.7 | [BlackSharp](https://github.com/Blacktempel/BlackSharp) (Florian K.) | MPL-2.0 — `LICENSE-MPL-2.0.txt` |
| `HidSharp.dll` | 2.6.4 | [HidSharp](https://www.nuget.org/packages/HidSharp/2.6.4) (James F. Bellinger) | Apache-2.0 — `LICENSE-HidSharp.txt` |
| `System.Buffers.dll` | 4.6.1 | [.NET](https://github.com/dotnet/maintenance-packages) (.NET Foundation) | MIT — `LICENSE-MIT-dotnet.txt` |
| `System.Memory.dll` | 4.6.3 | [.NET](https://github.com/dotnet/maintenance-packages) (.NET Foundation) | MIT — `LICENSE-MIT-dotnet.txt` |
| `System.Numerics.Vectors.dll` | 4.6.1 | [.NET](https://github.com/dotnet/maintenance-packages) (.NET Foundation) | MIT — `LICENSE-MIT-dotnet.txt` |
| `System.Runtime.CompilerServices.Unsafe.dll` | 6.1.2 | [.NET](https://github.com/dotnet/maintenance-packages) (.NET Foundation) | MIT — `LICENSE-MIT-dotnet.txt` |

The source code of the MPL-2.0 components is available from the project links
above, at the versions listed.

## Verifying the files

SHA-256 of each DLL, identical to the copies inside the release zip above:

```
6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0  LibreHardwareMonitorLib.dll
1acbf51b3c10c51c986cf43021680d34a2e38d9a5ba652bcfa9a1b5f7fc09800  DiskInfoToolkit.dll
b6882354c7c8ec186617e421507743dbfae09c5c1fc24cef76a1d0c0c26651de  RAMSPDToolkit-NDD.dll
cafb93afcc8d8a367e21f619673d05c06887d8964867fed1371f02ded1cd3e23  BlackSharp.Core.dll
d86690efde30ea9179f669320f39148853793b743a98b531afeaf30598e22f54  HidSharp.dll
2d78d770c9cb997199154ae8c018b9f1d1efbc86729f7264dde6dbad2a12cac3  System.Buffers.dll
d5e8e4866f9cfa66f7765660f84b210198893e55335487afe5ebda342c0e913d  System.Memory.dll
20c2fa81b8c70d651099d762954f285fd4f942e63b2d7217c145dab8d4b2f4c9  System.Numerics.Vectors.dll
08cbd7278b66f1e68425a82d4b97181a4130d93e3dd91831407aba7212ccdacf  System.Runtime.CompilerServices.Unsafe.dll
```

In PowerShell: `Get-FileHash lib\LibreHardwareMonitor\*.dll`

`HidSharp.dll` is also byte-identical to `lib/net35/HidSharp.dll` in the
official HidSharp 2.6.4 NuGet package.
