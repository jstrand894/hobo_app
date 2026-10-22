# hobo_app

Open replacement for HOBOware for HOBO U23 Pro v2 loggers over the optic USB base station.

Goal workflow: connect -> download (stop) -> save CSV -> status -> relaunch -> disconnect.

Status: protocol not yet identified. Run `python3 -m hobo.probe` with the adapter plugged in.

## Findings

- Device: USB 0x0F1B:0x0160, "HOBO U23-001A Temp/RH" (Onset), low-speed, bus powered 50 mA.
- One HID interface, one Interrupt IN endpoint 0x81 (8 bytes, 10 ms interval).
- HID report descriptor (vendor page 0xFF01): 8-byte Input report + 8-byte Feature report. So all traffic is 8-byte packets.
- macOS does not create an IOHIDDevice for it; use libusb (`brew install libusb`, `pyusb`), set `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
- Idle: no unsolicited interrupt data; GET_REPORT (feature/input) returns zeros.
- Next: capture HOBOware readout traffic to learn the command format.

## Protocol notes (from hook capture, run 1)

Transport (8-byte packets):
- Host -> device: HID SET_REPORT via control transfer: bmRequestType=0x21, bRequest=0x09, wValue=0x0003, wIndex=0, 8 bytes.
- Device -> host: 8 bytes read from interrupt IN pipe 1 (ReadPipe) right after each command.
- Bulk data: GET_DESCRIPTOR(string, index 0x0a, langid wIndex=0x0904) returns 66 data bytes (after the 2-byte string header).

Commands seen (byte0 = opcode):
- `09 02 ..` -> `09 00 70 01 08 ..` (init/handshake?)
- `02 02` -> status (`12/90/80 01 8b/8e 07 08 00 06 b9/b4`) - changes with logger state
- `02 01` -> raw sensor sample (`65 b5 62 b6 ff ff ff ff`) polled once a second on the status screen
- `02 03`, `02 04`: not decoded yet
- `01 00` / `01 01`: unknown (run just before/after readout)
- `03 00 AA AA AA ..` read memory: 24-bit big-endian address in bytes 1-3; each read returns 64 bytes via the string descriptor read
- Memory holds `0x88 tag len value` records: serial (tag 0x06, ASCII), logger name (0x0a "Test Logger"), software string (0x34), time zone name (0x14)

Not yet captured: stop-logging command, data download over a real dataset, launch/relaunch commands.

## Protocol notes (capture run 2: readout + launch, captures/run2_readout_launch.log)

- Status `02 02` reply byte0: `0x12` = logging (byte4 = interval in s, byte7 = seconds to next sample), `0x80`/`0x90` = idle/stopped.
- `01 00` appears before a stop/launch; `01 01` after a readout (exact meaning unconfirmed).
- `05 AA AA AA d0 d1 d2 d3` = write 4 bytes of memory at 24-bit address AA.
- Launch = (`01 00`, `02 03`), invalidate header (`05 00 00 00 48 ff 42 ff`), misc (`05 00 01 88 ff ff ff ff`, `03 01 00 03`, `07 00`, `05 04 00 00`, `06 01 88 00 1e 00 00 01` where 0x1e = 30 s interval), rewrite config memory 0x000-0x1FF in 4-byte writes, then rewrite `48 4f 42 4f` header last to commit. Status then shows `12 01 8b 00 1e ...`.
- Memory 0x000-0x17F is the tagged config header; samples start at 0x188 (fixed-size records, not decoded yet).
- Not yet seen: stop on a logger with real data, large sample download.

`hobo/device.py` implements read-only status + memory dump (untested: HOBOware was still holding the device).

## Capture run 3 (full readout, captures/run3_full_readout.log)

- A `.hobo` file is just the logger's raw memory image. Rebuilding memory from the `03 00 AA AA AA` reads (64 bytes each, 0x0000..0x8FFF here) reproduced HOBOware's `.hobo` file byte-for-byte (36,800 bytes). So readout = read memory blocks, write file.
- Channel metadata mentions `RH_Sensirion_SHT31_12bit`; sample format and calibration still to decode (need ground-truth values from HOBOware's plot).

## Sample decoding (solved, validated)

Validated against HOBOware's own CSV export (`captures/full log.csv`, 12,090 samples): temperature within 0.001 F, RH exact, timestamps match to the minute (HOBOware shows :26 seconds, we compute :25 from the header).

- Header 0x000-0x18F: `HOBO` + `0x88 tag len value` records (start time tag 0x07, interval seconds tag 0x08, serial tag 0x06, name tag 0x0a, time zone name tag 0x14).
- Data from 0x190 is a **nibble stream**. Sample = 6 nibbles: 12-bit temp, 12-bit RH.
- `T_C = -46.85 + 175.72*(raw+0.5)/4096`, `RH% = raw/4095*100`.
- Events begin with nibbles `fff`: `fff0` +2 nibbles (6 total, coupler detached), `fff4`+`0` (5, bad battery), `fff5`+`0` (5, good battery). Tail events (coupler attached, host connected, stopped, end of file: `fff1`, `fff6`, `fff3`) not yet parsed; the decoder stops at the first unknown type.
- Time zone: HOBOware shows a fixed GMT offset (-5:00 for this logger).

`python3 -m hobo.decode file.hobo out.csv` converts a readout to CSV.

## Usage

Quit HOBOware first (only one program can hold the USB device). From the project folder:

    .venv/bin/python -m hobo status
    .venv/bin/python -m hobo download -o downloads    # writes <name>_<serial>_<time>.hobo and .csv; does not stop the logger

`download` reads memory until the end-of-data marker, so the `.hobo` can be one 64-byte block shorter than HOBOware's (samples identical).

## Window

    .venv/bin/python -m hobo gui

Connect shows the logger's name, serial, interval and state; Download asks where to save (file dialog; the folder is remembered in ~/.hobo_gui.json) and writes a CSV plus, optionally, the raw .hobo (never stops the logger); the progress bar uses the write pointer in the status reply (bytes 1-2) as the total; Open .hobo file plots an existing file.

## Dock app

    ./scripts/build_app.sh        # builds dist/HOBO Logger.app (PyInstaller; bundles libusb)

Drag `dist/HOBO Logger.app` to Applications, open it, then right-click its Dock icon > Options > Keep in Dock.
The window shows a green light when a logger is connected (grey: nothing detected; orange: adapter seen but unreadable, e.g. HOBOware is open).
`dist/HOBO Logger.app/Contents/MacOS/HOBO\ Logger --selftest` checks USB access without a window.
Icons are generated by `scripts/make_icons.py` (needs Pillow); the generated files are committed.
