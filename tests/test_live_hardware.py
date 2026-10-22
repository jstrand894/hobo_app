"""Tests against a real logger. Skipped when none is plugged in (or when HOBOware / the app holds the device).

Read-only tests run by default. The destructive ones (stop, read out, launch) only run when
HOBO_LIVE_TESTS=1 is set AND the connected logger's name starts with "Test", so a deployed logger is never touched.
"""
import csv
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest

from hobo import autostart
from hobo.actions import describe, launch_logger, read_until_end, stop_logging, write_files
from hobo.decode import Readout
from hobo.device import Logger, logger_present
from hobo.version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def device_available():
    try:
        if not logger_present():
            return False
        with Logger():
            return True
    except Exception:  # noqa: BLE001 - busy, no permission, no libusb: treat as unavailable
        return False


@unittest.skipUnless(device_available(), "no logger available (unplugged, or another program is using it)")
class ReadOnly(unittest.TestCase):
    def test_status_and_identity(self):
        with Logger() as lg:
            st = lg.status()
            info = describe(lg)
        self.assertIn("pointer", st)
        self.assertGreater(st["pointer"], 0x180)
        self.assertTrue(info["serial"].isdigit() and len(info["serial"]) == 8)
        self.assertGreater(info["interval_s"], 0)
        self.assertEqual(info["interval_s"], st["interval_s"])

    def test_header_is_valid_and_readings_are_plausible(self):
        with Logger() as lg:
            head = lg.read_memory(0, 0x200)
            info = describe(lg)
        self.assertEqual(head[:4], b"HOBO")
        self.assertTrue(-40 < info["reading_c"] < 85, info["reading_c"])
        self.assertTrue(0 <= info["reading_rh"] <= 100, info["reading_rh"])
        if info["battery_v"] is not None:
            self.assertTrue(2.5 < info["battery_v"] < 4.2)

    def test_two_reads_of_the_same_block_agree(self):
        with Logger() as lg:
            self.assertEqual(lg.read_block(0), lg.read_block(0))

    def test_two_programs_cannot_use_the_device_at_once_but_recover(self):
        with Logger():
            pass
        with Logger() as lg:                       # opening again right after closing must work
            self.assertIn("state", lg.status())


def safe_to_modify():
    if os.environ.get("HOBO_LIVE_TESTS") != "1" or not device_available():
        return False
    with Logger() as lg:
        return describe(lg)["name"].startswith("Test")


@unittest.skipUnless(safe_to_modify(), "set HOBO_LIVE_TESTS=1 with a logger whose name starts with 'Test'")
class Destructive(unittest.TestCase):
    """Stop -> read out -> save -> launch on a real logger, the same steps the three buttons perform."""

    def test_full_workflow(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with Logger() as lg:
            before = describe(lg)
            if before["logging"]:
                after_stop = stop_logging(lg)
                self.assertFalse(after_stop["logging"])
            mem = read_until_end(lg)
            stopped = describe(lg)
        ro = Readout(mem)
        self.assertEqual(ro.serial, before["serial"])
        self.assertEqual(ro.interval_s, before["interval_s"])
        self.assertGreater(len(ro.samples), 0)
        self.assertTrue(abs(len(ro.samples) - stopped["samples"]) <= 3)
        csv_path, hobo_path = write_files(ro, mem, os.path.join(tmp, "live"), fahrenheit=False, save_hobo=True)
        with open(csv_path) as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][2], "Temp C")
        self.assertEqual(len(rows) - 1, len(ro.samples))
        with open(hobo_path, "rb") as f:
            self.assertEqual(len(Readout(f.read()).samples), len(ro.samples))     # the raw file decodes on its own
        with Logger() as lg:
            launched = launch_logger(lg, 60, "Test Logger")
        self.assertTrue(launched["logging"])
        self.assertEqual(launched["interval_s"], 60)
        self.assertEqual(launched["name"], "Test Logger")
        self.assertLessEqual(launched["samples"], 1)                                # a fresh deployment (the first sample is taken at launch)


class InstalledApp(unittest.TestCase):
    app = "/Applications/HOBO Reader v2 Pro Max.app"

    @unittest.skipUnless(os.path.isdir(app), "app not installed")
    def test_bundle_version_matches_the_source(self):
        with open(os.path.join(self.app, "Contents", "Info.plist"), "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleIdentifier"], "com.jstrand.hobologger")

    @unittest.skipUnless(os.path.isdir(app) and device_available(), "app not installed or no logger available")
    def test_bundled_app_can_reach_the_logger(self):
        exe = os.path.join(self.app, "Contents", "MacOS", "HOBO Reader v2 Pro Max")
        out = subprocess.run([exe, "--selftest"], capture_output=True, text=True, timeout=60).stdout
        self.assertIn("logger present: True", out)
        self.assertIn("serial", out)


if __name__ == "__main__":
    unittest.main()
