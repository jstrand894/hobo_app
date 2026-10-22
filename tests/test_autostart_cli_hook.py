import contextlib
import io
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from hobo import __main__ as cli
from hobo import autostart
from tests.helpers import FakeDevice, FakeLogger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(path) as f:
        return f.read()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class AutostartScript(unittest.TestCase):
    """The watcher opens the app once per plug-in, never repeatedly while the logger stays plugged in."""

    def test_opens_once_per_plug_in(self):
        tmp = tempfile.mkdtemp()
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)

        def make(name, body):
            path = os.path.join(bindir, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
            return path
        ioreg = make("ioreg", f'#!/bin/sh\n[ -f "{tmp}/present" ] && echo "+-o HOBO U23-001A Temp/RH@03100000"\nexit 0\n')
        pgrep = make("pgrep", f'#!/bin/sh\n[ -f "{tmp}/running" ] && exit 0\nexit 1\n')
        opener = make("open", f'#!/bin/sh\necho OPEN >> "{tmp}/opens.log"\n')
        script = (autostart.SCRIPT_TEXT.replace("/usr/sbin/ioreg", ioreg).replace("/usr/bin/pgrep", pgrep)
                  .replace("/usr/bin/open", opener).replace("/bin/sleep", "sleep").replace(f"sleep {autostart.POLL_SECONDS}", "sleep 0.15"))
        path = os.path.join(tmp, "watch.sh")
        with open(path, "w") as f:
            f.write(script)
        proc = subprocess.Popen(["sh", path, "/Applications/Test.app"])
        self.addCleanup(lambda: (proc.send_signal(signal.SIGTERM), proc.wait(), shutil.rmtree(tmp, ignore_errors=True)))

        def opens():
            p = os.path.join(tmp, "opens.log")
            return len(read(p).split()) if os.path.exists(p) else 0
        touch = lambda n: write(os.path.join(tmp, n), "")
        time.sleep(0.6)
        self.assertEqual(opens(), 0)                                  # nothing plugged in
        touch("present"); time.sleep(1.2)
        self.assertEqual(opens(), 1)                                  # opened on plug-in, and only once
        os.remove(os.path.join(tmp, "present")); time.sleep(0.6)
        touch("present"); time.sleep(1.0)
        self.assertEqual(opens(), 2)                                  # plugged in again -> opened again
        os.remove(os.path.join(tmp, "present")); time.sleep(0.6)
        touch("running"); touch("present"); time.sleep(1.0)
        self.assertEqual(opens(), 2)                                  # app already running -> never re-opened


class AutostartEnableDisable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.calls = []
        patches = {"SUPPORT": os.path.join(self.tmp, "support"), "SCRIPT": os.path.join(self.tmp, "support", "w.sh"),
                   "PLIST": os.path.join(self.tmp, "agent.plist"), "INSTALLED": self.tmp}
        for k, v in patches.items():
            p = mock.patch.object(autostart, k, v)
            p.start()
            self.addCleanup(p.stop)

    def fake_launchctl(self, code=0):
        def run(*args):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, code, "", "boom" if code else "")
        p = mock.patch.object(autostart, "_launchctl", run)
        p.start()
        self.addCleanup(p.stop)

    def test_enable_writes_script_and_plist(self):
        self.fake_launchctl()
        autostart.enable()
        self.assertTrue(autostart.is_enabled())
        self.assertTrue(os.stat(autostart.SCRIPT).st_mode & stat.S_IXUSR)
        import plistlib
        with open(autostart.PLIST, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["ProgramArguments"][:2], ["/bin/sh", autostart.SCRIPT])
        self.assertTrue(plist["KeepAlive"] and plist["RunAtLoad"])
        self.assertEqual([c[0] for c in self.calls], ["bootout", "bootstrap"])

    def test_launchctl_failure_leaves_nothing_behind(self):
        self.fake_launchctl(code=5)
        with self.assertRaises(RuntimeError):
            autostart.enable()
        self.assertFalse(autostart.is_enabled())

    def test_disable_removes_files(self):
        self.fake_launchctl()
        autostart.enable()
        autostart.disable()
        self.assertFalse(os.path.exists(autostart.PLIST) or os.path.exists(autostart.SCRIPT))
        self.assertEqual(self.calls[-1][0], "bootout")

    def test_refuses_without_an_installed_app(self):
        with mock.patch.object(autostart, "INSTALLED", os.path.join(self.tmp, "nope.app")):
            with self.assertRaises(RuntimeError):
                autostart.enable()


