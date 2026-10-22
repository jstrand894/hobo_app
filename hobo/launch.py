"""Launch (start a new deployment on) a HOBO U23-001A logger.

This replays what HOBOware sent during a launch, captured with the USB hook (captures run 2 and 4):
  1. stop commands, then invalidate the header word and clear the first data word,
  2. three setup commands (03 01 00 03 / 07 00 / 05 04 00 00) and `06 <data start> <interval> 00 00 01`,
  3. rewrite the header block by block (4-byte writes, then three read-backs of each 64-byte block),
  4. write the "HOBO" magic word last to make the header valid again.

The new header is the old one with only the fields below changed; everything else is copied.
"""
import struct
import time
from datetime import datetime

from .decode import parse_records
from .device import BLOCK

TAG_DATA_START = 0x0D     # 4 bytes: address where samples begin (header end rounded up to 8)
TAG_HEADER_END = 0x1D     # 4 bytes: header end + 2
TAG_INTERVAL = 0x08       # 4 bytes: logging interval, seconds
TAG_START = 0x07          # 8 bytes: century, yy, month, day, hour, minute, second, 0 (local time)
TAG_NAME = 0x0A           # ASCII
TAG_LAUNCHES = 0x0E       # 4 bytes: launch counter
TAG_DEPLOY = 0x22         # 29 bytes: see build_header

MIN_INTERVAL_S, MAX_INTERVAL_S = 1, 64800
MAX_NAME = 40


