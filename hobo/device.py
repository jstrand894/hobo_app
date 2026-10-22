"""Read-only access to a HOBO U23 logger, based on captured HOBOware traffic.

Protocol (see README): every command is an 8-byte HID SET_REPORT; the reply is 8 bytes on the
interrupt IN endpoint. Memory reads (opcode 0x03) additionally return 64 bytes through
GET_DESCRIPTOR(string, index 10, langid 0x0904).
"""
import os

os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

import usb.core  # noqa: E402
import usb.util  # noqa: E402

VID, PID = 0x0F1B, 0x0160
BLOCK = 0x40


class Logger:
    def __init__(self):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise RuntimeError("No HOBO logger found. Is the adapter plugged in and HOBOware closed?")
        self.dev.set_configuration()
        usb.util.claim_interface(self.dev, 0)

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

    def status(self) -> dict:
        s = self.command(bytes([0x02, 0x02]))
        return {"raw": s.hex(" "), "state": s[0], "interval_s": s[4] if s[0] == 0x12 else None,
                "next_sample_in_s": s[7] if s[0] == 0x12 else None}

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
