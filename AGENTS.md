# Notes for AI coding assistants

Rules for any AI agent working on this repo. Humans are welcome to read them
too.

## The project

- Drives the LCD in a CrystalX CoreView V-950 case from Windows by writing
  JPEG frames to its COM port. See [README.md](README.md) for use and
  [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) for everything technical.
- **Windows only.** Don't add Linux, macOS or cross-platform code, and don't
  describe things by comparing them with Linux. State panel facts as plain
  facts.
- The maintainer's test machine: Windows 10 LTSC, AMD Ryzen 5 5600,
  Radeon RX 580, three fixed drives (C:, E:, F:), ethernet with a VPN adapter
  (Tailscale) also present.

## Before changing code

- Read **"Things that must not change"** in `docs/HOW-IT-WORKS.md` before
  touching rendering or the transport. Those constraints were measured on the
  physical hardware; they are not guesses.
- **The real pixel ceiling is ~472,000 px per frame**, lower than
  `MAX_PIXELS = 500_000` in `lcd_win.py`. Frames of 484,056 px already come out
  scrambled. Never raise `MAX_PIXELS`, never suggest widths above 320 for the
  rooftop GIF, and don't re-run the width experiment — it has been done.
- The vendor app (LCD Control) is obfuscated; its code and strings cannot be
  read statically. Don't spend time trying.
- **Temperatures** come from LibreHardwareMonitor via `temps_win.py`. CPU needs
  PawnIO **running** and admin rights; GPU (AMD) needs neither. An unreadable
  sensor reports 0, not None. Never switch to a WinRing0-based build. See
  "Temperatures" in `docs/HOW-IT-WORKS.md` before debugging a `--`.
- The DLLs in `lib/LibreHardwareMonitor/` are unmodified release files. When
  upgrading them, update the versions and SHA-256 list in its
  `THIRD-PARTY-NOTICES.md`, and keep each component's license file.

## The app (service + window + installer)

See "The app: service, window and installer" in `docs/HOW-IT-WORKS.md`.

- **Keep the security rules** listed there. In particular: the service runs as
  SYSTEM, so never let the window pass it a file path (send bytes), never grant
  users more than start/stop on the service (`SERVICE_SDDL`; never
  `SERVICE_CHANGE_CONFIG`), keep `C:\ProgramData\CrystalX LCD` locked to
  SYSTEM/administrators, and keep the app in Program Files (the installer has
  no folder choice on purpose).
- **Service control from the window must ask for minimal rights** — use
  `tray_win._with_service`. pywin32's `win32serviceutil.StartService` /
  `StopService` open the service manager with full access and fail for normal
  users.
- The engine is shared: the service runs `clock_win.main(argv, stop, log)`.
  Keep the command-line behaviour of the scripts unchanged when editing it.
- The window: everything lives in one window; the tray menu has only **Show**
  and **Quit**. Closing the window hides it; Quit stops the service and exits.
- **Releases:** the version lives only in `VERSION` in `ipc_win.py`. Bump it,
  commit, then the user tags `vX.Y.Z` and pushes the tag; the workflow checks
  they match. Never change the installer's `AppId`. When updating PawnIO or
  Inno Setup, update the URL **and** SHA-256 in `release.yml` together.
- **The user commits and pushes; don't.** Hand over a commit message instead.

## Testing

- **You cannot see the panel.** Only the user can confirm that output looks
  right. When testing a change, make it produce a visibly distinct result and
  ask — do not assume it worked because the process ran without error.
- **Ask the user before sending anything to the panel.** The vendor app must be
  closed first, and they need to be watching the screen.
- **Ask before anything that shows a Windows admin (UAC) prompt** —
  `run-clock.cmd`, `autostart-on.cmd`, `autostart-off.cmd`, or elevating a
  test. The user has to click Yes.
- Use `--preview FILE` for anything visual. It renders a frame to PNG without
  opening the port, so it neither disturbs a running panel nor risks the
  display. Look at the PNG before sending anything to hardware.
- A test that renders identically to the previous test tells you nothing. Vary
  one thing at a time, and make the variable visible on screen (a number, a
  label) so the user can report which case they are looking at.
- Solid colours and uniform images **hide geometry bugs completely**. A sheared
  or offset red frame still looks red. Always test with structured content:
  grids, diagonals, numbered bands, distinct corners.
- `run-gif.cmd` is the known-good baseline. If something breaks, go back to it
  to establish whether the problem is the hardware or the change.
- Put experiments and throwaway scripts outside the repo.
- **In-process tests of the service must use their own pipe name and data
  folder** (patch `PIPE_NAME`, `DATA_DIR`, `CONFIG_FILE`, `LOG_FILE` in both
  `ipc_win` and `service_win`). When the app is installed, the real service
  owns `\\.\pipe\CrystalXLCD`; a test copy cannot create it, so the test's
  requests silently reach the real service and change the user's settings.
  This happened once.
- Windows reads a `.cmd` file line by line while it runs. Don't edit
  `run-clock.cmd` or `run-gif.cmd` while a copy of it is running.
- Measure before optimising, and check what a measurement actually means —
  `psutil.Process.cpu_percent()` is normalised to *one core*, not to the whole
  CPU, and multiplying it by the thread count gives a figure 12× too high.

## License

- The project is **GPL-3.0-or-later**, copyright Abdul Moez
  (https://github.com/Abdul-Moez), with an additional section 7(b) term
  requiring that attribution to be preserved.
- Never remove or alter the license header at the top of `lcd_win.py`,
  `clock_win.py` or the `.cmd` files. Give every new source file the same
  header.
- Only bring in code or libraries whose licenses are GPL-3.0-compatible (MIT,
  BSD, Apache-2.0, MPL-2.0, LGPL, GPL). Keep each bundled third-party
  component's own license file next to it.

## Dependencies and docs

- Python libraries are pinned to exact versions in `requirements.txt`;
  `setup.cmd` installs them into `venv-win/`. Add new libraries there with an
  exact version. Never commit `venv-win/`.
- **Never use Python 3.13.0.** Its venv `pythonw.exe` opens a console window
  (CPython #126084), which breaks the hidden autostart. `setup.cmd` refuses it;
  keep that check. The maintainer's `venv-win` uses Python 3.13.5.
- **PawnIO** (needed for the CPU temperature) is a kernel driver. It is **never
  stored in the repo**: the release workflow downloads the official installer
  (checked by SHA-256 and signature) and the app's installer runs it with
  `-install -silent` only if PawnIO is missing. Script users install it from
  https://pawnio.eu/. Keep it that way, and never uninstall PawnIO with the app.
- `setup.cmd` must keep unblocking `lib\` (`Unblock-File`): .NET refuses DLLs
  that carry the "downloaded from the internet" mark.
- `README.md` is for people who just want the screen running and may not know
  GitHub: install the app, use it, uninstall it. Keep it step by step and free
  of internals. The script route lives in `docs/SCRIPTS.md`, technical detail
  in `docs/HOW-IT-WORKS.md`, rules for agents here.
