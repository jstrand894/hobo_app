"""Shared test fixtures: synthetic logger memory images and a fake logger that speaks the same protocol.

Nothing here touches USB, the user's settings file, launchd, or the real filesystem outside temp folders.
"""
import math
import struct
from datetime import datetime

from hobo.device import BLOCK, Logger

MEM_SIZE = 0x10000


def encode_temp(celsius):
    return int(round((celsius + 46.85) * 4096 / 175.72 - 0.5))


def encode_rh(rh):
    return int(round(rh / 100 * 4095))


def make_memory(name="Test Logger", serial="22035414", interval=60, start=datetime(2026, 1, 1, 12, 0, 0),
                samples=(), battery=3.55, launches=4, events=()):
    """Build a memory image like a real U23's: tagged header, then a nibble stream of samples.

    events: list of (sample_index, nibble_string) inserted before that sample, e.g. (3, "fff40") = bad battery."""
    def rec(tag, val):
        return bytes([0x88, tag, len(val)]) + val
    deploy = bytearray(29)
    deploy[2:4] = launches.to_bytes(2, "big")
    deploy[4:8] = (0x326D0000).to_bytes(4, "big")
    deploy[11:13] = interval.to_bytes(2, "big")

    def body(data_start, header_end, pad=0):
        return b"".join([rec(0x35, bytes(pad)) if pad else b"",
            rec(0x0D, data_start.to_bytes(4, "big")), rec(0x1D, header_end.to_bytes(4, "big")),
            rec(0x22, bytes(deploy)), rec(0x05, b"HOBO U23-001A Temp/RH"), rec(0x06, serial.encode()),
            rec(0x07, bytes([start.year // 100, start.year % 100, start.month, start.day, start.hour, start.minute,
                             start.second, 0])),
            rec(0x08, interval.to_bytes(4, "big")), rec(0x0A, name.encode()),
            rec(0x0E, launches.to_bytes(4, "big")), rec(0x1E, struct.pack(">f", battery)),
            rec(0x14, b"Central Daylight Time"), rec(0x11, b"")])
    n = len(body(0, 0))
    pad = max(0, 0x188 - (4 + n + 2) - 3)        # real headers are never shorter than 0x188 bytes: filler record
    n = len(body(0, 0, pad))
    header_end = 4 + n + 2
    data_start = (header_end + 7) // 8 * 8
    head = (b"HOBO" + body(data_start, header_end, pad)).ljust(data_start, b"\xff")
    ev = dict()
    for idx, nib in events:
        ev.setdefault(idx, []).append(nib)
    nibbles = ""
    for i, (tc, rh) in enumerate(samples):
        for nib in ev.get(i, []):
            nibbles += nib
        nibbles += "%03x%03x" % (encode_temp(tc), encode_rh(rh))
    for nib in ev.get(len(samples), []):
        nibbles += nib
    if len(nibbles) % 2:
        nibbles += "f"
    data = bytes.fromhex(nibbles) if nibbles else b""
    mem = bytearray(b"\xff" * MEM_SIZE)
    mem[:data_start] = head
    mem[data_start:data_start + len(data)] = data
    return bytes(mem), data_start + len(data)


class FakeDevice:
    """The state of a pretend logger and the pretend USB bus it sits on."""

    def __init__(self, logging=True, samples=None, **mem_kw):
        samples = samples if samples is not None else [(20 + i * 0.1, 40 + i * 0.2) for i in range(30)]
        mem, pointer = make_memory(samples=samples, **mem_kw)
        self.mem = bytearray(mem)
        self.pointer = pointer
        self.logging = logging
        self.interval = mem_kw.get("interval", 60)
        self.reading = (22.0, 35.0)
        self.present = True
        self.fail_open = False
        self.stop_calls = 0
        self.armed = False
        self.pending = None
        self.commands = []

    def name(self):
        from hobo.decode import Readout
        return Readout(bytes(self.mem), end=self.pointer).name


class FakeLogger(Logger):
    """A Logger whose transport is a FakeDevice instead of USB."""

    def __init__(self, device):
        if not device.present or device.fail_open:
            raise RuntimeError("No HOBO logger found. Is the adapter plugged in and HOBOware closed?")
        self.d = device

    def close(self):
        pass

    def command(self, data):
        d = self.d
        data = bytes(data).ljust(8, b"\0")
        d.commands.append(data)
        op = data[0]
        if op == 0x02 and data[1] == 0x02:                      # status
            state = 0x92 if d.logging else 0x80
            return bytes([state, d.pointer >> 8, d.pointer & 255, d.interval >> 8, d.interval & 255, 0, 0, 30])
        if op == 0x02 and data[1] == 0x01:                      # live reading
            t12, h12 = encode_temp(d.reading[0]), encode_rh(d.reading[1])
            return bytes([t12 >> 4, ((t12 & 15) << 4) | (h12 >> 8), h12 & 255, 0xB6, 0xFF, 0xFF, 0xFF, 0xFF])
        if op == 0x01 and data[1] == 0x00:                      # stop: only effective when sent twice
            d.stop_calls += 1
            if d.stop_calls % 2 == 0:
                d.logging = False
        elif op == 0x05 and data[1] != 0x04:                    # write 4 bytes of memory
            addr = (data[1] << 16) | (data[2] << 8) | data[3]
            if addr + 4 <= len(d.mem):
                d.mem[addr:addr + 4] = data[4:8]
            if addr == 0 and data[4:8] == b"HOBO":
                d.armed = True
        elif op == 0x06:                                        # set data start + interval
            d.pending = ((data[1] << 8) | data[2], (data[3] << 8) | data[4])
        elif op == 0x09 and data[1] == 0x02 and d.armed and d.pending:   # handshake after a launch: start logging
            d.logging, d.armed = True, False
            d.interval = d.pending[1]
            d.pointer = d.pending[0] + 3
        return bytes([data[0], 0, 0, 0, 0, 0, 0, 0])

    def read_block(self, addr):
        self.d.commands.append(bytes([3, addr >> 16 & 255, addr >> 8 & 255, addr & 255]))
        return bytes(self.d.mem[addr:addr + BLOCK]).ljust(BLOCK, b"\xff")
