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
- **The real pixel ceiling is ~472,000 px per frame.** Frames of 484,056 px
  already come out scrambled. `MAX_PIXELS = 475_000` in `lcd_win.py` enforces
  it. Never raise `MAX_PIXELS`, never suggest widths above 320 for the
  rooftop GIF, and don't re-run the width experiment — it has been done.
- **The frame rate is not a limit up to 60.** The panel was watched at 10 to
  120 frames a second on 2026-10-01 and showed all of them cleanly; it takes
  in about 73 video-sized frames a second at most. Don't re-run that either.
- The vendor app (LCD Control) is obfuscated; its code and strings cannot be
  read statically. Don't spend time trying.
- **Never send the panel guessed or experimental commands** (anything but
  header + JPEG frames). Its firmware protocol is unknown, and an unexpected
  command could leave it in a bad state. Brightness is done in software
  (`--brightness`) for this reason.
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
- The engine is shared: the scripts run `clock_win.main()`, and the service
  plays the same `clock_win.Show` and `Player` itself (`service_win.Display`),
  so that it can swap settings without a pause. Both start the loop and both
  rebuild at `valid_until`, so a change to either step may need making in
  `main()` and in `Display`. Keep the command-line behaviour of the scripts
  unchanged when editing it.
- **Settings go through `settings_win.py`.** A new setting needs all of: a
  default in `DEFAULT_LOOK`, a check in `clean_look` (the service runs as
  SYSTEM; nothing unchecked may reach it), a `clock_win` option, and a line in
  `engine_argv`. A setting of one playlist item (its placement, its timing)
  goes in `new_item` and `clean_item` instead. The window's preview uses
  `clock_win.preview()` with the same `engine_argv`, so it always matches the
  panel — never draw the preview any other way.
- **A playlist item names a file in the media pool, never a path.** The names
  are made by the service from the file's content (`keep_in_pool`) and checked
  against `MEDIA_FILE`. Files reach the service only through the `upload_*`
  commands, which check them before keeping them. Don't add a way round
  either. Saved layouts hold no pictures of their own: they share the pool,
  and `gc_media` deletes what nothing uses.
- **Old settings must keep loading.** A `config.json` or layout from v1.0 to
  v1.2 (one picture, its placement in the look) loads as a one-item playlist
  and must give the engine the very options it got before; `migrate_media`
  moves the files when the service starts. The same goes for the clock's
  formats: the app's defaults (`DEFAULT_TIME_FORMAT`, `DEFAULT_DATE_FORMAT` in
  `settings_win.py`; with leading zeros since v1.4) are for new installs and
  Reset to defaults only. A saved file keeps the formats it names, a v1.0
  file that names none keeps the old ones (`LEGACY_FORMATS`), and the scripts'
  own defaults in `clock_win.py` stay as they were.
- **Video.** A video is converted once, by the window, into a clip
  (`video_win`); the service plays clips with FFmpeg's H.264 decoder only.
  Read "Video" in `docs/HOW-IT-WORKS.md` before touching it. In particular:
  **the window process must never import `av`** (FFmpeg) — it goes through
  `video_win.Worker`, a helper process — and nothing but
  `video_win.ClipReader` may decode in the service. Don't add GPU decoding:
  it was measured, and it is slower.
- The window: everything lives in one window; the tray menu has only **Show**
  and **Quit**. Closing the window hides it; Quit stops the service and exits.
  One Apply / Undo covers every tab. (Two switches take effect at once
  instead, because they belong to the app and not to a look: Start display
  with Windows, and the rotation of saved layouts.)
- **Uninstall never deletes saved layouts without asking** (the user asked for
  this). No is the default; a silent uninstall keeps them, and the `media`
  folder with them, since that is where their pictures and videos are.
- **Reset to defaults keeps what the user chose or wrote** (`CONTENT_KEYS` in
  `tray_win.py`: the playlist, the text, the countdown, the to-do list).
  To-do ticks and removals reach the screen at once through the service's
  `todo` command; adding and editing items waits for Apply.
- **Releases:** the version lives only in `VERSION` in `ipc_win.py`. Bump it,
  commit, then the user tags `vX.Y.Z` and pushes the tag; the workflow checks
  they match. Never change the installer's `AppId`. When updating PawnIO or
  Inno Setup, update the URL **and** SHA-256 in `release.yml` together. When
  updating PyAV, follow the note above `FFMPEG_FOR_PYAV` in
  `packaging/collect_licenses.py`: the list of what its FFmpeg is made of and
  the licence texts in `packaging/licenses/ffmpeg` must be brought up to date
  (the build stops until they are).
- **The user commits and pushes; don't.** Hand over a commit message instead.

## Keep it light

