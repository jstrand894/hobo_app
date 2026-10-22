"""Tkinter window: connect to a logger, download to .hobo + CSV, and look at the data."""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from .actions import describe, read_until_end, write_files
from .decode import Readout
from .device import Logger, logger_present

CONFIG = os.path.expanduser("~/.hobo_gui.json")
LIGHT = {"none": ("#b0b0b0", "No logger detected"),
         "detected": ("#ff9f0a", "Adapter detected, can't read it (is HOBOware open?)"),
         "ok": ("#34c759", "Logger connected")}
POLL_MS = 1500


def asset(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)

TEMP_COLOR = "#d1495b"
RH_COLOR = "#2e86ab"


class Plot(tk.Canvas):
    """Temperature (left axis) and RH (right axis) over time, drawn on a plain canvas."""

    ML, MR, MT, MB = 62, 62, 16, 36

    def __init__(self, master, **kw):
        super().__init__(master, background="white", highlightthickness=1, highlightbackground="#bbbbbb", **kw)
        self.series = None  # (times, temps, rhs, temp_unit)
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, ro: Readout, fahrenheit: bool):
        rows = list(ro.rows())
        times = [r[0] for r in rows]
        temps = [r[1] * 9 / 5 + 32 if fahrenheit else r[1] for r in rows]
        rhs = [r[2] for r in rows]
        self.series = (times, temps, rhs, "°F" if fahrenheit else "°C")
        self.redraw()

    def clear(self):
        self.series = None
        self.redraw()

    @staticmethod
    def _range(vals, pad=0.05):
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        span = hi - lo
        return lo - span * pad, hi + span * pad

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 200 or h < 120:
            return
        if not self.series or not self.series[0]:
            self.create_text(w / 2, h / 2, text="No data yet. Download from a logger or open a .hobo file.",
                             fill="#777777", font=("Helvetica", 13))
            return
        times, temps, rhs, unit = self.series
        x0, x1, y0, y1 = self.ML, w - self.MR, self.MT, h - self.MB
        t0, t1 = times[0], times[-1]
        span = max((t1 - t0).total_seconds(), 1.0)
        tlo, thi = self._range(temps)
        rlo, rhi = self._range(rhs)

        def px(t): return x0 + (t - t0).total_seconds() / span * (x1 - x0)
        def pyt(v): return y1 - (v - tlo) / (thi - tlo) * (y1 - y0)
        def pyr(v): return y1 - (v - rlo) / (rhi - rlo) * (y1 - y0)

        for i in range(6):  # horizontal grid + both axes' labels
            f = i / 5
            y = y1 - f * (y1 - y0)
            self.create_line(x0, y, x1, y, fill="#e6e6e6")
            self.create_text(x0 - 6, y, text=f"{tlo + f * (thi - tlo):.1f}", anchor="e", fill=TEMP_COLOR,
                             font=("Helvetica", 10))
            self.create_text(x1 + 6, y, text=f"{rlo + f * (rhi - rlo):.0f}", anchor="w", fill=RH_COLOR,
                             font=("Helvetica", 10))
        for i in range(7):  # time ticks
            f = i / 6
            t = t0 + timedelta(seconds=span * f)
            x = x0 + f * (x1 - x0)
            self.create_line(x, y1, x, y1 + 4, fill="#888888")
            self.create_text(x, y1 + 6, text=t.strftime("%b %d\n%Y"), anchor="n", fill="#555555",
                             font=("Helvetica", 9), justify="center")
        self.create_text(x0 - 6, y0 - 8, text=unit, anchor="e", fill=TEMP_COLOR, font=("Helvetica", 10, "bold"))
        self.create_text(x1 + 6, y0 - 8, text="% RH", anchor="w", fill=RH_COLOR, font=("Helvetica", 10, "bold"))
        self.create_rectangle(x0, y0, x1, y1, outline="#999999")

        n = len(times)
        step = max(1, n // max(1, (x1 - x0) * 2 // 1))
        idx = list(range(0, n, step))
        if idx[-1] != n - 1:
            idx.append(n - 1)
        for vals, conv, color in ((rhs, pyr, RH_COLOR), (temps, pyt, TEMP_COLOR)):
            pts = []
            for i in idx:
                pts += [px(times[i]), conv(vals[i])]
            if len(pts) >= 4:
                self.create_line(*pts, fill=color, width=1.4)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HOBO Logger")
        self.geometry("900x640")
        self.minsize(720, 520)
        self.q = queue.Queue()
        self.busy = False
        self.last_dir = self._load_last_dir()
        self.celsius = tk.BooleanVar(value=False)
        self.save_hobo = tk.BooleanVar(value=True)
        self.logger_info = None
        self.info = {k: tk.StringVar(value="—") for k in ("Status", "Name", "Serial", "Interval", "Started")}
        self.msg = tk.StringVar(value="Plug in the adapter with a logger seated in it. Close HOBOware first.")
        self.link = "none"
        self.icons = {}
        for n in ("connect", "download", "open", "refresh", "stop", "launch", "app"):
            try:
                self.icons[n] = tk.PhotoImage(file=asset(n + ".png"))
            except tk.TclError:
                pass  # missing icon: buttons just show text
        if "app" in self.icons:
            self.iconphoto(True, self.icons["app"])  # also sets the Dock icon when run from Python
        self._build()
        self.after(100, self._pump)
        self.after(300, self._poll)

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        box = ttk.LabelFrame(top, text="Logger")
        box.pack(side="left", fill="y")
        status = ttk.Frame(box)
        status.grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 2))
        self.light = tk.Canvas(status, width=16, height=16, highlightthickness=0)
        self.light.pack(side="left")
        self.light_dot = self.light.create_oval(2, 2, 14, 14, fill=LIGHT["none"][0], outline="#888888")
        self.link_text = tk.StringVar(value=LIGHT["none"][1])
        ttk.Label(status, textvariable=self.link_text, width=34).pack(side="left", padx=(6, 0))
        for r, key in enumerate(self.info, start=1):
            ttk.Label(box, text=key + ":", foreground="#666666").grid(row=r, column=0, sticky="e", padx=(10, 4), pady=2)
            ttk.Label(box, textvariable=self.info[key], width=30).grid(row=r, column=1, sticky="w", padx=(0, 10), pady=2)

        side = ttk.Frame(top)
        side.pack(side="left", fill="both", expand=True, padx=(16, 0))
        row = ttk.Frame(side)
        row.pack(fill="x")
        def button(text, icon, cmd, **kw):
            return ttk.Button(row, text=text, image=self.icons.get(icon), compound="left", command=cmd, **kw)

        self.btn_connect = button("Refresh", "refresh", self.connect)
        self.btn_connect.pack(side="left")
        self.btn_download = button("Download", "download", self.download, state="disabled")
        self.btn_download.pack(side="left", padx=8)
        self.btn_open = button("Open file…", "open", self.open_file)
        self.btn_open.pack(side="left")

        ttk.Checkbutton(side, text="Temperature in °C (CSV and plot)", variable=self.celsius,
                        command=self.refresh_plot).pack(anchor="w", pady=(10, 0))
        ttk.Checkbutton(side, text="Also save the raw .hobo file", variable=self.save_hobo).pack(anchor="w")

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=12)
        ttk.Label(self, textvariable=self.msg, wraplength=860, justify="left").pack(fill="x", padx=12, pady=(4, 6))

        self.plot = Plot(self)
        self.plot.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.current = None  # Readout being displayed

    # ---- helpers ----
    def set_busy(self, busy, text=None):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_connect.config(state=state)
        self.btn_open.config(state=state)
        if busy:
            self.btn_download.config(state="disabled")
            self.progress.config(mode="indeterminate", value=0)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.config(mode="determinate", value=0)
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
        self.msg.set("Error: " + str(exc))
        messagebox.showerror("HOBO Logger", text)

    def _on_quiet_err(self, exc):
        self.set_busy(False)
        self.set_link("detected" if logger_present() else "none")
        self.msg.set(f"Could not read the logger: {exc}")

    def _on_ok(self, result):
        self.set_busy(False)
        result()  # the callback returned by the worker, runs on the UI thread

    # ---- actions ----
    @staticmethod
    def _load_last_dir():
        try:
            with open(CONFIG) as f:
                d = json.load(f).get("last_dir")
            if d and os.path.isdir(d):
                return d
        except (OSError, ValueError):
            pass
        return os.path.expanduser("~/Documents")

    def _remember_dir(self, d):
        self.last_dir = d
        try:
            with open(CONFIG, "w") as f:
                json.dump({"last_dir": d}, f)
        except OSError:
            pass

    def set_link(self, state):
        self.link = state
        color, text = LIGHT[state]
        self.light.itemconfig(self.light_dot, fill=color)
        self.link_text.set(text)
        if state != "ok":
            self.logger_info = None
            for v in self.info.values():
                v.set("—")
            self.btn_download.config(state="disabled")

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
        self.after(POLL_MS, self._poll)

    def connect(self, auto=False):
        self.set_busy(True, "Reading logger…")

        def work():
            with Logger() as lg:
                d = describe(lg)
            return lambda: self._show_info(d)
        self.run_bg(work, quiet=auto)

    def _show_info(self, d):
        self.set_link("ok")
        self.logger_info = d
        self.info["Status"].set("Logging" if d["logging"] else "Stopped / idle")
        self.info["Name"].set(d["name"])
        self.info["Serial"].set(d["serial"])
        self.info["Interval"].set(self._fmt_interval(d["interval_s"]))
        self.info["Started"].set(d["started"].strftime("%Y-%m-%d %H:%M:%S"))
        self.btn_download.config(state="normal")
        self.msg.set("Logger connected. Click Download to save the data (this does not stop the logger).")

    @staticmethod
    def _fmt_interval(s):
        if s % 3600 == 0:
            return f"{s // 3600} h"
        if s % 60 == 0:
            return f"{s // 60} min"
        return f"{s} s"

    def download(self):
        d = self.logger_info or {}
        ext = ".csv"
        default = f"{d.get('name', 'HOBO')}_{d.get('serial', '')}_{datetime.now():%Y%m%d-%H%M}".replace(" ", "_")
        path = filedialog.asksaveasfilename(
            title="Save data as", initialdir=self.last_dir, initialfile=default + ext,
            defaultextension=ext, filetypes=[("CSV file", "*.csv")])
        if not path:
            return  # cancelled
        base = path[:-4] if path.lower().endswith(".csv") else path
        self._remember_dir(os.path.dirname(base))
        fahrenheit = not self.celsius.get()
        save_hobo = self.save_hobo.get()
        self.set_busy(True, "Downloading…")

        def work():
            with Logger() as lg:
                mem = read_until_end(lg, progress=lambda a, t: self.q.put(("progress", (a, t))))
            ro = Readout(mem)
            csv_path, hobo_path = write_files(ro, mem, base, fahrenheit=fahrenheit, save_hobo=save_hobo)
            return lambda: self._downloaded(ro, hobo_path, csv_path)
        self.run_bg(work)

    def _downloaded(self, ro, hobo_path, csv_path):
        files = "\n".join(p for p in (csv_path, hobo_path) if p)
        self.msg.set(f"Saved {len(ro.samples):,} samples:\n{files}")
        self.progress["value"] = 100
        self.btn_download.config(state="normal")
        self.show(ro)

    def open_file(self):
        path = filedialog.askopenfilename(title="Open a .hobo file", filetypes=[("HOBO files", "*.hobo"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "rb") as f:
                ro = Readout(f.read())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("HOBO Logger", f"Could not read that file:\n{e}")
            return
        self.msg.set(f"Opened {os.path.basename(path)}: {len(ro.samples):,} samples")
        self.show(ro)

    def show(self, ro):
        self.current = ro
        self.plot.set_data(ro, fahrenheit=not self.celsius.get())

    def refresh_plot(self):
        if self.current:
            self.plot.set_data(self.current, fahrenheit=not self.celsius.get())


def run():
    App().mainloop()


if __name__ == "__main__":
    run()
