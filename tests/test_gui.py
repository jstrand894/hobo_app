"""Drives the real window (every button, switch, dialog and menu item) against a fake logger.

The USB layer, the user's settings file, launchd, and the file dialogs are all replaced, so these tests are
repeatable and cannot touch real hardware or real settings.
"""
import csv
import os
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from datetime import datetime
from unittest import mock

from hobo import autostart, gui, theme
from hobo.decode import Readout
from hobo.widgets import Battery, Card, MemoryBar, Pill, ProgressBar
from tests.helpers import FakeDevice, FakeLogger, make_memory


class Ev:
    def __init__(self, x=5, y=5):
        self.x, self.y = x, y


class GuiCase(unittest.TestCase):
    start_present = True
    device_kw = {}
    theme_name = "light"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["HOBO_THEME"] = self.theme_name
        theme.refresh(True)
        self.device = FakeDevice(**self.device_kw)
        self.device.present = self.start_present
        self.answers = {"yesno": True, "save": os.path.join(self.tmp, "out.csv"), "open": None}
        self.asked, self.warned, self.errors, self.dialog_calls = [], [], [], []
        self.auto = {"enabled": False, "calls": [], "fail": None}

        def auto_toggle(on):
            def f():
                self.auto["calls"].append(on)
                if self.auto["fail"]:
                    raise RuntimeError(self.auto["fail"])
                self.auto["enabled"] = on
            return f
        patches = [
            (gui, "CONFIG", os.path.join(self.tmp, "cfg.json")),
            (gui, "Logger", lambda: FakeLogger(self.device)),
            (gui, "logger_present", lambda: self.device.present),
            (autostart, "is_enabled", lambda: self.auto["enabled"]),
            (autostart, "enable", auto_toggle(True)),
            (autostart, "disable", auto_toggle(False)),
            (gui.messagebox, "askyesno", lambda *a, **k: (self.asked.append(a), self.answers["yesno"])[1]),
            (gui.messagebox, "showwarning", lambda *a, **k: self.warned.append(a[-1])),
            (gui.messagebox, "showerror", lambda *a, **k: self.errors.append(a[-1])),
            (gui.filedialog, "asksaveasfilename", self.fake_save),
            (gui.filedialog, "askopenfilename", lambda **k: (self.dialog_calls.append("open"), self.answers["open"])[1]),
            (gui.filedialog, "askdirectory", lambda **k: self.tmp),
            (gui.subprocess, "run", lambda *a, **k: None),
            (gui.os, "system", lambda *a, **k: 0),
        ]
        for obj, name, value in patches:
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.app = gui.App()
        self.addCleanup(self.close_app)
        if self.start_present:
            self.wait(lambda: self.app.link == "ok")

    def close_app(self):
        try:
            self.app.destroy()
        except tk.TclError:
            pass

    def fake_save(self, **k):
        self.dialog_calls.append("save")
        return self.answers["save"]

    # ---- helpers ----
    def spin(self, seconds=0.2):
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.01)

    def wait(self, cond, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if cond():
                return True
            time.sleep(0.01)
        self.fail("timed out waiting for a condition")

    def click(self, button):
        """Click an IconButton the way a mouse would (does nothing if it is disabled)."""
        button._set(pressed=True)
        button._release(Ev(5, 5))
        self.app.update()

    def states(self):
        a = self.app
        return {"readout": a.btn_readout.state, "save": a.btn_save.state, "launch": a.btn_launch.state,
                "refresh": a.btn_connect.state, "open": a.btn_open.state}

    def do_readout(self):
        self.click(self.app.btn_readout)
        self.wait(lambda: self.app.readout is not None and not self.app.busy)
        self.spin(0.1)

    def do_save(self):
        self.click(self.app.btn_save)
        self.spin(0.2)


class Startup(GuiCase):
    start_present = False

    def test_no_logger_state(self):
        a = self.app
        self.spin(0.5)
        self.assertEqual(a.title(), gui.APP_NAME)
        self.assertEqual(a.link, "none")
        self.assertEqual(a.link_text.get(), gui.LINK_TEXT["none"])
        self.assertEqual(a.status_pill.text, "No logger")
        self.assertEqual(self.states(), {"readout": "disabled", "save": "disabled", "launch": "disabled",
                                         "refresh": "normal", "open": "normal"})
        self.assertIsNone(a.battery.volts)
        self.assertEqual(a.info["Name"].get(), "—")
        self.assertEqual(a.live_temp.get(), "—")

    def test_plugging_in_connects_automatically(self):
        self.device.present = True
        self.wait(lambda: self.app.link == "ok")
        self.assertEqual(self.app.info["Name"].get(), "Test Logger")

    def test_unplugging_disconnects(self):
        self.device.present = True
        self.wait(lambda: self.app.link == "ok")
        self.device.present = False
        self.wait(lambda: self.app.link == "none")
        self.assertEqual(self.states()["readout"], "disabled")
        self.assertEqual(self.app.status_pill.text, "No logger")

    def test_unreadable_adapter_retries_until_it_works(self):
        self.device.fail_open = True
        self.device.present = True
        self.wait(lambda: self.app.link == "detected")
        self.assertEqual(self.app.status_pill.text, "Can't read")
        self.device.fail_open = False
        self.wait(lambda: self.app.link == "ok", timeout=12)


class Connected(GuiCase):
    def test_everything_is_filled_in(self):
        a = self.app
        self.assertEqual(a.link_text.get(), "Logger connected")
        self.assertEqual(a.status_pill.text, "Logging")
        self.assertEqual(a.info["Name"].get(), "Test Logger")
        self.assertEqual(a.sub.get(), "Serial 22035414")
        self.assertEqual(a.info["Interval"].get(), "1 min")
        self.assertEqual(a.info["Samples"].get(), "30")
        self.assertEqual(a.battery_text.get(), "3.55 V · Good")
        self.assertEqual(a.live_temp.get(), "71.6 °F")
        self.assertEqual(a.live_rh.get(), "35 % RH")
        self.assertIn("Next sample in 30 s", a.live_next.get())
        self.assertGreater(a.memory_bar.frac, 0)
        self.assertIn("used", a.info["Memory"].get())

    def test_buttons_while_logging(self):
        self.assertEqual(self.states(), {"readout": "normal", "save": "disabled", "launch": "disabled",
                                         "refresh": "normal", "open": "normal"})
        self.assertTrue(self.app.btn_readout.primary)              # step 1 is highlighted
        self.assertFalse(self.app.btn_save.primary or self.app.btn_launch.primary)

    def test_refresh_button_rereads_the_logger(self):
        self.device.mem[:0] = b""
        self.device.logging = False
        self.click(self.app.btn_connect)
        self.wait(lambda: self.app.status_pill.text == "Stopped")
        self.assertEqual(self.states()["launch"], "normal")

    def test_live_values_refresh_on_their_own(self):
        self.device.reading = (30.0, 60.0)
        self.app._last_live = 0
        self.wait(lambda: self.app.live_rh.get() == "60 % RH")
        self.assertEqual(self.app.live_temp.get(), "86.0 °F")

    def test_tooltips_explain_each_button(self):
        a = self.app
        texts = {b: tip for b, tip in ((a.btn_readout, "stops the logger"), (a.btn_save, "Read out"),
                                       (a.btn_launch, "Read out the device first"), (a.btn_connect, "Re-read"),
                                       (a.btn_open, ".hobo"))}
        for button, expect in texts.items():
            from hobo.widgets import Tooltip
            shown = []
            with mock.patch.object(Tooltip, "_show", lambda self, shown=shown: shown.append(self.text() if callable(self.text) else self.text)):
                button.event_generate("<Enter>")
                self.spin(0.8)
                button.event_generate("<Leave>")
            self.assertTrue(shown and expect.lower() in shown[0].lower(), (button.text, shown))


class ReadoutStep(GuiCase):
    def test_cancelling_the_confirmation_changes_nothing(self):
        self.answers["yesno"] = False
        self.click(self.app.btn_readout)
        self.spin(0.4)
        self.assertTrue(self.device.logging)
        self.assertIsNone(self.app.readout)
        self.assertEqual(len(self.asked), 1)

    def test_readout_stops_the_logger_and_loads_the_data(self):
        self.do_readout()
        a = self.app
        self.assertFalse(self.device.logging)
        self.assertEqual(len(a.readout["ro"].samples), 30)
        self.assertFalse(a.readout["saved"])
        self.assertEqual(len(a.plot.series[0]), 30)
        self.assertIn("30 samples", a.summary.get())
        self.assertIn("readout", a._notices)
        self.assertEqual(a.status_pill.text, "Stopped")
        self.assertEqual(self.states(), {"readout": "normal", "save": "normal", "launch": "normal",
                                         "refresh": "normal", "open": "normal"})
        self.assertTrue(a.btn_save.primary and not a.btn_readout.primary)   # step 2 is next
        self.assertEqual(a.progress["value"], 100)

    def test_a_stopped_logger_needs_no_confirmation(self):
        self.device.logging = False
        self.click(self.app.btn_connect)
        self.wait(lambda: self.app.status_pill.text == "Stopped")
        self.do_readout()
        self.assertEqual(self.asked, [])
        self.assertEqual(len(self.app.readout["ro"].samples), 30)

    def test_a_failure_shows_an_error_and_recovers(self):
        self.answers["yesno"] = True
        self.device.fail_open = True
        self.click(self.app.btn_readout)
        self.wait(lambda: "error" in self.app._notices)
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.readout)
        self.device.fail_open = False
        self.wait(lambda: self.states()["readout"] == "normal")

    def test_progress_bar_shows_while_working(self):
        seen = []
        orig = self.app._on_progress
        self.app._on_progress = lambda payload: (seen.append(payload), orig(payload))[1]
        self.device.logging = False
        self.device.mem = bytearray(self.device.mem)
        self.click(self.app.btn_connect)
        self.wait(lambda: self.app.status_pill.text == "Stopped")
        self.do_readout()
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1])


