"""Decode a HOBO U23-001A memory image (.hobo file or a raw readout) into samples and CSV.

Layout (derived from captured traffic, validated against HOBOware's own CSV export):
- 0x000-0x18F: tagged header ("HOBO", then 0x88 tag len value ... records).
- 0x190 onward: a nibble stream. A sample is 6 nibbles: 12-bit temp, 12-bit RH.
  A leading 0xFFF marks an event: 'fff0' + 2 nibbles (6 total) or 'fff4'/'fff5' (5 total).
"""
import csv
import sys
from datetime import datetime, timedelta

# Event type nibble -> (length in nibbles, name). Only types seen so far are listed.
EVENTS = {0x0: (6, "coupler detached"), 0x4: (5, "bad battery"), 0x5: (5, "good battery")}
# Events that mark the end of recorded samples: 1 coupler attached, 3 host connected, 6 stopped,
# 0xF unwritten memory.
TERMINAL = {0x1, 0x3, 0x6, 0xF}


def parse_records(mem: bytes):
    """Flat list of [tag, value] for the 0x88-delimited header records, and the offset where they end."""
    i, out = 4, []
    while i + 2 < len(mem) and mem[i] == 0x88:
        tag, n = mem[i + 1], mem[i + 2]
        out.append([tag, bytes(mem[i + 3:i + 3 + n])])
        i += 3 + n
    return out, i


def temp_c(raw: int) -> float:
    return -46.85 + 175.72 * (raw + 0.5) / 4096


def rh_pct(raw: int) -> float:
    return raw / 4095 * 100


class Readout:
    def __init__(self, mem: bytes, end: int = None):
        """mem: memory image. end: byte address where recorded data ends (the logger's write pointer);
        needed for a logger that is still logging, whose memory beyond that point is stale."""
        self.mem = mem
        self.end = end
        rec = {t: v for t, v in parse_records(mem)[0]}
        for needed in (0x06, 0x07, 0x08, 0x0A, 0x0D):
            if needed not in rec:
                raise ValueError(f"not a HOBO U23 readout: header record 0x{needed:02x} is missing")
        start = rec[0x07]
        # bytes: century(0x14=20), yy, month, day, hour, minute, second
        self.start = datetime(start[0] * 100 + start[1], start[2], start[3], start[4], start[5], start[6])
        self.interval_s = int.from_bytes(rec[0x08], "big")
        self.serial = rec[0x06].decode("ascii")
        self.name = rec[0x0A].decode("ascii", "replace")
        self.data_start = int.from_bytes(rec[0x0D], "big")
        self.ended = False   # True once a terminating event (stopped, coupler attached, ...) was seen
        self.end_byte = len(mem)
        self.samples, self.events = self._decode()

    def _decode(self):
        nib = []
        for b in (self.mem if self.end is None else self.mem[:self.end]):
            nib += [b >> 4, b & 15]
        pos, samples, events = self.data_start * 2, [], []
        while pos + 5 < len(nib):
            t = nib[pos] << 8 | nib[pos + 1] << 4 | nib[pos + 2]
            if t == 0xFFF:
                typ = nib[pos + 3]
                if typ in TERMINAL:
                    self.ended = True
                    self.end_byte = pos // 2
                    self.end_nibble = pos
                    return samples, events
                if typ not in EVENTS:
                    raise ValueError(f"unknown event type 0x{typ:x} at byte 0x{pos // 2:x}; "
                                     "refusing to guess so data is not silently truncated")
                n, name = EVENTS[typ]
                events.append((len(samples), name))
                pos += n
                continue
            h = nib[pos + 3] << 8 | nib[pos + 4] << 4 | nib[pos + 5]
            samples.append((t, h))
            pos += 6
        self.end_nibble = pos   # where the next unread item would start
        return samples, events

    def rows(self, utc_offset_h=-5):
        for k, (t, h) in enumerate(self.samples):
            when = self.start + timedelta(seconds=self.interval_s * k)
            yield when, temp_c(t), rh_pct(h)

    def write_csv(self, path, fahrenheit=False, utc_offset_h=-5):
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
