# CrystalX CoreView LCD for Windows

Show your own pictures, GIFs or videos with a clock, the date and live PC
stats (CPU and GPU usage and temperature, RAM, drives, network) on the LCD
screen of a **CrystalX CoreView V-950** PC case — without the vendor's LCD
Control app.

This is an unofficial project. It is not made by or affiliated with CrystalX or
the makers of LCD Control.

- [Install](#install)
- [Using the app](#using-the-app)
- [Update or uninstall](#update-or-uninstall)
- [Stop LCD Control starting with Windows](#stop-lcd-control-starting-with-windows)
- [If something goes wrong](#if-something-goes-wrong)
- [Other ways to run it, and how it works](#other-ways-to-run-it-and-how-it-works)
- [License](#license)

---

## Install

**You need:** the CrystalX CoreView V-950 case with the LCD's USB cable plugged
into the motherboard (as it comes from the factory), and Windows 10 or 11,
64-bit. Nothing else — the installer contains everything.

1. **Close the vendor's LCD Control app** if it's running. Only one program can
   use the screen at a time. If you can't find its window, look for its icon
   near the clock in the bottom-right corner, or end **LCD Control** in Task
   Manager.

2. **Download the installer.** Open the
   **[latest release](https://github.com/Abdul-Moez/CrystalX-CoreView-Software-For-Windows/releases/latest)**
   and click **`CrystalX-LCD-Setup-<version>.exe`** under *Assets*.

3. **Run it.** Windows may say **"Windows protected your PC"** — the installer
   isn't code-signed yet, so Windows doesn't recognise the publisher. Click
   **More info**, then **Run anyway**. Then click **Yes** when Windows asks for
   administrator permission.

4. **Click through the steps:**
   - the license (GPL v3) — click **I accept** and **Next**;
   - if LCD Control is set to start with Windows, a box offers to stop that —
     leave it ticked unless you want to keep using LCD Control;
   - **Install**, then **Finish** (with *Open CrystalX LCD* ticked).

Within a few seconds the case screen shows the clock, the date and your PC's
stats, and the CrystalX LCD window opens.

**What the installer puts on your PC**, so nothing is a surprise:

| What | Why |
|---|---|
| The app, in `C:\Program Files\CrystalX LCD` | The program itself |
| A Windows service, **CrystalX LCD** | Drives the screen from the moment the PC starts — even before you log in — and reads the CPU temperature without asking for admin rights |
| A Start menu entry, **CrystalX LCD** | Opens the app window |
| The app's icon near the clock at every login | Quick access; it starts quietly, without opening a window |
| The **PawnIO** driver, only if you don't have it | Windows only lets drivers read the CPU temperature. PawnIO is free, open source and signed by Microsoft |

---

## Using the app

Open **CrystalX LCD** from the Start menu, or click its icon near the clock
(it may be hidden behind the **^** arrow). Right-clicking the icon gives
**Show** and **Quit**.

The window has everything in one place. On the left is a **preview** of the
screen, with example readings. It updates as you change things, and nothing
reaches the case screen until you click **Apply**. Under the preview, the
slider shows any moment of a GIF or video, and **Play** plays it there.

At the top, **Display** shows what the screen is doing, for example *Showing
on COM5*. **Stop display** turns the screen off and frees it for other
programs such as LCD Control; **Start display** brings the clock back. No
admin prompts.

Below it are six tabs:

- **Picture** — what shows behind the clock: one picture, or a **playlist** of
  pictures, GIFs and videos shown one after another.
  - **Add…** picks one or more files: pictures and GIFs (GIF, PNG, JPEG, WEBP
    or BMP) and **videos** (MP4, MOV, MKV, WEBM, AVI and most others) up to
    **5 minutes** long — trim a longer one first. The first file you add
    takes the place of the rooftop GIF; **Add default** puts that back.
    **Remove**, **Up** and **Down** change the list, and **Shuffle the order**
    mixes it afresh each time round. Up to 20.
  - **Click an item** in the list to see it in the preview and set it up.
    Each has its own settings:
    - **Fill** crops it to cover the whole screen. **Fit** shows the whole
      picture, with the empty space filled by **blurred edges** or a **solid
      colour** you pick. The screen is tall and narrow, so with Fill a wide
      video shows only its middle strip; Fit shows all of it, smaller.
    - **Drag the preview** to choose which part shows, and **scroll on it**
      (or use the **Zoom** slider) to zoom in, up to 4×. **Reset** centres it
      again.
    - How long it stays before the next one: a still picture for a number of
      **seconds**, a GIF or video a number of **times** through. With only
      one item in the list, it simply keeps playing.
    - A video: **Limit to 30 frames a second** is on to start with, which is
      lighter on the PC. Switch it off to play a 60-frames-a-second video at
      its full rate. Videos play without sound.
    - A GIF: **Play at the GIF's own speed**, for GIFs made to run faster
      than the usual 10 frames a second.
  - **Videos are converted when you click Apply.** A small window shows how
    far it is, with a **Cancel** button; a 5-minute video takes a few
    minutes. The case screen keeps showing what it shows now until it is
    done.
  - To move, zoom or refit a video later, the app needs the video file
    again. While the app stays open it remembers where it was; after that,
    add the video once more.
  - The best picture size is **320 × 1476 px**, or larger in the same tall
    shape. The tab shows your picture's size and warns you if it's small
    enough to look blurry.
  - It also shows the **biggest empty space on the screen** in pixels —
    normally the one between the clock and the stats, for example *320 × 931
    px, from 137 to 1068 px down* — in case you want a picture whose subject
    fits exactly in that gap.
- **Clock** — the **time** and the **date**, each of which you can switch off.
  - Time: 12-hour with or without AM/PM, or 24-hour, with or without seconds.
  - Date: pick a ready-made format, or choose **Custom pattern…** and write
    your own with codes such as `ddd D-MMM-YYYY` (the codes are listed under
    the box).
  - A size slider and a colour for each. The size stops at the largest that
    still fits the screen's width. Without AM/PM, the clock can be about twice
    as big.
  - **Height on screen** moves the clock up or down. You can also drag it in
    the preview.
- **Stats** — up to 7 readings, in spots laid out like the screen: three rows
  of two and one at the bottom. **Show the stats** switches them all off;
  your choices are kept for when you switch them back on.
  - Pick a reading for each spot, or **Empty**. If one spot in a row is empty,
    the other moves to the middle.
  - The box under each spot is the name shown on the screen; type your own,
    for example `CPU` instead of `CPU Temp`.
  - **One per row** gives each reading its own line with much bigger numbers.
  - Size sliders and colours for the names and the numbers, and **Height on
    screen** (or drag the block in the preview).
  - **Warning colours…** makes a number change colour when it gets high. For
    CPU, GPU, RAM, disk and storage use and for the two temperatures, tick
    the ones you want and give each a *warning* and a *critical* level: the
    CPU temperature can turn orange at 75°C and red at 90°C, for example.
    You can pick the two colours too.
- **Extras** — things of your own to add, each switched on separately. The
  text, the countdown and the to-do list each have their own size, colour,
  alignment (left, centre or right), frosted panel on or off and height on the
  screen (or drag them in the preview):
  - **Text** — anything you like: a quote, a reminder, your name. Long lines
    wrap.
  - **Countdown** — days to a date (*12 days to Launch*, then *Launch is
    today!*), days and hours to a date and time, or days since a date (*214
    days since I quit*).
  - **To-do list** — with an optional title. Ticked items are crossed out and
    dimmed. **Ticking and removing items show on the screen straight away**;
    new or edited items show when you click Apply. Double-click an item to
    edit it.
  - **Calendar** — this month or just this week, under the date or instead
    of it, with the week starting on Monday or Sunday. Today is marked. It has
    its own size and colour, and moves with the clock.
- **Style** — **Brightness** dims the whole screen, from 100% down to 10%.
  (The case screen's own backlight can't be changed by any software, so this
  darkens the picture itself.) The **font**, chosen from every font installed
  on the PC for all users, each shown in its own style. And the **frosted
  panel** behind the text: how dark and how blurred, or off.
- **Options**
  - **Start display with Windows** decides whether the clock comes on by
    itself when the PC starts.
  - **Saved layouts**: **Save as…** keeps everything on the screen, its
    pictures and videos too, under a name. **Load** brings it back. Up to 10.
  - **Change layout every … minutes** goes through your saved layouts in
    turn, changing when the picture or video that is playing has finished.
    While it is on, a change you apply lasts until the next change of layout,
    unless you save it as a layout.

Applying never interrupts the screen: it keeps showing the old look for as
long as the new one takes to prepare, then switches.

**Reset to defaults** puts the look back as it came, but keeps your playlist
(the pictures and videos, and how each is placed and timed) and what you
wrote (the text, the countdown and the to-do list — their blocks are just
switched off).
**Undo changes** goes back to what's on the screen now.

**Open log** shows what the app has been doing — useful if something goes
wrong.

**Closing the window** with **X** only hides it; the clock keeps running.
**Quit** (right-click the icon) stops the display and closes the app until your
next login.

**The "Welcome" screen** you see while the PC boots is built into the case
screen and can't be changed. The app takes over as soon as Windows starts.

---

## Update or uninstall

**To update:** download the newer `CrystalX-LCD-Setup-<version>.exe` from the
[releases page](https://github.com/Abdul-Moez/CrystalX-CoreView-Software-For-Windows/releases)
and run it. Your pictures and videos, settings and saved layouts are kept.

**To uninstall:** open **Settings → Apps**, find **CrystalX LCD** and click
**Uninstall**. This removes the app, the service, the Start menu entry, the
icon at login, and your pictures and videos, settings and log.

If you have **saved layouts**, it asks whether to delete them too. Choose
**No** to keep them, with their pictures and videos: they stay in
`C:\ProgramData\CrystalX LCD` (the `layouts` and `media` folders) and come
back if you install the app again.

**PawnIO stays installed** on purpose: it's a shared driver that other
programs (for example LibreHardwareMonitor or FanControl) also use. If you
want it gone too, uninstall **PawnIO** from the same list.

---

## Stop LCD Control starting with Windows

LCD Control starts itself at every login through a Windows scheduled task
called **`LCD ControlPowerBoot`**. That's why it doesn't show up in Task
Manager's Startup tab, and its own autostart switch doesn't always remove it.
While it runs, it holds the screen and this app can't use it.

The installer offers to switch this off for you. To do it yourself:

**What the command below does:**

- It **only switches off that login task**. Nothing is deleted or uninstalled.
- It **doesn't close LCD Control** if it's open right now, and doesn't touch
  anything else you're working on.
- LCD Control **still works whenever you open it yourself**.
- It takes effect from your **next login**.

**To turn it off:**

1. Click **Start**, type `cmd`, right-click **Command Prompt** and choose
   **Run as administrator**. Click **Yes** on the Windows prompt.
2. Paste this and press Enter:

   ```cmd
   schtasks /change /tn "LCD ControlPowerBoot" /disable
   ```

   It should say `SUCCESS`. If it says **"The system cannot find the file
   specified"**, the task doesn't exist — LCD Control isn't set to start at
   login, and there's nothing to do.

**To check it**, paste this in the same window:

```cmd
schtasks /query /tn "LCD ControlPowerBoot"
```

The **Status** column says `Disabled` when it's off, and `Ready` when LCD
Control will start at login.

**To undo it** and let LCD Control start at login again, paste this in an
administrator Command Prompt:

```cmd
schtasks /change /tn "LCD ControlPowerBoot" /enable
```

If that says "The system cannot find the file specified", the task has been
removed (LCD Control's own switch does that). Turn on the autostart option
inside LCD Control instead, and it creates the task again.

---

## If something goes wrong

The **Display** line in the app window usually says what's wrong.

**"Waiting: COM5 is busy -- close LCD Control.exe …"** (the number may differ)
Another program is using the screen — almost always LCD Control. Close it (or
end **LCD Control** in Task Manager); the app notices within a few seconds and
takes over. To stop it coming back at every login, see
[Stop LCD Control starting with Windows](#stop-lcd-control-starting-with-windows).

**"Waiting: panel 33c3:f101 not found -- is the case plugged in?"**
Windows can't see the case screen. Check the LCD's USB cable is connected to
the motherboard. In Device Manager, under **Ports (COM & LPT)**, you should see
**USB Serial Device (COMx)**. The app retries by itself every few seconds.

**"CPU temperature: needs the PawnIO driver from pawnio.eu"**
The PawnIO driver is missing or was removed. Run the installer again (it adds
PawnIO), or install it from **https://pawnio.eu/**.

**A reading shows `--`**
That reading isn't available on your PC (for example, no GPU counters), or the
display has only just started — speed readings need two samples.

**"That video is … minutes long"** (when adding a video)
The screen takes videos up to 5 minutes long. Cut it shorter first (the
Photos app or Clipchamp that come with Windows can), then add the shorter file.

**"The video stops at … seconds although it should last …"**
The video file is cut short or damaged, often from an unfinished download or
copy. Get the file again and add it once more.

**A video shows only a narrow strip of its picture**
The case screen is tall and narrow. With **Fill**, a wide video is cropped to
its middle strip; drag the preview to choose which strip, or pick **Fit** to
see all of it, smaller.

**A video's colours look washed out**
It is an HDR video (many phones record these), and the app shows it without
HDR processing. Record or export it as a normal (SDR) video.

**A video stutters, or the PC feels busier while it plays**
Keep **Limit to 30 frames a second** switched on for that video, and click
Apply again. A video is the heaviest thing the screen can show; a GIF or a
picture costs far less.

**"Windows protected your PC" when running the installer**
Expected for now: the installer isn't code-signed. Click **More info**, then
**Run anyway**. To be sure the file is genuine, compare its checksum with the
one on the release page — in PowerShell: `Get-FileHash CrystalX-LCD-Setup-<version>.exe`.

**I can't find the icon near the clock**
Click the **^** arrow next to the icons; Windows hides new icons there at first.
You can drag it out to keep it visible. Or open **CrystalX LCD** from the Start
menu.

**"The CrystalX LCD service is not installed."**
Something removed the service. Run the installer again to repair it.

**Anything else**
Click **Open log** in the app window, and include what it shows when you
[report an issue](https://github.com/Abdul-Moez/CrystalX-CoreView-Software-For-Windows/issues).

---

## Other ways to run it, and how it works

- **[docs/SCRIPTS.md](docs/SCRIPTS.md)** — run it straight from the source
  files with Python, without installing the app.
- **[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)** — the protocol, the screen's
  hardware limits and how they were found, how each reading is measured, how
  the app, service and installer work, and what didn't work.
- **[AGENTS.md](AGENTS.md)** — rules for AI coding assistants working on this
  project.

---

## License

Copyright © 2026 **Abdul Moez** — [github.com/Abdul-Moez](https://github.com/Abdul-Moez)

This project is free software under the
**[GNU General Public License v3.0](LICENSE)** (or any later version).

In plain words — you may use, copy, change and share it, including for
commercial purposes, as long as:

- **you credit the original author** — keep the name and GitHub link above in
  every copy and modified version (an additional term under section 7(b) of
  the license, stated at the top of each code file);
- **you share your changes under GPL-3.0 as well**, with their source code, if
  you distribute a modified version;
- you keep the license and copyright notices intact.

The [LICENSE](LICENSE) file is the legally binding text; this summary is only a
guide.

The app bundles other open-source components, each under its own license —
among them FFmpeg, which reads and plays the videos. See the `licenses` folder
in the installed app, [packaging/licenses](packaging/licenses) and
[lib/LibreHardwareMonitor/THIRD-PARTY-NOTICES.md](lib/LibreHardwareMonitor/THIRD-PARTY-NOTICES.md).
