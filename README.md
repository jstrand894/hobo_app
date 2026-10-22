# HOBO Reader v2 Pro Max

A Mac app for reading and relaunching **HOBO U23-001A (U23 Pro v2 Temp/RH)** data loggers over Onset's optical USB base station, without HOBOware. Read the data off a logger, save it as a clean CSV, and start it logging again: three steps, one window.

## What you need

- A Mac (developed on Apple silicon, recent macOS).
- A HOBO U23-001A logger and its Onset optical USB base station (BASE-U-4) with the logger coupler.
- Python 3.10 or newer **with Tk**. The installer from python.org includes Tk (the app was built with Python 3.14 and Tk 8.6).
- [Homebrew](https://brew.sh) and its `libusb`: `brew install libusb`.

## Install

### Option A: run it from the source

```bash
git clone https://github.com/jstrand894/hobo_app.git
cd hobo_app
brew install libusb
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m hobo gui
```

### Option B: build the Mac app (recommended, gives you a Dock icon)

```bash
git clone https://github.com/jstrand894/hobo_app.git
cd hobo_app
brew install libusb
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
./scripts/build_app.sh
```

This creates `dist/HOBO Reader v2 Pro Max.app` (libusb is bundled inside it). Then:

1. Drag the app into your **Applications** folder.
2. Open it. Right-click its Dock icon, then **Options, Keep in Dock**.
3. Optional: in **Settings** (the gear), turn on *Open this app when a logger is plugged in*. It needs the app to be in Applications first.

A copy you built yourself opens without a security prompt. If you copy the app from another Mac and macOS refuses to open it, run `xattr -dr com.apple.quarantine "/Applications/HOBO Reader v2 Pro Max.app"` once.

To check that the app can reach a logger without opening a window: `"/Applications/HOBO Reader v2 Pro Max.app/Contents/MacOS/HOBO Reader v2 Pro Max" --selftest`

### Updating

`git pull`, then run `./scripts/build_app.sh` again and replace the app in Applications.

## Before you start

**Close HOBOware.** Only one program can use the USB device at a time. Plug in the base station and seat the logger in its coupler. When the logger is found, the dot at the top turns green and the Logger card fills in.

## Using the app

The three numbered buttons are the workflow, and the checklist on the right ticks them off as you go. The button for the next sensible step is highlighted in blue.

1. **Readout device** stops the logger if it is logging (after you confirm), reads all its data into the app, and plots it. Hover the plot for exact values, drag to zoom, double-click to reset.
2. **Save readout** writes a CSV (temperatures are always in °C) and, if turned on in Settings, the raw `.hobo` file.
3. **Launch** drops a settings panel down inside the Logger card: name, interval, and an estimate of how long the memory will last. **Start logging** restarts the logger. If you haven't saved this logger's data, it warns you that launching erases it and asks you to confirm.

Other controls:

- **Stop logging** stops a logger without reading it out (for a logger that will just sit on a desk). The recorded data stays on it.
- **Refresh** re-reads the logger. **Open file…** loads a saved `.hobo` file so you can look at it or export it to CSV, no logger needed.
- The **°F / °C** rocker changes the units shown in the app. Saved CSV files are always in °C.
- **Settings** (gear): save a `.hobo` file alongside the CSV, ask where to save each time or use one folder, and open the app when a logger is plugged in.
- Shortcuts: ⌘O open, ⌘R refresh, ⌘D readout, ⌘S save, ⌘L launch, ⌘. stop, ⌘, settings.
- Warning strips appear if the battery is fair or low, or the logger's memory is nearly full. The app follows the system light or dark mode.

## Command line

Run from the project folder (close HOBOware first):

```bash
.venv/bin/python -m hobo status                                  # name, serial, interval, state
.venv/bin/python -m hobo download -o ~/Desktop/readouts          # saves .csv + .hobo; does not stop the logger (-f for °F)
.venv/bin/python -m hobo stop                                    # stop logging (asks first; -y to skip)
.venv/bin/python -m hobo launch -i 60 -n "Barn roof"             # start a new deployment: 60 s interval
.venv/bin/python -m hobo gui                                     # open the window
.venv/bin/python -m hobo.decode file.hobo out.csv                # convert a saved .hobo file to CSV (no logger needed)
```

## Troubleshooting

| Problem | What to do |
|---|---|
| Orange dot, "can't read it" | Close HOBOware (or any other program using the logger). Re-seat the logger in the coupler. |
| Grey dot, "No logger detected" | Check the base station is plugged in and the logger is seated. Unplug and replug. |
| `libusb` or "No backend" error from source | `brew install libusb`. |
| `No module named '_tkinter'` | Your Python has no Tk. Install Python from python.org, or `brew install python-tk`. |
| App doesn't open when I plug in a logger | Turn on *Open this app when a logger is plugged in* in Settings (the app must be in Applications). |
| Launch button is grey | The logger must be stopped first: use Readout device or Stop logging. |

## Development

```bash
.venv/bin/python scripts/dev.py          # live preview: the window restarts whenever you save a file
./scripts/run_tests.sh                   # 126 tests (about 90 s): decoder, launch protocol, every button, CLI, auto-open, version hook
HOBO_LIVE_TESTS=1 ./scripts/run_tests.sh # also stops, reads out and relaunches a connected logger whose name starts with "Test"
./scripts/build_app.sh                   # build the Mac app
.venv/bin/python scripts/make_icons.py   # regenerate the app icon and the status-light glow images
git config core.hooksPath scripts/hooks  # turn on the commit hook that raises the version (1.0.1 -> 1.0.2 ...) on every commit
```

The window tests drive the real UI against a fake logger (`tests/helpers.py`). They never touch real hardware, your settings file or launchd. The live tests are skipped when no logger is available.

Your settings are kept in `~/.hobo_gui.json`. Auto-open uses a small launchd agent (`~/Library/LaunchAgents/com.jstrand.hobo-reader.usb.plist`) that you can remove from Settings.

Layout: `hobo/` (`device.py` USB, `decode.py` readout to samples, `launch.py` launch, `actions.py` shared actions, `gui.py` and `widgets.py` window, `theme.py`, `autostart.py`), `scripts/`, `tests/`.

## Technical notes (how it talks to the logger)

The protocol is not published by Onset. It was worked out by logging HOBOware's USB traffic (`hook/`, a small DYLD hook that records the calls) and checked against HOBOware's own CSV export and launch output.

- **Device:** USB `0x0F1B:0x0160`, "HOBO U23-001A Temp/RH", low speed, one HID interface with a vendor-defined report descriptor (8-byte input and feature reports). macOS does not expose it as an HID device, so the app talks to it with libusb (`pyusb`).
- **Transport:** every command is an 8-byte HID SET_REPORT (control transfer `0x21`, request `0x09`, value `0x0003`); the reply is 8 bytes from the interrupt-in endpoint. Memory reads also return 64 bytes via GET_DESCRIPTOR (string index `0x0A`, language `0x0904`).
- **Commands:** `09 02` handshake. `02 02` status: byte 0 state (bit `0x02` set while logging; `0x12`/`0x92` logging, `0x00`/`0x10`/`0x80`/`0x90` stopped), bytes 1-2 write pointer, bytes 3-4 interval in seconds, bytes 5-7 countdown to the next sample. `02 01` live reading (12-bit temperature, 12-bit humidity). `03 AA AA AA` read the 64-byte block at that address. `05 AA AA AA d0 d1 d2 d3` write 4 bytes. `01 00` (sent twice) stop, `01 01` end of session. `06 <data start> <interval> 00 00 01` arm a launch.
- **Memory:** a `.hobo` file is the logger's raw memory image. The first bytes are `HOBO` followed by `0x88 tag length value` records (serial `0x06`, start time `0x07`, interval `0x08`, name `0x0A`, data start `0x0D`, battery float `0x1E`, ...). Samples start at the data-start address as a nibble stream: 6 nibbles per sample (12-bit temperature, 12-bit humidity), with events starting `fff` (battery, coupler, stopped, end).
- **Conversion:** `T(°C) = -46.85 + 175.72 * (raw + 0.5) / 4096`, `RH(%) = raw / 4095 * 100`. The decoder reproduces HOBOware's CSV for a 12,090-sample deployment (temperature within 0.001 °F, humidity exact).
- **Launch:** replays HOBOware's sequence: stop, invalidate header word 0, clear the first data word, arm with `06`, rewrite the header in 4-byte writes (reading each 64-byte block back three times), and write `HOBO` last. Only the start time, interval, name, launch counters, logger clock and the two header-size pointers change; everything else is copied. The generated header is byte-identical to HOBOware's for the same inputs.
- **Battery:** header tag `0x1E` is a big-endian float (3.55 = 3.55 V), refreshed at each launch. It is inferred to be the battery voltage; HOBOware calls 3.55 V "good". The Fair and Low cut-offs in the app are estimates.