class SaveStep(GuiCase):
    def test_save_is_disabled_without_data(self):
        self.assertEqual(self.states()["save"], "disabled")
        self.click(self.app.btn_save)
        self.assertEqual(self.dialog_calls, [])

    def test_saves_a_celsius_csv_and_the_raw_file(self):
        self.do_readout()
        self.do_save()
        rows = list(csv.reader(open(self.answers["save"])))
        self.assertEqual(rows[0], ["#", "time GMT-5:00", "Temp C", "RH %"])
        self.assertEqual(len(rows), 31)
        self.assertTrue(os.path.exists(self.answers["save"][:-4] + ".hobo"))
        self.assertTrue(self.app.readout["saved"])
        self.assertIn("download", self.app._notices)
        self.assertNotIn("readout", self.app._notices)
        self.assertTrue(self.app.btn_launch.primary and not self.app.btn_save.primary)   # step 3 is next
        with open(self.answers["save"][:-4] + ".hobo", "rb") as f:
            self.assertEqual(len(Readout(f.read()).samples), 30)

    def test_raw_file_is_optional(self):
        self.app.save_hobo.set(False)
        self.do_readout()
        self.do_save()
        self.assertTrue(os.path.exists(self.answers["save"]))
        self.assertFalse(os.path.exists(self.answers["save"][:-4] + ".hobo"))

    def test_fahrenheit_display_never_changes_the_csv(self):
        self.app.celsius.set(False)
        self.do_readout()
        self.do_save()
        first = list(csv.reader(open(self.answers["save"])))[1]
        self.assertAlmostEqual(float(first[2]), 20.0, delta=0.06)       # degrees C, not 68 F

    def test_cancelled_dialog_saves_nothing(self):
        self.do_readout()
        self.answers["save"] = ""
        self.do_save()
        self.assertFalse(self.app.readout["saved"])
        self.assertEqual(os.listdir(self.tmp), ["cfg.json"] if os.path.exists(os.path.join(self.tmp, "cfg.json")) else [])

    def test_skips_the_dialog_when_a_folder_is_chosen(self):
        self.app.ask_each_time.set(False)
        self.app.last_dir = self.tmp
        self.do_readout()
        before = list(self.dialog_calls)
        self.do_save()
        self.assertEqual(self.dialog_calls, before)                       # no save dialog was shown
        self.assertTrue([f for f in os.listdir(self.tmp) if f.endswith(".csv")])

    def test_unwritable_location_reports_an_error(self):
        self.do_readout()
        self.answers["save"] = "/nonexistent-folder-xyz/out.csv"
        self.do_save()
        self.assertIn("error", self.app._notices)
        self.assertFalse(self.app.readout["saved"])


