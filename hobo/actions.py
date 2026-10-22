"""Logger operations shared by the command line and the window."""
import os
import re
from datetime import datetime

from .decode import Readout
from .device import BLOCK, Logger

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
        if total is None and addr > 0x190 and (addr // BLOCK) % 16 == 0:
            ro = Readout(mem)
            if ro.ended:
                return mem[:-(-ro.end_byte // BLOCK) * BLOCK]  # keep through the block with the end marker
    return mem


def describe(lg: Logger) -> dict:
    """Identity and state of the connected logger (read-only)."""
    st = lg.status()
    ro = Readout(lg.read_memory(0, 0x1C0))
    return {
        "name": ro.name, "serial": ro.serial, "interval_s": ro.interval_s, "started": ro.start,
        "logging": st["state"] == 0x12, "state": st["state"],
    }


def default_stem(ro: Readout) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ro.name)
    return f"{safe}_{ro.serial}_{datetime.now():%Y%m%d-%H%M}"


def write_files(ro: Readout, mem: bytes, base_path: str, fahrenheit=True, save_hobo=True):
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


def save_readout(mem: bytes, output_dir: str, fahrenheit=True):
    """Write <name>_<serial>_<time>.hobo and .csv into output_dir; returns (Readout, hobo_path, csv_path)."""
    ro = Readout(mem)
    csv_path, hobo_path = write_files(ro, mem, os.path.join(output_dir, default_stem(ro)), fahrenheit)
    return ro, hobo_path, csv_path
