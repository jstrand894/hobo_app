import csv
import os
import tempfile
import unittest
from datetime import datetime

from hobo import decode
from hobo.actions import blank_stale
from hobo.decode import Readout
from tests.helpers import make_memory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDLAKE = os.path.join(ROOT, "captures", "SandLake_RP_Ambient_#2.hobo")
REFERENCE = os.path.join(ROOT, "captures", "full log.csv")


class Conversions(unittest.TestCase):
    def test_known_raw_values(self):
        # raw 1662 / 3101 matched HOBOware's first SandLake sample (76.05 F, 75.726 %)
        self.assertAlmostEqual(decode.temp_c(1662) * 9 / 5 + 32, 76.05, delta=0.002)
        self.assertAlmostEqual(decode.rh_pct(3101), 75.726, places=3)

    def test_extremes_are_monotonic(self):
        self.assertLess(decode.temp_c(0), decode.temp_c(4095))
        self.assertAlmostEqual(decode.rh_pct(4095), 100.0)
        self.assertEqual(decode.rh_pct(0), 0.0)


class SyntheticReadout(unittest.TestCase):
    def setUp(self):
        self.samples = [(-20 + i * 1.7, 30 + i * 2.1) for i in range(25)]
        mem, self.ptr = make_memory(name="Orchard Gate 3", serial="22000001", interval=1800,
                                    start=datetime(2025, 9, 2, 13, 29, 25), samples=self.samples)
        self.ro = Readout(mem)

    def test_header_fields(self):
        self.assertEqual(self.ro.name, "Orchard Gate 3")
        self.assertEqual(self.ro.serial, "22000001")
        self.assertEqual(self.ro.interval_s, 1800)
        self.assertEqual(self.ro.start, datetime(2025, 9, 2, 13, 29, 25))
        self.assertEqual(self.ro.data_start % 8, 0)

    def test_samples_round_trip(self):
        self.assertEqual(len(self.ro.samples), len(self.samples))
        for (t, h), (_, tc, rh) in zip(self.samples, self.ro.rows()):
            self.assertAlmostEqual(tc, t, delta=0.05)
            self.assertAlmostEqual(rh, h, delta=0.03)

    def test_timestamps_follow_the_interval(self):
        rows = list(self.ro.rows())
        self.assertEqual(rows[0][0], datetime(2025, 9, 2, 13, 29, 25))
        self.assertEqual((rows[1][0] - rows[0][0]).total_seconds(), 1800)
        self.assertEqual((rows[-1][0] - rows[0][0]).total_seconds(), 1800 * 24)

    def test_battery_events_do_not_break_alignment(self):
        mem, _ = make_memory(samples=self.samples, events=[(5, "fff40"), (12, "fff50")])   # 5-nibble events
        ro = Readout(mem)
        self.assertEqual(len(ro.samples), len(self.samples))
        self.assertEqual([n for _, n in ro.events], ["bad battery", "good battery"])
        for (t, h), (_, tc, rh) in zip(self.samples, ro.rows()):
            self.assertAlmostEqual(tc, t, delta=0.05)

    def test_six_nibble_event_and_terminal_event(self):
        mem, _ = make_memory(samples=self.samples, events=[(0, "fff014"), (len(self.samples), "fff6" + "0")])
        ro = Readout(mem)
        self.assertEqual(len(ro.samples), len(self.samples))
        self.assertTrue(ro.ended)

    def test_unknown_event_is_refused_not_silently_truncated(self):
        mem, _ = make_memory(samples=self.samples, events=[(4, "fff2")])
        with self.assertRaises(ValueError):
            Readout(mem)

    def test_end_pointer_ignores_stale_bytes(self):
        mem = bytearray(self.ro.mem)
        stale = bytes.fromhex("6a5c3b" * 40)              # leftovers from an old deployment after the pointer
        mem[self.ptr:self.ptr + len(stale)] = stale
        self.assertGreater(len(Readout(bytes(mem)).samples), len(self.samples))        # without the pointer: wrong
        self.assertEqual(len(Readout(bytes(mem), end=self.ptr).samples), len(self.samples))
        fixed = blank_stale(bytes(mem), self.ptr)
        self.assertEqual(len(Readout(fixed).samples), len(self.samples))               # saved file decodes alone

    def test_not_a_hobo_image_is_rejected(self):
        with self.assertRaises(ValueError):
            Readout(b"\xff" * 1024)


class CsvOutput(unittest.TestCase):
    def setUp(self):
        mem, _ = make_memory(samples=[(10.0, 50.0), (20.0, 60.0), (30.0, 70.0)], interval=600)
        self.ro = Readout(mem)
        self.dir = tempfile.mkdtemp()

    def read(self, path):
        with open(path) as f:
            return list(csv.reader(f))

    def test_default_is_celsius(self):
        path = os.path.join(self.dir, "c.csv")
        self.ro.write_csv(path)
        rows = self.read(path)
        self.assertEqual(rows[0], ["#", "time GMT-5:00", "Temp C", "RH %"])
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(float(rows[1][2]), 10.0, delta=0.05)

    def test_fahrenheit_option(self):
        path = os.path.join(self.dir, "f.csv")
        self.ro.write_csv(path, fahrenheit=True)
        rows = self.read(path)
        self.assertEqual(rows[0][2], "Temp F")
        self.assertAlmostEqual(float(rows[1][2]), 50.0, delta=0.1)


@unittest.skipUnless(os.path.exists(SANDLAKE) and os.path.exists(REFERENCE), "real captures not present")
class AgainstHoboware(unittest.TestCase):
    """The decoder reproduces HOBOware's own CSV export for a 12,090-sample deployment."""

    def test_every_sample_matches(self):
        with open(SANDLAKE, "rb") as f:
            ro = Readout(f.read())
        with open(REFERENCE, encoding="utf-8-sig") as f:
            ref = [r for r in csv.reader(f)][1:]
        ref = [(float(r[2]), float(r[3])) for r in ref if r[2]]
        self.assertEqual(len(ro.samples), len(ref))
        bad = 0
        for (_, tc, rh), (tf, rrh) in zip(ro.rows(), ref):
            bad += abs(tc * 9 / 5 + 32 - tf) > 0.0015 or abs(rh - rrh) > 0.0015
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
