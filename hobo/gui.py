"""Tkinter window: connect to a logger, download to .hobo + CSV, and look at the data."""
import bisect
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
import time as _time
from tkinter import filedialog, messagebox, ttk

from .actions import describe, launch_logger, live, read_until_end, stop_logging, write_files
from .decode import Readout
from .device import Logger, logger_present
from . import autostart, theme
from .version import __author__, __version__
from .widgets import (Battery, Card, CheckItem, GearButton, IconButton, Light, MemoryBar, Pill, ProgressBar, Switch,
                      Tooltip, UnitSwitch, disc, round_rect)

CONFIG = os.path.expanduser("~/.hobo_gui.json")
LINK_TEXT = {"none": "No logger detected",
             "detected": "Adapter detected, can't read it (is HOBOware open?)",
             "ok": "Logger connected"}
POLL_MS = 1500
APP_NAME = "HOBO Reader v2 Pro Max"
VALUE_W = {"Interval": 8, "Started": 17, "Running for": 10, "Samples": 7, "Memory": 23}
UI_FONT = ".AppleSystemUIFont"                  # the system font (San Francisco on macOS)


def asset(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)

TEMP_COLOR = "#d1495b"
RH_COLOR = "#2e86ab"


class Plot(tk.Canvas):
    """Temperature (left axis) and RH (right axis) over time.

    Hover for a crosshair and the exact values, drag to zoom into a range, double-click to reset.
    """

    ML, MR, MT, MB = 62, 62, 16, 36

    def __init__(self, master, **kw):
        super().__init__(master, highlightthickness=1, **kw)
        self.series = None            # (epochs, times, temps, rhs, unit)
        self.view = None              # (i0, i1) visible sample index range, None = everything
        self._drag = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag_move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", lambda e: self.reset_zoom())
        self.bind("<<TkSystemAppearanceChanged>>", lambda e: (theme.refresh(), self.redraw()))

    def set_data(self, ro: Readout, fahrenheit: bool):
        rows = list(ro.rows())
        times = [r[0] for r in rows]
        temps = [r[1] * 9 / 5 + 32 if fahrenheit else r[1] for r in rows]
        rhs = [r[2] for r in rows]
        self.series = ([t.timestamp() for t in times], times, temps, rhs, "°F" if fahrenheit else "°C")
        self.view = None
        self.redraw()

    def clear(self):
        self.series = None
        self.view = None
        self.redraw()

    def reset_zoom(self):
        self.view = None
        self.redraw()

    @staticmethod
    def _range(vals, pad=0.05):
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        span = hi - lo
        return lo - span * pad, hi + span * pad

    def _frame(self):
        w, h = self.winfo_width(), self.winfo_height()
        return self.ML, w - self.MR, self.MT, h - self.MB

    def _visible(self):
        n = len(self.series[0])
        i0, i1 = self.view if self.view else (0, n - 1)
        return max(0, i0), min(n - 1, i1)

    # ---- interaction ----
    def _index_at(self, x):
        epochs = self.series[0]
        i0, i1 = self._visible()
        x0, x1, _, _ = self._frame()
        if x1 <= x0:
            return i0
        t = epochs[i0] + (x - x0) / (x1 - x0) * (epochs[i1] - epochs[i0])
        i = bisect.bisect_left(epochs, t, i0, i1 + 1)
        if i > i0 and (i > i1 or t - epochs[i - 1] < epochs[i] - t):
            i -= 1
        return max(i0, min(i1, i))

    def _hover(self, e):
        self.delete("hover")
        if not self.series or not self.series[0] or self._drag:
            return
        x0, x1, y0, y1 = self._frame()
        if not (x0 <= e.x <= x1 and y0 <= e.y <= y1):
            return
        epochs, times, temps, rhs, unit = self.series
        i = self._index_at(e.x)
        i0, i1 = self._visible()
        vt = temps[i0:i1 + 1]
        vr = rhs[i0:i1 + 1]
        tlo, thi = self._range(vt)
        rlo, rhi = self._range(vr)
        span = max(epochs[i1] - epochs[i0], 1.0)
        x = x0 + (epochs[i] - epochs[i0]) / span * (x1 - x0)
        yt = y1 - (temps[i] - tlo) / (thi - tlo) * (y1 - y0)
        yr = y1 - (rhs[i] - rlo) / (rhi - rlo) * (y1 - y0)
        self.create_line(x, y0, x, y1, fill=theme.color("axis"), dash=(3, 3), tags="hover")
        for y, col in ((yr, theme.color("rh")), (yt, theme.color("temp"))):
            disc(self, x, y, 5.5, theme.color("plot_bg"), tags="hover")
            disc(self, x, y, 4, col, tags="hover")
        lines = [times[i].strftime("%b %d, %Y  %H:%M"), f"Temperature  {temps[i]:.1f} {unit}",
                 f"Humidity  {rhs[i]:.1f} %"]
        bw, bh = 188, 58
        bx = x + 14 if x + 14 + bw < x1 + self.MR - 4 else x - 14 - bw
        by = max(y0 + 4, min(y1 - bh - 4, min(yt, yr) - bh / 2))
        round_rect(self, bx, by, bx + bw, by + bh, 6, fill=theme.color("tip_bg"), outline=theme.color("tip_border"),
                   tags="hover")
        for k, line in enumerate(lines):
            self.create_text(bx + 10, by + 12 + k * 16, text=line, anchor="w", tags="hover",
                             fill=(theme.color("tip_text") if k == 0 else theme.color("temp") if k == 1
                                   else theme.color("rh")), font=(UI_FONT, 11, "bold" if k == 0 else "normal"))

    def _press(self, e):
        if not self.series:
            return
        x0, x1, y0, y1 = self._frame()
        if x0 <= e.x <= x1 and y0 <= e.y <= y1:
            self._drag = e.x
            self.delete("hover")

    def _drag_move(self, e):
        if self._drag is None:
            return
        self.delete("sel")
        x0, x1, y0, y1 = self._frame()
        x = max(x0, min(x1, e.x))
        self.create_rectangle(self._drag, y0, x, y1, fill=theme.color("select"), outline=theme.color("select"),
                              stipple="gray25", tags="sel")

    def _release(self, e):
        if self._drag is None:
            return
        start, self._drag = self._drag, None
        self.delete("sel")
        if abs(e.x - start) < 10:
            return
        a, b = sorted((self._index_at(start), self._index_at(e.x)))
        if b - a >= 2:
            self.view = (a, b)
            self.redraw()

    # ---- drawing ----
    def redraw(self):
        self.delete("all")
        self.config(background=theme.color("plot_bg"), highlightbackground=theme.color("plot_border"))
        w, h = self.winfo_width(), self.winfo_height()
        if w < 200 or h < 120:
            return
        if not self.series or not self.series[0]:
            self.create_text(w / 2, h / 2, text="No data yet. Download from a logger or open a .hobo file.",
                             fill=theme.color("empty"), font=(UI_FONT, 13))
            return
        epochs, times, temps, rhs, unit = self.series
        i0, i1 = self._visible()
        x0, x1, y0, y1 = self._frame()
        t_epoch0, t_epoch1 = epochs[i0], epochs[i1]
        span = max(t_epoch1 - t_epoch0, 1.0)
        vt, vr = temps[i0:i1 + 1], rhs[i0:i1 + 1]
        tlo, thi = self._range(vt)
        rlo, rhi = self._range(vr)
        tc, rc = theme.color("temp"), theme.color("rh")

        def px(e): return x0 + (e - t_epoch0) / span * (x1 - x0)
        def pyt(v): return y1 - (v - tlo) / (thi - tlo) * (y1 - y0)
        def pyr(v): return y1 - (v - rlo) / (rhi - rlo) * (y1 - y0)

        for i in range(6):  # horizontal grid + both axes' labels
            f = i / 5
            y = y1 - f * (y1 - y0)
            self.create_line(x0, y, x1, y, fill=theme.color("grid"))
            self.create_text(x0 - 6, y, text=f"{tlo + f * (thi - tlo):.1f}", anchor="e", fill=tc, font=(UI_FONT, 10))
            self.create_text(x1 + 6, y, text=f"{rlo + f * (rhi - rlo):.0f}", anchor="w", fill=rc, font=(UI_FONT, 10))
        short = span < 2 * 86400
        for i in range(7):  # time ticks
            f = i / 6
            t = times[i0] + timedelta(seconds=span * f)
            x = x0 + f * (x1 - x0)
            self.create_line(x, y1, x, y1 + 4, fill=theme.color("axis"))
            self.create_text(x, y1 + 6, text=t.strftime("%H:%M\n%b %d" if short else "%b %d\n%Y"), anchor="n",
                             fill=theme.color("text"), font=(UI_FONT, 9), justify="center")
        self.create_text(x0 - 6, y0 - 8, text=unit, anchor="e", fill=tc, font=(UI_FONT, 10, "bold"))
        self.create_text(x1 + 6, y0 - 8, text="% RH", anchor="w", fill=rc, font=(UI_FONT, 10, "bold"))
        self.create_rectangle(x0, y0, x1, y1, outline=theme.color("axis"))
        lx, ly = x0 + 12, y0 + 12                      # legend
        for label, color in (("Temperature", tc), ("Humidity", rc)):
            self.create_line(lx, ly, lx + 16, ly, fill=color, width=2.5)
            self.create_text(lx + 22, ly, text=label, anchor="w", fill=theme.color("legend"), font=(UI_FONT, 10))
            lx += 22 + 12 + len(label) * 6.5
        if self.view:
            self.create_text(x1 - 8, y0 + 12, text="Zoomed. Double-click to reset", anchor="e",
                             fill=theme.color("text"), font=(UI_FONT, 10, "italic"))

        freeze = 32.0 if unit == "°F" else 0.0                # freezing point, drawn when it is within view
        if tlo < freeze < thi:
            yf = pyt(freeze)
            self.create_line(x0, yf, x1, yf, fill=theme.color("freeze"), dash=(6, 4), width=1.3)
            self.create_text(x1 - 8, yf - 8, text=f"Freezing ({freeze:.0f}{unit})", anchor="e",
                             fill=theme.color("freeze"), font=(UI_FONT, 10, "bold"))

        n = i1 - i0 + 1
        step = max(1, n // max(1, int((x1 - x0) * 2)))
        idx = list(range(i0, i1 + 1, step))
        if idx[-1] != i1:
            idx.append(i1)
        for vals, conv, color in ((rhs, pyr, rc), (temps, pyt, tc)):
            pts = []
            for i in idx:
                pts += [px(epochs[i]), conv(vals[i])]
            if len(pts) >= 4:
                self.create_line(*pts, fill=color, width=1.4)
            elif len(pts) == 2:
                disc(self, pts[0], pts[1], 3, color)

        if n >= 3:  # mark the warmest and coldest points in view
            imax = i0 + vt.index(max(vt))
            imin = i0 + vt.index(min(vt))
            for i, word, above in ((imax, "max", True), (imin, "min", False)):
                x, y = px(epochs[i]), pyt(temps[i])
                disc(self, x, y, 7, theme.color("plot_bg"))
                disc(self, x, y, 5, tc)
                label = f"{word} {temps[i]:.1f} {unit}  ·  {times[i]:%b %d}"
                lx = max(x0 + 80, min(x1 - 80, x))
                ly = y - 16 if above else y + 16
                ly = max(y0 + 8, min(y1 - 8, ly))
                self.create_text(lx, ly, text=label, fill=theme.color("legend"), font=(UI_FONT, 10, "bold"))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x740")
        self.minsize(1060, 640)
        self.q = queue.Queue()
        self.busy = False
        self.cfg = self._load_cfg()
        self.last_dir = self.cfg["last_dir"]
        self.celsius = tk.BooleanVar(value=self.cfg["celsius"])
        self.save_hobo = tk.BooleanVar(value=self.cfg["save_hobo"])
        self.ask_each_time = tk.BooleanVar(value=self.cfg["ask_each_time"])
        self.celsius.trace_add("write", lambda *a: self._save_cfg())
        self.save_hobo.trace_add("write", lambda *a: self._save_cfg())
        self.ask_each_time.trace_add("write", lambda *a: (self.cfg.update(ask_each_time=self.ask_each_time.get()),
                                                           self._save_cfg()))
        self.logger_info = None
        self.readout = None       # data read from a logger (or opened from a file): {"ro", "mem", "saved"}
        self.launch_open = False
        self.steps = {"readout": False, "save": False, "launch": False}   # checklist progress for the connected logger
        self.steps_serial = None
        self.launch_name = tk.StringVar()
        self.launch_interval = tk.StringVar()
        self.launch_hint = tk.StringVar()
        self.launch_warn = tk.StringVar()
        self.launch_ack = tk.BooleanVar(value=False)    # "I understand" tick, only used when the data is unsaved
        self.launch_unsaved = False
        self.info = {k: tk.StringVar(value="—") for k in (
            "Status", "Name", "Serial", "Interval", "Started", "Running for", "Samples", "Memory", "Reading now",
            "Next sample")}
        self._last_live = 0.0
        self._poll_n = 0
        self._themed = []         # (widget, {option: theme colour name}) re-applied when the appearance changes
        self._cards = []
        self.sub = tk.StringVar(value="")
        self.live_temp = tk.StringVar(value="—")
        self.live_rh = tk.StringVar(value="")
        self.live_next = tk.StringVar(value="")
        self.summary = tk.StringVar(value="")
        self._notices = {}        # key -> (row widget, kind, text, actions, dismissible)
        self._muted = []          # (label, theme colour name) re-coloured when the appearance changes
        self._boxes = []
        self.msg = tk.StringVar(value="Plug in the adapter with a logger seated in it. Close HOBOware first.")
        self.link = "none"
        self.icons = {}
        for n in ("app",):
            try:
                self.icons[n] = tk.PhotoImage(file=asset(n + ".png"))
            except tk.TclError:
                pass  # missing icon: buttons just show text
        if "app" in self.icons:
            self.iconphoto(True, self.icons["app"])  # also sets the Dock icon when run from Python
        theme.refresh(True)
        self.configure(background=theme.color("window_bg"))
        self._build()
        self.set_link("none")           # initial look: grey light, "No logger" pill, everything that needs a logger off
        self._build_menu()
        self.bind("<<TkSystemAppearanceChanged>>", lambda e: self._retheme())
        self.after(100, self._pump)
        self.after(300, self._poll)

    def muted(self, parent, text, kind="muted", **kw):
        """A label in a secondary text colour that follows light/dark mode."""
        lab = ttk.Label(parent, text=text, foreground=theme.color(kind), **kw)
        self._muted.append((lab, kind))
        return lab

    def box(self, parent, **pack):
        """A bordered panel on the window background."""
        frame = tk.Frame(parent, background=self.cget("background"), highlightthickness=1,
                         highlightbackground=theme.color("box_border"), highlightcolor=theme.color("box_border"))
        self._boxes.append(frame)
        return frame

    def _retheme(self):
        theme.refresh()
        self.configure(background=theme.color("window_bg"))
        for w, cfg in self._themed:
            if w.winfo_exists():
                w.config(**{opt: theme.color(key) for opt, key in cfg.items()})
        wbg = theme.color("window_bg")
        self.gear.set_bg(wbg)
        self.light.config(background=wbg)
        self.light.draw()
        self.battery.draw()
        for c in self._cards:
            c.restyle()
        for item, name in self._steps_ui.values():
            item.draw()
        self.status_pill.draw()
        self.progress.draw()
        self.memory_bar.draw()
        for lab, kind in self._muted:
            if lab.winfo_exists():
                lab.config(foreground=theme.color(kind))
        self.plot.redraw()
        for key, (row, kind, text, actions, dismissible) in dict(self._notices).items():
            self.notify(key, kind, text, actions, dismissible)
        for w in (self.btn_connect, self.btn_readout, self.btn_save, self.btn_launch, self.btn_stop, self.btn_open,
                  self.unit_switch):
            w.draw()

    # ---- notices: coloured strips under the header (alerts, results, errors) ----
    def notify(self, key, kind, text, actions=(), dismissible=True, timeout=None):
        """kind: ok | err | warn | info. A notice with the same key is replaced."""
        self.dismiss(key)
        bg, fg = theme.color(f"{kind}_bg"), theme.color(f"{kind}_fg")
        row = tk.Frame(self.notices, background=bg)
        row.pack(fill="x")
        tk.Label(row, text=text, background=bg, foreground=fg, font=(UI_FONT, 12, "bold"), anchor="w",
                 justify="left", wraplength=640).pack(side="left", padx=(14, 10), pady=7)
        for label, cmd in actions:
            link = tk.Label(row, text=label, background=bg, foreground=fg, font=(UI_FONT, 12, "underline"),
                            cursor="pointinghand")
            link.pack(side="left", padx=(0, 12))
            link.bind("<Button-1>", lambda e, c=cmd: c())
        if dismissible:
            x = tk.Label(row, text="✕", background=bg, foreground=fg, font=(UI_FONT, 12), cursor="pointinghand")
            x.pack(side="right", padx=14)
            x.bind("<Button-1>", lambda e, k=key: self.dismiss(k))
        self._notices[key] = (row, kind, text, actions, dismissible)
        if timeout:
            self.after(timeout, lambda: self.dismiss(key) if self._notices.get(key, (None,))[0] is row else None)

    def dismiss(self, key):
        entry = self._notices.pop(key, None)
        if entry and entry[0].winfo_exists():
            entry[0].destroy()

    def _update_alerts(self, d):
        """Standing warnings about the connected logger."""
        lvl = self.battery.level()
        v = d.get("battery_v")
        if v and lvl is not None and lvl < 0.5:
            kind = "err" if lvl < 0.2 else "warn"
            self.notify("battery", kind, f"Battery is {self.battery.word().lower()} ({v:.2f} V). "
                        "Plan to replace it before the next deployment.", dismissible=False)
        else:
            self.dismiss("battery")
        used = d.get("memory_used", 0)
        if used >= 0.8:
            self.notify("memory", "err" if used >= 0.95 else "warn",
                        f"Logger memory is {used * 100:.0f}% full. Download the data before it fills up.",
                        dismissible=False)
        else:
            self.dismiss("memory")

    @staticmethod
    def _fmt_span(sec):
        days = sec / 86400
        if days < 2:
            return f"{sec / 3600:.0f} hours"
        if days < 60:
            return f"{days:.0f} days"
        if days < 730:
            return f"{days / 30.4:.0f} months"
        return f"{days / 365:.1f} years"

    @staticmethod
    def _reveal(path):
        subprocess.run(["open", "-R", path])

    @staticmethod
    def _open_path(path):
        subprocess.run(["open", path])

    def _build_menu(self):
        """macOS menu bar: app menu (About, Settings… with Cmd+,), File menu with the main actions and shortcuts."""
        bar = tk.Menu(self)
        appmenu = tk.Menu(bar, name="apple")
        bar.add_cascade(menu=appmenu)
        appmenu.add_command(label=f"About {APP_NAME}", command=self.open_settings)
        self.createcommand("tk::mac::ShowPreferences", self.open_settings)   # Settings… in the app menu, Cmd+,
        filem = tk.Menu(bar, tearoff=0)
        bar.add_cascade(label="File", menu=filem)
        entries = (("Open .hobo File…", "o", self.btn_open, self.open_file),
                   ("Refresh Logger", "r", self.btn_connect, self.connect),
                   ("Readout Device…", "d", self.btn_readout, self.readout_device),
                   ("Save Readout…", "s", self.btn_save, self.save_readout),
                   ("Launch Logger…", "l", self.btn_launch, self.toggle_launch),
                   ("Stop Logging…", ".", self.btn_stop, self.stop_logger))
        self._menu_entries = []
        for i, (label, key, btn, cmd) in enumerate(entries):
            filem.add_command(label=label, accelerator=f"Cmd+{key.upper() if key != '.' else '.'}", command=cmd)
            self._menu_entries.append((filem, i, btn))
            self.bind_all(f"<Command-{key}>", lambda e, b=btn, c=cmd: c() if b.state == "normal" else None)
        self.config(menu=bar)
        self._sync_menu()

    def _sync_menu(self):
        """Grey out menu items exactly when their buttons are disabled."""
        for menu, i, btn in self._menu_entries:
            menu.entryconfig(i, state="normal" if btn.state == "normal" else "disabled")
        self.after(250, self._sync_menu)

    def lbl(self, parent, bg="card_bg", fg="ink", size=13, bold=False, text=None, textvariable=None, **kw):
        """A label on a card/window surface whose colours follow light/dark mode."""
        w = tk.Label(parent, text=text or "", textvariable=textvariable, background=theme.color(bg),
                     foreground=theme.color(fg), font=(UI_FONT, size, "bold" if bold else "normal"), **kw)
        self._themed.append((w, {"background": bg, "foreground": fg}))
        return w

    def surface(self, widget, bg="card_bg"):
        """Register a widget whose background should track the theme."""
        self._themed.append((widget, {"background": bg}))
        return widget

    def card(self, parent, **kw):
        c = Card(parent, **kw)
        self._cards.append(c)
        return c

    def _build(self):
        self.notices = self.surface(tk.Frame(self, background=theme.color("window_bg")), "window_bg")
        self.notices.pack(fill="x")
        self.battery_text = tk.StringVar(value="")

        content = self.surface(tk.Frame(self, background=theme.color("window_bg")), "window_bg")
        content.pack(fill="both", expand=True, padx=18, pady=(12, 14))

        # ---- top row: connection status on the left, settings on the right ----
        wbg = theme.color("window_bg")
        bar = self.surface(tk.Frame(content, background=wbg), "window_bg")
        bar.pack(fill="x", pady=(0, 8))
        self.light = Light(bar, bg=wbg)
        self.light.pack(side="left")
        self.link_text = tk.StringVar(value=LINK_TEXT["none"])
        self.lbl(bar, bg="window_bg", fg="ink", size=13, bold=True, textvariable=self.link_text).pack(
            side="left", padx=(2, 0))
        self.gear = GearButton(bar, self.open_settings, wbg)
        self.gear.pack(side="right")

        # ---- row of cards: logger, live reading, options ----
        cards = self.surface(tk.Frame(content, background=theme.color("window_bg")), "window_bg")
        cards.pack(fill="x")

        logger = self.card(cards)
        logger.pack(side="left", fill="both", expand=True)
        lb = logger.body
        head = self.surface(tk.Frame(lb, background=theme.color("card_bg")))
        head.pack(fill="x")
        self.lbl(head, size=19, bold=True, textvariable=self.info["Name"]).pack(side="left")
        self.status_pill = Pill(head)
        self.status_pill.pack(side="right")
        self.lbl(lb, fg="key", size=12, textvariable=self.sub).pack(anchor="w", pady=(0, 10))
        middle = self.surface(tk.Frame(lb, background=theme.color("card_bg")))
        middle.pack(fill="x")
        grid = self.surface(tk.Frame(middle, background=theme.color("card_bg")))
        grid.pack(side="left", anchor="n")
        live_block = self.surface(tk.Frame(middle, background=theme.color("card_bg")))
        live_block.pack(side="right", anchor="n", padx=(28, 0))
        self.lbl(live_block, fg="temp", size=34, bold=True, textvariable=self.live_temp, width=8, anchor="e").pack(
            anchor="e")
        self.lbl(live_block, fg="rh", size=20, bold=True, textvariable=self.live_rh, width=10, anchor="e").pack(
            anchor="e", pady=(2, 0))
        self.lbl(live_block, fg="key", size=11, textvariable=self.live_next, width=20, anchor="e").pack(
            anchor="e", pady=(6, 0))
        keys = ("Interval", "Started", "Running for", "Samples", "Memory", "Battery")
        for idx, key in enumerate(keys):
            col, r = idx // 3, idx % 3
            self.lbl(grid, fg="key", size=12, text=key).grid(row=r, column=col * 2, sticky="w",
                                                              padx=(0 if col == 0 else 26, 8), pady=2)
            if key == "Battery":     # battery glyph + voltage and a word
                cell = self.surface(tk.Frame(grid, background=theme.color("card_bg")))
                cell.grid(row=r, column=col * 2 + 1, sticky="w", pady=2)
                self.battery = Battery(cell)
                self.battery.pack(side="left")
                self.lbl(cell, size=13, bold=True, textvariable=self.battery_text, width=14, anchor="w").pack(
                    side="left", padx=(8, 0))
                continue
            if key == "Memory":      # text with a small usage bar under it
                cell = self.surface(tk.Frame(grid, background=theme.color("card_bg")))
                cell.grid(row=r, column=col * 2 + 1, sticky="w", pady=2)
                self.lbl(cell, size=13, bold=True, textvariable=self.info[key], width=VALUE_W[key], anchor="w").pack(
                    anchor="w")
                self.memory_bar = MemoryBar(cell)
                self.memory_bar.pack(anchor="w", pady=(2, 0))
                continue
            self.lbl(grid, size=13, bold=True, textvariable=self.info[key], width=VALUE_W[key], anchor="w").grid(
                row=r, column=col * 2 + 1, sticky="w", pady=2)

        self.launch_panel = self.surface(tk.Frame(lb, background=theme.color("card_bg")))   # shown by "3  Launch…"
        self._build_launch_panel(self.launch_panel)

        check = self.card(cards)
        check.pack(side="left", fill="both", padx=(12, 0))
        self._build_checklist(check.body)

        # ---- toolbar ----
        row = self.surface(tk.Frame(content, background=theme.color("window_bg")), "window_bg")
        row.pack(fill="x", pady=(14, 8))

        def button(text, icon, cmd, **kw):
            return IconButton(row, text, icon, cmd, **kw)

        self.btn_readout = button("1  Readout device", "download", self.readout_device, state="disabled", primary=True)
        self.btn_readout.pack(side="left")
        self.btn_save = button("2  Save readout", "save", self.save_readout, state="disabled")
        self.btn_save.pack(side="left", padx=8)
        self.btn_launch = button("3  Launch…", "launch", self.toggle_launch, state="disabled")
        self.btn_launch.pack(side="left")
        self.btn_stop = button("Stop logging", "stop", self.stop_logger, state="disabled")
        self.btn_stop.pack(side="left", padx=(24, 0))
        self.btn_connect = button("Refresh", "refresh", self.connect)
        self.btn_connect.pack(side="left", padx=(24, 8))
        self.btn_open = button("Open file…", "open", self.open_file)
        self.btn_open.pack(side="left")

        # quick options live at the right end of the toolbar
        wbg = theme.color("window_bg")
        opts = self.surface(tk.Frame(row, background=wbg), "window_bg")
        opts.pack(side="right")
        self.unit_switch = self.surface(UnitSwitch(opts, self.celsius, wbg, command=self.refresh_plot), "window_bg")
        self.unit_switch.pack(side="right")

        def off_connect():
            return "Connect a logger first (adapter plugged in, HOBOware closed)."

        Tooltip(self.btn_connect, "Re-read the logger's status and settings.")
        Tooltip(self.btn_readout, lambda: "Read all recorded data off the logger into the app. This stops the logger."
                if self.btn_readout.state == "normal" else off_connect())
        Tooltip(self.btn_save, lambda: "Save the readout as a CSV file."
                if self.btn_save.state == "normal" else "Read out a logger (or open a .hobo file) first.")
        Tooltip(self.btn_launch, lambda: "Set the name and interval, then start the logger again."
                if self.btn_launch.state == "normal" else (
                    "Read out the device first. That stops the logger so it can be launched."
                    if self.link == "ok" else off_connect()))
        Tooltip(self.btn_stop, lambda: "Stop the logger without reading it out. The recorded data stays on it."
                if self.btn_stop.state == "normal" else (
                    "The logger is not logging." if self.link == "ok" else off_connect()))
        Tooltip(self.btn_open, "Open a saved .hobo file and plot it. No logger needed.")
        Tooltip(self.gear, "Settings and About")
        Tooltip(self.unit_switch, "Show temperature in °F or °C in the app. Saved CSV files are always in °C.")

        # ---- progress + message ----
        self.progress = ProgressBar(content)
        self.progress.pack(fill="x", pady=(0, 6))
        self.lbl(content, bg="window_bg", fg="key", size=12, textvariable=self.msg, anchor="w", justify="left",
                 wraplength=880).pack(fill="x")

        # ---- plot card ----
        pc = self.card(content, fill_height=True)
        pc.pack(fill="both", expand=True, pady=(10, 0))
        pb = pc.body
        top_row = self.surface(tk.Frame(pb, background=theme.color("card_bg")))
        top_row.pack(fill="x", pady=(0, 6))
        self.lbl(top_row, size=14, bold=True, text="Temperature and humidity").pack(side="left")
        self.lbl(top_row, fg="key", size=11, text="Hover for values  ·  drag to zoom  ·  double-click to reset").pack(
            side="right")
        self.lbl(pb, fg="key", size=12, textvariable=self.summary, justify="left", anchor="w").pack(
            side="bottom", fill="x", pady=(8, 0))
        self.plot = Plot(pb, height=200)
        self.plot.pack(fill="both", expand=True)
        self.current = None  # Readout being displayed

    # ---- helpers ----
    def _sync_buttons(self):
        """Enable the workflow buttons to match what is possible, and highlight the next step."""
        connected = self.link == "ok" and bool(self.logger_info)
        logging = connected and bool(self.logger_info.get("logging"))
        has_data = bool(self.readout)
        unsaved = has_data and not self.readout["saved"]
        self.btn_readout.config(state="normal" if connected else "disabled")
        self.btn_save.config(state="normal" if has_data else "disabled")
        self.btn_launch.config(state="normal" if connected and not logging else "disabled")
        self.btn_stop.config(state="normal" if logging else "disabled")
        self.btn_readout.set_primary(connected and not has_data)
        self.btn_save.set_primary(unsaved)
        self.btn_launch.set_primary(connected and not logging and has_data and not unsaved)
        if (not connected or logging) and self.launch_open:
            self.hide_launch()
        self._sync_checklist()

    def set_busy(self, busy, text=None):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_connect.config(state=state)
        self.btn_open.config(state=state)
        if busy:
            for b in (self.btn_readout, self.btn_save, self.btn_launch, self.btn_stop):
                b.config(state="disabled")
            self.progress.config(mode="indeterminate", value=0)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.config(mode="determinate", value=0)
            self._sync_buttons()
        if text:
            self.msg.set(text)

    def run_bg(self, fn, quiet=False):
        """Run fn() in a thread; post ('ok', result) or ('err', exc) to the queue.
        quiet=True reports an error in the message line only (no popup), for automatic attempts."""
        def work():
            try:
                self.q.put(("ok", fn()))
            except Exception as e:  # noqa: BLE001 - surfaced to the user
                self.q.put(("quiet_err" if quiet else "err", e))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                getattr(self, "_on_" + kind)(payload)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _on_progress(self, payload):
        done, total = payload
        if total:
            if str(self.progress.cget("mode")) != "determinate":
                self.progress.stop()
                self.progress.config(mode="determinate", maximum=100)
            self.progress["value"] = 100 * done / total
            self.msg.set(f"Downloading… {100 * done // total}%  ({done // 1024} of {-(-total // 1024)} KB)")
        else:
            self.msg.set(f"Downloading… {done // 1024} KB read")

    def _on_err(self, exc):
        self.set_busy(False)
        text = str(exc)
        if "No HOBO logger" in text or "No such device" in text or "Access denied" in text or "busy" in text.lower():
            text += ("\n\nMake sure the adapter is plugged in with a logger seated in it, "
                     "and that HOBOware is closed.")
        self.msg.set("")
        self.notify("error", "err", text.replace("\n\n", "  "))

    def _on_status(self, text):
        self.msg.set(text)

    def _on_quiet_err(self, exc):
        self.set_busy(False)
        self.set_link("detected" if logger_present() else "none")
        self.msg.set(f"Could not read the logger: {exc}")

    def _on_ok(self, result):
        self.set_busy(False)
        result()  # the callback returned by the worker, runs on the UI thread

    # ---- actions ----
    DEFAULTS = {"last_dir": None, "celsius": False, "save_hobo": True, "ask_each_time": True}

    @classmethod
    def _load_cfg(cls):
        cfg = dict(cls.DEFAULTS)
        try:
            with open(CONFIG) as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
        if not (cfg["last_dir"] and os.path.isdir(cfg["last_dir"])):
            cfg["last_dir"] = os.path.expanduser("~/Documents")
        return cfg

    def _save_cfg(self):
        self.cfg.update(celsius=self.celsius.get(), save_hobo=self.save_hobo.get())
        try:
            with open(CONFIG, "w") as f:
                json.dump(self.cfg, f)
        except OSError:
            pass

    def _remember_dir(self, d):
        self.last_dir = d
        self.cfg["last_dir"] = d
        self._save_cfg()

    def set_link(self, state):
        self.link = state
        self.light.set(state)
        self.link_text.set(LINK_TEXT[state])
        if state != "ok":
            self.dismiss("battery")
            self.dismiss("memory")
            self.battery.set(None)
            self.battery_text.set("")
            self.status_pill.set("No logger" if state == "none" else "Can't read", "off" if state == "none" else "idle")
            self.sub.set("")
            self.live_temp.set("—")
            self.live_rh.set("")
            self.live_next.set("")
            self.memory_bar.set(0)
        if state != "ok":
            self.logger_info = None
            for v in self.info.values():
                v.set("—")
            self._reset_steps()
            self._sync_buttons()

    def _poll(self):
        """Watch for the adapter being plugged in or removed; read the logger when it appears."""
        if not self.busy:
            try:
                present = logger_present()
            except Exception:  # noqa: BLE001 - libusb hiccup: treat as absent
                present = False
            if not present and self.link != "none":
                self.set_link("none")
                self.msg.set("Logger disconnected.")
            elif present and self.link == "none":
                self.set_link("detected")
                self.connect(auto=True)
            elif present and self.link == "detected":      # a failed read is retried, not left stuck
                self._poll_n += 1
                if self._poll_n % 2 == 0:
                    self.connect(auto=True)
            elif present and self.link == "ok" and _time.time() - self._last_live > 5:
                self.refresh_live()
        self.after(POLL_MS, self._poll)

    def refresh_live(self):
        """Update the values that change while logging, without touching the busy state or the progress bar."""
        self._last_live = _time.time()

        def work():
            try:
                with Logger() as lg:
                    d = live(lg)
                self.q.put(("live", d))
            except Exception:  # noqa: BLE001 - a missed refresh is harmless; the next one retries
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_live(self, d):
        if self.link == "ok" and self.logger_info and not self.busy:
            self.logger_info.update(d)
            self._fill_live(self.logger_info)
            self._update_alerts(self.logger_info)

    @staticmethod
    def _fmt_duration(sec):
        sec = int(max(0, sec))
        days, rem = divmod(sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        if days:
            return f"{days} d {hours} h"
        return f"{hours} h {mins} min" if hours else f"{mins} min"

    def _fill_live(self, d):
        c = d.get("reading_c")
        if c is not None:
            f = self.celsius.get()
            self.live_temp.set(f"{c if f else c * 9 / 5 + 32:.1f} {'°C' if f else '°F'}")
            self.live_rh.set(f"{d['reading_rh']:.0f} % RH")
        self.info["Samples"].set(f"{d['samples']:,}")
        used = d["memory_used"]
        text = f"{used * 100:.1f}% used" if used >= 0.001 else "< 0.1% used"
        if d.get("logging") and d.get("interval_s") and 0 < used < 1:
            left = (1 - used) / used * d["samples"] * d["interval_s"] if d["samples"] else 0
            if left:
                full = datetime.now() + timedelta(seconds=left)
                text += f" · full {full:%b %d}" if full.year == datetime.now().year else f" · full {full:%b %d, %Y}"
        self.info["Memory"].set(text)
        self.memory_bar.set(used)
        started = d.get("started")
        if d.get("logging") and started:
            self.info["Running for"].set(self._fmt_duration((datetime.now() - started).total_seconds()))
        else:
            self.info["Running for"].set("—")
        ns = d.get("next_sample_in_s")
        self.info["Next sample"].set(f"{ns} s" if d.get("logging") and ns is not None else "—")
        self.live_next.set(f"Next sample in {ns} s" if d.get("logging") and ns is not None else "")

    def connect(self, auto=False):
        self.set_busy(True, "Reading logger…")

        def work():
            with Logger() as lg:
                d = describe(lg)
            return lambda: self._show_info(d)
        self.run_bg(work, quiet=auto)

    def _show_info(self, d):
        if self.steps_serial and self.steps_serial != d["serial"]:
            self._reset_steps()
        self.set_link("ok")
        self.logger_info = d
        self.info["Status"].set("Logging" if d["logging"] else "Stopped / idle")
        self.status_pill.set("Logging" if d["logging"] else "Stopped", "ok" if d["logging"] else "idle")
        self.sub.set(f"Serial {d['serial']}")
        self._fill_live(d)
        self._last_live = _time.time()
        self.battery.set(d.get("battery_v"))
        v = d.get("battery_v")
        self.battery_text.set(f"{v:.2f} V · {self.battery.word()}" if v else "")
        self.info["Name"].set(d["name"])
        self.info["Serial"].set(d["serial"])
        self.info["Interval"].set(self._fmt_interval(d["interval_s"]))
        self.info["Started"].set(d["started"].strftime("%b %d, %Y %H:%M"))
        self._sync_buttons()
        self._update_alerts(d)
        self.msg.set("Logger connected. Step 1: read out the device to load its data (this stops the logger).")

    @staticmethod
    def _fmt_interval(s):
        if s % 3600 == 0:
            return f"{s // 3600} h"
        if s % 60 == 0:
            return f"{s // 60} min"
        return f"{s} s"

    INTERVALS = [("1 second", 1), ("5 seconds", 5), ("10 seconds", 10), ("30 seconds", 30), ("1 minute", 60),
                 ("5 minutes", 300), ("10 minutes", 600), ("15 minutes", 900), ("30 minutes", 1800),
                 ("1 hour", 3600), ("2 hours", 7200), ("6 hours", 21600), ("12 hours", 43200)]

    # ---- checklist (right-hand card) ----
    STEPS = (("readout", "Readout device", "Stop the logger, load its data"),
             ("save", "Save readout", "Save the data as a CSV"),
             ("launch", "Launch", "Set options, then restart"))

    def _build_checklist(self, body):
        self.lbl(body, fg="key", size=10, bold=True, text="CHECKLIST").pack(anchor="w")
        # the steps and the "connect a logger" hint share one fixed-size area, so the card never changes size
        self.check_holder = self.surface(tk.Frame(body, background=theme.color("card_bg")))
        self.check_holder.pack(anchor="w", fill="x")
        self.check_rows = self.surface(tk.Frame(self.check_holder, background=theme.color("card_bg")))
        self._steps_ui = {}
        for number, (key, title, desc) in enumerate(self.STEPS, start=1):
            row = self.surface(tk.Frame(self.check_rows, background=theme.color("card_bg")))
            row.pack(fill="x", pady=(10, 0))
            item = CheckItem(row, number)
            item.pack(side="left", anchor="n")
            text = self.surface(tk.Frame(row, background=theme.color("card_bg")))
            text.pack(side="left", padx=(10, 0))
            name = self.lbl(text, size=13, bold=True, text=title, width=18, anchor="w")
            name.pack(anchor="w")
            self.lbl(text, fg="key", size=11, text=desc, width=26, anchor="w").pack(anchor="w")
            self._steps_ui[key] = (item, name)
        self.check_rows.pack(fill="x")
        self.update_idletasks()
        self.check_holder.config(width=self.check_rows.winfo_reqwidth(), height=self.check_rows.winfo_reqheight())
        self.check_holder.pack_propagate(False)
        self.check_hint = self.lbl(self.check_holder, fg="key", size=12, text="Connect a logger to start.", anchor="w")
        self.check_rows.pack_forget()
        self.check_hint.pack(anchor="nw", pady=(12, 0))

    def _set_fg(self, widget, key):
        """Change a themed label's colour and remember it, so a light/dark switch keeps it."""
        for i, (w, cfg) in enumerate(self._themed):
            if w is widget:
                cfg["foreground"] = key
        widget.config(foreground=theme.color(key))

    def _reset_steps(self):
        self.steps = {"readout": False, "save": False, "launch": False}
        self.steps_serial = None
        self._sync_checklist()

    def _sync_checklist(self):
        connected = self.link == "ok" and bool(self.logger_info)
        if not connected:
            self.check_rows.pack_forget()
            self.check_hint.pack(anchor="nw", pady=(12, 0))
            for item, name in self._steps_ui.values():
                item.set_state("blocked", animate=False)
            return
        self.check_hint.pack_forget()
        self.check_rows.pack(fill="x")
        rd, sv, ln = self.steps["readout"], self.steps["save"], self.steps["launch"]
        states = {"readout": "done" if rd else "pending",
                  "save": "done" if sv else ("pending" if rd else "blocked"),
                  "launch": "done" if ln else ("pending" if sv else "blocked")}
        for key, state in states.items():
            item, name = self._steps_ui[key]
            item.set_state(state)
            self._set_fg(name, "ink" if state == "pending" else "key")      # finished steps grey out

    # ---- launch panel (drops down inside the logger card) ----
    def _build_launch_panel(self, panel):
        rule = tk.Frame(panel, height=1, background=theme.color("card_border"))
        self._themed.append((rule, {"background": "card_border"}))
        rule.pack(fill="x", pady=(14, 10))
        self.lbl(panel, fg="key", size=10, bold=True, text="LAUNCH SETTINGS").pack(anchor="w")
        form = self.surface(tk.Frame(panel, background=theme.color("card_bg")))
        form.pack(fill="x", pady=(6, 0))
        self.lbl(form, fg="key", size=12, text="Name").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(form, textvariable=self.launch_name, width=30).grid(row=0, column=1, sticky="w", pady=4)
        self.lbl(form, fg="key", size=12, text="Interval").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        labels = [t for t, _ in self.INTERVALS]
        combo = ttk.Combobox(form, textvariable=self.launch_interval, values=labels, state="readonly", width=16)
        combo.grid(row=1, column=1, sticky="w", pady=4)
        combo.bind("<<ComboboxSelected>>", lambda e: self._update_launch_hint())
        self.lbl(form, fg="key", size=11, textvariable=self.launch_hint).grid(row=2, column=1, sticky="w")
        # shown only when this logger's data has NOT been saved
        self.launch_alert = self.surface(tk.Frame(panel, background=theme.color("card_bg")))
        self.lbl(self.launch_alert, fg="err_fg", size=12, bold=True, textvariable=self.launch_warn, justify="left",
                 anchor="w", wraplength=560).pack(anchor="w", pady=(12, 4))
        ttk.Checkbutton(self.launch_alert, text="I understand. Erase the data and launch.",
                        variable=self.launch_ack).pack(anchor="w")
        buttons = self.surface(tk.Frame(panel, background=theme.color("card_bg")))
        buttons.pack(fill="x", pady=(12, 0))
        self._launch_buttons = buttons
        ttk.Button(buttons, text="Start logging", command=self.start_launch).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.hide_launch).pack(side="left", padx=(8, 0))

    def _update_launch_hint(self):
        secs = dict(self.INTERVALS)[self.launch_interval.get()]
        self.launch_hint.set(f"Starts immediately. Memory lasts about {self._fmt_span(self._capacity_samples() * secs)}.")

    def _data_saved(self, d):
        """True if what is on the logger right now has been read out and saved (or matches an opened file)."""
        r = self.readout
        if not r or r["ro"].serial != d["serial"]:
            return False
        if r.get("from_logger"):
            return bool(r["saved"]) and r["logger_samples"] == d["samples"]
        return abs(len(r["ro"].samples) - d["samples"]) <= 3        # a file you opened: compare the sample count

    def toggle_launch(self):
        if self.launch_open:
            self.hide_launch()
            return
        d = self.logger_info
        if not d or d["logging"]:
            return
        labels = [t for t, _ in self.INTERVALS]
        remembered = self.cfg.get("launch_interval")
        self.launch_name.set(d["name"])
        self.launch_interval.set(remembered if remembered in labels else next(
            (t for t, v in self.INTERVALS if v == d["interval_s"]), labels[3]))
        self._update_launch_hint()
        self.launch_unsaved = not self._data_saved(d)
        self.launch_ack.set(False)
        if self.launch_unsaved:
            self.launch_warn.set("You haven't saved this logger's data yet. Launching is destructive: it erases "
                                 "the data currently on the logger.")
            self.launch_alert.pack(fill="x", before=self._launch_buttons)
        else:
            self.launch_alert.pack_forget()
        self.launch_panel.pack(fill="x")
        self.launch_open = True
        for c in self._cards:
            c.shrink()

    def hide_launch(self):
        self.launch_panel.pack_forget()
        self.launch_open = False
        for c in self._cards:
            c.shrink()

    def start_launch(self):
        nm = self.launch_name.get().strip()
        if not nm or len(nm) > 40 or not all(32 <= ord(c) < 127 for c in nm):
            messagebox.showwarning(APP_NAME, "The name must be 1 to 40 plain characters.")
            return
        if self.launch_unsaved and not self.launch_ack.get():
            messagebox.showwarning(APP_NAME, "This logger's data has not been saved. Save the readout first, or "
                                   "tick the box to erase it and launch anyway.")
            return
        seconds = dict(self.INTERVALS)[self.launch_interval.get()]
        self.cfg["launch_interval"] = self.launch_interval.get()
        self._save_cfg()
        self.hide_launch()
        self._do_launch(nm, seconds)

    def _launched(self):
        self.steps["launch"] = True
        self.steps_serial = (self.logger_info or {}).get("serial", self.steps_serial)
        self._sync_checklist()

    @staticmethod
    def _capacity_samples():
        return (0x10000 - 0x190) // 3        # about 21,700 samples (64 KB, 3 bytes each)

    def _do_launch(self, name, seconds):
        self.set_busy(True, "Launching…")

        def work():
            with Logger() as lg:
                after = launch_logger(lg, seconds, name, progress=lambda t: self.q.put(("status", t)))
            return lambda: (self._show_info(after), self.msg.set(""), self._launched(),
                            self.notify("action", "ok", f"Launched. Logging every {self._fmt_interval(seconds)}; "
                                        f"memory lasts about {self._fmt_span(self._capacity_samples() * seconds)}.",
                                        timeout=30000))
        self.run_bg(work)

    def open_settings(self):
        dlg = tk.Toplevel(self)
        dlg.title("Settings")
        dlg.transient(self)
        dlg.resizable(False, False)
        bg = dlg.cget("background")
        body = ttk.Frame(dlg, padding=18)
        body.pack(fill="both", expand=True)

        self.muted(body, "SETTINGS", "muted2", font=(UI_FONT, 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        UnitSwitch(body, self.celsius, bg, command=self.refresh_plot).grid(row=1, column=0, sticky="w", pady=7,
                                                                           padx=(0, 10))
        ttk.Label(body, text="Temperature units in the app", font=(UI_FONT, 13)).grid(row=1, column=1, sticky="w")
        for r, (var, text) in enumerate(((self.save_hobo, "Also save the raw .hobo file"),
                                         (self.ask_each_time, "Ask where to save each readout")), start=2):
            Switch(body, var, bg).grid(row=r, column=0, sticky="w", pady=7, padx=(0, 10))
            ttk.Label(body, text=text, font=(UI_FONT, 13)).grid(row=r, column=1, sticky="w")
        auto = tk.BooleanVar(value=autostart.is_enabled())

        def toggle_auto():
            try:
                autostart.enable() if auto.get() else autostart.disable()
                self.notify("action", "info", "The app will open when a logger is plugged in." if auto.get()
                            else "Auto-open is off.", timeout=8000)
            except Exception as e:  # noqa: BLE001
                auto.set(autostart.is_enabled())
                self.notify("error", "err", str(e))
        Switch(body, auto, bg, command=toggle_auto).grid(row=4, column=0, sticky="w", pady=7, padx=(0, 10))
        ttk.Label(body, text="Open this app when a logger is plugged in", font=(UI_FONT, 13)).grid(
            row=4, column=1, sticky="w")
        self.muted(body, "Save downloads to:").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(10, 2))
        folder = tk.StringVar(value=self.last_dir)
        ttk.Label(body, textvariable=folder, font=(UI_FONT, 12), wraplength=420).grid(
            row=6, column=0, columnspan=2, sticky="w")
        btns = ttk.Frame(body)
        btns.grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def choose():
            d = filedialog.askdirectory(initialdir=self.last_dir, parent=dlg)
            if d:
                self._remember_dir(d)
                folder.set(d)
        ttk.Button(btns, text="Choose…", command=choose).pack(side="left")
        ttk.Button(btns, text="Show in Finder", command=lambda: os.system(f'open "{self.last_dir}"')).pack(
            side="left", padx=8)

        ttk.Separator(body).grid(row=8, column=0, columnspan=2, sticky="ew", pady=16)
        self.muted(body, "ABOUT", "muted2", font=(UI_FONT, 10, "bold")).grid(
            row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text=APP_NAME, font=(UI_FONT, 15, "bold")).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(body, text=f"Version {__version__}").grid(row=11, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text=f"Built by {__author__}").grid(row=12, column=0, columnspan=2, sticky="w")
        self.muted(body, "Reads HOBO U23-001A (U23 Pro v2) loggers over the optical USB base station.", "muted2",
                   wraplength=360).grid(row=13, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(body, text="Done", command=dlg.destroy).grid(row=14, column=0, columnspan=2, sticky="e", pady=(14, 0))
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.grab_set()

    def readout_device(self):
        """Step 1: stop the logger (if it is logging), read all its data into the app, and plot it."""
        d = self.logger_info or {}
        if d.get("logging") and not messagebox.askyesno(
                "Read out and stop?",
                f"Read out {d.get('name', 'this logger')} (serial {d.get('serial', '?')})?\n\n"
                "Reading the data stops the logger. The data stays on it until you launch it again.",
                icon="warning", default="yes"):
            return
        self.set_busy(True, "Reading out the device…")

        def work():
            with Logger() as lg:
                if lg.status()["logging"]:
                    self.q.put(("status", "Stopping the logger…"))
                    lg.stop()
                mem = read_until_end(lg, progress=lambda a, t: self.q.put(("progress", (a, t))))
                after = describe(lg)
            ro = Readout(mem)
            return lambda: self._read_out(ro, mem, after)
        self.run_bg(work)

    def stop_logger(self):
        """Stop the logger without reading it out (the recorded data stays on it)."""
        d = self.logger_info or {}
        if not d.get("logging"):
            return
        if not messagebox.askyesno(
                "Stop logging?",
                f"Stop logging on {d.get('name', 'this logger')} (serial {d.get('serial', '?')})?\n\n"
                "The data already recorded stays on the logger. Use Readout device to load and save it.",
                icon="warning", default="no"):
            return
        self.set_busy(True, "Stopping the logger…")

        def work():
            with Logger() as lg:
                after = stop_logging(lg)
            return lambda: (self._show_info(after), self.msg.set(""),
                            self.notify("action", "info", "Logging stopped. The data is still on the logger.",
                                        timeout=20000))
        self.run_bg(work)

    def _read_out(self, ro, mem, after):
        self.readout = {"ro": ro, "mem": mem, "saved": False, "from_logger": True, "logger_samples": after["samples"]}
        self.steps = {"readout": True, "save": False, "launch": False}      # a new readout starts the checklist over
        self.steps_serial = ro.serial
        self.progress["value"] = 100
        self._show_info(after)
        self.show(ro)
        self.msg.set("")
        self.notify("readout", "ok", f"Read {len(ro.samples):,} samples and stopped the logger. "
                    "Step 2: save the readout.", actions=[("Save readout…", self.save_readout)], timeout=60000)

    def save_readout(self):
        """Step 2: save the readout as a CSV (and the raw .hobo, if enabled in Settings)."""
        if not self.readout:
            return
        ro, mem = self.readout["ro"], self.readout["mem"]
        default = f"{ro.name}_{datetime.now():%Y%m%d-%H%M}".replace(" ", "_")
        if self.ask_each_time.get() or not os.path.isdir(self.last_dir):
            # No filetypes list: it adds a "Format" popup that the compact macOS save panel clips off its edge.
            path = filedialog.asksaveasfilename(
                title="Save readout as", initialdir=self.last_dir, initialfile=default + ".csv",
                defaultextension=".csv")
            if not path:
                return  # cancelled
        else:
            path = os.path.join(self.last_dir, default + ".csv")
        base = path[:-4] if path.lower().endswith(".csv") else path
        self._remember_dir(os.path.dirname(base))
        try:
            csv_path, hobo_path = write_files(ro, mem, base, fahrenheit=False, save_hobo=self.save_hobo.get())  # CSV is °C
        except OSError as e:
            self.notify("error", "err", f"Could not save: {e}")
            return
        self.readout["saved"] = True
        if self.readout.get("from_logger") and self.steps["readout"]:
            self.steps["save"] = True
        self.dismiss("readout")
        self.notify("download", "ok", f"Saved {len(ro.samples):,} samples to {os.path.basename(csv_path)}. "
                    "Step 3: launch the logger when you are ready.",
                    actions=[("Open CSV", lambda: self._open_path(csv_path)),
                             ("Show in Finder", lambda: self._reveal(csv_path))], timeout=60000)
        self._sync_buttons()

    def open_file(self):
        path = filedialog.askopenfilename(title="Open a .hobo file", filetypes=[("HOBO files", "*.hobo")])
        if not path:
            return
        try:
            with open(path, "rb") as f:
                mem = f.read()
            ro = Readout(mem)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Could not read that file:\n{e}")
            return
        self.readout = {"ro": ro, "mem": mem, "saved": True, "from_logger": False}   # already on disk; can still export a CSV
        self.msg.set(f"Opened {os.path.basename(path)}: {len(ro.samples):,} samples")
        self.show(ro)
        self._sync_buttons()

    def show(self, ro):
        self.current = ro
        self.plot.set_data(ro, fahrenheit=not self.celsius.get())
        self._summarize(ro)

    def _summarize(self, ro):
        rows = list(ro.rows())
        if not rows:
            self.summary.set("No samples")
            return
        f = not self.celsius.get()
        temps = [r[1] * 9 / 5 + 32 if f else r[1] for r in rows]
        rhs = [r[2] for r in rows]

        def span(v, unit):
            return f"min {min(v):.1f}  ·  max {max(v):.1f}  ·  avg {sum(v) / len(v):.1f} {unit}"

        first, last = rows[0][0], rows[-1][0]
        self.summary.set(f"{len(rows):,} samples, {first:%b %d, %Y} to {last:%b %d, %Y}\n"
                         f"Temperature   {span(temps, '°F' if f else '°C')}\n"
                         f"Humidity        {span(rhs, '%')}")

    def refresh_plot(self):
        if self.logger_info and self.link == "ok":
            self._fill_live(self.logger_info)
        if self.current:
            self.plot.set_data(self.current, fahrenheit=not self.celsius.get())
            self._summarize(self.current)


def run():
    App().mainloop()


if __name__ == "__main__":
    run()
