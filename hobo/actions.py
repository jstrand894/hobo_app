"""Logger operations shared by the command line and the window."""
import os
import re
import struct
from datetime import datetime

from .decode import Readout
from .device import BLOCK, Logger
from .decode import parse_records, rh_pct, temp_c
from .launch import launch

MAX_ADDR = 0x10000  # stop here if no end-of-data marker is found


def read_until_end(lg: Logger, progress=None) -> bytes:
    """Read the logger's memory up to the end of recorded data.

    The status reply holds the write pointer, which gives the total up front (for a progress bar).
    If the pointer looks wrong, fall back to reading until the decoder sees the end marker.
    progress(bytes_read, total_or_None) is called after each block.
    """
    ptr = lg.status()["pointer"]
    total = -(-ptr // BLOCK) * BLOCK if 0x190 <= ptr <= MAX_ADDR else None
    mem = b""
    addr = 0
    while addr < (total or MAX_ADDR):
        try:
            mem += lg.read_block(addr)
        except Exception:
            break
        addr += BLOCK
        if progress:
            progress(addr, total)
        if total is None and addr > 0x200 and (addr // BLOCK) % 16 == 0:
            ro = Readout(mem)
            if ro.ended:
                return mem[:-(-ro.end_byte // BLOCK) * BLOCK]  # keep through the block with the end marker
    return blank_stale(mem, ptr) if total else mem


def blank_stale(mem: bytes, ptr: int) -> bytes:
    """Bytes past the logger's write pointer are leftovers from an earlier deployment. Blank them (0xFF) from
    exactly where the last real sample ends, so a saved .hobo file decodes correctly on its own."""
    ro = Readout(mem, end=ptr)
    if ro.ended:
        return mem
    n = ro.end_nibble
    out = bytearray(mem)
    byte = n // 2
    if n % 2:
        out[byte] |= 0x0F
        byte += 1
    out[byte:] = b"\xff" * (len(out) - byte)
    return bytes(out)


def describe(lg: Logger) -> dict:
    """Identity and state of the connected logger (read-only)."""
    st = lg.status()
    head = lg.read_memory(0, 0x1C0)
    ro = Readout(head)
    return {
        "name": ro.name, "serial": ro.serial, "interval_s": ro.interval_s, "started": ro.start,
        "logging": st["logging"], "state": st["state"], "next_sample_in_s": st["next_sample_in_s"],
        "battery_v": battery_volts(head),
        **live_fields(lg, st, head),
    }


MEMORY_BYTES = 0x10000   # 64 KB: the U23 holds about 21,000 two-channel samples (assumed from the spec sheet)


def live_fields(lg: Logger, st: dict, head: bytes) -> dict:
    """Things that change while logging: current reading, samples so far, memory used."""
    rec = {t: v for t, v in parse_records(head)[0]}
    data_start = int.from_bytes(rec.get(0x0D, b"\0\0\x01\x90"), "big")
    r = lg.command(bytes([0x02, 0x01]))                 # live reading: 12-bit temp, 12-bit RH, same layout as samples
    t12, h12 = (r[0] << 4) | (r[1] >> 4), ((r[1] & 15) << 8) | r[2]
    used = max(0, st["pointer"] - data_start)
    return {
        "reading_c": temp_c(t12), "reading_rh": rh_pct(h12),
        "samples": used // 3,                            # approximate: events take a slot too
        "memory_used": used / (MEMORY_BYTES - data_start),
        "interval_s": st["interval_s"] or None, "logging": st["logging"],
        "next_sample_in_s": st["next_sample_in_s"],
    }


def live(lg: Logger) -> dict:
    """Quick refresh of the changing values (two short commands)."""
    st = lg.status()
    return live_fields(lg, st, lg.read_memory(0, 0x1C0))


def battery_volts(header: bytes):
    """Battery voltage the logger recorded at its last launch (header tag 0x1E, a big-endian float).

    Inferred, not confirmed: the value sits at 3.5 V for these loggers and changes at each launch, and the
    logs hold 'bad battery'/'good battery' events. Returns None if it is missing or implausible."""
    for tag, val in parse_records(header)[0]:
        if tag == 0x1E and len(val) == 4:
            v = struct.unpack(">f", val)[0]
            return round(v, 2) if 2.0 <= v <= 4.5 else None
    return None


def stop_logging(lg: Logger) -> dict:
    """Stop the connected logger; returns describe() afterwards."""
    lg.stop()
    return describe(lg)


def launch_logger(lg: Logger, interval_s: int, name: str, progress=None) -> dict:
    """Start a new deployment on a stopped logger; returns describe() afterwards."""
    launch(lg, interval_s, name, progress=progress)
    return describe(lg)


def default_stem(ro: Readout) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ro.name)
    return f"{safe}_{ro.serial}_{datetime.now():%Y%m%d-%H%M}"


def write_files(ro: Readout, mem: bytes, base_path: str, fahrenheit=False, save_hobo=True):
    """Write base_path + .csv (and .hobo); returns (csv_path, hobo_path or None)."""
    os.makedirs(os.path.dirname(os.path.abspath(base_path)), exist_ok=True)
    csv_path = base_path + ".csv"
    ro.write_csv(csv_path, fahrenheit=fahrenheit)
    hobo_path = None
    if save_hobo:
        hobo_path = base_path + ".hobo"
        with open(hobo_path, "wb") as f:
            f.write(mem)
    return csv_path, hobo_path


def save_readout(mem: bytes, output_dir: str, fahrenheit=False):
    """Write <name>_<serial>_<time>.hobo and .csv into output_dir; returns (Readout, hobo_path, csv_path)."""
    ro = Readout(mem)
    csv_path, hobo_path = write_files(ro, mem, os.path.join(output_dir, default_stem(ro)), fahrenheit)
    return ro, hobo_path, csv_path
