"""Decode a HOBO U23-001A memory image (.hobo file or a raw readout) into samples and CSV.

Layout (derived from captured traffic, validated against HOBOware's own CSV export):
- 0x000-0x18F: tagged header ("HOBO", then 0x88 tag len value ... records).
- 0x190 onward: a nibble stream. A sample is 6 nibbles: 12-bit temp, 12-bit RH.
  A leading 0xFFF marks an event: 'fff0' + 2 nibbles (6 total) or 'fff4'/'fff5' (5 total).
"""
import csv
import sys
from datetime import datetime, timedelta

DATA_START = 0x190

# Event type nibble -> (length in nibbles, name). Only types seen so far are listed.
EVENTS = {0x0: (6, "coupler detached"), 0x4: (5, "bad battery"), 0x5: (5, "good battery")}


def _find(mem: bytes, tag: int, n: int):
    i = mem.find(bytes([0x88, tag, n]), 4, DATA_START)
    return None if i < 0 else mem[i + 3:i + 3 + n]


def temp_c(raw: int) -> float:
    return -46.85 + 175.72 * (raw + 0.5) / 4096


def rh_pct(raw: int) -> float:
    return raw / 4095 * 100


class Readout:
    def __init__(self, mem: bytes):
        self.mem = mem
        start = _find(mem, 0x07, 8)
        # bytes: century(0x14=20), yy, month, day, hour, minute, second
        self.start = datetime(start[0] * 100 + start[1], start[2], start[3], start[4], start[5], start[6])
        self.interval_s = int.from_bytes(_find(mem, 0x08, 4), "big")
        self.serial = _find(mem, 0x06, 8).decode("ascii")
        self.name = _find(mem, 0x0A, mem[mem.find(bytes([0x88, 0x0A]), 4) + 2]).decode("ascii", "replace")
        self.samples, self.events = self._decode()

    def _decode(self):
        nib = []
        for b in self.mem:
            nib += [b >> 4, b & 15]
        pos, samples, events = DATA_START * 2, [], []
        while pos + 5 < len(nib):
            t = nib[pos] << 8 | nib[pos + 1] << 4 | nib[pos + 2]
            if t == 0xFFF:
                typ = nib[pos + 3]
                if typ not in EVENTS:
                    break  # end of recorded data / unknown event
                n, name = EVENTS[typ]
                events.append((len(samples), name))
                pos += n
                continue
            h = nib[pos + 3] << 8 | nib[pos + 4] << 4 | nib[pos + 5]
            samples.append((t, h))
            pos += 6
        return samples, events

    def rows(self, utc_offset_h=-5):
        for k, (t, h) in enumerate(self.samples):
            when = self.start + timedelta(seconds=self.interval_s * k)
            yield when, temp_c(t), rh_pct(h)

    def write_csv(self, path, fahrenheit=True, utc_offset_h=-5):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["#", f"time GMT{utc_offset_h:+d}:00", "Temp F" if fahrenheit else "Temp C", "RH %"])
            for k, (when, tc, rh) in enumerate(self.rows(), 1):
                w.writerow([k, when.strftime("%Y-%m-%d %H:%M:%S"),
                            round(tc * 9 / 5 + 32, 3) if fahrenheit else round(tc, 3), round(rh, 3)])


def main():
    src, dst = sys.argv[1], sys.argv[2]
    ro = Readout(open(src, "rb").read())
    ro.write_csv(dst)
    print(f"{ro.name} (serial {ro.serial}): {len(ro.samples)} samples, interval {ro.interval_s}s, "
          f"{len(ro.events)} events -> {dst}")


if __name__ == "__main__":
    main()
