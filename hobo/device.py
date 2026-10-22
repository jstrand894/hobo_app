"""Read-only access to a HOBO U23 logger, based on captured HOBOware traffic.

Protocol (see README): every command is an 8-byte HID SET_REPORT; the reply is 8 bytes on the
interrupt IN endpoint. Memory reads (opcode 0x03) additionally return 64 bytes through
GET_DESCRIPTOR(string, index 10, langid 0x0904).
"""
import os
import sys
import time

os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

import usb.backend.libusb1  # noqa: E402
import usb.core  # noqa: E402
import usb.util  # noqa: E402

VID, PID = 0x0F1B, 0x0160
BLOCK = 0x40


def _backend():
    """In the bundled app, use the libusb shipped inside it; otherwise let pyusb find it."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        lib = os.path.join(base, "libusb-1.0.0.dylib")
        if os.path.exists(lib):
            return usb.backend.libusb1.get_backend(find_library=lambda _: lib)
    return None


def logger_present() -> bool:
    """True if a HOBO logger is visible on USB (does not open it)."""
    return usb.core.find(idVendor=VID, idProduct=PID, backend=_backend()) is not None


class Logger:
    def __init__(self):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID, backend=_backend())
        if self.dev is None:
            raise RuntimeError("No HOBO logger found. Is the adapter plugged in and HOBOware closed?")
        for attempt in range(5):   # another window or poll may hold the interface for a moment
            try:
                self.dev.set_configuration()
                usb.util.claim_interface(self.dev, 0)
                break
            except usb.core.USBError:
                if attempt == 4:
                    raise
                time.sleep(0.4)

    def close(self):
        usb.util.release_interface(self.dev, 0)
        usb.util.dispose_resources(self.dev)

    def __enter__(self):
        self.command(bytes([0x09, 0x02]))  # handshake HOBOware sends first
        return self

    def __exit__(self, *exc):
        self.close()

    def command(self, data: bytes) -> bytes:
        data = bytes(data).ljust(8, b"\0")
        self.dev.ctrl_transfer(0x21, 0x09, 0x0003, 0, data, timeout=2000)
        return bytes(self.dev.read(0x81, 8, timeout=2000))

    LOGGING_BIT = 0x02   # state byte: 0x12 / 0x92 = logging, 0x00 / 0x10 / 0x80 / 0x90 = stopped

    def status(self) -> dict:
        """Decode the reply to `02 02` (layout inferred from captures, checked against launch settings)."""
        s = self.command(bytes([0x02, 0x02]))
        logging = bool(s[0] & self.LOGGING_BIT)
        return {"raw": s.hex(" "), "state": s[0], "logging": logging,
                "pointer": s[1] << 8 | s[2],            # end of recorded data in memory
                "interval_s": s[3] << 8 | s[4],          # logging interval, seconds
                "next_sample_in_s": int.from_bytes(s[5:8], "big") if logging else None}

    def stop(self, timeout_s=3.0) -> dict:
        """Stop logging (`01 00`, as HOBOware sends it) and confirm the state changed. Data is kept."""
        if not self.status()["logging"]:
            return self.status()
        self.command(bytes([0x01, 0x00]))   # HOBOware sends this twice back to back; a single one is ignored
        self.command(bytes([0x01, 0x00]))
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.status()
            if not st["logging"]:
                self.command(bytes([0x01, 0x01]))  # end of session, as HOBOware does after a readout
                return self.status()
            time.sleep(0.2)
        raise RuntimeError("The logger did not stop. Check the connection and try again.")

    def read_block(self, addr: int) -> bytes:
        self.command(bytes([0x03, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]))
        raw = bytes(self.dev.ctrl_transfer(0x80, 0x06, 0x030A, 0x0904, 74, timeout=2000))
        return raw[2:2 + BLOCK]

    def read_memory(self, start: int, length: int) -> bytes:
        out = b""
        for addr in range(start, start + length, BLOCK):
            out += self.read_block(addr)
        return out[:length]


def parse_records(mem: bytes):
    """Yield (tag, value_bytes) for the 0x88-delimited records seen in the header."""
    i = 4  # skip 'HOBO'
    while i + 2 < len(mem):
        if mem[i] != 0x88:
            i += 1
            continue
        tag, n = mem[i + 1], mem[i + 2]
        yield tag, mem[i + 3:i + 3 + n]
        i += 3 + n


def main():
    import sys
    with Logger() as lg:
        print("status:", lg.status())
        mem = lg.read_memory(0, 0x200)
        for tag, val in parse_records(mem[:0x180]):
            txt = val.decode("ascii", "replace") if val and all(32 <= b < 127 for b in val) else val.hex(" ")
            print(f"  tag 0x{tag:02x} len {len(val)}: {txt}")
        if len(sys.argv) > 1:
            open(sys.argv[1], "wb").write(mem)
            print("wrote", sys.argv[1])


if __name__ == "__main__":
    main()