def _start_bytes(t: datetime) -> bytes:
    return bytes([t.year // 100, t.year % 100, t.month, t.day, t.hour, t.minute, t.second, 0])


def _start_dt(b: bytes) -> datetime:
    return datetime(b[0] * 100 + b[1], b[2], b[3], b[4], b[5], b[6])


def build_header(old_mem: bytes, now: datetime, interval_s: int, name: str, battery: bytes = None):
    """Return (header_bytes, data_start): the old header with start time, interval, name and counters updated."""
    if not MIN_INTERVAL_S <= interval_s <= MAX_INTERVAL_S:
        raise ValueError(f"interval must be between {MIN_INTERVAL_S} s and {MAX_INTERVAL_S // 3600} h")
    if not 1 <= len(name) <= MAX_NAME or not all(32 <= ord(c) < 127 for c in name):
        raise ValueError(f"name must be 1-{MAX_NAME} plain ASCII characters")
    if old_mem[:4] != b"HOBO":
        raise ValueError("the logger's header is not valid; launch it once from HOBOware first")
    recs, _ = parse_records(old_mem)
    by = {t: v for t, v in recs}
    for needed in (TAG_START, TAG_INTERVAL, TAG_NAME, TAG_LAUNCHES, TAG_DEPLOY, TAG_HEADER_END, TAG_DATA_START):
        if needed not in by:
            raise ValueError(f"header record 0x{needed:02x} not found; refusing to guess")
    old_start = _start_dt(by[TAG_START])
    elapsed = int((now - old_start).total_seconds())

    deploy = bytearray(by[TAG_DEPLOY])
    deploy[2:4] = (struct.unpack(">H", deploy[2:4])[0] + 1).to_bytes(2, "big")          # launch count
    deploy[4:8] = ((struct.unpack(">I", deploy[4:8])[0] + elapsed) & 0xFFFFFFFF).to_bytes(4, "big")  # logger clock
    deploy[11:13] = interval_s.to_bytes(2, "big")

    new = []
    for tag, val in recs:
        if tag == TAG_START:
            val = _start_bytes(now)
        elif tag == TAG_INTERVAL:
            val = interval_s.to_bytes(4, "big")
        elif tag == TAG_NAME:
            val = name.encode("ascii")
        elif tag == TAG_LAUNCHES:
            val = (struct.unpack(">I", val)[0] + 1).to_bytes(4, "big")
        elif tag == TAG_DEPLOY:
            val = bytes(deploy)
        elif tag == 0x1E and battery is not None:
            val = battery
        new.append([tag, val])
    body = b"".join(bytes([0x88, t, len(v)]) + v for t, v in new)
    end = 4 + len(body)
    header_end = end + 2
    data_start = (header_end + 7) // 8 * 8
    for rec in new:
        if rec[0] == TAG_HEADER_END:
            rec[1] = header_end.to_bytes(4, "big")
        elif rec[0] == TAG_DATA_START:
            rec[1] = data_start.to_bytes(4, "big")
    body = b"".join(bytes([0x88, t, len(v)]) + v for t, v in new)
    img = b"HOBO" + body
    return img.ljust(data_start, b"\xff"), data_start


def _write_word(lg, addr: int, data: bytes):
    lg.command(bytes([0x05, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]) + data)


def launch(lg, interval_s: int, name: str, now: datetime = None, progress=None):
    """Start a new deployment ("start now"). The logger must be stopped. Erases nothing explicitly, but
    resets the data pointer, so the previous data will be overwritten: download it first."""
    if lg.status()["logging"]:
        raise RuntimeError("The logger is still logging. Stop it first.")
    now = now or datetime.now().replace(microsecond=0)
    # current header: read generously, then trim to what the old header says it is
    old = lg.read_memory(0, 0x200)
    old_data_start = None
    recs, _ = parse_records(old)
    for t, v in recs:
        if t == TAG_DATA_START:
            old_data_start = int.from_bytes(v, "big")
    if old_data_start and old_data_start > len(old):
        old = lg.read_memory(0, -(-old_data_start // BLOCK) * BLOCK)
    img, data_start = build_header(old, now, interval_s, name)

    def step(text):
        if progress:
            progress(text)

    step("Preparing the logger…")
    for cmd in ([1, 0], [1, 0], [2, 2], [1, 0], [1, 0], [2, 3]):
        lg.command(bytes(cmd))
    _write_word(lg, 0x000000, bytes([0x48, 0xFF, 0x42, 0xFF]))        # invalidate the header while rewriting it
    _write_word(lg, data_start, b"\xff\xff\xff\xff")                    # clear the first data word
    lg.command(bytes([0x03, 0x01, 0x00, 0x03]))
    lg.command(bytes([0x07, 0x00]))
    lg.command(bytes([0x05, 0x04, 0x00, 0x00]))
    lg.command(bytes([0x06, data_start >> 8, data_start & 0xFF, (interval_s >> 8) & 0xFF, interval_s & 0xFF, 0, 0, 1]))

    work = bytearray(img)
    work[0:4] = bytes([0x48, 0xFF, 0x42, 0xFF])                         # stays invalid until the very end
    for start in range(0, data_start, BLOCK):
        step(f"Writing settings… {100 * start // data_start}%")
        end = min(start + BLOCK, data_start)
        for a in range(start, end, 4):
            _write_word(lg, a, bytes(work[a:a + 4]))
        for _ in range(3):  # HOBOware reads each block back three times after writing it
            got = lg.read_block(start)
        if got[:end - start] != bytes(work[start:end]):
            raise RuntimeError(f"Verification failed at 0x{start:x}. The logger's header may be incomplete; "
                               "launch it from HOBOware to recover it.")
    _write_word(lg, 0x000000, img[0:4])                                  # "HOBO": header valid again
    if lg.read_block(0)[:4] != img[:4]:
        raise RuntimeError("The logger did not accept the new header. Launch it from HOBOware to recover it.")
    step("Starting…")
    lg.command(bytes([0x09, 0x02]))
    deadline = time.time() + 5
    while time.time() < deadline:
        st = lg.status()
        if st["logging"]:
            if st["interval_s"] != interval_s:
                raise RuntimeError(f"The logger reports a {st['interval_s']} s interval, expected {interval_s} s.")
            return st
        time.sleep(0.3)
    raise RuntimeError("The logger did not start logging. Launch it from HOBOware.")
