import struct
import unittest
from datetime import datetime

from hobo import launch
from hobo.actions import blank_stale, describe, launch_logger, read_until_end, stop_logging
from hobo.decode import Readout, parse_records
from tests.helpers import FakeDevice, FakeLogger, make_memory


class StatusDecoding(unittest.TestCase):
    def test_logging_bit(self):
        for state, logging in ((0x12, True), (0x92, True), (0x10, False), (0x00, False), (0x80, False), (0x90, False)):
            d = FakeDevice(logging=False)
            lg = FakeLogger(d)
            lg.command = lambda data, s=state: bytes([s, 0x01, 0x90, 0x00, 0x3C, 0, 0, 7])
            st = lg.status()
            self.assertEqual(st["logging"], logging, hex(state))
            self.assertEqual(st["pointer"], 0x190)
            self.assertEqual(st["interval_s"], 60)

    def test_countdown_only_while_logging(self):
        d = FakeDevice(logging=True)
        self.assertEqual(FakeLogger(d).status()["next_sample_in_s"], 30)
        d.logging = False
        self.assertIsNone(FakeLogger(d).status()["next_sample_in_s"])


class Describe(unittest.TestCase):
    def test_fields(self):
        d = FakeDevice(logging=True, name="Barn Roof", serial="22000007", interval=300, battery=3.55)
        with FakeLogger(d) as lg:
            info = describe(lg)
        self.assertEqual((info["name"], info["serial"], info["interval_s"]), ("Barn Roof", "22000007", 300))
        self.assertTrue(info["logging"])
        self.assertEqual(info["battery_v"], 3.55)
        self.assertEqual(info["samples"], 30)
        self.assertAlmostEqual(info["reading_c"], 22.0, delta=0.05)
        self.assertAlmostEqual(info["reading_rh"], 35.0, delta=0.05)
        self.assertGreater(info["memory_used"], 0)

    def test_implausible_battery_is_ignored(self):
        with FakeLogger(FakeDevice(battery=9.9)) as lg:
            self.assertIsNone(describe(lg)["battery_v"])


class Stop(unittest.TestCase):
    def test_needs_two_stop_commands_and_confirms(self):
        d = FakeDevice(logging=True)
        with FakeLogger(d) as lg:
            lg.stop()
        self.assertFalse(d.logging)
        self.assertEqual(sum(1 for c in d.commands if c[:2] == b"\x01\x00"), 2)
        self.assertIn(bytes([1, 1, 0, 0, 0, 0, 0, 0]), d.commands)          # end-of-session after stopping

    def test_stop_on_stopped_logger_sends_nothing(self):
        d = FakeDevice(logging=False)
        with FakeLogger(d) as lg:
            lg.stop()
        self.assertEqual(d.stop_calls, 0)

    def test_stop_reports_failure(self):
        d = FakeDevice(logging=True)
        lg = FakeLogger(d)
        lg.command = lambda data: bytes([0x92, 1, 0x90, 0, 60, 0, 0, 5])     # never stops
        with self.assertRaises(RuntimeError):
            lg.stop(timeout_s=0.5)

    def test_stop_logging_action(self):
        d = FakeDevice(logging=True)
        with FakeLogger(d) as lg:
            info = stop_logging(lg)
        self.assertFalse(info["logging"])