class Cli(unittest.TestCase):
    def run_cli(self, *argv, device=None):
        device = device or FakeDevice(logging=True)
        out = io.StringIO()
        with mock.patch.object(cli, "Logger", lambda: FakeLogger(device)), mock.patch.object(sys, "argv", ["hobo", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli.main()
        return out.getvalue(), device

    def test_status(self):
        out, _ = self.run_cli("status")
        self.assertIn("Test Logger", out)
        self.assertIn("logging", out)

    def test_download_writes_celsius_csv_and_hobo(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        out, device = self.run_cli("download", "-o", tmp)
        files = sorted(os.listdir(tmp))
        self.assertEqual([os.path.splitext(f)[1] for f in files], [".csv", ".hobo"])
        self.assertTrue(device.logging)                                 # download never stops the logger
        rows = read(os.path.join(tmp, files[0])).splitlines()
        self.assertIn("Temp C", rows[0])
        self.assertEqual(len(rows), 31)

    def test_download_fahrenheit_flag(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.run_cli("download", "-o", tmp, "-f")
        csv_file = [f for f in os.listdir(tmp) if f.endswith(".csv")][0]
        self.assertIn("Temp F", read(os.path.join(tmp, csv_file)).splitlines()[0])

    def test_stop(self):
        out, device = self.run_cli("stop", "-y")
        self.assertFalse(device.logging)

    def test_launch_requires_a_stopped_logger(self):
        out, device = self.run_cli("launch", "-i", "60", "-y")
        self.assertIn("still logging", out)
        self.assertEqual(device.interval, 60)                           # untouched (it was already 60): no writes
        self.assertFalse(any(c[0] == 5 for c in device.commands))

    def test_launch(self):
        device = FakeDevice(logging=False, interval=1800)
        out, device = self.run_cli("launch", "-i", "600", "-n", "CLI Name", "-y", device=device)
        self.assertTrue(device.logging)
        self.assertEqual(device.interval, 600)
        self.assertIn("CLI Name", out)


class VersionHook(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_commits_bump_the_patch_number(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)

        def git(*args, **kw):
            return subprocess.run(["git", *args], cwd=tmp, capture_output=True, text=True, **kw)
        git("init", "-q", ".")
        os.makedirs(os.path.join(tmp, "hobo"))
        os.makedirs(os.path.join(tmp, "scripts", "hooks"))
        shutil.copy(os.path.join(ROOT, "hobo", "version.py"), os.path.join(tmp, "hobo", "version.py"))
        shutil.copy(os.path.join(ROOT, "scripts", "hooks", "pre-commit"), os.path.join(tmp, "scripts", "hooks", "pre-commit"))
        git("config", "core.hooksPath", "scripts/hooks")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        version = lambda: re.search(r'__version__ = "(.*?)"', read(os.path.join(tmp, "hobo", "version.py"))).group(1)
        start = [int(x) for x in version().split(".")]
        git("add", "-A"); git("commit", "-qm", "one")
        self.assertEqual(version(), f"{start[0]}.{start[1]}.{start[2] + 1}")
        write(os.path.join(tmp, "a.txt"), "x")
        git("add", "-A"); git("commit", "-qm", "two")
        self.assertEqual(version(), f"{start[0]}.{start[1]}.{start[2] + 2}")
        # a manual bump is respected, and bumping continues from it
        path = os.path.join(tmp, "hobo", "version.py")
        write(path, read(path).replace(version(), "2.0.0"))
        git("add", "-A"); git("commit", "-qm", "manual")
        self.assertEqual(version(), "2.0.0")
        write(os.path.join(tmp, "b.txt"), "x")
        git("add", "-A"); git("commit", "-qm", "after manual")
        self.assertEqual(version(), "2.0.1")


if __name__ == "__main__":
    unittest.main()