class OpenFile(GuiCase):
    def make_file(self, samples=None, name="From File", serial="22000099"):
        mem, ptr = make_memory(name=name, serial=serial, samples=samples or [(5.0 + i, 50.0) for i in range(12)])
        path = os.path.join(self.tmp, "x.hobo")
        with open(path, "wb") as f:
            f.write(mem[:-(-ptr // 64) * 64])
        return path

    def test_opens_and_plots_a_file_without_a_logger(self):
        self.device.present = False
        self.wait(lambda: self.app.link == "none")
        self.answers["open"] = self.make_file()
        self.click(self.app.btn_open)
        self.assertEqual(len(self.app.plot.series[0]), 12)
        self.assertIn("12 samples", self.app.summary.get())
        self.assertEqual(self.states()["save"], "normal")                  # can export it to CSV
        self.assertFalse(self.app.readout["from_logger"])

    def test_cancel_and_bad_files(self):
        self.answers["open"] = ""
        self.click(self.app.btn_open)
        self.assertIsNone(self.app.readout)
        bad = os.path.join(self.tmp, "bad.hobo")
        open(bad, "wb").write(b"not a hobo file")
        self.answers["open"] = bad
        self.click(self.app.btn_open)
        self.assertEqual(len(self.errors), 1)
        self.assertIsNone(self.app.readout)


class LaunchStep(GuiCase):
    def open_panel(self):
        self.click(self.app.btn_launch)
        self.spin(0.3)

    def to_stopped(self, save=False):
        self.do_readout()
        if save:
            self.do_save()

    def test_launch_needs_a_stopped_logger(self):
        self.assertEqual(self.states()["launch"], "disabled")
        self.click(self.app.btn_launch)
        self.assertFalse(self.app.launch_open)

    def test_panel_drops_down_and_folds_back(self):
        self.to_stopped()
        card = self.app._cards[0]
        h0 = card.winfo_height()
        self.open_panel()
        self.assertTrue(self.app.launch_open)
        self.assertGreater(card.winfo_height(), h0 + 80)
        self.assertEqual(self.app.launch_name.get(), "Test Logger")
        self.assertEqual(self.app.launch_interval.get(), "1 minute")
        self.assertIn("Memory lasts", self.app.launch_hint.get())
        self.open_panel()                                                 # clicking again closes it
        self.assertFalse(self.app.launch_open)
        self.spin(0.6)
        self.assertEqual(card.winfo_height(), h0)

    def test_unsaved_data_shows_the_destructive_warning(self):
        self.to_stopped()
        self.open_panel()
        a = self.app
        self.assertEqual(a.launch_alert.winfo_manager(), "pack")
        self.assertIn("haven't saved", a.launch_warn.get())
        self.assertIn("destructive", a.launch_warn.get())

    def test_unsaved_launch_is_blocked_until_acknowledged(self):
        self.to_stopped()
        self.open_panel()
        self.app.start_launch()
        self.assertTrue(self.warned)
        self.assertTrue(self.app.launch_open)
        self.assertFalse(self.device.logging)
        self.app.launch_ack.set(True)
        self.app.launch_name.set("Acknowledged")
        self.app.launch_interval.set("30 seconds")
        self.app.start_launch()
        self.wait(lambda: self.device.logging)
        self.assertEqual(self.device.interval, 30)
        self.assertEqual(self.device.name(), "Acknowledged")

    def test_saved_data_shows_no_warning_and_launches_directly(self):
        self.to_stopped(save=True)
        self.open_panel()
        a = self.app
        self.assertEqual(a.launch_alert.winfo_manager(), "")
        a.launch_name.set("Fresh Start")
        a.launch_interval.set("1 hour")
        a.start_launch()
        self.wait(lambda: self.device.logging and not self.app.busy)
        self.spin(0.3)
        self.assertEqual(self.device.interval, 3600)
        self.assertFalse(a.launch_open)
        self.assertEqual(a.info["Name"].get(), "Fresh Start")
        self.assertEqual(a.status_pill.text, "Logging")
        self.assertIn("action", a._notices)
        self.assertEqual(self.states()["launch"], "disabled")
        self.assertEqual(a.btn_launch.primary, False)
        with open(gui.CONFIG) as f:
            self.assertIn("1 hour", f.read())                              # interval is remembered

    def test_new_deployment_counts_as_unsaved_again(self):
        self.to_stopped(save=True)
        self.assertTrue(self.app._data_saved(self.app.logger_info))
        self.device.pointer += 60                                           # the logger recorded more since
        self.click(self.app.btn_connect)
        self.wait(lambda: not self.app.busy)
        self.spin(0.3)
        self.assertFalse(self.app._data_saved(self.app.logger_info))

    def test_bad_names_are_rejected(self):
        self.to_stopped(save=True)
        self.open_panel()
        for bad in ("", "   ", "x" * 41, "café"):
            self.warned.clear()
            self.app.launch_name.set(bad)
            self.app.start_launch()
            self.assertTrue(self.warned, repr(bad))
            self.assertFalse(self.device.logging)

    def test_every_interval_choice_maps_to_the_right_seconds(self):
        self.assertEqual(dict(gui.App.INTERVALS)["1 second"], 1)
        self.assertEqual(dict(gui.App.INTERVALS)["12 hours"], 43200)
        for label, secs in gui.App.INTERVALS:
            self.assertLessEqual(secs, 64800)

    def test_panel_closes_if_the_logger_starts_logging(self):
        self.to_stopped()
        self.open_panel()
        self.device.logging = True
        self.click(self.app.btn_connect)
        self.wait(lambda: not self.app.launch_open)

    def test_launch_failure_shows_an_error(self):
        self.to_stopped(save=True)
        self.open_panel()
        self.device.fail_open = True
        self.app.start_launch()
        self.wait(lambda: "error" in self.app._notices)
        self.assertFalse(self.app.busy)


class Controls(GuiCase):
    def test_unit_rocker(self):
        a = self.app
        self.assertFalse(a.celsius.get())
        a.unit_switch._click(Ev(90, 10))                                    # right half = Celsius
        self.assertTrue(a.celsius.get())
        self.assertEqual(a.live_temp.get(), "22.0 °C")
        a.unit_switch._click(Ev(10, 10))
        self.assertFalse(a.celsius.get())
        self.assertEqual(a.live_temp.get(), "71.6 °F")
        with open(gui.CONFIG) as f:
            self.assertIn('"celsius": false', f.read())

    def test_rocker_changes_plot_and_summary_units(self):
        self.do_readout()
        self.assertEqual(self.app.plot.series[4], "°F")
        self.assertIn("°F", self.app.summary.get())
        self.app.unit_switch._click(Ev(90, 10))
        self.assertEqual(self.app.plot.series[4], "°C")
        self.assertIn("°C", self.app.summary.get())

    def test_settings_are_remembered(self):
        self.app.save_hobo.set(False)
        self.app.ask_each_time.set(False)
        with open(gui.CONFIG) as f:
            text = f.read()
        self.assertIn('"save_hobo": false', text)
        self.assertIn('"ask_each_time": false', text)

    def test_settings_window_and_auto_open_switch(self):
        self.app.open_settings()
        self.spin(0.3)
        tops = [w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel"]
        self.assertEqual(len(tops), 1)
        found = []

        def walk(w):
            for c in w.winfo_children():
                walk(c)
                if c.__class__.__name__ == "Switch":
                    found.append(c)
        walk(tops[0])
        self.assertEqual(len(found), 3)                                     # .hobo, ask-each-time, auto-open
        auto = [s for s in found if s.__class__.__name__ == "Switch"][-1]
        auto.toggle()
        self.assertEqual(self.auto["calls"], [True])
        auto.toggle()
        self.assertEqual(self.auto["calls"], [True, False])

    def test_auto_open_failure_reverts_the_switch(self):
        self.auto["fail"] = "Install the app in your Applications folder first."
        self.app.open_settings()
        self.spin(0.2)
        tops = [w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel"]
        switches = []

        def walk(w):
            for c in w.winfo_children():
                walk(c)
                if c.__class__.__name__ == "Switch":
                    switches.append(c)
        walk(tops[0])
        switches[-1].toggle()
        self.assertFalse(switches[-1].var.get())
        self.assertIn("error", self.app._notices)

    def test_menu_items_follow_the_buttons_and_run_the_actions(self):
        self.spin(0.4)
        menu = self.app.nametowidget(self.app.cget("menu"))
        file_menu = self.app.nametowidget(menu.entrycget(2, "menu")) if menu.type(2) == "cascade" else None
        entries = [(file_menu.entrycget(i, "label"), file_menu.entrycget(i, "state")) for i in range(file_menu.index("end") + 1)]
        self.assertEqual([e[0] for e in entries], ["Open .hobo File…", "Refresh Logger", "Readout Device…", "Save Readout…",
                                                   "Launch Logger…"])
        self.assertEqual([e[1] for e in entries], ["normal", "normal", "normal", "disabled", "disabled"])
        file_menu.invoke(0)                                                 # Open .hobo File…
        self.assertIn("open", self.dialog_calls)
        file_menu.invoke(2)                                                 # Readout Device…
        self.wait(lambda: self.app.readout is not None and not self.app.busy)
        self.spin(0.5)
        entries = [file_menu.entrycget(i, "state") for i in range(file_menu.index("end") + 1)]
        self.assertEqual(entries, ["normal"] * 5)


class PlotBehaviour(GuiCase):
    def load(self, samples):
        mem, _ = make_memory(samples=samples, interval=1800)
        self.app.show(Readout(mem))
        self.spin(0.3)
        return self.app.plot

    def test_empty_state_message(self):
        plot = self.app.plot
        plot.clear()
        texts = [plot.itemcget(i, "text") for i in plot.find_all() if plot.type(i) == "text"]
        self.assertTrue(any("No data yet" in t for t in texts))

    def test_hover_tooltip_follows_the_mouse(self):
        plot = self.load([(10 + i * 0.5, 40 + i) for i in range(60)])
        plot._hover(Ev(plot.winfo_width() // 2, plot.winfo_height() // 2))
        hover_text = [plot.itemcget(i, "text") for i in plot.find_withtag("hover") if plot.type(i) == "text"]
        self.assertEqual(len(hover_text), 3)
        self.assertIn("Temperature", hover_text[1])
        plot._hover(Ev(1, 1))                                               # outside the plot area: no tooltip
        self.assertEqual(len(plot.find_withtag("hover")), 0)

    def test_drag_zooms_and_double_click_resets(self):
        plot = self.load([(10 + i * 0.5, 40 + i) for i in range(100)])
        w = plot.winfo_width()
        plot._press(Ev(w // 3, 100)); plot._drag_move(Ev(w // 2, 100)); plot._release(Ev(w // 2, 100))
        self.assertIsNotNone(plot.view)
        a, b = plot.view
        self.assertLess(b - a, 99)
        plot.reset_zoom()
        self.assertIsNone(plot.view)

    def test_a_tiny_drag_does_not_zoom(self):
        plot = self.load([(10 + i * 0.5, 40 + i) for i in range(100)])
        plot._press(Ev(300, 100)); plot._release(Ev(304, 100))
        self.assertIsNone(plot.view)

    def test_freezing_line_and_extreme_markers(self):
        plot = self.load([(-5 + i * 0.5, 50) for i in range(40)])          # crosses 0 C / 32 F
        texts = [plot.itemcget(i, "text") for i in plot.find_all() if plot.type(i) == "text"]
        self.assertTrue(any("Freezing (32°F)" in t for t in texts))
        self.assertTrue(any(t.startswith("max ") for t in texts))
        self.assertTrue(any(t.startswith("min ") for t in texts))
        plot2 = self.load([(15 + i * 0.1, 50) for i in range(40)])         # never freezing
        texts = [plot2.itemcget(i, "text") for i in plot2.find_all() if plot2.type(i) == "text"]
        self.assertFalse(any("Freezing" in t for t in texts))


class Alerts(GuiCase):
    device_kw = {"battery": 3.05}

    def test_low_battery_warning(self):
        self.assertIn("battery", self.app._notices)
        self.assertEqual(self.app._notices["battery"][1], "err")
        self.assertEqual(self.app.battery.word(), "Low")

    def test_alerts_clear_when_unplugged(self):
        self.device.present = False
        self.wait(lambda: self.app.link == "none")
        self.assertNotIn("battery", self.app._notices)


class FairBattery(GuiCase):
    device_kw = {"battery": 3.2}

    def test_fair_battery_is_a_warning(self):
        self.assertEqual(self.app._notices["battery"][1], "warn")


class GoodBatteryNoAlert(GuiCase):
    def test_no_battery_alert(self):
        self.assertNotIn("battery", self.app._notices)


class MemoryAlert(GuiCase):
    def test_nearly_full_memory(self):
        for pointer, kind in ((0xE000, "warn"), (0xF800, "err")):
            self.device.pointer = pointer
            self.click(self.app.btn_connect)
            self.wait(lambda: not self.app.busy)
            self.spin(0.3)
            self.assertEqual(self.app._notices["memory"][1], kind, hex(pointer))
        self.device.pointer = 0x300
        self.click(self.app.btn_connect)
        self.wait(lambda: "memory" not in self.app._notices)


class Notices(GuiCase):
    def test_notify_replace_dismiss_and_timeout(self):
        a = self.app
        a.notify("k", "ok", "first")
        a.notify("k", "warn", "second")
        self.assertEqual(len([n for n in a._notices if n == "k"]), 1)
        self.assertEqual(a._notices["k"][2], "second")
        a.dismiss("k")
        self.assertNotIn("k", a._notices)
        a.notify("t", "info", "temporary", timeout=300)
        self.wait(lambda: "t" not in a._notices, timeout=3)

    def test_action_links_run(self):
        hits = []
        self.app.notify("a", "ok", "go", actions=[("Do it", lambda: hits.append(1))])
        self.spin(0.3)
        row = self.app._notices["a"][0]
        link = [c for c in row.winfo_children() if c.cget("text") == "Do it"][0]
        link.event_generate("<Button-1>", when="now")
        self.app.update()
        self.assertEqual(hits, [1])


class ThemeSwitching(GuiCase):
    def test_both_palettes_define_the_same_colours(self):
        self.assertEqual(set(theme.PALETTE[False]), set(theme.PALETTE[True]))

    def test_dark_mode_recolours_everything_without_errors(self):
        self.do_readout()
        light_bg = self.app.cget("background")
        os.environ["HOBO_THEME"] = "dark"
        theme.refresh(True)
        self.app._retheme()
        self.spin(0.3)
        self.assertNotEqual(self.app.cget("background"), light_bg)
        self.assertEqual(self.app.plot.cget("background"), theme.color("plot_bg"))
        os.environ["HOBO_THEME"] = "light"
        theme.refresh(True)
        self.app._retheme()
        self.assertEqual(self.app.cget("background"), light_bg)


class LayoutStability(GuiCase):
    def test_cards_do_not_resize_when_values_change(self):
        a = self.app
        self.spin(1.2)                                               # let the layout settle after connecting
        base = [(c.winfo_width(), c.winfo_height()) for c in a._cards[:2]]
        for temp, rh, nxt in ((-40.0, 100, 3), (60.0, 5, 59), (22.0, 35, 31)):
            d = dict(a.logger_info)
            d.update(reading_c=temp, reading_rh=rh, next_sample_in_s=nxt, samples=21000, memory_used=0.97, logging=True)
            a._fill_live(d)
            self.spin(0.5)
            self.assertEqual([(c.winfo_width(), c.winfo_height()) for c in a._cards[:2]], base)


class Checklist(GuiCase):
    """The right-hand checklist: readout, save, launch. Items animate a check mark and grey out as they finish."""

    def states(self):
        return {k: self.app._steps_ui[k][0].state for k in ("readout", "save", "launch")}

    def grey(self, key):
        return self.app._steps_ui[key][1].cget("foreground") == theme.color("key")

    def settle(self):
        self.spin(0.8)                                                # let the check-mark animation finish

    def test_hidden_until_a_logger_is_connected(self):
        self.device.present = False
        self.wait(lambda: self.app.link == "none")
        self.assertEqual(self.app.check_rows.winfo_manager(), "")
        self.assertIn("Connect a logger", self.app.check_hint.cget("text"))
        self.device.present = True
        self.wait(lambda: self.app.link == "ok")
        self.assertEqual(self.app.check_rows.winfo_manager(), "pack")
        self.assertEqual(self.app.check_hint.winfo_manager(), "")

    def test_first_step_is_current_and_the_rest_wait(self):
        self.assertEqual(self.states(), {"readout": "pending", "save": "blocked", "launch": "blocked"})
        self.assertFalse(self.grey("readout"))                         # the current step is in full-strength text
        self.assertTrue(self.grey("save") and self.grey("launch"))

    def test_steps_check_off_in_order_and_grey_out(self):
        self.do_readout()
        self.settle()
        self.assertEqual(self.states(), {"readout": "done", "save": "pending", "launch": "blocked"})
        self.assertEqual(self.app._steps_ui["readout"][0].progress, 1.0)       # animation completed
        self.assertTrue(self.grey("readout"))
        self.do_save()
        self.settle()
        self.assertEqual(self.states(), {"readout": "done", "save": "done", "launch": "pending"})
        self.click(self.app.btn_launch)
        self.spin(0.3)
        self.app.start_launch()
        self.wait(lambda: self.app.steps["launch"])
        self.settle()
        self.assertEqual(self.states(), {"readout": "done", "save": "done", "launch": "done"})
        self.assertTrue(all(self.grey(k) for k in ("readout", "save", "launch")))

    def test_check_mark_animates(self):
        item = self.app._steps_ui["readout"][0]
        item.set_state("done")
        self.assertLess(item.progress, 1.0)                           # starts mid-animation, not instantly
        seen = set()
        end = time.time() + 1.0
        while time.time() < end:
            self.app.update()
            seen.add(round(item.progress, 1))
            time.sleep(0.01)
        self.assertEqual(item.progress, 1.0)
        self.assertGreater(len(seen), 4)                              # it went through several frames

    def test_a_new_readout_starts_the_checklist_over(self):
        self.do_readout()
        self.do_save()
        self.assertEqual(self.states()["save"], "done")
        self.do_readout()
        self.assertEqual(self.states(), {"readout": "done", "save": "pending", "launch": "blocked"})

    def test_unplugging_resets_it(self):
        self.do_readout()
        self.device.present = False
        self.wait(lambda: self.app.link == "none")
        self.assertEqual(self.app.steps, {"readout": False, "save": False, "launch": False})
        self.assertEqual(set(self.states().values()), {"blocked"})

    def test_saving_a_file_you_opened_does_not_tick_the_logger_steps(self):
        mem, ptr = make_memory(samples=[(5.0, 50.0)] * 5)
        path = os.path.join(self.tmp, "o.hobo")
        with open(path, "wb") as f:
            f.write(mem[:-(-ptr // 64) * 64])
        self.answers["open"] = path
        self.click(self.app.btn_open)
        self.do_save()
        self.assertEqual(self.states(), {"readout": "pending", "save": "blocked", "launch": "blocked"})

    def test_the_card_keeps_its_size_when_the_steps_appear(self):
        self.device.present = False
        self.wait(lambda: self.app.link == "none")
        self.spin(1.0)
        before = [(c.winfo_width(), c.winfo_height()) for c in self.app._cards[:2]]
        self.device.present = True
        self.wait(lambda: self.app.link == "ok")
        self.spin(1.0)
        self.assertEqual([(c.winfo_width(), c.winfo_height()) for c in self.app._cards[:2]], before)

    def test_live_reading_sits_in_the_status_card(self):
        logger_body = self.app._cards[0].body

        def texts(w):
            out = []
            for c in w.winfo_children():
                out += texts(c)
                if c.winfo_class() == "Label":
                    out.append(c.cget("text") or self.app.getvar(str(c.cget("textvariable"))) if c.cget("textvariable") else c.cget("text"))
            return out
        found = texts(logger_body)
        self.assertIn("71.6 °F", found)
        self.assertIn("35 % RH", found)


class Widgets(GuiCase):
    def test_battery_levels_and_words(self):
        b = Battery(self.app)
        for volts, word in ((3.6, "Good"), (3.35, "Good"), (3.3, "Good"), (3.2, "Fair"), (3.05, "Low"), (2.5, "Low"), (None, "")):
            b.set(volts)
            self.assertEqual(b.word(), word, volts)
        b.set(3.3)
        self.assertAlmostEqual(b.level(), 0.5)
        b.set(4.5)
        self.assertEqual(b.level(), 1.0)

    def test_memory_bar_clamps(self):
        m = MemoryBar(self.app)
        for value, expect in ((-1, 0.0), (0.4, 0.4), (5, 1.0), (None, 0.0)):
            m.set(value)
            self.assertEqual(m.frac, expect)

    def test_progress_bar_api(self):
        p = ProgressBar(self.app)
        p.config(mode="determinate", value=40)
        self.assertEqual(p.cget("mode"), "determinate")
        p["value"] = 75
        self.assertEqual(p.value, 75)
        p.start(10)
        self.spin(0.1)
        p.stop()
        self.assertIsNone(p._job)

    def test_pill_and_light_states(self):
        pill = Pill(self.app)
        for text, kind in (("Logging", "ok"), ("Stopped", "idle"), ("No logger", "off")):
            pill.set(text, kind)
            self.assertEqual((pill.text, pill.kind), (text, kind))
        for state in ("none", "detected", "ok", "none"):
            self.app.light.set(state)
            self.spin(0.15)
            self.assertEqual(self.app.light.state, state)
        self.assertIsNone(self.app.light._job)                             # pulse stops when not connected

    def test_card_can_shrink_again(self):
        card = Card(self.app)
        card.pack()
        tk.Label(card.body, text="x", height=10).pack()
        tk.Label(card.body, text="y", height=10).pack()
        self.spin(0.6)
        grown = card.winfo_reqheight()
        card.body.winfo_children()[0].destroy()
        self.spin(0.6)
        self.assertEqual(card.winfo_reqheight(), grown)                    # grow-only by design
        card.shrink()
        self.spin(0.6)
        self.assertLess(card.winfo_reqheight(), grown)


if __name__ == "__main__":
    unittest.main()