class ReadOut(unittest.TestCase):
    def test_reads_exactly_up_to_the_write_pointer(self):
        d = FakeDevice(logging=False, samples=[(15.0 + i * 0.05, 40.0) for i in range(500)])
        calls = []
        with FakeLogger(d) as lg:
            mem = read_until_end(lg, progress=lambda a, t: calls.append((a, t)))
        self.assertEqual(len(mem), -(-d.pointer // 64) * 64)
        self.assertEqual(calls[-1][0], calls[-1][1])                              # progress reaches 100 %
        self.assertTrue(all(b[0] > a[0] for a, b in zip(calls, calls[1:])))      # and only ever moves forward
        self.assertEqual(len(Readout(mem).samples), 500)

    def test_stale_bytes_after_the_pointer_are_blanked(self):
        d = FakeDevice(logging=False, samples=[(15.0, 40.0)] * 10)
        d.mem[d.pointer:d.pointer + 90] = bytes.fromhex("6a5c3b") * 30          # old deployment's leftovers
        with FakeLogger(d) as lg:
            mem = read_until_end(lg)
        self.assertEqual(len(Readout(mem).samples), 10)

    def test_bad_pointer_falls_back_to_scanning(self):
        d = FakeDevice(logging=False, samples=[(15.0, 40.0)] * 40)
        d.pointer = 0x0001                                                          # nonsense pointer
        with FakeLogger(d) as lg:
            mem = read_until_end(lg)
        self.assertEqual(len(Readout(mem).samples), 40)


class BuildHeader(unittest.TestCase):
    def setUp(self):
        self.old, _ = make_memory(name="Test Logger", interval=1800, launches=4, start=datetime(2026, 1, 1, 8, 0, 0))
        self.now = datetime(2026, 1, 1, 9, 30, 0)

    def fields(self, header):
        recs = {t: v for t, v in parse_records(header)[0]}
        return recs

    def test_changed_fields(self):
        header, ds = launch.build_header(self.old, self.now, 3600, "Orchard Gate 3")
        f = self.fields(header)
        self.assertEqual(f[0x08], (3600).to_bytes(4, "big"))
        self.assertEqual(f[0x0A], b"Orchard Gate 3")
        self.assertEqual(f[0x07][:7], bytes([20, 26, 1, 1, 9, 30, 0]))
        self.assertEqual(int.from_bytes(f[0x0E], "big"), 5)                          # launch counter +1
        self.assertEqual(int.from_bytes(f[0x22][2:4], "big"), 5)
        self.assertEqual(int.from_bytes(f[0x22][11:13], "big"), 3600)
        old_clock = struct.unpack(">I", self.fields(self.old)[0x22][4:8])[0]
        self.assertEqual(struct.unpack(">I", f[0x22][4:8])[0], old_clock + 5400)    # + seconds since the old start

    def test_layout_rules(self):
        for name in ("A", "Test Logger", "A much longer logger name 1234567"):
            header, ds = launch.build_header(self.old, self.now, 60, name)
            f = self.fields(header)
            _, end = parse_records(header)
            self.assertEqual(ds % 8, 0)
            self.assertEqual(int.from_bytes(f[0x0D], "big"), ds)
            self.assertEqual(int.from_bytes(f[0x1D], "big"), end + 2)
            self.assertEqual(len(header), ds)
            self.assertEqual(header[:4], b"HOBO")

    def test_other_fields_copied_unchanged(self):
        header, _ = launch.build_header(self.old, self.now, 60, "X")
        new, old = self.fields(header), self.fields(self.old)
        for tag in (0x05, 0x06, 0x14, 0x1E):
            self.assertEqual(new[tag], old[tag])

    def test_validation(self):
        for bad_interval in (0, -5, 64801):
            with self.assertRaises(ValueError):
                launch.build_header(self.old, self.now, bad_interval, "ok")
        for bad_name in ("", "x" * 41, "caf\u00e9", "tab\there"):
            with self.assertRaises(ValueError):
                launch.build_header(self.old, self.now, 60, bad_name)
        with self.assertRaises(ValueError):
            launch.build_header(b"\xff" * 600, self.now, 60, "ok")


class LaunchProtocol(unittest.TestCase):
    def test_launch_a_stopped_logger(self):
        d = FakeDevice(logging=False, name="Test Logger", interval=1800)
        with FakeLogger(d) as lg:
            info = launch_logger(lg, 900, "Field Station 9")
        self.assertTrue(d.logging)
        self.assertEqual(d.interval, 900)
        self.assertTrue(info["logging"])
        self.assertEqual(info["name"], "Field Station 9")
        self.assertEqual(info["interval_s"], 900)
        self.assertEqual(bytes(d.mem[:4]), b"HOBO")                                 # header valid again
        ro = Readout(bytes(d.mem), end=d.pointer)
        self.assertEqual(ro.interval_s, 900)
        self.assertEqual(len(ro.samples), 0)                                        # a fresh, empty deployment

    def test_command_order_matches_what_hoboware_sends(self):
        d = FakeDevice(logging=False)
        with FakeLogger(d) as lg:
            launch_logger(lg, 60, "Order Test")
        ops = [c[0] for c in d.commands if c[0] != 3]
        first_write = ops.index(5)
        self.assertEqual(d.commands[[c[0] for c in d.commands].index(5)][4:8], bytes([0x48, 0xFF, 0x42, 0xFF]))  # header invalidated first
        self.assertIn(6, ops[first_write:])
        self.assertEqual(d.commands[-1][:2] != b"", True)
        magic = [c for c in d.commands if c[0] == 5 and c[1:4] == b"\x00\x00\x00"]
        self.assertEqual(magic[-1][4:8], b"HOBO")                                  # "HOBO" word is written last

    def test_refuses_while_logging(self):
        d = FakeDevice(logging=True)
        with FakeLogger(d) as lg, self.assertRaises(RuntimeError):
            launch_logger(lg, 60, "Nope")

    def test_detects_a_header_write_that_did_not_stick(self):
        d = FakeDevice(logging=False)
        lg = FakeLogger(d)
        real = lg.command
        lg.command = lambda data: real(data) if data[0] != 5 else bytes(8)         # writes silently dropped
        with self.assertRaises(RuntimeError):
            launch.launch(lg, 60, "Dropped")

    def test_bad_input_is_rejected_before_anything_is_written(self):
        d = FakeDevice(logging=False)
        with FakeLogger(d) as lg, self.assertRaises(ValueError):
            launch_logger(lg, 0, "Zero interval")
        self.assertFalse(any(c[0] == 5 for c in d.commands))


if __name__ == "__main__":
    unittest.main()