The service runs all day and the window app sits in the tray at every login;
the user asked for both to be as light as possible. See "Performance" in
`docs/HOW-IT-WORKS.md` for the measured numbers.

- **The display loop does per frame only what must be per frame.** Frosting,
  the 180° flip and packing happen once per frame at load (`prepare`); the text
  is drawn once per change (`text_bands`), not once per frame. Don't move work
  back into the loop.
- **Content that changes at a known moment is drawn into the frames**, not
  per frame: the text, countdown and to-do blocks and the calendar go in
  `Layout.static`, and `Layout.valid_until` says when they go out of date
  (midnight, or the countdown's next hour); the player then rebuilds the Show
  in the background. Only the clock, the date and the stats are drawn as the
  loop runs.
- **Never stop the loop to change settings.** Build a new `clock_win.Show`
  while the old one plays and hand it over with `Player.swap` (the service's
  `Display._rebuild`). Stopping the loop froze the screen for ~2 s in v1.1.0.
  A playlist changes item the same way, with the next one made ahead and
  swapped in at its moment (`swap(show, at=...)`).
- Animated pictures are held zlib-packed and made one frame at a time
  (`load_frames(..., each=...)`), so a 300-frame GIF never sits in memory at
  full size. Keep it that way.
- **Pictures and GIFs must not pay for video.** The picture loop
  (`Player.run`) was left as it was when video came in v1.3, and its output is
  byte for byte what v1.2 sent; a video has a loop of its own
  (`_play_video`), with the exact pacing, the text thread and the cheaper
  frost it needs. Keep the two apart. FFmpeg is imported only where a video is
  used (`video_win._av`), so a service showing pictures never loads it, and
  `video_win` imports nothing heavy at the top. With one playlist item and no
  rotation, no extra thread runs.
- **Hidden, the window app does nothing but keep the tray tooltip current**: no
  redraws, no settings or layout reads, no preview picture held, no helper
  process (`MainWindow.release`). The font list is read only when the Style
  tab is opened. The preview never plays by itself.
- **Measure before and after** any change to the loop, with frames going to a
  stand-in panel rather than the real one (see "Performance"), and compare
  like with like: same picture, same settings, 60 s after it has settled.
  One run against one proves nothing: single runs of the same code differ by
  up to a point of one core (4.3% to 5.7% on the rooftop GIF). Run the two
  versions in alternating pairs, several of them, and if they still seem to
  differ, count the work each did (text draws, frames encoded) before
  believing it.

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
- **In-process tests of the service or the window must use their own pipe
  name and data folder** (patch `PIPE_NAME`, `DATA_DIR`, `CONFIG_FILE`,
  `LOG_FILE`, `LAYOUTS_DIR`, `MEDIA_DIR`, `INCOMING_DIR` and `GUARD_FILE` in
  `ipc_win`, `settings_win`, `service_win` and `tray_win` — each imported
  them by name). To test the window, replace
  `tray_win.send_settings` with a call into `CrystalLcdService.handle` on a
  stand-in object, and stub `service_state`, `start_service`, `stop_service`
  and the tray icon, so nothing reaches the installed service. When the app is installed, the real service
  owns `\\.\pipe\CrystalXLCD`; a test copy cannot create it, so the test's
  requests silently reach the real service and change the user's settings.
  This happened once.
- **A test copy of the window appears on the user's screen.** For
  measurements, put it off-screen (`root.geometry("+-6000+-6000")`) before
  showing it, and open it with `root.deiconify()`, not `show()`, which takes
  the keyboard focus from whatever the user is doing. When a test must be on
  screen (screenshots), tell the user first that a test window will pop up
  and close by itself. Once, a test window popped up unannounced; the user
  didn't know what it was, clicked it, and spoiled the measurement. A test
  that only checks behaviour can leave the window withdrawn: nothing shows,
  and the preview is still drawn (read it with `ImageTk.getimage`).
- **Drive a window test from inside `mainloop()`** (a script stepped with
  `root.after`). The window's threads hand their results back with
  `root.after`, which fails unless the main loop is really running; pumping
  `root.update()` in a loop does not count.
- **After any change near the picture loop, check it sends the same bytes.**
  Run the engine from the working tree and from a copy of the last release
  (`git archive vX.Y.Z | tar -x -C <folder>`) on the same pictures with a
  fixed clock and example readings, and compare a hash of every prepared
  frame and of a few finished JPEGs. v1.3 was checked this way against v1.2
  on the rooftop GIF (default, everything switched on, one column without
  frost), a 300-frame GIF and three stills: identical.
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
- **FFmpeg** comes inside PyAV's package, with x264 and x265, which are under
  the GPL: the installer therefore carries every part's licence text
  (`packaging/licenses/ffmpeg`) and says where each part's source is
  (`collect_licenses.py`). Keep both true to the PyAV version in
  `requirements.txt`.

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
